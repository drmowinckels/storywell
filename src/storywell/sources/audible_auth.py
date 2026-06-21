"""In-app Amazon/Audible login, so users never run ``audible quickstart`` in a terminal.

Uses the Audible SDK's *external* login flow: Storywell opens Amazon's real login page in a
browser, the user signs in there (handling CAPTCHA / 2FA on Amazon's own page), and we capture
the post-login redirect. Storywell never sees the password. The registered device's tokens are
written to a Storywell-owned auth file, locked to owner-only, which :mod:`storywell.sources.audible`
then reads.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import audible_auth_path, ensure_config_dir, secure_file
from .base import SourceError

# Audible-cli's marketplace country codes; each resolves to a valid Audible locale.
MARKETPLACES: tuple[str, ...] = ("us", "ca", "uk", "au", "fr", "de", "jp", "it", "in", "es", "br")

# Amazon redirects here once login (incl. CAPTCHA/2FA) is complete; its URL carries the auth code.
MAPLANDING_PATH = "/ap/maplanding"

LoginUrlCallback = Callable[[str], str]
AuthenticatorFactory = Callable[..., Any]


class AudibleLoginError(SourceError):
    pass


def chromium_login_callback(url: str) -> str:
    """Open the Amazon login page in a headed Chromium and return the post-login URL.

    Reuses the Chromium that StoryGraph sync already provisions (no extra browser). The user
    logs in on Amazon's page; we poll until Amazon redirects to ``/ap/maplanding`` and hand
    that URL back to the SDK, which extracts the auth code. Mirrors the StoryGraph login: a
    real headed browser, no password ever touched by Storywell.
    """
    from audible.login import build_init_cookies
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            context = browser.new_context()
            context.add_cookies(
                [
                    {"name": name, "value": value, "url": url}
                    for name, value in build_init_cookies().items()
                ]
            )
            page = context.new_page()
            page.goto(url)
            while MAPLANDING_PATH not in page.url:
                page.wait_for_timeout(600)
            return page.url
        finally:
            browser.close()


def audible_login(
    marketplace: str,
    *,
    login_url_callback: LoginUrlCallback | None = None,
    auth_path: Path | None = None,
    authenticator_factory: AuthenticatorFactory | None = None,
) -> Path:
    """Run an external Amazon login for ``marketplace`` and persist the auth file (0600).

    Returns the path to the saved auth file. Raises :class:`AudibleLoginError` for an unknown
    marketplace or any failure during the login/registration handshake.
    """
    marketplace = marketplace.strip().lower()
    if marketplace not in MARKETPLACES:
        raise AudibleLoginError(
            f"Unknown Audible marketplace '{marketplace}'. "
            f"Choose one of: {', '.join(MARKETPLACES)}."
        )

    if authenticator_factory is None:
        import audible

        authenticator_factory = audible.Authenticator.from_login_external

    callback = login_url_callback or chromium_login_callback
    try:
        auth = authenticator_factory(locale=marketplace, login_url_callback=callback)
    except Exception as err:  # noqa: BLE001 - SDK raises many types; re-raised as a domain error
        raise AudibleLoginError(f"Audible login failed: {err}") from err

    if auth_path is None:
        ensure_config_dir()  # creates the 0700 config dir for the default location
        auth_path = audible_auth_path()
    # encryption=False keeps it readable without a passphrase every sync — the tokens are
    # protected by 0600 + the 0700 dir, same as the StoryGraph session. set_default=False
    # avoids touching any audible-cli global default; this file stands alone.
    auth.to_file(str(auth_path), encryption=False, set_default=False)
    secure_file(auth_path)
    return auth_path
