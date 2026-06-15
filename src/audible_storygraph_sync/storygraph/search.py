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
from .session import BASE_URL, PlaywrightFactory, _load_sync_playwright

SEARCH_URL = f"{BASE_URL}/browse"
RESULT_SELECTOR = ".book-pane"
RESULT_TIMEOUT_MS = 8000
_BOOK_ID_RE = re.compile(r"/books/([^/?#]+)")

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


def _candidates_from_records(records: list[dict], max_results: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    for record in records:
        book_id = parse_book_id(record.get("href", ""))
        if not book_id or book_id == "new":
            continue
        candidates.append(
            Candidate(
                book_id=book_id,
                title=(record.get("title") or "").strip(),
                author=(record.get("author") or "").strip(),
            )
        )
        if len(candidates) >= max_results:
            break
    return candidates


def _extract_records(page) -> list[dict]:
    return [el.evaluate(_EXTRACT_JS) for el in page.query_selector_all(RESULT_SELECTOR)]


def _search_url(query: str) -> str:
    return f"{SEARCH_URL}?search_term={quote_plus(query)}"


class StorygraphSearcher:
    """Reusable authenticated browser session for running many searches."""

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        playwright_factory: PlaywrightFactory | None = None,
        headless: bool = True,
        max_results: int = 10,
    ):
        self._state_path = state_path or storygraph_state_path()
        self._factory = playwright_factory or _load_sync_playwright()
        self._headless = headless
        self._max_results = max_results
        self._pw_cm = None
        self._browser = None
        self._page = None

    def __enter__(self) -> StorygraphSearcher:
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

    def search(self, query: str) -> list[Candidate]:
        self._page.goto(_search_url(query), wait_until="domcontentloaded")
        try:
            self._page.wait_for_selector(RESULT_SELECTOR, timeout=RESULT_TIMEOUT_MS)
        except Exception:
            return []
        return _candidates_from_records(_extract_records(self._page), self._max_results)


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
