import csv
import io
from datetime import datetime

from storywell.models import Shelf
from storywell.sources.bookwyrm import (
    BookwyrmSource,
    clean_isbn,
    parse_authors,
    parse_date,
    parse_rating,
    parse_review,
)
from storywell.sources.csv_source import read_rows

HEADER = [
    "title",
    "authors",
    "isbn_13",
    "isbn_10",
    "shelf",
    "review_name",
    "review_body",
    "rating",
    "date_added",
    "date_started",
    "date_finished",
]


def _row(**overrides):
    base = {
        "title": "The Will of the Many",
        "authors": "James Islington",
        "isbn_13": "9781982141189",
        "isbn_10": "1982141182",
        "shelf": "read",
        "review_name": "",
        "review_body": "",
        "rating": "5",
        "date_added": "2024-01-01",
        "date_started": "2024-03-01",
        "date_finished": "2024-03-10",
    }
    base.update(overrides)
    return base


def _write(tmp_path, *rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=HEADER)
    writer.writeheader()
    for row in rows:
        writer.writerow({h: row.get(h, "") for h in HEADER})
    path = tmp_path / "bookwyrm_export.csv"
    path.write_text(out.getvalue(), encoding="utf-8")
    return path


def _one(tmp_path, row):
    src = BookwyrmSource(path=_write(tmp_path, row))
    return src.row_to_book(read_rows(src.path)[0])


# --- pure helpers -----------------------------------------------------------------------


def test_parse_authors_splits_comma_list():
    assert parse_authors({"authors": "First Writer, Second Writer"}) == (
        "First Writer",
        "Second Writer",
    )


def test_parse_authors_falls_back_to_author_text():
    assert parse_authors({"author_text": "Solo Author"}) == ("Solo Author",)
    assert parse_authors({"authors": "", "author_text": "Fallback"}) == ("Fallback",)


def test_parse_authors_empty_is_tuple():
    assert parse_authors({}) == ()
    assert parse_authors({"authors": " , , "}) == ()


def test_parse_rating_half_stars_and_unrated():
    assert parse_rating("5") == 5.0
    assert parse_rating("4.5") == 4.5
    assert parse_rating("0") is None
    assert parse_rating("") is None
    assert parse_rating(None) is None
    assert parse_rating("nope") is None


def test_parse_review_joins_name_and_body():
    assert parse_review({"review_name": "Loved it", "review_body": "A great read."}) == (
        "Loved it\n\nA great read."
    )
    assert parse_review({"review_name": "", "review_body": "Body only"}) == "Body only"
    assert parse_review({"review_name": "Title only", "review_body": ""}) == "Title only"
    assert parse_review({"review_name": "  ", "review_body": "  "}) is None
    assert parse_review({}) is None


def test_parse_date_handles_iso_variants():
    assert parse_date("2024-03-10") == datetime(2024, 3, 10)
    assert parse_date("2024-03-10T14:30:00") == datetime(2024, 3, 10, 14, 30)
    assert parse_date("2024-03-10T14:30:00Z") == datetime(2024, 3, 10, 14, 30)
    assert parse_date("2024-03-10 14:30:00") == datetime(2024, 3, 10, 14, 30)
    assert parse_date("") is None
    assert parse_date(None) is None
    assert parse_date("not-a-date") is None


def test_clean_isbn_strips_hyphens_and_blanks():
    assert clean_isbn("978-1-982141-18-9") == "9781982141189"
    assert clean_isbn(" 1982141182 ") == "1982141182"
    assert clean_isbn("") is None
    assert clean_isbn(None) is None


# --- shelf -> status mapping ------------------------------------------------------------


def test_read_shelf_maps_to_finished_with_finish_date(tmp_path):
    book = _one(tmp_path, _row(shelf="read", date_finished="2024-03-10"))
    assert book.is_finished is True
    assert book.status is Shelf.READ
    assert book.finished_at == datetime(2024, 3, 10)


