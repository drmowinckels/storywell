import builtins
import json
import re
from pathlib import Path

import pytest

from storywell.stats import compute_all, load_export
from storywell.stats.render import (
    StatsDependencyError,
    _bar_chart,
    _column_chart,
    _donut,
    _lollipop,
    _stacked_bar,
    _taste_panel,
    render_dashboard,
    write_dashboard,
)

FIXTURE = Path(__file__).parent / "fixtures" / "storygraph_export_sample.csv"


@pytest.fixture
def data():
    return compute_all(load_export(FIXTURE))


def _embedded_json(html: str) -> dict:
    match = re.search(
        r'<script type="application/json" id="storywell-stats">(.*?)</script>', html, re.S
    )
    assert match, "embedded stats JSON not found"
    return json.loads(match.group(1))


def test_render_dashboard_is_a_complete_html_document(data):
    html = render_dashboard(data)
    assert html.startswith("<!DOCTYPE html>")
    assert "<title>Your StoryGraph reading stats</title>" in html
    assert html.rstrip().endswith("</html>")


def test_render_dashboard_shows_headline_numbers(data):
    html = render_dashboard(data)
    assert '<span class="num">6</span><span class="lbl">books read</span>' in html
    assert "4.42" in html  # average rating
    assert "finished in 2024" in html


def test_render_dashboard_embeds_the_full_stats_blob(data):
    blob = _embedded_json(render_dashboard(data))
    assert blob["summary"]["read_books"] == 6
    assert blob["ratings"]["mean"] == 4.42


def test_render_dashboard_has_no_external_asset_urls(data):
    html = render_dashboard(data)
    assert "http://" not in html
    assert "https://" not in html


def test_render_dashboard_escapes_user_content(tmp_path):
    export = tmp_path / "evil.csv"
    export.write_text(
        "Title,Read Status,Format,Dates Read,Star Rating\n"
        "<script>alert(1)</script>,read,ebook,2024/01/01-2024/01/05,4.0\n",
        encoding="utf-8",
    )
    html = render_dashboard(compute_all(load_export(export)))
    assert "<script>alert(1)</script>" not in html  # neither in the body nor the JSON blob
    assert "&lt;script&gt;alert(1)" in html  # present, but inert


def test_render_dashboard_requires_jinja(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "jinja2":
            raise ImportError("simulated missing jinja2")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(StatsDependencyError, match=r"\[stats\]"):
        render_dashboard({})


def test_write_dashboard_creates_file_and_returns_path(tmp_path, data):
    out = write_dashboard(data, tmp_path / "nested" / "stats.html")
    assert out.exists()
    assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_bar_chart_renders_labels_bars_and_values():
    svg = _bar_chart([("Fantasy", 5), ("Sci-Fi", 3)])
    assert svg.startswith("<svg")
    assert 'class="bar-label"' in svg and ">Fantasy</text>" in svg
    assert 'class="bar"' in svg
    assert 'class="bar-value"' in svg and ">5</text>" in svg


def test_bar_chart_escapes_labels():
    svg = _bar_chart([("<b>evil", 1)])
    assert "<b>evil" not in svg
    assert "&lt;b&gt;evil" in svg


def test_column_chart_renders_columns_with_value_and_axis_labels():
    svg = _column_chart([(2023, 1), (2024, 6)])
    assert 'class="bar"' in svg
    assert 'class="col-value"' in svg and ">6</text>" in svg
    assert 'class="col-label"' in svg and ">2024</text>" in svg


def test_donut_renders_ring_total_and_legend_with_percentages():
    out = _donut([("audiobook", 4), ("ebook", 1), ("physical book", 1)])
    assert out.startswith('<div class="donut-wrap">')
    assert 'class="chart donut"' in out
    assert 'class="donut-total"' in out and ">6</text>" in out  # total books
    assert '<ul class="legend">' in out
    assert "<b>4</b> (67%)" in out  # 4 of 6


def test_donut_draws_a_full_ring_for_a_single_category():
    out = _donut([("audiobook", 3)])
    assert "<circle" in out  # one closed ring, not an arc path
    assert '<path d="M' not in out


def test_stacked_bar_renders_segments_with_titles_and_legend():
    out = _stacked_bar([("fast", 2), ("medium", 3), ("slow", 1)])
    assert out.startswith('<div class="stacked">')
    assert 'class="chart stack"' in out
    assert "<title>fast: 2</title>" in out
    assert "<b>3</b> (50%)" in out  # 3 of 6


def test_lollipop_renders_stem_dot_and_value():
    svg = _lollipop([("Andy Weir", 3), ("Gabrielle Zevin", 1)])
    assert 'class="bar-label"' in svg and ">Andy Weir</text>" in svg
    assert 'class="stem"' in svg
    assert 'class="lolli"' in svg
    assert 'class="bar-value"' in svg and ">3</text>" in svg


@pytest.mark.parametrize("chart", [_bar_chart, _column_chart, _donut, _stacked_bar, _lollipop])
def test_charts_render_an_empty_state_for_no_data(chart):
    out = chart([])
    assert 'class="empty"' in out
    assert "No data yet" in out


def test_taste_panel_renders_the_split_and_trait_rates():
    fingerprint = {
        "character_or_plot": [["Character", 4], ["Plot", 2]],
        "strong_character_development": {"rated": 6, "yes": 5, "yes_rate": 0.83},
        "loveable_characters": {"rated": 6, "yes": 3, "yes_rate": 0.5},
        "diverse_characters": {"rated": 6, "yes": 4, "yes_rate": 0.67},
        "flawed_characters": {"rated": 6, "yes": 2, "yes_rate": 0.33},
    }
    out = _taste_panel(fingerprint)
    assert out.startswith('<div class="traits">')
    assert "Character- vs plot-driven" in out
    assert 'class="stacked"' in out  # the character/plot split
    assert "How often books had" in out
    assert "Strong character development" in out
    assert 'class="pct-fill"' in out
    assert "83%" in out  # strong_character_development yes_rate


def test_taste_panel_skips_unrated_traits():
    fingerprint = {
        "character_or_plot": [],
        "strong_character_development": {"rated": 0, "yes": 0, "yes_rate": None},
    }
    out = _taste_panel(fingerprint)
    assert 'class="empty"' in out
    assert "No data yet" in out


def test_taste_panel_is_empty_for_an_empty_fingerprint():
    assert 'class="empty"' in _taste_panel({})
