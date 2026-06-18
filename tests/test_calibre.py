import sqlite3
from datetime import datetime

import pytest

from storywell.sources import available_sources, make_source
from storywell.sources.base import SourceError
from storywell.sources.calibre import (
    CalibreSource,
    clean_isbn,
    is_finished_value,
    parse_authors,
    parse_timestamp,
    resolve_db_path,
    resolve_read_column,
)


def _make_db(
    tmp_path,
    *,
    custom_column_id=1,
    custom_label="read",
    custom_datatype="bool",
    books=(),
):
    """Build a minimal Calibre metadata.db.

    ``books`` is a list of dicts: id, title, isbn (fixed column), timestamp, authors (list),
    identifiers (list of (type, val)), read_value (the per-book custom-column value or None).
    """
    db_path = tmp_path / "metadata.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, isbn TEXT, timestamp TEXT);
            CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE books_authors_link (book INTEGER, author INTEGER);
            CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INTEGER, type TEXT, val TEXT);
            CREATE TABLE custom_columns (
                id INTEGER PRIMARY KEY, label TEXT, name TEXT, datatype TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO custom_columns (id, label, name, datatype) VALUES (?, ?, ?, ?)",
            (custom_column_id, custom_label, custom_label.title(), custom_datatype),
        )
        connection.execute(
            f"CREATE TABLE custom_{custom_column_id} (id INTEGER PRIMARY KEY, "
            "book INTEGER, value)"
        )
        author_ids: dict[str, int] = {}
        for book in books:
            connection.execute(
                "INSERT INTO books (id, title, isbn, timestamp) VALUES (?, ?, ?, ?)",
                (book["id"], book.get("title"), book.get("isbn"), book.get("timestamp")),
            )
            for name in book.get("authors", ()):
                if name not in author_ids:
                    cur = connection.execute("INSERT INTO authors (name) VALUES (?)", (name,))
                    author_ids[name] = cur.lastrowid
                connection.execute(
                    "INSERT INTO books_authors_link (book, author) VALUES (?, ?)",
                    (book["id"], author_ids[name]),
                )
            for id_type, val in book.get("identifiers", ()):
                connection.execute(
                    "INSERT INTO identifiers (book, type, val) VALUES (?, ?, ?)",
                    (book["id"], id_type, val),
                )
            if "read_value" in book:
                connection.execute(
                    f"INSERT INTO custom_{custom_column_id} (book, value) VALUES (?, ?)",
                    (book["id"], book["read_value"]),
                )
        connection.commit()
    finally:
        connection.close()
    return db_path


def test_clean_isbn_routes_thirteen_digits_to_isbn13():
    assert clean_isbn("9780439023481") == (None, "9780439023481")


def test_clean_isbn_routes_ten_digits_to_isbn():
    assert clean_isbn("0439023483") == ("0439023483", None)


def test_clean_isbn_strips_hyphens_spaces_and_handles_empty():
    assert clean_isbn("  9780439023481  ") == (None, "9780439023481")
    assert clean_isbn("978-0-439-02348-1") == (None, "9780439023481")
    assert clean_isbn("0-439-02348-3") == ("0439023483", None)
    assert clean_isbn("") == (None, None)
    assert clean_isbn("   ") == (None, None)
    assert clean_isbn(None) == (None, None)


def test_parse_authors_splits_on_ampersand():
    assert parse_authors("Frank Herbert & Brian Herbert") == ("Frank Herbert", "Brian Herbert")
    assert parse_authors("Solo Author") == ("Solo Author",)
    assert parse_authors(None) == ()
    assert parse_authors("") == ()


def test_parse_timestamp_tolerates_fractions_and_garbage():
    assert parse_timestamp("2024-01-15T08:30:00.123456") == datetime(2024, 1, 15, 8, 30, 0)
    assert parse_timestamp("2024-01-15T08:30:00Z") == datetime(2024, 1, 15, 8, 30, 0)
    assert parse_timestamp("2024-01-15 08:30:00") == datetime(2024, 1, 15, 8, 30, 0)
    assert parse_timestamp(None) is None
    assert parse_timestamp("not-a-date") is None


def test_is_finished_value_truthy_cases():
    assert is_finished_value(True) is True
    assert is_finished_value(1) is True
    assert is_finished_value(5) is True
    assert is_finished_value(3.5) is True
    assert is_finished_value("Yes") is True
    assert is_finished_value("true") is True
    assert is_finished_value("READ") is True


def test_is_finished_value_falsy_cases():
    assert is_finished_value(None) is False
    assert is_finished_value(False) is False
    assert is_finished_value(0) is False
    assert is_finished_value(0.0) is False
    assert is_finished_value("") is False
    assert is_finished_value("no") is False
    assert is_finished_value("maybe") is False


def test_resolve_db_path_accepts_directory_and_file(tmp_path):
    db = _make_db(tmp_path)
    assert resolve_db_path(db) == db
    assert resolve_db_path(tmp_path) == db


