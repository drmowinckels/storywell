from typer.testing import CliRunner

from audible_storygraph_sync.cli import app
from audible_storygraph_sync.models import Audiobook
from audible_storygraph_sync.storygraph import StorygraphDependencyError
from audible_storygraph_sync.storygraph.matching import Candidate

runner = CliRunner()


class _FakeSearcher:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def search(self, query):
        return [Candidate("b1", "Hyperion", "Dan Simmons")]


def test_cli_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "audible-storygraph-sync" in result.stdout


def test_cli_storygraph_login_success(monkeypatch, tmp_path):
    saved = tmp_path / "state.json"
    monkeypatch.setattr("audible_storygraph_sync.storygraph.login", lambda: saved)
    result = runner.invoke(app, ["storygraph-login"])
    assert result.exit_code == 0
    assert "Saved StoryGraph session" in result.stdout


def test_cli_storygraph_login_dependency_error(monkeypatch):
    def boom():
        raise StorygraphDependencyError("install playwright first")

    monkeypatch.setattr("audible_storygraph_sync.storygraph.login", boom)
    result = runner.invoke(app, ["storygraph-login"])
    assert result.exit_code == 1
    assert "install playwright first" in result.stdout


def test_cli_storygraph_status_active(monkeypatch):
    monkeypatch.setattr("audible_storygraph_sync.storygraph.is_authenticated", lambda: True)
    result = runner.invoke(app, ["storygraph-status"])
    assert result.exit_code == 0
    assert "active" in result.stdout.lower()


def test_cli_storygraph_status_inactive(monkeypatch):
    monkeypatch.setattr("audible_storygraph_sync.storygraph.is_authenticated", lambda: False)
    result = runner.invoke(app, ["storygraph-status"])
    assert result.exit_code == 1
    assert "storygraph-login" in result.stdout


def test_cli_sync_no_dry_run_is_blocked():
    result = runner.invoke(app, ["sync", "--no-dry-run"])
    assert result.exit_code == 1
    assert "not implemented" in result.stdout.lower()


def test_cli_sync_no_finished_books(monkeypatch):
    monkeypatch.setattr("audible_storygraph_sync.cli.finished_audiobooks", lambda **kw: [])
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "No finished audiobooks" in result.stdout


def test_cli_sync_requires_session(monkeypatch):
    monkeypatch.setattr(
        "audible_storygraph_sync.cli.finished_audiobooks",
        lambda **kw: [Audiobook(asin="A", title="X", authors=())],
    )
    monkeypatch.setattr("audible_storygraph_sync.storygraph.is_authenticated", lambda: False)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1
    assert "storygraph-login" in result.stdout


def test_cli_sync_dry_run_reports_matches(monkeypatch):
    monkeypatch.setattr(
        "audible_storygraph_sync.cli.finished_audiobooks",
        lambda **kw: [Audiobook(asin="A", title="Hyperion", authors=("Dan Simmons",))],
    )
    monkeypatch.setattr("audible_storygraph_sync.storygraph.is_authenticated", lambda: True)
    monkeypatch.setattr(
        "audible_storygraph_sync.storygraph.search.StorygraphSearcher", _FakeSearcher
    )
    result = runner.invoke(app, ["sync", "--dry-run"])
    assert result.exit_code == 0
    assert "match:" in result.stdout.lower()
