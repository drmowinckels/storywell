"""Pre-render step for the docs site: build the example reading-stats dashboard.

Quarto runs this before rendering (see ``project.pre-render`` in _quarto.yml). It turns the
bundled sample StoryGraph export into the same self-contained HTML dashboard the CLI emits,
which dashboard.qmd then embeds in an ``<iframe>``. Output lives under ``assets/`` (Quarto
ignores ``_``-prefixed paths) and is a build artifact, not committed to git.

Run standalone with the project venv to refresh it locally:

    .venv/bin/python site/build_dashboard.py
"""

from __future__ import annotations

from pathlib import Path

from storywell.stats import compute_all, load_export
from storywell.stats.render import write_dashboard

HERE = Path(__file__).parent
SAMPLE = HERE / "sample-export.csv"
OUT = HERE / "assets" / "dashboard" / "example.html"


def main() -> None:
    entries = load_export(SAMPLE)
    data = compute_all(entries)
    write_dashboard(data, OUT, title="Example reading stats")
    print(f"[build_dashboard] wrote {OUT.relative_to(HERE)} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
