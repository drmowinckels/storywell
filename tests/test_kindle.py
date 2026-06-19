import csv
from datetime import UTC, datetime

import pytest

from storywell.sources import KindleSource, available_sources, make_source
from storywell.sources.base import SourceError
from storywell.sources.kindle import (
    DEFAULT_MIN_MINUTES,
    DEFAULT_MIN_PAGE_FLIPS,
    _Sessions,
    aggregate_sessions,
    build_books,
    extract_asin,
    is_finished,
    load_export,
    parse_authors,
    parse_timestamp,
    read_metadata,
)

_METADATA_HEADER = ["ASIN", "title", "authors", "identifiers"]
_SESSION_HEADER = [
    "ASIN",
    "total_reading_millis",
    "number_of_page_flips",
    "start_timestamp",
    "end_timestamp",
    "device_family",
    "content_type",
]

_MIN_MILLIS = int(DEFAULT_MIN_MINUTES * 60_000)


def _write_csv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _metadata_row(asin="B001", title="Dune", authors="Frank Herbert", identifiers=""):
    return {"ASIN": asin, "title": title, "authors": authors, "identifiers": identifiers}


def _session_row(
    asin="B001",
    millis=_MIN_MILLIS,
    page_flips=0,
    start="2024-01-15T08:30:00Z",
    end="2024-01-15T09:30:00Z",
    device_family="Kindle E-reader",
    content_type="E-Book",
):
    return {
        "ASIN": asin,
        "total_reading_millis": str(millis),
        "number_of_page_flips": str(page_flips),
        "start_timestamp": start,
        "end_timestamp": end,
        "device_family": device_family,
        "content_type": content_type,
    }


def _export_dir(tmp_path, metadata_rows, session_rows, *, nested=False):
    root = tmp_path / "export"
    base = root / "Kindle" / "datasets" if nested else root
    base.mkdir(parents=True)
    _write_csv(base / "Kindle.KindleDocs.DocumentMetadata.csv", _METADATA_HEADER, metadata_rows)
    _write_csv(base / "Kindle.Devices.ReadingSession.csv", _SESSION_HEADER, session_rows)
    return root


def test_extract_asin_prefers_dedicated_column():
    assert extract_asin({"ASIN": "B001", "identifiers": "amazon:B999"}) == "B001"


def test_extract_asin_falls_back_to_identifiers_cell():
    assert extract_asin({"identifiers": "isbn:123, amazon:B07HJYTRMD, other:x"}) == "B07HJYTRMD"


def test_extract_asin_is_case_insensitive_on_header():
    assert extract_asin({"asin": "B042"}) == "B042"


def test_extract_asin_returns_empty_when_absent():
    assert extract_asin({"title": "No id here"}) == ""


def test_parse_authors_splits_on_semicolon_then_comma():
    assert parse_authors("Frank Herbert; Brian Herbert") == ("Frank Herbert", "Brian Herbert")
    assert parse_authors("Herbert, Frank") == ("Herbert", "Frank")
    assert parse_authors("") == ()
    assert parse_authors(None) == ()


def test_parse_timestamp_handles_z_suffix_and_garbage():
    assert parse_timestamp("2024-01-15T08:30:00Z") == datetime(2024, 1, 15, 8, 30, tzinfo=UTC)
    assert parse_timestamp("2024-01-15T08:30:00") == datetime(2024, 1, 15, 8, 30)
    assert parse_timestamp("not-a-date") is None
    assert parse_timestamp("") is None
    assert parse_timestamp(None) is None


def test_read_metadata_keeps_first_row_per_asin():
    rows = [
        _metadata_row(asin="B001", title="Dune"),
        _metadata_row(asin="B001", title="Dune (duplicate device copy)"),
        _metadata_row(asin="B002", title="Hyperion", authors="Dan Simmons"),
    ]
    catalogue = read_metadata(rows)
    assert catalogue["B001"] == ("Dune", ("Frank Herbert",))
    assert catalogue["B002"] == ("Hyperion", ("Dan Simmons",))


