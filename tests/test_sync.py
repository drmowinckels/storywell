from datetime import UTC, date, datetime

from audible_storygraph_sync.models import Audiobook
from audible_storygraph_sync.storygraph.matching import (
    Candidate,
    MatchResult,
    MatchStatus,
    ScoredCandidate,
)
from audible_storygraph_sync.storygraph.store import SyncStore
from audible_storygraph_sync.storygraph.sync import (
    SyncPlanItem,
    plan_sync,
    query_for,
    resolve_match,
    run_sync,
    summarize,
)


def _book(title, *authors, asin="A", finished_at=None):
    return Audiobook(asin=asin, title=title, authors=tuple(authors), finished_at=finished_at)


class _FakeWriter:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def mark_finished(self, book_id, finish_date=None):
        self.calls.append((book_id, finish_date))
        return self.ok


def _store(tmp_path):
    return SyncStore.load(tmp_path / "store.json")


def test_query_for_combines_title_and_first_author():
    assert query_for(_book("Hyperion", "Dan Simmons", "Other")) == "Hyperion Dan Simmons"
    assert query_for(_book("Untitled")) == "Untitled"


def test_plan_sync_matches_each_book_with_search_results():
    books = [_book("Hyperion", "Dan Simmons"), _book("Nonexistent Book", "Nobody")]

    def fake_search(query):
        if query.startswith("Hyperion"):
            return [Candidate("b1", "Hyperion", "Dan Simmons")]
        return []

    items = plan_sync(books, fake_search)
    assert [i.result.status for i in items] == [MatchStatus.MATCH, MatchStatus.NO_MATCH]
    assert items[0].result.best.candidate.book_id == "b1"


def test_plan_sync_passes_query_per_book():
    seen = []

    def fake_search(query):
        seen.append(query)
        return []

    plan_sync([_book("A", "X"), _book("B", "Y")], fake_search)
    assert seen == ["A X", "B Y"]


def test_summarize_counts_each_status():
    items = [
        SyncPlanItem(_book("a"), _result(MatchStatus.MATCH)),
        SyncPlanItem(_book("b"), _result(MatchStatus.MATCH)),
        SyncPlanItem(_book("c"), _result(MatchStatus.AMBIGUOUS)),
    ]
    counts = summarize(items)
    assert counts[MatchStatus.MATCH] == 2
    assert counts[MatchStatus.AMBIGUOUS] == 1
    assert counts[MatchStatus.NO_MATCH] == 0


def _result(status):
    return MatchResult(status, None)


def test_resolve_match_returns_best_for_match():
    scored = ScoredCandidate(Candidate("b1", "T", "A"), 0.9, 0.9, 0.9)
    result = MatchResult(MatchStatus.MATCH, scored)
    assert resolve_match(_book("x"), result, None).book_id == "b1"


def test_resolve_match_uses_confirm_fn_for_ambiguous():
    scored = ScoredCandidate(Candidate("b2", "T", "A"), 0.7, 0.7, 0.7)
    result = MatchResult(MatchStatus.AMBIGUOUS, scored)
    chosen = resolve_match(_book("x"), result, lambda book, res: res.best.candidate)
    assert chosen.book_id == "b2"


def test_resolve_match_none_for_ambiguous_without_confirm():
    scored = ScoredCandidate(Candidate("b2", "T", "A"), 0.7, 0.7, 0.7)
    result = MatchResult(MatchStatus.AMBIGUOUS, scored)
    assert resolve_match(_book("x"), result, None) is None


def test_run_sync_writes_high_confidence_match(tmp_path):
    books = [
        _book("Hyperion", "Dan Simmons", asin="A1", finished_at=datetime(2023, 8, 18, tzinfo=UTC))
    ]
    writer = _FakeWriter()
    store = _store(tmp_path)
    outcome = run_sync(
        books,
        search_fn=lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")],
        writer=writer,
        store=store,
    )
    assert outcome.written == ["A1"]
    assert writer.calls == [("b1", date(2023, 8, 18))]
    assert store.is_synced("A1", date(2023, 8, 18)) is True


def test_run_sync_skips_already_synced(tmp_path):
    store = _store(tmp_path)
    store.record("A1", "b1", date(2023, 8, 18))
    books = [_book("X", asin="A1", finished_at=datetime(2023, 8, 18, tzinfo=UTC))]
    writer = _FakeWriter()
    outcome = run_sync(books, search_fn=lambda q: [], writer=writer, store=store)
    assert outcome.skipped_synced == ["A1"]
    assert writer.calls == []


def test_run_sync_uses_cached_book_id_without_searching(tmp_path):
    store = _store(tmp_path)
    store.remember_match("A1", "bX")
    searched = []

    def search(query):
        searched.append(query)
        return []

    writer = _FakeWriter()
    outcome = run_sync([_book("X", asin="A1")], search_fn=search, writer=writer, store=store)
    assert searched == []
    assert writer.calls == [("bX", None)]
    assert outcome.written == ["A1"]


def test_run_sync_no_match(tmp_path):
    books = [_book("Zzz Unknown", "Nobody", asin="A1")]
    outcome = run_sync(
        books,
        search_fn=lambda q: [Candidate("b", "Totally Different Title", "X")],
        writer=_FakeWriter(),
        store=_store(tmp_path),
    )
    assert outcome.no_match == ["A1"]


def _ambiguous_candidates():
    # same title, different non-blank authors, and the book has no author to
    # disambiguate -> genuinely ambiguous (not an edition tie).
    return [
        Candidate("b1", "Twilight", "Stephenie Meyer"),
        Candidate("b2", "Twilight", "Some Other Author"),
    ]


def test_run_sync_ambiguous_confirmed_is_written(tmp_path):
    writer = _FakeWriter()
    outcome = run_sync(
        [_book("Twilight", asin="A1")],
        search_fn=lambda q: _ambiguous_candidates(),
        writer=writer,
        store=_store(tmp_path),
        confirm_fn=lambda book, result: result.best.candidate,
    )
    assert outcome.written == ["A1"]


def test_run_sync_ambiguous_skipped_when_confirm_declines(tmp_path):
    outcome = run_sync(
        [_book("Twilight", asin="A1")],
        search_fn=lambda q: _ambiguous_candidates(),
        writer=_FakeWriter(),
        store=_store(tmp_path),
        confirm_fn=lambda book, result: None,
    )
    assert outcome.ambiguous_skipped == ["A1"]


def test_run_sync_dry_run_plans_without_writing(tmp_path):
    writer = _FakeWriter()
    outcome = run_sync(
        [_book("Hyperion", "Dan Simmons", asin="A1")],
        search_fn=lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")],
        writer=writer,
        store=_store(tmp_path),
        dry_run=True,
    )
    assert outcome.planned == ["A1"]
    assert writer.calls == []


def test_run_sync_records_failure(tmp_path):
    outcome = run_sync(
        [_book("Hyperion", "Dan Simmons", asin="A1")],
        search_fn=lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")],
        writer=_FakeWriter(ok=False),
        store=_store(tmp_path),
    )
    assert outcome.failed == ["A1"]
    assert outcome.written == []
