from datetime import UTC, date, datetime

import pytest

from storywell.models import Shelf, SourceBook
from storywell.storygraph.editions import Edition
from storywell.storygraph.matching import (
    Candidate,
    MatchResult,
    MatchStatus,
    ScoredCandidate,
)
from storywell.storygraph.session import StorygraphAuthError
from storywell.storygraph.store import SyncStore
from storywell.storygraph.sync import (
    SyncPlanItem,
    TitleEntry,
    plan_retag,
    plan_sync,
    query_for,
    resolve_match,
    run_review_sync,
    run_sync,
    run_title_sync,
    summarize,
    target_shelf,
)


def _book(title, *authors, source_id="A", finished_at=None, media_format="", status=Shelf.UNKNOWN):
    return SourceBook(
        source="audible",
        source_id=source_id,
        title=title,
        authors=tuple(authors),
        finished_at=finished_at,
        media_format=media_format,
        status=status,
    )


class _FakeWriter:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def mark_shelf(self, book_id, status, date=None):
        self.calls.append((book_id, status, date))
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


def test_plan_sync_reports_progress_per_book():
    seen = []
    plan_sync(
        [_book("A", "X"), _book("B", "Y"), _book("C", "Z")],
        lambda q: [],
        on_progress=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(1, 3), (2, 3), (3, 3)]


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
        _book(
            "Hyperion",
            "Dan Simmons",
            source_id="A1",
            finished_at=datetime(2023, 8, 18, tzinfo=UTC),
        )
    ]
    writer = _FakeWriter()
    store = _store(tmp_path)
    outcome = run_sync(
        books,
        search_fn=lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")],
        writer=writer,
        store=store,
    )
    assert outcome.written == ["audible:A1"]
    assert writer.calls == [("b1", Shelf.READ, date(2023, 8, 18))]
    assert store.is_synced("audible:A1", date(2023, 8, 18), Shelf.READ.value) is True


def test_run_sync_dedupes_same_book_across_sources(tmp_path):
    shared_isbn = "978-0-441-17271-9"
    books = [
        SourceBook(
            source="audible",
            source_id="A1",
            title="Dune",
            authors=("Frank Herbert",),
            isbn13=shared_isbn,
        ),
        SourceBook(
            source="kobo",
            source_id="K9",
            title="Dune",
            authors=("Frank Herbert",),
            isbn13=shared_isbn,
            is_finished=True,
            finished_at=datetime(2023, 8, 18, tzinfo=UTC),
        ),
    ]
    writer = _FakeWriter()
    store = _store(tmp_path)

    outcome = run_sync(
        books,
        search_fn=lambda q: [Candidate("b1", "Dune", "Frank Herbert")],
        writer=writer,
        store=store,
    )

    assert outcome.written == ["kobo:K9"]  # only the winner is pushed, once
    assert writer.calls == [("b1", Shelf.READ, date(2023, 8, 18))]


def test_run_sync_skips_already_synced(tmp_path):
    store = _store(tmp_path)
    store.record("audible:A1", "b1", date(2023, 8, 18))
    books = [_book("X", source_id="A1", finished_at=datetime(2023, 8, 18, tzinfo=UTC))]
    writer = _FakeWriter()
    outcome = run_sync(books, search_fn=lambda q: [], writer=writer, store=store)
    assert outcome.skipped_synced == ["audible:A1"]
    assert writer.calls == []


def test_run_sync_uses_cached_book_id_without_searching(tmp_path):
    store = _store(tmp_path)
    store.remember_match("audible:A1", "bX")
    searched = []

    def search(query):
        searched.append(query)
        return []

    writer = _FakeWriter()
    outcome = run_sync([_book("X", source_id="A1")], search_fn=search, writer=writer, store=store)
    assert searched == []
    assert writer.calls == [("bX", Shelf.READ, None)]
    assert outcome.written == ["audible:A1"]


def test_run_sync_no_match(tmp_path):
    books = [_book("Zzz Unknown", "Nobody", source_id="A1")]
    outcome = run_sync(
        books,
        search_fn=lambda q: [Candidate("b", "Totally Different Title", "X")],
        writer=_FakeWriter(),
        store=_store(tmp_path),
    )
    assert outcome.no_match == ["audible:A1"]


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
        [_book("Twilight", source_id="A1")],
        search_fn=lambda q: _ambiguous_candidates(),
        writer=writer,
        store=_store(tmp_path),
        confirm_fn=lambda book, result: result.best.candidate,
    )
    assert outcome.written == ["audible:A1"]