def test_aggregate_sessions_sums_time_and_flips_and_tracks_latest_timestamp():
    rows = [
        _session_row(asin="B001", millis=1000, page_flips=10, end="2024-01-10T10:00:00Z"),
        _session_row(asin="B001", millis=2000, page_flips=20, end="2024-01-12T10:00:00Z"),
        _session_row(asin="B002", millis=500, page_flips=5, end="2024-02-01T10:00:00Z"),
    ]
    agg = aggregate_sessions(rows)
    assert agg["B001"].total_millis == 3000
    assert agg["B001"].total_page_flips == 30
    assert agg["B001"].last_timestamp == datetime(2024, 1, 12, 10, 0, tzinfo=UTC)
    assert agg["B002"].total_millis == 500


def test_aggregate_sessions_drops_rows_without_asin():
    rows = [_session_row(asin="", millis=999999), _session_row(asin="B001", millis=1000)]
    agg = aggregate_sessions(rows)
    assert set(agg) == {"B001"}


def test_aggregate_sessions_falls_back_to_start_timestamp_when_no_end():
    rows = [_session_row(asin="B001", start="2024-03-01T09:00:00Z", end="")]
    agg = aggregate_sessions(rows)
    assert agg["B001"].last_timestamp == datetime(2024, 3, 1, 9, 0, tzinfo=UTC)


def test_aggregate_sessions_tolerates_mixed_aware_and_naive_timestamps():
    rows = [
        _session_row(asin="B001", end="2024-01-10T10:00:00Z"),
        _session_row(asin="B001", end="2024-01-12T10:00:00"),
    ]
    agg = aggregate_sessions(rows)
    assert agg["B001"].last_timestamp is not None


def test_is_finished_crosses_on_minutes_or_page_flips():
    long_read = _Sessions(total_millis=DEFAULT_MIN_MINUTES * 60_000, total_page_flips=0)
    many_flips = _Sessions(total_millis=0, total_page_flips=DEFAULT_MIN_PAGE_FLIPS)
    barely = _Sessions(total_millis=60_000, total_page_flips=5)
    thresholds = {"min_minutes": DEFAULT_MIN_MINUTES, "min_page_flips": DEFAULT_MIN_PAGE_FLIPS}
    assert is_finished(long_read, **thresholds)
    assert is_finished(many_flips, **thresholds)
    assert not is_finished(barely, **thresholds)


def test_build_books_joins_by_asin_and_keeps_only_finished():
    catalogue = {
        "B001": ("Dune", ("Frank Herbert",)),
        "B002": ("Sampled Book", ("Someone",)),
    }
    sessions = {
        "B001": _Sessions(
            total_millis=_MIN_MILLIS,
            total_page_flips=300,
            last_timestamp=datetime(2024, 1, 12, 10, 0, tzinfo=UTC),
        ),
        "B002": _Sessions(total_millis=10_000, total_page_flips=3),
    }
    books = build_books(
        catalogue, sessions, min_minutes=DEFAULT_MIN_MINUTES, min_page_flips=DEFAULT_MIN_PAGE_FLIPS
    )
    assert len(books) == 1
    book = books[0]
    assert book.source == "kindle"
    assert book.source_id == "B001"
    assert book.title == "Dune"
    assert book.authors == ("Frank Herbert",)
    assert book.is_finished is True
    assert book.media_format == "ebook"
    assert book.isbn is None and book.isbn13 is None
    assert book.finished_at == datetime(2024, 1, 12, 10, 0, tzinfo=UTC)


def test_build_books_degrades_title_to_asin_without_catalogue_entry():
    sessions = {"B002": _Sessions(total_millis=_MIN_MILLIS)}
    books = build_books(
        {}, sessions, min_minutes=DEFAULT_MIN_MINUTES, min_page_flips=DEFAULT_MIN_PAGE_FLIPS
    )
    assert books[0].source_id == "B002"
    assert books[0].title == "B002"


def test_load_export_reads_directory_with_canonical_filenames(tmp_path):
    root = _export_dir(
        tmp_path,
        [_metadata_row(asin="B001", title="Dune")],
        [_session_row(asin="B001")],
    )
    catalogue, sessions = load_export(root)
    assert catalogue["B001"][0] == "Dune"
    assert "B001" in sessions


def test_load_export_finds_nested_files(tmp_path):
    root = _export_dir(
        tmp_path,
        [_metadata_row(asin="B001")],
        [_session_row(asin="B001")],
        nested=True,
    )
    catalogue, sessions = load_export(root)
    assert "B001" in catalogue and "B001" in sessions


