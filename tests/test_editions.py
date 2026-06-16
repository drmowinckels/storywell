from storywell.storygraph.editions import (
    Edition,
    parse_editions,
    pick_edition,
    sg_formats_for,
)


def test_sg_formats_for_maps_audio():
    assert sg_formats_for("audio") == ("audio",)


def test_sg_formats_for_is_case_insensitive_and_empty_for_unknown():
    assert sg_formats_for("AUDIO") == ("audio",)
    assert sg_formats_for("  audio ") == ("audio",)
    assert sg_formats_for("") == ()
    assert sg_formats_for("ebook") == ()  # not yet verified live -> treated as unknown
    assert sg_formats_for("vinyl") == ()


def test_parse_editions_lowercases_format_and_drops_idless_rows():
    records = [
        {"id": "ed1", "format": "Audio"},
        {"id": "", "format": "Audio"},
        {"id": "ed2", "format": "Paperback"},
        {"format": "Hardcover"},
    ]
    editions = parse_editions(records)
    assert editions == [Edition("ed1", "audio"), Edition("ed2", "paperback")]


def test_pick_edition_returns_first_matching_format():
    editions = [
        Edition("paper", "paperback"),
        Edition("audio1", "audio"),
        Edition("audio2", "audio"),
    ]
    assert pick_edition(editions, "audio") == "audio1"


def test_pick_edition_none_when_no_matching_format():
    editions = [Edition("paper", "paperback"), Edition("hard", "hardcover")]
    assert pick_edition(editions, "audio") is None


def test_pick_edition_none_for_unknown_media_format():
    assert pick_edition([Edition("audio1", "audio")], "") is None
