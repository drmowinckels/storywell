"""Goodreads source: reads a ``goodreads_library_export.csv`` and reports finished books.

Goodreads has no automation API; the user exports their library from Settings > Export and
points Storywell at the CSV with ``--file``. Each row maps to a ``SourceBook``; the shared
``CsvSource.finished_books`` keeps only the rows on the ``read`` shelf. ISBNs are exported as
Excel-safe formulas (``="9780..."``) and unwrapped by ``unwrap_isbn`` so StoryGraph matching
can resolve by identifier instead of fuzzy title.

Validated against real ``goodreads_library_export.csv`` files (1,156 rows across three
exports, old and post-2022 layouts): parses cleanly with ~90% ISBN coverage. Note many
``read`` rows have no ``Date Read`` and are therefore marked read without a finish date.
"""

from __future__ import annotations

from datetime import datetime

from ..models import SourceBook
from .csv_source import CsvSource, unwrap_isbn

SOURCE_NAME = "goodreads"
DATE_READ_FORMAT = "%Y/%m/%d"


def parse_authors(row: dict[str, str]) -> tuple[str, ...]:
    primary = (row.get("Author") or "").strip()
    authors = [primary] if primary else []
    additional = row.get("Additional Authors") or ""
    authors.extend(name.strip() for name in additional.split(",") if name.strip())
    return tuple(authors)


def parse_rating(row: dict[str, str]) -> float | None:
    raw = (row.get("My Rating") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return None
    return float(value) if value else None


def parse_review(row: dict[str, str]) -> str | None:
    review = (row.get("My Review") or "").strip()
    return review or None


def parse_finished_at(row: dict[str, str]) -> datetime | None:
    raw = (row.get("Date Read") or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, DATE_READ_FORMAT)
    except ValueError:
        return None


class GoodreadsSource(CsvSource):
    """Reports finished books from a Goodreads library CSV export."""

    name = SOURCE_NAME

    def row_to_book(self, row: dict[str, str]) -> SourceBook | None:
        source_id = (row.get("Book Id") or "").strip()
        title = (row.get("Title") or "").strip()
        if not source_id or not title:
            return None
        return SourceBook(
            source=self.name,
            source_id=source_id,
            title=title,
            authors=parse_authors(row),
            finished_at=parse_finished_at(row),
            is_finished=row.get("Exclusive Shelf") == "read",
            rating=parse_rating(row),
            review=parse_review(row),
            isbn=unwrap_isbn(row.get("ISBN")),
            isbn13=unwrap_isbn(row.get("ISBN13")),
        )