def test_run_sync_ambiguous_skipped_when_confirm_declines(tmp_path):
    outcome = run_sync(
        [_book("Twilight", source_id="A1")],
        search_fn=lambda q: _ambiguous_candidates(),
        writer=_FakeWriter(),
        store=_store(tmp_path),
        confirm_fn=lambda book, result: None,
    )
    assert outcome.ambiguous_skipped == ["audible:A1"]


def test_run_sync_dry_run_plans_without_writing(tmp_path):
    writer = _FakeWriter()
    outcome = run_sync(
        [_book("Hyperion", "Dan Simmons", source_id="A1")],
        search_fn=lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")],
        writer=writer,
        store=_store(tmp_path),
        dry_run=True,
    )
    assert outcome.planned == ["audible:A1"]
    assert writer.calls == []


class _FakeEditionResolver:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.calls = []

    def __call__(self, book_id, media_format):
        self.calls.append((book_id, media_format))
        return self.mapping.get(book_id)


def test_run_sync_marks_the_audio_edition_for_an_audiobook_source(tmp_path):
    writer = _FakeWriter()
    store = _store(tmp_path)
    resolver = _FakeEditionResolver({"b1": "audio-ed"})
    outcome = run_sync(
        [_book("Hyperion", "Dan Simmons", source_id="A1", media_format="audio")],
        search_fn=lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")],
        writer=writer,
        store=store,
        edition_fn=resolver,
    )
    assert resolver.calls == [("b1", "audio")]
    assert writer.calls == [("audio-ed", Shelf.READ, None)]
    assert outcome.written == ["audible:A1"]
    assert store.cached_book_id("audible:A1") == "audio-ed"


def test_run_sync_falls_back_to_best_match_when_no_audio_edition(tmp_path):
    writer = _FakeWriter()
    resolver = _FakeEditionResolver(mapping={})  # no audio edition for b1
    run_sync(
        [_book("Hyperion", "Dan Simmons", source_id="A1", media_format="audio")],
        search_fn=lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")],
        writer=writer,
        store=_store(tmp_path),
        edition_fn=resolver,
    )
    assert writer.calls == [("b1", Shelf.READ, None)]


def test_run_sync_skips_edition_resolution_without_media_format(tmp_path):
    writer = _FakeWriter()
    resolver = _FakeEditionResolver({"b1": "audio-ed"})
    run_sync(
        [_book("Hyperion", "Dan Simmons", source_id="A1")],
        search_fn=lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")],
        writer=writer,
        store=_store(tmp_path),
        edition_fn=resolver,
    )
    assert resolver.calls == []
    assert writer.calls == [("b1", Shelf.READ, None)]


def test_run_sync_does_not_re_resolve_cached_edition(tmp_path):
    store = _store(tmp_path)
    store.remember_match("audible:A1", "audio-ed")
    resolver = _FakeEditionResolver({"audio-ed": "should-not-be-used"})
    writer = _FakeWriter()
    run_sync(
        [_book("X", source_id="A1", media_format="audio")],
        search_fn=lambda q: [],
        writer=writer,
        store=store,
        edition_fn=resolver,
    )
    assert resolver.calls == []
    assert writer.calls == [("audio-ed", Shelf.READ, None)]


def test_run_sync_skips_marking_when_read_on_another_edition(tmp_path):
    # the cross-edition duplicate guard: a freshly matched book the reader already read on a
    # different StoryGraph edition must be left unmarked, and recorded so it is not rechecked.
    writer = _FakeWriter()
    store = _store(tmp_path)
    checked = []
    outcome = run_sync(
        [_book("Wool", source_id="A1", finished_at=datetime(2023, 8, 18, tzinfo=UTC))],
        search_fn=lambda q: [Candidate("b1", "Wool", "Hugh Howey")],
        writer=writer,
        store=store,
        read_elsewhere_fn=lambda book_id: checked.append(book_id) or True,
    )
    assert checked == ["b1"]
    assert writer.calls == []
    assert outcome.skipped_other_edition == ["audible:A1"]
    assert outcome.written == []
    assert store.is_synced("audible:A1", date(2023, 8, 18), Shelf.READ.value) is True


