from __future__ import annotations

import tomllib
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import audible
import audible.exceptions
import httpx

from .models import Audiobook

LIBRARY_RESPONSE_GROUPS = (
    "product_desc,product_attrs,contributors,is_finished,percent_complete,relationships"
)
DEFAULT_AUTH_DIR = Path.home() / ".audible"
DEFAULT_CONFIG_FILENAME = "config.toml"
PAGE_SIZE = 1000
MAX_PAGES = 50


class AuthFileNotFound(RuntimeError):
    pass


class LibraryFetchError(RuntimeError):
    pass


def _read_auth_file_from_config(config_path: Path, profile: str | None) -> Path:
    try:
        config = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise AuthFileNotFound(f"Could not read {config_path}: {err}") from err

    profile_name = profile or config.get("APP", {}).get("primary_profile")
    if not profile_name:
        raise AuthFileNotFound(f"{config_path} has no primary profile and no --profile was given.")

    profile_section = config.get("profile", {}).get(profile_name)
    if not profile_section:
        raise AuthFileNotFound(f"Profile '{profile_name}' is not defined in {config_path}.")

    auth_filename = profile_section.get("auth_file")
    if not auth_filename:
        raise AuthFileNotFound(f"Profile '{profile_name}' in {config_path} has no auth_file entry.")

    auth_path = (config_path.parent / auth_filename).resolve()
    if not auth_path.exists():
        raise AuthFileNotFound(
            f"Auth file '{auth_path}' referenced by profile '{profile_name}' is missing."
        )
    return auth_path


def locate_auth_file(
    explicit: Path | None = None,
    profile: str | None = None,
    config_dir: Path | None = None,
) -> Path:
    if explicit is not None:
        if not explicit.exists():
            raise AuthFileNotFound(f"--auth-file does not exist: {explicit}")
        return explicit

    config_dir = config_dir or DEFAULT_AUTH_DIR
    config_path = config_dir / DEFAULT_CONFIG_FILENAME
    if not config_path.exists():
        raise AuthFileNotFound(
            f"No audible-cli config at {config_path}. "
            "Run `pipx install audible-cli && audible quickstart` first, "
            "or pass --auth-file."
        )
    return _read_auth_file_from_config(config_path, profile)


def parse_authors(item: dict[str, Any]) -> tuple[str, ...]:
    return tuple(a["name"] for a in item.get("authors") or [] if a.get("name"))


def parse_narrators(item: dict[str, Any]) -> tuple[str, ...]:
    return tuple(n["name"] for n in item.get("narrators") or [] if n.get("name"))


def _listening_status(item: dict[str, Any]) -> dict[str, Any]:
    status = item.get("listening_status")
    return status if isinstance(status, dict) else {}


def parse_finished_at(item: dict[str, Any]) -> datetime | None:
    status = _listening_status(item)
    raw = status.get("finished_at_timestamp") or item.get("finished_at_timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_percent_complete(item: dict[str, Any]) -> float:
    status = _listening_status(item)
    raw = status.get("percent_complete")
    if raw is None:
        raw = item.get("percent_complete")
    try:
        return float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0


def parse_is_finished(item: dict[str, Any]) -> bool:
    status = _listening_status(item)
    return bool(status.get("is_finished") or item.get("is_finished"))


def item_to_audiobook(item: dict[str, Any]) -> Audiobook:
    return Audiobook(
        asin=item["asin"],
        title=item.get("title", "").strip(),
        authors=parse_authors(item),
        narrators=parse_narrators(item),
        percent_complete=parse_percent_complete(item),
        finished_at=parse_finished_at(item),
        is_finished=parse_is_finished(item),
    )


def filter_finished(items: Iterable[dict[str, Any]], threshold: float = 0.95) -> list[Audiobook]:
    cutoff = threshold * 100.0
    finished: list[Audiobook] = []
    for raw in items:
        book = item_to_audiobook(raw)
        if book.is_finished or book.percent_complete >= cutoff:
            finished.append(book)
    return finished


def _paginate(client: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        response = client.get(
            "1.0/library",
            num_results=PAGE_SIZE,
            page=page,
            response_groups=LIBRARY_RESPONSE_GROUPS,
        )
        items = response.get("items") or []
        results.extend(items)
        if len(items) < PAGE_SIZE:
            return results
    raise LibraryFetchError(
        f"Audible library exceeds {MAX_PAGES * PAGE_SIZE} items; refusing to paginate further."
    )


def fetch_library_items(auth_file: Path) -> list[dict[str, Any]]:
    try:
        auth = audible.Authenticator.from_file(str(auth_file))
    except (audible.exceptions.AuthFlowError, audible.exceptions.NoRefreshToken, OSError) as err:
        raise LibraryFetchError(f"Could not load Audible auth from {auth_file}: {err}") from err

    try:
        with audible.Client(auth=auth) as client:
            return _paginate(client)
    except audible.exceptions.Unauthorized as err:
        raise LibraryFetchError(
            "Audible rejected the saved credentials. "
            "Try `audible quickstart` again to re-authenticate."
        ) from err
    except audible.exceptions.RatelimitError as err:
        raise LibraryFetchError("Audible rate-limited the request; try again later.") from err
    except audible.exceptions.RequestError as err:
        raise LibraryFetchError(f"Audible API request failed: {err}") from err
    except httpx.HTTPError as err:
        raise LibraryFetchError(f"Network error talking to Audible: {err}") from err


def finished_audiobooks(
    threshold: float = 0.95,
    auth_file: Path | None = None,
    profile: str | None = None,
) -> list[Audiobook]:
    resolved = locate_auth_file(auth_file, profile=profile)
    items = fetch_library_items(resolved)
    return filter_finished(items, threshold=threshold)
