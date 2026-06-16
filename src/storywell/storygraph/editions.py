"""Pure helpers for picking the StoryGraph edition that matches a source's format.

On StoryGraph a ``book_id`` *is* a single edition; a work's other editions live at
``/books/{id}/editions`` (each ``.book-pane`` links to its own ``/books/{edition_id}``
and states ``Format: Audio|Paperback|Hardcover|Digital``). To tag a book as the audio
version we mark the audiobook edition's own page read, so we need to map a matched
``book_id`` to the audio edition's ``book_id``. These helpers are pure; the browser
scrape that produces the records lives in ``storywell.storygraph.search``.
"""

from __future__ import annotations

from dataclasses import dataclass

# StoryGraph edition formats (lowercased) that satisfy each source ``media_format``.
# Only "audio" is verified against the live editions DOM; add other formats (e.g. ebook
# -> "digital", print -> "paperback"/"hardcover") when a source needs them and the label
# is confirmed live, rather than shipping unvalidated guesses.
_SG_FORMATS_FOR: dict[str, tuple[str, ...]] = {
    "audio": ("audio",),
}


@dataclass(frozen=True)
class Edition:
    book_id: str
    format: str  # StoryGraph's format label, lowercased ("audio", "paperback", ...)


def sg_formats_for(media_format: str) -> tuple[str, ...]:
    """The StoryGraph format labels that count as ``media_format`` (empty if unknown)."""
    return _SG_FORMATS_FOR.get(media_format.strip().lower(), ())


def parse_editions(records: list[dict]) -> list[Edition]:
    editions: list[Edition] = []
    for record in records:
        book_id = (record.get("id") or "").strip()
        if book_id:
            editions.append(Edition(book_id, (record.get("format") or "").strip().lower()))
    return editions


def pick_edition(editions: list[Edition], media_format: str) -> str | None:
    """The first edition whose format matches ``media_format``, or None if there is none."""
    targets = sg_formats_for(media_format)
    if not targets:
        return None
    for edition in editions:
        if edition.format in targets:
            return edition.book_id
    return None
