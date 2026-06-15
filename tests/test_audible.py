from datetime import UTC, datetime
from textwrap import dedent
from unittest.mock import MagicMock, patch

import audible.exceptions
import pytest

from storywell.sources.audible import (
    PAGE_SIZE,
    AuthFileNotFound,
    LibraryFetchError,
    fetch_library_items,
    filter_finished,
    item_to_book,
    locate_auth_file,
    parse_finished_at,
    parse_is_finished,
    parse_percent_complete,
)


def make_item(
    asin="B0EXAMPLE01",
    title="The Sample Novel",
    authors=("Sample Author",),
    narrators=("Sample Narrator",),
    percent_complete=0,
    is_finished=False,
    finished_at_timestamp=None,
):
    return {
        "asin": asin,
        "title": title,
        "authors": [{"name": a} for a in authors],
        "narrators": [{"name": n} for n in narrators],
        "percent_complete": percent_complete,
        "is_finished": is_finished,
        "listening_status": {
            "is_finished": is_finished,
            "percent_complete": percent_complete,
            "finished_at_timestamp": finished_at_timestamp,
        },
    }


def test_item_to_book_extracts_contributors():
    book = item_to_book(make_item(authors=("Ursula Le Guin",), narrators=("Carrington MacDuffie",)))
    assert book.source_id == "B0EXAMPLE01"
    assert book.source == "audible"
    assert book.key == "audible:B0EXAMPLE01"
    assert book.authors == ("Ursula Le Guin",)
    assert book.narrators == ("Carrington MacDuffie",)


def test_item_to_book_parses_finished_at_iso_with_z():
    book = item_to_book(make_item(finished_at_timestamp="2025-09-12T08:30:00Z"))
    assert book.finished_at == datetime(2025, 9, 12, 8, 30, tzinfo=UTC)


def test_item_to_book_handles_missing_optional_fields():
    book = item_to_book({"asin": "B0X", "title": "Bare"})
    assert book.authors == ()
    assert book.narrators == ()
    assert book.percent_complete == 0.0
    assert book.finished_at is None
    assert book.is_finished is False


@pytest.mark.parametrize(
    "raw_value",
    [None, "", "not-a-date", 12345, {"nested": "object"}, ["list"]],
)
def test_parse_finished_at_tolerates_garbage(raw_value):
    assert parse_finished_at({"listening_status": {"finished_at_timestamp": raw_value}}) is None


def test_parse_finished_at_reads_nested_listening_status():
    parsed = parse_finished_at(
        {"listening_status": {"finished_at_timestamp": "2024-01-15T12:00:00Z"}}
    )
    assert parsed == datetime(2024, 1, 15, 12, 0, tzinfo=UTC)


def test_parse_finished_at_falls_back_to_top_level_timestamp():
    parsed = parse_finished_at({"finished_at_timestamp": "2024-01-15T12:00:00Z"})
    assert parsed == datetime(2024, 1, 15, 12, 0, tzinfo=UTC)


def test_parse_percent_complete_prefers_nested_over_top_level():
    item = {"percent_complete": 10.0, "listening_status": {"percent_complete": 88.0}}
    assert parse_percent_complete(item) == 88.0


def test_parse_percent_complete_falls_back_to_top_level():
    assert parse_percent_complete({"percent_complete": 42.0}) == 42.0


def test_parse_percent_complete_tolerates_missing_and_garbage():
    assert parse_percent_complete({}) == 0.0
    assert parse_percent_complete({"percent_complete": "nope"}) == 0.0


def test_parse_is_finished_true_when_either_source_is_finished():
    assert parse_is_finished({"is_finished": True}) is True
    assert parse_is_finished({"listening_status": {"is_finished": True}}) is True
    both_false = {"is_finished": False, "listening_status": {"is_finished": False}}
    assert parse_is_finished(both_false) is False


def test_filter_finished_includes_is_finished_true():
    items = [make_item(asin="A1", is_finished=True, percent_complete=12)]
    assert [b.source_id for b in filter_finished(items)] == ["A1"]


def test_filter_finished_includes_percent_above_threshold():
    items = [
        make_item(asin="A1", percent_complete=94),
        make_item(asin="A2", percent_complete=95),
        make_item(asin="A3", percent_complete=100),
    ]
    assert {b.source_id for b in filter_finished(items, threshold=0.95)} == {"A2", "A3"}


def test_filter_finished_threshold_is_configurable():
    items = [make_item(asin="A1", percent_complete=80)]
    assert filter_finished(items, threshold=0.95) == []
    assert [b.source_id for b in filter_finished(items, threshold=0.75)] == ["A1"]


def test_filter_finished_skips_unstarted_books():
    items = [make_item(asin="A1", percent_complete=0, is_finished=False)]
    assert filter_finished(items) == []


