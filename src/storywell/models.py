from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Shelf(StrEnum):
    """A StoryGraph shelf a book can be routed to.

    The string values are StoryGraph's own status slugs (the same ones the library export
    uses), so they pass straight into the ``status=`` segment of the update-status form.
    ``UNKNOWN`` is the inert default: a book whose source has not declared a shelf is never
    written until the caller routes it somewhere (read for a finished signal, or the
    source's opt-in target shelf otherwise).
    """

    READ = "read"
    CURRENTLY_READING = "currently-reading"
    TO_READ = "to-read"
    DID_NOT_FINISH = "did-not-finish"
    UNKNOWN = "unknown"


WRITABLE_SHELVES: tuple[Shelf, ...] = (
    Shelf.READ,
    Shelf.CURRENTLY_READING,
    Shelf.TO_READ,
    Shelf.DID_NOT_FINISH,
)


@dataclass(frozen=True)
class SourceBook:
    """A book reported by a source (Audible, Goodreads, ...).

    ``source`` + ``source_id`` uniquely identify the listing across every vendor,
    so ``key`` is what the sync store and idempotency layer index on. ``narrators``
    is audiobook-specific and simply stays empty for text-only sources. ``media_format``
    carries the source's native format ("audio", "ebook", "print", or "" when unknown)
    so the sync can mark the matching StoryGraph edition rather than any edition. ``isbn`` /
    ``isbn13`` are populated by shelf sources that export them (Goodreads, LibraryThing,
    Kobo) and let StoryGraph matching resolve by identifier instead of fuzzy title; they
    stay empty for sources without ISBNs (Audible).

    ``status`` is the source's declared shelf intent and stays ``UNKNOWN`` until a source
    sets it; the sync router turns a finished signal into ``read`` and otherwise honours
    ``status`` (falling back to the caller's default shelf). It is independent of
    ``is_finished`` so a source can report a non-finished book (e.g. a borrow on a library
    source) without claiming it was read.
    """

    source: str
    source_id: str
    title: str
    authors: tuple[str, ...] = ()
    narrators: tuple[str, ...] = ()
    percent_complete: float = 0.0
    finished_at: datetime | None = None
    is_finished: bool = False
    is_collection: bool = False
    rating: float | None = None
    review: str | None = None
    media_format: str = ""
    isbn: str | None = None
    isbn13: str | None = None
    status: Shelf = Shelf.UNKNOWN

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"
