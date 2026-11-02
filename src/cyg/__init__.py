"""cyg: query the Cygnet multilingual wordnet database.

Cyg is a small, dependency-free Python library that queries `Cygnet`_ -- a
merged multilingual wordnet covering 40+ languages and ~180 000 concepts.

It automatically downloads the latest version of the database on first use
and exposes a small object model for exploring concepts, lexemes and senses.

Basic usage::

    from cyg import Cygnet

    db = Cygnet()
    concepts = db.concepts(form="cheek", langs="en")
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

import requests



__version__ = "0.0.1b1.post6"

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



def releases() -> list[str]:
    """Return the tags of the releases available upstream, newest first.       

    Returns:
        The release tags, e.g. ``["2026.05.12", "2026.03.01", ...]``.

    Raises:
        DownloadError: If the releases cannot be listed.
    """
    url = "https://api.github.com/repos/omwn/cygnet/tags"
    tags = []
    page = 1

    while True:
        response = requests.get(
            url,
            params={"per_page": 100, "page": page},
            timeout=300,
        )
        response.raise_for_status()

        data = response.json()
        if not data:
            break

        tags.extend(tag["name"] for tag in data)
        page += 1

    return tags
