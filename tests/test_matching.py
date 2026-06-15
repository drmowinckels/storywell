from storywell.storygraph.matching import (
    Candidate,
    MatchStatus,
    match_book,
    normalize_author,
    normalize_title,
    search_title,
)


def test_normalize_title_drops_subtitle_and_noise():
    assert normalize_title("American Gods: The Tenth Anniversary Edition") == "american gods"
    assert normalize_title("The Two Towers (Unabridged)") == "the two towers"
    assert normalize_title("Howl’s Moving Castle") == "howl s moving castle"


def test_search_title_strips_series_omnibus_and_free_noise():
    assert search_title("The Amber Spyglass: His Dark Materials Trilogy, Book 3") == (
        "The Amber Spyglass"
    )
    assert search_title("The Hunger Games: Hunger Games Trilogy, Book 1") == "The Hunger Games"
    assert search_title("Wool Omnibus Edition (Wool 1 - 5)") == "Wool"
    assert search_title("FREE STORY: The Relic Guild") == "The Relic Guild"
    assert search_title("FREE: Professional Integrity (A Riyria Chronicles Tale)") == (
        "Professional Integrity"
    )
    assert search_title("The Jester (A Riyria Chronicles Tale)") == "The Jester"


def test_search_title_keeps_plain_titles():
    assert search_title("Hyperion") == "Hyperion"
    assert search_title("American Gods: The Tenth Anniversary Edition").startswith("American Gods")


def test_normalize_title_handles_free_prefix_and_omnibus():
    assert normalize_title("FREE: Professional Integrity (A Riyria Tale)") == (
        "professional integrity"
    )
    assert normalize_title("Wool Omnibus Edition (Wool 1 - 5)") == "wool"


def test_normalize_author_strips_translator_and_diacritics():
    assert normalize_author("Andrzej Sapkowski, David French - translator") == (
        "andrzej sapkowski david french"
    )
    assert normalize_author("Émile Zola") == "emile zola"


def test_exact_title_and_author_is_high_confidence_match():
    cands = [Candidate("b1", "The Will of the Many", "James Islington")]
    result = match_book("The Will of the Many", "James Islington", cands)
    assert result.status is MatchStatus.MATCH
    assert result.best.candidate.book_id == "b1"
    assert result.best.score >= 0.85


def test_subtitle_difference_still_matches():
    cands = [Candidate("b1", "American Gods", "Neil Gaiman")]
    result = match_book("American Gods: The Tenth Anniversary Edition", "Neil Gaiman", cands)
    assert result.status is MatchStatus.MATCH


def test_author_list_superset_still_matches_via_containment():
    cands = [Candidate("b1", "A Memory of Light", "Robert Jordan, Brandon Sanderson")]
    result = match_book("A Memory of Light", "Robert Jordan", cands)
    assert result.status is MatchStatus.MATCH


def test_single_candidate_without_author_matches_on_title_alone():
    cands = [Candidate("b1", "Hyperion", "")]
    result = match_book("Hyperion", "Dan Simmons", cands)
    assert result.status is MatchStatus.MATCH
    assert result.best.author_score == 0.0


def test_identical_editions_resolve_to_top_match():
    cands = [
        Candidate("b1", "A Storm of Swords", "George R.R. Martin"),
        Candidate("b2", "A Storm of Swords", "George R.R. Martin"),
    ]
    result = match_book("A Storm of Swords", "George R.R. Martin", cands)
    assert result.status is MatchStatus.MATCH
    assert result.best.candidate.book_id == "b1"


def test_edition_tie_with_blank_runner_up_author_resolves_to_match():
    cands = [
        Candidate("b1", "The Will of the Many", "James Islington"),
        Candidate("b2", "The Will of the Many", ""),
    ]
    result = match_book("The Will of the Many", "James Islington", cands)
    assert result.status is MatchStatus.MATCH
    assert result.best.candidate.book_id == "b1"


def test_same_title_different_authors_stays_ambiguous():
    cands = [
        Candidate("b1", "Twilight", "Stephenie Meyer"),
        Candidate("b2", "Twilight", "Some Other Author"),
    ]
    result = match_book("Twilight", "", cands)
    assert result.status is MatchStatus.AMBIGUOUS


def test_plausible_but_weak_single_candidate_is_ambiguous():
    cands = [Candidate("b1", "The Great Hunt", "Robert Jordan")]
    result = match_book("The Great Snail", "Robert Jordan", cands)
    assert result.status is MatchStatus.AMBIGUOUS


def test_unrelated_candidate_is_no_match():
    cands = [Candidate("b1", "Pride and Prejudice", "Jane Austen")]
    result = match_book("The Will of the Many", "James Islington", cands)
    assert result.status is MatchStatus.NO_MATCH
    assert result.best is None


def test_empty_candidates_is_no_match():
    result = match_book("Anything", "Anyone", [])
    assert result.status is MatchStatus.NO_MATCH
    assert result.best is None
    assert result.alternatives == ()


def test_best_is_chosen_among_several():
    cands = [
        Candidate("b1", "Pride and Prejudice", "Jane Austen"),
        Candidate("b2", "Children of Time", "Adrian Tchaikovsky"),
        Candidate("b3", "Children of Ruin", "Adrian Tchaikovsky"),
    ]
    result = match_book("Children of Time", "Adrian Tchaikovsky", cands)
    assert result.status is MatchStatus.MATCH
    assert result.best.candidate.book_id == "b2"
