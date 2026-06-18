from datetime import date

import pytest

from storywell.storygraph.store import SyncStore, sync_marker


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


def test_sync_marker_prefers_date_over_status():
    assert sync_marker(date(2023, 8, 18), "read") == "2023-08-18"
    assert sync_marker(date(2023, 8, 18), None) == "2023-08-18"


def test_sync_marker_keys_dateless_non_read_book_on_shelf():
    assert sync_marker(None, "to-read") == "shelf:to-read"
    assert sync_marker(None, "currently-reading") == "shelf:currently-reading"


def test_sync_marker_dateless_read_keeps_legacy_empty_marker():
    # a dateless read IS the old statusless case; reusing "" avoids a needless re-scan
    # of already-synced reads when upgrading a pre-shelf-routing store.
    assert sync_marker(None, "read") == ""
    assert sync_marker(None, None) == ""
    assert sync_marker(None) == ""


def test_dateless_shelf_idempotency_keys_on_status(tmp_path):
    store = SyncStore.load(tmp_path / "s.json")
    store.record("A1", "b1", None, "to-read")
    assert store.is_synced("A1", None, "to-read") is True
    # a different shelf for the same book is not "already synced"
    assert store.is_synced("A1", None, "currently-reading") is False
    # a dateless read is also distinct from a dateless to-read
    assert store.is_synced("A1", None, "read") is False


def test_legacy_dateless_marker_still_matches_statusless_query(tmp_path):
    # pre-shelf-routing stores recorded dateless reads as "" — that must stay valid.
    store = SyncStore.load(tmp_path / "s.json")
    store.record("A1", "b1", None)
    assert store.is_synced("A1", None) is True
    assert store.is_synced("A1", None, None) is True


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


@pytest.mark.parametrize("payload", ["[]", "42", '"hello"', "null", '{"mappings": []}'])
def test_load_tolerates_valid_json_of_wrong_shape(tmp_path, payload):
    path = tmp_path / "s.json"
    path.write_text(payload)
    store = SyncStore.load(path)
    assert store.mappings == {}
    assert store.synced == {}
    assert store.rated == {}


def test_record_rated_and_is_rated(tmp_path):
    store = SyncStore.load(tmp_path / "s.json")
    assert store.is_rated("audible:A1") is False
    store.record_rated("audible:A1")
    assert store.is_rated("audible:A1") is True


def test_rated_persists_across_reload(tmp_path):
    path = tmp_path / "s.json"
    store = SyncStore.load(path)
    store.record_rated("audible:A1")
    store.save()
    assert SyncStore.load(path).is_rated("audible:A1") is True
