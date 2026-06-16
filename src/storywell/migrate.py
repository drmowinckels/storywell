"""One-time migration from the pre-rename ``audible-storygraph-sync`` config.

The rename moved the config dir (``audible-storygraph-sync`` -> ``storywell``) and
namespaced sync-store keys (bare ASIN -> ``audible:<asin>``). This carries the saved
StoryGraph session and sync history forward so a long-running sync isn't lost. It is
non-destructive: the legacy files are left untouched.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path

from .config import (
    STORYGRAPH_STATE_FILENAME,
    SYNC_STORE_FILENAME,
    config_dir,
    ensure_config_dir,
    storygraph_state_path,
    sync_store_path,
)

LEGACY_APP_DIR = "audible-storygraph-sync"
LEGACY_SOURCE_PREFIX = "audible"


@dataclass
class MigrationReport:
    state_migrated: bool = False
    store_migrated: bool = False
    mappings: int = 0
    synced: int = 0


def legacy_dir() -> Path:
    return config_dir().parent / LEGACY_APP_DIR


def reprefix_store(data: dict) -> dict:
    """Namespace legacy bare-ASIN keys under the audible source.

    Tolerant of a corrupt or wrong-shaped legacy file: a non-dict at any level
    degrades to an empty section rather than crashing the migration.
    """
    data = data if isinstance(data, dict) else {}

    def section(name: str) -> dict:
        value = data.get(name, {})
        return value if isinstance(value, dict) else {}

    def prefix(values: dict) -> dict:
        return {f"{LEGACY_SOURCE_PREFIX}:{key}": value for key, value in values.items()}

    return {
        "mappings": prefix(section("mappings")),
        "synced": prefix(section("synced")),
    }


def _secure(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def migrate_legacy() -> MigrationReport:
    """Copy the legacy session and reprefix the legacy sync-store into the new dir.

    Skips either file if its destination already exists, so re-running is safe and a
    fresh Storywell install is never clobbered.
    """
    report = MigrationReport()
    legacy = legacy_dir()
    if not legacy.exists():
        return report

    ensure_config_dir()

    legacy_state = legacy / STORYGRAPH_STATE_FILENAME
    new_state = storygraph_state_path()
    if legacy_state.exists() and not new_state.exists():
        new_state.write_bytes(legacy_state.read_bytes())
        _secure(new_state)
        report.state_migrated = True

    legacy_store = legacy / SYNC_STORE_FILENAME
    new_store = sync_store_path()
    if legacy_store.exists() and not new_store.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            migrated = reprefix_store(json.loads(legacy_store.read_text()))
            new_store.write_text(json.dumps(migrated, indent=2, sort_keys=True))
            _secure(new_store)
            report.store_migrated = True
            report.mappings = len(migrated["mappings"])
            report.synced = len(migrated["synced"])

    return report
