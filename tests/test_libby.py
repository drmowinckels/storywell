from pathlib import Path

from storywell.models import Shelf
from storywell.sources import LibbySource as ExportedLibbySource
from storywell.sources import available_sources, make_source
from storywell.sources.csv_source import read_rows
from storywell.sources.libby import (
    LibbySource,
    dedup_key,
    parse_isbn,
    parse_rating,
)

FIXTURE = Path(__file__).parent / "fixtures" / "libby_timeline_sample.csv"

HEADER = [
    "Cover",
    "Title",
    "Author",
    "Publisher",
    "ISBN",
    "Timestamp",
    "Activity",
    "Details",
    "Library",
]


def _write(tmp_path, *rows):
    lines = [",".join(HEADER)]
    for row in rows:
        lines.append(",".join(row.get(h, "") for h in HEADER))
    path = tmp_path / "libbytimeline-activities.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _event(**overrides):
    base = {
        "Title": "The Will of the Many",
        "Author": "James Islington",
        "Publisher": "Saga Press",
        "ISBN": "9781982141189",
        "Timestamp": "2026-05-01T09:12:00Z",
        "Activity": "Borrowed",
        "Library": "Example County Library",
    }
    base.update(overrides)
    return base


def _one(tmp_path, row, **opts):
    src = LibbySource(path=_write(tmp_path, row), **opts)
    return src.row_to_book(read_rows(src.path)[0])


# --- registration -----------------------------------------------------------------------


def test_libby_is_registered_and_buildable(tmp_path):
    assert "libby" in available_sources()
    assert ExportedLibbySource is LibbySource
    src = make_source("libby", path=FIXTURE, shelf=Shelf.TO_READ)
    assert isinstance(src, LibbySource)
    assert src.shelf is Shelf.TO_READ


def test_make_source_drops_options_libby_does_not_accept():
    src = make_source("libby", path=FIXTURE, profile="us", read_date=True)
    assert isinstance(src, LibbySource)
    assert src.shelf is None


# --- pure helpers -----------------------------------------------------------------------


def test_parse_isbn_routes_by_length():
    assert parse_isbn("9781982141189") == (None, "9781982141189")
    assert parse_isbn("1982141182") == ("1982141182", None)
    assert parse_isbn("978-1-982141-18-9") == (None, "9781982141189")
    assert parse_isbn("") == (None, None)
    assert parse_isbn(None) == (None, None)
    assert parse_isbn("not-an-isbn") == (None, None)


def test_parse_isbn_unwraps_excel_formula():
    assert parse_isbn('="9781982141189"') == (None, "9781982141189")


def test_parse_rating_handles_unrated_and_garbage():
    assert parse_rating("4") == 4.0
    assert parse_rating("4.5") == 4.5
    assert parse_rating("0") is None
    assert parse_rating("") is None
    assert parse_rating("nope") is None


def test_dedup_key_uses_title_author_even_when_isbn_present():
    # Title+author drives dedup; the ISBN must not, or one book's event rows fragment when
    # some rows carry an ISBN and others (holds/returns) do not.
    assert dedup_key("The Will of the Many", "James Islington", "9781982141189", None) == (
        "ta:the-will-of-the-many|james-islington"
    )
    assert dedup_key("The Will of the Many", "James Islington", None, None) == (
        "ta:the-will-of-the-many|james-islington"
    )


def test_dedup_key_is_stable_across_mixed_isbn_presence():
    # The exact fragmentation case: same book, one row with ISBN, one without -> same key.
    with_isbn = dedup_key("Babel", "R. F. Kuang", "9780063021426", None)
    without_isbn = dedup_key("Babel", "R. F. Kuang", None, None)
    assert with_isbn == without_isbn


def test_dedup_key_falls_back_to_isbn_when_no_title():
    assert dedup_key(None, None, "9781982141189", None) == "isbn:9781982141189"
    assert dedup_key(None, None, None, "1982141182") == "isbn:1982141182"


# --- mapping a single activity row ------------------------------------------------------


def test_row_to_book_parses_raw_event(tmp_path):
    book = _one(tmp_path, _event(), shelf=Shelf.TO_READ)
    assert book.source == "libby"
    assert book.source_id == "ta:the-will-of-the-many|james-islington"
    assert book.title == "The Will of the Many"
    assert book.authors == ("James Islington",)
    assert book.isbn13 == "9781982141189"  # ISBN still rides on the book for matching
    assert book.isbn is None


def test_row_without_isbn_keys_on_title_author(tmp_path):
    book = _one(tmp_path, _event(ISBN=""), shelf=Shelf.TO_READ)
    assert book.source_id == "ta:the-will-of-the-many|james-islington"


def test_borrow_is_never_finished_without_read_shelf(tmp_path):
    for shelf in (Shelf.TO_READ, Shelf.CURRENTLY_READING, Shelf.DID_NOT_FINISH):
        book = _one(tmp_path, _event(), shelf=shelf)
        assert book.is_finished is False, shelf
        assert book.finished_at is None, shelf
        assert book.status is shelf


