"""StoryGraph write client (route a book to a shelf + finish date).

Selectors/flow verified against the live StoryGraph DOM (2026-06-15):
- a book page exposes ``.read-status-label`` (current status text) and one Rails form
  per status, ``form[action*='/update-status'][action*='status=<slug>']``, to set that
  status (``read`` was the one originally mapped; the other shelves reuse the same form
  shape with their own status slug);
- the finish date is added on ``/read_instances/new?book_id={id}`` via selects
  ``new_read_instance[day|month|year]`` (numeric values) submitted to POST
  ``/read_instances``. That page also lists every existing read with a remove-reread link
  (``a[href*='remove-reread']``), which is the reliable 'already read' signal — the
  book-page ``.read-status-label`` is not consistently rendered, so a ``read`` write is
  guarded on read-instance presence, not the label.

The pure helpers (``date_fields``, ``status_form_selector``, ``expected_label``) are
unit-tested; the browser flow is covered with a mocked page. The non-``read`` shelf forms
share the ``read`` form's structure but have not been exercised against a live
authenticated session — see the PR's manual verification note.
"""

from __future__ import annotations

import contextlib
from datetime import date
from pathlib import Path

from ..config import storygraph_state_path
from ..models import Shelf
from .session import BASE_URL, PlaywrightFactory, _load_sync_playwright, raise_if_signed_out

READ_STATUS_LABEL_SELECTOR = ".read-status-label"


def status_form_selector(status: Shelf | str) -> str:
    """The update-status form selector for a shelf, parameterised on its StoryGraph slug.

    Mirrors the verified ``read`` form; the slug is StoryGraph's own status value (``Shelf`` is
    a ``StrEnum``, so ``str`` is the slug), so it drops straight into the ``status=`` action
    segment for every shelf."""
    return f"form[action*='/update-status'][action*='status={status}']"


# Kept for back-compat with code/tests that import the read form selector by name.
STATUS_READ_FORM_SELECTOR = status_form_selector(Shelf.READ)


def expected_label(status: Shelf | str) -> str:
    """The ``.read-status-label`` text StoryGraph shows once a shelf is set (lower-cased).

    StoryGraph renders the status with a space, not the hyphenated slug ("to read", not
    "to-read"), so the post-write confirmation compares against this rather than the slug."""
    return str(status).replace("-", " ")


READ_INSTANCE_NEW_PATH = "/read_instances/new"
REMOVE_READ_SELECTOR = "a[href*='remove-reread']"
DATE_DAY_SELECTOR = "select[name='new_read_instance[day]']"
DATE_MONTH_SELECTOR = "select[name='new_read_instance[month]']"
DATE_YEAR_SELECTOR = "select[name='new_read_instance[year]']"
SUBMIT_INSTANCE_SELECTOR = (
    "form[action$='/read_instances'] button[type='submit'], "
    "form[action$='/read_instances'] input[type='submit']"
)
REVIEW_NEW_PATH = "/reviews/new"
STARS_INTEGER_SELECTOR = "select[name='stars_integer']"
STARS_DECIMAL_SELECTOR = "select[name='stars_decimal']"
EXPLANATION_SELECTOR = "input[name='review[explanation]']"
REVIEW_SUBMIT_SELECTOR = (
    "form[action='/reviews'] button[type='submit'], form[action='/reviews'] input[type='submit']"
)
SETTLE_MS = 1200


def date_fields(value: date) -> dict[str, str]:
    return {"day": str(value.day), "month": str(value.month), "year": str(value.year)}


