"""Tests for cyg.storage against the real Cygnet database."""

from __future__ import annotations

import gzip
import io
import json
import logging
import sqlite3
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

from conftest import make_schema_db
from cyg.storage import (
    ENV_DATABASE_PATH,
    RELEASE_DOWNLOAD_URL,
    RELEASE_TAG_SUFFIX,
    RELEASES_API,
    DatabaseError,
    DatabaseNotFoundError,
    DownloadError,
    Storage,
)


class _Response(io.BytesIO):
    """A context-managed, file-like object simulating ``urlopen`` output."""

    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def _fake_urlopen(monkeypatch: pytest.MonkeyPatch, *payloads: bytes) -> mock.Mock:
    """Mock ``urlopen`` returning the given payloads in order.

    A single payload is returned for every call; several payloads are consumed
    in sequence (used for the API-JSON-then-gzip download flow).
    """
    if len(payloads) == 1:
        urlopen = mock.Mock(return_value=_Response(payloads[0]))
    else:
        urlopen = mock.Mock(side_effect=[_Response(p) for p in payloads])
    monkeypatch.setattr("cyg.storage.urllib.request.urlopen", urlopen)
    return urlopen


def _release_json(*tags: str, single: bool = False) -> bytes:
    """A GitHub releases API payload for the given tags."""
    if single:
        return json.dumps({"tag_name": tags[0]}).encode("utf-8")
    return json.dumps([{"tag_name": tag} for tag in tags]).encode("utf-8")


