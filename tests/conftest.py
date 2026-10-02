"""Shared fixtures: real Cygnet database downloaded once per test session."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from cyg import Cygnet

_SCHEMA = """
CREATE TABLE relation_types (rowid INTEGER PRIMARY KEY, type TEXT NOT NULL UNIQUE);
CREATE TABLE languages (rowid INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT);
CREATE TABLE synsets (rowid INTEGER PRIMARY KEY, ili TEXT, pos TEXT NOT NULL);
CREATE TABLE entries (
    rowid INTEGER PRIMARY KEY,
    language_rowid INTEGER NOT NULL REFERENCES languages(rowid),
    pos TEXT NOT NULL
);
CREATE TABLE forms (
    rowid INTEGER PRIMARY KEY,
    entry_rowid INTEGER NOT NULL REFERENCES entries(rowid),
    form TEXT NOT NULL,
    normalized_form TEXT,
    rank INTEGER DEFAULT 1
);
CREATE TABLE pronunciations (
    rowid INTEGER PRIMARY KEY,
    form_rowid INTEGER NOT NULL REFERENCES forms(rowid),
    variety TEXT,
    pronunciation TEXT NOT NULL,
    audio TEXT
);
CREATE TABLE senses (
    rowid INTEGER PRIMARY KEY,
    entry_rowid INTEGER NOT NULL REFERENCES entries(rowid),
    synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    sense_index INTEGER DEFAULT 1
);
CREATE TABLE definitions (
    rowid INTEGER PRIMARY KEY,
    synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    definition TEXT,
    language_rowid INTEGER REFERENCES languages(rowid)
);
CREATE TABLE synset_relations (
    rowid INTEGER PRIMARY KEY,
    source_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    target_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    type_rowid INTEGER NOT NULL REFERENCES relation_types(rowid)
);
CREATE TABLE sense_relations (
    rowid INTEGER PRIMARY KEY,
    source_rowid INTEGER NOT NULL REFERENCES senses(rowid),
    target_rowid INTEGER NOT NULL REFERENCES senses(rowid),
    type_rowid INTEGER NOT NULL REFERENCES relation_types(rowid)
);
CREATE TABLE examples (rowid INTEGER PRIMARY KEY, example TEXT NOT NULL);
CREATE TABLE sense_examples (
    rowid INTEGER PRIMARY KEY,
    sense_rowid INTEGER NOT NULL REFERENCES senses(rowid),
    example_rowid INTEGER NOT NULL REFERENCES examples(rowid)
);
CREATE TABLE definition_annotations (
    rowid INTEGER PRIMARY KEY,
    definition_rowid INTEGER NOT NULL REFERENCES definitions(rowid),
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    sense_rowid INTEGER NOT NULL REFERENCES senses(rowid)
);
CREATE TABLE example_annotations (
    rowid INTEGER PRIMARY KEY,
    example_rowid INTEGER NOT NULL REFERENCES examples(rowid),
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    sense_rowid INTEGER NOT NULL REFERENCES senses(rowid)
);
CREATE TABLE resources (
    rowid INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    version TEXT,
    label TEXT,
    language_rowid INTEGER REFERENCES languages(rowid),
    url TEXT,
    citation TEXT,
    licence TEXT,
    email TEXT,
    status TEXT,
    confidence_score REAL,
    extra TEXT,
    synset_count INTEGER,
    sense_count INTEGER
);
CREATE TABLE arasaac (synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid), arasaac_id INTEGER NOT NULL);
CREATE TABLE core_synsets (synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid));
"""


def make_schema_db(path: str | Path) -> None:
    """Create a minimal valid Cygnet schema database at ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(_SCHEMA)


@pytest.fixture(scope="session")
def cyg(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Cygnet]:
    """Real Cygnet database downloaded once per test session."""
    target = tmp_path_factory.mktemp("cygnet-real") / "cygnet.db"
    client = Cygnet(db_path=str(target), download=True)
    yield client
    client.close()
