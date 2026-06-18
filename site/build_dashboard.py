"""Pre-render step for the docs site: build the dashboard assets.

Quarto runs this before rendering (see ``project.pre-render`` in _quarto.yml). It produces
everything the Dashboard page needs, all under ``assets/dashboard/`` (a build artifact, not
committed to git):

- ``example.html`` — the sample StoryGraph export rendered with the real storywell.stats
  pipeline (the same self-contained HTML the CLI emits), embedded in an ``<iframe>``.
- ``storywell-<version>-py3-none-any.whl`` — a pure-Python wheel that the page installs into
  Pyodide so a visitor's own export is rendered in their browser, no server involved.
- ``manifest.json`` — points the page's JavaScript at the wheel filename.

Run standalone with the project venv to refresh it locally:

    .venv/bin/python site/build_dashboard.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from storywell import __version__
from storywell.stats import compute_all, load_export
from storywell.stats.render import write_dashboard

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
SAMPLE = HERE / "sample-export.csv"
ASSETS = HERE / "assets" / "dashboard"


def build_example() -> None:
    out = ASSETS / "example.html"
    data = compute_all(load_export(SAMPLE))
    write_dashboard(data, out, title="Example reading stats")
    print(f"[build_dashboard] wrote {out.relative_to(HERE)} ({out.stat().st_size:,} bytes)")


def build_wheel() -> str:
    """Build storywell's wheel into the assets dir and return its filename.

    ``--no-deps`` keeps it to just storywell (pure Python); the browser pulls jinja2 via
    micropip at runtime. ``--no-build-isolation`` would need build deps preinstalled, so we
    let pip provision them.
    """
    ASSETS.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(REPO_ROOT), "--no-deps", "-w", str(ASSETS)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    wheel = max(ASSETS.glob("storywell-*.whl"), key=lambda p: p.stat().st_mtime)
    print(f"[build_dashboard] built {wheel.name}")
    return wheel.name


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    build_example()
    wheel = build_wheel()
    manifest = ASSETS / "manifest.json"
    manifest.write_text(json.dumps({"wheel": wheel, "version": __version__}), encoding="utf-8")
    print(f"[build_dashboard] wrote {manifest.relative_to(HERE)}")


if __name__ == "__main__":
    main()
