"""Read a StoryGraph library CSV export into typed ``LibraryEntry`` records.

StoryGraph lets every user export their full library (Account → Manage Account → Export
StoryGraph Library) regardless of tier — the gating is on the *visualisations*, not the
data. This module turns that export into records the stats layer can crunch. It reuses
``sources.csv_source.read_rows`` for delimiter detection and UTF-16/UTF-8-BOM/Latin-1
decoding rather than re-solving encoding here. Columns are looked up by header name (not
position) so a re-ordered or trimmed export still loads. The pure helpers are unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..sources.base import SourceError
from ..sources.csv_source import read_rows
from . import parse
from .parse import ReadInstance, ReadStatus

TITLE = "Title"
AUTHORS = "Authors"
CONTRIBUTORS = "Contributors"
ISBN_UID = "ISBN/UID"
FORMAT = "Format"
READ_STATUS = "Read Status"
DATE_ADDED = "Date Added"
LAST_DATE_READ = "Last Date Read"
DATES_READ = "Dates Read"
READ_COUNT = "Read Count"
MOODS = "Moods"
PACE = "Pace"
CHARACTER_OR_PLOT = "Character- or Plot-Driven?"
STRONG_CHARACTER_DEVELOPMENT = "Strong Character Development?"
LOVEABLE_CHARACTERS = "Loveable Characters?"
DIVERSE_CHARACTERS = "Diverse Characters?"
FLAWED_CHARACTERS = "Flawed Characters?"
STAR_RATING = "Star Rating"
REVIEW = "Review"
CONTENT_WARNINGS = "Content Warnings"
CONTENT_WARNING_DESCRIPTION = "Content Warning Description"
TAGS = "Tags"
OWNED = "Owned?"

_REQUIRED_COLUMNS = (TITLE, READ_STATUS)


@dataclass(frozen=True)
class LibraryEntry:
    """One row of a StoryGraph export, parsed. Stats-shaped, not sync-shaped.

    Distinct from ``models.SourceBook`` on purpose: that one is keyed and lossy (no moods,
    pace, content warnings), built for the write path. This one keeps everything the export
    offers so the dashboard can describe a reading life.
    """

    title: str
    authors: tuple[str, ...]
    contributors: tuple[str, ...]
    isbn_uid: str
    media_format: str
    read_status: ReadStatus
    date_added: date | None
    last_date_read: date | None
    read_instances: tuple[ReadInstance, ...]
    read_count: int
    moods: tuple[str, ...]
    pace: str
    character_or_plot: str
    strong_character_development: bool | None
    loveable_characters: bool | None
    diverse_characters: bool | None
    flawed_characters: bool | None
    star_rating: float | None
    review: str
    content_warnings: str
    content_warning_description: str
    tags: tuple[str, ...]
    owned: bool | None

    @property
    def is_read(self) -> bool:
        return self.read_status is ReadStatus.READ

    @property
    def narrators(self) -> tuple[str, ...]:
        return parse.narrators(self.contributors)


def row_to_entry(row: dict[str, str]) -> LibraryEntry:
    """Map one export row (column→value) to a ``LibraryEntry``. Missing columns are tolerated."""

    def cell(name: str) -> str:
        return (row.get(name) or "").strip()

    return LibraryEntry(
        title=cell(TITLE),
        authors=parse.parse_list(cell(AUTHORS)),
        contributors=parse.parse_list(cell(CONTRIBUTORS)),
        isbn_uid=cell(ISBN_UID),
        media_format=cell(FORMAT),
        read_status=parse.parse_status(cell(READ_STATUS)),
        date_added=parse.parse_date(cell(DATE_ADDED)),
        last_date_read=parse.parse_date(cell(LAST_DATE_READ)),
        read_instances=tuple(parse.parse_dates_read(cell(DATES_READ))),
        read_count=parse.parse_int(cell(READ_COUNT)),
        moods=parse.parse_list(cell(MOODS)),
        pace=cell(PACE).lower(),
        character_or_plot=cell(CHARACTER_OR_PLOT),
        strong_character_development=parse.parse_bool(cell(STRONG_CHARACTER_DEVELOPMENT)),
        loveable_characters=parse.parse_bool(cell(LOVEABLE_CHARACTERS)),
        diverse_characters=parse.parse_bool(cell(DIVERSE_CHARACTERS)),
        flawed_characters=parse.parse_bool(cell(FLAWED_CHARACTERS)),
        star_rating=parse.parse_rating(cell(STAR_RATING)),
        review=cell(REVIEW),
        content_warnings=cell(CONTENT_WARNINGS),
        content_warning_description=cell(CONTENT_WARNING_DESCRIPTION),
        tags=parse.parse_list(cell(TAGS)),
        owned=parse.parse_bool(cell(OWNED)),
    )


def load_export(path: Path | str) -> list[LibraryEntry]:
    """Read a StoryGraph export file into ``LibraryEntry`` records.

    Raises ``SourceError`` if the file is unreadable or does not look like a StoryGraph
    export (no ``Title``/``Read Status`` columns) — a clear signal the user pointed at the
    wrong file rather than a confusing empty dashboard.
    """
    path = Path(path)
    if not path.exists():
        raise SourceError(f"Export file not found: {path}")
    rows = read_rows(path)
    if not rows:
        raise SourceError(f"Export file is empty: {path}")
    header = rows[0].keys()
    missing = [name for name in _REQUIRED_COLUMNS if name not in header]
    if missing:
        raise SourceError(
            "This does not look like a StoryGraph library export "
            f"(missing column(s): {', '.join(missing)}). Export it from "
            "Account → Manage Account → Export StoryGraph Library."
        )
    return [row_to_entry(row) for row in rows]
