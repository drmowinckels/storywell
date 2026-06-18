"""Kindle source: reads Amazon's "Request My Data" export and reports finished books.

Amazon has no Kindle reading API; the user requests their data at
https://www.amazon.com/hz/privacy-central/data-requests/preview.html (Kindle) and receives a
folder/zip of CSVs (NOT JSON). Two files matter:

* ``Kindle.KindleDocs.DocumentMetadata.csv`` — the book catalogue: one row per document with an
  ``ASIN``, ``title`` and ``authors``. The ASIN may live in a dedicated ``ASIN`` column or be
  embedded in an ``identifiers`` cell (``amazon:B0...``); both layouts are handled.
* ``Kindle.Devices.ReadingSession.csv`` — per-session telemetry: one row per reading session
  with an ``ASIN``, ``total_reading_millis``, ``number_of_page_flips`` and a ``start_timestamp``
  (and usually ``end_timestamp``). An export can contain MORE than one session file (Amazon
  sometimes splits or repeats them, and they may be nested under per-export subfolders); every
  matching session file is read and the sessions are aggregated per ASIN.

There is **no reliable finished flag** in this export. (The well-known "``percentageRead`` is
broken" reports concern the separate ``read.amazon.com`` web-reader API, not this export.)
"Finished" is therefore **inferred heuristically** from the aggregated sessions: a book counts
as finished when its cumulative reading time crosses ``min_minutes`` OR its cumulative page
flips cross ``min_page_flips``. The defaults (2 hours / 200 flips) are deliberately generous
enough to exclude a sample-only open but conservative enough to catch a genuinely-read book;
they are exposed as constructor defaults so callers can tune them. This signal is best-effort.

Books key on **ASIN**, not ISBN (the export carries no ISBN), so ``isbn``/``isbn13`` stay empty
and StoryGraph matching falls back to title+author. ``finished_at`` is the latest session
timestamp for the book (``end_timestamp`` preferred, else ``start_timestamp``), or None when no
session carries a parseable timestamp.

Reference: ``arpanghosh8453/kindle-stats`` parses the same session CSV (but does not compute
completion — that heuristic is net-new here).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..models import SourceBook
from .base import SourceError
from .csv_source import read_rows

SOURCE_NAME = "kindle"
SOURCE_FORMAT = "ebook"

# Canonical Amazon export filenames. Real exports nest them under per-request subfolders and
# sometimes split sessions across several files, so the directory is searched recursively and
# matched on the trailing filename (case-insensitive) rather than an exact path.
METADATA_FILENAME = "DocumentMetadata.csv"
SESSION_FILENAME = "ReadingSession.csv"

# Finished heuristic defaults. A book is treated as finished when its cumulative reading time
# clears MIN_MINUTES *or* its cumulative page flips clear MIN_PAGE_FLIPS. Generous enough to
# drop a sample/preview open, conservative enough to keep a real read; best-effort either way.
DEFAULT_MIN_MINUTES = 120.0
DEFAULT_MIN_PAGE_FLIPS = 200

_MILLIS_PER_MINUTE = 60_000.0


@dataclass
class _Sessions:
    """Aggregated reading sessions for one ASIN."""

    total_millis: float = 0.0
    total_page_flips: int = 0
    last_timestamp: datetime | None = None


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _get_ci(row: dict[str, str], *names: str) -> str:
    """First non-empty value among the named columns, matched case-insensitively.

    Amazon's header casing has drifted between exports (``ASIN`` vs ``asin``,
    ``title`` vs ``Title``), so every column read here is resolved tolerantly.
    """
    lowered = {key.lower(): value for key, value in row.items() if key}
    for name in names:
        value = _clean(lowered.get(name.lower()))
        if value:
            return value
    return ""


def extract_asin(row: dict[str, str]) -> str:
    """Resolve a row's ASIN from a dedicated column or an embedded ``identifiers`` cell.

    The metadata export uses either an ``ASIN`` column or an ``identifiers`` cell holding
    comma-separated ``source:value`` pairs (``amazon:B0...``); the session export always has an
    ``ASIN`` column. Returns "" when no ASIN can be resolved.
    """
    asin = _get_ci(row, "ASIN", "asin")
    if asin:
        return asin
    identifiers = _get_ci(row, "identifiers")
    for part in identifiers.split(","):
        token = part.strip()
        if token.lower().startswith("amazon:"):
            return token[len("amazon:") :].strip()
    return ""


def parse_authors(value: str | None) -> tuple[str, ...]:
    """Split an ``authors`` cell. Amazon separates multiple authors with ``;`` (and sometimes
    ``,``); the more specific ``;`` wins so "Last, First" single authors stay intact."""
    text = _clean(value)
    if not text:
        return ()
    separator = ";" if ";" in text else ","
    return tuple(name.strip() for name in text.split(separator) if name.strip())


def parse_int(value: str | None) -> int:
    text = _clean(value)
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def parse_float(value: str | None) -> float:
    text = _clean(value)
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse an export timestamp, tolerating a trailing Z, fractional seconds or garbage."""
    text = _clean(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _session_timestamp(row: dict[str, str]) -> datetime | None:
    return parse_timestamp(_get_ci(row, "end_timestamp") or _get_ci(row, "start_timestamp"))


def _newer(a: datetime | None, b: datetime | None) -> datetime | None:
    """The later of two timestamps, ignoring None, comparing tz-aware and naive safely."""
    if a is None:
        return b
    if b is None:
        return a
    try:
        return max(a, b)
    except TypeError:
        # One side is tz-aware and the other naive; prefer the aware value rather than crash.
        return a if a.tzinfo is not None else b


def aggregate_sessions(rows: list[dict[str, str]]) -> dict[str, _Sessions]:
    """Aggregate per-session rows into per-ASIN totals.

    Sums reading time and page flips and tracks the latest session timestamp for each ASIN.
    Rows with no resolvable ASIN are dropped (there is no stable id to sync them on).
    """
    by_asin: dict[str, _Sessions] = defaultdict(_Sessions)
    for row in rows:
        asin = extract_asin(row)
        if not asin:
            continue
        agg = by_asin[asin]
        agg.total_millis += parse_float(_get_ci(row, "total_reading_millis"))
        agg.total_page_flips += parse_int(_get_ci(row, "number_of_page_flips"))
        agg.last_timestamp = _newer(agg.last_timestamp, _session_timestamp(row))
    return dict(by_asin)


def read_metadata(rows: list[dict[str, str]]) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Map ASIN -> (title, authors) from the document-metadata rows.

    The first row seen for an ASIN wins; later duplicates (Amazon can repeat a document across
    devices) are ignored so the title/author is stable.
    """
    catalogue: dict[str, tuple[str, tuple[str, ...]]] = {}
    for row in rows:
        asin = extract_asin(row)
        if not asin or asin in catalogue:
            continue
        title = _get_ci(row, "title")
        catalogue[asin] = (title, parse_authors(_get_ci(row, "authors")))
    return catalogue


def is_finished(agg: _Sessions, *, min_minutes: float, min_page_flips: int) -> bool:
    """Heuristic finished signal: cumulative minutes OR cumulative page flips clear a threshold."""
    minutes = agg.total_millis / _MILLIS_PER_MINUTE
    return minutes >= min_minutes or agg.total_page_flips >= min_page_flips


def build_books(
    catalogue: dict[str, tuple[str, tuple[str, ...]]],
    sessions: dict[str, _Sessions],
    *,
    min_minutes: float,
    min_page_flips: int,
) -> list[SourceBook]:
    """Join sessions to the catalogue by ASIN and return the books the heuristic calls finished.

    Only ASINs with sessions are considered (a catalogued book never opened cannot be "read").
    A book with sessions but no catalogue entry (the metadata file was absent or omitted it) still
    syncs under its ASIN as title, so it is never silently dropped.
    """
    books: list[SourceBook] = []
    for asin, agg in sessions.items():
        if not is_finished(agg, min_minutes=min_minutes, min_page_flips=min_page_flips):
            continue
        title, authors = catalogue.get(asin, ("", ()))
        books.append(
            SourceBook(
                source=SOURCE_NAME,
                source_id=asin,
                title=title or asin,
                authors=authors,
                finished_at=agg.last_timestamp,
                is_finished=True,
                media_format=SOURCE_FORMAT,
            )
        )
    return books


def _find_files(path: Path, filename: str) -> list[Path]:
    """Locate every export file whose name ends in ``filename`` under ``path``.

    Accepts ``path`` itself when it is a single matching CSV, else searches the directory tree
    recursively (Amazon nests the CSVs under per-request subfolders). Matching is on the
    trailing filename, case-insensitive, so ``Kindle.Devices.ReadingSession.csv`` and a bare
    ``ReadingSession.csv`` both resolve.
    """
    suffix = filename.lower()
    if path.is_file():
        return [path] if path.name.lower().endswith(suffix) else []
    return sorted(p for p in path.rglob("*.csv") if p.name.lower().endswith(suffix))


def load_export(
    path: Path,
) -> tuple[dict[str, tuple[str, tuple[str, ...]]], dict[str, _Sessions]]:
    """Read the catalogue and aggregated sessions from a Kindle export folder or CSV.

    ``path`` is either the export directory (the two CSVs are located by name, tolerating
    Amazon's nested layout and multiple session files) or a single ReadingSession CSV. At least
    one session file is required; the metadata file is optional (a book's title then degrades to
    its ASIN).
    """
    session_files = _find_files(path, SESSION_FILENAME)
    if not session_files:
        raise SourceError(
            f"No {SESSION_FILENAME} found under {path}. Point --file at your Kindle "
            '"Request My Data" export folder (or the ReadingSession CSV itself).'
        )

    metadata_rows: list[dict[str, str]] = []
    for meta_file in _find_files(path, METADATA_FILENAME):
        metadata_rows.extend(read_rows(meta_file))
    catalogue = read_metadata(metadata_rows)

    session_rows: list[dict[str, str]] = []
    for session_file in session_files:
        session_rows.extend(read_rows(session_file))
    sessions = aggregate_sessions(session_rows)
    return catalogue, sessions


class KindleSource:
    """Reports finished books inferred from an Amazon Kindle "Request My Data" export.

    ``path`` is the export folder (or a single ReadingSession CSV). ``min_minutes`` and
    ``min_page_flips`` tune the best-effort finished heuristic; a book counts as finished when
    its cumulative reading time or page flips clear either threshold.
    """

    name = SOURCE_NAME
    media_format = SOURCE_FORMAT

    def __init__(
        self,
        *,
        path: Path | str | None = None,
        min_minutes: float = DEFAULT_MIN_MINUTES,
        min_page_flips: int = DEFAULT_MIN_PAGE_FLIPS,
    ):
        if path is None:
            raise SourceError(
                "The kindle source needs --file PATH to your Amazon "
                '"Request My Data" export folder (or the ReadingSession CSV).'
            )
        self.path = Path(path)
        if not self.path.exists():
            raise SourceError(f"Kindle export not found: {self.path}")
        self.min_minutes = min_minutes
        self.min_page_flips = min_page_flips

    def finished_books(self, *, threshold: float = 0.95) -> list[SourceBook]:
        catalogue, sessions = load_export(self.path)
        return build_books(
            catalogue,
            sessions,
            min_minutes=self.min_minutes,
            min_page_flips=self.min_page_flips,
        )
