"""Libby / OverDrive source: reads a library borrow history and routes it to a shelf.

Libby and OverDrive record *borrow activity*, not reading status: there is no finished flag,
no finish date, and (often) no ISBN. So, like LibraryThing's catalogue, this source has no
finished signal of its own — every borrowed book is routed to a StoryGraph shelf the caller
chooses, or nothing is synced at all:

* default (no ``shelf``): nothing is reported. A borrow history is not reading status, so a
  plain sync must not silently flood a shelf with every book the user ever checked out
  (storywell's opt-in philosophy). The user opts in with ``--shelf``.
* ``shelf=Shelf.TO_READ`` (CLI ``--shelf to-read``): route every borrowed book to ``to-read``
  — the usual case (issue #27's intended target), since a borrow is a "want to read" signal.
* ``shelf=Shelf.READ`` (CLI ``--shelf read``): treat every borrowed book as finished/read. This
  marks books ``read`` and is the opt-in for users who finish what they borrow.
* ``shelf=Shelf.CURRENTLY_READING`` / ``DID_NOT_FINISH``: route the borrow history there instead.

Two real export formats are accepted; columns are matched case-insensitively with fallbacks:

* **Libby Timeline "Spreadsheet"** (the *raw* export, ``libbytimeline-activities.csv``):
  per-event rows, one row per activity, comma-delimited with a header row. Columns:
  ``Cover, Title, Author, Publisher, ISBN, Timestamp, Activity, Details, Library``. ``Activity``
  is the event ("Borrowed", "Returned", "Placed on hold", ...). A single title yields several
  rows (borrow + return + holds), so rows are de-duplicated to one book per title+author.
  (This is the *raw* export, NOT the Goodreads-formatted CSV Libby can also produce — that one
  has Goodreads' own columns and should be synced with the goodreads source.)
* **OverDrive website "History" export**: already one row per book (OverDrive de-dupes). Columns:
  ``Title, Sub Title, Author, Series, Publisher, Publish Date, Star Rating, Star Rating Count,
  Maturity Level, ISBN, Cover Art URL, Borrow Date, Type``.

EXPERIMENTAL until exercised on more real exports (the raw Libby schema is undocumented).
"""

from __future__ import annotations

import re

from ..models import Shelf, SourceBook
from .csv_source import CsvSource, unwrap_isbn

SOURCE_NAME = "libby"

_HEADERS = {
    "title": ("title",),
    "author": ("author", "authors"),
    "isbn": ("isbn", "isbns"),
    "rating": ("star rating", "rating"),
}


def _pick(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value and value.strip():
            return value.strip()
    return None


def parse_isbn(raw: str | None) -> tuple[str | None, str | None]:
    """Parse a Libby/OverDrive ISBN cell into ``(isbn10, isbn13)``; both None when absent."""
    text = unwrap_isbn(raw)
    if not text:
        return None, None
    digits = re.sub(r"[\s-]", "", text)
    if len(digits) == 13 and digits.isdigit():
        return None, digits
    if len(digits) == 10:
        return digits, None
    return None, None


def parse_rating(raw: str | None) -> float | None:
    if not raw or not raw.strip():
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return value or None


def _slug(text: str | None) -> str | None:
    return "-".join(text.lower().split()) if text else None


def dedup_key(title: str | None, author: str | None, isbn13: str | None, isbn: str | None) -> str:
    """A stable per-book key for collapsing a title's many activity rows into one book.

    Keys on the normalised title+author, which is the only field present and consistent on
    every event row for a title in the raw Libby export (a borrow row may carry an ISBN while
    its hold/return rows do not, so keying on ISBN would fragment one book into several). Falls
    back to the ISBN only when a row has no title at all. The ISBN still rides on the resulting
    ``SourceBook`` for StoryGraph matching; it just does not drive dedup.
    """
    if title or author:
        return f"ta:{_slug(title) or ''}|{_slug(author) or ''}"
    return f"isbn:{isbn13 or isbn}"


class LibbySource(CsvSource):
    """Reports borrowed books from a Libby/OverDrive export, routed to a shelf (EXPERIMENTAL).

    A borrow history has no finished signal, so ``shelf`` decides what (if anything) is synced:
    ``read`` marks every borrow finished, ``to-read`` / ``currently-reading`` / ``did-not-finish``
    route them to that shelf, and omitting ``shelf`` syncs nothing (a borrow history is not
    reading status). Activity rows are de-duplicated to one book per title.
    """

    name = SOURCE_NAME

    def __init__(
        self,
        *,
        path=None,
        shelf: Shelf | str | None = None,
    ):
        super().__init__(path=path)
        self.shelf = Shelf(shelf) if shelf is not None else None

    def row_to_book(self, row: dict[str, str]) -> SourceBook | None:
        row = {k.lower().strip(): v for k, v in row.items() if k}
        title = _pick(row, _HEADERS["title"])
        author = _pick(row, _HEADERS["author"])
        isbn, isbn13 = parse_isbn(_pick(row, _HEADERS["isbn"]))
        if not (title or author or isbn13 or isbn):  # nothing identifies this row
            return None
        source_id = dedup_key(title, author, isbn13, isbn)

        route_to_read = self.shelf is Shelf.READ
        status = self.shelf or Shelf.UNKNOWN

        return SourceBook(
            source=self.name,
            source_id=source_id,
            title=title or "",
            authors=(author,) if author else (),
            is_finished=route_to_read,
            status=status,
            rating=parse_rating(_pick(row, _HEADERS["rating"])),
            isbn=isbn,
            isbn13=isbn13,
        )

    def finished_books(self, *, threshold: float = 0.95) -> list[SourceBook]:
        """De-duplicate the borrow history to one book before the shared shelf-routing filter.

        A borrow + return + hold for the same title are many activity rows; collapsing them on
        ``source_id`` (first occurrence wins) keeps the sync from writing the same book twice.
        The base filter then decides what is reported (finished, or routed to a writable shelf).
        """
        books = super().finished_books(threshold=threshold)
        deduped: dict[str, SourceBook] = {}
        for book in books:
            deduped.setdefault(book.source_id, book)
        return list(deduped.values())
