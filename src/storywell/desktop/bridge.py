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
from pathlib import Path
from typing import Any

from .. import service
from ..models import Shelf


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


def source_kwargs(options: dict[str, Any] | None) -> dict[str, Any]:
    """Translate the UI's per-source options into ``service.list_finished`` keyword args.

    The web UI sends a plain object; only the keys a given source needs are populated
    (``path`` for file sources, ``token`` for Hardcover/Literal, ``readColumn`` for Calibre,
    ``shelf`` for catalogue sources like LibraryThing). Keys map to ``service.list_finished``.
    """
    opts = options or {}
    path = opts.get("path")
    shelf = opts.get("shelf")
    return {
        "threshold": opts.get("threshold", 0.95),
        "path": Path(path) if path else None,
        "token": opts.get("token") or None,
        "read_column": opts.get("readColumn") or None,
        "shelf": Shelf(shelf) if shelf else None,
    }


def open_file_dialog() -> str | None:
    """Open the OS file picker and return the chosen path, or None if cancelled.

    The native dialog is a pywebview/UI capability JS can't reach directly, so the bridge
    is the only place to expose it. Imported lazily so the bridge stays testable without
    pywebview; not unit-tested (it needs a real window).
    """
    import webview

    window = webview.windows[0]
    result = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
    return result[0] if result else None


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
        # The UI only calls this after chromium_status reported missing, so install
        # directly rather than re-checking (which would spin up the driver again).
        return _envelope(service.install_chromium)

    def audible_login(self, marketplace: str) -> dict[str, Any]:
        return _envelope(lambda: service.audible_login(marketplace))

    def storygraph_login(self) -> dict[str, Any]:
        return _envelope(service.storygraph_login)

    def list_finished(self, source: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        # source_kwargs runs inside the worker so a bad option surfaces as an error envelope.
        return _envelope(
            lambda: [
                book_to_dict(b) for b in service.list_finished(source, **source_kwargs(options))
            ]
        )

    def sync_plan(self, source: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            books = service.list_finished(source, **source_kwargs(options))
            items = service.build_sync_plan(books, headless=self.headless)
            return {
                "plan": [plan_item_to_dict(i) for i in items],
                "summary": service.summarize_plan(items),
            }

        return _envelope(work)

    def choose_file(self) -> dict[str, Any]:
        # The native file picker is a UI-thread operation pywebview marshals itself, so it
        # runs directly here (not via run_off_thread) and JS can only reach it through this
        # bridge method.
        try:
            return {"ok": True, "value": open_file_dialog()}
        except Exception as err:  # noqa: BLE001 - surfaced to the UI as a message
            return {"ok": False, "error": str(err), "errorType": type(err).__name__}
