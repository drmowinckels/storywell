import csv
import io
from datetime import datetime

from storywell.sources.goodreads import (
    GoodreadsSource,
    parse_authors,
    parse_finished_at,
    parse_rating,
    parse_review,
)

HEADER = (
    "Book Id,Title,Author,Author l-f,Additional Authors,ISBN,ISBN13,My Rating,"
    "Average Rating,Publisher,Binding,Number of Pages,Year Published,"
    "Original Publication Year,Date Read,Date Added,Bookshelves,"
    "Bookshelves with positions,Exclusive Shelf,My Review,Spoiler,Private Notes,"
    "Read Count,Owned Copies"
)


def _row(**overrides):
    base = {
        "Book Id": "12345",
        "Title": "The Sample Novel",
        "Author": "Sample Author",
        "Additional Authors": "",
        "ISBN": '=""',
        "ISBN13": '=""',
        "My Rating": "0",
        "Date Read": "",
        "Exclusive Shelf": "read",
        "My Review": "",
    }
    base.update(overrides)
    return base


def _write_csv(tmp_path, *body_lines):
    csv_file = tmp_path / "goodreads_library_export.csv"
    csv_file.write_text("\n".join([HEADER, *body_lines]) + "\n", encoding="utf-8")
    return csv_file


def test_row_to_book_maps_read_shelf_to_finished():
    book = GoodreadsSource.row_to_book(GoodreadsSource, _row())
    assert book is not None
    assert book.source == "goodreads"
    assert book.source_id == "12345"
    assert book.title == "The Sample Novel"
    assert book.is_finished is True
    assert book.percent_complete == 0.0


def test_row_to_book_returns_book_for_to_read_but_not_finished():
    book = GoodreadsSource.row_to_book(GoodreadsSource, _row(**{"Exclusive Shelf": "to-read"}))
    assert book is not None
    assert book.is_finished is False


def test_row_to_book_returns_none_when_required_fields_missing():
    assert GoodreadsSource.row_to_book(GoodreadsSource, {"Title": "No Id"}) is None
    assert GoodreadsSource.row_to_book(GoodreadsSource, {"Book Id": "1"}) is None
    assert GoodreadsSource.row_to_book(GoodreadsSource, _row(**{"Book Id": ""})) is None


def test_parse_rating_zero_is_unrated():
    assert parse_rating(_row(**{"My Rating": "0"})) is None


def test_parse_rating_five_is_float():
    assert parse_rating(_row(**{"My Rating": "5"})) == 5.0


def test_parse_rating_tolerates_garbage():
    assert parse_rating(_row(**{"My Rating": "not-a-number"})) is None
    assert parse_rating(_row(**{"My Rating": ""})) is None


def test_parse_authors_combines_primary_and_additional():
    authors = parse_authors(
        _row(**{"Author": "Primary Writer", "Additional Authors": "Second Hand, Third Pen"})
    )
    assert authors == ("Primary Writer", "Second Hand", "Third Pen")


def test_parse_authors_single_when_no_additional():
    assert parse_authors(_row(**{"Author": "Solo Author"})) == ("Solo Author",)


def test_parse_authors_skips_empty_additional_names():
    authors = parse_authors(_row(**{"Author": "Primary", "Additional Authors": " , Real Name , "}))
    assert authors == ("Primary", "Real Name")


def test_isbn_and_isbn13_unwrap_excel_formula():
    book = GoodreadsSource.row_to_book(
        GoodreadsSource,
        _row(**{"ISBN": '="0439023483"', "ISBN13": '="9780439023481"'}),
    )
    assert book.isbn == "0439023483"
    assert book.isbn13 == "9780439023481"


def test_isbn_empty_formula_is_none():
    book = GoodreadsSource.row_to_book(GoodreadsSource, _row(**{"ISBN": '=""', "ISBN13": '=""'}))
    assert book.isbn is None
    assert book.isbn13 is None


def test_parse_finished_at_reads_goodreads_date():
    assert parse_finished_at(_row(**{"Date Read": "2025/09/12"})) == datetime(2025, 9, 12)


def test_parse_finished_at_empty_is_none():
    assert parse_finished_at(_row(**{"Date Read": ""})) is None


def test_parse_finished_at_unparseable_is_none():
    assert parse_finished_at(_row(**{"Date Read": "12-09-2025"})) is None


def test_parse_review_strips_and_blanks_to_none():
    assert parse_review(_row(**{"My Review": "  loved it  "})) == "loved it"
    assert parse_review(_row(**{"My Review": "   "})) is None


def test_row_to_book_tolerates_trimmed_optional_columns():
    book = GoodreadsSource.row_to_book(GoodreadsSource, {"Book Id": "1", "Title": "Bare"})
    assert book.authors == ()
    assert book.rating is None
    assert book.review is None
    assert book.isbn is None
    assert book.isbn13 is None
    assert book.finished_at is None
    assert book.is_finished is False


def _csv_row(**cells):
    columns = HEADER.split(",")
    out = io.StringIO()
    csv.writer(out, lineterminator="").writerow([cells.get(c, "") for c in columns])
    return out.getvalue()


def test_finished_books_end_to_end(tmp_path):
    csv_file = _write_csv(
        tmp_path,
        _csv_row(
            **{
                "Book Id": "1",
                "Title": "Read One",
                "Author": "Author A",
                "ISBN": '="0439023483"',
                "ISBN13": '="9780439023481"',
                "My Rating": "5",
                "Date Read": "2025/01/02",
                "Exclusive Shelf": "read",
                "My Review": "Great read",
            }
        ),
        _csv_row(**{"Book Id": "2", "Title": "Wishlist", "Exclusive Shelf": "to-read"}),
        _csv_row(
            **{"Book Id": "3", "Title": "In Progress", "Exclusive Shelf": "currently-reading"}
        ),
    )
    books = GoodreadsSource(path=csv_file).finished_books()
    assert [b.source_id for b in books] == ["1"]
    book = books[0]
    assert book.title == "Read One"
    assert book.authors == ("Author A",)
    assert book.rating == 5.0
    assert book.review == "Great read"
    assert book.isbn13 == "9780439023481"
    assert book.finished_at == datetime(2025, 1, 2)
