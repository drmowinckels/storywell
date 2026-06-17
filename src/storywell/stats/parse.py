"""Pure parsers for the fiddly cells in a StoryGraph library export.

The export packs several values into single string cells, so each function here turns one
raw cell into a typed value and never raises on messy input — a malformed cell degrades to
``None`` / empty rather than failing the whole load. All functions are unit-tested in
``tests/test_stats_parse.py``.

Cell formats (verified against a real export header, 2026-06-17):
- ``Read Status``: ``read`` / ``currently-reading`` / ``to-read`` / ``did-not-finish``.
- ``Dates Read``: dates are ``YYYY/MM/DD`` (slashes); a range is ``start-end`` (a hyphen
  between two dates); multiple reads are joined by ``;`` — e.g. ``2024/01/05-2024/01/14;
  2024/06/01-2024/06/05``. A bare ``YYYY/MM/DD`` is a finish with no recorded start.
- ``Moods`` / ``Tags`` / ``Authors``: comma-separated lists.
- ``Star Rating``: a number like ``4.5`` (or empty).
- the character/plot booleans: ``Yes`` / ``No`` / empty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class ReadStatus(StrEnum):
    READ = "read"
    CURRENTLY_READING = "currently-reading"
    TO_READ = "to-read"
    DID_NOT_FINISH = "did-not-finish"
    UNKNOWN = "unknown"


_STATUS_ALIASES = {
    "read": ReadStatus.READ,
    "currently-reading": ReadStatus.CURRENTLY_READING,
    "currently reading": ReadStatus.CURRENTLY_READING,
    "to-read": ReadStatus.TO_READ,
    "to read": ReadStatus.TO_READ,
    "did-not-finish": ReadStatus.DID_NOT_FINISH,
    "did not finish": ReadStatus.DID_NOT_FINISH,
    "dnf": ReadStatus.DID_NOT_FINISH,
}


@dataclass(frozen=True)
class ReadInstance:
    """One recorded read of a book. ``start`` is ``None`` when only a finish date was logged.

    ``days`` is the inclusive-to-exclusive span (``end - start``) when both ends are known,
    else ``None`` — so an undated or single-date read contributes no duration.
    """

    start: date | None
    end: date | None

    @property
    def days(self) -> int | None:
        if self.start is None or self.end is None:
            return None
        delta = (self.end - self.start).days
        # A reversed range (end before start) is bad data; report no duration rather than
        # letting a negative span pollute pace stats.
        return delta if delta >= 0 else None

    @property
    def finished_year(self) -> int | None:
        anchor = self.end or self.start
        return anchor.year if anchor else None


def parse_status(value: str | None) -> ReadStatus:
    if not value:
        return ReadStatus.UNKNOWN
    return _STATUS_ALIASES.get(value.strip().lower(), ReadStatus.UNKNOWN)


def parse_date(value: str | None) -> date | None:
    """Parse a single ``YYYY/MM/DD`` (or ``YYYY-MM-DD``) date; ``None`` on empty/garbage."""
    if not value:
        return None
    text = value.strip().replace("-", "/")
    parts = text.split("/")
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(p) for p in parts)
        return date(year, month, day)
    except (ValueError, TypeError):
        return None


_DATE_TOKEN = re.compile(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}")


def parse_dates_read(value: str | None) -> list[ReadInstance]:
    """Parse the ``Dates Read`` cell into read instances.

    Reads are separated by ``;``; within each read the date(s) may use either ``/`` or ``-``
    as the in-date separator. We extract whole ``YYYY-M-D`` / ``YYYY/M/D`` tokens by regex
    rather than splitting on ``-`` — that keeps an all-dashes range like ``2024-01-05-2024-01-14``
    unambiguous instead of silently shredding it. One date → ``ReadInstance(start=None, end)``
    (a finish with no recorded start); two or more → ``ReadInstance(first, last)``. Chunks with
    no usable date are dropped.
    """
    if not value:
        return []
    instances: list[ReadInstance] = []
    for chunk in value.split(";"):
        dates = [d for d in (parse_date(tok) for tok in _DATE_TOKEN.findall(chunk)) if d]
        if not dates:
            continue
        if len(dates) == 1:
            instances.append(ReadInstance(start=None, end=dates[0]))
        else:
            instances.append(ReadInstance(start=dates[0], end=dates[-1]))
    return instances


def parse_list(value: str | None) -> tuple[str, ...]:
    """Split a comma-separated cell (Moods, Tags, Authors, Contributors) into stripped items."""
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def parse_rating(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.strip())
    except (ValueError, TypeError):
        return None


def parse_bool(value: str | None) -> bool | None:
    """``Yes`` → True, ``No`` → False, anything else (incl. empty) → ``None`` (not rated)."""
    if not value:
        return None
    text = value.strip().lower()
    if text == "yes":
        return True
    if text == "no":
        return False
    return None


def parse_int(value: str | None, default: int = 0) -> int:
    if not value:
        return default
    try:
        return int(value.strip())
    except (ValueError, TypeError):
        return default


_NARRATOR_MARKER = "(narrator)"


def narrators(contributors: tuple[str, ...]) -> tuple[str, ...]:
    """Names tagged ``(Narrator)`` in the ``Contributors`` cell, with the tag stripped."""
    found: list[str] = []
    for raw in contributors:
        lowered = raw.lower()
        if _NARRATOR_MARKER in lowered:
            name = raw[: lowered.index(_NARRATOR_MARKER)].strip()
            if name:
                found.append(name)
    return tuple(found)
