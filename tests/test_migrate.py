import json

from storywell.migrate import LEGACY_APP_DIR, migrate_legacy, reprefix_store


def test_reprefix_store_namespaces_keys_under_audible():
    out = reprefix_store({"mappings": {"B01": "sg1"}, "synced": {"B01": "2023-08-18"}})
    assert out == {
        "mappings": {"audible:B01": "sg1"},
        "synced": {"audible:B01": "2023-08-18"},
    }


def test_reprefix_store_tolerates_missing_sections():
    assert reprefix_store({}) == {"mappings": {}, "synced": {}}


def _write_legacy(tmp_path, *, state=True, store=True):
    legacy = tmp_path / LEGACY_APP_DIR
    legacy.mkdir(parents=True)
    if state:
        (legacy / "storygraph-state.json").write_text('{"cookies": [1]}')
    if store:
        (legacy / "sync-store.json").write_text(
            json.dumps({"mappings": {"B01": "sg1"}, "synced": {"B01": "2023-08-18"}})
        )
    return legacy


def test_migrate_legacy_copies_session_and_reprefixes_store(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    legacy = _write_legacy(tmp_path)

    report = migrate_legacy()

    assert report.state_migrated is True
    assert report.store_migrated is True
    assert report.mappings == 1
    assert report.synced == 1

    new_dir = tmp_path / "storywell"
    assert (new_dir / "storygraph-state.json").read_text() == '{"cookies": [1]}'
    store = json.loads((new_dir / "sync-store.json").read_text())
    assert store["mappings"] == {"audible:B01": "sg1"}
    assert store["synced"] == {"audible:B01": "2023-08-18"}
    # non-destructive
    assert (legacy / "sync-store.json").exists()


def test_migrate_legacy_no_legacy_dir_is_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    report = migrate_legacy()
    assert report.state_migrated is False
    assert report.store_migrated is False


def test_migrate_legacy_does_not_clobber_existing(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_legacy(tmp_path)
    new_dir = tmp_path / "storywell"
    new_dir.mkdir(parents=True)
    (new_dir / "sync-store.json").write_text('{"mappings": {"audible:keep": "x"}, "synced": {}}')

    report = migrate_legacy()

    assert report.store_migrated is False
    store = json.loads((new_dir / "sync-store.json").read_text())
    assert store["mappings"] == {"audible:keep": "x"}
