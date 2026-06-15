from storywell.storygraph.collections import (
    parse_contained_titles,
    parse_titles_from_storygraph_title,
    proposed_titles,
    select_titles,
)

_DICKENS_TITLE = (
    "The Complete Novels of Charles Dickens: Part Two: Dombey and Son, "
    "David Copperfield, Bleak House, Hard Times, Little Dorrit, A Tale of Two Cities, "
    "Great Expectations, Our Mutual Friend, & The Mystery of Edwin Drood By: Charles Dickens"
)


def test_parse_contained_titles_simple_list_strips_years():
    desc = (
        "A lovely set. Included are the following: Sense and Sensibility (1811), "
        "Pride and Prejudice (1813), Emma (1815)."
    )
    assert parse_contained_titles(desc) == [
        "Sense and Sensibility",
        "Pride and Prejudice",
        "Emma",
    ]


def test_parse_contained_titles_handles_section_labels():
    desc = "Included are the following: Major Works: Emma, Persuasion. Minor Works: Lady Susan."
    assert parse_contained_titles(desc) == ["Emma", "Persuasion", "Lady Susan"]


def test_parse_contained_titles_strips_year_ranges_and_varied_labels():
    desc = (
        "Included are the following: Emma, Persuasion (Posthumous, 1818). "
        "Early Works: Lady Susan (1794-1805), Love and Friendship.\n"
        "Experience Austen like never before."
    )
    assert parse_contained_titles(desc) == [
        "Emma",
        "Persuasion",
        "Lady Susan",
        "Love and Friendship",
    ]


def test_parse_contained_titles_empty_when_no_list():
    assert parse_contained_titles("") == []
    assert parse_contained_titles("A sweeping tale of love and loss.") == []


def test_parse_titles_from_storygraph_title_dickens():
    assert parse_titles_from_storygraph_title(_DICKENS_TITLE) == [
        "Dombey and Son",
        "David Copperfield",
        "Bleak House",
        "Hard Times",
        "Little Dorrit",
        "A Tale of Two Cities",
        "Great Expectations",
        "Our Mutual Friend",
        "The Mystery of Edwin Drood",
    ]


def test_parse_titles_from_storygraph_title_empty_for_plain_title():
    assert parse_titles_from_storygraph_title("Hyperion By: Dan Simmons") == []
    assert parse_titles_from_storygraph_title("Jane Austen: The Complete Collection") == []


def test_proposed_titles_prefers_title_then_falls_back_to_description():
    assert proposed_titles(_DICKENS_TITLE, "")[0] == "Dombey and Son"
    assert proposed_titles(
        "Jane Austen: The Complete Collection",
        "Included are the following: Emma, Persuasion.",
    ) == ["Emma", "Persuasion"]


def test_select_titles_by_numbers():
    suggestions = ["A", "B", "C"]
    assert select_titles(suggestions, "1,3") == ["A", "C"]
    assert select_titles(suggestions, "2") == ["B"]
    assert select_titles(suggestions, "1 3") == ["A", "C"]


def test_select_titles_all_and_blank():
    assert select_titles(["A", "B"], "a") == ["A", "B"]
    assert select_titles(["A", "B"], "") == []


def test_select_titles_ignores_out_of_range_and_duplicates():
    assert select_titles(["A", "B"], "1,1,9,x") == ["A"]