def test_load_export_aggregates_multiple_session_files(tmp_path):
    root = tmp_path / "export"
    root.mkdir()
    _write_csv(
        root / "Kindle.KindleDocs.DocumentMetadata.csv",
        _METADATA_HEADER,
        [_metadata_row(asin="B001", title="Dune")],
    )
    _write_csv(
        root / "Kindle.Devices.ReadingSession.csv",
        _SESSION_HEADER,
        [_session_row(asin="B001", millis=1000, page_flips=100)],
    )
    _write_csv(
        root / "Kindle.Devices.ReadingSession2.csv",
        _SESSION_HEADER,
        [_session_row(asin="B001", millis=2000, page_flips=150)],
    )
    _, sessions = load_export(root)
    assert sessions["B001"].total_millis == 3000
    assert sessions["B001"].total_page_flips == 250


def test_load_export_accepts_a_single_session_csv(tmp_path):
    session_file = _write_csv(
        tmp_path / "Kindle.Devices.ReadingSession.csv",
        _SESSION_HEADER,
        [_session_row(asin="B001")],
    )
    catalogue, sessions = load_export(session_file)
    assert catalogue == {}
    assert "B001" in sessions


def test_load_export_raises_when_no_session_file(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(SourceError, match="No ReadingSession.csv found"):
        load_export(tmp_path / "empty")


def test_finished_books_end_to_end_separates_above_and_below_heuristic(tmp_path):
    root = _export_dir(
        tmp_path,
        [
            _metadata_row(asin="B001", title="Dune", authors="Frank Herbert"),
            _metadata_row(asin="B002", title="Just Sampled", authors="Nobody"),
        ],
        [
            _session_row(asin="B001", millis=1000, page_flips=120, end="2024-01-10T10:00:00Z"),
            _session_row(asin="B001", millis=2000, page_flips=120, end="2024-01-12T10:00:00Z"),
            _session_row(asin="B002", millis=5000, page_flips=4),
        ],
    )
    books = KindleSource(path=root).finished_books()
    assert [b.source_id for b in books] == ["B001"]
    book = books[0]
    assert book.title == "Dune"
    assert book.authors == ("Frank Herbert",)
    assert book.is_finished is True
    assert book.finished_at == datetime(2024, 1, 12, 10, 0, tzinfo=UTC)


def test_finished_books_honours_custom_thresholds(tmp_path):
    root = _export_dir(
        tmp_path,
        [_metadata_row(asin="B001", title="Quick Read")],
        [_session_row(asin="B001", millis=600_000, page_flips=10)],
    )
    assert KindleSource(path=root).finished_books() == []
    lenient = KindleSource(path=root, min_minutes=5.0, min_page_flips=1000).finished_books()
    assert [b.source_id for b in lenient] == ["B001"]


def test_finished_books_extracts_asin_from_identifiers_metadata(tmp_path):
    root = tmp_path / "export"
    root.mkdir()
    _write_csv(
        root / "Kindle.KindleDocs.DocumentMetadata.csv",
        ["title", "authors", "identifiers"],
        [{"title": "Dune", "authors": "Frank Herbert", "identifiers": "amazon:B001"}],
    )
    _write_csv(
        root / "Kindle.Devices.ReadingSession.csv",
        _SESSION_HEADER,
        [_session_row(asin="B001")],
    )
    books = KindleSource(path=root).finished_books()
    assert books[0].title == "Dune"


def test_kindle_source_requires_a_path():
    with pytest.raises(SourceError, match="needs --file PATH"):
        KindleSource()


def test_kindle_source_raises_when_path_missing(tmp_path):
    with pytest.raises(SourceError, match="Kindle export not found"):
        KindleSource(path=tmp_path / "absent")


def test_kindle_registered_and_declares_ebook_format():
    assert "kindle" in available_sources()
    assert KindleSource.media_format == "ebook"


def test_make_source_builds_kindle_with_threshold_options(tmp_path):
    root = _export_dir(tmp_path, [_metadata_row(asin="B001")], [_session_row(asin="B001")])
    src = make_source("kindle", path=root, min_minutes=1.0, min_page_flips=1)
    assert isinstance(src, KindleSource)
    assert src.min_minutes == 1.0
    assert src.min_page_flips == 1
