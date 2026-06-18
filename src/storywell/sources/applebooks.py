"""Apple Books source: reads finished books from the on-device Apple Books library.

Apple Books (macOS) keeps its library in a SQLite database under the app's sandbox
container, ``~/Library/Containers/com.apple.iBooksX/Data/Documents/BKLibrary/``. The
filename carries a numeric suffix (``BKLibrary-1-091020131601.sqlite``) that varies per
machine, so the path is GLOBbed rather than hardcoded. There is no API and no ISBN in
this database (only Apple-internal ids), so downstream matching is title/author based,
like Audible. The user can point Storywell at the file with ``--file``; otherwise it is
auto-detected.

Books live in the ``ZBKLIBRARYASSET`` table. The pure helpers
(``coredata_to_datetime``, ``row_to_book``, ``filter_finished``) are unit-tested; the
column names are confirmed against ``vgnshiyer/py-apple-books`` and a live database. This
source is macOS-only: when the database is absent (a non-mac host, or Books has never been
opened) a clear :class:`SourceError` is raised rather than crashing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from ..models import SourceBook
from .base import SourceError

SOURCE_NAME = "applebooks"
SOURCE_FORMAT = "ebook"

LIBRARY_DIR = (
    Path.home()
    / "Library"
    / "Containers"
    / "com.apple.iBooksX"
    / "Data"
    / "Documents"
    / "BKLibrary"
)
LIBRARY_GLOB = "BKLibrary*.sqlite"

# Core Data stores dates as seconds since 2001-01-01 UTC; add this to reach the Unix epoch.
COREDATA_EPOCH_OFFSET = 978307200

BOOK_QUERY = (
    "SELECT ZASSETID, ZTITLE, ZAUTHOR, ZISFINISHED, ZREADINGPROGRESS, ZDATEFINISHED "
    "FROM ZBKLIBRARYASSET"
)


def coredata_to_datetime(value: int | float | None) -> datetime | None:
    """Convert a Core Data timestamp (seconds since 2001-01-01 UTC) to a ``datetime``."""
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value + COREDATA_EPOCH_OFFSET)
    except (OverflowError, OSError, ValueError):
        return None


def row_to_book(row: sqlite3.Row | tuple) -> SourceBook:
    asset_id, title, author, is_finished, reading_progress, date_finished = row
    return SourceBook(
        source=SOURCE_NAME,
        source_id=str(asset_id),
        title=title or "",
        authors=(author,) if author else (),
        percent_complete=float(reading_progress or 0.0) * 100.0,
        finished_at=coredata_to_datetime(date_finished),
        is_finished=bool(is_finished),
        media_format=SOURCE_FORMAT,
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


def locate_database(explicit: Path | None = None) -> Path:
    """Return the Apple Books library path, honouring an explicit override.

    Raises :class:`SourceError` when an explicit path is missing or, on auto-detect,
    when no database is found (a non-mac host, or Books has never been opened).
    """
    if explicit is not None:
        if not explicit.exists():
            raise SourceError(f"Apple Books database not found: {explicit}")
        return explicit

    matches = sorted(LIBRARY_DIR.glob(LIBRARY_GLOB)) if LIBRARY_DIR.is_dir() else []
    if not matches:
        raise SourceError(
            f"No Apple Books database under {LIBRARY_DIR}. "
            "Apple Books is macOS-only; open the Books app at least once, or pass "
            "--file PATH to the BKLibrary*.sqlite database."
        )
    return matches[0]


def read_book_rows(path: Path) -> list[tuple]:
    """Read book rows from the Apple Books SQLite database, opened read-only."""
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as err:
        raise SourceError(f"Could not open Apple Books database {path}: {err}") from err
    try:
        return list(connection.execute(BOOK_QUERY))
    except sqlite3.Error as err:
        raise SourceError(f"Could not read Apple Books database {path}: {err}") from err
    finally:
        connection.close()


class AppleBooksSource:
    """Reports finished books from the on-device Apple Books library (macOS)."""

    name = SOURCE_NAME
    media_format = SOURCE_FORMAT

    def __init__(self, *, path: Path | str | None = None):
        self.path = locate_database(Path(path) if path is not None else None)

    def finished_books(self, *, threshold: float = 0.95) -> list[SourceBook]:
        return filter_finished(read_book_rows(self.path), threshold=threshold)
