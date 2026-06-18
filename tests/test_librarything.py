import json
from datetime import date, datetime

import pytest

from storywell.models import Shelf
from storywell.sources import librarything as lt
from storywell.sources.base import SourceError
from storywell.sources.csv_source import read_rows
from storywell.sources.librarything import (
    LibraryThingSource,
    flip_name,
    parse_date,
    parse_isbns,
    parse_rating,
    split_collections,
)


def _raise_recursion(*args, **kwargs):
    raise RecursionError("too deep")


HEADER = [
    "Book Id",
    "Title",
    "Primary Author",
    "Secondary Author",
    "Rating",
    "Review",
    "Date Read",
    "Collections",
    "ISBN",
    "ISBNs",
]


def _write(tmp_path, *rows):
    lines = ["\t".join(HEADER)]
    for row in rows:
        lines.append("\t".join(row.get(h, "") for h in HEADER))
    path = tmp_path / "librarything.tsv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _book(**overrides):
    base = {
        "Book Id": "316340625",
        "Title": "The Will of the Many",
        "Primary Author": "Islington, James",
        "Rating": "5",
        "Collections": "Your library",
        "ISBN": "[1982141182]",
        "ISBNs": "1982141182, 9781982141189",
    }
    base.update(overrides)
    return base


def _one(tmp_path, row, **opts):
    src = LibraryThingSource(path=_write(tmp_path, row), **opts)
    return src.row_to_book(read_rows(src.path)[0])


# --- pure helpers -----------------------------------------------------------------------


def test_flip_name_reorders_last_first():
    assert flip_name("Islington, James") == "James Islington"
    assert flip_name("James Islington") == "James Islington"
    assert flip_name(None) is None


def test_parse_isbns_list_and_bracketed():
    assert parse_isbns("1982141182, 9781982141189", "[1982141182]") == (
        "1982141182",
        "9781982141189",
    )
    assert parse_isbns("", "[1982141182]") == ("1982141182", None)
    assert parse_isbns("9781982141189", "") == (None, "9781982141189")
    assert parse_isbns("", "") == (None, None)


def test_parse_rating_half_stars_and_unrated():
    assert parse_rating("5") == 5.0
    assert parse_rating("4.5") == 4.5
    assert parse_rating("0") is None
    assert parse_rating("") is None
    assert parse_rating("nope") is None


def test_parse_date_multiple_formats():
    for raw in ("2024-03-10", "03/10/2024", "Mar 10, 2024", "2024/03/10"):
        assert parse_date(raw) == datetime(2024, 3, 10)
    assert parse_date("") is None
    assert parse_date("sometime") is None


def test_split_collections():
    assert split_collections("Your library, Read") == ["Your library", "Read"]
    assert split_collections("") == []


# --- mapping against the real tab-delimited format --------------------------------------


def test_row_to_book_parses_real_format(tmp_path):
    book = _one(tmp_path, _book())
    assert book.source == "librarything"
    assert book.source_id == "316340625"
    assert book.title == "The Will of the Many"
    assert book.authors == ("James Islington",)
    assert book.rating == 5.0
    assert book.isbn == "1982141182"
    assert book.isbn13 == "9781982141189"


def test_catalogue_book_is_not_finished_by_default(tmp_path):
    book = _one(tmp_path, _book())  # "Your library", no Date Read
    assert book.is_finished is False
    assert book.finished_at is None


def test_catalogue_book_status_unknown_by_default(tmp_path):
    book = _one(tmp_path, _book())
    assert book.status is Shelf.UNKNOWN


def test_mark_read_treats_catalogue_as_read(tmp_path):
    book = _one(tmp_path, _book(), mark_read=True)
    assert book.is_finished is True
    assert book.finished_at is None
    assert book.status is Shelf.READ


def test_shelf_read_is_the_modern_as_read(tmp_path):
    book = _one(tmp_path, _book(), shelf=Shelf.READ)
    assert book.is_finished is True
    assert book.status is Shelf.READ


def test_shelf_to_read_routes_catalogue_without_marking_finished(tmp_path):
    book = _one(tmp_path, _book(), shelf=Shelf.TO_READ)
    assert book.is_finished is False
    assert book.finished_at is None
    assert book.status is Shelf.TO_READ


def test_shelf_to_read_still_marks_explicitly_read_books_read(tmp_path):
    book = _one(tmp_path, _book(**{"Date Read": "2024-03-10"}), shelf=Shelf.TO_READ)
    assert book.is_finished is True
    assert book.status is Shelf.READ
    assert book.finished_at == datetime(2024, 3, 10)


def test_shelf_accepts_string_value(tmp_path):
    book = _one(tmp_path, _book(), shelf="currently-reading")
    assert book.status is Shelf.CURRENTLY_READING
    assert book.is_finished is False


def test_finished_books_includes_routed_to_read_catalogue(tmp_path):
    path = _write(
        tmp_path,
        _book(**{"Book Id": "1", "Collections": "Your library"}),
        _book(**{"Book Id": "2", "Collections": "Your library"}),
    )
    src = LibraryThingSource(path=path, shelf=Shelf.TO_READ)
    books = src.finished_books()
    assert sorted(b.source_id for b in books) == ["1", "2"]
    assert all(b.status is Shelf.TO_READ for b in books)


def test_read_date_stamps_today_placeholder(tmp_path):
    book = _one(tmp_path, _book(), mark_read=True, read_date=True, today=date(2026, 6, 16))
    assert book.is_finished is True
    assert book.finished_at == datetime(2026, 6, 16)


def test_explicit_date_read_is_honored_without_mark_read(tmp_path):
    book = _one(tmp_path, _book(**{"Date Read": "2024-03-10"}))
    assert book.is_finished is True
    assert book.finished_at == datetime(2024, 3, 10)


