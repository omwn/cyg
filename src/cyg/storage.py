"""Persistent storage for the Cygnet database.

This module is responsible for locating, downloading (on first use),
validating and opening the read-only SQLite database that backs the rest of
the package.

The database is resolved in the following order of precedence:

1. The explicit ``db_path`` argument passed to :class:`Cygnet` /
   :class:`Storage`; this never happens for the writer unless called explicitly.
2. The ``CYG_DB`` environment variable.
3. A per-user, per-version cache directory: ``<cache>/cyg/<version>/cygnet.db``
   (``~/.cache/cyg/..`` on Linux, ``~/Library/Caches/cyg/..`` on macOS,
   ``%LOCALAPPDATA%\\cyg\\..`` on Windows). The newest release already present
   is used, otherwise the selected (or latest) release is downloaded.

Version management
------------------

The upstream ``omwn/cygnet`` repository ships the database as a release asset
(``cygnet.db.gz``) tagged by date (``YYYY.MM.DD``). When a release is
installed the tag is recorded next to the database file in a ``<database>.tag``
sidecar file. This lets :class:`Storage` report the version currently
installed (:meth:`Storage.current_release`), list and inspect the versions
available upstream (:meth:`Storage.releases` /
:meth:`Storage.latest_release`), and upgrade the local database atomically
(:meth:`Storage.upgrade`). Because every download is stored under its own
version directory (or keeps its sidecar after an upgrade), several releases
can coexist on the same machine.

No filesystem access happens at import time; the database is only touched
when a :class:`Storage` instance is constructed.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("cyg.storage")

#: Name of the directory (under the platform cache directory) that holds the database.
DATABASE_DIRECTORY_NAME = "cyg"
#: File name of the local SQLite database.
DATABASE_FILE_NAME = "cygnet.db"
#: Environment variable that can point at an alternative database file.
ENV_DATABASE_PATH = "CYG_DB"
#: Base URL of the GitHub releases API for the upstream Cygnet repository.
RELEASES_API = "https://api.github.com/repos/omwn/cygnet/releases"
#: Download URL template for a specific release asset.
RELEASE_DOWNLOAD_URL = "https://github.com/omwn/cygnet/releases/download/{version}/{name}"
#: Suffix appended to the database path for the release-tag sidecar file.
RELEASE_TAG_SUFFIX = ".tag"
#: User-Agent header sent to the GitHub API.
USER_AGENT = "cyg"
#: Seconds to wait for downloads and GitHub API requests.
DOWNLOAD_TIMEOUT = 300
#: SQLite file header magic bytes used to validate downloaded databases.
SQLITE_HEADER = b"SQLite format 3\x00"

_Params = Iterable[Any]


class DatabaseError(RuntimeError):
    """Base exception for all cyg storage failures."""


class DatabaseNotFoundError(DatabaseError):
    """The database file does not exist and could not be produced."""


class DownloadError(DatabaseError):
    """The database could not be downloaded or decompressed."""


class Storage:
    """Owns a local Cygnet database file and its read-only connection.

    When no ``db_path`` (and no ``CYG_DB`` environment variable) is given the
    database is stored in a versioned per-user cache directory so that several
    releases can coexist on the same machine:

    * Linux: ``~/.cache/cyg/<version>/cygnet.db``
    * macOS: ``~/Library/Caches/cyg/<version>/cygnet.db``
    * Windows: ``%LOCALAPPDATA%\\cyg\\<version>\\cygnet.db``

    The newest release already present locally is used, otherwise the latest
    upstream release (or the release selected with ``version``) is downloaded.

    Args:
        db_path: Explicit path to a ``cygnet.db`` file. When ``None`` the path
            is resolved from the ``CYG_DB`` environment variable or the
            versioned platform cache directory.
        download: When ``True`` (the default) and the resolved path does not
            exist, attempt to download the database. When ``False`` a
            :class:`DatabaseNotFoundError` is raised instead.
        version: The upstream release tag (e.g. ``"2026.05.12"``) to select.
            When ``None`` the newest release already present locally (or the
            latest upstream release) is used.

    Raises:
        DatabaseError: If the resolved database is missing or invalid.
        DownloadError: If the automatic download fails.
    """

    _conn: sqlite3.Connection | None = None
   
    def __init__(
        self,
        db_path: str | None = None,
        *,
        download: bool = True,
        version: str | None = None,
    ) -> None:                
        explicit = db_path is not None or ENV_DATABASE_PATH in os.environ
        self._versioned = not explicit
        path: Path | None
        if explicit:
            path = self._resolve_path(db_path)
            self._version = version
            
        else:
            path, self._version = self._resolve_cached(version, download=download)
   

        if path is not None and path.exists():
            self._path = path            
            if self._version != self.latest_release():  
                print ("A new version of the Cygnet database with tag "+ self.latest_release() +" has been released and needs to be downloaded.")              
                self.upgrade()
                print ("Cygnet database successfully upgraded.")
            self._validate()
            self._connect()           
            return

        if not download:
            where = str(path) if path is not None else str(self._cache_dir())
            raise DatabaseNotFoundError(
                f"The Cygnet database was not found at {where}. "
                f"Set the {ENV_DATABASE_PATH} environment variable or pass db_path."
            )

        if path is None:
            tag = self.latest_release()
            path, self._version = self._versioned_path(tag), tag

        self._path = path
        tag = self._version if self._version is not None else self.latest_release()
        print ("Downloading the Cygnet database...")
        self._install_release(self._path, tag, timeout=DOWNLOAD_TIMEOUT)
        print ("Cygnet database successfully downloaded.")
        self._validate()
        self._connect()
        

    @classmethod
    def _resolve_path(cls, db_path: str | None) -> Path:
        if db_path is not None:
            return Path(db_path).expanduser()
        env_path = os.environ.get(ENV_DATABASE_PATH)
        if env_path:
            return Path(env_path).expanduser()
        return cls._cache_dir() / DATABASE_FILE_NAME

    @classmethod
    def _cache_dir(cls) -> Path:
        return cls._cache_root() / DATABASE_DIRECTORY_NAME

    @classmethod
    def _versioned_path(cls, version: str) -> Path:
        return cls._cache_dir() / version / DATABASE_FILE_NAME

    @classmethod
    def _resolve_cached(
        cls, version: str | None, *, download: bool
    ) -> tuple[Path | None, str | None]:
        """Pick the local database path for the default cache layout.

        Args:
            version: A specific release tag, or ``None`` to use the newest
                release already present locally.
            download: Whether a missing/ambiguous database may be downloaded.
                When ``False`` a legacy (unversioned) database is used as-is.

        Returns:
            ``(path, tag)`` where ``path`` is ``None`` when no usable database
            is present locally.
        """
        
        if version is not None:
            return cls._versioned_path(version), version
        candidates = sorted(
            (p for p in cls._cache_dir().glob(f"*/{DATABASE_FILE_NAME}")),
            key=lambda p: p.parent.name,
        )
        if candidates:
            latest = candidates[-1]
            return latest, latest.parent.name
        legacy = cls._cache_dir() / DATABASE_FILE_NAME
        tag_path = cls._tag_path_for(legacy)
        if legacy.exists() and (not download or tag_path.exists()):
            tag = tag_path.read_text(encoding="utf-8").strip() if tag_path.exists() else ""
            return legacy, tag or None
        return None, None
        
        
    @staticmethod
    def _cache_root() -> Path:
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA") or str(Path.home())
            return Path(base)
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Caches"
        return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")

    def _connect(self) -> None:
        uri = self._path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        self._conn = conn

    def execute(self, sql: str, params: _Params = ()) -> sqlite3.Cursor:
        """Execute ``sql`` with ``params`` against the read-only connection.

        Args:
            sql: The SQL statement to execute.
            params: Bound parameters.

        Returns:
            The cursor returned by the ``sqlite3`` module.
        """
        conn = self._conn
        if conn is None:
            raise RuntimeError("connection closed")
        return conn.cursor().execute(sql, tuple(params))

    def close(self) -> None:
        """Close the underlying SQLite connection, if open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self._path)!r})"

    @property
    def path(self) -> Path:
        """The resolved path of the local database file."""
        return self._path

    # ------------------------------------------------------------------
    # release / version management
    # ------------------------------------------------------------------
    
    @classmethod
    def _fetch_json(cls, url: str, *, timeout: int = DOWNLOAD_TIMEOUT) -> Any:
        """Fetch and decode a JSON payload from ``url``.

        Raises:
            DownloadError: If the request fails or returns no JSON.
        """
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise DownloadError(f"Could not fetch releases from {url}: {exc}") from exc

    
    def latest_release(self, *, timeout: int = DOWNLOAD_TIMEOUT) -> str:
        """Return the tag of the newest upstream release.

        Args:
            timeout: Seconds to wait for the GitHub API request.

        Returns:
            The newest release tag, e.g. ``"2026.05.12"``.

        Raises:
            DownloadError: If the latest release cannot be determined.
        """
        data = self._fetch_json(RELEASES_API + "/latest", timeout=timeout)
        tag = data.get("tag_name") if isinstance(data, dict) else None
        if not tag:
            raise DownloadError(f"No latest release found at {RELEASES_API}")
        return str(tag)
 
    
    @staticmethod
    def _tag_path_for(db: Path) -> Path:
        """Return the path of the release-tag sidecar beside ``db``."""
        return Path(str(db) + RELEASE_TAG_SUFFIX)

 
    @staticmethod
    def _write_tag(db: Path, version: str) -> None:    
        """Record the release tag that produced the database at ``db``."""    
        Storage._tag_path_for(db).write_text(version + "\n", encoding="utf-8")

    def upgrade(self, version: str | None = None, *, timeout: int = DOWNLOAD_TIMEOUT) -> str:
        """Install the newest upstream release (or ``version``) and reopen.

        In the default versioned cache layout the new database is stored next
        to the previous one (``<cache>/cyg/<version>/cygnet.db``), so the old
        release remains available on disk and can still be selected later. The
        currently-open read-only connection is re-pointed at the new database.

        When an explicit ``db_path`` (or ``CYG_DB``) is used, the resolved
        file holds exactly one release: the new database is installed over it
        in place and the previous release is not retained.

        Args:
            version: Release tag to install, e.g. ``"2026.05.12"``. When
                ``None`` the latest release is used.
            timeout: Seconds to wait for the download.

        Returns:
            The installed release tag.

        Raises:
            DownloadError: If the release cannot be downloaded or installed.
        """
        tag = self.latest_release(timeout=timeout) if version is None else version

        if self._versioned:
            target = self._versioned_path(tag)
            if target == self._path:
                LOGGER.info("Already at the latest release %s", tag)
                return tag
            self.close()
            old_path, old_version = self._path, self._version
            self._path, self._version = target, tag
            try:
                if not target.exists():
                    self._install_release(target, tag, timeout=timeout)
            except Exception:
                self.close()
                self._path, self._version = old_path, old_version
                self._pass_validation()
                raise
            self._pass_validation()
            return tag

        if tag == self.current_release():
            LOGGER.info("Already at the latest release %s", tag)
            return tag
        self.close()
        try:
            self._install_release(self._path, tag, timeout=timeout)
        finally:
            self._pass_validation()
        return tag

    def _pass_validation(self) -> None:
        """Validate the current database and (re)open its read-only connection."""
        self._validate()
        self._connect()

    # ------------------------------------------------------------------
    # download / install
    # ------------------------------------------------------------------

    def _install_release(self, target: Path, version: str, *, timeout: int) -> None:       
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DownloadError(f"Could not create directory {target.parent}: {exc}") from exc

        temp_dir = Path(tempfile.mkdtemp(prefix="cyg-", dir=str(target.parent)))
        temp_gz = temp_dir / (target.name + ".gz")
        temp_db = temp_dir / target.name
        try:
            self._stream_download(temp_gz, version, timeout=timeout)
            self._decompress(temp_gz, temp_db)
            os.replace(temp_db, target)
            self._write_tag(target, version)
            LOGGER.info("Cygnet database %s stored at %s", version, target)
        except DownloadError:
            raise
        except (OSError, EOFError) as exc:
            raise DownloadError(f"Could not install the Cygnet database: {exc}") from exc
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _stream_download(self, target: Path, version: str, *, timeout: int) -> None:
        url = RELEASE_DOWNLOAD_URL.format(version=version, name=DATABASE_FILE_NAME + ".gz")      
        LOGGER.info("Downloading Cygnet database %s from %s", version, url)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                total = int(response.headers.get("Content-Length", 0) or 0)
                show_progress = total > 0 and sys.stderr.isatty()
                done = 0
                with target.open("wb") as out:
                    while chunk := response.read(1024 * 256):
                        out.write(chunk)
                        done += len(chunk)
                        if show_progress:
                            print(
                                f"\rCygnet database {version}: {done * 100 // total:3d}% "
                                f"({done / 1048576:.1f} MiB)",
                                end="",
                                file=sys.stderr,
                                flush=True,
                            )
                if show_progress:
                    print(file=sys.stderr)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise DownloadError(f"Could not download the Cygnet database: {exc}") from exc

    @staticmethod
    def _decompress(source: Path, target: Path) -> None:
        # Validate the gzip member is actually an SQLite database before
        # replacing anything on disk.
        with gzip.open(source, "rb") as gz:
            header = gz.read(len(SQLITE_HEADER))
        if header != SQLITE_HEADER:
            raise DownloadError("The downloaded archive is not a valid Cygnet database")
        with gzip.open(source, "rb") as gz, target.open("wb") as out:
            shutil.copyfileobj(gz, out)

    def _validate(self) -> None:
        if not self._path.exists():
            raise DatabaseNotFoundError(f"The Cygnet database was not found at {self._path}")
        try:
            with self._path.open("rb") as fh:
                header = fh.read(len(SQLITE_HEADER))
        except OSError as exc:
            raise DatabaseError(f"Could not read {self._path}: {exc}") from exc
        if header != SQLITE_HEADER:
            raise DatabaseError(f"{self._path} is not a valid SQLite database")

        try:
            conn = sqlite3.connect(str(self._path))
            try:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            raise DatabaseError(f"Could not open {self._path}: {exc}") from exc

        expected_tables = {
            "arasaac",           
            "definitions",
            "definition_annotations",            
            "entries",
            "example_annotations",
            "examples",
            "forms",
            "languages",
            "pronunciations",
            "relation_types", 
            "resources",  
            "sense_examples", 
            "sense_relations",            
            "senses",           
            "synset_relations",           
            "synsets",     
                      
        }
        missing = sorted(expected_tables - tables)
        if missing:
            raise DatabaseError(
                f"The given file is not a Cygnet database; missing tables: {', '.join(missing)}"
            )
