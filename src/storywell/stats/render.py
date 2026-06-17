"""Render a computed stats blob into a single self-contained HTML dashboard.

Everything is inlined — CSS, server-rendered SVG charts, and the data itself — so the output
is one file the user can open offline, hand to a friend, or print to PDF with no network
requests and no data leaving their device. Each stat uses the chart type that fits its data
story (columns for trends/distributions, a donut for format share, a stacked bar for pace,
horizontal bars and a lollipop for ranked counts, percentage bars for the taste profile, a
calendar heatmap for when reading happened) — all hand-rendered SVG, no JS chart library, so
the numbers stay snapshot-testable. The full ``compute_all`` blob is also embedded as JSON for
the client-side filters that arrive in a later slice.

Categorical charts use the Okabe–Ito colourblind-safe palette; single-series charts use the
theme accent. Jinja2 (autoescaping on) renders the shell, so user free-text can't break the
page; the embedded JSON has ``<`` escaped so it can't close the script tag. Rendering lives
behind the optional ``[stats]`` extra: ``import jinja2`` is deferred so the base install (and
the text-summary path) never needs it.
"""

from __future__ import annotations

import html
import json
import math
from importlib.resources import files
from pathlib import Path

_TEMPLATE = (files(__package__) / "dashboard.html.jinja").read_text(encoding="utf-8")

# Okabe–Ito, colourblind-safe (orange first so it sits with the amber theme accent).
_PALETTE = ("#e69f00", "#56b4e9", "#009e73", "#cc79a7", "#d55e00", "#0072b2", "#f0e442")

_EMPTY = '<p class="empty">No data yet.</p>'


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


def _svg(width: float, height: float, body: str, *, klass: str = "chart") -> str:
    return (
        f'<svg class="{klass}" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="100%" height="{height:.0f}" role="img">{body}</svg>'
    )


def _bar_chart(pairs: list, *, label_width: int = 150, bar_area: int = 240, row_h: int = 28) -> str:
    """Ranked horizontal bars — best for many categories with long labels (moods)."""
    if not pairs:
        return _EMPTY
    max_val = max(value for _, value in pairs) or 1
    pad = 8
    rows = []
    for i, (label, value) in enumerate(pairs):
        mid = i * row_h + row_h / 2
        bar_w = value / max_val * bar_area
        rows.append(
            f'<text class="bar-label" x="{label_width - pad}" y="{mid}" '
            f'text-anchor="end" dominant-baseline="middle">{html.escape(str(label))}</text>'
            f'<rect class="bar" x="{label_width}" y="{i * row_h + 4}" '
            f'width="{bar_w:.1f}" height="{row_h - 9}" rx="3"/>'
            f'<text class="bar-value" x="{label_width + bar_w + pad}" y="{mid}" '
            f'dominant-baseline="middle">{html.escape(str(value))}</text>'
        )
    return _svg(label_width + bar_area + 48, len(pairs) * row_h, "".join(rows))


def _column_chart(pairs: list, *, bar_w: int = 48, gap: int = 20, height: int = 180) -> str:
    """Vertical columns — for a trend over few periods (years) or an ordinal distribution
    (ratings), where reading left-to-right as an axis is natural."""
    if not pairs:
        return _EMPTY
    max_val = max(value for _, value in pairs) or 1
    top_pad, bottom_pad = 22, 26
    plot_h = height - top_pad - bottom_pad
    cols = []
    for i, (label, value) in enumerate(pairs):
        x = i * (bar_w + gap)
        bar_h = value / max_val * plot_h
        y = top_pad + (plot_h - bar_h)
        cx = x + bar_w / 2
        cols.append(
            f'<rect class="bar" x="{x}" y="{y:.1f}" width="{bar_w}" height="{bar_h:.1f}" rx="3"/>'
            f'<text class="col-value" x="{cx}" y="{y - 6:.1f}" text-anchor="middle">'
            f"{html.escape(str(value))}</text>"
            f'<text class="col-label" x="{cx}" y="{height - 8}" text-anchor="middle">'
            f"{html.escape(str(label))}</text>"
        )
    return _svg(max(len(pairs) * (bar_w + gap) - gap, bar_w), height, "".join(cols))


