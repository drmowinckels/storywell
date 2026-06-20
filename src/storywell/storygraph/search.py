"""StoryGraph search.

Selectors below were verified against the live StoryGraph ``/browse`` DOM
(2026-06-15): each result is a ``.book-pane`` holding ``h3 a[href*='/books/']``
(title) and ``a[href*='/authors/']`` (author). The pure helpers
(``parse_book_id``, ``_candidates_from_records``) are unit-tested.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote_plus

from ..config import storygraph_state_path
from .editions import Edition, parse_editions, pick_edition, sg_formats_for
from .matching import Candidate
from .session import BASE_URL, PlaywrightFactory, _load_sync_playwright, raise_if_signed_out

SEARCH_URL = f"{BASE_URL}/browse"
RESULT_SELECTOR = ".book-pane"
DESCRIPTION_SELECTOR = ".trix-content"
RESULT_TIMEOUT_MS = 8000
_BOOK_ID_RE = re.compile(r"/books/([^/?#]+)")

# Editions live at /books/{id}/editions, paginated ?page=N. Each .book-pane links to
# its own /books/{edition_id} and states "Format: Audio|Paperback|Hardcover|Digital".
# Verified against the live editions DOM (2026-06-15).
EDITIONS_PANE_SELECTOR = ".browse-editions .book-pane"
EDITIONS_MAX_PAGES = 3
# StoryGraph stamps "You've read another edition" on every edition pane except the one the
# reader actually read; its presence means the work is already read on a *different*
# edition, so marking our target edition would add a second read to the same work.
READ_ANOTHER_EDITION_SELECTOR = "text=read another edition"

_EXPAND_SHOW_MORE_JS = """
() => {
  const el = [...document.querySelectorAll('a,button,span,div')]
    .find(e => /^\\s*show more\\s*$/i.test(e.textContent || ''));
  if (el) el.click();
}
"""
_READ_DESCRIPTION_JS = """
() => {
  const els = [...document.querySelectorAll('.trix-content')];
  return els.map(e => e.innerText || '').sort((a, b) => b.length - a.length)[0] || '';
}
"""

_EXTRACT_JS = """
(el) => {
  const titleLink = el.querySelector("h3 a[href*='/books/']")
    || el.querySelector("a[href*='/books/']");
  const authorLink = el.querySelector("a[href*='/authors/']");
  return {
    href: titleLink ? titleLink.getAttribute('href') : '',
    title: titleLink ? (titleLink.textContent || '').trim() : '',
    author: authorLink ? (authorLink.textContent || '').trim() : '',
  };
}
"""

_EDITION_EXTRACT_JS = r"""
(el) => {
  const id = [...el.querySelectorAll("a[href*='/books/']")]
    .map(a => (a.getAttribute('href') || '').match(/\/books\/([0-9a-f-]{36})/))
    .filter(Boolean).map(m => m[1])[0] || '';
  const info = el.querySelector('.edition-info');
  const text = (info ? info.innerText : el.innerText) || '';
  const m = text.match(/Format:\s*([A-Za-z]+)/);
  return {id, format: m ? m[1] : ''};
}
"""


def parse_book_id(href: str) -> str | None:
    match = _BOOK_ID_RE.search(href or "")
    return match.group(1) if match else None


def _record_to_candidate(record: dict) -> Candidate | None:
    book_id = parse_book_id(record.get("href", ""))
    if not book_id or book_id == "new":
        return None
    return Candidate(
        book_id=book_id,
        title=(record.get("title") or "").strip(),
        author=(record.get("author") or "").strip(),
    )


def _candidates_from_records(records: list[dict], max_results: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    for record in records:
        candidate = _record_to_candidate(record)
        if candidate is None:
            continue
        candidates.append(candidate)
        if len(candidates) >= max_results:
            break
    return candidates


def _extract_records(page, selector: str, js: str) -> list[dict]:
    return [el.evaluate(js) for el in page.query_selector_all(selector)]


def _search_url(query: str) -> str:
    return f"{SEARCH_URL}?search_term={quote_plus(query)}"


class StorygraphSearcher:
    """Reusable authenticated browser session for running many searches."""

    def __init__(
        self,
        *,
        page=None,
        state_path: Path | None = None,
        playwright_factory: PlaywrightFactory | None = None,
        headless: bool = True,
        max_results: int = 10,
    ):
        self._external_page = page
        self._state_path = state_path or storygraph_state_path()
        self._factory = playwright_factory or _load_sync_playwright()
        self._headless = headless
        self._max_results = max_results
        self._pw_cm = None
        self._browser = None
        self._page = page

    def __enter__(self) -> StorygraphSearcher:
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

    def search(self, query: str) -> list[Candidate]:
        self._page.goto(_search_url(query), wait_until="domcontentloaded")
        raise_if_signed_out(self._page.url)
        try:
            self._page.wait_for_selector(RESULT_SELECTOR, timeout=RESULT_TIMEOUT_MS)
        except Exception:
            return []  # selector never appeared: genuinely no results (session checked above)
        candidates: list[Candidate] = []
        for element in self._page.query_selector_all(RESULT_SELECTOR):
            candidate = _record_to_candidate(element.evaluate(_EXTRACT_JS))
            if candidate is None:
                continue
            candidates.append(candidate)
            if len(candidates) >= self._max_results:
                break
        return candidates

    def fetch_description(self, book_id: str) -> str:
        self._page.goto(f"{BASE_URL}/books/{book_id}", wait_until="domcontentloaded")
        raise_if_signed_out(self._page.url)
        try:
            self._page.wait_for_selector(DESCRIPTION_SELECTOR, timeout=RESULT_TIMEOUT_MS)
        except Exception:
            return ""
        self._page.evaluate(_EXPAND_SHOW_MORE_JS)
        self._page.wait_for_timeout(300)
        return self._page.evaluate(_READ_DESCRIPTION_JS)

    def _edition_pages(self, book_id: str, max_pages: int):
        """Yield each ``/books/{id}/editions`` page as an ``Edition`` list, in document
        order, stopping at the last page (no next-page link) or a page with no panes."""
        for page_num in range(1, max_pages + 1):
            self._page.goto(
                f"{BASE_URL}/books/{book_id}/editions?page={page_num}",
                wait_until="domcontentloaded",
            )
            try:
                self._page.wait_for_selector(EDITIONS_PANE_SELECTOR, timeout=RESULT_TIMEOUT_MS)
            except Exception:
                return
            yield parse_editions(
                _extract_records(self._page, EDITIONS_PANE_SELECTOR, _EDITION_EXTRACT_JS)
            )
            if self._page.query_selector(f"a[href*='editions?page={page_num + 1}']") is None:
                return

    def resolve_edition(
        self, book_id: str, media_format: str, *, max_pages: int = EDITIONS_MAX_PAGES
    ) -> str | None:
        """Return the ``book_id`` of the edition matching ``media_format`` (e.g. the
        audiobook edition), or None when the work has no such edition or the format is
        unknown. Pages through ``/books/{id}/editions`` up to ``max_pages`` and stops at
        the first match, so the matched edition itself counts when it already fits.

        Edition tagging is a best-effort enhancement: any scrape failure returns None so
        the caller falls back to the matched edition rather than aborting the whole sync."""
        if not sg_formats_for(media_format):
            return None
        try:
            for editions in self._edition_pages(book_id, max_pages):
                chosen = pick_edition(editions, media_format)
                if chosen is not None:
                    return chosen
        except Exception:
            return None
        return None

    def read_on_another_edition(self, book_id: str) -> bool:
        """Whether the reader already has a read on some *other* edition of this work.

        StoryGraph stamps "You've read another edition" on the editions page for every
        edition the reader has not read once they've read any one of them, so its presence
        means the work is already read elsewhere and our target edition should be left
        unmarked (marking it would record a second read on the same work). The marker shows
        on the first editions page whenever the work has more than one edition, so a single
        page load suffices. Best-effort: any scrape failure returns False, so a never-read
        book is still marked (a rare missed dedup beats silently skipping a wanted read)."""
        try:
            self._page.goto(f"{BASE_URL}/books/{book_id}/editions", wait_until="domcontentloaded")
            raise_if_signed_out(self._page.url)
            try:
                self._page.wait_for_selector(EDITIONS_PANE_SELECTOR, timeout=RESULT_TIMEOUT_MS)
            except Exception:
                return False
            return self._page.query_selector(READ_ANOTHER_EDITION_SELECTOR) is not None
        except Exception:
            return False

    def list_editions(self, book_id: str, *, max_pages: int = EDITIONS_MAX_PAGES) -> list[Edition]:
        """All editions of a work (paged, document order, deduped by id). Best-effort:
        returns whatever was collected before any scrape failure. Used by the read-only
        retag report to inspect which edition a book is currently marked on."""
        seen: set[str] = set()
        editions: list[Edition] = []
        try:
            for page in self._edition_pages(book_id, max_pages):
                for edition in page:
                    if edition.book_id not in seen:
                        seen.add(edition.book_id)
                        editions.append(edition)
        except Exception:
            return editions
        return editions


def search_books(
    query: str,
    *,
    state_path: Path | None = None,
    playwright_factory: PlaywrightFactory | None = None,
    headless: bool = True,
    max_results: int = 10,
) -> list[Candidate]:
    with StorygraphSearcher(
        state_path=state_path,
        playwright_factory=playwright_factory,
        headless=headless,
        max_results=max_results,
    ) as searcher:
        return searcher.search(query)
