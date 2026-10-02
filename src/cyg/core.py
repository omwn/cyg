"""Core implementation: Cygnet client, domain models, and SQL queries."""

from __future__ import annotations

import functools
import sqlite3
from collections.abc import Iterable
from typing import Any, Literal, cast

from .storage import DOWNLOAD_TIMEOUT, Storage
#from collections import defaultdict
from itertools import groupby


POS = Literal["noun", "verb", "adj", "adv", "adp", "unk", "conj", "nref"]

# ---------------------------------------------------------------------------
# Basic SQL queries (module-level constants for prepared-statement reuse)
# ---------------------------------------------------------------------------

_CONCEPT_COLUMNS = "synsets.rowid AS rowid, LOWER(synsets.pos) AS pos, synsets.ili AS ili"

#-------- concepts

_SQL_CONCEPTS = f"""
    SELECT DISTINCT {_CONCEPT_COLUMNS}
    FROM synsets
    JOIN senses ON synsets.rowid = senses.synset_rowid
    JOIN entries ON entries.rowid = senses.entry_rowid
    JOIN languages ON languages.rowid = entries.language_rowid
"""

_SQL_CONCEPT_BY_ILI = f"""
    SELECT {_CONCEPT_COLUMNS}
    FROM synsets
    WHERE synsets.ili = ?
"""

_SQL_DEFINITION = """
    SELECT definitions.definition AS definition, languages.code AS lang
    FROM definitions
    JOIN languages ON languages.rowid = definitions.language_rowid
    WHERE definitions.synset_rowid = ? AND languages.code = ?
"""

_SQL_CONCEPT_BY_ROWID = f"""
    SELECT {_CONCEPT_COLUMNS}
    FROM synsets
    WHERE synsets.rowid = ?
"""

_SQL_CONCEPTS_BY_LEXEME = """
    SELECT synsets.rowid AS rowid, LOWER(synsets.pos) AS pos, synsets.ili AS ili
    FROM senses
    JOIN synsets ON synsets.rowid = senses.synset_rowid
    WHERE senses.entry_rowid = ?
    ORDER BY synsets.rowid ASC
"""

_SQL_RELATION_TYPE = "SELECT rowid FROM relation_types WHERE type = ?"

_SQL_SPEC_RELATED = """
    SELECT synsets.rowid AS rowid, LOWER(synsets.pos) AS pos, synsets.ili AS ili
    FROM synset_relations
    JOIN synsets ON synsets.rowid = synset_relations.target_rowid
    WHERE synset_relations.source_rowid = ? AND synset_relations.type_rowid = ?
    ORDER BY synsets.rowid ASC
"""

_SQL_ALL_RELATED = """
    SELECT synsets.rowid AS rowid, LOWER(synsets.pos) AS pos, synsets.ili AS ili, relation_types.type AS type
    FROM synset_relations
    JOIN synsets ON synsets.rowid = synset_relations.target_rowid
    JOIN relation_types ON  relation_types.rowid = synset_relations.type_rowid
"""

#-------- lexemes

_SQL_LEXEMES = """
    SELECT DISTINCT entries.rowid AS rowid,
           languages.code AS lang,
           (SELECT normalized_form FROM forms
             WHERE forms.entry_rowid = entries.rowid AND forms.rank = 0
             LIMIT 1) AS lemma
    FROM entries
    JOIN languages ON languages.rowid = entries.language_rowid
"""

_SQL_LEXEMES_BY_CONCEPT = """
    SELECT DISTINCT senses.entry_rowid AS rowid,
           languages.code AS lang,
           (SELECT normalized_form FROM forms
             WHERE forms.entry_rowid = senses.entry_rowid AND forms.rank = 0
             LIMIT 1) AS lemma
    FROM senses
    JOIN entries ON entries.rowid = senses.entry_rowid
    JOIN languages ON languages.rowid = entries.language_rowid
    WHERE senses.synset_rowid = ?
"""

_SQL_LEXEME_BY_ENTRY = """
    SELECT entries.rowid AS rowid,
           languages.code AS lang,
           (SELECT normalized_form FROM forms
             WHERE forms.entry_rowid = entries.rowid AND forms.rank = 0
             LIMIT 1) AS lemma
    FROM entries
    JOIN languages ON languages.rowid = entries.language_rowid
    WHERE entries.rowid = ?
"""

