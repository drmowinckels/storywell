"""Read-side analytics for a StoryGraph library export.

Storywell's sync side *writes* finished books into StoryGraph. This package does the
opposite: it reads the user's own library CSV export and turns it into rich reading stats
the StoryGraph free tier doesn't surface — fully offline, no scraping, no account. The
parse/compute layers are pure and unit-tested; rendering (HTML dashboard) lands in a later
slice behind the optional ``[stats]`` extra.
"""

from __future__ import annotations

from .compute import compute_all
from .export import LibraryEntry, load_export
from .parse import ReadInstance, ReadStatus

__all__ = [
    "LibraryEntry",
    "ReadInstance",
    "ReadStatus",
    "compute_all",
    "load_export",
]