def test_run_sync_marks_normally_when_not_read_on_another_edition(tmp_path):
    writer = _FakeWriter()
    outcome = run_sync(
        [_book("Wool", source_id="A1", finished_at=datetime(2023, 8, 18, tzinfo=UTC))],
        search_fn=lambda q: [Candidate("b1", "Wool", "Hugh Howey")],
        writer=writer,
        store=_store(tmp_path),
        read_elsewhere_fn=lambda book_id: False,
    )
    assert writer.calls == [("b1", Shelf.READ, date(2023, 8, 18))]
    assert outcome.written == ["audible:A1"]


def test_run_sync_does_not_check_other_edition_for_cached_book(tmp_path):
    # a book already matched on a prior run is the storywell-marked edition; the cross-edition
    # check (which scrapes the editions page) must not run for it every time.
    store = _store(tmp_path)
    store.remember_match("audible:A1", "bX")
    checked = []
    writer = _FakeWriter()
    run_sync(
        [_book("X", source_id="A1", finished_at=datetime(2023, 8, 18, tzinfo=UTC))],
        search_fn=lambda q: [],
        writer=writer,
        store=store,
        read_elsewhere_fn=lambda book_id: checked.append(book_id) or True,
    )
    assert checked == []
    assert writer.calls == [("bX", Shelf.READ, date(2023, 8, 18))]


def test_run_sync_dry_run_reports_other_edition_skip_without_recording(tmp_path):
    store = _store(tmp_path)
    outcome = run_sync(
        [_book("Wool", source_id="A1", finished_at=datetime(2023, 8, 18, tzinfo=UTC))],
        search_fn=lambda q: [Candidate("b1", "Wool", "Hugh Howey")],
        writer=_FakeWriter(),
        store=store,
        read_elsewhere_fn=lambda book_id: True,
        dry_run=True,
    )
    assert outcome.skipped_other_edition == ["audible:A1"]
    assert outcome.planned == []
    assert store.is_synced("audible:A1", date(2023, 8, 18), Shelf.READ.value) is False


def test_run_sync_does_not_check_other_edition_for_non_read_shelf(tmp_path):
    # cross-edition reads only matter for the read shelf; a to-read routing never checks.
    checked = []
    writer = _FakeWriter()
    run_sync(
        [_book("X", source_id="A1")],
        search_fn=lambda q: [Candidate("b1", "X", "")],
        writer=writer,
        store=_store(tmp_path),
        read_elsewhere_fn=lambda book_id: checked.append(book_id) or True,
        default_shelf=Shelf.TO_READ,
    )
    assert checked == []
    assert writer.calls == [("b1", Shelf.TO_READ, None)]


# --- shelf routing ----------------------------------------------------------------------


def test_target_shelf_finished_signal_wins():
    finished = _book("X", finished_at=datetime(2023, 8, 18, tzinfo=UTC))
    assert target_shelf(finished, default_shelf=Shelf.TO_READ) is Shelf.READ
    flagged = SourceBook(source="s", source_id="1", title="X", is_finished=True)
    assert target_shelf(flagged, default_shelf=Shelf.TO_READ) is Shelf.READ


def test_target_shelf_uses_declared_status_when_not_finished():
    book = _book("X", status=Shelf.CURRENTLY_READING)
    assert target_shelf(book, default_shelf=None) is Shelf.CURRENTLY_READING
    # an explicit status beats the caller's default
    assert target_shelf(book, default_shelf=Shelf.TO_READ) is Shelf.CURRENTLY_READING


def test_target_shelf_falls_back_to_default_shelf():
    book = _book("X")  # unfinished, status unknown
    assert target_shelf(book, default_shelf=Shelf.TO_READ) is Shelf.TO_READ


def test_target_shelf_defaults_to_read_without_default_shelf():
    # legacy behaviour: a surfaced book with no other signal is read.
    assert target_shelf(_book("X"), default_shelf=None) is Shelf.READ


def test_run_sync_routes_unfinished_book_to_default_shelf(tmp_path):
    writer = _FakeWriter()
    store = _store(tmp_path)
    outcome = run_sync(
        [_book("Hyperion", "Dan Simmons", source_id="A1")],
        search_fn=lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")],
        writer=writer,
        store=store,
        default_shelf=Shelf.TO_READ,
    )
    assert writer.calls == [("b1", Shelf.TO_READ, None)]
    assert outcome.written == ["audible:A1"]
    # dateless shelf is keyed on the shelf, not an empty date
    assert store.is_synced("audible:A1", None, Shelf.TO_READ.value) is True


