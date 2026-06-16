from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from ..models import SourceBook
from .matching import Candidate, MatchResult, MatchStatus, match_book, search_title
from .reviews import compose_review, rating_to_stars
from .store import SyncStore

SearchFn = Callable[[str], list[Candidate]]
ConfirmFn = Callable[[Any, MatchResult], "Candidate | None"]
EditionFn = Callable[[str, str], "str | None"]


class Writer(Protocol):
    def mark_finished(self, book_id: str, finish_date: date | None = None) -> bool: ...


class Rater(Protocol):
    def write_review(
        self, book_id: str, *, stars_integer: str, stars_decimal: str, explanation: str
    ) -> str: ...


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


def _effective_book_id(book_id: str, media_format: str, edition_fn: EditionFn | None) -> str:
    """Re-point a matched book to its format-specific edition (e.g. the audiobook one).

    Falls back to the matched ``book_id`` when no edition_fn is wired, the source has no
    known format, or the work has no edition in that format (best-match fallback)."""
    if edition_fn is None or not media_format:
        return book_id
    edition_id = edition_fn(book_id, media_format)
    return edition_id if edition_id is not None else book_id


def resolve_match(item: Any, result: MatchResult, confirm_fn: ConfirmFn | None) -> Candidate | None:
    if result.status is MatchStatus.MATCH and result.best is not None:
        return result.best.candidate
    if result.status is MatchStatus.AMBIGUOUS and confirm_fn is not None:
        return confirm_fn(item, result)
    return None


def run_sync(
    books: Iterable[SourceBook],
    *,
    search_fn: SearchFn,
    writer: Writer,
    store: SyncStore,
    confirm_fn: ConfirmFn | None = None,
    edition_fn: EditionFn | None = None,
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
            book_id = _effective_book_id(chosen.book_id, book.media_format, edition_fn)
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


@dataclass(frozen=True)
class TitleEntry:
    key: str
    title: str
    finish_date: date | None = None
    author: str = ""
    media_format: str = ""


def run_title_sync(
    entries: Iterable[TitleEntry],
    *,
    search_fn: SearchFn,
    writer: Writer,
    store: SyncStore,
    confirm_fn: ConfirmFn | None = None,
    edition_fn: EditionFn | None = None,
    dry_run: bool = False,
) -> SyncOutcome:
    """Mark a list of plain titles read (used for a collection's contained books).

    Mirrors run_sync but keys on a caller-supplied ``key`` (contained books have no
    source id of their own) and searches by the title string.
    """
    outcome = SyncOutcome()
    for entry in entries:
        if store.is_synced(entry.key, entry.finish_date):
            outcome.skipped_synced.append(entry.key)
            continue

        book_id = store.cached_book_id(entry.key)
        if book_id is None:
            query = f"{search_title(entry.title)} {entry.author}".strip()
            result = match_book(entry.title, entry.author, search_fn(query))
            chosen = resolve_match(entry, result, confirm_fn)
            if chosen is None:
                if result.status is MatchStatus.NO_MATCH:
                    outcome.no_match.append(entry.key)
                else:
                    outcome.ambiguous_skipped.append(entry.key)
                continue
            book_id = _effective_book_id(chosen.book_id, entry.media_format, edition_fn)
            store.remember_match(entry.key, book_id)

        if dry_run:
            outcome.planned.append(entry.key)
            continue

        if writer.mark_finished(book_id, entry.finish_date):
            store.record(entry.key, book_id, entry.finish_date)
            outcome.written.append(entry.key)
        else:
            outcome.failed.append(entry.key)

    return outcome


def run_review_sync(
    books: Iterable[SourceBook],
    *,
    rater: Rater,
    store: SyncStore,
    narrator_note: bool = True,
    dry_run: bool = False,
) -> SyncOutcome:
    """Write each book's rating + review (with a narrator note) to its matched
    StoryGraph book. Requires the book to already be matched (mark-read pass populates
    the store mapping). Idempotent via the store's ``rated`` set; an existing StoryGraph
    review is left untouched (the writer reports 'skipped')."""
    outcome = SyncOutcome()
    for book in books:
        if store.is_rated(book.key):
            outcome.skipped_synced.append(book.key)
            continue
        book_id = store.cached_book_id(book.key)
        if book_id is None:
            outcome.no_match.append(book.key)
            continue

        stars_integer, stars_decimal = ("", "")
        if book.rating:
            stars_integer, stars_decimal = rating_to_stars(book.rating)
        narrators = book.narrators if narrator_note else ()
        explanation = compose_review(book.review, narrators) or ""
        if not stars_integer and not explanation:
            continue

        if dry_run:
            outcome.planned.append(book.key)
            continue

        status = rater.write_review(
            book_id,
            stars_integer=stars_integer,
            stars_decimal=stars_decimal,
            explanation=explanation,
        )
        if status == "written":
            store.record_rated(book.key)
            outcome.written.append(book.key)
        elif status == "skipped":
            store.record_rated(book.key)
            outcome.skipped_synced.append(book.key)
        else:
            outcome.failed.append(book.key)

    return outcome
