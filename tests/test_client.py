from datetime import date

import pytest

from storywell.storygraph.client import (
    DATE_DAY_SELECTOR,
    DATE_MONTH_SELECTOR,
    DATE_YEAR_SELECTOR,
    EXPLANATION_SELECTOR,
    READ_STATUS_LABEL_SELECTOR,
    REVIEW_SUBMIT_SELECTOR,
    STARS_DECIMAL_SELECTOR,
    STARS_INTEGER_SELECTOR,
    STATUS_READ_FORM_SELECTOR,
    SUBMIT_INSTANCE_SELECTOR,
    StorygraphClient,
    date_fields,
)
from storywell.storygraph.session import StorygraphAuthError

SIGN_IN_URL = "https://app.thestorygraph.com/users/sign_in"


def test_date_fields():
    assert date_fields(date(2023, 8, 5)) == {"day": "5", "month": "8", "year": "2023"}


class _FakeLabel:
    def __init__(self, text):
        self._text = text

    def inner_text(self):
        return self._text


class _FakeForm:
    def __init__(self, on_submit=None):
        self.on_submit = on_submit
        self.submitted = False

    def evaluate(self, _js):
        self.submitted = True
        if self.on_submit:
            self.on_submit()


class _FakeSelect:
    def __init__(self):
        self.value = None

    def select_option(self, value):
        self.value = value


class _FakeSubmit:
    def __init__(self, on_click=None):
        self.on_click = on_click
        self.clicked = False

    def click(self):
        self.clicked = True
        if self.on_click:
            self.on_click()


class _FakeExplanation:
    def __init__(self):
        self.value = None

    def evaluate(self, _js, arg):
        self.value = arg


class _FakePage:
    def __init__(self, elements, redirect_to=None):
        self.elements = dict(elements)
        self._redirect_to = redirect_to
        self.goto_urls = []
        self.url = ""

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)
        self.url = self._redirect_to or url

    def query_selector(self, selector):
        return self.elements.get(selector)

    def wait_for_timeout(self, _ms):
        pass

    def wait_for_load_state(self, *_args, **_kwargs):
        pass

    def set_label(self, text):
        self.elements[READ_STATUS_LABEL_SELECTOR] = _FakeLabel(text)

    def remove(self, selector):
        self.elements.pop(selector, None)


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
    page = _FakePage({READ_STATUS_LABEL_SELECTOR: _FakeLabel("to read")})
    form = _FakeForm(on_submit=lambda: page.set_label("read"))
    page.elements[STATUS_READ_FORM_SELECTOR] = form
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1") is True
    assert form.submitted is True


def test_mark_finished_returns_false_when_status_form_missing(tmp_path):
    page = _FakePage({READ_STATUS_LABEL_SELECTOR: _FakeLabel("to read")})
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1") is False


def test_mark_finished_false_when_status_never_flips(tmp_path):
    # the form submits but the status stays "to read": a silently-rejected write
    # must report failure, not be recorded as synced.
    page = _FakePage({READ_STATUS_LABEL_SELECTOR: _FakeLabel("to read")})
    form = _FakeForm()  # no on_submit -> label unchanged
    page.elements[STATUS_READ_FORM_SELECTOR] = form
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1") is False
    assert form.submitted is True


def test_mark_finished_sets_date_fields_and_submits(tmp_path):
    year, month, day = _FakeSelect(), _FakeSelect(), _FakeSelect()
    page = _FakePage({READ_STATUS_LABEL_SELECTOR: _FakeLabel("to read")})
    submit = _FakeSubmit(on_click=lambda: page.set_label("read"))
    page.elements.update(
        {
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


def test_mark_finished_dated_false_when_status_never_flips(tmp_path):
    year, month, day = _FakeSelect(), _FakeSelect(), _FakeSelect()
    submit = _FakeSubmit()  # submits but does not flip the status
    page = _FakePage(
        {
            READ_STATUS_LABEL_SELECTOR: _FakeLabel("to read"),
            DATE_YEAR_SELECTOR: year,
            DATE_MONTH_SELECTOR: month,
            DATE_DAY_SELECTOR: day,
            SUBMIT_INSTANCE_SELECTOR: submit,
        }
    )
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1", date(2023, 8, 18)) is False
    assert submit.clicked is True


def test_mark_finished_returns_false_when_date_field_missing(tmp_path):
    page = _FakePage({READ_STATUS_LABEL_SELECTOR: _FakeLabel("to read")})
    with _client(page, tmp_path) as client:
        assert client.mark_finished("b1", date(2023, 8, 18)) is False


def test_mark_finished_raises_when_session_expired(tmp_path):
    page = _FakePage({}, redirect_to=SIGN_IN_URL)
    with _client(page, tmp_path) as client, pytest.raises(StorygraphAuthError):
        client.mark_finished("b1")


def test_client_with_external_page_is_noop_on_already_read():
    page = _FakePage({READ_STATUS_LABEL_SELECTOR: _FakeLabel("read")})
    with StorygraphClient(page=page) as client:
        assert client.mark_finished("b1") is True
    assert page.goto_urls == ["https://app.thestorygraph.com/books/b1"]


def test_write_review_skipped_when_form_absent(tmp_path):
    page = _FakePage({})  # /reviews/new redirected -> no stars select
    with _client(page, tmp_path) as client:
        assert client.write_review("b1", stars_integer="5", explanation="x") == "skipped"


def test_write_review_writes_rating_and_explanation(tmp_path):
    stars, decimal, expl = _FakeSelect(), _FakeSelect(), _FakeExplanation()
    page = _FakePage({STARS_INTEGER_SELECTOR: stars, STARS_DECIMAL_SELECTOR: decimal})
    # a successful submit makes /reviews/new redirect away, so the stars form disappears.
    submit = _FakeSubmit(on_click=lambda: page.remove(STARS_INTEGER_SELECTOR))
    page.elements.update({EXPLANATION_SELECTOR: expl, REVIEW_SUBMIT_SELECTOR: submit})
    with _client(page, tmp_path) as client:
        status = client.write_review(
            "b1", stars_integer="5", stars_decimal="", explanation="Narrated by X."
        )
    assert status == "written"
    assert stars.value == "5"
    assert expl.value == "Narrated by X."
    assert submit.clicked is True
    assert any("/reviews/new?book_id=b1" in u for u in page.goto_urls)


def test_write_review_failed_when_no_submit(tmp_path):
    page = _FakePage({STARS_INTEGER_SELECTOR: _FakeSelect()})
    with _client(page, tmp_path) as client:
        assert client.write_review("b1", stars_integer="5") == "failed"


def test_write_review_failed_when_review_not_recorded(tmp_path):
    # the submit clicks but the review form is still present on re-check: not verified.
    stars = _FakeSelect()
    submit = _FakeSubmit()  # does not remove the stars form
    page = _FakePage({STARS_INTEGER_SELECTOR: stars, REVIEW_SUBMIT_SELECTOR: submit})
    with _client(page, tmp_path) as client:
        assert client.write_review("b1", stars_integer="5") == "failed"
    assert submit.clicked is True


def test_write_review_raises_when_session_expired(tmp_path):
    page = _FakePage({STARS_INTEGER_SELECTOR: _FakeSelect()}, redirect_to=SIGN_IN_URL)
    with _client(page, tmp_path) as client, pytest.raises(StorygraphAuthError):
        client.write_review("b1", stars_integer="5")
