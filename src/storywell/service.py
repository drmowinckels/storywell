"""Framework-agnostic engine API shared by the CLI and the desktop GUI.

These functions wrap the source and StoryGraph modules with no dependency on Typer,
Rich, or pywebview: they take plain arguments and return data or raise domain errors,
so any front end (terminal, desktop window, future web UI) can drive the same engine.
The CLI converts the errors here into ``typer.Exit`` + console output; the GUI bridge
converts them into JSON-able results.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .models import Shelf, SourceBook

NO_SESSION_MESSAGE = "No active StoryGraph session. Run `storygraph-login`."


class NotAuthenticatedError(RuntimeError):
    """A StoryGraph operation needs an active session but none is logged in."""


def list_finished(
    source: str,
    *,
    threshold: float = 0.95,
    auth_file: Path | None = None,
    profile: str | None = None,
    path: Path | None = None,
    token: str | None = None,
    shelf: Shelf | None = None,
    read_date: bool = False,
    collections: tuple[str, ...] = (),
    read_column: str | None = None,
) -> list[SourceBook]:
    """Read a source's finished books. Raises ``SourceError``, never ``typer.Exit``."""
    from .sources import make_source

    src = make_source(
        source,
        auth_file=auth_file,
        profile=profile,
        path=path,
        token=token,
        shelf=shelf,
        read_date=read_date,
        collections=collections,
        read_column=read_column,
    )
    return src.finished_books(threshold=threshold)


def _default_browser_factory(headless: bool) -> Callable[[], Any]:
    def factory() -> Any:
        from .storygraph import StorygraphBrowser

        return StorygraphBrowser(headless=headless)

    return factory


@contextlib.contextmanager
def session_browser(
    *,
    headless: bool = True,
    browser_factory: Callable[[], Any] | None = None,
) -> Iterator[Any]:
    """Yield one authenticated StoryGraph browser, or raise ``NotAuthenticatedError``.

    Checks the session on the shared page rather than launching a throwaway browser
    first: Playwright's sync API forbids a second concurrent context. Constructing the
    browser may raise ``StorygraphDependencyError`` when Playwright is not installed;
    that propagates so the caller can report the install instructions.
    """
    from .storygraph import is_authenticated

    factory = browser_factory or _default_browser_factory(headless)
    with factory() as browser:
        if not is_authenticated(page=browser.page):
            raise NotAuthenticatedError(NO_SESSION_MESSAGE)
        yield browser


@contextlib.contextmanager
def _open_searcher(*, headless: bool = True) -> Iterator[Any]:
    from .storygraph.search import StorygraphSearcher

    with session_browser(headless=headless) as browser:
        searcher = StorygraphSearcher(page=browser.page)
        with searcher:
            yield searcher


def build_sync_plan(
    books: list[SourceBook],
    *,
    headless: bool = True,
    open_searcher: Callable[[], Any] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Any]:
    """Match each book against StoryGraph and return the match plan (no writes).

    Opens an authenticated session, searches per book, and returns ``SyncPlanItem``s.
    ``open_searcher`` injects an alternative searcher context manager for testing;
    ``on_progress`` receives ``(done, total)`` after each book for live UI feedback.
    """
    from .storygraph import plan_sync

    opener = open_searcher or (lambda: _open_searcher(headless=headless))
    with opener() as searcher:
        return plan_sync(books, searcher.search, on_progress=on_progress)


def summarize_plan(items: list[Any]) -> dict[str, int]:
    """Count a match plan by status, keyed by StoryGraph status slug (JSON-able)."""
    from .storygraph import summarize

    return {status.value: count for status, count in summarize(items).items()}


def login_state(*, headless: bool = True) -> bool:
    """Return whether a saved StoryGraph session is still authenticated."""
    from .storygraph import is_authenticated

    return is_authenticated(headless=headless)


def saved_logins() -> dict[str, bool]:
    """Return which services have a saved credential on disk — a cheap presence check.

    Instant and offline: it only tests whether the credential file exists, unlike
    :func:`login_state`, which launches a browser to confirm the StoryGraph session is
    still live. The UI uses this to reflect logged-in state without a startup browser hit.
    """
    from .config import audible_auth_path, storygraph_state_path

    return {
        "storygraph": storygraph_state_path().exists(),
        "audible": audible_auth_path().exists(),
    }


def forget_login(service_name: str) -> bool:
    """Delete the saved credential for ``service_name`` ('storygraph' or 'audible').

    Backs the UI's log-out / switch-account action. Idempotent — a missing file is a
    no-op. Raises ``ValueError`` for an unknown service so a UI typo surfaces loudly.
    """
    from .config import audible_auth_path, storygraph_state_path

    paths = {"storygraph": storygraph_state_path, "audible": audible_auth_path}
    if service_name not in paths:
        raise ValueError(f"unknown login: {service_name!r}")
    paths[service_name]().unlink(missing_ok=True)
    return True


def chromium_installed() -> bool:
    """Return whether Playwright's Chromium is already downloaded."""
    from .storygraph import chromium_installed as _chromium_installed

    return _chromium_installed()


def install_chromium() -> bool:
    """Download Playwright's Chromium. Idempotent; returns success."""
    from .storygraph import install_chromium as _install_chromium

    return _install_chromium()


def audible_login(marketplace: str) -> str:
    """Run an external Amazon login for ``marketplace`` and return the saved auth-file path."""
    from .sources.audible_auth import audible_login as _audible_login

    return str(_audible_login(marketplace))


def storygraph_login() -> str:
    """Log in to StoryGraph in a browser (no terminal) and return the saved session path."""
    from .storygraph import login, wait_until_signed_in

    return str(login(wait_for_user=wait_until_signed_in))
