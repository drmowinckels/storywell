from datetime import date, datetime

import pytest

from storywell.sources.base import SourceError
from storywell.sources.hardcover import HardcoverSource, book_from_node


def _read_node(
    book_id=101,
    title="A Read Book",
    authors=("Read Author",),
    rating=4.5,
    review="loved it",
    isbn_13="9780000000001",
    isbn_10="0000000001",
    last_read_date="2024-01-15",
    status_id=3,
):
    return {
        "status_id": status_id,
        "rating": rating,
        "review_raw": review,
        "last_read_date": last_read_date,
        "book": {
            "id": book_id,
            "title": title,
            "contributions": [{"author": {"name": a}} for a in authors],
            "editions": [{"isbn_13": isbn_13, "isbn_10": isbn_10}],
        },
    }


def _reading_node(book_id=202, title="A Current Book"):
    return {
        "status_id": 2,
        "rating": None,
        "review_raw": None,
        "last_read_date": None,
        "book": {
            "id": book_id,
            "title": title,
            "contributions": [{"author": {"name": "Reading Author"}}],
            "editions": [{"isbn_13": "9780000000002", "isbn_10": "0000000002"}],
        },
    }


def _fake_transport(user_books):
    def transport(query, variables):
        return {"me": [{"user_books": list(user_books)}]}

    return transport


def test_requires_token_or_transport(monkeypatch):
    monkeypatch.delenv("HARDCOVER_TOKEN", raising=False)
    with pytest.raises(SourceError, match="--token"):
        HardcoverSource()


def test_accepts_token_without_transport(monkeypatch):
    monkeypatch.delenv("HARDCOVER_TOKEN", raising=False)
    source = HardcoverSource(token="secret")
    assert source.name == "hardcover"
    assert source.token == "secret"


def test_token_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("HARDCOVER_TOKEN", "env-secret")
    source = HardcoverSource()
    assert source.token == "env-secret"


def test_finished_books_returns_only_read_node():
    source = HardcoverSource(transport=_fake_transport([_read_node(), _reading_node()]))
    books = source.finished_books()
    assert [b.source_id for b in books] == ["101"]


def test_finished_books_maps_read_node_correctly():
    source = HardcoverSource(transport=_fake_transport([_read_node(), _reading_node()]))
    (book,) = source.finished_books()
    assert book.source == "hardcover"
    assert book.key == "hardcover:101"
    assert book.title == "A Read Book"
    assert book.authors == ("Read Author",)
    assert book.rating == 4.5
    assert book.review == "loved it"
    assert book.isbn13 == "9780000000001"
    assert book.isbn == "0000000001"
    assert book.is_finished is True
    assert book.finished_at == datetime.fromisoformat("2024-01-15")


def test_finished_books_tolerates_empty_me():
    def transport(query, variables):
        return {"me": []}

    source = HardcoverSource(transport=transport)
    assert source.finished_books() == []


def test_finished_books_wraps_transport_exception():
    def transport(query, variables):
        raise RuntimeError("boom")

    source = HardcoverSource(transport=transport)
    with pytest.raises(SourceError, match="Hardcover request failed"):
        source.finished_books()


def test_finished_books_propagates_source_error():
    def transport(query, variables):
        raise SourceError("Hardcover GraphQL error: nope")

    source = HardcoverSource(transport=transport)
    with pytest.raises(SourceError, match="GraphQL error"):
        source.finished_books()


def test_book_from_node_full_node():
    book = book_from_node(_read_node(book_id=42, authors=("Ada", "Grace")))
    assert book.source_id == "42"
    assert book.authors == ("Ada", "Grace")
    assert book.rating == 4.5
    assert book.is_finished is True


def test_book_from_node_missing_book_editions_contributions():
    book = book_from_node({"status_id": 3})
    assert book.source_id == "None"
    assert book.title == ""
    assert book.authors == ()
    assert book.isbn is None
    assert book.isbn13 is None
    assert book.rating is None
    assert book.review is None
    assert book.finished_at is None
    assert book.is_finished is True


def test_book_from_node_not_read_is_not_finished():
    book = book_from_node(_reading_node())
    assert book.is_finished is False


def test_book_from_node_rating_zero_becomes_none():
    book = book_from_node(_read_node(rating=0))
    assert book.rating is None


def test_book_from_node_blank_review_becomes_none():
    book = book_from_node(_read_node(review="   "))
    assert book.review is None


@pytest.mark.parametrize("raw", [None, "", "not-a-date", 12345, {"x": 1}])
def test_book_from_node_tolerates_bad_last_read_date(raw):
    book = book_from_node(_read_node(last_read_date=raw))
    assert book.finished_at is None


def test_book_from_node_parses_iso_datetime_with_z():
    book = book_from_node(_read_node(last_read_date="2024-03-09T08:30:00Z"))
    assert book.finished_at.year == 2024
    assert book.finished_at.month == 3
    assert book.finished_at.day == 9


def test_book_from_node_no_editions_yields_no_isbns():
    node = _read_node()
    node["book"]["editions"] = []
    book = book_from_node(node)
    assert book.isbn is None
    assert book.isbn13 is None


def test_book_from_node_skips_missing_author_names():
    node = _read_node()
    node["book"]["contributions"] = [{"author": {"name": "Real"}}, {"author": {}}, {}]
    book = book_from_node(node)
    assert book.authors == ("Real",)


def test_book_from_node_date_only_is_naive_datetime():
    book = book_from_node(_read_node(last_read_date="2024-01-15"))
    assert book.finished_at == datetime.combine(date(2024, 1, 15), datetime.min.time())