def test_resolve_read_column_matches_label_case_insensitively(tmp_path):
    db = _make_db(tmp_path, custom_column_id=7, custom_label="Read Status")
    connection = sqlite3.connect(db)
    try:
        assert resolve_read_column(connection, "read status") == 7
    finally:
        connection.close()


def test_resolve_read_column_unknown_label_lists_available(tmp_path):
    db = _make_db(tmp_path, custom_label="read")
    connection = sqlite3.connect(db)
    try:
        with pytest.raises(SourceError, match="custom column 'finished' not found.*read"):
            resolve_read_column(connection, "finished")
    finally:
        connection.close()


def test_finished_books_keeps_only_books_marked_read(tmp_path):
    db = _make_db(
        tmp_path,
        custom_label="read",
        custom_datatype="bool",
        books=[
            {"id": 1, "title": "Read One", "authors": ["A Author"], "read_value": 1},
            {"id": 2, "title": "Unread", "authors": ["B Author"], "read_value": 0},
            {"id": 3, "title": "No Value", "authors": ["C Author"]},
        ],
    )
    books = CalibreSource(path=db, read_column="read").finished_books()
    assert {b.title for b in books} == {"Read One"}
    assert books[0].source == "calibre"
    assert books[0].source_id == "1"
    assert books[0].authors == ("A Author",)
    assert books[0].is_finished is True


def test_finished_books_extracts_isbn_from_identifiers_table(tmp_path):
    db = _make_db(
        tmp_path,
        custom_label="read",
        books=[
            {
                "id": 1,
                "title": "Has ISBN13",
                "identifiers": [("isbn", "9780439023481"), ("amazon", "B00X")],
                "read_value": "Yes",
            },
            {
                "id": 2,
                "title": "Has ISBN10",
                "identifiers": [("isbn", "0439023483")],
                "read_value": "Yes",
            },
            {
                "id": 3,
                "title": "No ISBN, only amazon",
                "identifiers": [("amazon", "B001")],
                "read_value": "Yes",
            },
        ],
    )
    by_title = {b.title: b for b in CalibreSource(path=db, read_column="read").finished_books()}
    assert by_title["Has ISBN13"].isbn13 == "9780439023481"
    assert by_title["Has ISBN13"].isbn is None
    assert by_title["Has ISBN10"].isbn == "0439023483"
    assert by_title["Has ISBN10"].isbn13 is None
    assert by_title["No ISBN, only amazon"].isbn is None
    assert by_title["No ISBN, only amazon"].isbn13 is None


def test_finished_books_handles_rating_column(tmp_path):
    db = _make_db(
        tmp_path,
        custom_label="myrating",
        custom_datatype="rating",
        books=[
            {"id": 1, "title": "Rated", "read_value": 8},
            {"id": 2, "title": "Zero rating", "read_value": 0},
        ],
    )
    books = CalibreSource(path=db, read_column="myrating").finished_books()
    assert {b.title for b in books} == {"Rated"}


def test_finished_books_sets_finished_at_from_timestamp(tmp_path):
    db = _make_db(
        tmp_path,
        custom_label="read",
        books=[{"id": 1, "title": "T", "timestamp": "2024-03-09T12:00:00", "read_value": 1}],
    )
    book = CalibreSource(path=db, read_column="read").finished_books()[0]
    assert book.finished_at == datetime(2024, 3, 9, 12, 0, 0)


def test_finished_books_accepts_library_directory(tmp_path):
    _make_db(
        tmp_path,
        custom_label="read",
        books=[{"id": 1, "title": "T", "read_value": 1}],
    )
    books = CalibreSource(path=tmp_path, read_column="read").finished_books()
    assert len(books) == 1


def test_calibre_source_requires_a_path():
    with pytest.raises(SourceError, match="needs --file PATH"):
        CalibreSource(read_column="read")


def test_calibre_source_requires_read_column(tmp_path):
    db = _make_db(tmp_path)
    with pytest.raises(SourceError, match="needs --read-column"):
        CalibreSource(path=db)
    with pytest.raises(SourceError, match="needs --read-column"):
        CalibreSource(path=db, read_column="   ")


def test_calibre_source_raises_when_db_missing(tmp_path):
    with pytest.raises(SourceError, match="Calibre database not found"):
        CalibreSource(path=tmp_path / "absent", read_column="read")


def test_calibre_source_declares_ebook_format():
    assert CalibreSource.media_format == "ebook"


def test_calibre_registered_in_sources():
    assert "calibre" in available_sources()


def test_make_source_builds_calibre_with_read_column(tmp_path):
    db = _make_db(tmp_path, books=[{"id": 1, "title": "T", "read_value": 1}])
    src = make_source("calibre", path=db, read_column="read")
    assert isinstance(src, CalibreSource)
    assert src.read_column == "read"
    assert len(src.finished_books()) == 1
