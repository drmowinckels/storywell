"""Out-of-process engine for the desktop GUI.

All Playwright/StoryGraph work runs in a separate, **spawned** worker process — never in
the GUI process. This is mandatory on macOS: once pywebview initialises the Cocoa/Objective-C
runtime on the main thread, any ``fork()`` in that process leaves the child in an undefined
state, and Playwright launches its driver by forking. The symptom is a silent deadlock with a
wedged ``python`` child stuck re-entering the GUI. A *spawned* worker gets a clean interpreter
with no inherited Cocoa state, so Playwright's fork is safe there. (GTK/Qt on Linux and the
Windows spawn-only model make process isolation the right cross-platform choice anyway.)

The worker is single-threaded and handles one request at a time over a ``Pipe`` — which is
also exactly what Playwright's sync API wants (one consistent thread). :class:`EngineClient`
owns the worker lifecycle; :func:`dispatch` is the testable, in-process core.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import threading
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from .. import service
from ..models import Shelf


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


def dispatch(
    method: str,
    args: dict[str, Any],
    on_progress: Callable[[int, int], None] | None = None,
) -> Any:
    """Run one engine operation by name and return JSON-able data.

    This is the worker's whole job and the unit-testable seam: it owns no transport or
    process machinery, so tests can drive it in-process with ``service`` monkeypatched.
    ``on_progress`` (used only by ``sync_plan``) receives ``(done, total)`` per book.
    """
    if method == "sources":
        from ..sources import available_sources

        return list(available_sources())
    if method == "login_state":
        return service.login_state(headless=args["headless"])
    if method == "saved_logins":
        return service.saved_logins()
    if method == "forget_login":
        return service.forget_login(args["service_name"])
    if method == "chromium_status":
        return service.chromium_installed()
    if method == "install_browser":
        return service.install_chromium()
    if method == "audible_login":
        return service.audible_login(args["marketplace"])
    if method == "storygraph_login":
        return service.storygraph_login()
    if method == "list_finished":
        books = service.list_finished(args["source"], **source_kwargs(args["options"]))
        return [book_to_dict(b) for b in books]
    if method == "sync_plan":
        books = service.list_finished(args["source"], **source_kwargs(args["options"]))
        items = service.build_sync_plan(books, headless=args["headless"], on_progress=on_progress)
        return {
            "plan": [plan_item_to_dict(i) for i in items],
            "summary": service.summarize_plan(items),
        }
    raise ValueError(f"unknown engine method: {method!r}")


def _envelope(
    method: str,
    args: dict[str, Any],
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Wrap one dispatch call as ``{ok, value}`` / ``{ok, error, errorType}`` for the UI."""
    try:
        return {"ok": True, "value": dispatch(method, args, on_progress)}
    except Exception as err:  # noqa: BLE001 - surfaced to the UI as a message
        return {"ok": False, "error": str(err), "errorType": type(err).__name__}


def _worker_loop(conn: Connection, progress: Any) -> None:
    """Serve engine requests until the parent closes the pipe. Runs in the spawned child.

    ``progress`` is a shared ``[done, total]`` array the parent polls during long jobs;
    each request resets it to zero before running.
    """

    def on_progress(done: int, total: int) -> None:
        progress[0] = done
        progress[1] = total

    while True:
        try:
            msg = conn.recv()
        except EOFError:
            return
        if msg is None:
            return
        method, args = msg
        progress[0] = 0
        progress[1] = 0
        conn.send(_envelope(method, args, on_progress))


class EngineClient:
    """Owns the spawned worker process and marshals one request at a time over a Pipe.

    Lazily starts the worker on first use; a dead worker is restarted on the next call. The
    lock serialises the pipe because pywebview dispatches each ``js_api`` call on its own
    thread, while the worker handles exactly one request at a time.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ctx = multiprocessing.get_context("spawn")
        self._proc: multiprocessing.process.BaseProcess | None = None
        self._conn: Connection | None = None
        # [done, total] for the in-flight job; the worker writes, the GUI polls. Read
        # without the request lock so progress polls never block on the running job.
        self._progress = self._ctx.Array("q", [0, 0])

    def _ensure(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            return
        self._conn, child = self._ctx.Pipe()
        self._proc = self._ctx.Process(
            target=_worker_loop,
            args=(child, self._progress),
            name="storywell-engine",
            daemon=True,
        )
        self._proc.start()
        child.close()  # only the worker keeps its end

    def progress(self) -> dict[str, int]:
        return {"done": self._progress[0], "total": self._progress[1]}

    def _reset(self) -> None:
        if self._conn is not None:
            self._conn.close()
        if self._proc is not None and self._proc.is_alive():
            self._proc.terminate()
        self._proc = None
        self._conn = None

    def call(self, method: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            try:
                self._ensure()
                assert self._conn is not None
                self._conn.send((method, args or {}))
                return self._conn.recv()
            except (EOFError, BrokenPipeError, ConnectionError, OSError) as err:
                self._reset()
                return {
                    "ok": False,
                    "error": f"The engine stopped unexpectedly: {err}. Please try again.",
                    "errorType": type(err).__name__,
                }

    def shutdown(self) -> None:
        with self._lock:
            if self._conn is not None:
                with contextlib.suppress(BrokenPipeError, OSError):
                    self._conn.send(None)
            self._reset()
