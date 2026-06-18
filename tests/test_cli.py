from datetime import UTC, datetime

import pytest
import typer
from typer.testing import CliRunner

from storywell.cli import (
    _choose_candidate,
    _display_name,
    _parse_shelf,
    _prompt_ambiguous,
    app,
)
from storywell.models import Shelf, SourceBook
from storywell.storygraph import StorygraphDependencyError
from storywell.storygraph.matching import Candidate, MatchResult, MatchStatus, ScoredCandidate

runner = CliRunner()


def test_parse_shelf_none_is_read_only_by_default():
    assert _parse_shelf(None, as_read=False) is None


def test_parse_shelf_as_read_alias_maps_to_read():
    assert _parse_shelf(None, as_read=True) is Shelf.READ


def test_parse_shelf_explicit_value_wins():
    assert _parse_shelf("to-read", as_read=False) is Shelf.TO_READ
    assert _parse_shelf("currently-reading", as_read=True) is Shelf.CURRENTLY_READING
    assert _parse_shelf("DID-NOT-FINISH", as_read=False) is Shelf.DID_NOT_FINISH


def test_parse_shelf_rejects_unknown_shelf():
    with pytest.raises(typer.Exit):
        _parse_shelf("favourites", as_read=False)


def test_parse_shelf_rejects_unknown_status():
    # 'unknown' is a real Shelf value but not a writable target shelf
    with pytest.raises(typer.Exit):
        _parse_shelf("unknown", as_read=False)


class _FakeBrowser:
    def __init__(self, *args, **kwargs):
        self.page = object()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeSearcher:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def search(self, query):
        return [Candidate("b1", "Hyperion", "Dan Simmons")]

    def resolve_edition(self, book_id, media_format, **kwargs):
        return None


class _FakeClient:
    def __init__(self, *args, **kwargs):
        self.shelves = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def mark_shelf(self, book_id, status, date=None):
        self.shelves.append((book_id, status, date))
        return True

    def write_review(self, book_id, *, stars_integer="", stars_decimal="", explanation=""):
        return "written"


def _one_book(*a, **k):
    return [
        SourceBook(source="audible", source_id="A1", title="Hyperion", authors=("Dan Simmons",))
    ]


def test_display_name_with_and_without_authors():
    with_authors = SourceBook(
        source="audible", source_id="A", title="Dune", authors=("Frank Herbert",)
    )
    assert _display_name(with_authors) == "Dune by Frank Herbert"
    bare = SourceBook(source="audible", source_id="B", title="Untitled")
    assert _display_name(bare) == "Untitled"


def test_prompt_ambiguous_returns_picked_candidate(monkeypatch):
    monkeypatch.setattr("storywell.cli.typer.prompt", lambda *a, **k: "1")
    best = ScoredCandidate(Candidate("b1", "T1", "A1"), 0.8, 0.8, 0.8)
    alt = ScoredCandidate(Candidate("b2", "T2", "A2"), 0.78, 0.78, 0.78)
    result = MatchResult(MatchStatus.AMBIGUOUS, best, (alt,))
    book = SourceBook(source="audible", source_id="A", title="T1", authors=("A1",))
    assert _prompt_ambiguous(book, result).book_id == "b1"


def test_prompt_ambiguous_skip_returns_none(monkeypatch):
    monkeypatch.setattr("storywell.cli.typer.prompt", lambda *a, **k: "s")
    best = ScoredCandidate(Candidate("b1", "T1", "A1"), 0.8, 0.8, 0.8)
    result = MatchResult(MatchStatus.AMBIGUOUS, best, ())
    book = SourceBook(source="audible", source_id="A", title="T1")
    assert _prompt_ambiguous(book, result) is None


def test_choose_candidate_picks_by_number():
    options = [
        ScoredCandidate(Candidate("b1", "T1", "A"), 0.7, 0.7, 0.7),
        ScoredCandidate(Candidate("b2", "T2", "A"), 0.6, 0.6, 0.6),
    ]
    assert _choose_candidate("2", options).book_id == "b2"


def test_choose_candidate_skip_and_invalid_return_none():
    options = [ScoredCandidate(Candidate("b1", "T1", "A"), 0.7, 0.7, 0.7)]
    assert _choose_candidate("s", options) is None
    assert _choose_candidate("9", options) is None
    assert _choose_candidate("", options) is None


def test_cli_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "storywell" in result.stdout


def test_cli_version_short_flag():
    result = runner.invoke(app, ["-V"])
    assert result.exit_code == 0
    assert "storywell" in result.stdout


def test_cli_short_help_flag():
    result = runner.invoke(app, ["-h"])
    assert result.exit_code == 0
    assert "Usage" in result.stdout
    assert result.stdout == runner.invoke(app, ["--help"]).stdout


