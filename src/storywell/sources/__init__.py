from __future__ import annotations

import inspect

from .audible import AudibleSource
from .base import Source, SourceError
from .goodreads import GoodreadsSource
from .librarything import LibraryThingSource

SOURCES: dict[str, type[Source]] = {
    AudibleSource.name: AudibleSource,
    GoodreadsSource.name: GoodreadsSource,
    LibraryThingSource.name: LibraryThingSource,
}


def available_sources() -> list[str]:
    return sorted(SOURCES)


def make_source(name: str, **options) -> Source:
    """Build a registered source by name, passing through CLI options it accepts.

    New vendors register their class in ``SOURCES`` and pick the options they use;
    options the source's constructor doesn't declare are dropped, so one CLI
    surface can carry the union of every source's options.
    """
    try:
        cls = SOURCES[name]
    except KeyError:
        raise SourceError(
            f"Unknown source '{name}'. Available: {', '.join(available_sources())}."
        ) from None
    accepted = inspect.signature(cls).parameters
    return cls(**{k: v for k, v in options.items() if k in accepted})


__all__ = [
    "SOURCES",
    "AudibleSource",
    "GoodreadsSource",
    "LibraryThingSource",
    "Source",
    "SourceError",
    "available_sources",
    "make_source",
]