class StorygraphClient:
    """Reusable authenticated browser session for routing books to StoryGraph shelves."""

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

    def _settle(self) -> None:
        # Let the form POST + redirect finish before we re-read state, without a
        # blind fixed sleep: returns as soon as the network is idle, capped at SETTLE_MS.
        with contextlib.suppress(Exception):
            self._page.wait_for_load_state("networkidle", timeout=SETTLE_MS)

    def current_status(self, book_id: str) -> str | None:
        page = self._page
        page.goto(f"{BASE_URL}/books/{book_id}", wait_until="domcontentloaded")
        raise_if_signed_out(page.url)
        label = page.query_selector(READ_STATUS_LABEL_SELECTOR)
        return label.inner_text().strip().lower() if label else None

    def mark_shelf(self, book_id: str, status: Shelf | str, date: date | None = None) -> bool:
        """Route a book to a StoryGraph shelf, idempotently.

        ``read`` is recorded as a *read instance*, so it is guarded on the read-instance
        list rather than the book-page status label: the label is not reliably rendered, so
        trusting it lets a re-mark (e.g. when a volatile source finish date shifts, or two
        source keys resolve to the same book) append a duplicate read. A book that already
        has any read instance is left untouched; only a never-read book gets one — dated via
        the dated-read flow (which both records the read and sets the status), or dateless via
        the status form when the source reports no finish date.

        Every other shelf carries no read instance, so it is routed by the status form and
        confirmed via the status label. Returns True only once the write is confirmed; a
        silently-rejected submit reports False so it is never recorded as synced (and is
        retried on the next run)."""
        target = status if isinstance(status, Shelf) else Shelf(status)
        if target is Shelf.READ:
            if self.has_read_instance(book_id):
                return True
            if date is not None:
                submitted = self._add_dated_read(book_id, date)
            else:
                submitted = self._mark_read_undated(book_id)
            if not submitted:
                return False
            return self.has_read_instance(book_id)

        want = expected_label(target)
        if self.current_status(book_id) == want:
            return True
        if not self._set_status(target):
            return False
        # Confirm the write actually landed; a silently-rejected submit must not be
        # recorded as synced (it would be skipped forever on idempotent re-runs).
        return self.current_status(book_id) == want

    def has_read_instance(self, book_id: str) -> bool:
        """Whether StoryGraph already records at least one read for this book.

        The read-instance page lists every read with a remove-reread link; the presence of
        any is the reliable 'already read' signal (the book-page status label is not
        consistently rendered, so it cannot be trusted to prevent a duplicate read)."""
        page = self._page
        page.goto(
            f"{BASE_URL}{READ_INSTANCE_NEW_PATH}?book_id={book_id}",
            wait_until="domcontentloaded",
        )
        raise_if_signed_out(page.url)
        return page.query_selector(REMOVE_READ_SELECTOR) is not None

    def _mark_read_undated(self, book_id: str) -> bool:
        """Mark a book read with no finish date via the book-page status form (used only when
        the source reports the book finished but exposes no date)."""
        page = self._page
        page.goto(f"{BASE_URL}/books/{book_id}", wait_until="domcontentloaded")
        raise_if_signed_out(page.url)
        return self._set_status(Shelf.READ)

    def mark_finished(self, book_id: str, finish_date: date | None = None) -> bool:
        """Mark a book read. Thin alias over ``mark_shelf`` kept for the read-only callers
        (``run_title_sync``, the collections flow) that only ever route to ``read``."""
        return self.mark_shelf(book_id, Shelf.READ, finish_date)

    def _set_status(self, status: Shelf) -> bool:
        form = self._page.query_selector(status_form_selector(status))
        if form is None:
            return False
        form.evaluate("f => f.requestSubmit()")
        self._settle()
        return True

    def _add_dated_read(self, book_id: str, value: date) -> bool:
        page = self._page
        page.goto(
            f"{BASE_URL}{READ_INSTANCE_NEW_PATH}?book_id={book_id}",
            wait_until="domcontentloaded",
        )
        raise_if_signed_out(page.url)
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
        self._settle()
        return True

    def write_review(
        self,
        book_id: str,
        *,
        stars_integer: str = "",
        stars_decimal: str = "",
        explanation: str = "",
    ) -> str:
        """Create a rating/review on StoryGraph. Returns 'written', 'skipped' (a review
        already exists, so /reviews/new redirects away and the form is absent), or
        'failed'. ``review[explanation]`` is a hidden Trix input set directly."""
        page = self._page
        page.goto(f"{BASE_URL}{REVIEW_NEW_PATH}?book_id={book_id}", wait_until="domcontentloaded")
        raise_if_signed_out(page.url)
        stars = page.query_selector(STARS_INTEGER_SELECTOR)
        if stars is None:
            return "skipped"
        if stars_integer:
            stars.select_option(stars_integer)
            decimal = page.query_selector(STARS_DECIMAL_SELECTOR)
            if decimal is not None:
                decimal.select_option(stars_decimal or "")
        if explanation:
            field = page.query_selector(EXPLANATION_SELECTOR)
            if field is not None:
                field.evaluate("(node, value) => { node.value = value; }", explanation)
        submit = page.query_selector(REVIEW_SUBMIT_SELECTOR)
        if submit is None:
            return "failed"
        submit.click()
        self._settle()
        # Confirm the review now exists; otherwise the submit was silently rejected
        # and must not be recorded as rated (it would never be retried).
        return "written" if self._review_recorded(book_id) else "failed"

    def _review_recorded(self, book_id: str) -> bool:
        page = self._page
        page.goto(f"{BASE_URL}{REVIEW_NEW_PATH}?book_id={book_id}", wait_until="domcontentloaded")
        raise_if_signed_out(page.url)
        # A saved review makes /reviews/new redirect away, so the rating form is gone.
        return page.query_selector(STARS_INTEGER_SELECTOR) is None
