"""Cross-source de-duplication of finished books.

A book finished in two sources (e.g. the same title from Audible and Kobo) arrives as two
distinct ``SourceBook`` records with different ``key`` values, so the per-source idempotency
layer in the sync store never sees them as the same book and pushes both to StoryGraph. This
module collapses such duplicates into one record *before* the push.

Identity rule (``identity_key``):
- ISBN-first: if the book carries an ISBN, its normalized ISBN (ISBN-13 preferred over ISBN-10,
  via :func:`isbn_query`) is the identity. Two books with the same ISBN are the same book.
- Fuzzy fallback: ISBN-less books (e.g. Audible) are grouped by fuzzy title+author similarity.
  Two ISBN-less books merge when their normalized titles and first authors both clear the
  similarity thresholds below. ISBN-keyed and fuzzy-keyed books never merge with each other —
  an ISBN match is exact, so it is never overridden by a fuzzy guess.

Winner rule (``_better``): when several records are the same book, the surviving record is
chosen deterministically by, in order: finished over unfinished, then having a finish date,
then richer metadata (more populated fields — ISBN, narrators, rating, review, ...), then a
stable tie-break on ``key`` so the result never depends on input ordering.
"""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING

from .matching import (
    author_similarity,
    normalize_author,
    normalize_title,
    title_similarity,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..models import SourceBook

# Stricter than the title/author *match* thresholds: a merge silently drops a finished book, so
# a false positive (two different books merged) loses a real read. Require near-identical titles
# and corroborating authors before collapsing ISBN-less records.
TITLE_MERGE_THRESHOLD = 0.90
AUTHOR_MERGE_THRESHOLD = 0.80


def _first_author(book: SourceBook) -> str:
    return book.authors[0] if book.authors else ""


def identity_key(book: SourceBook) -> str | None:
    """The cross-source identity of a book, or None when it has neither an ISBN nor a title.

    Returns an ``isbn:<digits>`` key when the book exports an ISBN (the exact, preferred
    identity), else a ``fuzzy:<normalized title>`` key used only as a grouping bucket — fuzzy
    keys are *not* compared for equality directly; :func:`merge_duplicates` re-checks fuzzy
    candidates with the similarity thresholds.
    """
    from .sync import isbn_query

    isbn = isbn_query(book)
    if isbn:
        return f"isbn:{isbn}"
    title = normalize_title(book.title)
    return f"fuzzy:{title}" if title else None


def _richness(book: SourceBook) -> int:
    return sum(1 for f in fields(book) if getattr(book, f.name))


def _better(challenger: SourceBook, incumbent: SourceBook) -> bool:
    """True when ``challenger`` should win over ``incumbent`` for the same book.

    Deterministic priority: finished > unfinished, then has-finish-date, then richer metadata,
    then a stable ``key`` tie-break (so equal records resolve identically regardless of order).
    """
    a = (
        challenger.is_finished,
        challenger.finished_at is not None,
        _richness(challenger),
    )
    b = (
        incumbent.is_finished,
        incumbent.finished_at is not None,
        _richness(incumbent),
    )
    if a != b:
        return a > b
    return challenger.key < incumbent.key


def _fuzzy_same(a: SourceBook, b: SourceBook) -> bool:
    title_score = title_similarity(normalize_title(a.title), normalize_title(b.title))
    if title_score < TITLE_MERGE_THRESHOLD:
        return False
    na, nb = normalize_author(_first_author(a)), normalize_author(_first_author(b))
    if not na or not nb:
        return False  # can't corroborate a title-only match across sources; keep both
    return author_similarity(na, nb) >= AUTHOR_MERGE_THRESHOLD


def merge_duplicates(books: Iterable[SourceBook]) -> list[SourceBook]:
    """Collapse the same book reported by several sources into one record.

    Preserves first-seen order of the surviving records and is a no-op for books that share no
    cross-source identity. Books with no identity at all (no ISBN, empty title) pass through
    untouched.
    """
    winners: list[SourceBook] = []
    isbn_index: dict[str, int] = {}

    for book in books:
        key = identity_key(book)
        if key is None:
            winners.append(book)
            continue

        is_isbn_key = key.startswith("isbn:")
        slot = isbn_index.get(key) if is_isbn_key else _fuzzy_slot(book, winners)
        if slot is None:
            if is_isbn_key:
                isbn_index[key] = len(winners)
            winners.append(book)
        elif _better(book, winners[slot]):
            winners[slot] = book

    return winners


def _fuzzy_slot(book: SourceBook, winners: list[SourceBook]) -> int | None:
    """Index of an existing ISBN-less winner that is the same book as ``book``, or None.

    Only ISBN-less winners are eligible: an ISBN-keyed record is an exact identity and must not
    be absorbed by a fuzzy title/author guess.
    """
    from .sync import isbn_query

    return next(
        (
            i
            for i, winner in enumerate(winners)
            if isbn_query(winner) is None and _fuzzy_same(book, winner)
        ),
        None,
    )
