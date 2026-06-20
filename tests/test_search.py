import pytest

from storywell.storygraph import search
from storywell.storygraph.search import (
    StorygraphSearcher,
    _candidates_from_records,
    parse_book_id,
)
from storywell.storygraph.session import StorygraphAuthError


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
    def __init__(self, records, redirect_to=None):
        self._records = records
        self._redirect_to = redirect_to
        self.goto_urls = []
        self.url = ""

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)
        self.url = self._redirect_to or url

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
    def __init__(self, text, redirect_to=None):
        self._text = text
        self._redirect_to = redirect_to
        self.goto_urls = []
        self.url = ""

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)
        self.url = self._redirect_to or url

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


class _EditionEl:
    def __init__(self, record):
        self._record = record

    def evaluate(self, _js):
        return self._record


class _EditionsPage:
    def __init__(self, pages):
        self._pages = pages
        self._current = []
        self.goto_urls = []

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)
        import re

        match = re.search(r"page=(\d+)", url)
        self._current = self._pages.get(int(match.group(1)) if match else 1, [])

    def wait_for_selector(self, selector, timeout=None):
        if not self._current:
            raise TimeoutError("no editions")
        return object()

    def query_selector_all(self, _selector):
        return [_EditionEl(r) for r in self._current]

    def query_selector(self, selector):
        import re

        match = re.search(r"page=(\d+)", selector)
        return object() if match and int(match.group(1)) in self._pages else None


def test_resolve_edition_returns_audio_edition_on_first_page():
    page = _EditionsPage(
        {1: [{"id": "paper", "format": "Paperback"}, {"id": "audio1", "format": "Audio"}]}
    )
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.resolve_edition("work", "audio") == "audio1"
    assert page.goto_urls == ["https://app.thestorygraph.com/books/work/editions?page=1"]


def test_resolve_edition_pages_until_it_finds_audio():
    page = _EditionsPage(
        {
            1: [{"id": "paper", "format": "Paperback"}],
            2: [{"id": "audio2", "format": "Audio"}],
        }
    )
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.resolve_edition("work", "audio") == "audio2"
    assert len(page.goto_urls) == 2


def test_resolve_edition_none_when_no_audio_edition_exists():
    page = _EditionsPage({1: [{"id": "paper", "format": "Paperback"}]})
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.resolve_edition("work", "audio") is None


def test_resolve_edition_skips_browser_for_unknown_format():
    page = _EditionsPage({1: [{"id": "audio1", "format": "Audio"}]})
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.resolve_edition("work", "") is None
    assert page.goto_urls == []


def test_resolve_edition_respects_max_pages():
    page = _EditionsPage(
        {n: [{"id": "paper", "format": "Paperback"}] for n in range(1, 5)}
        | {5: [{"id": "late", "format": "Audio"}]}
    )
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.resolve_edition("work", "audio", max_pages=3) is None
    assert len(page.goto_urls) == 3


def test_resolve_edition_stops_when_no_next_page_link():
    page = _EditionsPage({1: [{"id": "paper", "format": "Paperback"}]})  # single page, no audio
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.resolve_edition("work", "audio", max_pages=3) is None
    assert len(page.goto_urls) == 1  # did not load empty trailing pages


class _BoomEditionsPage:
    def __init__(self):
        self.goto_urls = []

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)
        raise RuntimeError("navigation failed")


def test_resolve_edition_degrades_to_none_on_scrape_failure():
    page = _BoomEditionsPage()
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.resolve_edition("work", "audio") is None
    assert len(page.goto_urls) == 1


def test_list_editions_returns_all_editions_on_one_page():
    page = _EditionsPage({1: [{"id": "a", "format": "Audio"}, {"id": "b", "format": "Paperback"}]})
    with StorygraphSearcher(page=page) as searcher:
        editions = searcher.list_editions("work")
    assert [(e.book_id, e.format) for e in editions] == [("a", "audio"), ("b", "paperback")]
    assert len(page.goto_urls) == 1


def test_list_editions_pages_and_dedupes_by_id():
    page = _EditionsPage(
        {
            1: [{"id": "a", "format": "Paperback"}, {"id": "b", "format": "Audio"}],
            2: [{"id": "b", "format": "Audio"}, {"id": "c", "format": "Hardcover"}],
        }
    )
    with StorygraphSearcher(page=page) as searcher:
        editions = searcher.list_editions("work")
    assert [e.book_id for e in editions] == ["a", "b", "c"]
    assert len(page.goto_urls) == 2


def test_list_editions_empty_when_no_panes():
    page = _EditionsPage({})
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.list_editions("work") == []


def test_list_editions_degrades_to_collected_on_failure():
    page = _BoomEditionsPage()
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.list_editions("work") == []


class _MarkerEditionsPage:
    def __init__(self, marker_present, *, panes=True, boom=False):
        self.marker_present = marker_present
        self.panes = panes
        self.boom = boom
        self.goto_urls = []
        self.url = ""

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)
        if self.boom:
            raise RuntimeError("navigation failed")
        self.url = url

    def wait_for_selector(self, selector, timeout=None):
        if not self.panes:
            raise TimeoutError("no editions")
        return object()

    def query_selector(self, selector):
        return object() if (self.marker_present and "another edition" in selector) else None


def test_read_on_another_edition_true_when_marker_present():
    page = _MarkerEditionsPage(marker_present=True)
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.read_on_another_edition("work") is True
    assert page.goto_urls == ["https://app.thestorygraph.com/books/work/editions"]


def test_read_on_another_edition_false_when_marker_absent():
    page = _MarkerEditionsPage(marker_present=False)
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.read_on_another_edition("work") is False


def test_read_on_another_edition_false_when_no_editions_pane():
    # an editions page that never renders panes must not be read as "read elsewhere".
    page = _MarkerEditionsPage(marker_present=True, panes=False)
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.read_on_another_edition("work") is False


def test_read_on_another_edition_degrades_to_false_on_scrape_failure():
    # best-effort: a scrape failure must not skip a wanted read (better a rare dup).
    page = _MarkerEditionsPage(marker_present=True, boom=True)
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.read_on_another_edition("work") is False


def test_search_returns_empty_when_no_results(tmp_path):
    page = _FakePage([])  # wait_for_selector times out -> no candidates
    with StorygraphSearcher(page=page) as searcher:
        assert searcher.search("nothing here") == []


def test_search_raises_when_session_expired():
    page = _FakePage(
        [{"href": "/books/b1", "title": "T", "author": "A"}],
        redirect_to="https://app.thestorygraph.com/users/sign_in",
    )
    with StorygraphSearcher(page=page) as searcher, pytest.raises(StorygraphAuthError):
        searcher.search("anything")


def test_search_caps_at_max_results():
    records = [{"href": f"/books/b{n}", "title": f"T{n}", "author": ""} for n in range(10)]
    page = _FakePage(records)
    with StorygraphSearcher(page=page, max_results=3) as searcher:
        results = searcher.search("q")
    assert [c.book_id for c in results] == ["b0", "b1", "b2"]


def test_fetch_description_raises_when_session_expired():
    page = _DescPage("desc", redirect_to="https://app.thestorygraph.com/users/sign_in")
    with StorygraphSearcher(page=page) as searcher, pytest.raises(StorygraphAuthError):
        searcher.fetch_description("b1")