def _polar(cx: float, cy: float, r: float, angle: float) -> tuple[float, float]:
    return cx + r * math.sin(angle), cy - r * math.cos(angle)


def _arc(cx: float, cy: float, r_out: float, r_in: float, a0: float, a1: float) -> str:
    x0o, y0o = _polar(cx, cy, r_out, a0)
    x1o, y1o = _polar(cx, cy, r_out, a1)
    x1i, y1i = _polar(cx, cy, r_in, a1)
    x0i, y0i = _polar(cx, cy, r_in, a0)
    large = 1 if (a1 - a0) > math.pi else 0
    return (
        f"M{x0o:.2f},{y0o:.2f} A{r_out:.2f},{r_out:.2f} 0 {large} 1 {x1o:.2f},{y1o:.2f} "
        f"L{x1i:.2f},{y1i:.2f} A{r_in:.2f},{r_in:.2f} 0 {large} 0 {x0i:.2f},{y0i:.2f} Z"
    )


def _legend(pairs: list, total: int) -> str:
    items = []
    for i, (label, value) in enumerate(pairs):
        pct = round(value / total * 100) if total else 0
        items.append(
            f'<li><span class="swatch" style="background:{_PALETTE[i % len(_PALETTE)]}"></span>'
            f"<span>{html.escape(str(label))} <b>{value}</b> ({pct}%)</span></li>"
        )
    return f'<ul class="legend">{"".join(items)}</ul>'


def _donut(pairs: list, *, size: int = 168, thickness: int = 34) -> str:
    """Part-to-whole for a few categories (formats) — a ring with a centred total."""
    if not pairs:
        return _EMPTY
    total = sum(value for _, value in pairs) or 1
    cx = cy = size / 2
    r_out = size / 2 - 2
    r_in = r_out - thickness
    r_mid = (r_out + r_in) / 2
    slices = []
    acc = 0.0
    for i, (_, value) in enumerate(pairs):
        frac = value / total
        color = _PALETTE[i % len(_PALETTE)]
        if frac >= 0.999:
            slices.append(
                f'<circle cx="{cx}" cy="{cy}" r="{r_mid:.2f}" fill="none" '
                f'stroke="{color}" stroke-width="{thickness}"/>'
            )
        else:
            a0, a1 = acc * 2 * math.pi, (acc + frac) * 2 * math.pi
            slices.append(f'<path d="{_arc(cx, cy, r_out, r_in, a0, a1)}" fill="{color}"/>')
        acc += frac
    center = (
        f'<text class="donut-total" x="{cx}" y="{cy - 2}" text-anchor="middle">{total}</text>'
        f'<text class="donut-sub" x="{cx}" y="{cy + 16}" text-anchor="middle">books</text>'
    )
    ring = _svg(size, size, "".join(slices) + center, klass="chart donut")
    return f'<div class="donut-wrap">{ring}{_legend(pairs, total)}</div>'


def _stacked_bar(pairs: list, *, width: int = 320, height: int = 22) -> str:
    """A single 100% bar split into ordinal segments (pace) — composition at a glance."""
    if not pairs:
        return _EMPTY
    total = sum(value for _, value in pairs) or 1
    x = 0.0
    segs = []
    for i, (label, value) in enumerate(pairs):
        seg_w = value / total * width
        color = _PALETTE[i % len(_PALETTE)]
        segs.append(
            f'<rect x="{x:.2f}" y="0" width="{seg_w:.2f}" height="{height}" fill="{color}">'
            f"<title>{html.escape(f'{label}: {value}')}</title></rect>"
        )
        x += seg_w
    bar = _svg(width, height, "".join(segs), klass="chart stack")
    return f'<div class="stacked">{bar}{_legend(pairs, total)}</div>'


