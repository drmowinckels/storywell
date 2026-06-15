"""StoryGraph write client (mark as read + finish date).

Selectors/flow verified against the live StoryGraph DOM (2026-06-15):
- a book page exposes ``.read-status-label`` (current status text) and a Rails form
  ``form[action*='/update-status'][action*='status=read']`` to set status to read;
- the finish date is added on ``/read_instances/new?book_id={id}`` via selects
  ``new_read_instance[day|month|year]`` (numeric values) submitted to POST
  ``/read_instances``.

The pure ``date_fields`` helper is unit-tested; the browser flow is covered with a
mocked page.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..config import storygraph_state_path
from .session import BASE_URL, PlaywrightFactory, _load_sync_playwright

READ_STATUS_LABEL_SELECTOR = ".read-status-label"
STATUS_READ_FORM_SELECTOR = "form[action*='/update-status'][action*='status=read']"
READ_INSTANCE_NEW_PATH = "/read_instances/new"
DATE_DAY_SELECTOR = "select[name='new_read_instance[day]']"
DATE_MONTH_SELECTOR = "select[name='new_read_instance[month]']"
DATE_YEAR_SELECTOR = "select[name='new_read_instance[year]']"
SUBMIT_INSTANCE_SELECTOR = (
    "form[action$='/read_instances'] button[type='submit'], "
    "form[action$='/read_instances'] input[type='submit']"
)
SETTLE_MS = 1200


def date_fields(value: date) -> dict[str, str]:
    return {"day": str(value.day), "month": str(value.month), "year": str(value.year)}


class StorygraphClient:
    """Reusable authenticated browser session for marking books finished."""

    def __init__(
        self,
        *,
        page=None,
        state_path: Path | None = None,
        playwright_factory: PlaywrightFactory | None = None,
        headless: bool = True,
    ):
        self._external_page = page
        self._state_path = state_path or storygraph_state_path()
        self._factory = playwright_factory or _load_sync_playwright()
        self._headless = headless
        self._pw_cm = None
        self._browser = None
        self._page = page

    def __enter__(self) -> StorygraphClient:
        if self._external_page is not None:
            self._page = self._external_page
            return self
        self._pw_cm = self._factory()
        pw = self._pw_cm.__enter__()
        self._browser = pw.chromium.launch(headless=self._headless)
        context = self._browser.new_context(storage_state=str(self._state_path))
        self._page = context.new_page()
        return self

    def __exit__(self, *exc) -> bool:
        if self._external_page is not None:
            return False
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._pw_cm is not None:
                self._pw_cm.__exit__(*exc)
        return False

    def current_status(self, book_id: str) -> str | None:
        page = self._page
        page.goto(f"{BASE_URL}/books/{book_id}", wait_until="domcontentloaded")
        label = page.query_selector(READ_STATUS_LABEL_SELECTOR)
        return label.inner_text().strip().lower() if label else None

    def mark_finished(self, book_id: str, finish_date: date | None = None) -> bool:
        if self.current_status(book_id) == "read":
            return True
        # A dated read instance both records the read and sets status to "read",
        # so when we have a date we add only that (one instance, correct date).
        # Setting status separately would auto-create a second instance dated today.
        if finish_date is not None:
            return self._add_dated_read(book_id, finish_date)
        return self._set_status_read()

    def _set_status_read(self) -> bool:
        form = self._page.query_selector(STATUS_READ_FORM_SELECTOR)
        if form is None:
            return False
        form.evaluate("f => f.requestSubmit()")
        self._page.wait_for_timeout(SETTLE_MS)
        return True

    def _add_dated_read(self, book_id: str, value: date) -> bool:
        page = self._page
        page.goto(
            f"{BASE_URL}{READ_INSTANCE_NEW_PATH}?book_id={book_id}",
            wait_until="domcontentloaded",
        )
        fields = date_fields(value)
        for selector, field_value in (
            (DATE_YEAR_SELECTOR, fields["year"]),
            (DATE_MONTH_SELECTOR, fields["month"]),
            (DATE_DAY_SELECTOR, fields["day"]),
        ):
            element = page.query_selector(selector)
            if element is None:
                return False
            element.select_option(field_value)

        submit = page.query_selector(SUBMIT_INSTANCE_SELECTOR)
        if submit is None:
            return False
        submit.click()
        page.wait_for_timeout(SETTLE_MS)
        return True
