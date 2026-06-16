"""Shared machinery for sources backed by a user-exported CSV file.

Shelf services (Goodreads, LibraryThing, ...) have no automation API; the user exports a
CSV and points Storywell at it with ``--file``. ``CsvSource`` reads the file, maps each row
to a ``SourceBook`` via the subclass's ``row_to_book``, and applies the same finished filter
every source uses. The pure helpers (``read_rows``, ``unwrap_isbn``) are unit-tested.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from ..models import SourceBook
from .base import SourceError

# Goodreads (and some others) write ISBNs as an Excel-safe formula: ="9780...". Strip the
# wrapper down to the bare identifier; an empty wrapper (="") means "no ISBN".
_ISBN_FORMULA_PREFIX = '="'
_ISBN_FORMULA_SUFFIX = '"'


def unwrap_isbn(value: str | None) -> str | None:
    """Unwrap an Excel-formula ISBN cell (``="9780..."``) to the bare value, or None if empty."""
    if value is None:
        return None
    text = value.strip()
    if text.startswith(_ISBN_FORMULA_PREFIX) and text.endswith(_ISBN_FORMULA_SUFFIX):
        text = text[len(_ISBN_FORMULA_PREFIX) : -len(_ISBN_FORMULA_SUFFIX)]
    text = text.strip()
    return text or None


def _detect_delimiter(header: str) -> str:
    if "\t" in header:
        return "\t"
    if header.count(";") > header.count(","):
        return ";"
    return ","


def _decode(data: bytes) -> str:
    """Decode export bytes: UTF-16 (via BOM, e.g. Excel "Unicode Text"), else UTF-8, else
    Latin-1 as a last resort. Latin-1 never fails, so a genuinely odd encoding degrades to
    mojibake rather than an error — UTF-16 is handled explicitly so it does not."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read a delimited export into a list of column->value dicts.

    Detects the delimiter from the header line (tab / semicolon / comma) so comma CSVs
    (Goodreads) and tab-delimited exports (LibraryThing) both parse; handles UTF-16/UTF-8(BOM)/
    Latin-1. Delimiter detection inspects the header bytes, so it assumes the header row has no
    delimiter character embedded inside a quoted column name.
    """
    try:
        data = path.read_bytes()
    except OSError as err:
        raise SourceError(f"Could not read export file {path}: {err}") from err
    text = _decode(data)
    delimiter = _detect_delimiter(text.split("\n", 1)[0])
    try:
        return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    except csv.Error as err:
        raise SourceError(f"Could not parse export file {path}: {err}") from err


class CsvSource:
    """Base class for CSV-export sources.

    Subclasses set ``name`` and implement ``row_to_book(row) -> SourceBook | None`` (returning
    None to drop a row, e.g. a to-read shelf entry). The shared ``finished_books`` reads the
    file and keeps rows the subclass flagged finished or that clear the percent threshold.
    """

    name: str = ""

    def __init__(self, *, path: Path | str | None = None):
        if path is None:
            raise SourceError(
                f"The {self.name or 'csv'} source needs an export file. Pass --file PATH."
            )
        self.path = Path(path)
        if not self.path.exists():
            raise SourceError(f"Export file not found: {self.path}")

    def row_to_book(self, row: dict[str, str]) -> SourceBook | None:
        raise NotImplementedError

    def _rows(self) -> list[dict[str, str]]:
        """The export's rows. Override to read a non-CSV layout (e.g. LibraryThing's JSON)."""
        return read_rows(self.path)

    def finished_books(self, *, threshold: float = 0.95) -> list[SourceBook]:
        cutoff = threshold * 100.0
        finished: list[SourceBook] = []
        for row in self._rows():
            book = self.row_to_book(row)
            if book is None:
                continue
            if book.is_finished or book.percent_complete >= cutoff:
                finished.append(book)
        return finished
