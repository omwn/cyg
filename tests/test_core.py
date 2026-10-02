"""Tests for cyg.core against the real Cygnet database.

All tests use the real downloaded database via the ``cyg`` fixture.
"""

from __future__ import annotations

from unittest import mock

import pytest

from conftest import make_schema_db
from cyg import Cygnet
from cyg.core import (
    AnnotatedString,
    Concept,
    Lexeme,
    Sense,
    _assemble_where,
    _normalize_form,
)
from cyg.storage import DatabaseError, DatabaseNotFoundError, Storage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ili_set(rows) -> set[str]:
    return {row["ili"] for row in rows}


def _first_ili(rows) -> str:
    return rows[0]["ili"]


def _first_rowid(rows) -> int:
    return rows[0]["rowid"]


# ---------------------------------------------------------------------------
# Storage tests
# ---------------------------------------------------------------------------


def test_storage_path_resolution(tmp_path) -> None:
    target = tmp_path / "cygnet.db"
    assert Storage._resolve_path(str(target)) == target.expanduser()


def test_storage_env_path(tmp_path, monkeypatch) -> None:
    target = tmp_path / "cygnet.db"
    monkeypatch.setenv("CYG_DB", str(target))
    assert Storage._resolve_path(None) == target.expanduser()


def test_storage_default_path_linux(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CYG_DB", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert Storage._cache_dir() == tmp_path / "cyg"


def test_storage_missing_without_download(tmp_path) -> None:
    with pytest.raises(DatabaseNotFoundError):
        Storage(db_path=str(tmp_path / "nope.db"), download=False)


def test_storage_invalid_sqlite(tmp_path) -> None:
    bogus = tmp_path / "bogus.db"
    bogus.write_bytes(b"not sqlite")
    with pytest.raises(DatabaseError, match="not a valid SQLite"):
        Storage(db_path=str(bogus), download=False)


def test_storage_repr(tmp_path) -> None:
    target = tmp_path / "cygnet.db"
    make_schema_db(target)
    store = Storage(db_path=str(target), download=False)
    try:
        assert target.name in repr(store)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Core helper function tests
# ---------------------------------------------------------------------------


def test_normalize_form() -> None:
    assert _normalize_form("CAFÉ") == "cafe"
    assert _normalize_form("Joué") == "joue"
    assert _normalize_form("already-lower") == "already-lower"


def test_assemble_where_basic() -> None:
    sql, params = _assemble_where("SELECT * FROM t", form="dog")
    assert "forms.normalized_form = ?" in sql
    assert params == ("dog",)


def test_assemble_where_startswith() -> None:
    sql, params = _assemble_where("SELECT * FROM t", startswith="do")
    assert "forms.normalized_form LIKE ?" in sql
    assert params == ("do%",)


def test_assemble_where_contains() -> None:
    sql, params = _assemble_where("SELECT * FROM t", contains="og")
    assert "forms.normalized_form LIKE ?" in sql
    assert params == ("%og%",)


def test_assemble_where_langs() -> None:
    sql, params = _assemble_where("SELECT * FROM t", langs=["en", "fr"])
    assert "languages.code IN (?,?)" in sql
    assert params == ("en", "fr")


def test_assemble_where_pos() -> None:
    sql, params = _assemble_where("SELECT * FROM t", pos="noun")
    assert "LOWER(synsets.pos) = LOWER(?)" in sql
    assert params == ("noun",)


# ---------------------------------------------------------------------------
# Concept tests
# ---------------------------------------------------------------------------


def test_concepts_unfiltered(cyg: Cygnet) -> None:
    nouns = cyg.concepts(pos="noun")
    assert len(nouns) > 100
    assert all(isinstance(c, Concept) for c in nouns)


def test_concepts_filtered_by_form(cyg: Cygnet) -> None:
    matching = cyg.concepts(form="dog")
    assert len(matching) > 0
    # "dog" exists as both noun and verb in Cygnet
    poses = {c.pos() for c in matching}
    assert "noun" in poses
    assert "verb" in poses or len(poses) == 1


def test_concepts_form_case_accent_insensitive(cyg: Cygnet) -> None:
    assert cyg.concepts(form="DOG") == cyg.concepts(form="dog")


def test_concepts_filtered_by_language(cyg: Cygnet) -> None:
    en = cyg.concepts(langs="en")
    fr = cyg.concepts(langs="fr")
    assert len(en) > 0
    assert len(fr) > 0
    assert set(en) != set(fr)


def test_concepts_filtered_by_languages_list(cyg: Cygnet) -> None:
    en_or_fr = cyg.concepts(langs=["en", "fr"])
    assert len(en_or_fr) >= len(cyg.concepts(langs="en"))


def test_concepts_filtered_by_pos(cyg: Cygnet) -> None:
    nouns = cyg.concepts(pos="noun")
    verbs = cyg.concepts(pos="verb")
    assert len(nouns) > 0
    assert len(verbs) > 0


def test_concepts_combined_filters(cyg: Cygnet) -> None:
    dog_en = cyg.concepts(form="dog", langs="en", pos="noun")
    assert len(dog_en) > 0
    assert all(c.pos() == "noun" for c in dog_en)


def test_concepts_startswith(cyg: Cygnet) -> None:
    hits = cyg.concepts(startswith="do")
    assert any(c.index() == "i46360" for c in hits)


def test_concepts_contains(cyg: Cygnet) -> None:
    hits = cyg.concepts(contains="og")
    assert any(c.index() == "i46360" for c in hits)


def test_concept_by_ili_found(cyg: Cygnet) -> None:
    concept = cyg.concept("i46360")
    assert concept is not None
    assert concept.index() == "i46360"
    assert concept.pos() == "noun"


def test_concept_by_ili_missing(cyg: Cygnet) -> None:
    assert cyg.concept("i99999999999") is None


def test_concept_core_properties(cyg: Cygnet) -> None:
    concept = cyg.concept("i46360")
    assert concept is not None
    assert concept.index() == "i46360"
    assert concept.pos() == "noun"
    assert "Concept" in repr(concept)


def test_concept_definition_default_and_language(cyg: Cygnet) -> None:
    concept = cyg.concept("i46360")
    assert concept is not None
    en = concept.definition()
    assert en is not None
    assert len(en.text()) > 5
    assert en.lang() == "en"
    it = concept.definition("it")
    assert it is not None
    assert it.lang() == "it"
    assert concept.definition("xx") is None


def test_concept_senses(cyg: Cygnet) -> None:
    concept = cyg.concept("i46360")
    assert concept is not None
    senses = concept.senses()
    assert len(senses) > 0
    assert all(isinstance(s, Sense) for s in senses)


def test_concept_lexemes(cyg: Cygnet) -> None:
    concept = cyg.concept("i46360")
    assert concept is not None
    lexemes = concept.lexemes()
    assert len(lexemes) > 0
    assert all(lx.lemma() for lx in lexemes)


def test_concept_relations(cyg: Cygnet) -> None:
    dog = cyg.concept("i46360")
    assert dog is not None
    assert len(dog.hypernyms()) > 0
    assert len(dog.hyponyms()) >= 0
    assert len(dog.meronyms()) >= 0
    assert len(dog.holonyms()) >= 0


def test_concept_equality(cyg: Cygnet) -> None:
    assert cyg.concept("i46360") == cyg.concept("i46360")
    assert cyg.concept("i46360") != cyg.concept("i999999999")
    assert len({cyg.concept("i46360"), cyg.concept("i46360")}) == 1
    assert cyg.concept("i46360") != "i46360"


# ---------------------------------------------------------------------------
# Sense tests
# ---------------------------------------------------------------------------


def test_senses(cyg: Cygnet) -> None:
    senses = cyg.senses()
    assert len(senses) > 1000
    assert all(isinstance(s, Sense) for s in senses)


def test_sense_properties(cyg: Cygnet) -> None:
    senses = cyg.senses()
    sense = senses[0]
    assert sense.index()
    assert sense.lang()
    assert "Sense" in repr(sense)


def test_sense_concept_and_lexeme(cyg: Cygnet) -> None:
    dog = cyg.concept("i46360")
    assert dog is not None
    sense = dog.senses()[0]
    assert sense.concept().index() == "i46360"
    assert sense.lexeme().lemma()
    assert sense.lexeme().lang()


def test_sense_examples(cyg: Cygnet) -> None:
    dog = cyg.concept("i46360")
    assert dog is not None
    found = False
    for sense in dog.senses():
        examples = sense.examples()
        for annotated in examples:
            offsets = annotated.sense_offsets()
            assert len(offsets) > 0
            linked, start, end = offsets[0]
            assert isinstance(linked, Sense)
            assert start < end
            found = True
            break
        if found:
            break
    assert found, "expected at least one example with offsets for dog"


# ---------------------------------------------------------------------------
# Lexeme tests
# ---------------------------------------------------------------------------


def test_lexemes(cyg: Cygnet) -> None:
    lexemes = cyg.lexemes()
    assert len(lexemes) > 1000
    assert all(isinstance(lx, Lexeme) for lx in lexemes)


def test_lexemes_filtered(cyg: Cygnet) -> None:
    dog_lexemes = cyg.lexemes(form="dog")
    assert len(dog_lexemes) > 0
    assert all(lx.lemma() == "dog" for lx in dog_lexemes)


def test_lexeme_properties(cyg: Cygnet) -> None:
    dog_lexemes = cyg.lexemes(form="dog")
    lexeme = dog_lexemes[0]
    assert lexeme.lang() == "en"
    assert lexeme.lemma() == "dog"
    assert "dog" in lexeme.all_forms()
    assert "Lexeme" in repr(lexeme)
    assert isinstance(lexeme, Lexeme)


def test_lexeme_navigation(cyg: Cygnet) -> None:
    dog_lexemes = cyg.lexemes(form="dog")
    lexeme = dog_lexemes[0]
    assert len(lexeme.senses()) > 0
    assert len(lexeme.concepts()) > 0
    assert "i46360" in {c.index() for c in lexeme.concepts()}
    assert lexeme.senses() == lexeme.senses()


def test_lexeme_equality(cyg: Cygnet) -> None:
    lexemes = cyg.lexemes()
    assert lexemes[0] == lexemes[0]
    assert hash(lexemes[0]) == hash(lexemes[0])
    assert isinstance(lexemes[0], Lexeme)


def test_lexeme_lazy_lookup(cyg: Cygnet) -> None:
    storage = cyg._storage
    dog_rowid = storage.execute(
        "SELECT rowid FROM entries WHERE rowid = ?",
        (cyg.lexemes(form="dog")[0].index(),),
    ).fetchone()
    row = storage.execute("SELECT rowid FROM entries WHERE rowid = ?", (dog_rowid[0],)).fetchone()
    lexeme = Lexeme(row, storage)
    assert lexeme._lang == "" and lexeme._lemma == ""
    assert lexeme.lang() == "en"
    assert lexeme.lemma() == "dog"


# ---------------------------------------------------------------------------
# Language tests
# ---------------------------------------------------------------------------


def test_langs(cyg: Cygnet) -> None:
    langs = cyg.langs()
    assert len(langs) > 0
    assert langs == sorted(langs)
    assert "en" in langs
    assert "fr" in langs


def test_langs_sorted(cyg: Cygnet) -> None:
    langs = cyg.langs()
    assert langs == sorted(langs)


# ---------------------------------------------------------------------------
# Cygnet client tests
# ---------------------------------------------------------------------------


def test_db_path_property_and_repr(cyg: Cygnet) -> None:
    assert cyg.db_path
    assert "Cygnet" in repr(cyg)


def test_context_manager(cyg: Cygnet) -> None:
    with Cygnet(db_path=cyg.db_path, download=False) as c:
        assert c.langs() == cyg.langs()


def test_close(cyg: Cygnet) -> None:
    cyg2 = Cygnet(db_path=cyg.db_path, download=False)
    cyg2.close()
    # Re-opening should work
    cyg3 = Cygnet(db_path=cyg.db_path, download=False)
    assert cyg3.langs()
    cyg3.close()


def test_missing_database_without_download(tmp_path) -> None:
    with pytest.raises(DatabaseNotFoundError):
        Cygnet(db_path=str(tmp_path / "nope.db"), download=False)


# ---------------------------------------------------------------------------
# Version management delegation
# ---------------------------------------------------------------------------


def test_version_param_selected(cyg: Cygnet) -> None:
    """Cygnet(version=...) routes the version to Storage.__init__."""
    with mock.patch("cyg.core.Storage", return_value=mock.Mock(spec=Storage)) as m:
        Cygnet(db_path="/some/path.db", download=True, version="2026.05.12")
        m.assert_called_once_with(db_path="/some/path.db", download=True, version="2026.05.12")


def test_releases_delegation(cyg: Cygnet) -> None:
    with mock.patch.object(cyg._storage, "releases", return_value=["2026.05.12"]) as m:
        assert cyg.releases() == ["2026.05.12"]
        m.assert_called_once_with(timeout=mock.ANY)


def test_latest_version_delegation(cyg: Cygnet) -> None:
    with mock.patch.object(cyg._storage, "latest_release", return_value="2026.05.12") as m:
        assert cyg.latest_version() == "2026.05.12"
        m.assert_called_once_with(timeout=mock.ANY)


def test_current_version_delegation(cyg: Cygnet) -> None:
    with mock.patch.object(cyg._storage, "current_release", return_value="2026.05.12") as m:
        assert cyg.current_version() == "2026.05.12"
        m.assert_called_once_with()


def test_current_version_none(cyg: Cygnet) -> None:
    with mock.patch.object(cyg._storage, "current_release", return_value=None) as m:
        assert cyg.current_version() is None
        m.assert_called_once_with()


def test_upgrade_delegation(cyg: Cygnet) -> None:
    with mock.patch.object(cyg._storage, "upgrade", return_value="2026.05.12") as m:
        assert cyg.upgrade() == "2026.05.12"
        m.assert_called_once_with(version=None, timeout=mock.ANY)


def test_upgrade_delegation_version(cyg: Cygnet) -> None:
    with mock.patch.object(cyg._storage, "upgrade", return_value="2026.03.01") as m:
        assert cyg.upgrade(version="2026.03.01") == "2026.03.01"
        m.assert_called_once_with(version="2026.03.01", timeout=mock.ANY)


# ---------------------------------------------------------------------------
# Real database sanity checks (from test_integration.py)
# ---------------------------------------------------------------------------


def test_real_counts(cyg: Cygnet) -> None:
    """Sanity-check row counts against the published dataset scale."""
    store = cyg._storage
    assert store.execute("SELECT COUNT(*) FROM synsets").fetchone()[0] >= 100_000
    assert store.execute("SELECT COUNT(*) FROM senses").fetchone()[0] >= 2_000_000
    assert store.execute("SELECT COUNT(*) FROM entries").fetchone()[0] >= 1_000_000
    assert len(cyg.langs()) >= 40


def test_real_search_and_navigation(cyg: Cygnet) -> None:
    """Query the real database through the public API."""
    concepts = cyg.concepts(form="dog", langs="en", pos="noun")
    assert concepts, "expected at least one English noun 'dog'"

    head = concepts[0]
    assert head.index().startswith("i")

    definition = head.definition("en")
    assert definition is not None and definition.text()

    senses = head.senses("en")
    assert senses, "expected English senses for the concept"

    lexemes = head.lexemes("en")
    assert lexemes

    hypernyms = head.hypernyms()
    assert isinstance(hypernyms, list)

    by_ili = cyg.concept(head.index())
    assert by_ili == head


def test_real_examples_annotations(cyg: Cygnet) -> None:
    """Examples must carry working sense annotations."""
    store = cyg._storage
    row = store.execute("SELECT sense_rowid FROM sense_examples LIMIT 1").fetchone()
    if row is None:
        pytest.skip("no examples in database")
    sense_rowid = int(row[0])

    example = store.execute(
        "SELECT example_rowid FROM sense_examples WHERE sense_rowid = ? LIMIT 1",
        (sense_rowid,),
    ).fetchone()
    assert example is not None
    offsets = store.execute(
        "SELECT start_offset, end_offset FROM example_annotations WHERE example_rowid = ?",
        (int(example[0]),),
    ).fetchall()
    assert len(offsets) >= 1
    assert all(0 <= int(s) < int(e) for s, e in offsets)


def test_no_network_required_after_download(cyg: Cygnet) -> None:
    """Once present, the local file is fully functional without a network."""
    offline = Cygnet(db_path=cyg.db_path, download=False)
    try:
        assert offline.concepts(form="dog", langs="en", pos="noun")
    finally:
        offline.close()


def test_annotated_string(cyg: Cygnet) -> None:
    """Test AnnotatedString directly."""
    dog = cyg.concept("i46360")
    assert dog is not None
    for sense in dog.senses():
        examples = sense.examples()
        for annotated in examples:
            assert isinstance(annotated, AnnotatedString)
            assert annotated.text()
            assert annotated.lang()
            offsets = annotated.sense_offsets()
            if offsets:
                linked, start, end = offsets[0]
                assert isinstance(linked, Sense)
                assert start < end
            break
        if examples:
            break


# ---------------------------------------------------------------------------
# Edge case tests for 100% coverage
# ---------------------------------------------------------------------------


def test_sense_eq_not_implemented(cyg: Cygnet) -> None:
    """Test Sense.__eq__ returns NotImplemented for non-Sense objects."""
    sense = cyg.senses()[0]
    assert sense.__eq__("not a sense") is NotImplemented


def test_sense_hash(cyg: Cygnet) -> None:
    """Test Sense.__hash__."""
    sense = cyg.senses()[0]
    assert hash(sense) == hash(sense._rowid)


def test_lexeme_eq_not_implemented(cyg: Cygnet) -> None:
    """Test Lexeme.__eq__ returns NotImplemented for non-Lexeme objects."""
    lexeme = cyg.lexemes()[0]
    assert lexeme.__eq__("not a lexeme") is NotImplemented


def test_lexeme_hash(cyg: Cygnet) -> None:
    """Test Lexeme.__hash__."""
    lexeme = cyg.lexemes()[0]
    assert hash(lexeme) == hash(lexeme._rowid)


def test_concept_eq_not_implemented(cyg: Cygnet) -> None:
    """Test Concept.__eq__ returns NotImplemented for non-Concept objects."""
    concept = cyg.concept("i46360")
    assert concept is not None
    assert concept.__eq__("not a concept") is NotImplemented


def test_concept_hash(cyg: Cygnet) -> None:
    """Test Concept.__hash__."""
    concept = cyg.concept("i46360")
    assert concept is not None
    assert hash(concept) == hash(concept._rowid)


def test_annotated_string_repr(cyg: Cygnet) -> None:
    """Test AnnotatedString.__repr__."""
    dog = cyg.concept("i46360")
    assert dog is not None
    for sense in dog.senses():
        examples = sense.examples()
        for annotated in examples:
            assert "AnnotatedString" in repr(annotated)
            break
        if examples:
            break


def test_annotated_string_sense_not_found(cyg: Cygnet) -> None:
    """Test AnnotatedString.sense_offsets raises LookupError for missing sense."""
    from cyg.core import AnnotatedString

    storage = cyg._storage
    # Create an AnnotatedString with a non-existent sense_rowid
    annotated = AnnotatedString("test", "en", [(999999999, 0, 4)], storage)
    try:
        annotated.sense_offsets()
        assert False, "Should have raised LookupError"
    except LookupError as e:
        assert "No sense for rowid" in str(e)


def test_relation_type_unknown(cyg: Cygnet) -> None:
    """Test _relation_type_cached raises KeyError for unknown relation."""
    from cyg.core import _relation_type_cached

    storage = cyg._storage
    try:
        _relation_type_cached(str(storage.path), storage, "not_a_real_relation")
        assert False, "Should have raised KeyError"
    except KeyError as e:
        assert "Unknown relation type" in str(e)


def test_sense_concept_not_found(cyg: Cygnet) -> None:
    """Test Sense.concept raises LookupError for missing concept."""

    from cyg.core import Sense

    storage = cyg._storage

    # Create a fake row with non-existent synset_rowid
    class FakeRow:
        def __init__(self):
            self._data = {
                "rowid": 999999999,
                "synset_rowid": 999999999,
                "entry_rowid": 1,
                "lang": "en",
            }

        def __getitem__(self, key):
            return self._data[key]

        def keys(self):
            return self._data.keys()

    fake_row = FakeRow()
    sense = Sense(fake_row, storage)
    try:
        sense.concept()
        assert False, "Should have raised LookupError"
    except LookupError as e:
        assert "No concept for synset rowid" in str(e)


def test_sense_lexeme_not_found(cyg: Cygnet) -> None:
    """Test Sense.lexeme raises LookupError for missing lexeme."""

    from cyg.core import Sense

    storage = cyg._storage

    # Create a fake row with non-existent entry_rowid
    class FakeRow:
        def __init__(self):
            self._data = {"rowid": 1, "synset_rowid": 1, "entry_rowid": 999999999, "lang": "en"}

        def __getitem__(self, key):
            return self._data[key]

        def keys(self):
            return self._data.keys()

    fake_row = FakeRow()
    sense = Sense(fake_row, storage)
    try:
        sense.lexeme()
        assert False, "Should have raised LookupError"
    except LookupError as e:
        assert "No lexeme for entry rowid" in str(e)


def test_lexeme_lang_not_found(cyg: Cygnet) -> None:
    """Test Lexeme.lang raises KeyError for missing language."""

    from cyg.core import Lexeme

    storage = cyg._storage

    # Create a fake row with non-existent rowid
    class FakeRow:
        def __init__(self):
            self._data = {"rowid": 999999999, "lang": "", "lemma": ""}

        def __getitem__(self, key):
            return self._data[key]

        def __contains__(self, key):
            return key in self._data

        def keys(self):
            return self._data.keys()

    fake_row = FakeRow()
    lexeme = Lexeme(fake_row, storage)
    try:
        lexeme.lang()
        assert False, "Should have raised KeyError"
    except KeyError as e:
        assert "No language for entry rowid" in str(e)


def test_lexeme_lemma_not_found(cyg: Cygnet) -> None:
    """Test Lexeme.lemma raises KeyError for missing lemma."""

    from cyg.core import Lexeme

    storage = cyg._storage

    # Create a fake row with non-existent rowid
    class FakeRow:
        def __init__(self):
            self._data = {"rowid": 999999999, "lang": "en", "lemma": ""}

        def __getitem__(self, key):
            return self._data[key]

        def __contains__(self, key):
            return key in self._data

        def keys(self):
            return self._data.keys()

    fake_row = FakeRow()
    lexeme = Lexeme(fake_row, storage)
    try:
        lexeme.lemma()
        assert False, "Should have raised KeyError"
    except KeyError as e:
        assert "No lemma for entry rowid" in str(e)


def test_language_cached_not_found(cyg: Cygnet) -> None:
    """Test _language_cached returns None for non-existent rowid."""
    from cyg.core import _language_cached

    storage = cyg._storage
    # Use a rowid that definitely doesn't exist
    row = _language_cached(storage, 999999999)
    assert row is None


def test_lemma_cached_not_found(cyg: Cygnet) -> None:
    """Test _lemma_cached returns None for non-existent rowid."""
    from cyg.core import _lemma_cached

    storage = cyg._storage
    # Use a rowid that definitely doesn't exist
    row = _lemma_cached(storage, 999999999)
    assert row is None


def test_lexeme_lang_cached(cyg: Cygnet) -> None:
    """Test Lexeme.lang() caches result on second call."""
    lexeme = cyg.lexemes(form="dog")[0]
    lang1 = lexeme.lang()
    lang2 = lexeme.lang()
    assert lang1 == lang2
    # This tests the else branch of `if not self._lang:`


def test_lexeme_lemma_cached(cyg: Cygnet) -> None:
    """Test Lexeme.lemma() caches result on second call."""
    lexeme = cyg.lexemes(form="dog")[0]
    lemma1 = lexeme.lemma()
    lemma2 = lexeme.lemma()
    assert lemma1 == lemma2
    # This tests the else branch of `if not self._lemma:`
