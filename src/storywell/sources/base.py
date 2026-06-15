from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import SourceBook


class SourceError(RuntimeError):
    """A source could not produce its finished books (auth, network, parsing)."""


@runtime_checkable
class Source(Protocol):
    """A vendor that can report the books a user has finished.

    Implementations live in ``storywell.sources`` and are registered in the
    ``SOURCES`` table so the CLI can select one with ``--source``. Each source
    owns its own auth/fetch details and normalizes everything into ``SourceBook``.
    """

    name: str

    def finished_books(self, *, threshold: float = 0.95) -> list[SourceBook]: ...
