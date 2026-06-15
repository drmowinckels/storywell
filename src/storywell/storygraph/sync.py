from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from ..models import SourceBook
from .matching import Candidate, MatchResult, MatchStatus, match_book, search_title
from .store import SyncStore

SearchFn = Callable[[str], list[Candidate]]
ConfirmFn = Callable[[SourceBook, MatchResult], "Candidate | None"]


class Writer(Protocol):
    def mark_finished(self, book_id: str, finish_date: date | None = None) -> bool: ...


@dataclass(frozen=True)
class SyncPlanItem:
    book: SourceBook
    result: MatchResult


@dataclass
class SyncOutcome:
    written: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    skipped_synced: list[str] = field(default_factory=list)
    no_match: list[str] = field(default_factory=list)
    ambiguous_skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def query_for(book: SourceBook) -> str:
    author = book.authors[0] if book.authors else ""
    return f"{search_title(book.title)} {author}".strip()


def plan_sync(books: Iterable[SourceBook], search_fn: SearchFn) -> list[SyncPlanItem]:
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


def _finish_date(book: SourceBook) -> date | None:
    return book.finished_at.date() if book.finished_at else None


def resolve_match(
    book: SourceBook, result: MatchResult, confirm_fn: ConfirmFn | None
) -> Candidate | None:
    if result.status is MatchStatus.MATCH and result.best is not None:
        return result.best.candidate
    if result.status is MatchStatus.AMBIGUOUS and confirm_fn is not None:
        return confirm_fn(book, result)
    return None


def run_sync(
    books: Iterable[SourceBook],
    *,
    search_fn: SearchFn,
    writer: Writer,
    store: SyncStore,
    confirm_fn: ConfirmFn | None = None,
    dry_run: bool = False,
) -> SyncOutcome:
    outcome = SyncOutcome()
    for book in books:
        finished_on = _finish_date(book)
        if store.is_synced(book.key, finished_on):
            outcome.skipped_synced.append(book.key)
            continue

        book_id = store.cached_book_id(book.key)
        if book_id is None:
            author = book.authors[0] if book.authors else ""
            result = match_book(book.title, author, search_fn(query_for(book)))
            chosen = resolve_match(book, result, confirm_fn)
            if chosen is None:
                if result.status is MatchStatus.NO_MATCH:
                    outcome.no_match.append(book.key)
                else:
                    outcome.ambiguous_skipped.append(book.key)
                continue
            book_id = chosen.book_id
            store.remember_match(book.key, book_id)

        if dry_run:
            outcome.planned.append(book.key)
            continue

        if writer.mark_finished(book_id, finished_on):
            store.record(book.key, book_id, finished_on)
            outcome.written.append(book.key)
        else:
            outcome.failed.append(book.key)

    return outcome