def test_cli_short_help_flag_on_subcommand():
    result = runner.invoke(app, ["sync", "-h"])
    assert result.exit_code == 0
    assert "Usage" in result.stdout


def test_cli_sources_lists_audible():
    result = runner.invoke(app, ["sources"])
    assert result.exit_code == 0
    assert "audible" in result.stdout


def test_cli_storygraph_login_success(monkeypatch, tmp_path):
    saved = tmp_path / "state.json"
    monkeypatch.setattr("storywell.storygraph.login", lambda: saved)
    result = runner.invoke(app, ["storygraph-login"])
    assert result.exit_code == 0
    assert "Saved StoryGraph session" in result.stdout


def test_cli_storygraph_login_dependency_error(monkeypatch):
    def boom():
        raise StorygraphDependencyError("install playwright first")

    monkeypatch.setattr("storywell.storygraph.login", boom)
    result = runner.invoke(app, ["storygraph-login"])
    assert result.exit_code == 1
    assert "install playwright first" in result.stdout


def test_cli_storygraph_status_active(monkeypatch):
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda *a, **k: True)
    result = runner.invoke(app, ["storygraph-status"])
    assert result.exit_code == 0
    assert "active" in result.stdout.lower()


def test_cli_storygraph_status_inactive(monkeypatch):
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda *a, **k: False)
    result = runner.invoke(app, ["storygraph-status"])
    assert result.exit_code == 1
    assert "storygraph-login" in result.stdout


def test_cli_sync_writes_matches(monkeypatch, tmp_path):
    monkeypatch.setattr("storywell.cli._load_finished", _one_book)
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda *a, **k: True)
    monkeypatch.setattr("storywell.storygraph.StorygraphBrowser", _FakeBrowser)
    monkeypatch.setattr("storywell.storygraph.search.StorygraphSearcher", _FakeSearcher)
    monkeypatch.setattr("storywell.storygraph.client.StorygraphClient", _FakeClient)
    monkeypatch.setattr("storywell.config.sync_store_path", lambda: tmp_path / "store.json")
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "written: 1" in result.stdout
    assert (tmp_path / "store.json").exists()


def test_cli_sync_shelf_routes_unfinished_books(monkeypatch, tmp_path):
    captured = {}

    class _CapturingClient(_FakeClient):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            captured["client"] = self

    monkeypatch.setattr("storywell.cli._load_finished", _one_book)
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda *a, **k: True)
    monkeypatch.setattr("storywell.storygraph.StorygraphBrowser", _FakeBrowser)
    monkeypatch.setattr("storywell.storygraph.search.StorygraphSearcher", _FakeSearcher)
    monkeypatch.setattr("storywell.storygraph.client.StorygraphClient", _CapturingClient)
    monkeypatch.setattr("storywell.config.sync_store_path", lambda: tmp_path / "store.json")
    result = runner.invoke(app, ["sync", "--shelf", "to-read", "--no-ratings"])
    assert result.exit_code == 0
    assert captured["client"].shelves == [("b1", Shelf.TO_READ, None)]


def test_cli_sync_rejects_bad_shelf(monkeypatch):
    monkeypatch.setattr("storywell.cli._load_finished", _one_book)
    result = runner.invoke(app, ["sync", "--shelf", "nonsense"])
    assert result.exit_code == 1
    assert "Unknown shelf" in result.stdout


class _AuthSearcher:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def search(self, query):
        from storywell.storygraph.session import StorygraphAuthError

        raise StorygraphAuthError("StoryGraph session expired mid-run.")

    def resolve_edition(self, book_id, media_format, **kwargs):
        return None


def test_cli_sync_aborts_and_saves_on_session_expiry(monkeypatch, tmp_path):
    monkeypatch.setattr("storywell.cli._load_finished", _one_book)
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda *a, **k: True)
    monkeypatch.setattr("storywell.storygraph.StorygraphBrowser", _FakeBrowser)
    monkeypatch.setattr("storywell.storygraph.search.StorygraphSearcher", _AuthSearcher)
    monkeypatch.setattr("storywell.storygraph.client.StorygraphClient", _FakeClient)
    monkeypatch.setattr("storywell.config.sync_store_path", lambda: tmp_path / "store.json")
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1
    assert "session" in result.stdout.lower()
    assert (tmp_path / "store.json").exists()  # progress persisted via finally


def test_cli_sync_no_books_to_sync(monkeypatch):
    monkeypatch.setattr("storywell.cli._load_finished", lambda *a, **k: [])
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "No books to sync" in result.stdout


def test_cli_sync_requires_session(monkeypatch):
    monkeypatch.setattr("storywell.cli._load_finished", _one_book)
    monkeypatch.setattr("storywell.storygraph.StorygraphBrowser", _FakeBrowser)
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda *a, **k: False)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1
    assert "storygraph-login" in result.stdout


