from datetime import date

from storywell.stats.parse import (
    ReadStatus,
    narrators,
    parse_bool,
    parse_date,
    parse_dates_read,
    parse_int,
    parse_list,
    parse_rating,
    parse_status,
)


def test_parse_status_maps_known_values_and_aliases():
    assert parse_status("read") is ReadStatus.READ
    assert parse_status("to-read") is ReadStatus.TO_READ
    assert parse_status("did-not-finish") is ReadStatus.DID_NOT_FINISH
    assert parse_status("Currently Reading") is ReadStatus.CURRENTLY_READING
    assert parse_status("") is ReadStatus.UNKNOWN
    assert parse_status("nonsense") is ReadStatus.UNKNOWN


def test_parse_date_accepts_slashes_and_dashes():
    assert parse_date("2024/01/14") == date(2024, 1, 14)
    assert parse_date("2024-01-14") == date(2024, 1, 14)
    assert parse_date("") is None
    assert parse_date("not a date") is None


def test_parse_dates_read_single_date_is_finish_only():
    instances = parse_dates_read("2024/01/14")
    assert len(instances) == 1
    assert instances[0].start is None
    assert instances[0].end == date(2024, 1, 14)
    assert instances[0].days is None
    assert instances[0].finished_year == 2024


def test_parse_dates_read_range_yields_start_and_end():
    (instance,) = parse_dates_read("2024/01/05-2024/01/14")
    assert instance.start == date(2024, 1, 5)
    assert instance.end == date(2024, 1, 14)
    assert instance.days == 9


def test_parse_dates_read_splits_multiple_reads_on_semicolon():
    first, second = parse_dates_read("2023/12/18-2023/12/28; 2024/06/01-2024/06/05")
    assert first.days == 10
    assert first.finished_year == 2023
    assert second.days == 4
    assert second.finished_year == 2024


def test_parse_dates_read_handles_iso_dash_dates():
    # An all-dashes export must not be silently shredded by splitting on '-'.
    (single,) = parse_dates_read("2024-01-14")
    assert single.start is None
    assert single.end == date(2024, 1, 14)

    (ranged,) = parse_dates_read("2024-01-05-2024-01-14")
    assert ranged.start == date(2024, 1, 5)
    assert ranged.end == date(2024, 1, 14)
    assert ranged.days == 9


def test_parse_dates_read_reversed_range_reports_no_duration():
    (instance,) = parse_dates_read("2024/02/03-2024/01/20")
    assert instance.days is None  # end before start -> not a usable span
    assert instance.finished_year == 2024


def test_parse_dates_read_drops_chunks_with_no_date():
    assert parse_dates_read("sometime last spring") == []


def test_parse_dates_read_empty_is_no_instances():
    assert parse_dates_read("") == []
    assert parse_dates_read(None) == []


def test_parse_list_splits_and_strips():
    assert parse_list("reflective, mysterious ,adventurous") == (
        "reflective",
        "mysterious",
        "adventurous",
    )
    assert parse_list("") == ()
    assert parse_list(None) == ()
    assert parse_list("N. K. Jemisin") == ("N. K. Jemisin",)


def test_parse_rating_handles_numbers_and_blanks():
    assert parse_rating("4.5") == 4.5
    assert parse_rating("") is None
    assert parse_rating("abc") is None


def test_parse_bool_yes_no_else_none():
    assert parse_bool("Yes") is True
    assert parse_bool("No") is False
    assert parse_bool("") is None
    assert parse_bool("maybe") is None


def test_parse_int_defaults_on_blank_and_garbage():
    assert parse_int("2") == 2
    assert parse_int("") == 0
    assert parse_int("x", default=1) == 1


def test_narrators_extracts_only_narrator_contributors():
    contributors = ("Chris Hoult (Narrator)", "Some Editor (Editor)")
    assert narrators(contributors) == ("Chris Hoult",)
    assert narrators(()) == ()
