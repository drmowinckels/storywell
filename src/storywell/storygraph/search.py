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
from .matching import Candidate
from .session import BASE_URL, PlaywrightFactory, _load_sync_playwright, raise_if_signed_out

SEARCH_URL = f"{BASE_URL}/browse"
RESULT_SELECTOR = ".book-pane"
DESCRIPTION_SELECTOR = ".trix-content"
RESULT_TIMEOUT_MS = 8000
_BOOK_ID_RE = re.compile(r"/books/([^/?#]+)")

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
