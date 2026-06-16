"""LibraryThing source: reads a LibraryThing export and reports catalogued books.

LibraryThing is a *catalogue*, not a read-status tracker: a typical export lists every book
in "Your library" (and any custom collections) with no read flag and no Date Read. So this
source reports the catalogue and lets the caller decide what counts as read:

* default: only books LibraryThing actually flags read count as finished — a "Read"-style
  collection or a real ``Date Read``. For a pure catalogue that is usually none.
* ``mark_read=True`` (CLI ``--as-read``): treat every reported book as read.
* ``read_date=True`` (CLI ``--read-date``): stamp ``today`` as a placeholder finish date on
  read books that have no real ``Date Read``.
* ``collections=(...)`` (CLI ``--collection``, repeatable): only import books that belong to
  one of the named collections (case-insensitive). Omitted = the whole catalogue. This is how
  the user scopes a sync to e.g. a custom "Read" or "Favorites" collection.

Format (verified against a real export, 2026-06): TAB-delimited, UTF-8 with BOM. Title-Case
headers include ``Book Id``, ``Title``, ``Primary Author`` ("Last, First"), ``Secondary
Author``, ``Rating`` (0-5, half steps), ``Review``, ``Date Read``, ``Collections`` (a
comma-separated list of the book's collections), ``ISBN`` (bracketed, e.g. ``[1982141182]``)
and ``ISBNs`` (comma list, e.g. ``1982141182, 9781982141189``). Column lookups are
case-insensitive with fallbacks so older / variant export headers still resolve.

A ``.json`` export is also accepted (detected by extension) and is preferred when available:
it is a dict keyed by book id with structured fields — ``authors[].fl`` already "First Last",
``collections`` a list, ``isbn`` keyed ``{0: isbn10, 2: isbn13}``, a ``rating``, and an
``originaltitle`` without the series suffix (which matches StoryGraph better). It is adapted to
the same flat rows ``row_to_book`` consumes, so the read/collection options work identically.
EXPERIMENTAL until exercised on more real exports.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from ..models import SourceBook
from .base import SourceError
from .csv_source import CsvSource, read_rows

SOURCE_NAME = "librarything"

_HEADERS = {
    "book_id": ("book id", "id"),
    "title": ("title",),
    "author": ("primary author", "author (first, last)", "author"),
    "secondary": ("secondary author", "other authors"),
    "rating": ("rating", "stars", "my rating"),
    "review": ("review", "my review"),
    "date_read": ("date read", "date ended", "date finished"),
    "collections": ("collections",),
    "isbns": ("isbns",),
    "isbn": ("isbn",),
}

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%Y/%m/%d", "%d %b %Y")
_READ_COLLECTION = re.compile(r"^\s*read\b", re.IGNORECASE)


def _pick(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value and value.strip():
            return value.strip()
    return None


def split_collections(raw: str | None) -> list[str]:
    """Split LibraryThing's ``Collections`` cell into individual collection names."""
    return [name.strip() for name in re.split(r"[;,]", raw or "") if name.strip()]


def flip_name(name: str | None) -> str | None:
    """Turn LibraryThing's "Last, First" author into "First Last"; leave others untouched."""
    if not name:
        return name
    parts = [p.strip() for p in name.split(",")]
    if len(parts) == 2 and all(parts):
        return f"{parts[1]} {parts[0]}"
    return name


def parse_isbns(isbns: str | None, isbn: str | None) -> tuple[str | None, str | None]:
    """Parse LibraryThing's ISBN fields (comma list and/or bracketed) into (isbn10, isbn13)."""
    isbn10 = isbn13 = None
    for raw in (isbns, isbn):
        if not raw:
            continue
        for token in raw.replace("[", "").replace("]", "").split(","):
            digits = re.sub(r"[\s-]", "", token)
            if len(digits) == 13 and digits.isdigit():
                isbn13 = isbn13 or digits
            elif len(digits) == 10:
                isbn10 = isbn10 or digits
    return isbn10, isbn13


def parse_rating(raw: str | None) -> float | None:
    if not raw or not raw.strip():
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return value or None


def parse_date(raw: str | None) -> datetime | None:
    if not raw or not raw.strip():
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def _slug(text: str | None) -> str | None:
    return "-".join(text.lower().split()) if text else None


