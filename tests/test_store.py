from datetime import date

from storywell.storygraph.store import SyncStore


def test_load_missing_file_gives_empty_store(tmp_path):
    store = SyncStore.load(tmp_path / "none.json")
    assert store.mappings == {}
    assert store.synced == {}


def test_record_marks_synced_and_caches_book_id(tmp_path):
    store = SyncStore.load(tmp_path / "s.json")
    store.record("A1", "b1", date(2023, 8, 18))
    assert store.cached_book_id("A1") == "b1"
    assert store.is_synced("A1", date(2023, 8, 18)) is True


def test_is_synced_false_when_finish_date_changed(tmp_path):
    store = SyncStore.load(tmp_path / "s.json")
    store.record("A1", "b1", date(2023, 8, 18))
    assert store.is_synced("A1", date(2024, 1, 1)) is False


def test_is_synced_handles_none_date(tmp_path):
    store = SyncStore.load(tmp_path / "s.json")
    store.record("A1", "b1", None)
    assert store.is_synced("A1", None) is True


def test_remembered_match_is_cached_but_not_synced(tmp_path):
    store = SyncStore.load(tmp_path / "s.json")
    store.remember_match("A2", "b2")
    assert store.cached_book_id("A2") == "b2"
    assert store.is_synced("A2", None) is False


def test_save_and_reload_persists(tmp_path):
    path = tmp_path / "s.json"
    store = SyncStore.load(path)
    store.record("A1", "b1", date(2020, 1, 1))
    store.remember_match("A2", "b2")
    store.save()

    reloaded = SyncStore.load(path)
    assert reloaded.cached_book_id("A1") == "b1"
    assert reloaded.cached_book_id("A2") == "b2"
    assert reloaded.is_synced("A1", date(2020, 1, 1)) is True


def test_save_sets_secure_permissions(tmp_path):
    path = tmp_path / "s.json"
    store = SyncStore.load(path)
    store.save()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_load_tolerates_corrupt_json(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{ not valid json")
    store = SyncStore.load(path)
    assert store.mappings == {}
    assert store.synced == {}
