from storywell.desktop import bridge
from storywell.desktop.bridge import Api


class FakeEngine:
    """Records the (method, args) the bridge forwards, and returns a canned envelope."""

    def __init__(self, result=None, progress=None):
        self.calls = []
        self.result = result if result is not None else {"ok": True, "value": "ok"}
        self._progress = progress or {"done": 3, "total": 10}

    def call(self, method, args=None):
        self.calls.append((method, args or {}))
        return self.result

    def progress(self):
        return self._progress

    def shutdown(self):
        self.calls.append(("shutdown", {}))


def test_api_sources_delegates():
    eng = FakeEngine(result={"ok": True, "value": ["audible"]})
    assert Api(engine=eng).sources() == {"ok": True, "value": ["audible"]}
    assert eng.calls == [("sources", {})]


def test_api_login_state_passes_headless():
    eng = FakeEngine()
    Api(headless=False, engine=eng).login_state()
    assert eng.calls == [("login_state", {"headless": False})]


def test_api_saved_logins_delegates():
    eng = FakeEngine()
    Api(engine=eng).saved_logins()
    assert eng.calls == [("saved_logins", {})]


def test_api_forget_login_passes_service_name():
    eng = FakeEngine()
    Api(engine=eng).forget_login("audible")
    assert eng.calls == [("forget_login", {"service_name": "audible"})]


def test_api_chromium_status_delegates():
    eng = FakeEngine()
    Api(engine=eng).chromium_status()
    assert eng.calls == [("chromium_status", {})]


def test_api_install_browser_delegates():
    eng = FakeEngine()
    Api(engine=eng).install_browser()
    assert eng.calls == [("install_browser", {})]


def test_api_audible_login_passes_marketplace():
    eng = FakeEngine()
    Api(engine=eng).audible_login("us")
    assert eng.calls == [("audible_login", {"marketplace": "us"})]


def test_api_storygraph_login_delegates():
    eng = FakeEngine()
    Api(engine=eng).storygraph_login()
    assert eng.calls == [("storygraph_login", {})]


def test_api_list_finished_passes_source_and_options():
    eng = FakeEngine()
    Api(engine=eng).list_finished("goodreads", {"path": "/x.csv"})
    assert eng.calls == [("list_finished", {"source": "goodreads", "options": {"path": "/x.csv"}})]


def test_api_sync_plan_passes_source_options_and_headless():
    eng = FakeEngine()
    Api(headless=True, engine=eng).sync_plan("audible", {"token": "t"})
    assert eng.calls == [
        ("sync_plan", {"source": "audible", "options": {"token": "t"}, "headless": True})
    ]


def test_api_progress_returns_engine_progress():
    eng = FakeEngine(progress={"done": 7, "total": 42})
    assert Api(engine=eng).progress() == {"ok": True, "value": {"done": 7, "total": 42}}


def test_api_choose_file_envelope(monkeypatch):
    monkeypatch.setattr(bridge, "open_file_dialog", lambda: "/picked/library.csv")
    assert Api(engine=FakeEngine()).choose_file() == {"ok": True, "value": "/picked/library.csv"}


def test_api_choose_file_surfaces_errors(monkeypatch):
    def boom():
        raise RuntimeError("no window")

    monkeypatch.setattr(bridge, "open_file_dialog", boom)
    res = Api(engine=FakeEngine()).choose_file()
    assert res == {"ok": False, "error": "no window", "errorType": "RuntimeError"}