def test_no_shelf_leaves_status_unknown_and_unfinished(tmp_path):
    book = _one(tmp_path, _event())
    assert book.status is Shelf.UNKNOWN
    assert book.is_finished is False


def test_shelf_read_marks_borrow_finished(tmp_path):
    book = _one(tmp_path, _event(), shelf=Shelf.READ)
    assert book.is_finished is True
    assert book.status is Shelf.READ


def test_shelf_accepts_string_value(tmp_path):
    book = _one(tmp_path, _event(), shelf="to-read")
    assert book.status is Shelf.TO_READ


def test_row_with_no_identity_is_dropped(tmp_path):
    book = _one(tmp_path, _event(Title="", Author="", ISBN=""), shelf=Shelf.TO_READ)
    assert book is None


# --- end-to-end over the realistic raw fixture ------------------------------------------


def test_no_shelf_syncs_nothing():
    # A borrow history is not reading status: a plain sync (no --shelf) must not flood a shelf.
    src = LibbySource(path=FIXTURE)
    assert src.finished_books() == []


def test_to_read_routes_every_borrow_deduped():
    src = LibbySource(path=FIXTURE, shelf=Shelf.TO_READ)
    books = src.finished_books()
    # 3 distinct titles across 5 activity rows (borrow+return, hold+borrow, single borrow).
    assert len(books) == 3
    assert {b.title for b in books} == {
        "The Will of the Many",
        "Project Hail Mary",
        "Babel",
    }
    assert all(b.status is Shelf.TO_READ for b in books)
    assert all(b.is_finished is False for b in books)


def test_dedup_collapses_multiple_activity_rows_per_title(tmp_path):
    path = _write(
        tmp_path,
        _event(Activity="Placed on hold"),
        _event(Activity="Borrowed"),
        _event(Activity="Returned"),
    )
    books = LibbySource(path=path, shelf=Shelf.TO_READ).finished_books()
    assert len(books) == 1
    assert books[0].source_id == "ta:the-will-of-the-many|james-islington"


def test_dedup_without_isbn_uses_title_author(tmp_path):
    path = _write(
        tmp_path,
        _event(ISBN="", Activity="Borrowed"),
        _event(ISBN="", Activity="Returned"),
    )
    books = LibbySource(path=path, shelf=Shelf.TO_READ).finished_books()
    assert len(books) == 1


def test_dedup_collapses_rows_with_inconsistent_isbn_presence(tmp_path):
    # Regression: a borrow row may carry an ISBN while its hold/return rows do not. All event
    # rows for one title must still collapse to a single book, never fragment by ISBN presence.
    path = _write(
        tmp_path,
        _event(Activity="Borrowed", ISBN="9781982141189"),
        _event(Activity="Returned", ISBN=""),
    )
    books = LibbySource(path=path, shelf=Shelf.TO_READ).finished_books()
    assert len(books) == 1


def test_read_shelf_marks_all_borrows_read():
    src = LibbySource(path=FIXTURE, shelf=Shelf.READ)
    books = src.finished_books()
    assert len(books) == 3
    assert all(b.is_finished is True for b in books)
    assert all(b.status is Shelf.READ for b in books)


def test_nothing_is_claimed_read_without_read_shelf():
    # The core safety property: routing to to-read must never claim a book as finished/read.
    src = LibbySource(path=FIXTURE, shelf=Shelf.TO_READ)
    books = src.finished_books()
    assert all(not b.is_finished for b in books)
    assert all(b.status is not Shelf.READ for b in books)
    assert all(b.finished_at is None for b in books)


# --- OverDrive website "History" export (the other accepted format) ----------------------

OVERDRIVE_HEADER = [
    "Title",
    "Sub Title",
    "Author",
    "Series",
    "Publisher",
    "Publish Date",
    "Star Rating",
    "Star Rating Count",
    "Maturity Level",
    "ISBN",
    "Cover Art URL",
    "Borrow Date",
    "Type",
]


def _write_overdrive(tmp_path, *rows):
    lines = [",".join(OVERDRIVE_HEADER)]
    for row in rows:
        lines.append(",".join(row.get(h, "") for h in OVERDRIVE_HEADER))
    path = tmp_path / "overdrive-history.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_overdrive_history_format_routes_to_shelf(tmp_path):
    path = _write_overdrive(
        tmp_path,
        {
            "Title": "The Will of the Many",
            "Author": "James Islington",
            "Star Rating": "5",
            "ISBN": "9781982141189",
            "Type": "ebook",
        },
        {"Title": "Project Hail Mary", "Author": "Andy Weir", "Type": "audiobook"},
    )
    books = LibbySource(path=path, shelf=Shelf.TO_READ).finished_books()
    assert {b.title for b in books} == {"The Will of the Many", "Project Hail Mary"}
    assert all(b.status is Shelf.TO_READ for b in books)
    rated = next(b for b in books if b.title == "The Will of the Many")
    assert rated.rating == 5.0
    assert rated.isbn13 == "9781982141189"
