from datetime import date

from audible_storygraph_sync.storygraph import client as client_mod
from audible_storygraph_sync.storygraph.client import (
    DATE_DAY_SELECTOR,
    DATE_MONTH_SELECTOR,
    DATE_YEAR_SELECTOR,
    EDIT_DATE_SELECTOR,
    MARK_READ_SELECTOR,
    READ_STATUS_SELECTOR,
    SAVE_DATE_SELECTOR,
    StorygraphClient,
    date_fields,
)


def test_date_fields():
    assert date_fields(date(2023, 8, 5)) == {"day": "5", "month": "8", "year": "2023"}


class _FakeControl:
    def __init__(self):
        self.clicked = False
        self.value = None

    def click(self):
        self.clicked = True

    def select_option(self, value):
        self.value = value

    def fill(self, value):
        self.value = value


class _FakePage:
    def __init__(self, elements):
        self._elements = elements
        self.goto_urls = []

    def goto(self, url):
        self.goto_urls.append(url)

    def query_selector(self, selector):
        return self._elements.get(selector)


class _FakeContext:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


class _FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.closed = False

    def new_context(self, storage_state=None):
        return _FakeContext(self._page)

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

    def __call__(self):
        return self

    def __enter__(self):
        return self.pw

    def __exit__(self, *exc):
        return False


def _client(page, tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}")
    return StorygraphClient(state_path=state, playwright_factory=_FakeFactory(page))


def test_mark_finished_already_read_no_date(tmp_path):
    page = _FakePage({READ_STATUS_SELECTOR: _FakeControl()})
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1") is True
    assert page.goto_urls[0].endswith("/books/b1")


def test_mark_finished_clicks_mark_control(tmp_path):
    control = _FakeControl()
    page = _FakePage({MARK_READ_SELECTOR: control})
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1") is True
    assert control.clicked is True


def test_mark_finished_returns_false_when_no_control(tmp_path):
    page = _FakePage({})
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1") is False


def test_mark_finished_sets_date_fields(tmp_path):
    year, month, day, save = _FakeControl(), _FakeControl(), _FakeControl(), _FakeControl()
    page = _FakePage(
        {
            READ_STATUS_SELECTOR: _FakeControl(),
            EDIT_DATE_SELECTOR: _FakeControl(),
            DATE_YEAR_SELECTOR: year,
            DATE_MONTH_SELECTOR: month,
            DATE_DAY_SELECTOR: day,
            SAVE_DATE_SELECTOR: save,
        }
    )
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1", date(2023, 8, 18)) is True
    assert (year.value, month.value, day.value) == ("2023", "8", "18")
    assert save.clicked is True


def test_mark_finished_returns_false_when_date_editor_missing(tmp_path):
    page = _FakePage({READ_STATUS_SELECTOR: _FakeControl()})
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1", date(2023, 8, 18)) is False


def test_set_value_falls_back_to_fill_when_select_unsupported():
    class _FillOnly:
        def __init__(self):
            self.filled = None

        def select_option(self, value):
            raise ValueError("not a select")

        def fill(self, value):
            self.filled = value

    element = _FillOnly()
    client_mod._set_value(element, "7")
    assert element.filled == "7"