def test_run_sync_routes_declared_status_over_default(tmp_path):
    writer = _FakeWriter()
    outcome = run_sync(
        [_book("Hyperion", "Dan Simmons", source_id="A1", status=Shelf.CURRENTLY_READING)],
        search_fn=lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")],
        writer=writer,
        store=_store(tmp_path),
        default_shelf=Shelf.TO_READ,
    )
    assert writer.calls == [("b1", Shelf.CURRENTLY_READING, None)]
    assert outcome.written == ["audible:A1"]


def test_run_sync_finished_book_ignores_default_shelf(tmp_path):
    writer = _FakeWriter()
    outcome = run_sync(
        [
            _book(
                "Hyperion",
                "Dan Simmons",
                source_id="A1",
                finished_at=datetime(2023, 8, 18, tzinfo=UTC),
            )
        ],
        search_fn=lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")],
        writer=writer,
        store=_store(tmp_path),
        default_shelf=Shelf.TO_READ,
    )
    assert writer.calls == [("b1", Shelf.READ, date(2023, 8, 18))]
    assert outcome.written == ["audible:A1"]


def test_run_sync_skips_already_synced_dateless_shelf(tmp_path):
    store = _store(tmp_path)
    store.record("audible:A1", "b1", None, Shelf.TO_READ.value)
    writer = _FakeWriter()
    outcome = run_sync(
        [_book("X", source_id="A1")],
        search_fn=lambda q: [],
        writer=writer,
        store=store,
        default_shelf=Shelf.TO_READ,
    )
    assert outcome.skipped_synced == ["audible:A1"]
    assert writer.calls == []


def test_run_sync_re_syncs_when_shelf_changes(tmp_path):
    store = _store(tmp_path)
    store.record("audible:A1", "b1", None, Shelf.TO_READ.value)
    writer = _FakeWriter()
    outcome = run_sync(
        [_book("X", source_id="A1", status=Shelf.CURRENTLY_READING)],
        search_fn=lambda q: [],
        writer=writer,
        store=store,
    )
    # the same book moved to a different shelf is not "already synced"
    assert outcome.written == ["audible:A1"]
    assert writer.calls == [("b1", Shelf.CURRENTLY_READING, None)]


def test_run_sync_records_failure(tmp_path):
    outcome = run_sync(
        [_book("Hyperion", "Dan Simmons", source_id="A1")],
        search_fn=lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")],
        writer=_FakeWriter(ok=False),
        store=_store(tmp_path),
    )
    assert outcome.failed == ["audible:A1"]
    assert outcome.written == []


def test_run_title_sync_writes_each_title(tmp_path):
    entries = [TitleEntry(key="audible:c::emma", title="Emma", finish_date=date(2020, 1, 1))]
    writer = _FakeWriter()
    outcome = run_title_sync(
        entries,
        search_fn=lambda q: [Candidate("b1", "Emma", "")],
        writer=writer,
        store=_store(tmp_path),
    )
    assert outcome.written == ["audible:c::emma"]
    assert writer.calls == [("b1", Shelf.READ, date(2020, 1, 1))]


def test_run_title_sync_skips_already_synced(tmp_path):
    store = _store(tmp_path)
    store.record("audible:c::emma", "b1", date(2020, 1, 1))
    entries = [TitleEntry(key="audible:c::emma", title="Emma", finish_date=date(2020, 1, 1))]
    outcome = run_title_sync(entries, search_fn=lambda q: [], writer=_FakeWriter(), store=store)
    assert outcome.skipped_synced == ["audible:c::emma"]


def test_run_title_sync_marks_audio_edition_when_entry_has_format(tmp_path):
    entries = [
        TitleEntry(key="audible:c::emma", title="Emma", media_format="audio"),
    ]
    writer = _FakeWriter()
    resolver = _FakeEditionResolver({"b1": "audio-ed"})
    outcome = run_title_sync(
        entries,
        search_fn=lambda q: [Candidate("b1", "Emma", "")],
        writer=writer,
        store=_store(tmp_path),
        edition_fn=resolver,
    )
    assert resolver.calls == [("b1", "audio")]
    assert writer.calls == [("audio-ed", Shelf.READ, None)]
    assert outcome.written == ["audible:c::emma"]


