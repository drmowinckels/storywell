from __future__ import annotations

import contextlib
import os
from pathlib import Path

APP_DIR_NAME = "storywell"
STORYGRAPH_STATE_FILENAME = "storygraph-state.json"
SYNC_STORE_FILENAME = "sync-store.json"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_DIR_NAME


def ensure_config_dir() -> Path:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        directory.chmod(0o700)
    return directory


def storygraph_state_path() -> Path:
    return config_dir() / STORYGRAPH_STATE_FILENAME


def sync_store_path() -> Path:
    return config_dir() / SYNC_STORE_FILENAME
