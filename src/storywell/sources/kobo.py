"""Kobo source: reads finished books from an on-device ``KoboReader.sqlite`` file.

A Kobo e-reader keeps its library in a SQLite database (``KoboReader.sqlite`` under the
hidden ``.kobo`` directory on the mounted device). There is no API; the user points
Storywell at the file with ``--file``. Books live in the ``content`` table with
``ContentType = 6``; this source maps each book row to a ``SourceBook`` and applies the
same finished filter every source uses. The pure helpers (``clean_isbn``,
``parse_finished_at``, ``row_to_book``, ``filter_finished``) are unit-tested.

Schema validated against a real device DB (a real ``KoboReader.sqlite``, 67-column
``content`` table): every column read here is present and the query runs without error.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from ..models import SourceBook
from .base import SourceError

SOURCE_NAME = "kobo"

BOOK_CONTENT_TYPE = 6
READ_STATUS_READ = 2

BOOK_QUERY = (
    "SELECT ContentID, Title, Attribution, ISBN, ReadStatus, ___PercentRead, DateLastRead "
    "FROM content WHERE ContentType = ?"
)


def clean_isbn(value: str | None) -> tuple[str | None, str | None]:
    """Return ``(isbn, isbn13)`` for a raw ISBN cell, routing 13-digit values to ``isbn13``."""
    if value is None:
        return None, None
    text = value.strip()
    if not text:
        return None, None
    if len(text) == 13:
        return None, text
    return text, None


def parse_finished_at(value: str | None) -> datetime | None:
    """Parse a Kobo ``DateLastRead`` timestamp, tolerating a trailing Z, fractions, or garbage."""
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


def row_to_book(row: sqlite3.Row | tuple) -> SourceBook:
    content_id, title, attribution, isbn, read_status, percent_read, date_last_read = row
    isbn10, isbn13 = clean_isbn(isbn)
    return SourceBook(
        source=SOURCE_NAME,
        source_id=str(content_id),
        title=title or "",
        authors=(attribution,) if attribution else (),
        percent_complete=float(percent_read or 0),
        finished_at=parse_finished_at(date_last_read),
        is_finished=read_status == READ_STATUS_READ,
        isbn=isbn10,
        isbn13=isbn13,
    )


def filter_finished(
    rows: Iterable[sqlite3.Row | tuple], threshold: float = 0.95
) -> list[SourceBook]:
    cutoff = threshold * 100.0
    finished: list[SourceBook] = []
    for row in rows:
        book = row_to_book(row)
        if book.is_finished or book.percent_complete >= cutoff:
            finished.append(book)
    return finished


def read_book_rows(path: Path) -> list[tuple]:
    """Read book rows from the Kobo SQLite database, opened read-only."""
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as err:
        raise SourceError(f"Could not open Kobo database {path}: {err}") from err
    try:
        return list(connection.execute(BOOK_QUERY, (BOOK_CONTENT_TYPE,)))
    except sqlite3.Error as err:
        raise SourceError(f"Could not read Kobo database {path}: {err}") from err
    finally:
        connection.close()


class KoboSource:
    """Reports finished books from a Kobo e-reader's ``KoboReader.sqlite`` file."""

    name = SOURCE_NAME

    def __init__(self, *, path: Path | str | None = None):
        if path is None:
            raise SourceError(
                "The kobo source needs --file PATH to the KoboReader.sqlite database."
            )
        self.path = Path(path)
        if not self.path.exists():
            raise SourceError(f"Kobo database not found: {self.path}")

    def finished_books(self, *, threshold: float = 0.95) -> list[SourceBook]:
        return filter_finished(read_book_rows(self.path), threshold=threshold)
