from storywell.models import SourceBook
from storywell.storygraph.matching import (
    Candidate,
    MatchStatus,
    is_isbn,
    normalize_isbn,
)
from storywell.storygraph.sync import isbn_query, match_for_book


def test_normalize_isbn_strips_separators():
    assert normalize_isbn("978-0-439-02348-1") == "9780439023481"
    assert normalize_isbn("  0439023483 ") == "0439023483"
    assert normalize_isbn("") is None
    assert normalize_isbn(None) is None


def test_is_isbn_accepts_valid_10_and_13():
    assert is_isbn("9780439023481")
    assert is_isbn("978-0-439-02348-1")
    assert is_isbn("0439023483")
    assert is_isbn("080442957X")


def test_is_isbn_rejects_malformed():
    assert not is_isbn("12345")
    assert not is_isbn("notanisbn123")
    assert not is_isbn("")
    assert not is_isbn(None)


def _book(**kwargs):
    return SourceBook(source="goodreads", source_id="1", title="Dune", **kwargs)


def test_isbn_query_prefers_isbn13_then_isbn():
    assert isbn_query(_book(isbn13="978-0-441-17271-9", isbn="0441172717")) == "9780441172719"
    assert isbn_query(_book(isbn="0441172717")) == "0441172717"
    assert isbn_query(_book()) is None
    assert isbn_query(_book(isbn13="garbage")) is None


class _RecordingSearch:
    def __init__(self, by_query):
        self.by_query = by_query
        self.queries: list[str] = []

    def __call__(self, query):
        self.queries.append(query)
        return self.by_query.get(query, [])


def test_match_for_book_resolves_by_isbn_without_title_search():
    candidate = Candidate(book_id="b1", title="Dune", author="Frank Herbert")
    search = _RecordingSearch({"9780441172719": [candidate]})
    book = _book(isbn13="978-0-441-17271-9", authors=("Frank Herbert",))

    result = match_for_book(book, search)

    assert result.status is MatchStatus.MATCH
    assert result.best.candidate.book_id == "b1"
    assert search.queries == ["9780441172719"]


def test_match_for_book_falls_back_to_title_when_isbn_misses():
    candidate = Candidate(book_id="b9", title="Dune", author="Frank Herbert")
    search = _RecordingSearch({"Dune Frank Herbert": [candidate]})
    book = _book(isbn13="978-0-441-17271-9", authors=("Frank Herbert",))

    result = match_for_book(book, search)

    assert result.status is MatchStatus.MATCH
    assert result.best.candidate.book_id == "b9"
    assert search.queries == ["9780441172719", "Dune Frank Herbert"]


def test_match_for_book_uses_title_path_when_no_isbn():
    candidate = Candidate(book_id="b3", title="Dune", author="Frank Herbert")
    search = _RecordingSearch({"Dune Frank Herbert": [candidate]})
    book = _book(authors=("Frank Herbert",))

    result = match_for_book(book, search)

    assert result.best.candidate.book_id == "b3"
    assert search.queries == ["Dune Frank Herbert"]


def test_match_for_book_ignores_noncorroborating_isbn_hit():
    wrong = Candidate(book_id="bX", title="An Entirely Unrelated Book", author="Nobody")
    right = Candidate(book_id="b1", title="Dune", author="Frank Herbert")
    search = _RecordingSearch({"9780441172719": [wrong], "Dune Frank Herbert": [right]})
    book = _book(isbn13="978-0-441-17271-9", authors=("Frank Herbert",))

    result = match_for_book(book, search)

    assert result.best.candidate.book_id == "b1"
    assert search.queries == ["9780441172719", "Dune Frank Herbert"]
