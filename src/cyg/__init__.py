"""cyg: query the Cygnet multilingual wordnet database.

Cyg is a small, dependency-free Python library that queries `Cygnet`_ -- a
merged multilingual wordnet covering 40+ languages and ~180 000 concepts.

It automatically downloads the latest version of the database on first use
and exposes a small object model for exploring concepts, lexemes and senses.

Basic usage::

    from cyg import Cygnet

    cyg = Cygnet()
    concepts = cyg.concepts(form="cheek", langs="en")
    for concept in concepts:
        print(concept.index(), concept.pos())
        for sense in concept.senses("en"):
            print(sense.index())

.. _Cygnet: https://github.com/omwn/cygnet
"""

from __future__ import annotations

from .core import (
    POS,
    AnnotatedString,
    Concept,
    Cygnet,
    Lexeme,
    Sense,
)
from .storage import DatabaseError, DatabaseNotFoundError, DownloadError

__version__ = "0.0.1b1.post5"

__all__ = [
    "AnnotatedString",
    "Concept",
    "Cygnet",
    "DatabaseError",
    "DatabaseNotFoundError",
    "DownloadError",
    "Lexeme",
    "POS",
    "Sense",
    "__version__",
]