_SQL_LEMMA_BY_ENTRY = """
    SELECT normalized_form AS lemma
    FROM forms
    WHERE forms.entry_rowid = ? AND forms.rank = 0
    ORDER BY forms.rowid LIMIT 1
"""

_SQL_FORMS_BY_ENTRY = """
    SELECT form AS form
    FROM forms
    WHERE forms.entry_rowid = ?
    ORDER BY form ASC
"""


#-------- senses

_SQL_SENSES = """
    SELECT DISTINCT senses.rowid AS rowid,
           senses.synset_rowid AS synset_rowid,
           senses.entry_rowid AS entry_rowid,
           languages.code AS lang,
           entries.pos AS entry_pos
    FROM senses
    JOIN entries ON entries.rowid = senses.entry_rowid
    JOIN languages ON languages.rowid = entries.language_rowid
"""

_SQL_SENSES_BY_CONCEPT = """
    SELECT DISTINCT senses.rowid AS rowid,
           senses.synset_rowid AS synset_rowid,
           senses.entry_rowid AS entry_rowid,
           languages.code AS lang
    FROM senses
    JOIN entries ON entries.rowid = senses.entry_rowid
    JOIN languages ON languages.rowid = entries.language_rowid
    WHERE senses.synset_rowid = ?
"""

_SQL_SENSES_BY_LEXEME = """
    SELECT senses.rowid AS rowid,
           senses.synset_rowid AS synset_rowid,
           senses.entry_rowid AS entry_rowid,
           languages.code AS lang
    FROM senses
    JOIN entries ON entries.rowid = senses.entry_rowid
    JOIN languages ON languages.rowid = entries.language_rowid
    WHERE senses.entry_rowid = ?
    ORDER BY senses.rowid ASC
"""

_SQL_SENSE_BY_ROWID = """
    SELECT senses.rowid AS rowid,
           senses.synset_rowid AS synset_rowid,
           senses.entry_rowid AS entry_rowid,
           languages.code AS lang
    FROM senses
    JOIN entries ON entries.rowid = senses.entry_rowid
    JOIN languages ON languages.rowid = entries.language_rowid
    WHERE senses.rowid = ?
"""


_SQL_EXAMPLES = """
    SELECT examples.example AS example,
           languages.code AS lang,
           example_annotations.sense_rowid AS sense_rowid,
           example_annotations.start_offset AS start_offset,
           example_annotations.end_offset AS end_offset
    FROM examples
    JOIN sense_examples ON sense_examples.example_rowid = examples.rowid
    JOIN example_annotations ON example_annotations.example_rowid = examples.rowid
        AND example_annotations.sense_rowid = sense_examples.sense_rowid
    JOIN senses ON senses.rowid = sense_examples.sense_rowid
    JOIN entries ON entries.rowid = senses.entry_rowid
    JOIN languages ON languages.rowid = entries.language_rowid
    WHERE sense_examples.sense_rowid = ?
    ORDER BY examples.example ASC
"""

#-------- languages
_SQL_LANGUAGES = "SELECT code FROM languages ORDER BY code"

_SQL_LANGUAGE_BY_ENTRY = """
    SELECT languages.code AS lang
    FROM entries
    JOIN languages ON languages.rowid = entries.language_rowid
    WHERE entries.rowid = ?
"""


_Params = Iterable[Any]


# ---------------------------------------------------------------------------
# Function for queries containing optional elements
# ---------------------------------------------------------------------------

def _assemble_where(
    base_sql: str,
    *,
    form: str | None = None,
    langs: list[str] | str | None = None,
    pos: str | None = None,
    startswith: str | None = None,
    contains: str | None = None,
) -> tuple[str, tuple[object, ...]]:
    """Append JOIN forms and WHERE conditions to base_sql."""
    langs = [langs] if isinstance(langs, str) else list(langs) if langs is not None else None    
    needs_forms = form is not None or startswith is not None or contains is not None
    sql = base_sql + (" JOIN forms ON forms.entry_rowid = entries.rowid" if needs_forms else "")

    conditions: list[str] = []
    params: list[object] = []

    if form is not None:
        conditions.append("forms.form = ?")
        params.append(form)
    if startswith is not None:
        conditions.append("forms.form LIKE ?")
        params.append(startswith + "%")
    if contains is not None:
        conditions.append("forms.form LIKE ?")
        params.append("%" + contains + "%")
    if langs:
        placeholders = ",".join("?" * len(langs))
        conditions.append(f"languages.code IN ({placeholders})")
        params.extend(langs)
    if pos:
        conditions.append("LOWER(synsets.pos) = LOWER(?)")
        params.append(pos)
    
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    return sql, tuple(params)
 

    
# ---------------------------------------------------------------------------
# Shortcuts to query the database
# ---------------------------------------------------------------------------

