from datetime import date

from audible_storygraph_sync.storygraph.client import (
    DATE_DAY_SELECTOR,
    DATE_MONTH_SELECTOR,
    DATE_YEAR_SELECTOR,
    READ_STATUS_LABEL_SELECTOR,
    STATUS_READ_FORM_SELECTOR,
    SUBMIT_INSTANCE_SELECTOR,
    StorygraphClient,
    date_fields,
)


def test_date_fields():
    assert date_fields(date(2023, 8, 5)) == {"day": "5", "month": "8", "year": "2023"}


class _FakeLabel:
    def __init__(self, text):
        self._text = text

    def inner_text(self):
        return self._text


class _FakeForm:
    def __init__(self):
        self.submitted = False

    def evaluate(self, _js):
        self.submitted = True


class _FakeSelect:
    def __init__(self):
        self.value = None

    def select_option(self, value):
        self.value = value


class _FakeSubmit:
    def __init__(self):
        self.clicked = False

    def click(self):
        self.clicked = True


class _FakePage:
    def __init__(self, elements):
        self._elements = elements
        self.goto_urls = []

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)

    def query_selector(self, selector):
        return self._elements.get(selector)

    def wait_for_timeout(self, _ms):
        pass


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


def test_mark_finished_already_read_is_noop(tmp_path):
    page = _FakePage({READ_STATUS_LABEL_SELECTOR: _FakeLabel("read")})
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1", date(2023, 8, 18)) is True
    assert page.goto_urls == ["https://app.thestorygraph.com/books/b1"]


def test_mark_finished_submits_status_form_when_not_read(tmp_path):
    form = _FakeForm()
    page = _FakePage(
        {READ_STATUS_LABEL_SELECTOR: _FakeLabel("to read"), STATUS_READ_FORM_SELECTOR: form}
    )
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1") is True
    assert form.submitted is True


def test_mark_finished_returns_false_when_status_form_missing(tmp_path):
    page = _FakePage({READ_STATUS_LABEL_SELECTOR: _FakeLabel("to read")})
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1") is False


def test_mark_finished_sets_date_fields_and_submits(tmp_path):
    form = _FakeForm()
    year, month, day, submit = _FakeSelect(), _FakeSelect(), _FakeSelect(), _FakeSubmit()
    page = _FakePage(
        {
            READ_STATUS_LABEL_SELECTOR: _FakeLabel("to read"),
            STATUS_READ_FORM_SELECTOR: form,
            DATE_YEAR_SELECTOR: year,
            DATE_MONTH_SELECTOR: month,
            DATE_DAY_SELECTOR: day,
            SUBMIT_INSTANCE_SELECTOR: submit,
        }
    )
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1", date(2023, 8, 18)) is True
    assert (year.value, month.value, day.value) == ("2023", "8", "18")
    assert submit.clicked is True
    assert any("/read_instances/new?book_id=b1" in u for u in page.goto_urls)


def test_mark_finished_returns_false_when_date_field_missing(tmp_path):
    form = _FakeForm()
    page = _FakePage(
        {READ_STATUS_LABEL_SELECTOR: _FakeLabel("to read"), STATUS_READ_FORM_SELECTOR: form}
    )
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1", date(2023, 8, 18)) is False


def test_client_with_external_page_is_noop_on_already_read():
    page = _FakePage({READ_STATUS_LABEL_SELECTOR: _FakeLabel("read")})
    with StorygraphClient(page=page) as client:
        assert client.mark_finished("b1") is True
    assert page.goto_urls == ["https://app.thestorygraph.com/books/b1"]
