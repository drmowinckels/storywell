from audible_storygraph_sync.models import Audiobook
from audible_storygraph_sync.storygraph.matching import Candidate, MatchStatus
from audible_storygraph_sync.storygraph.sync import (
    SyncPlanItem,
    plan_sync,
    query_for,
    summarize,
)


def _book(title, *authors):
    return Audiobook(asin="A", title=title, authors=tuple(authors))


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
    from audible_storygraph_sync.storygraph.matching import MatchResult

    return MatchResult(status, None)
