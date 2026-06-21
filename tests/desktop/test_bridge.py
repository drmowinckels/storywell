import threading
from datetime import UTC, datetime

from storywell.desktop import bridge
from storywell.desktop.bridge import Api, book_to_dict, plan_item_to_dict, run_off_thread
from storywell.models import SourceBook
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


def test_run_off_thread_runs_on_a_worker_thread():
    main = threading.get_ident()
    result = run_off_thread(lambda: threading.get_ident())
    assert result.ok
    assert result.value != main


def test_run_off_thread_captures_exception_as_result():
    def boom():
        raise ValueError("nope")

    result = run_off_thread(boom)
    assert result.ok is False
    assert result.error == "nope"
    assert result.error_type == "ValueError"


def test_book_to_dict_shape():
    data = book_to_dict(_book())
    assert data == {
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


def test_api_sources_envelope(monkeypatch):
    monkeypatch.setattr("storywell.sources.available_sources", lambda: ["audible", "goodreads"])
    res = Api().sources()
    assert res == {"ok": True, "value": ["audible", "goodreads"]}


def test_api_list_finished_returns_book_dicts(monkeypatch):
    monkeypatch.setattr(bridge.service, "list_finished", lambda *a, **k: [_book()])
    res = Api().list_finished("audible")
    assert res["ok"]
    assert res["value"][0]["title"] == "Hyperion"


def test_api_list_finished_surfaces_errors_as_envelope(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("source exploded")

    monkeypatch.setattr(bridge.service, "list_finished", boom)
    res = Api().list_finished("audible")
    assert res == {"ok": False, "error": "source exploded", "errorType": "RuntimeError"}


def test_api_login_state_envelope(monkeypatch):
    monkeypatch.setattr(bridge.service, "login_state", lambda **k: True)
    assert Api().login_state() == {"ok": True, "value": True}


def test_api_sync_plan_returns_plan_and_summary(monkeypatch):
    items = plan_sync([_book()], lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")])
    monkeypatch.setattr(bridge.service, "list_finished", lambda *a, **k: [_book()])
    monkeypatch.setattr(bridge.service, "build_sync_plan", lambda books, **k: items)
    monkeypatch.setattr(
        bridge.service, "summarize_plan", lambda its: {"match": 1, "ambiguous": 0, "no_match": 0}
    )

    res = Api().sync_plan("audible")

    assert res["ok"]
    assert res["value"]["summary"] == {"match": 1, "ambiguous": 0, "no_match": 0}
    assert res["value"]["plan"][0]["status"] == "match"


def test_api_chromium_status_envelope(monkeypatch):
    monkeypatch.setattr(bridge.service, "chromium_installed", lambda: True)
    assert Api().chromium_status() == {"ok": True, "value": True}


def test_api_install_browser_envelope(monkeypatch):
    monkeypatch.setattr(bridge.service, "ensure_chromium", lambda: True)
    assert Api().install_browser() == {"ok": True, "value": True}
