import pytest

from storywell.models import SourceBook
from storywell.sources.base import SourceError
from storywell.sources.csv_source import CsvSource, read_rows, unwrap_isbn


def test_unwrap_isbn_strips_excel_formula_wrapper():
    assert unwrap_isbn('="9780439023481"') == "9780439023481"


def test_unwrap_isbn_handles_empty_formula_and_blanks():
    assert unwrap_isbn('=""') is None
    assert unwrap_isbn("") is None
    assert unwrap_isbn("   ") is None
    assert unwrap_isbn(None) is None


def test_unwrap_isbn_passes_through_bare_value():
    assert unwrap_isbn("0439023483") == "0439023483"
    assert unwrap_isbn("  0439023483  ") == "0439023483"


def test_read_rows_parses_utf8_bom_and_headers(tmp_path):
    csv_file = tmp_path / "books.csv"
    csv_file.write_text("﻿Title,Author\nDune,Frank Herbert\n", encoding="utf-8")
    rows = read_rows(csv_file)
    assert rows == [{"Title": "Dune", "Author": "Frank Herbert"}]


def test_read_rows_raises_source_error_for_missing_file(tmp_path):
    with pytest.raises(SourceError, match="Could not read export file"):
        read_rows(tmp_path / "nope.csv")


def test_read_rows_detects_tab_delimiter(tmp_path):
    tsv = tmp_path / "books.tsv"
    tsv.write_text("Title\tAuthor\nThe Will of the Many\tIslington, James\n", encoding="utf-8")
    assert read_rows(tsv) == [{"Title": "The Will of the Many", "Author": "Islington, James"}]


def test_read_rows_falls_back_to_latin1(tmp_path):
    f = tmp_path / "books.csv"
    f.write_bytes("Title,Author\nCafé,Brontë\n".encode("latin-1"))
    rows = read_rows(f)
    assert rows == [{"Title": "Café", "Author": "Brontë"}]


def test_read_rows_decodes_utf16(tmp_path):
    f = tmp_path / "books.csv"
    f.write_bytes("Title,Author\nDune,Frank Herbert\n".encode("utf-16"))
    assert read_rows(f) == [{"Title": "Dune", "Author": "Frank Herbert"}]


class _ShelfSource(CsvSource):
    name = "shelf"

    def row_to_book(self, row):
        if row.get("Shelf") == "to-read":
            return None
        return SourceBook(
            source=self.name,
            source_id=row["Id"],
            title=row["Title"],
            percent_complete=float(row.get("Percent", 0) or 0),
            is_finished=row.get("Shelf") == "read",
        )


def _write(tmp_path, body):
    csv_file = tmp_path / "shelf.csv"
    csv_file.write_text(body, encoding="utf-8")
    return csv_file


def test_csv_source_requires_a_path():
    with pytest.raises(SourceError, match="needs an export file"):
        _ShelfSource()


def test_csv_source_raises_when_file_missing(tmp_path):
    with pytest.raises(SourceError, match="Export file not found"):
        _ShelfSource(path=tmp_path / "absent.csv")


def test_csv_source_keeps_finished_and_drops_unmapped_rows(tmp_path):
    csv_file = _write(
        tmp_path,
        "Id,Title,Shelf,Percent\n1,Read Book,read,0\n2,Wishlist,to-read,0\n",
    )
    books = _ShelfSource(path=csv_file).finished_books()
    assert [b.source_id for b in books] == ["1"]
    assert books[0].is_finished is True


def test_csv_source_applies_percent_threshold(tmp_path):
    csv_file = _write(
        tmp_path,
        "Id,Title,Shelf,Percent\n1,Almost,reading,94\n2,Done,reading,96\n",
    )
    source = _ShelfSource(path=csv_file)
    assert [b.source_id for b in source.finished_books(threshold=0.95)] == ["2"]
    assert {b.source_id for b in source.finished_books(threshold=0.90)} == {"1", "2"}
