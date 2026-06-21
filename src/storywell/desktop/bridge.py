"""The engine bridge exposed to the desktop web UI.

``Api`` is the object pywebview hands to JavaScript as ``pywebview.api``. Every method
returns a JSON-able envelope (``{"ok": bool, ...}``) and runs the blocking engine call on
a dedicated worker thread via :func:`run_off_thread`. Off-thread is required, not cosmetic:
the StoryGraph path drives Playwright's sync API, whose context must live entirely on one
thread and must never share a thread with the GUI event loop. Keeping each call on its own
thread also turns engine exceptions into messages the UI can render instead of crashing.

This module deliberately does not import pywebview, so the bridge is unit-testable headlessly.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .. import service


@dataclass
class JobResult:
    ok: bool
    value: Any = None
    error: str | None = None
    error_type: str | None = None


def run_off_thread(fn: Callable[[], Any]) -> JobResult:
    """Run ``fn`` on a fresh worker thread, returning its result or captured error.

    Joins before returning, so the caller sees a normal synchronous value. The point is
    isolation, not non-blocking: ``fn`` runs on a clean, dedicated thread because
    Playwright's sync API requires one, rather than on whatever thread pywebview invoked
    the bridge from. Window responsiveness comes from pywebview dispatching ``js_api``
    calls off the GUI thread, not from this join.
    """
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["value"] = fn()
        except Exception as err:  # noqa: BLE001 - surfaced to the UI as a message
            box["error"] = err

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join()

    err = box.get("error")
    if err is not None:
        return JobResult(ok=False, error=str(err), error_type=type(err).__name__)
    return JobResult(ok=True, value=box.get("value"))


def _envelope(fn: Callable[[], Any]) -> dict[str, Any]:
    outcome = run_off_thread(fn)
    if outcome.ok:
        return {"ok": True, "value": outcome.value}
    return {"ok": False, "error": outcome.error, "errorType": outcome.error_type}


def book_to_dict(book: Any) -> dict[str, Any]:
    return {
        "key": book.key,
        "title": book.title,
        "authors": list(book.authors),
        "percentComplete": round(book.percent_complete, 1),
        "finishedAt": book.finished_at.date().isoformat() if book.finished_at else None,
        "rating": book.rating,
    }


def plan_item_to_dict(item: Any) -> dict[str, Any]:
    best = item.result.best
    return {
        "title": item.book.title,
        "status": item.result.status.value,
        "match": best.candidate.title if best else None,
        "matchId": best.candidate.book_id if best else None,
        "score": round(best.score, 3) if best else None,
    }


@dataclass
class Api:
    """Engine methods exposed to the web UI. Each returns a JSON-able envelope."""

    headless: bool = True

    def sources(self) -> dict[str, Any]:
        from ..sources import available_sources

        return _envelope(lambda: list(available_sources()))

    def login_state(self) -> dict[str, Any]:
        return _envelope(lambda: service.login_state(headless=self.headless))

    def chromium_status(self) -> dict[str, Any]:
        return _envelope(service.chromium_installed)

    def install_browser(self) -> dict[str, Any]:
        return _envelope(service.ensure_chromium)

    def list_finished(self, source: str, threshold: float = 0.95) -> dict[str, Any]:
        return _envelope(
            lambda: [book_to_dict(b) for b in service.list_finished(source, threshold=threshold)]
        )

    def sync_plan(self, source: str, threshold: float = 0.95) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            books = service.list_finished(source, threshold=threshold)
            items = service.build_sync_plan(books, headless=self.headless)
            return {
                "plan": [plan_item_to_dict(i) for i in items],
                "summary": service.summarize_plan(items),
            }

        return _envelope(work)
