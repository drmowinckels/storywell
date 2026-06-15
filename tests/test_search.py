from storywell.storygraph import search
from storywell.storygraph.search import (
    StorygraphSearcher,
    _candidates_from_records,
    parse_book_id,
)


def test_parse_book_id_extracts_slug():
    assert parse_book_id("/books/abc-123") == "abc-123"
    assert parse_book_id("https://app.thestorygraph.com/books/xyz?ref=1") == "xyz"
    assert parse_book_id("/authors/someone") is None
    assert parse_book_id("") is None


def test_candidates_from_records_skips_rows_without_book_id():
    records = [
        {"href": "/books/b1", "title": " The Book ", "author": " Author "},
        {"href": "/authors/nope", "title": "Bad", "author": "X"},
        {"href": "/books/b2", "title": "Second", "author": ""},
    ]
    candidates = _candidates_from_records(records, max_results=10)
    assert [c.book_id for c in candidates] == ["b1", "b2"]
    assert candidates[0].title == "The Book"
    assert candidates[0].author == "Author"


def test_candidates_from_records_respects_max_results():
    records = [{"href": f"/books/b{n}", "title": f"T{n}", "author": ""} for n in range(5)]
    candidates = _candidates_from_records(records, max_results=2)
    assert [c.book_id for c in candidates] == ["b0", "b1"]


class _FakeElement:
    def __init__(self, record):
        self._record = record

    def evaluate(self, _js):
        return self._record


class _FakePage:
    def __init__(self, records):
        self._records = records
        self.goto_urls = []

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)

    def wait_for_selector(self, selector, timeout=None):
        if not self._records:
            raise TimeoutError("no results")
        return object()

    def query_selector_all(self, _selector):
        return [_FakeElement(r) for r in self._records]


class _FakeContext:
    def __init__(self, page, storage_state=None):
        self._page = page
        self.storage_state = storage_state

    def new_page(self):
        return self._page


class _FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.closed = False

    def new_context(self, storage_state=None):
        return _FakeContext(self._page, storage_state=storage_state)

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    def launch(self, **kwargs):
        return self._browser


class _FakePlaywright:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)


class _FakeFactory:
    def __init__(self, page):
        self.browser = _FakeBrowser(page)
        self.pw = _FakePlaywright(self.browser)
        self.exited = False

    def __call__(self):
        return self

    def __enter__(self):
        return self.pw

    def __exit__(self, *exc):
        self.exited = True
        return False


def test_searcher_runs_query_and_returns_candidates(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}")
    page = _FakePage([{"href": "/books/b1", "title": "Hyperion", "author": "Dan Simmons"}])
    factory = _FakeFactory(page)
    with StorygraphSearcher(state_path=state, playwright_factory=factory) as searcher:
        results = searcher.search("Hyperion Dan Simmons")
    assert [c.book_id for c in results] == ["b1"]
    assert "search_term=Hyperion+Dan+Simmons" in page.goto_urls[0]
    assert factory.browser.closed is True
    assert factory.exited is True


def test_searcher_reuses_one_browser_for_multiple_queries(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}")
    page = _FakePage([{"href": "/books/b1", "title": "T", "author": "A"}])
    factory = _FakeFactory(page)
    with StorygraphSearcher(state_path=state, playwright_factory=factory) as searcher:
        searcher.search("one")
        searcher.search("two")
    assert len(page.goto_urls) == 2


def test_searcher_with_external_page_does_not_own_browser():
    page = _FakePage([{"href": "/books/b1", "title": "T", "author": "A"}])
    with StorygraphSearcher(page=page) as searcher:
        results = searcher.search("q")
    assert [c.book_id for c in results] == ["b1"]
    assert len(page.goto_urls) == 1


def test_search_books_single_shot(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}")
    page = _FakePage([{"href": "/books/b9", "title": "Solo", "author": "X"}])
    results = search.search_books("Solo", state_path=state, playwright_factory=_FakeFactory(page))
    assert [c.book_id for c in results] == ["b9"]


class _DescPage:
    def __init__(self, text):
        self._text = text
        self.goto_urls = []

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)

    def wait_for_selector(self, selector, timeout=None):
        return object()

    def wait_for_timeout(self, _ms):
        pass

    def evaluate(self, _js):
        return self._text


def test_fetch_description_returns_text():
    page = _DescPage("Included are the following: A, B.")
    with StorygraphSearcher(page=page) as searcher:
        desc = searcher.fetch_description("b1")
    assert "Included are the following" in desc
    assert any("/books/b1" in u for u in page.goto_urls)
