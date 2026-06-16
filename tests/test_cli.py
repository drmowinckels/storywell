from datetime import UTC, datetime

from typer.testing import CliRunner

from storywell.cli import _choose_candidate, app
from storywell.models import SourceBook
from storywell.storygraph import StorygraphDependencyError
from storywell.storygraph.matching import Candidate, ScoredCandidate

runner = CliRunner()


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
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def mark_finished(self, book_id, finish_date=None):
        return True

    def write_review(self, book_id, *, stars_integer="", stars_decimal="", explanation=""):
        return "written"


def _one_book(*a, **k):
    return [
        SourceBook(source="audible", source_id="A1", title="Hyperion", authors=("Dan Simmons",))
    ]


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


def test_cli_sources_lists_audible():
    result = runner.invoke(app, ["sources"])
    assert result.exit_code == 0
    assert "audible" in result.stdout


def test_cli_migrate_store_reports_counts(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    legacy = tmp_path / "audible-storygraph-sync"
    legacy.mkdir(parents=True)
    (legacy / "sync-store.json").write_text('{"mappings": {"B01": "sg1"}, "synced": {}}')
    result = runner.invoke(app, ["migrate-store"])
    assert result.exit_code == 0
    assert "Migrated sync history: 1 matches" in result.stdout


def test_cli_migrate_store_nothing_to_do(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = runner.invoke(app, ["migrate-store"])
    assert result.exit_code == 0
    assert "Nothing to migrate" in result.stdout


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
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda: True)
    result = runner.invoke(app, ["storygraph-status"])
    assert result.exit_code == 0
    assert "active" in result.stdout.lower()


def test_cli_storygraph_status_inactive(monkeypatch):
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda: False)
    result = runner.invoke(app, ["storygraph-status"])
    assert result.exit_code == 1
    assert "storygraph-login" in result.stdout


def test_cli_sync_writes_matches(monkeypatch, tmp_path):
    monkeypatch.setattr("storywell.cli._load_finished", _one_book)
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda: True)
    monkeypatch.setattr("storywell.storygraph.StorygraphBrowser", _FakeBrowser)
    monkeypatch.setattr("storywell.storygraph.search.StorygraphSearcher", _FakeSearcher)
    monkeypatch.setattr("storywell.storygraph.client.StorygraphClient", _FakeClient)
    monkeypatch.setattr("storywell.config.sync_store_path", lambda: tmp_path / "store.json")
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "written: 1" in result.stdout
    assert (tmp_path / "store.json").exists()


def test_cli_sync_no_finished_books(monkeypatch):
    monkeypatch.setattr("storywell.cli._load_finished", lambda *a, **k: [])
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "No finished books" in result.stdout


def test_cli_sync_requires_session(monkeypatch):
    monkeypatch.setattr("storywell.cli._load_finished", _one_book)
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda: False)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1
    assert "storygraph-login" in result.stdout


def test_cli_sync_dry_run_reports_matches(monkeypatch):
    monkeypatch.setattr("storywell.cli._load_finished", _one_book)
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda: True)
    monkeypatch.setattr("storywell.storygraph.search.StorygraphSearcher", _FakeSearcher)
    result = runner.invoke(app, ["sync", "--dry-run"])
    assert result.exit_code == 0
    assert "match:" in result.stdout.lower()


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
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda: True)
    monkeypatch.setattr("storywell.storygraph.search.StorygraphSearcher", _FakeCollSearcher)
    result = runner.invoke(app, ["collections"])
    assert result.exit_code == 0
    assert "Emma" in result.stdout
    assert "Persuasion" in result.stdout


def test_cli_collections_no_dry_run_marks_selected(monkeypatch, tmp_path):
    monkeypatch.setattr("storywell.cli._load_finished", _one_collection)
    monkeypatch.setattr("storywell.storygraph.is_authenticated", lambda: True)
    monkeypatch.setattr("storywell.storygraph.StorygraphBrowser", _FakeBrowser)
    monkeypatch.setattr("storywell.storygraph.search.StorygraphSearcher", _FakeCollSearcher)
    monkeypatch.setattr("storywell.storygraph.client.StorygraphClient", _FakeClient)
    monkeypatch.setattr("storywell.config.sync_store_path", lambda: tmp_path / "store.json")
    result = runner.invoke(app, ["collections", "--no-dry-run"], input="1\n\n")
    assert result.exit_code == 0
    assert "written: 1" in result.stdout
