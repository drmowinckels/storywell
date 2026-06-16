from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SourceBook:
    """A finished book reported by a source (Audible, Goodreads, ...).

    ``source`` + ``source_id`` uniquely identify the listing across every vendor,
    so ``key`` is what the sync store and idempotency layer index on. ``narrators``
    is audiobook-specific and simply stays empty for text-only sources. ``media_format``
    carries the source's native format ("audio", "ebook", "print", or "" when unknown)
    so the sync can mark the matching StoryGraph edition rather than any edition.
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

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"
