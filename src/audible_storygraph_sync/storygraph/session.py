from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import ensure_config_dir, storygraph_state_path

BASE_URL = "https://app.thestorygraph.com"
SIGN_IN_URL = f"{BASE_URL}/users/sign_in"
SIGN_IN_PATH = "/users/sign_in"

PlaywrightFactory = Callable[[], Any]
WaitForUser = Callable[[], None]


class StorygraphDependencyError(RuntimeError):
    pass


class StorygraphAuthError(RuntimeError):
    pass


def _load_sync_playwright() -> PlaywrightFactory:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as err:
        raise StorygraphDependencyError(
            "Playwright is required for StoryGraph sync but is not installed.\n"
            "Install it with:\n"
            "  pipx inject audible-storygraph-sync playwright\n"
            "  (or: pip install 'audible-storygraph-sync[storygraph]')\n"
            "then download the browser:\n"
            "  playwright install chromium"
        ) from err
    return sync_playwright


def _is_signed_in(url: str) -> bool:
    return SIGN_IN_PATH not in url


def _secure_file(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _default_wait_for_user() -> None:
    input(
        "\nA browser window has opened on the StoryGraph sign-in page.\n"
        "Log in there (email/password, 2FA, Cloudflare), then return here and press Enter... "
    )


def login(
    state_path: Path | None = None,
    *,
    playwright_factory: PlaywrightFactory | None = None,
    wait_for_user: WaitForUser | None = None,
) -> Path:
    """Open a headed browser, wait for a manual StoryGraph login, persist the session.

    The user logs in by hand (handling 2FA and Cloudflare) and presses Enter when done;
    we never touch their password. The storage state (cookies) is written to ``state_path``.
    Detection is by Enter + a sign-in-redirect check, not by a fragile DOM selector.
    """
    state_path = state_path or storygraph_state_path()
    ensure_config_dir()
    factory = playwright_factory or _load_sync_playwright()
    wait = wait_for_user or _default_wait_for_user

    with factory() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(SIGN_IN_URL)
            wait()
            state = context.storage_state(path=str(state_path))
            final_url = page.url
        finally:
            browser.close()

    _secure_file(state_path)
    if SIGN_IN_PATH in final_url or not state.get("cookies"):
        raise StorygraphAuthError(
            "Login didn't complete — the browser is still on the sign-in page or no session "
            "cookies were saved. Re-run `storygraph-login` and finish logging in (wait for "
            "your StoryGraph home page to load) before pressing Enter."
        )
    return state_path


def is_authenticated(
    state_path: Path | None = None,
    *,
    playwright_factory: PlaywrightFactory | None = None,
    headless: bool = True,
) -> bool:
    """Return True if the saved session still has an authenticated StoryGraph login.

    Logged-out visits to the app root redirect to ``/users/sign_in``; staying anywhere
    else means the session is live.
    """
    state_path = state_path or storygraph_state_path()
    if not Path(state_path).exists():
        return False
    factory = playwright_factory or _load_sync_playwright()

    with factory() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            context = browser.new_context(storage_state=str(state_path))
            page = context.new_page()
            page.goto(BASE_URL)
            return _is_signed_in(page.url)
        finally:
            browser.close()


class StorygraphBrowser:
    """One authenticated Playwright page shared by search and write operations.

    Playwright's sync API forbids two concurrent ``sync_playwright()`` contexts, so
    search and marking must share a single browser/page rather than each owning one.
    """

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        playwright_factory: PlaywrightFactory | None = None,
        headless: bool = True,
    ):
        self._state_path = state_path or storygraph_state_path()
        self._factory = playwright_factory or _load_sync_playwright()
        self._headless = headless
        self._pw_cm = None
        self._browser = None
        self.page = None

    def __enter__(self) -> StorygraphBrowser:
        self._pw_cm = self._factory()
        pw = self._pw_cm.__enter__()
        self._browser = pw.chromium.launch(headless=self._headless)
        context = self._browser.new_context(storage_state=str(self._state_path))
        self.page = context.new_page()
        return self

    def __exit__(self, *exc) -> bool:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._pw_cm is not None:
                self._pw_cm.__exit__(*exc)
        return False
