import json
from pathlib import Path

from typer.testing import CliRunner

from storywell.cli import app

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "storygraph_export_sample.csv"


def test_stats_prints_summary():
    result = runner.invoke(app, ["stats", "-f", str(FIXTURE)])
    assert result.exit_code == 0
    assert "StoryGraph reading stats" in result.stdout
    assert "Books read" in result.stdout


def test_stats_json_emits_parseable_blob():
    result = runner.invoke(app, ["stats", "-f", str(FIXTURE), "--json"])
    assert result.exit_code == 0
    blob = json.loads(result.stdout)
    assert blob["summary"]["read_books"] == 6
    assert blob["ratings"]["mean"] == 4.42


def test_stats_errors_on_non_storygraph_csv(tmp_path):
    bad = tmp_path / "books.csv"
    bad.write_text("Name,Pages\nDune,412\n", encoding="utf-8")
    result = runner.invoke(app, ["stats", "-f", str(bad)])
    assert result.exit_code == 1
    assert "StoryGraph library export" in result.stdout


def test_stats_errors_on_missing_file(tmp_path):
    result = runner.invoke(app, ["stats", "-f", str(tmp_path / "nope.csv")])
    assert result.exit_code != 0


def test_stats_warns_when_read_books_have_no_readable_dates(tmp_path):
    export = tmp_path / "library.csv"
    export.write_text(
        "Title,Read Status,Dates Read\nSome Book,read,sometime last year\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["stats", "-f", str(export)])
    assert result.exit_code == 0
    assert "no readable finish dates" in result.stdout
