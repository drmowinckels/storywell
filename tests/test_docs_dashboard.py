from pathlib import Path

from storywell.stats import compute_all, load_export
from storywell.stats.render import render_dashboard

SAMPLE = Path(__file__).resolve().parents[1] / "site" / "sample-export.csv"


def test_site_sample_export_parses_every_book():
    # Guards the docs demo data: an unquoted comma in a title silently drops books,
    # which would make the example dashboard on the site under-count.
    data = compute_all(load_export(SAMPLE))
    assert data["summary"]["read_books"] == 22


def test_site_sample_export_renders_a_dashboard_with_charts():
    data = compute_all(load_export(SAMPLE))
    html = render_dashboard(data, title="Example reading stats")
    assert html.startswith("<!DOCTYPE html>")
    for marker in ("donut-wrap", 'class="stat"', 'class="stem"'):
        assert marker in html