def _sqlite_gzip() -> bytes:
    """A gzip-compressed SQLite database (valid download payload)."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        make_schema_db(tmp.name)
        tmp_path = tmp.name
    data = Path(tmp_path).read_bytes()
    import os

    os.unlink(tmp_path)

    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as gz:
        gz.write(data)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------


def test_resolve_explicit_path(tmp_path: Path) -> None:
    target = tmp_path / "cygnet.db"
    assert Storage._resolve_path(str(target)) == target.expanduser()


def test_resolve_env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "cygnet.db"
    monkeypatch.setenv(ENV_DATABASE_PATH, str(target))
    assert Storage._resolve_path(None) == target.expanduser()


def test_resolve_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_DATABASE_PATH, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert Storage._cache_dir() == tmp_path / "cyg"


def test_resolve_path_no_env_no_arg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _resolve_path falls back to the cache dir when no env var or arg."""
    monkeypatch.delenv(ENV_DATABASE_PATH, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    # This tests line 105 in storage.py
    result = Storage._resolve_path(None)
    assert result == tmp_path / "cyg" / "cygnet.db"


def test_cache_root_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert Storage._cache_root() == tmp_path


def test_cache_root_windows_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert Storage._cache_root() == tmp_path


def test_cache_root_darwin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert Storage._cache_root() == tmp_path / "Library" / "Caches"


def test_cache_root_linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert Storage._cache_root() == tmp_path / ".cache"


# ---------------------------------------------------------------------------
# construction & validation
# ---------------------------------------------------------------------------


def test_storage_missing_without_download(tmp_path: Path) -> None:
    with pytest.raises(DatabaseNotFoundError):
        Storage(db_path=str(tmp_path / "nope.db"), download=False)


def test_storage_invalid_sqlite_file(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.db"
    bogus.write_bytes(b"this is not sqlite at all")
    with pytest.raises(DatabaseError, match="not a valid SQLite"):
        Storage(db_path=str(bogus), download=False)


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


def test_download_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "cache" / "cyg" / "cygnet.db"
    _fake_urlopen(monkeypatch, _release_json("2026.05.12", single=True), _sqlite_gzip())
    store = Storage(db_path=str(target), download=True)
    try:
        assert target.exists()
        assert store.current_release() == "2026.05.12"
    finally:
        store.close()


def test_download_specific_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "cache" / "cyg" / "cygnet.db"
    urlopen = _fake_urlopen(monkeypatch, _sqlite_gzip())
    store = Storage(db_path=str(target), download=True, version="2026.03.01")
    try:
        assert target.exists()
        assert store.current_release() == "2026.03.01"
        called_url = urlopen.call_args.args[0]
        assert called_url == RELEASE_DOWNLOAD_URL.format(version="2026.03.01", name="cygnet.db.gz")
    finally:
        store.close()


def test_download_skips_when_database_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "cygnet.db"
    make_schema_db(target)

    before = target.read_bytes()
    urlopen = _fake_urlopen(monkeypatch, _sqlite_gzip())
    store = Storage(db_path=str(target), download=True)
    try:
        assert target.read_bytes() == before
        urlopen.assert_not_called()
    finally:
        store.close()


def test_download_error_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cyg.storage.urllib.request.urlopen",
        mock.Mock(side_effect=urllib.error.URLError("boom")),
    )
    with pytest.raises(DownloadError, match="Could not fetch releases"):
        Storage(db_path=str(tmp_path / "cygnet.db"), download=True)


def test_download_progress_on_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Download progress is written to stderr only on a TTY."""

    class TtyStderr:
        def __init__(self) -> None:
            self.buf: list[str] = []

        def isatty(self) -> bool:
            return True

        def write(self, data: str) -> None:
            self.buf.append(data)

        def flush(self) -> None:
            pass

    target = tmp_path / "cache" / "cyg" / "cygnet.db"
    _fake_urlopen(monkeypatch, _release_json("2026.05.12", single=True), _sqlite_gzip())
    fake_stderr = TtyStderr()
    monkeypatch.setattr("cyg.storage.sys.stderr", fake_stderr)
    store = Storage(db_path=str(target), download=True)
    try:
        output = "".join(fake_stderr.buf)
        assert "Cygnet database 2026.05.12" in output
        assert "MiB" in output
        assert output.endswith("\n")
    finally:
        store.close()


def test_download_stream_error_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _urlopen(url, *, timeout=None):  # noqa: ARG001
        if _urlopen._call_count == 0:
            _urlopen._call_count += 1
            return _Response(_release_json("2026.05.12", single=True))
        raise urllib.error.URLError("boom")

    _urlopen._call_count = 0  # type: ignore[attr-defined]
    monkeypatch.setattr("cyg.storage.urllib.request.urlopen", _urlopen)
    with pytest.raises(DownloadError, match="Could not download"):
        Storage(db_path=str(tmp_path / "cygnet.db"), download=True)


def test_download_unreadable_gzip_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_urlopen(monkeypatch, _release_json("2026.05.12", single=True), b"not gzip data at all")
    with pytest.raises(DownloadError):
        Storage(db_path=str(tmp_path / "cygnet.db"), download=True)


def test_download_archive_not_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as gz:
        gz.write(b"valid gzip, but not a database")
    _fake_urlopen(monkeypatch, _release_json("2026.05.12", single=True), buffer.getvalue())
    with pytest.raises(DownloadError, match="not a valid Cygnet database"):
        Storage(db_path=str(tmp_path / "cygnet.db"), download=True)


def test_download_truncated_gzip_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _sqlite_gzip()[:40]
    _fake_urlopen(monkeypatch, _release_json("2026.05.12", single=True), payload)
    with pytest.raises(DownloadError):
        Storage(db_path=str(tmp_path / "cygnet.db"), download=True)


def test_download_mkdir_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_urlopen(monkeypatch, _release_json("2026.05.12", single=True))
    monkeypatch.setattr("pathlib.Path.mkdir", mock.Mock(side_effect=OSError("permission denied")))
    with pytest.raises(DownloadError, match="Could not create directory"):
        Storage(db_path=str(tmp_path / "cygnet.db"), download=True)


# ---------------------------------------------------------------------------
# Edge case tests for 100% coverage
# ---------------------------------------------------------------------------


def test_storage_execute_closed_connection(tmp_path: Path) -> None:
    """Test execute raises RuntimeError when connection is closed."""
    target = tmp_path / "cygnet.db"
    make_schema_db(target)

    store = Storage(db_path=str(target), download=False)
    store.close()
    with pytest.raises(RuntimeError, match="connection closed"):
        store.execute("SELECT 1")


def test_storage_close_twice(tmp_path: Path) -> None:
    """Test close can be called multiple times safely."""
    target = tmp_path / "cygnet.db"
    make_schema_db(target)

    store = Storage(db_path=str(target), download=False)
    store.close()
    # Second close should not raise
    store.close()


def test_storage_context_manager(tmp_path: Path) -> None:
    """Test Storage context manager (__enter__ and __exit__)."""
    target = tmp_path / "cygnet.db"
    make_schema_db(target)

    with Storage(db_path=str(target), download=False) as store:
        assert store.execute("SELECT 1").fetchone()[0] == 1
    # Connection should be closed after exiting context


def test_storage_validate_missing_file(tmp_path: Path) -> None:
    """Test _validate raises DatabaseNotFoundError for missing file."""
    target = tmp_path / "cygnet.db"
    # Don't create the file
    store = Storage.__new__(Storage)
    store._path = target
    store._conn = None
    with pytest.raises(DatabaseNotFoundError, match="not found"):
        store._validate()


def test_storage_validate_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _validate raises DatabaseError on OSError reading file."""
    target = tmp_path / "cygnet.db"
    target.write_bytes(b"SQLite format 3\x00" + b"x" * 100)

    store = Storage.__new__(Storage)
    store._path = target
    store._conn = None

    # Mock open to raise OSError
    def mock_open(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", mock_open)

    with pytest.raises(DatabaseError, match="Could not read"):
        store._validate()


def test_storage_validate_sqlite_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _validate raises DatabaseError on sqlite3.DatabaseError."""
    target = tmp_path / "cygnet.db"
    target.write_bytes(b"SQLite format 3\x00" + b"x" * 100)

    store = Storage.__new__(Storage)
    store._path = target
    store._conn = None

    # Mock sqlite3.connect to raise DatabaseError
    def mock_connect(*args, **kwargs):
        raise sqlite3.DatabaseError("corrupt")

    monkeypatch.setattr(sqlite3, "connect", mock_connect)

    with pytest.raises(DatabaseError, match="Could not open"):
        store._validate()


def test_storage_validate_missing_tables(tmp_path: Path) -> None:
    """Test _validate raises DatabaseError for missing tables."""
    target = tmp_path / "cygnet.db"
    # Create a valid SQLite file but with wrong schema
    conn = sqlite3.connect(target)
    conn.execute("CREATE TABLE wrong_table (id INTEGER)")
    conn.commit()
    conn.close()

    store = Storage.__new__(Storage)
    store._path = target
    store._conn = None

    with pytest.raises(DatabaseError, match="missing tables"):
        store._validate()


def _store(tmp_path: Path) -> Storage:
    """An explicit-path Storage backed by a minimal valid database."""
    make_schema_db(tmp_path / "cygnet.db")
    return Storage(db_path=str(tmp_path / "cygnet.db"), download=False)


def _use_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Route the default cache directory to ``tmp_path``."""
    monkeypatch.delenv(ENV_DATABASE_PATH, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


# ---------------------------------------------------------------------------
# versioned cache layout
# ---------------------------------------------------------------------------


def test_versioned_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_cache(tmp_path, monkeypatch)
    assert Storage._versioned_path("2026.05.12") == tmp_path / "cyg" / "2026.05.12" / "cygnet.db"


def test_resolve_cached_specific_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_cache(tmp_path, monkeypatch)
    path, tag = Storage._resolve_cached("2026.05.12", download=True)
    assert tag == "2026.05.12"
    assert path == tmp_path / "cyg" / "2026.05.12" / "cygnet.db"


def test_resolve_cached_newest_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_cache(tmp_path, monkeypatch)
    make_schema_db(tmp_path / "cyg" / "2026.01.01" / "cygnet.db")
    make_schema_db(tmp_path / "cyg" / "2026.05.12" / "cygnet.db")
    path, tag = Storage._resolve_cached(None, download=True)
    assert tag == "2026.05.12"
    assert path == tmp_path / "cyg" / "2026.05.12" / "cygnet.db"


def test_resolve_cached_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_cache(tmp_path, monkeypatch)
    assert Storage._resolve_cached(None, download=True) == (None, None)


def test_resolve_cached_legacy_unversioned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_cache(tmp_path, monkeypatch)
    make_schema_db(tmp_path / "cyg" / "cygnet.db")
    assert Storage._resolve_cached(None, download=True) == (None, None)
    path, tag = Storage._resolve_cached(None, download=False)
    assert tag is None
    assert path == tmp_path / "cyg" / "cygnet.db"


def test_resolve_cached_legacy_sidetagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A legacy DB with a release sidecar is reused and versioned."""
    _use_cache(tmp_path, monkeypatch)
    legacy = tmp_path / "cyg" / "cygnet.db"
    make_schema_db(legacy)
    Path(str(legacy) + RELEASE_TAG_SUFFIX).write_text("2026.05.12\n", encoding="utf-8")
    path, tag = Storage._resolve_cached(None, download=True)
    assert tag == "2026.05.12"
    assert path == legacy


def test_default_uses_legacy_unversioned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_cache(tmp_path, monkeypatch)
    make_schema_db(tmp_path / "cyg" / "cygnet.db")
    urlopen = _fake_urlopen(monkeypatch, b"")
    store = Storage(download=False)
    try:
        assert store.path == tmp_path / "cyg" / "cygnet.db"
        assert store.current_release() is None
        urlopen.assert_not_called()
    finally:
        store.close()


def test_default_migrates_legacy_unversioned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sidecar-less legacy DB is replaced by the versioned latest release."""
    _use_cache(tmp_path, monkeypatch)
    make_schema_db(tmp_path / "cyg" / "cygnet.db")
    _fake_urlopen(monkeypatch, _release_json("2026.05.12", single=True), _sqlite_gzip())
    store = Storage()
    try:
        assert store.path == tmp_path / "cyg" / "2026.05.12" / "cygnet.db"
        assert store.current_release() == "2026.05.12"
    finally:
        store.close()


def test_upgrade_legacy_migrates_to_versioned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrading a legacy unversioned DB repoints to the versioned layout."""
    _use_cache(tmp_path, monkeypatch)
    make_schema_db(tmp_path / "cyg" / "cygnet.db")
    _fake_urlopen(monkeypatch, _sqlite_gzip())
    store = Storage(download=False)
    try:
        assert store.upgrade(version="2026.05.12") == "2026.05.12"
        assert store.path == tmp_path / "cyg" / "2026.05.12" / "cygnet.db"
        assert store.current_release() == "2026.05.12"
    finally:
        store.close()


def test_default_uses_newest_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_cache(tmp_path, monkeypatch)
    for tag in ("2026.01.01", "2026.05.12", "2026.03.01"):
        db = tmp_path / "cyg" / tag / "cygnet.db"
        make_schema_db(db)
        (Path(str(db) + RELEASE_TAG_SUFFIX)).write_text(tag + "\n", encoding="utf-8")
    urlopen = _fake_urlopen(monkeypatch, b"")
    store = Storage(download=False)
    try:
        assert store.path == tmp_path / "cyg" / "2026.05.12" / "cygnet.db"
        assert store.current_release() == "2026.05.12"
        urlopen.assert_not_called()
    finally:
        store.close()


def test_default_downloads_newest_versioned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_cache(tmp_path, monkeypatch)
    _fake_urlopen(monkeypatch, _release_json("2026.05.12", single=True), _sqlite_gzip())
    store = Storage()
    try:
        assert store.path == tmp_path / "cyg" / "2026.05.12" / "cygnet.db"
        assert store.current_release() == "2026.05.12"
        assert store.path.exists()
    finally:
        store.close()


def test_default_version_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_cache(tmp_path, monkeypatch)
    urlopen = _fake_urlopen(monkeypatch, _sqlite_gzip())
    store = Storage(version="2026.03.01")
    try:
        assert store.path == tmp_path / "cyg" / "2026.03.01" / "cygnet.db"
        assert store.current_release() == "2026.03.01"
        called_url = urlopen.call_args.args[0]
        assert called_url == RELEASE_DOWNLOAD_URL.format(version="2026.03.01", name="cygnet.db.gz")
    finally:
        store.close()


def test_default_missing_no_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_cache(tmp_path, monkeypatch)
    with pytest.raises(DatabaseNotFoundError, match=str(tmp_path / "cyg")):
        Storage(download=False)


def test_multiple_versions_coexist_and_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Several releases coexist on disk and any of them can be selected."""
    _use_cache(tmp_path, monkeypatch)
    for tag in ("2026.01.01", "2026.05.12"):
        db = tmp_path / "cyg" / tag / "cygnet.db"
        make_schema_db(db)
        (Path(str(db) + RELEASE_TAG_SUFFIX)).write_text(tag + "\n", encoding="utf-8")
    urlopen = _fake_urlopen(monkeypatch, b"")
    newest = Storage()
    try:
        assert newest.path == tmp_path / "cyg" / "2026.05.12" / "cygnet.db"
        assert newest.current_release() == "2026.05.12"
    finally:
        newest.close()
    older = Storage(version="2026.01.01")
    try:
        assert older.path == tmp_path / "cyg" / "2026.01.01" / "cygnet.db"
        assert older.current_release() == "2026.01.01"
    finally:
        older.close()
    urlopen.assert_not_called()


def test_upgrade_latest_no_download_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Upgrading an already-latest store probes upstream but never downloads."""
    _use_cache(tmp_path, monkeypatch)
    db = tmp_path / "cyg" / "2026.05.12" / "cygnet.db"
    make_schema_db(db)
    (Path(str(db) + RELEASE_TAG_SUFFIX)).write_text("2026.05.12\n", encoding="utf-8")
    urlopen = _fake_urlopen(monkeypatch, _release_json("2026.05.12", single=True))
    store = Storage()
    try:
        with caplog.at_level(logging.INFO, logger="cyg.storage"):
            assert store.upgrade() == "2026.05.12"
        assert "Already at the latest release 2026.05.12" in caplog.text
        urls = [getattr(call.args[0], "full_url", call.args[0]) for call in urlopen.call_args_list]
        assert urls == [RELEASES_API + "/latest"]
        assert all("releases/download" not in u for u in urls)
    finally:
        store.close()


def test_upgrade_versioned_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_cache(tmp_path, monkeypatch)
    db = tmp_path / "cyg" / "2026.05.12" / "cygnet.db"
    make_schema_db(db)
    (Path(str(db) + RELEASE_TAG_SUFFIX)).write_text("2026.05.12\n", encoding="utf-8")
    urlopen = _fake_urlopen(monkeypatch, b"")
    store = Storage(download=False)
    try:
        assert store.upgrade(version="2026.05.12") == "2026.05.12"
        urlopen.assert_not_called()
    finally:
        store.close()


def test_upgrade_versioned_new_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_cache(tmp_path, monkeypatch)
    old = tmp_path / "cyg" / "2026.01.01" / "cygnet.db"
    make_schema_db(old)
    (Path(str(old) + RELEASE_TAG_SUFFIX)).write_text("2026.01.01\n", encoding="utf-8")
    _fake_urlopen(monkeypatch, _release_json("2026.05.12", single=True), _sqlite_gzip())
    store = Storage(download=False)
    try:
        new_path = tmp_path / "cyg" / "2026.05.12" / "cygnet.db"
        assert store.path == old
        assert store.upgrade() == "2026.05.12"
        assert new_path.exists()
        assert old.exists()
        assert store.path == new_path
        assert store.current_release() == "2026.05.12"
        assert store.execute("SELECT count(*) FROM synsets").fetchone()[0] == 0
    finally:
        store.close()


def test_upgrade_versioned_repoints_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_cache(tmp_path, monkeypatch)
    for tag in ("2026.01.01", "2026.05.12"):
        db = tmp_path / "cyg" / tag / "cygnet.db"
        make_schema_db(db)
        (Path(str(db) + RELEASE_TAG_SUFFIX)).write_text(tag + "\n", encoding="utf-8")
    urlopen = _fake_urlopen(monkeypatch, b"")
    store = Storage(version="2026.01.01", download=False)
    try:
        assert store.path == tmp_path / "cyg" / "2026.01.01" / "cygnet.db"
        assert store.upgrade(version="2026.05.12") == "2026.05.12"
        assert store.path == tmp_path / "cyg" / "2026.05.12" / "cygnet.db"
        urlopen.assert_not_called()
    finally:
        store.close()


def test_upgrade_versioned_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_cache(tmp_path, monkeypatch)
    old = tmp_path / "cyg" / "2026.01.01" / "cygnet.db"
    make_schema_db(old)
    (Path(str(old) + RELEASE_TAG_SUFFIX)).write_text("2026.01.01\n", encoding="utf-8")
    store = Storage(download=False)
    monkeypatch.setattr(store, "_install_release", mock.Mock(side_effect=DownloadError("boom")))
    with pytest.raises(DownloadError, match="boom"):
        store.upgrade(version="2026.05.12")
    assert store.path == old
    assert store.current_release() == "2026.01.01"
    assert store.execute("SELECT count(*) FROM synsets").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# version management: releases API
# ---------------------------------------------------------------------------


def test_fetch_json_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    urlopen = _fake_urlopen(monkeypatch, _release_json("2026.05.12", single=True))
    store = _store(tmp_path)
    try:
        assert store._fetch_json("http://example.test") == {"tag_name": "2026.05.12"}
        urlopen.assert_called_once()
    finally:
        store.close()


def test_fetch_json_network_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cyg.storage.urllib.request.urlopen",
        mock.Mock(side_effect=urllib.error.URLError("boom")),
    )
    store = _store(tmp_path)
    try:
        with pytest.raises(DownloadError, match="Could not fetch releases"):
            store._fetch_json("http://example.test")
    finally:
        store.close()


def test_fetch_json_invalid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_urlopen(monkeypatch, b"this is not json")
    store = _store(tmp_path)
    try:
        with pytest.raises(DownloadError, match="Could not fetch releases"):
            store._fetch_json("http://example.test")
    finally:
        store.close()


def test_latest_release_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_urlopen(monkeypatch, _release_json("2026.05.12", single=True))
    store = _store(tmp_path)
    try:
        assert store.latest_release() == "2026.05.12"
    finally:
        store.close()


def test_latest_release_missing_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_urlopen(monkeypatch, b'{"name": "no tag"}')
    store = _store(tmp_path)
    try:
        with pytest.raises(DownloadError, match="No latest release"):
            store.latest_release()
    finally:
        store.close()


def test_latest_release_not_dict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_urlopen(monkeypatch, b'["not", "a", "dict"]')
    store = _store(tmp_path)
    try:
        with pytest.raises(DownloadError, match="No latest release"):
            store.latest_release()
    finally:
        store.close()


def test_releases_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_urlopen(monkeypatch, _release_json("2026.05.12", "2026.03.01", "2026.01.01"))
    store = _store(tmp_path)
    try:
        assert store.releases() == ["2026.05.12", "2026.03.01", "2026.01.01"]
    finally:
        store.close()


def test_releases_not_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_urlopen(monkeypatch, b'{"tags": []}')
    store = _store(tmp_path)
    try:
        with pytest.raises(DownloadError, match="Unexpected response"):
            store.releases()
    finally:
        store.close()


# ---------------------------------------------------------------------------
# version management: current release & explicit upgrades
# ---------------------------------------------------------------------------


def test_current_release_no_sidecar(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        assert store.current_release() is None
    finally:
        store.close()


def test_current_release_tag(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        Storage._write_tag(store.path, "2026.05.12")
        assert store.current_release() == "2026.05.12"
    finally:
        store.close()


def test_current_release_empty_sidecar(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        sidecar = Path(str(store.path) + RELEASE_TAG_SUFFIX)
        sidecar.write_text("   \n", encoding="utf-8")
        assert store.current_release() is None
    finally:
        store.close()


def test_current_release_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(
        "pathlib.Path.read_text", mock.Mock(side_effect=OSError("permission denied"))
    )
    try:
        assert store.current_release() is None
    finally:
        store.close()


def test_upgrade_explicit_up_to_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    Storage._write_tag(store.path, "2026.05.12")
    urlopen = _fake_urlopen(monkeypatch, b"")
    try:
        assert store.upgrade(version="2026.05.12") == "2026.05.12"
        urlopen.assert_not_called()
    finally:
        store.close()


def test_upgrade_explicit_installs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    Storage._write_tag(store.path, "2026.01.01")
    _fake_urlopen(monkeypatch, _release_json("2026.05.12", single=True), _sqlite_gzip())
    try:
        assert store.upgrade() == "2026.05.12"
        assert store.current_release() == "2026.05.12"
        assert store.execute("SELECT count(*) FROM synsets").fetchone()[0] == 0
    finally:
        store.close()


def test_upgrade_explicit_specific_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    Storage._write_tag(store.path, "2026.01.01")
    urlopen = _fake_urlopen(monkeypatch, _sqlite_gzip())
    try:
        assert store.upgrade(version="2026.03.01") == "2026.03.01"
        assert store.current_release() == "2026.03.01"
        called_url = urlopen.call_args.args[0]
        assert called_url == RELEASE_DOWNLOAD_URL.format(version="2026.03.01", name="cygnet.db.gz")
    finally:
        store.close()


def test_upgrade_explicit_failure_reconnects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    Storage._write_tag(store.path, "2026.01.01")
    monkeypatch.setattr(store, "_install_release", mock.Mock(side_effect=DownloadError("boom")))
    try:
        with pytest.raises(DownloadError, match="boom"):
            store.upgrade(version="2026.05.12")
        assert store.current_release() == "2026.01.01"
        assert store.execute("SELECT count(*) FROM synsets").fetchone()[0] == 0
    finally:
        store.close()
