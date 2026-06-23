"""The engine bridge exposed to the desktop web UI.

``Api`` is the object pywebview hands to JavaScript as ``pywebview.api``. Every method
returns a JSON-able envelope (``{"ok": bool, ...}``). The actual engine work runs in a
separate worker process (see :mod:`storywell.desktop.engine`): the GUI process must never
run Playwright itself, because forking a Cocoa/GTK GUI process deadlocks. ``Api`` is just a
thin marshaller — it forwards each call to the :class:`~storywell.desktop.engine.EngineClient`
and hands the result straight back to JS.

The one exception is :meth:`Api.choose_file`: the native file dialog is a GUI-thread
capability that must stay in this process.

This module deliberately does not import pywebview, so the bridge is unit-testable headlessly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .engine import EngineClient


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
    engine: EngineClient = field(default_factory=EngineClient)

    def sources(self) -> dict[str, Any]:
        return self.engine.call("sources")

    def login_state(self) -> dict[str, Any]:
        return self.engine.call("login_state", {"headless": self.headless})

    def saved_logins(self) -> dict[str, Any]:
        return self.engine.call("saved_logins")

    def forget_login(self, service_name: str) -> dict[str, Any]:
        return self.engine.call("forget_login", {"service_name": service_name})

    def chromium_status(self) -> dict[str, Any]:
        return self.engine.call("chromium_status")

    def install_browser(self) -> dict[str, Any]:
        return self.engine.call("install_browser")

    def audible_login(self, marketplace: str) -> dict[str, Any]:
        return self.engine.call("audible_login", {"marketplace": marketplace})

    def storygraph_login(self) -> dict[str, Any]:
        return self.engine.call("storygraph_login")

    def list_finished(self, source: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.engine.call("list_finished", {"source": source, "options": options})

    def sync_plan(self, source: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.engine.call(
            "sync_plan", {"source": source, "options": options, "headless": self.headless}
        )

    def progress(self) -> dict[str, Any]:
        # A cheap read of the in-flight job's [done, total]; the UI polls it while a long
        # call (sync_plan) runs. Bypasses the worker pipe, so it never blocks on that call.
        return {"ok": True, "value": self.engine.progress()}

    def choose_file(self) -> dict[str, Any]:
        # The native file picker is a GUI-thread operation pywebview marshals itself, so it
        # runs here (not in the worker) and JS can only reach it through this bridge method.
        try:
            return {"ok": True, "value": open_file_dialog()}
        except Exception as err:  # noqa: BLE001 - surfaced to the UI as a message
            return {"ok": False, "error": str(err), "errorType": type(err).__name__}
