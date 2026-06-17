import builtins
import json
import re
from pathlib import Path

import pytest

from storywell.stats import compute_all, load_export
from storywell.stats.render import StatsDependencyError, render_dashboard, write_dashboard

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
