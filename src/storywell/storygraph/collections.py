"""Parse a StoryGraph omnibus's contained book titles, for marking the books inside
a collection rather than the omnibus itself.

A source flags a book as a collection (``SourceBook.is_collection``); the contained
titles come from the StoryGraph omnibus title and description, parsed best-effort here
and confirmed interactively. See docs/prd-v0.3-collections.md.
"""

from __future__ import annotations

import re

_INCLUDES = re.compile(
    r"(?:included are the following|includes?|contains?|collected here are[^:]*)\s*[:\-]\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_SECTION_LABEL = re.compile(r"\b[a-z]+\s+works?\s*:", re.IGNORECASE)
_YEAR_PAREN = re.compile(r"\s*\([^)]*(?:18|19|20)\d{2}[^)]*\)")
_PROSE_START = (
    "the conclusion",
    "wherein",
    "and the",
    "etc",
    "plus ",
    "experience",
    "enjoy",
    "discover",
)
_BY_AUTHOR = re.compile(r"\s+By:\s+.*$", re.IGNORECASE)


def _dedupe_titles(parts: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        title = raw.strip(" .•-\t")
        if not (2 <= len(title) <= 60):
            continue
        if title.lower().startswith(_PROSE_START):
            continue
        key = title.lower()
        if key not in seen:
            seen.add(key)
            out.append(title)
    return out


def parse_contained_titles(description: str) -> list[str]:
    """Best-effort extraction of contained book titles from an omnibus description."""
    if not description:
        return []
    match = _INCLUDES.search(description)
    if not match:
        return []
    segment = _SECTION_LABEL.sub(",", match.group(1))
    segment = _YEAR_PAREN.sub("", segment)
    return _dedupe_titles(re.split(r"[;,\n]+", segment))


def parse_titles_from_storygraph_title(storygraph_title: str) -> list[str]:
    """Some omnibuses list their contents in the StoryGraph title itself, e.g.
    'The Complete Novels of X: Part Two: A, B, C, & D By: X'."""
    text = _BY_AUTHOR.sub("", storygraph_title)
    tail = text.rsplit(":", 1)[1] if ":" in text else text
    if "," not in tail and "&" not in tail:
        return []
    return _dedupe_titles(re.split(r"[;,]+", tail.replace("&", ",")))


def proposed_titles(storygraph_title: str, description: str) -> list[str]:
    """Suggested contained titles: prefer the title list (cleaner when present, e.g.
    Dickens), else fall back to the description list (e.g. Austen)."""
    return parse_titles_from_storygraph_title(storygraph_title) or parse_contained_titles(
        description
    )


def select_titles(suggestions: list[str], selection: str) -> list[str]:
    """Resolve a checklist selection string into chosen titles.

    Defaults to none (unchecked). "a"/"all" selects everything; otherwise a list of
    1-based indices ("1,3,5" or "1 3 5") picks those suggestions.
    """
    choice = (selection or "").strip().lower()
    if not choice:
        return []
    if choice in ("a", "all"):
        return list(suggestions)

    chosen: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[,\s]+", choice):
        if not token.isdigit():
            continue
        index = int(token) - 1
        if 0 <= index < len(suggestions) and suggestions[index] not in seen:
            seen.add(suggestions[index])
            chosen.append(suggestions[index])
    return chosen
