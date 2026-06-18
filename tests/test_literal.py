from datetime import date, datetime

import pytest

from storywell.sources.base import SourceError
from storywell.sources.literal import (
    LiteralSource,
    book_from_node,
    finished_date_from_read_dates,
    login_token,
    reviews_by_book_id,
)


def _book_node(
    book_id="b1",
    title="A Finished Book",
    authors=("An Author",),
    isbn10="0000000001",
    isbn13="9780000000001",
):
    return {
        "id": book_id,
        "title": title,
        "isbn10": isbn10,
        "isbn13": isbn13,
        "authors": [{"name": a} for a in authors],
    }


class _FakeTransport:
    """A mocked GraphQL transport keyed on the operation name, recording every call."""

    def __init__(self, *, books=None, read_dates=None, reviews=None, profile_id="p1"):
        self._pages = {"books": list(books or []), "reviews": list(reviews or [])}
        self._read_dates = read_dates or {}
        self._profile_id = profile_id
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, query, variables):
        self.calls.append((query, dict(variables)))
        if "StorywellProfile" in query:
            return {"me": {"id": self._profile_id}}
        if "StorywellFinishedBooks" in query:
            return {"booksByReadingStateAndProfile": self._page("books", variables)}
        if "StorywellMyReviews" in query:
            return {"myReviews": {"data": self._page("reviews", variables)}}
        if "StorywellReadDates" in query:
            return {"getReadDates": self._read_dates.get(variables["bookId"], [])}
        raise AssertionError(f"unexpected query: {query[:40]}")

    def _page(self, kind, variables):
        offset, limit = variables["offset"], variables["limit"]
        return self._pages[kind][offset : offset + limit]


def test_requires_token_or_transport(monkeypatch):
    monkeypatch.delenv("LITERAL_TOKEN", raising=False)
    with pytest.raises(SourceError, match="--token"):
        LiteralSource()


def test_accepts_token_without_transport(monkeypatch):
    monkeypatch.delenv("LITERAL_TOKEN", raising=False)
    source = LiteralSource(token="secret")
    assert source.name == "literal"
    assert source.token == "secret"


def test_token_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("LITERAL_TOKEN", "env-secret")
    source = LiteralSource()
    assert source.token == "env-secret"


def test_finished_books_maps_node_correctly():
    transport = _FakeTransport(
        books=[_book_node()],
        read_dates={"b1": [{"started": "2024-01-01", "finished": "2024-01-15"}]},
        reviews=[{"rating": 4.5, "text": "loved it", "book": {"id": "b1"}}],
    )
    source = LiteralSource(transport=transport)
    (book,) = source.finished_books()
    assert book.source == "literal"
    assert book.key == "literal:b1"
    assert book.title == "A Finished Book"
    assert book.authors == ("An Author",)
    assert book.rating == 4.5
    assert book.review == "loved it"
    assert book.isbn13 == "9780000000001"
    assert book.isbn == "0000000001"
    assert book.is_finished is True
    assert book.finished_at == datetime.fromisoformat("2024-01-15")


def test_finished_books_all_nodes_are_finished():
    transport = _FakeTransport(books=[_book_node("b1"), _book_node("b2")])
    source = LiteralSource(transport=transport)
    books = source.finished_books()
    assert [b.source_id for b in books] == ["b1", "b2"]
    assert all(b.is_finished for b in books)


def test_finished_books_queries_only_finished_status():
    transport = _FakeTransport(books=[_book_node()])
    LiteralSource(transport=transport).finished_books()
    finished_calls = [v for q, v in transport.calls if "StorywellFinishedBooks" in q]
    assert finished_calls
    assert all(v["readingStatus"] == "FINISHED" for v in finished_calls)


def test_finished_books_does_one_read_dates_call_per_book():
    transport = _FakeTransport(books=[_book_node("b1"), _book_node("b2"), _book_node("b3")])
    LiteralSource(transport=transport).finished_books()
    read_date_books = [v["bookId"] for q, v in transport.calls if "StorywellReadDates" in q]
    assert read_date_books == ["b1", "b2", "b3"]


def test_finished_books_passes_profile_id_to_per_book_and_page_queries():
    transport = _FakeTransport(books=[_book_node("b1")], profile_id="profile-xyz")
    LiteralSource(transport=transport).finished_books()
    page_calls = [v for q, v in transport.calls if "StorywellFinishedBooks" in q]
    read_calls = [v for q, v in transport.calls if "StorywellReadDates" in q]
    assert all(v["profileId"] == "profile-xyz" for v in page_calls + read_calls)


def test_finished_books_paginates_books_and_reviews():
    books = [_book_node(f"b{i}") for i in range(150)]
    reviews = [{"rating": 3, "text": f"r{i}", "book": {"id": f"b{i}"}} for i in range(150)]
    transport = _FakeTransport(books=books, reviews=reviews)
    result = LiteralSource(transport=transport).finished_books()
    assert [b.source_id for b in result] == [f"b{i}" for i in range(150)]
    assert result[149].review == "r149"
    page_offsets = sorted(v["offset"] for q, v in transport.calls if "StorywellFinishedBooks" in q)
    assert page_offsets == [0, 100]


def test_finished_books_uses_explicit_profile_id_without_me_query():
    transport = _FakeTransport(books=[_book_node("b1")])
    LiteralSource(transport=transport, profile_id="given").finished_books()
    assert not any("StorywellProfile" in q for q, _ in transport.calls)
    read_calls = [v for q, v in transport.calls if "StorywellReadDates" in q]
    assert all(v["profileId"] == "given" for v in read_calls)