def test_cli_sync_dry_run_reports_matches(monkeypatch):
    monkeypatch.setattr("storywell.cli._load_finished", _one_book)
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda *a, **k: True)
    monkeypatch.setattr("storywell.storygraph.StorygraphBrowser", _FakeBrowser)
    monkeypatch.setattr("storywell.storygraph.search.StorygraphSearcher", _FakeSearcher)
    result = runner.invoke(app, ["sync", "--dry-run"])
    assert result.exit_code == 0
    assert "match:" in result.stdout.lower()


def test_cli_sync_reports_missing_playwright(monkeypatch):
    def boom(*a, **k):
        from storywell.storygraph import StorygraphDependencyError

        raise StorygraphDependencyError("install playwright first")

    monkeypatch.setattr("storywell.cli._load_finished", _one_book)
    monkeypatch.setattr("storywell.storygraph.StorygraphBrowser", boom)
    result = runner.invoke(app, ["sync", "--dry-run"])
    assert result.exit_code == 1
    assert "install playwright first" in result.stdout


class _FakeCollSearcher:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def search(self, query):
        if "jane austen" in query.lower():
            return [Candidate("o1", "Jane Austen: The Complete Collection", "Jane Austen")]
        return [Candidate("k-" + query[:6], query, "")]

    def fetch_description(self, book_id):
        return "Included are the following: Emma, Persuasion."

    def resolve_edition(self, book_id, media_format, **kwargs):
        return None


def _one_collection(*a, **k):
    return [
        SourceBook(
            source="audible",
            source_id="C1",
            title="The Complete Jane Austen Collection",
            is_collection=True,
            finished_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
    ]


def test_cli_collections_none_found(monkeypatch):
    monkeypatch.setattr("storywell.cli._load_finished", _one_book)
    result = runner.invoke(app, ["collections"])
    assert result.exit_code == 0
    assert "No finished collections" in result.stdout


def test_cli_collections_dry_run_lists_titles(monkeypatch):
    monkeypatch.setattr("storywell.cli._load_finished", _one_collection)
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda *a, **k: True)
    monkeypatch.setattr("storywell.storygraph.StorygraphBrowser", _FakeBrowser)
    monkeypatch.setattr("storywell.storygraph.search.StorygraphSearcher", _FakeCollSearcher)
    result = runner.invoke(app, ["collections"])
    assert result.exit_code == 0
    assert "Emma" in result.stdout
    assert "Persuasion" in result.stdout


def test_cli_collections_no_dry_run_marks_selected(monkeypatch, tmp_path):
    monkeypatch.setattr("storywell.cli._load_finished", _one_collection)
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda *a, **k: True)
    monkeypatch.setattr("storywell.storygraph.StorygraphBrowser", _FakeBrowser)
    monkeypatch.setattr("storywell.storygraph.search.StorygraphSearcher", _FakeCollSearcher)
    monkeypatch.setattr("storywell.storygraph.client.StorygraphClient", _FakeClient)
    monkeypatch.setattr("storywell.config.sync_store_path", lambda: tmp_path / "store.json")
    result = runner.invoke(app, ["collections", "--no-dry-run"], input="1\n\n")
    assert result.exit_code == 0
    assert "written: 1" in result.stdout


def _one_audio_book(*a, **k):
    return [
        SourceBook(
            source="audible",
            source_id="A1",
            title="Hyperion",
            authors=("Dan Simmons",),
            media_format="audio",
        )
    ]


class _FakeRetagSearcher:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_editions(self, book_id, **kwargs):
        from storywell.storygraph.editions import Edition

        return [Edition("pap", "paperback"), Edition("aud", "audio")]


def _seed_store(path, mappings):
    import json

    path.write_text(json.dumps({"mappings": mappings, "synced": {}, "rated": {}}))


def test_cli_retag_reports_retaggable(monkeypatch, tmp_path):
    _seed_store(tmp_path / "store.json", {"audible:A1": "pap"})
    monkeypatch.setattr("storywell.cli._load_finished", _one_audio_book)
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda: True)
    monkeypatch.setattr("storywell.storygraph.StorygraphBrowser", _FakeBrowser)
    monkeypatch.setattr("storywell.storygraph.search.StorygraphSearcher", _FakeRetagSearcher)
    monkeypatch.setattr("storywell.config.sync_store_path", lambda: tmp_path / "store.json")
    result = runner.invoke(app, ["retag"])
    assert result.exit_code == 0
    assert "retaggable: 1" in result.stdout


def test_cli_retag_no_matched_books(monkeypatch, tmp_path):
    _seed_store(tmp_path / "store.json", {})  # nothing matched yet
    monkeypatch.setattr("storywell.cli._load_finished", _one_audio_book)
    monkeypatch.setattr("storywell.config.sync_store_path", lambda: tmp_path / "store.json")
    result = runner.invoke(app, ["retag"])
    assert result.exit_code == 0
    assert "No matched" in result.stdout
