from typer.testing import CliRunner

from audible_storygraph_sync.cli import _choose_candidate, app
from audible_storygraph_sync.models import Audiobook
from audible_storygraph_sync.storygraph import StorygraphDependencyError
from audible_storygraph_sync.storygraph.matching import Candidate, ScoredCandidate

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


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def mark_finished(self, book_id, finish_date=None):
        return True


def test_choose_candidate_picks_by_number():
    options = [
        ScoredCandidate(Candidate("b1", "T1", "A"), 0.7, 0.7, 0.7),
        ScoredCandidate(Candidate("b2", "T2", "A"), 0.6, 0.6, 0.6),
    ]
    assert _choose_candidate("2", options).book_id == "b2"


def test_choose_candidate_skip_and_invalid_return_none():
    options = [ScoredCandidate(Candidate("b1", "T1", "A"), 0.7, 0.7, 0.7)]
    assert _choose_candidate("s", options) is None
    assert _choose_candidate("9", options) is None
    assert _choose_candidate("", options) is None


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


def test_cli_sync_writes_matches(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "audible_storygraph_sync.cli.finished_audiobooks",
        lambda **kw: [Audiobook(asin="A1", title="Hyperion", authors=("Dan Simmons",))],
    )
    monkeypatch.setattr("audible_storygraph_sync.storygraph.is_authenticated", lambda: True)
    monkeypatch.setattr(
        "audible_storygraph_sync.storygraph.search.StorygraphSearcher", _FakeSearcher
    )
    monkeypatch.setattr("audible_storygraph_sync.storygraph.client.StorygraphClient", _FakeClient)
    monkeypatch.setattr(
        "audible_storygraph_sync.config.sync_store_path", lambda: tmp_path / "store.json"
    )
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "written: 1" in result.stdout
    assert (tmp_path / "store.json").exists()


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
