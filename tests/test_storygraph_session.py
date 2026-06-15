import json
import sys
from pathlib import Path

import pytest

from storywell.storygraph import session
from storywell.storygraph.session import (
    SIGN_IN_URL,
    StorygraphAuthError,
    StorygraphBrowser,
    StorygraphDependencyError,
    _is_signed_in,
    is_authenticated,
    login,
)


class _FakePage:
    def __init__(self, *, url="https://app.thestorygraph.com/"):
        self._url = url
        self.goto_urls: list[str] = []

    @property
    def url(self):
        return self._url

    def goto(self, url):
        self.goto_urls.append(url)


class _FakeContext:
    def __init__(self, page, *, storage_state=None, cookies=("remember_user_token",)):
        self._page = page
        self.storage_state_in = storage_state
        self._cookies = cookies
        self.saved_to = None

    def new_page(self):
        return self._page

    def storage_state(self, path=None):
        self.saved_to = path
        state = {"cookies": [{"name": name} for name in self._cookies]}
        if path:
            Path(path).write_text(json.dumps(state))
        return state


class _FakeBrowser:
    def __init__(self, page, cookies=("remember_user_token",)):
        self._page = page
        self._cookies = cookies
        self.closed = False
        self.contexts: list[_FakeContext] = []

    def new_context(self, **kwargs):
        ctx = _FakeContext(self._page, cookies=self._cookies, **kwargs)
        self.contexts.append(ctx)
        return ctx

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser
        self.launch_kwargs: list[dict] = []

    def launch(self, **kwargs):
        self.launch_kwargs.append(kwargs)
        return self._browser


class _FakePlaywright:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)


class _FakeFactory:
    def __init__(self, page, cookies=("remember_user_token",)):
        self.browser = _FakeBrowser(page, cookies=cookies)
        self.pw = _FakePlaywright(self.browser)

    def __call__(self):
        return self

    def __enter__(self):
        return self.pw

    def __exit__(self, *exc):
        return False


def test_is_signed_in_predicate():
    assert _is_signed_in("https://app.thestorygraph.com/") is True
    assert _is_signed_in("https://app.thestorygraph.com/dashboard") is True
    assert _is_signed_in("https://app.thestorygraph.com/users/sign_in") is False


def test_login_saves_state_and_closes_browser(tmp_path):
    state = tmp_path / "state.json"
    page = _FakePage()
    factory = _FakeFactory(page)
    result = login(state, playwright_factory=factory, wait_for_user=lambda: None)
    assert result == state
    assert state.exists()
    assert factory.browser.closed is True
    assert factory.pw.chromium.launch_kwargs[0]["headless"] is False
    assert SIGN_IN_URL in page.goto_urls


def test_login_calls_wait_for_user(tmp_path):
    called = []
    login(
        tmp_path / "state.json",
        playwright_factory=_FakeFactory(_FakePage()),
        wait_for_user=lambda: called.append(True),
    )
    assert called == [True]


def test_login_secures_state_file_permissions(tmp_path):
    state = tmp_path / "state.json"
    login(state, playwright_factory=_FakeFactory(_FakePage()), wait_for_user=lambda: None)
    assert (state.stat().st_mode & 0o777) == 0o600


def test_login_raises_when_still_on_sign_in(tmp_path):
    page = _FakePage(url="https://app.thestorygraph.com/users/sign_in")
    factory = _FakeFactory(page)
    with pytest.raises(StorygraphAuthError, match="didn't complete"):
        login(tmp_path / "state.json", playwright_factory=factory, wait_for_user=lambda: None)
    assert factory.browser.closed is True


def test_login_raises_when_no_cookies(tmp_path):
    factory = _FakeFactory(_FakePage(), cookies=())
    with pytest.raises(StorygraphAuthError, match="didn't complete"):
        login(tmp_path / "state.json", playwright_factory=factory, wait_for_user=lambda: None)


def test_is_authenticated_false_when_no_state(tmp_path):
    missing = tmp_path / "missing.json"
    assert is_authenticated(missing, playwright_factory=_FakeFactory(_FakePage())) is False


def test_is_authenticated_true_for_valid_session(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}")
    page = _FakePage(url="https://app.thestorygraph.com/")
    assert is_authenticated(state, playwright_factory=_FakeFactory(page)) is True


def test_is_authenticated_false_when_redirected_to_sign_in(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}")
    page = _FakePage(url="https://app.thestorygraph.com/users/sign_in")
    factory = _FakeFactory(page)
    assert is_authenticated(state, playwright_factory=factory) is False
    assert factory.browser.closed is True


def test_is_authenticated_loads_storage_state_into_context(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}")
    factory = _FakeFactory(_FakePage())
    is_authenticated(state, playwright_factory=factory)
    assert factory.browser.contexts[0].storage_state_in == str(state)


def test_storygraph_browser_opens_page_and_closes(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}")
    page = _FakePage()
    factory = _FakeFactory(page)
    with StorygraphBrowser(state_path=state, playwright_factory=factory) as browser:
        assert browser.page is page
    assert factory.browser.closed is True


def test_load_sync_playwright_returns_callable():
    assert callable(session._load_sync_playwright())


def test_load_sync_playwright_missing_raises_dependency_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(StorygraphDependencyError, match="playwright install chromium"):
        session._load_sync_playwright()
