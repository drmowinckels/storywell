import contextlib

from storywell.storygraph import provision


class _FakeChromium:
    def __init__(self, path):
        self.executable_path = str(path)


class _FakePlaywright:
    def __init__(self, path):
        self.chromium = _FakeChromium(path)


def _factory(path):
    @contextlib.contextmanager
    def factory():
        yield _FakePlaywright(path)

    return factory


class _Result:
    def __init__(self, returncode):
        self.returncode = returncode


def test_chromium_installed_true_when_executable_exists(tmp_path):
    exe = tmp_path / "chrome"
    exe.write_text("")
    assert provision.chromium_installed(playwright_factory=_factory(exe)) is True


def test_chromium_installed_false_when_executable_missing(tmp_path):
    missing = tmp_path / "absent"
    assert provision.chromium_installed(playwright_factory=_factory(missing)) is False


def test_install_chromium_runs_playwright_install_and_reports_success():
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Result(0)

    assert provision.install_chromium(runner=runner) is True
    assert captured["cmd"][1:] == ["-m", "playwright", "install", "chromium"]


def test_install_chromium_reports_failure_on_nonzero_exit():
    assert provision.install_chromium(runner=lambda cmd, **k: _Result(1)) is False


def test_ensure_chromium_skips_install_when_already_present(tmp_path):
    exe = tmp_path / "chrome"
    exe.write_text("")
    installed = []

    def runner(cmd, **k):
        installed.append(cmd)
        return _Result(0)

    assert provision.ensure_chromium(playwright_factory=_factory(exe), runner=runner) is True
    assert installed == []


def test_ensure_chromium_installs_when_missing(tmp_path):
    installed = []

    def runner(cmd, **k):
        installed.append(cmd)
        return _Result(0)

    ok = provision.ensure_chromium(playwright_factory=_factory(tmp_path / "absent"), runner=runner)
    assert ok is True
    assert installed and installed[0][1:] == ["-m", "playwright", "install", "chromium"]