def json_rows(path: Path) -> list[dict[str, str]]:
    """Adapt a LibraryThing JSON export (dict keyed by book id) to the flat rows
    ``row_to_book`` consumes, so one mapping serves both the TSV and JSON exports."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError) as err:
        raise SourceError(f"Could not read export file {path}: {err}") from err
    entries = data.values() if isinstance(data, dict) else data
    rows: list[dict[str, str]] = []
    for entry in entries:
        book = entry if isinstance(entry, dict) else {}
        names = [
            a.get("fl") or a.get("lf")
            for a in book.get("authors") or []
            if isinstance(a, dict) and (a.get("fl") or a.get("lf"))
        ]
        isbn = book.get("isbn") if isinstance(book.get("isbn"), dict) else {}
        isbns = ", ".join(v for v in (isbn.get("0"), isbn.get("2")) if v)
        collections = [c for c in book.get("collections") or [] if isinstance(c, str)]
        rows.append(
            {
                "book id": str(book.get("books_id") or ""),
                "title": book.get("originaltitle") or book.get("title") or "",
                "primary author": names[0] if names else "",
                "secondary author": names[1] if len(names) > 1 else "",
                "rating": str(book.get("rating") or ""),
                "review": book.get("review") or book.get("comment") or "",
                "date read": book.get("dateread") or "",
                "collections": ", ".join(collections),
                "isbns": isbns,
            }
        )
    return rows


class LibraryThingSource(CsvSource):
    """Reports catalogued books from a LibraryThing export (EXPERIMENTAL).

    ``mark_read`` treats every reported book as read; ``read_date`` stamps ``today`` on
    read-but-undated books; ``collections`` restricts the import to named collections.
    ``today`` is injectable for deterministic tests.
    """

    name = SOURCE_NAME

    def __init__(
        self,
        *,
        path=None,
        mark_read: bool = False,
        read_date: bool = False,
        collections: tuple[str, ...] = (),
        today: date | None = None,
    ):
        super().__init__(path=path)
        self.mark_read = mark_read
        self.read_date = read_date
        self.collections = tuple(c.strip().lower() for c in collections if c and c.strip())
        self._today = today or date.today()

    def row_to_book(self, row: dict[str, str]) -> SourceBook | None:
        row = {k.lower().strip(): v for k, v in row.items() if k}
        book_collections = split_collections(_pick(row, _HEADERS["collections"]))
        if self.collections and not any(c.lower() in self.collections for c in book_collections):
            return None

        title = _pick(row, _HEADERS["title"])
        isbn, isbn13 = parse_isbns(_pick(row, _HEADERS["isbns"]), _pick(row, _HEADERS["isbn"]))
        source_id = _pick(row, _HEADERS["book_id"]) or isbn13 or isbn or _slug(title)
        if not source_id:
            return None

        authors = tuple(
            author
            for author in (
                flip_name(_pick(row, _HEADERS["author"])),
                flip_name(_pick(row, _HEADERS["secondary"])),
            )
            if author
        )
        explicit_date = parse_date(_pick(row, _HEADERS["date_read"]))
        explicit_read = explicit_date is not None or any(
            _READ_COLLECTION.match(name) for name in book_collections
        )
        is_finished = explicit_read or self.mark_read
        finished_at = explicit_date
        if is_finished and finished_at is None and self.read_date:
            finished_at = datetime.combine(self._today, datetime.min.time())

        return SourceBook(
            source=self.name,
            source_id=source_id,
            title=title or "",
            authors=authors,
            finished_at=finished_at,
            is_finished=is_finished,
            rating=parse_rating(_pick(row, _HEADERS["rating"])),
            review=_pick(row, _HEADERS["review"]),
            isbn=isbn,
            isbn13=isbn13,
        )

    def _rows(self) -> list[dict[str, str]]:
        """Read the export as rows, from JSON (``.json``) or the delimited TSV/CSV otherwise.

        ``CsvSource.finished_books`` calls this, so JSON and TSV share one finished filter."""
        if self.path.suffix.lower() == ".json":
            return json_rows(self.path)
        return read_rows(self.path)
