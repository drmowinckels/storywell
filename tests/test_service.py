import contextlib

import pytest

from storywell import service
from storywell.models import SourceBook
from storywell.storygraph.matching import Candidate


def _book(title, author="X", source_id="A1"):
    return SourceBook(source="audible", source_id=source_id, title=title, authors=(author,))


class _FakeSource:
    def __init__(self):
        self.threshold = None

    def finished_books(self, *, threshold):
        self.threshold = threshold
        return [_book("Hyperion", "Dan Simmons")]


def test_list_finished_builds_source_and_forwards_threshold(monkeypatch):
    fake = _FakeSource()
    captured = {}

    def fake_make_source(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return fake

    monkeypatch.setattr("storywell.sources.make_source", fake_make_source)

    books = service.list_finished("audible", threshold=0.8, profile="me")

    assert [b.title for b in books] == ["Hyperion"]
    assert captured["name"] == "audible"
    assert captured["kwargs"]["profile"] == "me"
    assert fake.threshold == 0.8


class _FakeBrowserCM:
    def __init__(self, page=None):
        self.page = page or object()
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.exited = True
        return False


def test_session_browser_yields_browser_when_authenticated(monkeypatch):
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda *a, **k: True)
    cm = _FakeBrowserCM()

    with service.session_browser(browser_factory=lambda: cm) as browser:
        assert browser is cm
    assert cm.exited


def test_session_browser_raises_when_not_authenticated(monkeypatch):
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda *a, **k: False)
    cm = _FakeBrowserCM()

    with (
        pytest.raises(service.NotAuthenticatedError),
        service.session_browser(browser_factory=lambda: cm),
    ):
        pass
    assert cm.exited  # browser is still closed on the unauthenticated path


class _FakeSearcher:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def search(self, query):
        return [Candidate("b1", "Hyperion", "Dan Simmons")]


def test_build_sync_plan_matches_books_through_injected_searcher():
    @contextlib.contextmanager
    def opener():
        yield _FakeSearcher()

    items = service.build_sync_plan([_book("Hyperion", "Dan Simmons")], open_searcher=opener)

    assert len(items) == 1
    assert items[0].result.status.value == "match"


def test_summarize_plan_keys_by_status_slug():
    @contextlib.contextmanager
    def opener():
        yield _FakeSearcher()

    items = service.build_sync_plan([_book("Hyperion", "Dan Simmons")], open_searcher=opener)
    summary = service.summarize_plan(items)

    assert summary == {"match": 1, "ambiguous": 0, "no_match": 0}


def test_login_state_delegates_to_is_authenticated(monkeypatch):
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda *a, **k: True)
    assert service.login_state() is True
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda *a, **k: False)
    assert service.login_state() is False


def test_chromium_installed_delegates(monkeypatch):
    monkeypatch.setattr("storywell.storygraph.chromium_installed", lambda: True)
    assert service.chromium_installed() is True


def test_install_chromium_delegates(monkeypatch):
    monkeypatch.setattr("storywell.storygraph.install_chromium", lambda: True)
    assert service.install_chromium() is True


def test_audible_login_delegates_and_stringifies(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(
        "storywell.sources.audible_auth.audible_login", lambda mp: Path("/cfg") / f"{mp}.json"
    )
    assert service.audible_login("us") == str(Path("/cfg") / "us.json")
