"""Calibre source: reads finished books from a local Calibre library, read-only.

Calibre keeps its library metadata in a SQLite database, ``metadata.db``, at the top of the
library folder. There is no automation API needed; the user points Storywell at the library
with ``--file`` (either the library directory or ``metadata.db`` directly).

Calibre has **no built-in read/finished field**. Users track read status in a *custom column*
they created, so this source requires ``--read-column LABEL`` naming that column. Custom columns
are described in the ``custom_columns`` table (``id``, ``label``, ``name``, ``datatype``, ...);
the per-book value lives in a generated ``custom_<id>`` table keyed by ``book``. A book counts as
finished when that value is truthy: a checked boolean, a non-zero rating, or text like ``Yes`` /
``true`` / ``read``.

ISBNs are not a fixed column: they live in the ``identifiers`` table as the row whose
``type = 'isbn'`` for the book. Authors are linked through ``books_authors_link`` → ``authors``.

Reference: VirInvictus/getBooks (stdlib sqlite3; custom columns + ``identifiers:isbn``) and the
official ``calibre.db`` schema.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from ..models import SourceBook
from .base import SourceError

SOURCE_NAME = "calibre"

DB_FILENAME = "metadata.db"

_TRUTHY_TEXT = {"yes", "true", "read", "finished", "done", "1", "y"}


def resolve_db_path(path: Path | str) -> Path:
    """Resolve ``--file`` to the ``metadata.db`` file, accepting either it or the library dir."""
    resolved = Path(path)
    if resolved.is_dir():
        return resolved / DB_FILENAME
    return resolved


def clean_isbn(value: str | None) -> tuple[str | None, str | None]:
    """Return ``(isbn, isbn13)`` for a raw ISBN value, routing 13-digit values to ``isbn13``.

    Hyphens and spaces (e.g. ``978-0-439-02348-1``) are stripped first so the bare identifier
    length-routes correctly and matches StoryGraph by ISBN.
    """
    if value is None:
        return None, None
    text = re.sub(r"[\s-]", "", value)
    if not text:
        return None, None
    if len(text) == 13:
        return None, text
    return text, None


def parse_authors(value: str | None) -> tuple[str, ...]:
    """Split Calibre's ``group_concat`` author string (``A & B``) into a tuple of names."""
    if not value:
        return ()
    return tuple(name.strip() for name in value.split("&") if name.strip())


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse a Calibre timestamp, tolerating a trailing Z, fractions, or garbage."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1]
    if "." in text:
        text = text.split(".", 1)[0]
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def is_finished_value(value: object) -> bool:
    """Decide whether a custom-column value marks a book finished.

    Truthy booleans/ratings count, as does text like ``Yes`` / ``true`` / ``read``. ``None``
    (no value recorded for the book) and zero/empty values do not.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    text = str(value).strip().lower()
    if not text:
        return False
    return text in _TRUTHY_TEXT


def resolve_read_column(connection: sqlite3.Connection, read_column: str) -> int:
    """Return the ``custom_columns.id`` for the column whose label matches ``read_column``.

    Matched case-insensitively against the column label. Raises ``SourceError`` naming the
    available labels when no column matches, so a typo fails loudly.
    """
    rows = connection.execute("SELECT id, label FROM custom_columns").fetchall()
    for column_id, label in rows:
        if str(label).strip().lower() == read_column.strip().lower():
            return int(column_id)
    available = ", ".join(sorted(str(label) for _, label in rows)) or "(none defined)"
    raise SourceError(
        f"Calibre custom column '{read_column}' not found. Available columns: {available}."
    )


def read_book_rows(connection: sqlite3.Connection, column_id: int) -> list[tuple]:
    """Read book rows joined to the per-book value of custom column ``column_id``.

    The custom-column value table (``custom_<id>``) is joined by its ``book`` key; books with no
    recorded value get ``NULL`` (treated as not finished). The table name is composed from an
    integer id resolved from ``custom_columns``, never from user text, so it is not injectable.
    """
    value_table = f"custom_{column_id}"
    select = (
        "SELECT b.id, b.title, b.isbn, b.timestamp, "
        "(SELECT val FROM identifiers WHERE book = b.id AND type = 'isbn' LIMIT 1) AS ident_isbn, "
        "(SELECT group_concat(a.name, ' & ') FROM books_authors_link bal "
        " JOIN authors a ON a.id = bal.author WHERE bal.book = b.id) AS authors, "
        "cc.value AS read_value "
        f"FROM books b LEFT JOIN {value_table} cc ON cc.book = b.id"
    )
    try:
        return list(connection.execute(select))
    except sqlite3.Error as err:
        raise SourceError(f"Could not read Calibre database: {err}") from err


def row_to_book(row: sqlite3.Row | tuple) -> SourceBook:
    book_id, title, fixed_isbn, timestamp, ident_isbn, authors, read_value = row
    isbn10, isbn13 = clean_isbn(ident_isbn or fixed_isbn)
    finished = is_finished_value(read_value)
    return SourceBook(
        source=SOURCE_NAME,
        source_id=str(book_id),
        title=title or "",
        authors=parse_authors(authors),
        finished_at=parse_timestamp(timestamp) if finished else None,
        is_finished=finished,
        isbn=isbn10,
        isbn13=isbn13,
    )


def filter_finished(rows: Iterable[sqlite3.Row | tuple]) -> list[SourceBook]:
    """Keep only books the read column marks finished (Calibre has no percent signal)."""
    return [book for book in map(row_to_book, rows) if book.is_finished]


class CalibreSource:
    """Reports finished books from a local Calibre library's ``metadata.db`` (read-only).

    Read status comes from a user-defined custom column named via ``read_column`` (CLI
    ``--read-column``); without it the source cannot tell which books are finished and raises.
    """

    name = SOURCE_NAME
    media_format = "ebook"

    def __init__(self, *, path: Path | str | None = None, read_column: str | None = None):
        if path is None:
            raise SourceError(
                "The calibre source needs --file PATH to the Calibre library "
                "(its folder or the metadata.db inside it)."
            )
        if not read_column or not read_column.strip():
            raise SourceError(
                "The calibre source needs --read-column LABEL naming the custom column that "
                "tracks read status. Calibre has no built-in read field, so without it there "
                "is no way to tell which books are finished."
            )
        self.path = resolve_db_path(path)
        self.read_column = read_column
        if not self.path.exists():
            raise SourceError(f"Calibre database not found: {self.path}")

    def finished_books(self, *, threshold: float = 0.95) -> list[SourceBook]:
        try:
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        except sqlite3.Error as err:
            raise SourceError(f"Could not open Calibre database {self.path}: {err}") from err
        try:
            column_id = resolve_read_column(connection, self.read_column)
            return filter_finished(read_book_rows(connection, column_id))
        except sqlite3.Error as err:
            raise SourceError(f"Could not read Calibre database {self.path}: {err}") from err
        finally:
            connection.close()
