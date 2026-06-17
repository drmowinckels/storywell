"""Pure stat computations over ``LibraryEntry`` records.

Every function takes the loaded entries and returns plain JSON-friendly data (scalars, or
lists of ``[label, count]`` pairs) — no rendering, no I/O, no clock. That keeps each number
unit-testable in isolation and lets the HTML dashboard (a later slice) consume the same
``compute_all`` blob the ``--json`` flag emits. "Current year" is derived as the latest year
present in the data, not ``today()``, so results are deterministic.

Counting model: a *book* is an entry with ``Read Status == read``; a *finish* is one
``ReadInstance`` of a read book (so a re-read counts twice, in each year it happened). Read
books with no recorded dates are surfaced as ``undated_reads`` rather than silently dropped.
"""

from __future__ import annotations

from collections import Counter
from statistics import median

from .export import LibraryEntry
from .parse import ReadStatus

_PACE_ORDER = ("fast", "medium", "slow")


def _read(entries: list[LibraryEntry]) -> list[LibraryEntry]:
    return [e for e in entries if e.is_read]


def _counter_pairs(counter: Counter, *, limit: int | None = None) -> list[list]:
    """Counter → ``[[label, count], ...]`` sorted by count desc then label asc."""
    pairs = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    if limit is not None:
        pairs = pairs[:limit]
    return [[label, count] for label, count in pairs]


def status_counts(entries: list[LibraryEntry]) -> dict[str, int]:
    counter: Counter = Counter(e.read_status.value for e in entries)
    return {status.value: counter.get(status.value, 0) for status in ReadStatus}


def finishes_by_year(entries: list[LibraryEntry]) -> list[list]:
    counter: Counter = Counter()
    for entry in _read(entries):
        for instance in entry.read_instances:
            year = instance.finished_year
            if year is not None:
                counter[year] += 1
    return [[year, counter[year]] for year in sorted(counter)]


def reading_calendar(entries: list[LibraryEntry]) -> list[list]:
    """Finishes per ``YYYY-MM`` (for a heatmap), sorted chronologically."""
    counter: Counter = Counter()
    for entry in _read(entries):
        for instance in entry.read_instances:
            anchor = instance.end or instance.start
            if anchor is not None:
                counter[f"{anchor.year:04d}-{anchor.month:02d}"] += 1
    return [[month, counter[month]] for month in sorted(counter)]


def reading_pace(entries: list[LibraryEntry]) -> dict:
    """Days-to-finish stats from instances that have both a start and an end date."""
    durations: list[tuple[str, int]] = []
    for entry in _read(entries):
        for instance in entry.read_instances:
            if instance.days is not None:
                durations.append((entry.title, instance.days))
    if not durations:
        return {
            "count": 0,
            "mean_days": None,
            "median_days": None,
            "longest": None,
            "shortest": None,
        }
    days = [d for _, d in durations]
    longest = max(durations, key=lambda td: td[1])
    shortest = min(durations, key=lambda td: td[1])
    return {
        "count": len(durations),
        "mean_days": round(sum(days) / len(days), 1),
        "median_days": median([float(d) for d in days]),
        "longest": {"title": longest[0], "days": longest[1]},
        "shortest": {"title": shortest[0], "days": shortest[1]},
    }


def _rated(entries: list[LibraryEntry]) -> list[LibraryEntry]:
    return [e for e in _read(entries) if e.star_rating is not None]


def rating_distribution(entries: list[LibraryEntry]) -> list[list]:
    counter: Counter = Counter(e.star_rating for e in _rated(entries))
    return [[rating, counter[rating]] for rating in sorted(counter)]


def mean_rating(entries: list[LibraryEntry]) -> float | None:
    rated = _rated(entries)
    if not rated:
        return None
    return round(sum(e.star_rating for e in rated) / len(rated), 2)


def rating_extremes(entries: list[LibraryEntry]) -> dict:
    rated = _rated(entries)
    if not rated:
        return {"highest": None, "lowest": None}
    highest = max(rated, key=lambda e: e.star_rating)
    lowest = min(rated, key=lambda e: e.star_rating)
    return {
        "highest": {"title": highest.title, "rating": highest.star_rating},
        "lowest": {"title": lowest.title, "rating": lowest.star_rating},
    }


