from datetime import UTC, datetime
from pathlib import Path

import pytest

from storywell.desktop import engine
from storywell.desktop.engine import (
    EngineClient,
    book_to_dict,
    dispatch,
    plan_item_to_dict,
    source_kwargs,
)
from storywell.models import Shelf, SourceBook
from storywell.storygraph import plan_sync
from storywell.storygraph.matching import Candidate


def _book(title="Hyperion", author="Dan Simmons"):
    return SourceBook(
        source="audible",
        source_id="A1",
        title=title,
        authors=(author,),
        percent_complete=100.0,
        finished_at=datetime(2024, 1, 2, tzinfo=UTC),
        rating=4.5,
    )


def test_book_to_dict_shape():
    assert book_to_dict(_book()) == {
        "key": "audible:A1",
        "title": "Hyperion",
        "authors": ["Dan Simmons"],
        "percentComplete": 100.0,
        "finishedAt": "2024-01-02",
        "rating": 4.5,
    }


def test_plan_item_to_dict_reports_best_match():
    items = plan_sync([_book()], lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")])
    data = plan_item_to_dict(items[0])
    assert data["title"] == "Hyperion"
    assert data["status"] == "match"
    assert data["match"] == "Hyperion"
    assert data["matchId"] == "b1"
    assert 0.0 <= data["score"] <= 1.0


def test_source_kwargs_maps_ui_options():
    assert source_kwargs(
        {"path": "/x/export.csv", "token": "tok", "readColumn": "#read", "shelf": "read"}
    ) == {
        "threshold": 0.95,
        "path": Path("/x/export.csv"),
        "token": "tok",
        "read_column": "#read",
        "shelf": Shelf.READ,
    }


def test_source_kwargs_defaults_when_empty():
    assert source_kwargs(None) == {
        "threshold": 0.95,
        "path": None,
        "token": None,
        "read_column": None,
        "shelf": None,
    }
    # empty strings from untouched inputs collapse to None, not ""
    assert source_kwargs({"path": "", "token": "", "readColumn": "", "shelf": ""})["token"] is None


def test_dispatch_sources(monkeypatch):
    monkeypatch.setattr("storywell.sources.available_sources", lambda: ["audible", "goodreads"])
    assert dispatch("sources", {}) == ["audible", "goodreads"]


def test_dispatch_list_finished_returns_book_dicts(monkeypatch):
    monkeypatch.setattr(engine.service, "list_finished", lambda *a, **k: [_book()])
    out = dispatch("list_finished", {"source": "audible", "options": None})
    assert out[0]["title"] == "Hyperion"


def test_dispatch_list_finished_threads_options(monkeypatch):
    captured = {}

    def fake(source, **kwargs):
        captured["source"] = source
        captured["kwargs"] = kwargs
        return [_book()]

    monkeypatch.setattr(engine.service, "list_finished", fake)
    dispatch("list_finished", {"source": "goodreads", "options": {"path": "/x/export.csv"}})
    assert captured["source"] == "goodreads"
    assert captured["kwargs"]["path"] == Path("/x/export.csv")
    assert captured["kwargs"]["token"] is None


def test_dispatch_sync_plan_returns_plan_and_summary(monkeypatch):
    items = plan_sync([_book()], lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")])
    monkeypatch.setattr(engine.service, "list_finished", lambda *a, **k: [_book()])
    monkeypatch.setattr(engine.service, "build_sync_plan", lambda books, **k: items)
    monkeypatch.setattr(
        engine.service, "summarize_plan", lambda its: {"match": 1, "ambiguous": 0, "no_match": 0}
    )
    out = dispatch("sync_plan", {"source": "audible", "options": None, "headless": True})
    assert out["summary"] == {"match": 1, "ambiguous": 0, "no_match": 0}
    assert out["plan"][0]["status"] == "match"


def test_dispatch_sync_plan_threads_headless(monkeypatch):
    captured = {}
    monkeypatch.setattr(engine.service, "list_finished", lambda *a, **k: [_book()])

    def fake_plan(books, **k):
        captured["headless"] = k.get("headless")
        return []

    monkeypatch.setattr(engine.service, "build_sync_plan", fake_plan)
    monkeypatch.setattr(engine.service, "summarize_plan", lambda its: {})
    dispatch("sync_plan", {"source": "audible", "options": None, "headless": False})
    assert captured["headless"] is False


def test_dispatch_sync_plan_forwards_progress_callback(monkeypatch):
    seen = []
    monkeypatch.setattr(engine.service, "list_finished", lambda *a, **k: [_book(), _book()])

    def fake_plan(books, *, headless, on_progress=None):
        if on_progress is not None:
            on_progress(1, 2)
            on_progress(2, 2)
        return []

    monkeypatch.setattr(engine.service, "build_sync_plan", fake_plan)
    monkeypatch.setattr(engine.service, "summarize_plan", lambda its: {})
    dispatch(
        "sync_plan",
        {"source": "audible", "options": None, "headless": True},
        on_progress=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(1, 2), (2, 2)]


def test_dispatch_login_state_passes_headless(monkeypatch):
    captured = {}

    def fake(*, headless):
        captured["headless"] = headless
        return True

    monkeypatch.setattr(engine.service, "login_state", fake)
    assert dispatch("login_state", {"headless": True}) is True
    assert captured["headless"] is True


def test_dispatch_saved_logins(monkeypatch):
    monkeypatch.setattr(
        engine.service, "saved_logins", lambda: {"storygraph": True, "audible": False}
    )
    assert dispatch("saved_logins", {}) == {"storygraph": True, "audible": False}


def test_dispatch_forget_login_passes_service_name(monkeypatch):
    captured = {}

    def fake(name):
        captured["name"] = name
        return True

    monkeypatch.setattr(engine.service, "forget_login", fake)
    assert dispatch("forget_login", {"service_name": "audible"}) is True
    assert captured["name"] == "audible"


def test_dispatch_chromium_status(monkeypatch):
    monkeypatch.setattr(engine.service, "chromium_installed", lambda: True)
    assert dispatch("chromium_status", {}) is True


def test_dispatch_install_browser(monkeypatch):
    monkeypatch.setattr(engine.service, "install_chromium", lambda: True)
    assert dispatch("install_browser", {}) is True


def test_dispatch_audible_login(monkeypatch):
    monkeypatch.setattr(engine.service, "audible_login", lambda mp: f"/cfg/{mp}.json")
    assert dispatch("audible_login", {"marketplace": "us"}) == "/cfg/us.json"


def test_dispatch_storygraph_login(monkeypatch):
    monkeypatch.setattr(engine.service, "storygraph_login", lambda: "/cfg/sg.json")
    assert dispatch("storygraph_login", {}) == "/cfg/sg.json"


def test_dispatch_unknown_method_raises():
    with pytest.raises(ValueError, match="unknown engine method"):
        dispatch("nope", {})


def test_envelope_wraps_success(monkeypatch):
    monkeypatch.setattr(
        engine.service, "saved_logins", lambda: {"storygraph": False, "audible": False}
    )
    assert engine._envelope("saved_logins", {}) == {
        "ok": True,
        "value": {"storygraph": False, "audible": False},
    }


def test_envelope_wraps_error():
    res = engine._envelope("nope", {})
    assert res == {
        "ok": False,
        "error": "unknown engine method: 'nope'",
        "errorType": "ValueError",
    }


# --- real spawned-worker integration: this is the macOS fork-safety fix in action ---


def test_engine_client_round_trip_via_spawned_worker():
    # Full spawn + Pipe round-trip on a cheap, local method (no network/credentials).
    client = EngineClient()
    try:
        res = client.call("sources")
        assert res["ok"] is True
        assert "audible" in res["value"]
    finally:
        client.shutdown()


def test_engine_client_surfaces_worker_errors_as_envelope():
    client = EngineClient()
    try:
        res = client.call("definitely-not-a-method")
        assert res["ok"] is False
        assert res["errorType"] == "ValueError"
    finally:
        client.shutdown()


def test_engine_client_restarts_worker_after_shutdown():
    client = EngineClient()
    try:
        assert client.call("sources")["ok"] is True
        client.shutdown()
        assert client.call("sources")["ok"] is True  # lazily respawns
    finally:
        client.shutdown()


def test_engine_client_progress_starts_at_zero():
    client = EngineClient()
    try:
        assert client.progress() == {"done": 0, "total": 0}
    finally:
        client.shutdown()
