"""StoryGraph write client (mark as read + finish date).

NOTE: every selector below is PROVISIONAL and must be verified against the live
StoryGraph DOM with an authenticated session before any real run is trusted. The
flow mirrors the old good_audible_story_sync tool (book page -> mark read ->
read-date form), translated to Playwright. The pure ``date_fields`` helper and the
control-presence logic are unit-tested; only the selector strings are speculative.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..config import storygraph_state_path
from .session import BASE_URL, PlaywrightFactory, _load_sync_playwright

READ_STATUS_SELECTOR = ".read-status-label, [data-test-id='read-status']"
MARK_READ_SELECTOR = (
    "[data-test-id='mark-as-read'], button:has-text('Mark as read'), a:has-text('Mark as read')"
)
EDIT_DATE_SELECTOR = (
    "[data-test-id='edit-read-date'], a:has-text('read date'), button:has-text('read date')"
)
DATE_DAY_SELECTOR = "select[name*='[day]'], input[name*='[day]']"
DATE_MONTH_SELECTOR = "select[name*='[month]'], input[name*='[month]']"
DATE_YEAR_SELECTOR = "select[name*='[year]'], input[name*='[year]']"
SAVE_DATE_SELECTOR = (
    "form[action*='/read_instances'] button[type='submit'], "
    "form[action*='/read_instances'] input[type='submit']"
)


def date_fields(value: date) -> dict[str, str]:
    return {"day": str(value.day), "month": str(value.month), "year": str(value.year)}


def _set_value(element, value: str) -> None:
    try:
        element.select_option(value)
    except Exception:
        element.fill(value)


class StorygraphClient:
    """Reusable authenticated browser session for marking books finished."""

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
        self._page = None

    def __enter__(self) -> StorygraphClient:
        self._pw_cm = self._factory()
        pw = self._pw_cm.__enter__()
        self._browser = pw.chromium.launch(headless=self._headless)
        context = self._browser.new_context(storage_state=str(self._state_path))
        self._page = context.new_page()
        return self

    def __exit__(self, *exc) -> bool:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._pw_cm is not None:
                self._pw_cm.__exit__(*exc)
        return False

    def mark_finished(self, book_id: str, finish_date: date | None = None) -> bool:
        self._page.goto(f"{BASE_URL}/books/{book_id}")
        if not self._ensure_marked_read():
            return False
        if finish_date is None:
            return True
        return self._set_finish_date(finish_date)

    def _ensure_marked_read(self) -> bool:
        page = self._page
        if page.query_selector(READ_STATUS_SELECTOR) is not None:
            return True
        control = page.query_selector(MARK_READ_SELECTOR)
        if control is None:
            return False
        control.click()
        return True

    def _set_finish_date(self, value: date) -> bool:
        page = self._page
        editor = page.query_selector(EDIT_DATE_SELECTOR)
        if editor is None:
            return False
        editor.click()

        fields = date_fields(value)
        for selector, field_value in (
            (DATE_YEAR_SELECTOR, fields["year"]),
            (DATE_MONTH_SELECTOR, fields["month"]),
            (DATE_DAY_SELECTOR, fields["day"]),
        ):
            element = page.query_selector(selector)
            if element is None:
                return False
            _set_value(element, field_value)

        save = page.query_selector(SAVE_DATE_SELECTOR)
        if save is None:
            return False
        save.click()
        return True