def test_read_collection_counts_as_finished(tmp_path):
    book = _one(tmp_path, _book(**{"Collections": "Your library, Read"}))
    assert book.is_finished is True


def test_to_read_collection_not_finished(tmp_path):
    for collection in ("To-read", "Currently reading", "Reading"):
        book = _one(tmp_path, _book(**{"Collections": collection}))
        assert book.is_finished is False, collection


def test_collection_filter_includes_and_excludes(tmp_path):
    in_fav = _one(
        tmp_path, _book(**{"Collections": "Your library, Favorites"}), collections=("favorites",)
    )
    assert in_fav is not None
    out = _one(tmp_path, _book(**{"Collections": "Your library"}), collections=("favorites",))
    assert out is None


def test_source_id_falls_back_to_isbn13_then_slug(tmp_path):
    book = _one(tmp_path, _book(**{"Book Id": ""}))
    assert book.source_id == "9781982141189"


def test_case_insensitive_headers(tmp_path):
    path = tmp_path / "lt.tsv"
    path.write_text("TITLE\tPRIMARY AUTHOR\tRATING\nDune\tHerbert, Frank\t4\n", encoding="utf-8")
    book = LibraryThingSource(path=path).row_to_book(read_rows(path)[0])
    assert book.title == "Dune"
    assert book.authors == ("Frank Herbert",)
    assert book.rating == 4.0


def test_finished_books_end_to_end_with_as_read(tmp_path):
    path = _write(
        tmp_path,
        _book(**{"Book Id": "1", "Collections": "Your library"}),
        _book(**{"Book Id": "2", "Collections": "Your library"}),
    )
    catalogued = LibraryThingSource(path=path)
    assert catalogued.finished_books() == []  # pure catalogue: nothing read
    as_read = LibraryThingSource(path=path, mark_read=True, read_date=True, today=date(2026, 6, 16))
    books = as_read.finished_books()
    assert sorted(b.source_id for b in books) == ["1", "2"]
    assert all(b.finished_at == datetime(2026, 6, 16) for b in books)


# --- JSON export path -------------------------------------------------------------------


def _json_book(**overrides):
    base = {
        "books_id": "316340625",
        "title": "The Will of the Many (Hierarchy)",
        "originaltitle": "The Will of the Many",
        "authors": [{"fl": "James Islington", "lf": "Islington, James", "role": "Author"}],
        "rating": 5,
        "collections": ["Your library"],
        "isbn": {"0": "1982141182", "2": "9781982141189"},
    }
    base.update(overrides)
    return base


def _json_file(tmp_path, *books):
    path = tmp_path / "librarything.json"
    path.write_text(json.dumps({b["books_id"]: b for b in books}), encoding="utf-8")
    return path


def test_json_catalogue_not_finished_by_default(tmp_path):
    src = LibraryThingSource(path=_json_file(tmp_path, _json_book()))
    assert src.finished_books() == []


def test_json_as_read_prefers_originaltitle_and_maps_fields(tmp_path):
    src = LibraryThingSource(
        path=_json_file(tmp_path, _json_book()),
        mark_read=True,
        read_date=True,
        today=date(2026, 6, 16),
    )
    (book,) = src.finished_books()
    assert book.title == "The Will of the Many"  # originaltitle, not the series-suffixed title
    assert book.authors == ("James Islington",)  # fl is already "First Last"
    assert book.rating == 5.0
    assert book.isbn == "1982141182"
    assert book.isbn13 == "9781982141189"
    assert book.finished_at == datetime(2026, 6, 16)


def test_json_read_collection_is_finished_without_mark_read(tmp_path):
    src = LibraryThingSource(
        path=_json_file(tmp_path, _json_book(collections=["Your library", "Read"]))
    )
    (book,) = src.finished_books()
    assert book.is_finished is True


def test_json_collection_filter(tmp_path):
    path = _json_file(
        tmp_path,
        _json_book(books_id="1", collections=["Your library", "Favorites"]),
        _json_book(books_id="2", collections=["Your library"]),
    )
    src = LibraryThingSource(path=path, mark_read=True, collections=("favorites",))
    assert [b.source_id for b in src.finished_books()] == ["1"]


def test_json_rows_tolerates_list_and_malformed_entries(tmp_path):
    path = tmp_path / "lt.json"
    path.write_text(
        json.dumps(
            [
                _json_book(books_id="1", isbn="not-a-dict"),
                None,
                "garbage",
                {"books_id": "2", "authors": [{}], "collections": ["Read"]},
            ]
        ),
        encoding="utf-8",
    )
    books = LibraryThingSource(path=path, mark_read=True).finished_books()
    assert {b.source_id for b in books} == {"1", "2"}


# --- finish-date precedence -------------------------------------------------------------


def test_explicit_date_read_preferred_over_placeholder(tmp_path):
    book = _one(
        tmp_path,
        _book(**{"Date Read": "2024-03-10"}),
        mark_read=True,
        read_date=True,
        today=date(2026, 6, 16),
    )
    assert book.finished_at == datetime(2024, 3, 10)


def test_read_date_without_mark_read_leaves_catalogue_unread(tmp_path):
    book = _one(tmp_path, _book(), read_date=True, today=date(2026, 6, 16))
    assert book.is_finished is False
    assert book.finished_at is None


def test_json_recursion_error_becomes_source_error(tmp_path, monkeypatch):
    path = tmp_path / "deep.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(lt.json, "loads", _raise_recursion)
    with pytest.raises(SourceError):
        LibraryThingSource(path=path, mark_read=True).finished_books()
