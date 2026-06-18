"""Bookwyrm source: reads a Bookwyrm CSV export and reports shelf-routed books.

Bookwyrm (an ActivityPub reading tracker) has no per-user automation API for sync; the user
exports their library from Settings > Export and points Storywell at the CSV with ``--file``.
This is the richest CSV export of any shelf source: a single file carries the ``shelf``
status, ``date_started`` / ``date_finished``, ``rating``, ``isbn_13`` / ``isbn_10`` and the
review, so the full read state syncs from one export with no extra options.

The ``shelf`` column drives routing (column names from ``bookwyrm/models/import_job.py``):

* ``read`` -> ``is_finished=True`` with ``finished_at`` from ``date_finished`` (the router
  turns the finished signal into the ``read`` shelf).
* ``reading`` / ``currently-reading`` -> ``status=Shelf.CURRENTLY_READING``.
* ``to-read`` -> ``status=Shelf.TO_READ``.

``CsvSource.finished_books`` surfaces both finished books and books routed to a writable
non-``read`` shelf via ``status``, so the ``reading`` / ``to-read`` rows sync too. An unknown
shelf value leaves the book on ``Shelf.UNKNOWN`` and it is dropped (never written blindly).
"""

from __future__ import annotations

from datetime import datetime

from ..models import Shelf, SourceBook
from .csv_source import CsvSource

SOURCE_NAME = "bookwyrm"

# Bookwyrm shelf identifiers (bookwyrm/models/import_job.py) -> the StoryGraph shelf intent.
# ``read`` is handled separately because it carries the finished signal and a finish date.
_SHELF_TO_STATUS = {
    "reading": Shelf.CURRENTLY_READING,
    "currently-reading": Shelf.CURRENTLY_READING,
    "to-read": Shelf.TO_READ,
}


def parse_authors(row: dict[str, str]) -> tuple[str, ...]:
    """Read the comma-separated ``authors`` cell (or the ``author_text`` fallback)."""
    raw = (row.get("authors") or row.get("author_text") or "").strip()
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def parse_rating(raw: str | None) -> float | None:
    """Parse Bookwyrm's ``rating`` (0-5, half steps); 0 / blank / garbage all mean unrated."""
    if not raw or not raw.strip():
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return value or None


def parse_review(row: dict[str, str]) -> str | None:
    """Join Bookwyrm's ``review_name`` (title) and ``review_body`` into one review string."""
    name = (row.get("review_name") or "").strip()
    body = (row.get("review_body") or "").strip()
    parts = [part for part in (name, body) if part]
    return "\n\n".join(parts) or None


def parse_date(raw: str | None) -> datetime | None:
    """Parse a Bookwyrm export date. Exports stamp ISO 8601; tolerate a trailing Z, a space
    separator, fractional seconds, and a bare ``YYYY-MM-DD`` date."""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1]
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def clean_isbn(value: str | None) -> str | None:
    """Strip whitespace/hyphens from an ISBN cell; blank -> None."""
    if value is None:
        return None
    digits = value.replace("-", "").strip()
    return digits or None


class BookwyrmSource(CsvSource):
    """Reports shelf-routed books from a Bookwyrm CSV export."""

    name = SOURCE_NAME

    def row_to_book(self, row: dict[str, str]) -> SourceBook | None:
        title = (row.get("title") or "").strip()
        isbn = clean_isbn(row.get("isbn_10"))
        isbn13 = clean_isbn(row.get("isbn_13"))
        source_id = isbn13 or isbn or title
        if not title or not source_id:
            return None

        shelf = (row.get("shelf") or "").strip().lower()
        is_finished = shelf == "read"
        status = Shelf.READ if is_finished else _SHELF_TO_STATUS.get(shelf, Shelf.UNKNOWN)
        finished_at = parse_date(row.get("date_finished")) if is_finished else None

        return SourceBook(
            source=self.name,
            source_id=source_id,
            title=title,
            authors=parse_authors(row),
            finished_at=finished_at,
            is_finished=is_finished,
            status=status,
            rating=parse_rating(row.get("rating")),
            review=parse_review(row),
            isbn=isbn,
            isbn13=isbn13,
        )
