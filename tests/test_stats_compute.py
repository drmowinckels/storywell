from pathlib import Path

import pytest

from storywell.sources.base import SourceError
from storywell.stats import compute, load_export
from storywell.stats.export import LibraryEntry
from storywell.stats.parse import ReadStatus, parse_dates_read

FIXTURE = Path(__file__).parent / "fixtures" / "storygraph_export_sample.csv"


def make_entry(
    *,
    title="A Book",
    status=ReadStatus.READ,
    dates="",
    rating=None,
    media_format="ebook",
    authors=(),
    moods=(),
) -> LibraryEntry:
    """A LibraryEntry with neutral defaults, for exercising compute branches directly."""
    return LibraryEntry(
        title=title,
        authors=authors,
        contributors=(),
        isbn_uid="",
        media_format=media_format,
        read_status=status,
        date_added=None,
        last_date_read=None,
        read_instances=tuple(parse_dates_read(dates)),
        read_count=0,
        moods=moods,
        pace="",
        character_or_plot="",
        strong_character_development=None,
        loveable_characters=None,
        diverse_characters=None,
        flawed_characters=None,
        star_rating=rating,
        review="",
        content_warnings="",
        content_warning_description="",
        tags=(),
        owned=None,
    )


@pytest.fixture
def entries():
    return load_export(FIXTURE)


def test_load_export_reads_every_row_and_flags_read(entries):
    assert len(entries) == 8
    assert sum(1 for e in entries if e.is_read) == 6
    statuses = {e.read_status for e in entries}
    assert ReadStatus.DID_NOT_FINISH in statuses
    assert ReadStatus.TO_READ in statuses


def test_load_export_rejects_a_non_storygraph_csv(tmp_path):
    bad = tmp_path / "books.csv"
    bad.write_text("Name,Pages\nDune,412\n", encoding="utf-8")
    with pytest.raises(SourceError, match="StoryGraph library export"):
        load_export(bad)


def test_summary_counts_books_finishes_and_rating(entries):
    summary = compute.summary(entries)
    assert summary["read_books"] == 6
    assert summary["total_finishes"] == 7  # Project Hail Mary read twice
    assert summary["undated_reads"] == 0
    assert summary["rated_books"] == 6
    assert summary["mean_rating"] == 4.42
    assert summary["latest_year"] == 2024
    assert summary["latest_year_books"] == 6
    assert summary["status_counts"]["read"] == 6
    assert summary["status_counts"]["did-not-finish"] == 1
    assert summary["status_counts"]["to-read"] == 1


def test_finishes_by_year_splits_a_reread_across_years(entries):
    # Project Hail Mary's two reads land in 2023 and 2024; the DNF's dated read is excluded.
    assert compute.finishes_by_year(entries) == [[2023, 1], [2024, 6]]


def test_reading_pace_uses_only_dated_ranges(entries):
    pace = compute.reading_pace(entries)
    assert pace["count"] == 7
    assert pace["mean_days"] == 10.0
    assert pace["median_days"] == 9.0
    assert pace["longest"] == {"title": "Babel", "days": 22}
    assert pace["shortest"]["days"] == 4


def test_rating_distribution_and_extremes(entries):
    assert compute.rating_distribution(entries) == [[3.5, 1], [4.0, 1], [4.5, 2], [5.0, 2]]
    extremes = compute.rating_extremes(entries)
    assert extremes["highest"]["rating"] == 5.0
    assert extremes["lowest"] == {"title": "Babel", "rating": 3.5}


def test_format_split_counts_only_read_books(entries):
    assert compute.format_split(entries) == [
        ["audiobook", 4],
        ["ebook", 1],
        ["physical book", 1],
    ]


def test_top_authors_and_narrators(entries):
    authors = compute.top_authors(entries)
    assert len(authors) == 6
    assert all(count == 1 for _, count in authors)
    assert authors[0][0] == "Andy Weir"  # ties broken alphabetically
    narrators = compute.top_narrators(entries)
    assert len(narrators) == 3  # Sea of Tranquility has no narrator contributor


def test_mood_and_pace_distributions(entries):
    assert compute.mood_frequency(entries)[0] == ["reflective", 3]
    assert compute.pace_split(entries) == [["fast", 2], ["medium", 3], ["slow", 1]]


def test_taste_fingerprint(entries):
    fingerprint = compute.taste_fingerprint(entries)
    assert fingerprint["character_or_plot"] == [["Character", 4], ["Plot", 2]]
    scd = fingerprint["strong_character_development"]
    assert scd == {"rated": 6, "yes": 5, "yes_rate": 0.83}


def test_compute_all_is_json_serialisable(entries):
    import json

    blob = compute.compute_all(entries)
    assert set(blob) == {"summary", "volume_pace", "ratings", "formats_authors", "moods_taste"}
    json.dumps(blob)  # must not raise


def test_reading_calendar_buckets_finishes_by_month(entries):
    assert compute.reading_calendar(entries) == [
        ["2023-12", 1],
        ["2024-01", 1],
        ["2024-02", 1],
        ["2024-03", 1],
        ["2024-05", 1],
        ["2024-06", 2],  # Project Hail Mary's re-read + Sea of Tranquility
    ]


def test_read_book_with_no_dates_is_counted_as_undated():
    entries = [make_entry(title="No dates", status=ReadStatus.READ, dates="")]
    s = compute.summary(entries)
    assert s["read_books"] == 1
    assert s["total_finishes"] == 0
    assert s["undated_reads"] == 1
    assert s["latest_year"] is None
    assert s["latest_year_books"] is None


def test_latest_year_books_counts_distinct_books_not_rereads():
    entries = [make_entry(title="Re-read", dates="2024/01/01-2024/01/05; 2024/03/01-2024/03/04")]
    assert compute.finishes_by_year(entries) == [[2024, 2]]  # two finishes
    assert compute.summary(entries)["latest_year_books"] == 1  # but one distinct book


def test_empty_and_unrated_inputs_return_neutral_values():
    assert compute.summary([])["read_books"] == 0
    assert compute.finishes_by_year([]) == []
    assert compute.reading_pace([])["count"] == 0
    assert compute.mean_rating([]) is None
    assert compute.rating_extremes([]) == {"highest": None, "lowest": None}

    unrated = [make_entry(rating=None)]
    assert compute.mean_rating(unrated) is None
    assert compute.rating_distribution(unrated) == []
    assert compute.rating_extremes(unrated) == {"highest": None, "lowest": None}


def test_reversed_range_excluded_from_pace():
    entries = [make_entry(dates="2024/02/03-2024/01/20")]
    assert compute.reading_pace(entries)["count"] == 0  # negative span dropped
