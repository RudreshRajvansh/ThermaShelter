from __future__ import annotations

import pytest

from shelter_engine.idf import _field_text, idf_text


def test_commas_are_stripped_because_they_end_a_field():
    assert idf_text("Leh, Ladakh") == "Leh Ladakh"


def test_semicolons_and_bangs_are_stripped():
    assert idf_text("wall; north") == "wall north"
    assert idf_text("note ! comment") == "note comment"


def test_whitespace_is_collapsed():
    assert idf_text("  a   b  ") == "a b"


def test_non_strings_pass_through_untouched():
    assert idf_text(3.5) == 3.5
    assert idf_text(None) is None
    assert idf_text(True) is True


def test_field_text_keeps_whole_numbers_readable():
    assert _field_text(2.0) == "2"
    assert _field_text(3500.0) == "3500"


def test_field_text_keeps_precision_where_it_matters():
    assert _field_text(0.038) == "0.038"
    assert float(_field_text(1.2345678901)) == pytest.approx(1.23456789)


def test_field_text_handles_blanks():
    assert _field_text(None) == ""
    assert _field_text("  spaced  ") == "spaced"
