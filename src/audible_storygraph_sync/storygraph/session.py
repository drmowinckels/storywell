from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import ensure_config_dir, storygraph_state_path

BASE_URL = "https://app.thestorygraph.com"
SIGN_IN_URL = f"{BASE_URL}/users/sign_in"
SIGN_IN_PATH = "/users/sign_in"
PROFILE_LINK_SELECTOR = "a[href*='/profile/']"
LOGIN_TIMEOUT_MS = 5 * 60 * 1000

PlaywrightFactory = Callable[[], Any]


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


def _is_signed_in(url: str, has_profile_link: bool) -> bool:
    return has_profile_link and SIGN_IN_PATH not in url


def _secure_file(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def login(
    state_path: Path | None = None,
    *,
    playwright_factory: PlaywrightFactory | None = None,
    timeout_ms: int = LOGIN_TIMEOUT_MS,
) -> Path:
    """Open a headed browser, wait for a manual StoryGraph login, persist the session.

    The user logs in by hand (handling 2FA and Cloudflare); we never touch their
    password. On success the storage state (cookies) is written to ``state_path``.
    """
    state_path = state_path or storygraph_state_path()
    ensure_config_dir()
    factory = playwright_factory or _load_sync_playwright()

    with factory() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(SIGN_IN_URL)
            try:
                page.wait_for_selector(PROFILE_LINK_SELECTOR, timeout=timeout_ms)
            except Exception as err:
                raise StorygraphAuthError(
                    "Timed out waiting for a StoryGraph sign-in. Re-run "
                    "`storygraph-login` and complete the login in the browser window."
                ) from err
            context.storage_state(path=str(state_path))
        finally:
            browser.close()

    _secure_file(state_path)
    return state_path


def is_authenticated(
    state_path: Path | None = None,
    *,
    playwright_factory: PlaywrightFactory | None = None,
    headless: bool = True,
) -> bool:
    """Return True if the saved session still has an authenticated StoryGraph login."""
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
            has_profile = page.query_selector(PROFILE_LINK_SELECTOR) is not None
            return _is_signed_in(page.url, has_profile)
        finally:
            browser.close()