def _select(storage: Storage, sql: str, params: _Params = ()) -> list[sqlite3.Row]:
    """Retrieves corresponding all rows."""
    return storage.execute(sql, tuple(params)).fetchall()


def _select_one(storage: Storage, sql: str, params: _Params = ()) -> sqlite3.Row | None:
    """Retrieves only the first row."""
    cursor = storage.execute(sql, tuple(params))
    result = cursor.fetchone()
    if result is None:
        return None
    return cast("sqlite3.Row", result)




# ---------------------------------------------------------------------------
# Cached lookups (bounded, scoped by database path)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=64)
def _relation_type_cached(db_path: str, storage: Storage, relation_name: str) -> int:
    del db_path  # only used for cache scoping
    row = storage.execute(_SQL_RELATION_TYPE, (relation_name,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown relation type: {relation_name}")
    return int(row[0])


@functools.lru_cache(maxsize=32768)
def _sense_cached(db_path: str, storage: Storage, sense_rowid: int) -> sqlite3.Row | None:
    del db_path
    return _select_one(storage, _SQL_SENSE_BY_ROWID, (sense_rowid,))


def _language_cached(storage: Storage, entry_rowid: int) -> sqlite3.Row | None:
    return _select_one(storage, _SQL_LANGUAGE_BY_ENTRY, (entry_rowid,))


def _lemma_cached(storage: Storage, entry_rowid: int) -> sqlite3.Row | None:
    return _select_one(storage, _SQL_LEMMA_BY_ENTRY, (entry_rowid,))


@functools.lru_cache(maxsize=32768)
def _lexeme_cached(db_path: str, storage: Storage, entry_rowid: int) -> sqlite3.Row | None:
    del db_path
    return _select_one(storage, _SQL_LEXEME_BY_ENTRY, (entry_rowid,))


# ---------------------------------------------------------------------------
# Domain models (classes wrapping sqlite3.Row + Storage)
# ---------------------------------------------------------------------------


class Concept:
    """A language-independent concept (synset)."""

    __slots__ = ("_rowid", "_pos", "_ili", "_storage")

    def __init__(self, row: sqlite3.Row, storage: Storage) -> None:
        self._rowid = int(row["rowid"])
        self._pos = str(row["pos"])
        self._ili = str(row["ili"])
        self._storage = storage

    def __repr__(self) -> str:
        return f"Concept(id={self._rowid})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Concept):
            return NotImplemented
        return self._rowid == other._rowid

    def __hash__(self) -> int:
        return hash(self._rowid)

    def index(self) -> str:
        """Returns the unique identifier (ILI) for this concept."""
        return self._ili

    def pos(self) -> POS:
        """Returns the part of speech (ontological category) of this concept."""
        return self._pos

    def definition(self, lang: str = "en") -> AnnotatedString | None:
        """Returns the definition of this concept in the given language if available."""
        row = _select_one(self._storage, _SQL_DEFINITION, (self._rowid, lang))
        if row is None:  
            return AnnotatedString(
            text="No definition available in the chosen language.",
            lang="",
            offsets=[],
            storage=self._storage,
        )
        else:
            return AnnotatedString(
            text=str(row["definition"]),
            lang=str(row["lang"]),
            offsets=[],
            storage=self._storage,
        )

    def senses(self, lang: str | None = None) -> list[Sense]:
        """Returns all senses linked to this concept, optionally filtered by language."""
        sql = _SQL_SENSES_BY_CONCEPT
        params: list[object] = [self._rowid]
        if lang is not None:
            sql += " AND languages.code = ?"
            params.append(lang)
        rows = _select(self._storage, sql + " ORDER BY senses.sense_index, senses.rowid", params)
        return [Sense(row, self._storage) for row in rows]

    def lexemes(self, lang: str | None = None) -> list[Lexeme]:
        """Returns all lexemes linked to senses of this concept, optionally filtered by language."""
        sql = _SQL_LEXEMES_BY_CONCEPT
        params: list[object] = [self._rowid]
        if lang is not None:
            sql += " AND languages.code = ?"
            params.append(lang)
        rows = _select(self._storage, sql + " ORDER BY senses.entry_rowid ASC", params)
        return [Lexeme(row, self._storage) for row in rows]

    def _related(self, relation_name: str) -> list[Concept]:
        type_rowid = _relation_type_cached(str(self._storage.path), self._storage, relation_name)
        rows = _select(self._storage, _SQL_SPEC_RELATED, (self._rowid, type_rowid))
        return [Concept(row, self._storage) for row in rows]

    def hypernyms(self) -> list[Concept]:
        """Returns concepts that are connected to this concept by a hypernym relation."""
        return self._related("hypernym")

    def hyponyms(self) -> list[Concept]:
        """Returns concepts that are connected to this concept by a hyponymy relation."""
        return self._related("hyponym")

    def meronyms(self) -> list[Concept]:
        """Returns concepts that are connected to this concept by a meronymy relation."""
        return self._related("meronym")

    def holonyms(self) -> list[Concept]:
        """Returns concepts that are connected to this concept by a holonymy relation."""
        return self._related("holonym")
        
    def get_related (self, relations:list[str] | str | None = None) -> dict[str, list[Concept]]:
        """Returns a dictionary of all concepts related by that concept by any relation or any specified relation."""

        rel_dic = {}   
        sql = _SQL_ALL_RELATED
        rel_conditions = " WHERE synset_relations.source_rowid = ?"
        rel_params = [self._rowid]
        if relations:
            rel_placeholders = ",".join("?" * len(relations)) 
            rel_conditions += f" AND relation_types.type IN ({rel_placeholders})"
            rel_params.extend(relations)
      
        rows = _select(self._storage, sql + rel_conditions + " ORDER BY synsets.rowid ASC", rel_params)
        rel_dic = {key: [Concept(row, self._storage) for row in group] for key, group in groupby(sorted(rows, key=lambda x: x[-1]), key=lambda x: x[-1])}

        return rel_dic
       
        
    
class Sense:
    """A pairing of a lexeme with a concept."""

    __slots__ = ("_rowid", "_synset_rowid", "_entry_rowid", "_lang", "_storage")

    def __init__(self, row: sqlite3.Row, storage: Storage) -> None:
        self._rowid = int(row["rowid"])
        self._synset_rowid = int(row["synset_rowid"])
        self._entry_rowid = int(row["entry_rowid"])
        self._lang = str(row["lang"]) if "lang" in row.keys() else "" 
        self._storage = storage

    def __repr__(self) -> str:
        return f"Sense(id={self._rowid})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sense):
            return NotImplemented
        return self._rowid == other._rowid

    def __hash__(self) -> int:
        return hash(self._rowid)

    def index(self) -> str:
        """Returns the unique identifier for this sense."""
        return str(self._rowid)

    def lang(self) -> str:        
        """Returns the language code of this sense's lexeme."""
        return self._lang

    def examples(self) -> list[AnnotatedString]:
        """Returns usage examples illustrating this sense."""
        rows = _select(self._storage, _SQL_EXAMPLES, (self._rowid,))
        if len(rows)==0:
            return [AnnotatedString(
                text="No example available for this sense!",
                lang="",
                offsets=[],
                storage=self._storage,
            )]
        else:
            return [
            AnnotatedString(
                text=str(row["example"]),
                lang=str(row["lang"]),
                offsets=[
                    (int(row["sense_rowid"]), int(row["start_offset"]), int(row["end_offset"]))
                ],
                storage=self._storage,
            )
            for row in rows
        ]
         
            

    def concept(self) -> Concept:
        """Returns the concept associated with this sense (the signified)."""
        row = _select_one(self._storage, _SQL_CONCEPT_BY_ROWID, (self._synset_rowid,))
        if row is None:
            raise LookupError(f"No concept for synset rowid {self._synset_rowid}")
        return Concept(row, self._storage)

    def lexeme(self) -> Lexeme:
        """Returns the lexeme associated with this sense (the signifier)."""
        row = _lexeme_cached(str(self._storage.path), self._storage, self._entry_rowid)
        if row is None:
            raise LookupError(f"No lexeme for entry rowid {self._entry_rowid}")
        return Lexeme(row, self._storage)


class Lexeme:
    """A word in a specific language, with its inflected forms."""

    __slots__ = ("_rowid", "_lang", "_lemma", "_storage")

    def __init__(self, row: sqlite3.Row, storage: Storage) -> None:
        self._rowid = int(row["rowid"])
        self._lang = str(row["lang"]) if "lang" in row.keys() else "" 
        self._lemma = str(row["lemma"]) if "lemma" in row else ""
        self._storage = storage

    def __repr__(self) -> str:
        return f"Lexeme(id={self._rowid})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Lexeme):
            return NotImplemented
        return self._rowid == other._rowid

    def __hash__(self) -> int:
        return hash(self._rowid)

    def index(self) -> str:
        """Returns the unique identifier for this lexeme."""
        return str(self._rowid)

    def lang(self) -> str:
        """Returns the language code of this lexeme."""
        if not self._lang:
            row = _language_cached(self._storage, self._rowid)
            if row is None:
                raise KeyError(f"No language for entry rowid {self._rowid}")
            self._lang = str(row["lang"])
        return self._lang

    def lemma(self) -> str:
        """Returns the canonical form of this lexeme."""
        if not self._lemma:
            row = _lemma_cached(self._storage, self._rowid)
            if row is None: 
                raise KeyError(f"No lemma for entry rowid {self._rowid}")
            self._lemma = str(row["lemma"])
        return self._lemma

    def all_forms(self) -> list[str]:
        """Returns all inflected forms of this lexeme (including the lemma)."""
        rows = _select(self._storage, _SQL_FORMS_BY_ENTRY, (self._rowid,))
        return [str(row["form"]) for row in rows]

    def senses(self) -> list[Sense]:
        """Returns every sense that references this lexeme."""
        rows = _select(self._storage, _SQL_SENSES_BY_LEXEME, (self._rowid,))
        return [Sense(row, self._storage) for row in rows]

    def concepts(self) -> list[Concept]:
        """Returns every concept linked to this lexeme via a sense."""
        rows = _select(self._storage, _SQL_CONCEPTS_BY_LEXEME, (self._rowid,))
        return [Concept(row, self._storage) for row in rows]


class AnnotatedString:
    """A text string with optional sense or definition annotations."""

    __slots__ = ("_text", "_lang", "_offsets", "_storage")

    def __init__(
        self,
        text: str,
        lang: str,
        offsets: list[tuple[int, int, int]],
        storage: Storage,
    ) -> None:       
        self._text = text
        self._lang = lang
        self._offsets = offsets
        self._storage = storage

    def __repr__(self) -> str:
        return f"AnnotatedString({self._text!r})"

    def text(self) -> str:
        """Return the plain text content of this string."""
        return self._text

    def lang(self) -> str:
        """Return the language code of the language this string is in."""
        return self._lang

    def sense_offsets(self) -> list[tuple[Sense, int, int]]:
        """Return a list of (sense, start, end) tuples marking annotated spans within the text."""
        result: list[tuple[Sense, int, int]] = []
        for sense_rowid, start, end in self._offsets:
            row = _sense_cached(str(self._storage.path), self._storage, sense_rowid)
            if row is None:
                raise LookupError(f"No sense for rowid {sense_rowid}")
            result.append((Sense(row, self._storage), start, end))
        return result


# ---------------------------------------------------------------------------
# Cygnet client (top-level entry point)
# ---------------------------------------------------------------------------


class Cygnet:
    """Top-level access point for querying the Cygnet database.

    The database is resolved from the cache directory, the ``CYG_DB``
    environment variable or an explicit ``db_path``, and downloaded
    automatically on first use.

    Args:
        db_path: Explicit path to a ``cygnet.db`` file. When ``None`` the
            path comes from the ``CYG_DB`` environment variable or the
            per-user cache directory.
        download: When ``True`` (default) a missing database is downloaded
            automatically. Set to ``False`` to require an existing file.
        version: The upstream release tag to install (e.g. ``"2026.05.12"``)
            when the database has to be downloaded. When ``None`` the latest
            release is used.

    Example:
        >>> from cyg import Cygnet
        >>> cyg = Cygnet()
        >>> nouns = cyg.concepts(pos="noun")
        >>> len(nouns) > 0
        True
    """

    def __init__(
        self,
        db_path: str | None = None,
        *,
        download: bool = True,
        version: str | None = None,
    ) -> None:
        self._storage = Storage(db_path=db_path, download=download, version=version)

    def __repr__(self) -> str:
        return f"Cygnet(storage={self._storage!r})"

    @property
    def db_path(self) -> str:
        """The resolved path of the local database file."""
        return str(self._storage.path)

    def releases(self, *, timeout: int = DOWNLOAD_TIMEOUT) -> list[str]:
        """Return the tags of the Cygnet database releases available upstream.

        Args:
            timeout: Seconds to wait for the GitHub API request.

        Returns:
            The release tags, newest first.
        """
        return self._storage.releases(timeout=timeout)

    def latest_version(self, *, timeout: int = DOWNLOAD_TIMEOUT) -> str:
        """Return the tag of the newest database release available upstream.

        Args:
            timeout: Seconds to wait for the GitHub API request.

        Returns:
            The newest release tag, e.g. ``"2026.05.12"``.
        """
        return self._storage.latest_release(timeout=timeout)

    def current_version(self) -> str | None:
        """Return the release tag the local database was downloaded from.

        Returns:
            The recorded release tag, or ``None`` when the database was
            installed manually (no release sidecar exists).
        """
        return self._storage.current_release()

    def upgrade(self, version: str | None = None, *, timeout: int = DOWNLOAD_TIMEOUT) -> str:
        """Install the newest database release (or ``version``) and reopen.

        Returns:
            The installed release tag.
        """
        return self._storage.upgrade(version=version, timeout=timeout)

    def concepts(
        self,
        form: str | None = None,
        langs: list[str] | str | None = None,
        pos: POS | None = None,
        startswith: str | None = None,
        contains: str | None = None,
    ) -> list[Concept]:
        """Return all concepts, optionally filtered."""
        sql, params = _assemble_where(
            _SQL_CONCEPTS,
            form=form,
            langs=langs,
            pos=pos,
            startswith=startswith,
            contains=contains,
        )
        rows = _select(self._storage, sql + " ORDER BY synsets.rowid ASC", params)
        return [Concept(row, self._storage) for row in rows]

    def concept(self, ili: str) -> Concept | None:
        """Return the concept identified by an ILI string."""
        row = _select_one(self._storage, _SQL_CONCEPT_BY_ILI, (ili,))
        if row is None:
            return None
        return Concept(row, self._storage)

    def senses(
        self,
        form: str | None = None,
        langs: list[str] | str | None = None,
        startswith: str | None = None,
        contains: str | None = None,
    ) -> list[Sense]:
        """Return all senses, optionally filtered."""
        sql, params = _assemble_where(
            _SQL_SENSES,
            form=form,
            langs=langs,
            pos=None,
            startswith=startswith,
            contains=contains,
        )
        rows = _select(self._storage, sql + " ORDER BY senses.sense_index, senses.rowid", params)
        return [Sense(row, self._storage) for row in rows]

    def lexemes(
        self,
        form: str | None = None,
        langs: list[str] | str | None = None,
        startswith: str | None = None,
        contains: str | None = None,
    ) -> list[Lexeme]:
        """Return all lexemes, optionally filtered."""
        sql, params = _assemble_where(
            _SQL_LEXEMES,
            form=form,
            langs=langs,
            pos=None,
            startswith=startswith,
            contains=contains,
        )
        rows = _select(self._storage, sql + " ORDER BY entries.rowid ASC", params)
        return [Lexeme(row, self._storage) for row in rows]

    def langs(self) -> list[str]:
        """Return the sorted list of language codes in the database."""
        return [row[0] for row in _select(self._storage, _SQL_LANGUAGES)]

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._storage.close()

    def __enter__(self) -> Cygnet:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
