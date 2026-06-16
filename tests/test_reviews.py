from storywell.storygraph.reviews import compose_review, rating_to_stars


def test_rating_to_stars_integer():
    assert rating_to_stars(4.0) == ("4", "")
    assert rating_to_stars(5.0) == ("5", "")


def test_rating_to_stars_quarter_increments():
    assert rating_to_stars(4.25) == ("4", "25")
    assert rating_to_stars(4.5) == ("4", "5")
    assert rating_to_stars(3.75) == ("3", "75")


def test_rating_to_stars_snaps_off_grid_to_nearest_quarter():
    assert rating_to_stars(4.3) == ("4", "25")
    assert rating_to_stars(3.7) == ("3", "75")
    assert rating_to_stars(4.9) == ("5", "")


def test_rating_to_stars_clamps_out_of_range():
    assert rating_to_stars(6.0) == ("5", "")
    assert rating_to_stars(-1.0) == ("0", "")


def test_compose_review_combines_body_and_narrator_note():
    assert compose_review("loved it", ("Carrington MacDuffie",)) == (
        "loved it\n\nNarrated by Carrington MacDuffie."
    )


def test_compose_review_narrator_only():
    assert compose_review(None, ("A", "B")) == "Narrated by A, B."


def test_compose_review_body_only():
    assert compose_review("great", ()) == "great"


def test_compose_review_none_when_empty():
    assert compose_review(None, ()) is None
    assert compose_review("   ", ()) is None
