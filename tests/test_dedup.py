from datetime import UTC, datetime

from storywell.models import SourceBook
from storywell.storygraph.dedup import identity_key, merge_duplicates


def _book(
    source,
    source_id,
    title,
    *authors,
    isbn13=None,
    isbn=None,
    finished_at=None,
    is_finished=False,
    rating=None,
    review=None,
):
    return SourceBook(
        source=source,
        source_id=source_id,
        title=title,
        authors=tuple(authors),
        isbn13=isbn13,
        isbn=isbn,
        finished_at=finished_at,
        is_finished=is_finished,
        rating=rating,
        review=review,
    )


def test_identity_key_prefers_isbn13_then_isbn10():
    book13 = _book("g", "1", "Dune", isbn13="978-0-441-17271-9")
    assert identity_key(book13) == "isbn:9780441172719"
    assert identity_key(_book("g", "1", "Dune", isbn="0441172717")) == "isbn:0441172717"


def test_identity_key_falls_back_to_normalized_title_when_no_isbn():
    assert identity_key(_book("audible", "A", "Dune: Special Edition")) == "fuzzy:dune"


def test_identity_key_is_none_without_isbn_or_title():
    assert identity_key(_book("audible", "A", "")) is None


def test_merge_collapses_same_isbn_across_sources():
    audible = _book("audible", "A", "Dune", "Frank Herbert", isbn13="978-0-441-17271-9")
    kobo = _book(
        "kobo",
        "K",
        "Dune",
        "Frank Herbert",
        isbn13="978-0-441-17271-9",
        is_finished=True,
        finished_at=datetime(2024, 1, 2, tzinfo=UTC),
    )

    merged = merge_duplicates([audible, kobo])

    assert len(merged) == 1
    assert merged[0].source == "kobo"  # the finished, dated record wins


def test_merge_collapses_fuzzy_title_author_when_no_isbn():
    audible = _book("audible", "A", "The Hobbit (Unabridged)", "J.R.R. Tolkien")
    apple = _book(
        "apple",
        "P",
        "The Hobbit",
        "J. R. R. Tolkien",
        is_finished=True,
        finished_at=datetime(2024, 3, 1, tzinfo=UTC),
    )

    merged = merge_duplicates([audible, apple])

    assert len(merged) == 1
    assert merged[0].source == "apple"


def test_merge_keeps_genuinely_different_books():
    dune = _book("audible", "A", "Dune", "Frank Herbert")
    hyperion = _book("kobo", "K", "Hyperion", "Dan Simmons")

    merged = merge_duplicates([dune, hyperion])

    assert {b.title for b in merged} == {"Dune", "Hyperion"}


def test_merge_does_not_collapse_same_title_different_author():
    a = _book("audible", "A", "Beautiful World", "Sally Rooney")
    b = _book("kobo", "K", "Beautiful World", "Someone Else")

    merged = merge_duplicates([a, b])

    assert len(merged) == 2


def test_merge_keeps_isbn_less_title_only_books_separate():
    a = _book("audible", "A", "Untitled Memo")
    b = _book("kobo", "K", "Untitled Memo")

    merged = merge_duplicates([a, b])

    assert len(merged) == 2  # no author to corroborate, so don't merge


def test_isbn_match_never_overridden_by_fuzzy():
    with_isbn = _book("kobo", "K", "Dune", "Frank Herbert", isbn13="978-0-441-17271-9")
    without_isbn = _book("audible", "A", "Dune", "Frank Herbert")

    merged = merge_duplicates([with_isbn, without_isbn])

    # Different identity keys (isbn: vs fuzzy:), so both survive — an ISBN record is exact and
    # is not absorbed by a fuzzy title/author guess.
    assert len(merged) == 2


def test_winner_prefers_finished_over_unfinished():
    unfinished = _book("audible", "A", "Dune", "Frank Herbert", isbn13="9780441172719")
    finished = _book(
        "kobo", "K", "Dune", "Frank Herbert", isbn13="9780441172719", is_finished=True
    )

    merged = merge_duplicates([unfinished, finished])

    assert len(merged) == 1
    assert merged[0].source == "kobo"


def test_winner_prefers_record_with_finish_date():
    no_date = _book("audible", "A", "Dune", isbn13="9780441172719", is_finished=True)
    dated = _book(
        "kobo",
        "K",
        "Dune",
        isbn13="9780441172719",
        is_finished=True,
        finished_at=datetime(2024, 5, 1, tzinfo=UTC),
    )

    merged = merge_duplicates([no_date, dated])

    assert merged[0].source == "kobo"


def test_winner_prefers_richer_metadata_when_finish_state_equal():
    lean = _book("audible", "A", "Dune", isbn13="9780441172719", is_finished=True)
    rich = _book(
        "kobo",
        "K",
        "Dune",
        "Frank Herbert",
        isbn13="9780441172719",
        is_finished=True,
        rating=4.5,
        review="Loved it",
    )

    merged = merge_duplicates([lean, rich])

    assert merged[0].source == "kobo"


def test_winner_is_order_independent():
    lean = _book("audible", "A", "Dune", isbn13="9780441172719", is_finished=True)
    rich = _book(
        "kobo",
        "K",
        "Dune",
        "Frank Herbert",
        isbn13="9780441172719",
        is_finished=True,
        rating=4.5,
    )

    forward = merge_duplicates([lean, rich])
    backward = merge_duplicates([rich, lean])

    assert forward[0].source == backward[0].source == "kobo"


def test_winner_stable_tiebreak_on_key_when_all_else_equal():
    a = _book("aaa", "1", "Dune", isbn13="9780441172719")
    b = _book("bbb", "2", "Dune", isbn13="9780441172719")

    assert merge_duplicates([a, b])[0].key == "aaa:1"
    assert merge_duplicates([b, a])[0].key == "aaa:1"


def test_merge_preserves_first_seen_order_of_survivors():
    first = _book("audible", "A", "Hyperion", "Dan Simmons")
    dup_a = _book("kobo", "K", "Dune", "Frank Herbert", isbn13="9780441172719")
    dup_b = _book("apple", "P", "Dune", "Frank Herbert", isbn13="9780441172719")
    last = _book("audible", "B", "Neuromancer", "William Gibson")

    merged = merge_duplicates([first, dup_a, dup_b, last])

    assert [b.title for b in merged] == ["Hyperion", "Dune", "Neuromancer"]


def test_merge_passes_through_books_without_identity():
    a = _book("audible", "A", "")
    b = _book("audible", "B", "")

    merged = merge_duplicates([a, b])

    assert len(merged) == 2
