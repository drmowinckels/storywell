from typer.testing import CliRunner

from audible_storygraph_sync.cli import app
from audible_storygraph_sync.storygraph import StorygraphDependencyError

runner = CliRunner()


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
