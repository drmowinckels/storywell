import sqlite3
from datetime import datetime

import pytest

from storywell.sources.base import SourceError
from storywell.sources.kobo import (
    KoboSource,
    clean_isbn,
    parse_finished_at,
    read_book_rows,
    row_to_book,
)

_COLUMNS = (
    "ContentID",
    "Title",
    "Attribution",
    "ISBN",
    "ReadStatus",
    "___PercentRead",
    "DateLastRead",
    "ContentType",
)


def _book_row(
    content_id,
    title="A Title",
    attribution="An Author",
    isbn=None,
    read_status=2,
    percent_read=100,
    date_last_read=None,
    content_type=6,
):
    return (
        content_id,
        title,
        attribution,
        isbn,
        read_status,
        percent_read,
        date_last_read,
        content_type,
    )


def _make_db(tmp_path, rows):
    db_path = tmp_path / "KoboReader.sqlite"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE content ("
            "ContentID TEXT, Title TEXT, Attribution TEXT, ISBN TEXT, "
            "ReadStatus INTEGER, ___PercentRead INTEGER, DateLastRead TEXT, "
            "ContentType INTEGER)"
        )
        columns = ", ".join(_COLUMNS)
        placeholders = ", ".join("?" * len(_COLUMNS))
        connection.executemany(
            f"INSERT INTO content ({columns}) VALUES ({placeholders})",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return db_path


def test_clean_isbn_routes_thirteen_digits_to_isbn13():
    assert clean_isbn("9780439023481") == (None, "9780439023481")


def test_clean_isbn_routes_ten_digits_to_isbn():
    assert clean_isbn("0439023483") == ("0439023483", None)


def test_clean_isbn_strips_and_handles_empty():
    assert clean_isbn("  9780439023481  ") == (None, "9780439023481")
    assert clean_isbn("") == (None, None)
    assert clean_isbn("   ") == (None, None)
    assert clean_isbn(None) == (None, None)


def test_parse_finished_at_handles_trailing_z_and_fractions():
    assert parse_finished_at("2024-01-15T08:30:00.000") == datetime(2024, 1, 15, 8, 30, 0)
    assert parse_finished_at("2024-01-15T08:30:00Z") == datetime(2024, 1, 15, 8, 30, 0)


def test_parse_finished_at_tolerates_none_and_garbage():
    assert parse_finished_at(None) is None
    assert parse_finished_at("") is None
    assert parse_finished_at("not-a-date") is None


def test_row_to_book_maps_fields_and_authors():
    book = row_to_book(("id-1", "Dune", "Frank Herbert", None, 2, 100, "2024-01-15T08:30:00"))
    assert book.source == "kobo"
    assert book.source_id == "id-1"
    assert book.title == "Dune"
    assert book.authors == ("Frank Herbert",)
    assert book.is_finished is True
    assert book.percent_complete == 100.0
    assert book.finished_at == datetime(2024, 1, 15, 8, 30, 0)


def test_row_to_book_tolerates_nulls():
    book = row_to_book(("id-2", None, None, None, None, None, None))
    assert book.title == ""
    assert book.authors == ()
    assert book.percent_complete == 0.0
    assert book.is_finished is False
    assert book.finished_at is None
    assert book.isbn is None and book.isbn13 is None


def test_read_book_rows_excludes_non_book_content_type(tmp_path):
    db_path = _make_db(
        tmp_path,
        [
            _book_row("book", content_type=6),
            _book_row("chapter", content_type=9, read_status=2),
        ],
    )
    rows = read_book_rows(db_path)
    assert [r[0] for r in rows] == ["book"]


def test_finished_books_includes_read_status_two_with_isbn13(tmp_path):
    db_path = _make_db(
        tmp_path,
        [
            _book_row(
                "finished",
                isbn="9780439023481",
                read_status=2,
                percent_read=100,
                date_last_read="2024-01-15T08:30:00.000",
            )
        ],
    )
    books = KoboSource(path=db_path).finished_books()
    assert len(books) == 1
    book = books[0]
    assert book.is_finished is True
    assert book.isbn13 == "9780439023481"
    assert book.isbn is None
    assert book.finished_at == datetime(2024, 1, 15, 8, 30, 0)


def test_finished_books_keeps_book_above_threshold_even_if_not_marked_read(tmp_path):
    db_path = _make_db(
        tmp_path, [_book_row("almost-done", read_status=1, percent_read=96, isbn="0439023483")]
    )
    books = KoboSource(path=db_path).finished_books(threshold=0.95)
    assert len(books) == 1
    assert books[0].is_finished is False
    assert books[0].isbn == "0439023483"
    assert books[0].isbn13 is None


def test_finished_books_drops_book_below_threshold(tmp_path):
    db_path = _make_db(tmp_path, [_book_row("barely-started", read_status=1, percent_read=10)])
    assert KoboSource(path=db_path).finished_books(threshold=0.95) == []


def test_finished_books_filters_a_mixed_library(tmp_path):
    db_path = _make_db(
        tmp_path,
        [
            _book_row("read", read_status=2, percent_read=100),
            _book_row("near", read_status=1, percent_read=96),
            _book_row("early", read_status=1, percent_read=10),
            _book_row("nullisbn", read_status=2, percent_read=100, isbn=None, date_last_read=None),
            _book_row("not-a-book", content_type=899, read_status=2, percent_read=100),
        ],
    )
    ids = {b.source_id for b in KoboSource(path=db_path).finished_books(threshold=0.95)}
    assert ids == {"read", "near", "nullisbn"}


def test_kobo_source_requires_a_path():
    with pytest.raises(SourceError, match="needs --file PATH"):
        KoboSource()


def test_kobo_source_raises_when_file_missing(tmp_path):
    with pytest.raises(SourceError, match="Kobo database not found"):
        KoboSource(path=tmp_path / "absent.sqlite")