def test_finished_books_empty_profile_raises():
    def transport(query, variables):
        return {"me": {"id": "  "}}

    source = LiteralSource(transport=transport)
    with pytest.raises(SourceError, match="profile id"):
        source.finished_books()


def test_finished_books_wraps_transport_exception():
    def transport(query, variables):
        raise RuntimeError("boom")

    source = LiteralSource(transport=transport)
    with pytest.raises(SourceError, match="Literal request failed"):
        source.finished_books()


def test_finished_books_propagates_source_error():
    def transport(query, variables):
        raise SourceError("Literal GraphQL error: nope")

    source = LiteralSource(transport=transport)
    with pytest.raises(SourceError, match="GraphQL error"):
        source.finished_books()


def test_finished_books_tolerates_empty_results():
    transport = _FakeTransport(books=[])
    assert LiteralSource(transport=transport).finished_books() == []


def test_book_from_node_full_node():
    book = book_from_node(
        _book_node("42", authors=("Ada", "Grace")),
        finished_at=datetime(2024, 5, 1),
        rating=5.0,
        review="great",
    )
    assert book.source_id == "42"
    assert book.authors == ("Ada", "Grace")
    assert book.rating == 5.0
    assert book.review == "great"
    assert book.finished_at == datetime(2024, 5, 1)
    assert book.is_finished is True


def test_book_from_node_missing_keys():
    book = book_from_node({"id": "x"})
    assert book.source_id == "x"
    assert book.title == ""
    assert book.authors == ()
    assert book.isbn is None
    assert book.isbn13 is None
    assert book.rating is None
    assert book.review is None
    assert book.finished_at is None
    assert book.is_finished is True


def test_book_from_node_blank_isbns_and_title_become_clean():
    book = book_from_node({"id": "x", "title": "  Trimmed  ", "isbn10": "  ", "isbn13": ""})
    assert book.title == "Trimmed"
    assert book.isbn is None
    assert book.isbn13 is None


def test_book_from_node_skips_blank_author_names():
    node = _book_node()
    node["authors"] = [{"name": "Real"}, {"name": "  "}, {}, "junk"]
    book = book_from_node(node)
    assert book.authors == ("Real",)


def test_finished_date_picks_latest_finish():
    read_dates = [
        {"started": "2020-01-01", "finished": "2020-02-01"},
        {"started": "2023-01-01", "finished": "2023-06-15"},
    ]
    assert finished_date_from_read_dates(read_dates) == datetime.fromisoformat("2023-06-15")


def test_finished_date_skips_unfinished_rereads():
    read_dates = [
        {"started": "2024-01-01", "finished": None},
        {"started": "2022-01-01", "finished": "2022-03-01"},
    ]
    assert finished_date_from_read_dates(read_dates) == datetime.fromisoformat("2022-03-01")


@pytest.mark.parametrize("read_dates", [None, [], [{"finished": "not-a-date"}], "junk", [{}]])
def test_finished_date_none_when_no_valid_finish(read_dates):
    assert finished_date_from_read_dates(read_dates) is None


def test_finished_date_parses_iso_with_z():
    result = finished_date_from_read_dates([{"finished": "2024-03-09T08:30:00Z"}])
    assert result.year == 2024
    assert result.month == 3
    assert result.day == 9


def test_finished_date_date_only_is_naive_datetime():
    result = finished_date_from_read_dates([{"finished": "2024-01-15"}])
    assert result == datetime.combine(date(2024, 1, 15), datetime.min.time())


def test_reviews_by_book_id_indexes_rating_and_text():
    index = reviews_by_book_id(
        [
            {"rating": 4, "text": "good", "book": {"id": "b1"}},
            {"rating": 0, "text": "   ", "book": {"id": "b2"}},
        ]
    )
    assert index["b1"] == (4.0, "good")
    assert index["b2"] == (None, None)


def test_reviews_by_book_id_skips_entries_without_book_id():
    index = reviews_by_book_id([{"rating": 4, "text": "x"}, {"book": {}}, "junk"])
    assert index == {}


def test_reviews_by_book_id_later_page_wins_on_duplicate():
    index = reviews_by_book_id(
        [
            {"rating": 1, "text": "first", "book": {"id": "b1"}},
            {"rating": 5, "text": "second", "book": {"id": "b1"}},
        ]
    )
    assert index["b1"] == (5.0, "second")


def test_reviews_by_book_id_coerces_numeric_book_id_to_str():
    index = reviews_by_book_id([{"rating": 4, "text": "x", "book": {"id": 99}}])
    assert "99" in index


def test_login_token_returns_token(monkeypatch):
    captured = {}

    def fake_post(query, variables, token):
        captured["query"] = query
        captured["variables"] = variables
        captured["token"] = token
        return {"login": {"token": "the-token", "profile": {"id": "p1"}}}

    monkeypatch.setattr("storywell.sources.literal._post", fake_post)
    assert login_token("a@b.com", "pw") == "the-token"
    assert "StorywellLogin" in captured["query"]
    assert captured["variables"] == {"email": "a@b.com", "password": "pw"}
    assert captured["token"] is None


def test_login_token_requires_email():
    with pytest.raises(SourceError, match="email"):
        login_token("", "pw")


def test_login_token_requires_password():
    with pytest.raises(SourceError, match="password"):
        login_token("a@b.com", "")


def test_login_token_missing_token_raises(monkeypatch):
    monkeypatch.setattr("storywell.sources.literal._post", lambda q, v, token: {"login": {}})
    with pytest.raises(SourceError, match="did not return a token"):
        login_token("a@b.com", "pw")
