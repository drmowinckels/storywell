"""Render a computed stats blob into a single self-contained HTML dashboard.

Everything is inlined — CSS, server-rendered SVG charts, and the data itself — so the output
is one file the user can open offline, hand to a friend, or print to PDF with no network
requests and no data leaving their device. Charts are emitted as plain SVG (no JS library) so
the file is meaningful without scripts and the numbers are snapshot-testable; the full
``compute_all`` blob is also embedded as JSON for the client-side filters that arrive in a
later slice. Jinja2 (autoescaping on) renders the shell, so user free-text (titles, authors)
can't break the page; the embedded JSON has ``<`` escaped so it can't close the script tag.

Rendering lives behind the optional ``[stats]`` extra: ``import jinja2`` is deferred so the
base install (and the text-summary path) never needs it.
"""

from __future__ import annotations

import html
import json
from importlib.resources import files
from pathlib import Path

_TEMPLATE = (files(__package__) / "dashboard.html.jinja").read_text(encoding="utf-8")


class StatsDependencyError(RuntimeError):
    pass


def _require_jinja():
    try:
        import jinja2
    except ImportError as err:
        raise StatsDependencyError(
            "Jinja2 is required to render the HTML dashboard but is not installed.\n"
            "Install it with:\n"
            "  pipx inject storywell jinja2\n"
            "  (or: pip install 'storywell[stats]')"
        ) from err
    return jinja2


def _bar_chart(pairs: list, *, label_width: int = 150, bar_area: int = 240, row_h: int = 28) -> str:
    """Horizontal bar chart as inline SVG. Labels are escaped; bars/text are CSS-themed."""
    if not pairs:
        return '<p class="empty">No data yet.</p>'
    max_val = max(value for _, value in pairs) or 1
    pad = 8
    rows = []
    for i, (label, value) in enumerate(pairs):
        mid = i * row_h + row_h / 2
        bar_w = (value / max_val) * bar_area
        text = html.escape(str(label))
        rows.append(
            f'<text class="bar-label" x="{label_width - pad}" y="{mid}" '
            f'text-anchor="end" dominant-baseline="middle">{text}</text>'
            f'<rect class="bar" x="{label_width}" y="{i * row_h + 4}" '
            f'width="{bar_w:.1f}" height="{row_h - 9}" rx="3"/>'
            f'<text class="bar-value" x="{label_width + bar_w + pad}" y="{mid}" '
            f'dominant-baseline="middle">{html.escape(str(value))}</text>'
        )
    height = len(pairs) * row_h
    width = label_width + bar_area + 48
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" '
        f'height="{height}" role="img">' + "".join(rows) + "</svg>"
    )


def _heat_color(intensity: float) -> str:
    if intensity <= 0:
        return "#222c39"
    lo, hi = (90, 70, 42), (232, 176, 104)
    rgb = tuple(round(lo[c] + (hi[c] - lo[c]) * intensity) for c in range(3))
    return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"


def _heatmap(calendar: list) -> str:
    """Year-by-month finish heatmap as inline SVG."""
    if not calendar:
        return '<p class="empty">No dated reads yet.</p>'
    counts: dict[tuple[int, int], int] = {}
    for ym, count in calendar:
        year, month = ym.split("-")
        counts[(int(year), int(month))] = count
    years = sorted({year for year, _ in counts})
    max_count = max(counts.values()) or 1
    cell, gap, left, top = 26, 4, 48, 20
    months = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]
    parts = [
        f'<text class="hm-axis" x="{left + m * (cell + gap) + cell / 2}" y="{top - 7}" '
        f'text-anchor="middle">{months[m]}</text>'
        for m in range(12)
    ]
    for r, year in enumerate(years):
        yy = top + r * (cell + gap)
        parts.append(
            f'<text class="hm-axis" x="{left - 9}" y="{yy + cell / 2}" '
            f'text-anchor="end" dominant-baseline="middle">{year}</text>'
        )
        for month in range(1, 13):
            count = counts.get((year, month), 0)
            fill = _heat_color(count / max_count if count else 0)
            xx = left + (month - 1) * (cell + gap)
            label = html.escape(f"{year}-{month:02d}: {count}")
            parts.append(
                f'<rect class="hm-cell" x="{xx}" y="{yy}" width="{cell}" height="{cell}" '
                f'rx="4" fill="{fill}"><title>{label}</title></rect>'
            )
    width = left + 12 * (cell + gap) + 12
    height = top + len(years) * (cell + gap) + 8
    return (
        f'<svg class="chart heatmap" viewBox="0 0 {width} {height}" width="100%" '
        f'height="{height}" role="img">' + "".join(parts) + "</svg>"
    )


def render_dashboard(data: dict, *, title: str = "Your StoryGraph reading stats") -> str:
    """Build the full self-contained dashboard HTML from a ``compute_all`` blob."""
    jinja2 = _require_jinja()
    charts = {
        "finishes_by_year": _bar_chart(data["volume_pace"]["finishes_by_year"]),
        "heatmap": _heatmap(data["volume_pace"]["reading_calendar"]),
        "rating": _bar_chart([[f"{r}★", c] for r, c in data["ratings"]["distribution"]]),
        "format": _bar_chart(data["formats_authors"]["format_split"]),
        "pace": _bar_chart(data["moods_taste"]["pace_split"]),
        "moods": _bar_chart(data["moods_taste"]["mood_frequency"][:12]),
        "authors": _bar_chart(data["formats_authors"]["top_authors"][:10]),
    }
    # <type="application/json"> only ends on "</script>"; escaping "<" makes the blob inert.
    data_json = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    env = jinja2.Environment(
        autoescape=jinja2.select_autoescape(["html", "xml"]),
        undefined=jinja2.StrictUndefined,
    )
    return env.from_string(_TEMPLATE).render(
        title=title,
        summary=data["summary"],
        pace=data["volume_pace"]["reading_pace"],
        charts=charts,
        data_json=data_json,
    )


def write_dashboard(
    data: dict, path: Path | str, *, title: str = "Your StoryGraph reading stats"
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard(data, title=title), encoding="utf-8")
    return path