def test_reading_shelf_maps_to_currently_reading(tmp_path):
    book = _one(tmp_path, _row(shelf="reading"))
    assert book.is_finished is False
    assert book.status is Shelf.CURRENTLY_READING
    assert book.finished_at is None


def test_currently_reading_alias_maps_to_currently_reading(tmp_path):
    book = _one(tmp_path, _row(shelf="currently-reading"))
    assert book.status is Shelf.CURRENTLY_READING
    assert book.is_finished is False


def test_to_read_shelf_maps_to_to_read(tmp_path):
    book = _one(tmp_path, _row(shelf="to-read"))
    assert book.is_finished is False
    assert book.status is Shelf.TO_READ
    assert book.finished_at is None


def test_unknown_shelf_stays_unknown_and_unfinished(tmp_path):
    book = _one(tmp_path, _row(shelf="favorites"))
    assert book.is_finished is False
    assert book.status is Shelf.UNKNOWN


def test_read_shelf_without_finish_date_is_finished_without_date(tmp_path):
    book = _one(tmp_path, _row(shelf="read", date_finished=""))
    assert book.is_finished is True
    assert book.status is Shelf.READ
    assert book.finished_at is None


# --- field extraction -------------------------------------------------------------------


def test_row_to_book_extracts_isbn_rating_review(tmp_path):
    book = _one(
        tmp_path,
        _row(review_name="Wow", review_body="Best of the year.", rating="4.5"),
    )
    assert book.source == "bookwyrm"
    assert book.title == "The Will of the Many"
    assert book.authors == ("James Islington",)
    assert book.isbn == "1982141182"
    assert book.isbn13 == "9781982141189"
    assert book.rating == 4.5
    assert book.review == "Wow\n\nBest of the year."


def test_source_id_prefers_isbn13_then_isbn10_then_title(tmp_path):
    assert _one(tmp_path, _row()).source_id == "9781982141189"
    assert _one(tmp_path, _row(isbn_13="")).source_id == "1982141182"
    assert _one(tmp_path, _row(isbn_13="", isbn_10="")).source_id == "The Will of the Many"


def test_row_to_book_returns_none_without_title(tmp_path):
    assert _one(tmp_path, _row(title="")) is None


def test_row_to_book_tolerates_missing_optional_columns():
    book = BookwyrmSource.row_to_book(BookwyrmSource, {"title": "Bare", "shelf": "to-read"})
    assert book.title == "Bare"
    assert book.source_id == "Bare"
    assert book.authors == ()
    assert book.rating is None
    assert book.review is None
    assert book.isbn is None
    assert book.isbn13 is None
    assert book.status is Shelf.TO_READ


# --- end to end -------------------------------------------------------------------------


def test_finished_books_surfaces_read_reading_and_to_read(tmp_path):
    path = _write(
        tmp_path,
        _row(
            title="Finished Book",
            isbn_13="9780000000001",
            isbn_10="",
            shelf="read",
            date_finished="2024-05-01",
            rating="5",
            review_name="Done",
            review_body="",
        ),
        _row(
            title="Reading Now",
            isbn_13="9780000000002",
            isbn_10="",
            shelf="reading",
            date_finished="",
        ),
        _row(
            title="Want It",
            isbn_13="9780000000003",
            isbn_10="",
            shelf="to-read",
            date_finished="",
        ),
        _row(
            title="On A Custom Shelf",
            isbn_13="9780000000004",
            isbn_10="",
            shelf="favorites",
            date_finished="",
        ),
    )
    books = BookwyrmSource(path=path).finished_books()
    by_title = {b.title: b for b in books}

    assert set(by_title) == {"Finished Book", "Reading Now", "Want It"}
    assert by_title["Finished Book"].is_finished is True
    assert by_title["Finished Book"].status is Shelf.READ
    assert by_title["Finished Book"].finished_at == datetime(2024, 5, 1)
    assert by_title["Finished Book"].rating == 5.0
    assert by_title["Finished Book"].review == "Done"
    assert by_title["Reading Now"].status is Shelf.CURRENTLY_READING
    assert by_title["Want It"].status is Shelf.TO_READ
