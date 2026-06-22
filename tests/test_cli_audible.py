from typer.testing import CliRunner

from storywell.cli import app
from storywell.sources.audible_auth import AudibleLoginError

runner = CliRunner()


def test_audible_login_saves_session(monkeypatch, tmp_path):
    saved = tmp_path / "audible.json"
    monkeypatch.setattr("storywell.sources.audible_auth.audible_login", lambda mp: saved)
    result = runner.invoke(app, ["audible-login", "-m", "uk"])
    assert result.exit_code == 0
    assert "Saved Audible login" in result.stdout


def test_audible_login_reports_error(monkeypatch):
    def boom(mp):
        raise AudibleLoginError("Unknown Audible marketplace 'zz'.")

    monkeypatch.setattr("storywell.sources.audible_auth.audible_login", boom)
    result = runner.invoke(app, ["audible-login", "-m", "zz"])
    assert result.exit_code == 1
    assert "Unknown Audible marketplace" in result.stdout
