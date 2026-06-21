from typer.testing import CliRunner

from storywell.cli import app

runner = CliRunner()


def test_storygraph_install_already_installed(monkeypatch):
    monkeypatch.setattr("storywell.storygraph.chromium_installed", lambda: True)
    result = runner.invoke(app, ["storygraph-install"])
    assert result.exit_code == 0
    assert "already installed" in result.stdout


def test_storygraph_install_downloads_when_missing(monkeypatch):
    monkeypatch.setattr("storywell.storygraph.chromium_installed", lambda: False)
    monkeypatch.setattr("storywell.storygraph.install_chromium", lambda: True)
    result = runner.invoke(app, ["storygraph-install"])
    assert result.exit_code == 0
    assert "Chromium installed" in result.stdout


def test_storygraph_install_reports_failure(monkeypatch):
    monkeypatch.setattr("storywell.storygraph.chromium_installed", lambda: False)
    monkeypatch.setattr("storywell.storygraph.install_chromium", lambda: False)
    result = runner.invoke(app, ["storygraph-install"])
    assert result.exit_code == 1
    assert "failed" in result.stdout.lower()


def test_storygraph_install_reports_missing_playwright(monkeypatch):
    def boom():
        from storywell.storygraph import StorygraphDependencyError

        raise StorygraphDependencyError("install playwright first")

    monkeypatch.setattr("storywell.storygraph.chromium_installed", boom)
    result = runner.invoke(app, ["storygraph-install"])
    assert result.exit_code == 1
    assert "install playwright first" in result.stdout
