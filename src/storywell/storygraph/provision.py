"""Make Playwright's Chromium available without the user touching a terminal.

A source/editable install expects the user to run ``playwright install chromium`` by
hand. That's a non-starter for a packaged desktop app aimed at non-technical users, so
these helpers let the app (or a CLI command) detect a missing browser and download it
once on first run. The browser lands in Playwright's normal cache, so a later ``sync``
reuses it.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .session import PlaywrightFactory, _load_sync_playwright

INSTALL_COMMAND: tuple[str, ...] = (sys.executable, "-m", "playwright", "install", "chromium")

Runner = Callable[..., Any]


def chromium_installed(*, playwright_factory: PlaywrightFactory | None = None) -> bool:
    """True if Playwright's Chromium browser is already downloaded.

    ``executable_path`` resolves to where Chromium *would* live whether or not it is
    installed, so the existence of that file is the real download check. Starting the
    Playwright driver to read it is cheap — it launches no browser.
    """
    factory = playwright_factory or _load_sync_playwright()
    with factory() as pw:
        return Path(pw.chromium.executable_path).exists()


def install_chromium(*, runner: Runner | None = None) -> bool:
    """Download Chromium via ``playwright install chromium``. Idempotent; returns success.

    Runs in the current interpreter so a packaged app uses its bundled Playwright rather
    than whatever ``playwright`` might be on PATH. Output is left to stream to the parent's
    stdout/stderr (the terminal for the CLI, the app log for the packaged GUI) so a failed
    download leaves a diagnosable trail instead of a bare ``False``.
    """
    run = runner or subprocess.run
    result = run(list(INSTALL_COMMAND))
    return result.returncode == 0