def test_run_title_sync_no_match(tmp_path):
    entries = [TitleEntry(key="audible:c::zzz", title="Zzz Unknown Book")]
    outcome = run_title_sync(
        entries,
        search_fn=lambda q: [Candidate("b", "Totally Different Title", "")],
        writer=_FakeWriter(),
        store=_store(tmp_path),
    )
    assert outcome.no_match == ["audible:c::zzz"]


class _FakeRater:
    def __init__(self, status="written"):
        self.status = status
        self.calls = []

    def write_review(self, book_id, *, stars_integer, stars_decimal, explanation):
        self.calls.append((book_id, stars_integer, stars_decimal, explanation))
        return self.status


def _rated_book(source_id="A1", rating=5.0, narrators=("Moira Quirk",), review=None):
    return SourceBook(
        source="audible",
        source_id=source_id,
        title="Nona the Ninth",
        rating=rating,
        narrators=tuple(narrators),
        review=review,
    )


def test_run_review_sync_writes_rating_and_narrator_note(tmp_path):
    store = _store(tmp_path)
    book = _rated_book()
    store.remember_match(book.key, "sg1")
    rater = _FakeRater()
    outcome = run_review_sync([book], rater=rater, store=store)
    assert outcome.written == [book.key]
    assert rater.calls == [("sg1", "5", "", "Narrated by Moira Quirk.")]
    assert store.is_rated(book.key) is True


def test_run_review_sync_skips_already_rated(tmp_path):
    store = _store(tmp_path)
    book = _rated_book()
    store.remember_match(book.key, "sg1")
    store.record_rated(book.key)
    rater = _FakeRater()
    outcome = run_review_sync([book], rater=rater, store=store)
    assert outcome.skipped_synced == [book.key]
    assert rater.calls == []


def test_run_review_sync_no_match_when_unmatched(tmp_path):
    store = _store(tmp_path)
    outcome = run_review_sync([_rated_book()], rater=_FakeRater(), store=store)
    assert outcome.no_match == ["audible:A1"]


def test_run_review_sync_existing_storygraph_review_recorded_as_done(tmp_path):
    store = _store(tmp_path)
    book = _rated_book()
    store.remember_match(book.key, "sg1")
    outcome = run_review_sync([book], rater=_FakeRater(status="skipped"), store=store)
    assert outcome.skipped_synced == [book.key]
    assert store.is_rated(book.key) is True


def _editions_fn(mapping):
    return lambda book_id: mapping.get(book_id, [])


def test_plan_retag_classifies_each_matched_book(tmp_path):
    store = _store(tmp_path)
    store.remember_match("audible:A", "pap")  # on paperback, audio exists -> retaggable
    store.remember_match("audible:B", "aud")  # already on audio
    store.remember_match("audible:C", "only")  # paperback only -> no audio edition
    store.remember_match("audible:D", "boom")  # editions unreadable -> unknown
    books = [
        _book("A", source_id="A", media_format="audio"),
        _book("B", source_id="B", media_format="audio"),
        _book("C", source_id="C", media_format="audio"),
        _book("D", source_id="D", media_format="audio"),
    ]
    editions_fn = _editions_fn(
        {
            "pap": [Edition("pap", "paperback"), Edition("aud2", "audio")],
            "aud": [Edition("aud", "audio"), Edition("pap2", "paperback")],
            "only": [Edition("only", "paperback")],
            "boom": [],
        }
    )
    items = {i.key: i for i in plan_retag(books, store=store, editions_fn=editions_fn)}
    assert items["audible:A"].status == "retaggable"
    assert items["audible:A"].audio_id == "aud2"
    assert items["audible:B"].status == "already_audio"
    assert items["audible:C"].status == "no_audio_edition"
    assert items["audible:D"].status == "unknown"


def test_plan_retag_skips_unmatched_and_non_audio_books(tmp_path):
    store = _store(tmp_path)
    store.remember_match("audible:MATCHED", "pap")
    books = [
        _book("Unmatched", source_id="UNMATCHED", media_format="audio"),  # no mapping
        _book("Ebook", source_id="EBK", media_format="ebook"),  # not an audio source
        _book("Matched", source_id="MATCHED", media_format="audio"),
    ]
    editions_fn = _editions_fn({"pap": [Edition("pap", "paperback"), Edition("a", "audio")]})
    items = plan_retag(books, store=store, editions_fn=editions_fn)
    assert [i.key for i in items] == ["audible:MATCHED"]