def format_split(entries: list[LibraryEntry]) -> list[list]:
    counter: Counter = Counter((e.media_format or "unknown") for e in _read(entries))
    return _counter_pairs(counter)


def _count_attribute(
    entries: list[LibraryEntry], attr: str, *, limit: int | None = None
) -> list[list]:
    """Count items of an iterable ``LibraryEntry`` attribute across read books."""
    counter: Counter = Counter()
    for entry in _read(entries):
        for item in getattr(entry, attr):
            counter[item] += 1
    return _counter_pairs(counter, limit=limit)


def top_authors(entries: list[LibraryEntry], limit: int = 10) -> list[list]:
    return _count_attribute(entries, "authors", limit=limit)


def top_narrators(entries: list[LibraryEntry], limit: int = 10) -> list[list]:
    return _count_attribute(entries, "narrators", limit=limit)


def mood_frequency(entries: list[LibraryEntry], limit: int | None = None) -> list[list]:
    return _count_attribute(entries, "moods", limit=limit)


def pace_split(entries: list[LibraryEntry]) -> list[list]:
    counter: Counter = Counter(e.pace for e in _read(entries) if e.pace)
    known = [[pace, counter[pace]] for pace in _PACE_ORDER if counter.get(pace)]
    extra = _counter_pairs(Counter({k: v for k, v in counter.items() if k not in _PACE_ORDER}))
    return known + extra


def _yes_rate(entries: list[LibraryEntry], attr: str) -> dict:
    rated = [getattr(e, attr) for e in _read(entries) if getattr(e, attr) is not None]
    if not rated:
        return {"rated": 0, "yes": 0, "yes_rate": None}
    yes = sum(1 for v in rated if v)
    return {"rated": len(rated), "yes": yes, "yes_rate": round(yes / len(rated), 2)}


def taste_fingerprint(entries: list[LibraryEntry]) -> dict:
    driven: Counter = Counter(e.character_or_plot for e in _read(entries) if e.character_or_plot)
    return {
        "character_or_plot": _counter_pairs(driven),
        "strong_character_development": _yes_rate(entries, "strong_character_development"),
        "loveable_characters": _yes_rate(entries, "loveable_characters"),
        "diverse_characters": _yes_rate(entries, "diverse_characters"),
        "flawed_characters": _yes_rate(entries, "flawed_characters"),
    }


def summary(entries: list[LibraryEntry]) -> dict:
    read = _read(entries)
    years = finishes_by_year(entries)
    total_finishes = sum(count for _, count in years)
    latest_year = years[-1][0] if years else None
    # Distinct books finished in the latest year — not finishes — so a same-year re-read
    # doesn't inflate the headline count.
    latest_year_books = (
        sum(1 for e in read if any(i.finished_year == latest_year for i in e.read_instances))
        if latest_year is not None
        else None
    )
    return {
        "total_entries": len(entries),
        "status_counts": status_counts(entries),
        "read_books": len(read),
        "total_finishes": total_finishes,
        "undated_reads": sum(1 for e in read if not e.read_instances),
        "rated_books": len(_rated(entries)),
        "mean_rating": mean_rating(entries),
        "latest_year": latest_year,
        "latest_year_books": latest_year_books,
    }


def compute_all(entries: list[LibraryEntry]) -> dict:
    """Every stat the dashboard needs, as one JSON-friendly blob (the ``--json`` payload)."""
    return {
        "summary": summary(entries),
        "volume_pace": {
            "finishes_by_year": finishes_by_year(entries),
            "reading_calendar": reading_calendar(entries),
            "reading_pace": reading_pace(entries),
        },
        "ratings": {
            "distribution": rating_distribution(entries),
            "mean": mean_rating(entries),
            "extremes": rating_extremes(entries),
        },
        "formats_authors": {
            "format_split": format_split(entries),
            "top_authors": top_authors(entries),
            "top_narrators": top_narrators(entries),
        },
        "moods_taste": {
            "mood_frequency": mood_frequency(entries),
            "pace_split": pace_split(entries),
            "fingerprint": taste_fingerprint(entries),
        },
    }
