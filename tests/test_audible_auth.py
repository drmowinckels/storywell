import stat
from pathlib import Path

import pytest

from storywell.sources.audible_auth import MARKETPLACES, AudibleLoginError, audible_login


class _FakeAuth:
    def __init__(self):
        self.encryption = "unset"

    def to_file(self, filename, encryption=None, **kwargs):
        self.encryption = encryption
        Path(filename).write_text("{}")


def _factory(auth, captured):
    def factory(*, locale, login_url_callback):
        captured["locale"] = locale
        captured["callback"] = login_url_callback
        return auth

    return factory


def test_audible_login_writes_owner_only_auth_file(tmp_path):
    auth, captured = _FakeAuth(), {}
    dest = tmp_path / "audible.json"
    sentinel = lambda url: url  # noqa: E731

    result = audible_login(
        "us",
        login_url_callback=sentinel,
        auth_path=dest,
        authenticator_factory=_factory(auth, captured),
    )

    assert result == dest
    assert dest.read_text() == "{}"
    assert captured == {"locale": "us", "callback": sentinel}
    assert auth.encryption is False
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600


def test_audible_login_normalizes_marketplace(tmp_path):
    captured = {}
    audible_login(
        "  UK ",
        login_url_callback=lambda url: url,
        auth_path=tmp_path / "a.json",
        authenticator_factory=_factory(_FakeAuth(), captured),
    )
    assert captured["locale"] == "uk"


def test_audible_login_rejects_unknown_marketplace(tmp_path):
    called = []

    def factory(**kwargs):
        called.append(kwargs)
        return _FakeAuth()

    with pytest.raises(AudibleLoginError, match="Unknown Audible marketplace"):
        audible_login("zz", auth_path=tmp_path / "a.json", authenticator_factory=factory)
    assert called == []


def test_audible_login_wraps_factory_errors(tmp_path):
    def factory(**kwargs):
        raise RuntimeError("amazon said no")

    with pytest.raises(AudibleLoginError, match="Audible login failed"):
        audible_login(
            "us",
            login_url_callback=lambda url: url,
            auth_path=tmp_path / "a.json",
            authenticator_factory=factory,
        )


def test_marketplaces_are_the_audible_cli_codes():
    assert MARKETPLACES == ("us", "ca", "uk", "au", "fr", "de", "jp", "it", "in", "es", "br")
