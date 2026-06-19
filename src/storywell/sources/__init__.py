from __future__ import annotations

import importlib
import inspect

from .base import Source, SourceError

# name -> (submodule, class). Kept static so listing or building one source never
# imports every vendor's dependencies (e.g. the Audible SDK) up front. This also keeps
# the stats CSV reader — which lives under this package — dependency-light enough to run
# under Pyodide/WASM in the browser, where the heavier sources can't be installed.
_REGISTRY: dict[str, tuple[str, str]] = {
    "applebooks": ("applebooks", "AppleBooksSource"),
    "audible": ("audible", "AudibleSource"),
    "goodreads": ("goodreads", "GoodreadsSource"),
    "hardcover": ("hardcover", "HardcoverSource"),
    "kobo": ("kobo", "KoboSource"),
    "librarything": ("librarything", "LibraryThingSource"),
    "literal": ("literal", "LiteralSource"),
}

_CLASS_TO_MODULE = {cls: module for module, cls in _REGISTRY.values()}


def _load_source_class(name: str) -> type[Source]:
    module, cls = _REGISTRY[name]
    return getattr(importlib.import_module(f".{module}", __name__), cls)


def available_sources() -> list[str]:
    return sorted(_REGISTRY)


def make_source(name: str, **options) -> Source:
    """Build a registered source by name, passing through CLI options it accepts.

    New vendors register their class in ``_REGISTRY`` and pick the options they use;
    options the source's constructor doesn't declare are dropped, so one CLI
    surface can carry the union of every source's options.
    """
    try:
        cls = _load_source_class(name)
    except KeyError:
        raise SourceError(
            f"Unknown source '{name}'. Available: {', '.join(available_sources())}."
        ) from None
    accepted = inspect.signature(cls).parameters
    return cls(**{k: v for k, v in options.items() if k in accepted})


def __getattr__(name: str):
    """Lazily expose the source classes and the ``SOURCES`` map (PEP 562), so
    ``from storywell.sources import AudibleSource`` still works without importing
    every source on a plain ``import storywell.sources``."""
    if name in _CLASS_TO_MODULE:
        return getattr(importlib.import_module(f".{_CLASS_TO_MODULE[name]}", __name__), name)
    if name == "SOURCES":
        return {n: _load_source_class(n) for n in _REGISTRY}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "SOURCES",
    "AppleBooksSource",
    "AudibleSource",
    "GoodreadsSource",
    "HardcoverSource",
    "KoboSource",
    "LibraryThingSource",
    "LiteralSource",
    "Source",
    "SourceError",
    "available_sources",
    "make_source",
]