def test_locate_auth_file_prefers_explicit_path(tmp_path):
    explicit = tmp_path / "creds.json"
    explicit.write_text("{}")
    assert locate_auth_file(explicit) == explicit


def test_locate_auth_file_raises_when_explicit_missing(tmp_path):
    with pytest.raises(AuthFileNotFound):
        locate_auth_file(tmp_path / "nope.json")


def _write_config(config_dir, primary="audible", auth_filename="audible.json", make_auth=True):
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.joinpath("config.toml").write_text(
        dedent(
            f"""
            title = "Audible Config File"
            [APP]
            primary_profile = "{primary}"

            [profile.{primary}]
            auth_file = "{auth_filename}"
            country_code = "us"
            """
        ).strip()
    )
    if make_auth:
        config_dir.joinpath(auth_filename).write_text("{}")


def test_locate_auth_file_reads_primary_profile_from_config(tmp_path):
    _write_config(tmp_path)
    resolved = locate_auth_file(config_dir=tmp_path)
    assert resolved == (tmp_path / "audible.json").resolve()


def test_locate_auth_file_respects_explicit_profile_override(tmp_path):
    _write_config(tmp_path, primary="us", auth_filename="us.json")
    tmp_path.joinpath("config.toml").write_text(
        dedent(
            """
            [APP]
            primary_profile = "us"

            [profile.us]
            auth_file = "us.json"

            [profile.uk]
            auth_file = "uk.json"
            """
        ).strip()
    )
    tmp_path.joinpath("uk.json").write_text("{}")
    resolved = locate_auth_file(profile="uk", config_dir=tmp_path)
    assert resolved.name == "uk.json"


def test_locate_auth_file_missing_config_points_to_quickstart(tmp_path):
    with pytest.raises(AuthFileNotFound, match="audible quickstart"):
        locate_auth_file(config_dir=tmp_path)


def test_locate_auth_file_raises_for_unknown_profile(tmp_path):
    _write_config(tmp_path)
    with pytest.raises(AuthFileNotFound, match="not defined"):
        locate_auth_file(profile="ghost", config_dir=tmp_path)


def test_locate_auth_file_raises_when_referenced_auth_missing(tmp_path):
    _write_config(tmp_path, make_auth=False)
    with pytest.raises(AuthFileNotFound, match="is missing"):
        locate_auth_file(config_dir=tmp_path)


class _FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, *args, **kwargs):
        self.calls.append(kwargs)
        return self.pages.pop(0) if self.pages else {"items": []}


def _patches(client):
    return (
        patch("storywell.sources.audible.audible.Client", return_value=client),
        patch(
            "storywell.sources.audible.audible.Authenticator.from_file",
            return_value=MagicMock(),
        ),
    )


def test_fetch_library_items_returns_single_short_page(tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    client = _FakeClient([{"items": [make_item(asin="A1")]}])
    cp, ap = _patches(client)
    with cp, ap:
        items = fetch_library_items(auth)
    assert [i["asin"] for i in items] == ["A1"]
    assert len(client.calls) == 1
    assert client.calls[0]["page"] == 1
    assert client.calls[0]["num_results"] == PAGE_SIZE
    assert "is_finished" in client.calls[0]["response_groups"]


def test_fetch_library_items_paginates_until_short_page(tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    full_page = {"items": [make_item(asin=f"A{n}") for n in range(PAGE_SIZE)]}
    last_page = {"items": [make_item(asin="LAST")]}
    client = _FakeClient([full_page, last_page])
    cp, ap = _patches(client)
    with cp, ap:
        items = fetch_library_items(auth)
    assert len(items) == PAGE_SIZE + 1
    assert [call["page"] for call in client.calls] == [1, 2]


def test_fetch_library_items_wraps_unauthorized(tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get.side_effect = audible.exceptions.Unauthorized(MagicMock(status_code=401), {})
    cp, ap = _patches(client)
    with cp, ap, pytest.raises(LibraryFetchError, match="rejected the saved credentials"):
        fetch_library_items(auth)


def test_fetch_library_items_wraps_rate_limit(tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get.side_effect = audible.exceptions.RatelimitError(MagicMock(status_code=429), {})
    cp, ap = _patches(client)
    with cp, ap, pytest.raises(LibraryFetchError, match="rate-limited"):
        fetch_library_items(auth)


def test_fetch_library_items_wraps_auth_load_failure(tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    with (
        patch(
            "storywell.sources.audible.audible.Authenticator.from_file",
            side_effect=audible.exceptions.NoRefreshToken(),
        ),
        pytest.raises(LibraryFetchError, match="Could not load Audible auth"),
    ):
        fetch_library_items(auth)