class _RaisingWriter:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def mark_shelf(self, book_id, status, date=None):
        self.calls += 1
        raise self.exc


def test_run_sync_one_book_failure_does_not_abort_batch(tmp_path):
    books = [
        _book("Boom", "X", source_id="A1"),
        _book("Hyperion", "Dan Simmons", source_id="A2"),
    ]

    class _FlakyWriter:
        def __init__(self):
            self.calls = []

        def mark_shelf(self, book_id, status, date=None):
            self.calls.append(book_id)
            if book_id == "boom":
                raise RuntimeError("transient browser error")
            return True

    def search(query):
        if query.startswith("Boom"):
            return [Candidate("boom", "Boom", "X")]
        return [Candidate("b2", "Hyperion", "Dan Simmons")]

    store = _store(tmp_path)
    outcome = run_sync(books, search_fn=search, writer=_FlakyWriter(), store=store)
    assert outcome.failed == ["audible:A1"]
    assert outcome.written == ["audible:A2"]
    assert store.is_synced("audible:A2", None, Shelf.READ.value) is True


def test_run_sync_reraises_auth_error(tmp_path):
    writer = _RaisingWriter(StorygraphAuthError("session expired"))
    with pytest.raises(StorygraphAuthError):
        run_sync(
            [_book("Hyperion", "Dan Simmons", source_id="A1")],
            search_fn=lambda q: [Candidate("b1", "Hyperion", "Dan Simmons")],
            writer=writer,
            store=_store(tmp_path),
        )


def test_run_review_sync_one_book_failure_does_not_abort_batch(tmp_path):
    store = _store(tmp_path)
    good, bad = _rated_book(source_id="A1"), _rated_book(source_id="A2")
    store.remember_match(good.key, "sg1")
    store.remember_match(bad.key, "sg2")

    class _FlakyRater:
        def write_review(self, book_id, *, stars_integer, stars_decimal, explanation):
            if book_id == "sg2":
                raise RuntimeError("transient")
            return "written"

    outcome = run_review_sync([good, bad], rater=_FlakyRater(), store=store)
    assert outcome.written == [good.key]
    assert outcome.failed == [bad.key]


def test_run_review_sync_reraises_auth_error(tmp_path):
    store = _store(tmp_path)
    book = _rated_book()
    store.remember_match(book.key, "sg1")

    class _AuthRater:
        def write_review(self, book_id, *, stars_integer, stars_decimal, explanation):
            raise StorygraphAuthError("session expired")

    with pytest.raises(StorygraphAuthError):
        run_review_sync([book], rater=_AuthRater(), store=store)


def test_run_review_sync_records_failed_status(tmp_path):
    store = _store(tmp_path)
    book = _rated_book()
    store.remember_match(book.key, "sg1")
    outcome = run_review_sync([book], rater=_FakeRater(status="failed"), store=store)
    assert outcome.failed == [book.key]
    assert store.is_rated(book.key) is False


def test_run_title_sync_one_book_failure_does_not_abort_batch(tmp_path):
    entries = [
        TitleEntry(key="audible:c::boom", title="Boom"),
        TitleEntry(key="audible:c::emma", title="Emma"),
    ]

    class _FlakyWriter:
        def mark_shelf(self, book_id, status, date=None):
            if book_id == "boom":
                raise RuntimeError("transient")
            return True

    def search(query):
        if query.startswith("Boom"):
            return [Candidate("boom", "Boom", "")]
        return [Candidate("emma", "Emma", "")]

    outcome = run_title_sync(
        entries, search_fn=search, writer=_FlakyWriter(), store=_store(tmp_path)
    )
    assert outcome.failed == ["audible:c::boom"]
    assert outcome.written == ["audible:c::emma"]


def test_run_title_sync_reraises_auth_error(tmp_path):
    writer = _RaisingWriter(StorygraphAuthError("session expired"))
    with pytest.raises(StorygraphAuthError):
        run_title_sync(
            [TitleEntry(key="audible:c::emma", title="Emma")],
            search_fn=lambda q: [Candidate("emma", "Emma", "")],
            writer=writer,
            store=_store(tmp_path),
        )