def _lollipop(pairs: list, *, label_width: int = 150, area: int = 220, row_h: int = 26) -> str:
    """Ranked counts as a stem + dot — less ink than bars, good for a top-N list (authors)."""
    if not pairs:
        return _EMPTY
    max_val = max(value for _, value in pairs) or 1
    rows = []
    for i, (label, value) in enumerate(pairs):
        mid = i * row_h + row_h / 2
        x_end = label_width + value / max_val * area
        rows.append(
            f'<text class="bar-label" x="{label_width - 8}" y="{mid}" '
            f'text-anchor="end" dominant-baseline="middle">{html.escape(str(label))}</text>'
            f'<line class="stem" x1="{label_width}" y1="{mid}" x2="{x_end:.1f}" y2="{mid}"/>'
            f'<circle class="lolli" cx="{x_end:.1f}" cy="{mid}" r="5"/>'
            f'<text class="bar-value" x="{x_end + 9:.1f}" y="{mid}" '
            f'dominant-baseline="middle">{html.escape(str(value))}</text>'
        )
    return _svg(label_width + area + 40, len(pairs) * row_h, "".join(rows))


def _pct_bars(items: list) -> str:
    """Labelled percentage tracks (0–100%) — for the trait Yes-rates."""
    rows = []
    for label, frac in items:
        pct = round(frac * 100)
        rows.append(
            f'<div class="pct-row"><span class="pct-label">{html.escape(str(label))}</span>'
            f'<span class="pct-track"><span class="pct-fill" style="width:{pct}%"></span></span>'
            f'<span class="pct-val">{pct}%</span></div>'
        )
    return f'<div class="pct-list">{"".join(rows)}</div>'


_TRAITS = (
    ("Strong character development", "strong_character_development"),
    ("Loveable characters", "loveable_characters"),
    ("Diverse characters", "diverse_characters"),
    ("Flawed characters", "flawed_characters"),
)


def _taste_panel(fingerprint: dict) -> str:
    """Character-vs-plot split + how often books showed each character trait."""
    blocks = []
    driven = fingerprint.get("character_or_plot") or []
    if driven:
        blocks.append(
            '<div class="trait-block"><div class="trait-h">Character- vs plot-driven</div>'
            f"{_stacked_bar(driven)}</div>"
        )
    rated = [
        (label, fingerprint[key]["yes_rate"])
        for label, key in _TRAITS
        if fingerprint.get(key, {}).get("rated") and fingerprint[key]["yes_rate"] is not None
    ]
    if rated:
        blocks.append(
            '<div class="trait-block"><div class="trait-h">How often books had…</div>'
            f"{_pct_bars(rated)}</div>"
        )
    if not blocks:
        return _EMPTY
    return f'<div class="traits">{"".join(blocks)}</div>'


def _heat_color(intensity: float) -> str:
    if intensity <= 0:
        return "#222c39"
    lo, hi = (90, 70, 42), (232, 176, 104)
    rgb = tuple(round(lo[c] + (hi[c] - lo[c]) * intensity) for c in range(3))
    return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"


def _heatmap(calendar: list) -> str:
    """Year × month finish heatmap — when reading happened across the calendar."""
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
    return _svg(width, height, "".join(parts), klass="chart heatmap")


def _build_charts(data: dict) -> dict:
    ratings = [[f"{r:g}★", c] for r, c in data["ratings"]["distribution"]]
    return {
        "year": _column_chart(data["volume_pace"]["finishes_by_year"]),
        "rating": _column_chart(ratings),
        "heatmap": _heatmap(data["volume_pace"]["reading_calendar"]),
        "format": _donut(data["formats_authors"]["format_split"]),
        "pace": _stacked_bar(data["moods_taste"]["pace_split"]),
        "moods": _bar_chart(data["moods_taste"]["mood_frequency"][:12]),
        "authors": _lollipop(data["formats_authors"]["top_authors"][:10]),
        "taste": _taste_panel(data["moods_taste"]["fingerprint"]),
    }


def render_dashboard(data: dict, *, title: str = "Your StoryGraph reading stats") -> str:
    """Build the full self-contained dashboard HTML from a ``compute_all`` blob."""
    jinja2 = _require_jinja()
    charts = _build_charts(data)
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
