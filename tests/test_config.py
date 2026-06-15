from pathlib import Path

from audible_storygraph_sync import config


def test_config_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.config_dir() == tmp_path / "audible-storygraph-sync"


def test_config_dir_defaults_to_home_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert config.config_dir() == tmp_path / ".config" / "audible-storygraph-sync"


def test_ensure_config_dir_creates_with_secure_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    created = config.ensure_config_dir()
    assert created.is_dir()
    assert (created.stat().st_mode & 0o777) == 0o700


def test_storygraph_state_path_under_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.storygraph_state_path() == (
        tmp_path / "audible-storygraph-sync" / "storygraph-state.json"
    )
