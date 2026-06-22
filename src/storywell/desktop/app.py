"""Native window host for the desktop GUI.

Wires the :class:`~storywell.desktop.bridge.Api` into a pywebview window pointing at the
bundled web UI. This is the only piece that imports pywebview and owns no engine
logic, so it stays a thin, hard-to-unit-test shell; the testable surface lives in
:mod:`storywell.desktop.bridge` and :mod:`storywell.service`.
"""

from __future__ import annotations

from pathlib import Path

from .bridge import Api

WINDOW_TITLE = "Storywell"
INDEX_HTML = Path(__file__).parent / "web" / "index.html"


class DesktopDependencyError(RuntimeError):
    pass


def _load_webview() -> object:
    try:
        import webview
    except ImportError as err:
        raise DesktopDependencyError(
            "The desktop GUI needs pywebview. Install it with:\n  pip install 'storywell[desktop]'"
        ) from err
    return webview


def run(*, headless: bool = True, debug: bool = False) -> None:
    """Open the Storywell desktop window.

    ``headless`` is forwarded to the engine (it controls the *StoryGraph* automation
    browser, not this window): True keeps the automation invisible; False shows it for
    login or debugging.
    """
    webview = _load_webview()
    webview.create_window(
        WINDOW_TITLE,
        url=INDEX_HTML.resolve().as_uri(),
        js_api=Api(headless=headless),
        width=920,
        height=720,
        min_size=(640, 480),
    )
    webview.start(debug=debug)
