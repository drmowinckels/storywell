from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..models import Audiobook
from .matching import Candidate, MatchResult, MatchStatus, match_book

SearchFn = Callable[[str], list[Candidate]]


@dataclass(frozen=True)
class SyncPlanItem:
    book: Audiobook
    result: MatchResult


def query_for(book: Audiobook) -> str:
    author = book.authors[0] if book.authors else ""
    return f"{book.title} {author}".strip()


def plan_sync(books: Iterable[Audiobook], search_fn: SearchFn) -> list[SyncPlanItem]:
    items: list[SyncPlanItem] = []
    for book in books:
        candidates = search_fn(query_for(book))
        author = book.authors[0] if book.authors else ""
        result = match_book(book.title, author, candidates)
        items.append(SyncPlanItem(book, result))
    return items


def summarize(items: Iterable[SyncPlanItem]) -> dict[MatchStatus, int]:
    counts = {status: 0 for status in MatchStatus}
    for item in items:
        counts[item.result.status] += 1
    return counts
