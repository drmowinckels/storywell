from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Audiobook:
    asin: str
    title: str
    authors: tuple[str, ...] = ()
    narrators: tuple[str, ...] = ()
    percent_complete: float = 0.0
    finished_at: datetime | None = None
    is_finished: bool = False
