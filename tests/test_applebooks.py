import sqlite3
from datetime import datetime

import pytest

from storywell.sources.applebooks import (
    COREDATA_EPOCH_OFFSET,
    AppleBooksSource,
    coredata_to_datetime,
    filter_finished,
    read_book_rows,
    row_to_book,
)
from storywell.sources.base import SourceError

_COLUMNS = (
    "ZASSETID",
    "ZTITLE",
    "ZAUTHOR",
    "ZISFINISHED",
    "ZREADINGPROGRESS",
    "ZDATEFINISHED",
)


def _book_row(
    asset_id,
    title="A Title",
    author="An Author",
    is_finished=1,
    reading_progress=1.0,
    date_finished=None,
):
    return (asset_id, title, author, is_finished, reading_progress, date_finished)


def _make_db(tmp_path, rows):
    db_path = tmp_path / "BKLibrary-1-091020131601.sqlite"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE ZBKLIBRARYASSET ("
            "ZASSETID TEXT, ZTITLE TEXT, ZAUTHOR TEXT, ZISFINISHED INTEGER, "
            "ZREADINGPROGRESS REAL, ZDATEFINISHED REAL)"
        )
        columns = ", ".join(_COLUMNS)
        placeholders = ", ".join("?" * len(_COLUMNS))
        connection.executemany(
            f"INSERT INTO ZBKLIBRARYASSET ({columns}) VALUES ({placeholders})",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return db_path


def test_coredata_to_datetime_adds_the_2001_epoch_offset():
    # Core Data 0.0 is 2001-01-01 UTC; round-trip a known Unix instant through the offset.
    target = datetime(2024, 1, 15, 8, 30, 0)
    coredata_value = target.timestamp() - COREDATA_EPOCH_OFFSET
    assert coredata_to_datetime(coredata_value) == target


def test_coredata_to_datetime_treats_zero_as_the_2001_epoch():
    assert coredata_to_datetime(0) == datetime.fromtimestamp(COREDATA_EPOCH_OFFSET)


def test_coredata_to_datetime_tolerates_none_and_non_numeric():
    assert coredata_to_datetime(None) is None
    assert coredata_to_datetime("not-a-number") is None


def test_coredata_to_datetime_survives_out_of_range_values():
    assert coredata_to_datetime(1e308) is None


def test_row_to_book_maps_fields_and_scales_progress_to_percent():
    coredata_value = datetime(2024, 1, 15, 8, 30, 0).timestamp() - COREDATA_EPOCH_OFFSET
    book = row_to_book(("asset-1", "Dune", "Frank Herbert", 1, 0.5, coredata_value))
    assert book.source == "applebooks"
    assert book.source_id == "asset-1"
    assert book.title == "Dune"
    assert book.authors == ("Frank Herbert",)
    assert book.is_finished is True
    assert book.percent_complete == 50.0
    assert book.media_format == "ebook"
    assert book.finished_at == datetime(2024, 1, 15, 8, 30, 0)


def test_row_to_book_leaves_isbn_empty():
    book = row_to_book(("asset-2", "Dune", "Frank Herbert", 1, 1.0, None))
    assert book.isbn is None
    assert book.isbn13 is None


def test_row_to_book_tolerates_nulls():
    book = row_to_book(("asset-3", None, None, None, None, None))
    assert book.title == ""
    assert book.authors == ()
    assert book.percent_complete == 0.0
    assert book.is_finished is False
    assert book.finished_at is None


def test_filter_finished_keeps_finished_flag_even_when_progress_is_low():
    books = filter_finished([_book_row("done", is_finished=1, reading_progress=0.0)])
    assert [b.source_id for b in books] == ["done"]


def test_filter_finished_keeps_progress_above_threshold_without_finished_flag():
    books = filter_finished(
        [_book_row("almost", is_finished=0, reading_progress=0.96)], threshold=0.95
    )
    assert len(books) == 1
    assert books[0].is_finished is False
    assert books[0].percent_complete == pytest.approx(96.0)


def test_filter_finished_drops_progress_below_threshold():
    books = filter_finished(
        [_book_row("early", is_finished=0, reading_progress=0.1)], threshold=0.95
    )
    assert books == []


def test_read_book_rows_opens_read_only(tmp_path):
    db_path = _make_db(tmp_path, [_book_row("a"), _book_row("b")])
    rows = read_book_rows(db_path)
    assert {r[0] for r in rows} == {"a", "b"}

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO ZBKLIBRARYASSET (ZASSETID) VALUES ('x')")
    finally:
        connection.close()


def test_finished_books_filters_a_mixed_library(tmp_path):
    coredata_value = datetime(2024, 1, 15, 8, 30, 0).timestamp() - COREDATA_EPOCH_OFFSET
    db_path = _make_db(
        tmp_path,
        [
            _book_row("read", is_finished=1, reading_progress=1.0, date_finished=coredata_value),
            _book_row("near", is_finished=0, reading_progress=0.96),
            _book_row("early", is_finished=0, reading_progress=0.1),
        ],
    )
    books = AppleBooksSource(path=db_path).finished_books(threshold=0.95)
    ids = {b.source_id for b in books}
    assert ids == {"read", "near"}
    read = next(b for b in books if b.source_id == "read")
    assert read.finished_at == datetime(2024, 1, 15, 8, 30, 0)


def test_read_book_rows_wraps_query_errors_as_source_error(tmp_path):
    db_path = tmp_path / "BKLibrary-1-empty.sqlite"
    sqlite3.connect(db_path).close()  # valid db, but no ZBKLIBRARYASSET table
    with pytest.raises(SourceError, match="Could not read Apple Books database"):
        read_book_rows(db_path)


def test_apple_books_source_declares_ebook_format():
    assert AppleBooksSource.media_format == "ebook"


def test_apple_books_source_raises_when_explicit_file_missing(tmp_path):
    with pytest.raises(SourceError, match="Apple Books database not found"):
        AppleBooksSource(path=tmp_path / "absent.sqlite")


def test_apple_books_source_raises_clear_error_when_db_absent(tmp_path, monkeypatch):
    import storywell.sources.applebooks as mod

    missing_dir = tmp_path / "no-library"
    monkeypatch.setattr(mod, "LIBRARY_DIR", missing_dir)
    with pytest.raises(SourceError, match="No Apple Books database"):
        AppleBooksSource()


def test_apple_books_source_auto_detects_globbed_database(tmp_path, monkeypatch):
    import storywell.sources.applebooks as mod

    library_dir = tmp_path / "BKLibrary"
    library_dir.mkdir()
    _make_db(library_dir, [_book_row("auto", is_finished=1)])
    monkeypatch.setattr(mod, "LIBRARY_DIR", library_dir)

    books = AppleBooksSource().finished_books()
    assert [b.source_id for b in books] == ["auto"]
