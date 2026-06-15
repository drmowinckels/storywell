from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


def _norm_date(finished_on: date | str | None) -> str:
    if finished_on is None:
        return ""
    if isinstance(finished_on, date):
        return finished_on.isoformat()
    return str(finished_on)


@dataclass
class SyncStore:
    """Persisted ASIN -> StoryGraph mapping and last-synced finish dates.

    ``mappings`` lets a confirmed match skip search forever; ``synced`` makes
    re-runs idempotent (a book is re-synced only if its finish date changed).
    """

    path: Path
    mappings: dict[str, str] = field(default_factory=dict)
    synced: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> SyncStore:
        data: dict = {}
        if path.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                data = json.loads(path.read_text())
        return cls(
            path=path,
            mappings=dict(data.get("mappings", {})),
            synced=dict(data.get("synced", {})),
        )

    def cached_book_id(self, asin: str) -> str | None:
        return self.mappings.get(asin)

    def is_synced(self, asin: str, finished_on: date | str | None) -> bool:
        return asin in self.synced and self.synced[asin] == _norm_date(finished_on)

    def remember_match(self, asin: str, book_id: str) -> None:
        self.mappings[asin] = book_id

    def record(self, asin: str, book_id: str, finished_on: date | str | None) -> None:
        self.mappings[asin] = book_id
        self.synced[asin] = _norm_date(finished_on)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"mappings": self.mappings, "synced": self.synced},
                indent=2,
                sort_keys=True,
            )
        )
        with contextlib.suppress(OSError):
            self.path.chmod(0o600)
