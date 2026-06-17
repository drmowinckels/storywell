from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from ..models import SourceBook
from .editions import Edition, pick_edition
from .matching import (
    Candidate,
    MatchResult,
    MatchStatus,
    is_isbn,
    match_book,
    normalize_isbn,
    search_title,
)
from .reviews import compose_review, rating_to_stars
from .session import StorygraphAuthError
from .store import SyncStore

SearchFn = Callable[[str], list[Candidate]]
ConfirmFn = Callable[[Any, MatchResult], "Candidate | None"]
EditionFn = Callable[[str, str], "str | None"]
EditionsFn = Callable[[str], list[Edition]]


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


def isbn_query(book: SourceBook) -> str | None:
    """The book's normalized ISBN to search StoryGraph by, preferring ISBN-13, or None."""
    for raw in (book.isbn13, book.isbn):
        if is_isbn(raw):
            return normalize_isbn(raw)
    return None


def match_for_book(book: SourceBook, search_fn: SearchFn) -> MatchResult:
    """Resolve a StoryGraph match for a book: by ISBN if the source exports one, else by
    title/author search.

    The ISBN is searched first (StoryGraph's search box accepts ISBNs and usually returns the
    exact edition), but the hit is still scored by title/author and only accepted when it clears
    the normal MATCH bar. StoryGraph search is free-text, so a numeric ISBN query can return an
    unrelated book — trusting it blindly would mark the wrong book read. A weak or empty ISBN
    result falls back to the ordinary title/author search, so ISBN-less sources are unaffected.
    """
    author = book.authors[0] if book.authors else ""
    isbn = isbn_query(book)
    if isbn:
        result = match_book(book.title, author, search_fn(isbn))
        if result.status is MatchStatus.MATCH:
            return result
    return match_book(book.title, author, search_fn(query_for(book)))


def plan_sync(books: Iterable[SourceBook], search_fn: SearchFn) -> list[SyncPlanItem]:
    items: list[SyncPlanItem] = []
    for book in books:
        result = match_for_book(book, search_fn)
        items.append(SyncPlanItem(book, result))
    return items


def summarize(items: Iterable[SyncPlanItem]) -> dict[MatchStatus, int]:
    counts = {status: 0 for status in MatchStatus}
    for item in items:
        counts[item.result.status] += 1
    return counts


RETAG_FORMAT = "audio"


@dataclass(frozen=True)
class RetagItem:
    """One already-matched book's audio-edition status, for the read-only retag report."""

    key: str
    current_id: str
    current_format: str
    audio_id: str | None

    @property
    def status(self) -> str:
        if not self.current_format and self.audio_id is None:
            return "unknown"  # could not read the editions page
        if self.current_format == RETAG_FORMAT:
            return "already_audio"
        return "retaggable" if self.audio_id else "no_audio_edition"


def plan_retag(
    books: Iterable[SourceBook], *, store: SyncStore, editions_fn: EditionsFn
) -> list[RetagItem]:
    """Report which already-matched audiobook-source books are on a non-audio StoryGraph
    edition (and whether an audio edition exists to move them to). Read-only: inspects the
    cached match for each book and the work's editions; never writes."""
    items: list[RetagItem] = []
    for book in books:
        if book.media_format != RETAG_FORMAT:
            continue
        current_id = store.cached_book_id(book.key)
        if current_id is None:
            continue
        editions = editions_fn(current_id)
        current_format = next((e.format for e in editions if e.book_id == current_id), "")
        items.append(
            RetagItem(book.key, current_id, current_format, pick_edition(editions, RETAG_FORMAT))
        )
    return items


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
        try:
            finished_on = _finish_date(book)
            if store.is_synced(book.key, finished_on):
                outcome.skipped_synced.append(book.key)
                continue

            book_id = store.cached_book_id(book.key)
            if book_id is None:
                result = match_for_book(book, search_fn)
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
        except StorygraphAuthError:
            raise  # a dead session is fatal; don't bury it as a per-book failure
        except Exception:
            outcome.failed.append(book.key)  # one flaky book must not abort the batch

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
        try:
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
        except StorygraphAuthError:
            raise
        except Exception:
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
    """Write each book's rating + review to its matched StoryGraph book. Requires the
    book to already be matched (mark-read pass populates the store mapping). Idempotent
    via the store's ``rated`` set; an existing StoryGraph review is left untouched (the
    writer reports 'skipped').

    Only books the listener actually rated or reviewed are posted. The narrator note is
    appended to those, but a narrator note alone never triggers a post — we don't publish
    a public review for a book the listener never rated or reviewed."""
    outcome = SyncOutcome()
    for book in books:
        try:
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
            has_written_review = bool(book.review and book.review.strip())
            if not stars_integer and not has_written_review:
                continue
            narrators = book.narrators if narrator_note else ()
            explanation = compose_review(book.review, narrators) or ""

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
        except StorygraphAuthError:
            raise
        except Exception:
            outcome.failed.append(book.key)

    return outcome
