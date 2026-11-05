"""Pure logic inside the UI-automation layer.

The flow itself needs a running Fakturama, but the decisions it makes do not:
how a caption is compared, whether a control is positioned like the input that
belongs to a label, how a date format is inferred, whether a search result row
counts as an exact match.  Those are the parts most likely to be wrong, so
they are tested directly.
"""

import datetime as _dt

import pytest

from fakturama_automation.extraction.parser import OrderImageParser
from fakturama_automation.flow.widgets import (
    cell_matches, infer_date_format, row_contains_all,
)
from fakturama_automation.uia.actions import (
    _numeric_key,
    escape_keys,
    values_match,
)
from fakturama_automation.uia.locators import (
    Rect,
    _directional_distance,
    matches,
    normalize,
)


class TestCaptionNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("&Save", "save"),
            ("Cust.Ref.:", "cust.ref."),
            ("  Order  ", "order"),
            ("*Order 1234", "order 1234"),      # Eclipse dirty-editor marker
            ("Terms  of   payment", "terms of payment"),
            (None, ""),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize(raw) == expected

    def test_exact_match_is_exact(self):
        assert matches("Invoice", "Invoice")
        assert not matches("Invoice address", "Invoice")

    def test_substring_match_when_asked(self):
        assert matches("Invoice address", "Invoice", exact=False)


class TestLabelAnchoring:
    """The geometry behind "the Edit just right of this label"."""

    def label(self):
        return Rect(100, 200, 180, 220)

    def test_input_to_the_right_on_the_same_line_is_found(self):
        distance = _directional_distance(self.label(), Rect(190, 200, 400, 220), "right")
        assert distance is not None

    def test_input_on_a_different_line_is_rejected(self):
        assert _directional_distance(
            self.label(), Rect(190, 400, 400, 420), "right"
        ) is None

    def test_input_to_the_left_is_rejected_when_looking_right(self):
        assert _directional_distance(
            self.label(), Rect(10, 200, 90, 220), "right"
        ) is None

    def test_nearer_input_scores_lower(self):
        near = _directional_distance(self.label(), Rect(190, 200, 400, 220), "right")
        far = _directional_distance(self.label(), Rect(500, 200, 700, 220), "right")
        assert near < far

    def test_below_direction_needs_horizontal_overlap(self):
        assert _directional_distance(
            self.label(), Rect(110, 230, 260, 250), "below"
        ) is not None
        assert _directional_distance(
            self.label(), Rect(900, 230, 1100, 250), "below"
        ) is None

    def test_unknown_direction_is_a_programming_error(self):
        with pytest.raises(ValueError):
            _directional_distance(self.label(), Rect(0, 0, 1, 1), "sideways")


class TestRectOverlap:
    def test_vertical_overlap_ratio(self):
        a, b = Rect(0, 0, 100, 20), Rect(0, 10, 100, 30)
        assert a.vertical_overlap(b) == pytest.approx(0.5)

    def test_no_overlap(self):
        assert Rect(0, 0, 100, 20).vertical_overlap(Rect(0, 50, 100, 70)) == 0.0


class TestValueComparison:
    @pytest.mark.parametrize(
        "actual,expected",
        [
            ("678.30", "678.30"),
            ("678,30", "678.30"),          # locale decimal comma
            ("EUR 678.30", "678.30"),
            ("1.234,56", "1234.56"),
            ("678.3", "678.30"),
        ],
    )
    def test_numeric_comparison_ignores_formatting(self, actual, expected):
        assert values_match(actual, expected, numeric=True)

    def test_numeric_comparison_still_catches_a_real_difference(self):
        assert not values_match("678.30", "679.30", numeric=True)

    def test_text_comparison_is_normalised(self):
        assert values_match("  Bank Transfer ", "bank transfer")

    def test_numeric_key_of_junk_does_not_explode(self):
        assert _numeric_key("n/a") is not None


class TestKeyEscaping:
    """send_keys() treats these as syntax; literal text must survive."""

    @pytest.mark.parametrize("char", list("^+%~(){}[]"))
    def test_special_characters_are_escaped(self, char):
        assert escape_keys(char) == "{" + char + "}"

    def test_ordinary_text_is_untouched(self):
        assert escape_keys("WEB-2026-0714-A17") == "WEB-2026-0714-A17"

    def test_phone_number_with_plus_is_escaped(self):
        assert escape_keys("+49 30 5550 1420") == "{+}49 30 5550 1420"


class TestDateFormatInference:
    """Dates are written in whatever format the control already shows."""

    @pytest.mark.parametrize(
        "sample,expected",
        [
            ("2026-07-14", "%Y-%m-%d"),
            ("14.07.2026", "%d.%m.%Y"),
            ("Jul 14, 2026", "%b %d, %Y"),
        ],
    )
    def test_infers_the_format_on_screen(self, sample, expected):
        assert infer_date_format(sample) == expected

    def test_round_trips_the_target_date(self):
        fmt = infer_date_format("Jul 14, 2026")
        assert _dt.date(2026, 7, 18).strftime(fmt) == "Jul 18, 2026"

    def test_returns_none_for_non_dates(self):
        assert infer_date_format("") is None
        assert infer_date_format("not a date") is None


class TestExactRowMatching:
    """Brief 2.3: a Debtor is only reused on an exact field match."""

    ROW = ["CUST0001", "Marta", "Klein", "Northstar Office GmbH", "10117", "Berlin"]

    def test_accepts_a_row_matching_every_required_value(self):
        assert row_contains_all(
            self.ROW,
            ["Northstar Office GmbH", "Marta", "Klein", "10117", "Berlin"],
        )

    def test_rejects_a_row_with_a_different_zip(self):
        assert not row_contains_all(self.ROW, ["Northstar Office GmbH", "10555"])

    def test_does_not_accept_a_partial_city_match(self):
        """'Berlin' must not match a cell reading 'Berlin-Mitte'."""
        row = ["CUST0002", "Marta", "Klein", "Northstar Office GmbH",
               "10117", "Berlin-Mitte"]
        assert not row_contains_all(row, ["Berlin"])

    def test_ignores_empty_requirements(self):
        assert row_contains_all(self.ROW, ["Marta", "", None or ""])


class TestPositionAndSkuSplit:
    split = staticmethod(OrderImageParser._split_position_and_sku)

    def test_row_number_before_the_sku(self):
        assert self.split("2 MAT-DESK-02", 9) == (2, "MAT-DESK-02")

    def test_row_number_after_the_sku(self):
        """Crop order is not stable, so either arrangement must work."""
        assert self.split("MAT-DESK-02 2", 9) == (2, "MAT-DESK-02")

    def test_sku_alone_falls_back_to_the_row_index(self):
        assert self.split("CHR-ERG-01", 1) == (1, "CHR-ERG-01")

    def test_empty_cell(self):
        assert self.split("   ", 3) == (3, "")


class TestTruncatedCellMatching:
    """A table that visibly shortens a value must still be matchable."""

    def test_matches_a_cell_the_table_truncated(self):
        row = ["CUST000001", "Marta", "Klein", "Northstar Office …",
               "10117", "Berlin"]
        assert row_contains_all(
            row, ["Northstar Office GmbH", "Marta", "Klein", "10117", "Berlin"]
        )

    def test_truncation_must_still_be_a_real_prefix(self):
        row = ["CUST000001", "Marta", "Klein", "Southwind Trading …",
               "10117", "Berlin"]
        assert not row_contains_all(
            row, ["Northstar Office GmbH", "Marta", "Klein", "10117", "Berlin"]
        )

    def test_other_fields_are_still_compared_exactly(self):
        row = ["CUST000001", "Marta", "Kleinmann", "Northstar Office …",
               "10117", "Berlin"]
        assert not row_contains_all(
            row, ["Northstar Office GmbH", "Marta", "Klein", "10117", "Berlin"]
        )

    def test_a_full_cell_is_not_treated_as_a_prefix(self):
        row = ["CUST000001", "Marta", "Klein", "Northstar", "10117", "Berlin"]
        assert not row_contains_all(
            row, ["Northstar Office GmbH", "Marta", "Klein", "10117", "Berlin"]
        )

    def test_marker_glyph_varies_with_the_ocr_read(self):
        """The same painted ellipsis is recognised as different characters."""
        required = ["Northstar Office GmbH", "Marta", "Klein", "10117", "Berlin"]
        for marker in ("…", "•", "�", "...", "»"):
            row = ["CUST000001", "Marta", "Klein",
                   f"Northstar Office {marker}", "10117", "Berlin"]
            assert row_contains_all(row, required), f"failed for {marker!r}"

    def test_a_trailing_word_is_not_a_truncation_marker(self):
        row = ["CUST000001", "Marta", "Klein", "Northstar Office AG",
               "10117", "Berlin"]
        assert not row_contains_all(
            row, ["Northstar Office GmbH", "Marta", "Klein", "10117", "Berlin"]
        )


class TestOcrTolerantMatching:
    """Rows read from pixels carry OCR confusions; rows from UIA do not."""

    def test_sku_read_from_screen_matches_the_real_sku(self):
        assert cell_matches("CHR-ERG-OI", "CHR-ERG-01", ocr_tolerant=True)

    def test_the_same_confusion_is_rejected_without_tolerance(self):
        assert not cell_matches("CHR-ERG-OI", "CHR-ERG-01")

    def test_a_genuinely_different_sku_still_fails(self):
        assert not cell_matches("CHR-ERG-02", "CHR-ERG-01", ocr_tolerant=True)

    def test_different_words_are_never_equivalent(self):
        assert not cell_matches("Southwind", "Northstar", ocr_tolerant=True)

    def test_length_must_match(self):
        assert not cell_matches("CHR-ERG-1", "CHR-ERG-01", ocr_tolerant=True)

    def test_row_matching_accepts_a_screen_read_sku(self):
        row = ["CHR-ERG-OI", "Ergonomic Desk Chair", "297.50"]
        assert row_contains_all(row, ["CHR-ERG-01"], ocr_tolerant=True)
        assert not row_contains_all(row, ["CHR-ERG-01"])


class TestScreenGridRows:
    """Turning recognised lines into grid rows.

    This is the part of the NatTable fallback that can be reasoned about
    without a screen: which line is the header, which column a word lands in,
    and which lines are not rows at all.
    """

    @staticmethod
    def _line(*pairs):
        """A line of (text, x) pairs at a fixed y, as OCR would report it."""
        from fakturama_automation.extraction.geometry import Box
        from fakturama_automation.extraction.ocr import OcrWord

        return [OcrWord(text=text, box=Box(x, 10.0, 8.0 * len(text), 12.0))
                for text, x in pairs]

    def _rows(self, lines, width=500.0, has_header=True):
        from fakturama_automation.uia.screen_grid import rows_from_lines

        return rows_from_lines(lines, width, has_header=has_header)

    def test_header_only_grid_yields_no_rows(self):
        """An empty list must report nothing -- not its own column headings.

        A search that found no match used to come back claiming one candidate
        row, because the header was the only line and so was treated as data.
        """
        header = self._line(("No.", 10), ("Name", 150), ("Company", 300))
        assert self._rows([header]) == []

    def test_header_is_not_counted_as_a_row(self):
        header = self._line(("No.", 10), ("Name", 150))
        body = self._line(("CUST000001", 10), ("Klein", 150))
        rows = self._rows([header, body])
        assert len(rows) == 1
        assert rows[0].cells[0] == "CUST000001"

    def test_words_land_in_the_column_they_are_drawn_under(self):
        header = self._line(("No.", 10), ("Name", 150), ("Company", 300))
        body = self._line(("CUST000001", 10), ("Klein", 150),
                          ("Northstar", 300))
        cells = self._rows([header, body])[0].cells
        assert cells == ["CUST000001", "Klein", "Northstar"]

    def test_words_in_one_cell_are_joined_in_reading_order(self):
        header = self._line(("No.", 10), ("Company", 150))
        body = self._line(("CUST000001", 10), ("Office", 190),
                          ("Northstar", 150))
        assert self._rows([header, body])[0].cells[1] == "Northstar Office"

    def test_gridline_noise_is_not_a_row(self):
        """NatTable's rules and half-scrolled rows produce punctuation only."""
        header = self._line(("No.", 10), ("Name", 150))
        noise = self._line((".", 10), ("-", 150))
        assert self._rows([header, noise]) == []

    def test_headerless_read_clusters_by_gaps(self):
        """A dropdown has no header, so cells come from spacing alone."""
        line = self._line(("Bank", 10), ("Transfer", 50), ("Cash", 400))
        cells = self._rows([line], has_header=False)[0].cells
        assert cells == ["Bank Transfer", "Cash"]

    def test_no_lines_is_not_an_error(self):
        assert self._rows([]) == []


class TestGridTextCleanup:
    """Artefacts the screen read has to remove before a value can be compared."""

    def test_column_rules_are_stripped(self):
        from fakturama_automation.uia.screen_grid import _strip_rules

        assert _strip_rules("1.00 |") == "1.00"
        assert _strip_rules("| 250.00 |") == "250.00"

    def test_meaningful_punctuation_survives(self):
        """'%', '$' and '-' carry meaning in a cell; a pipe never does."""
        from fakturama_automation.uia.screen_grid import _strip_rules

        assert _strip_rules("19%") == "19%"
        assert _strip_rules("$250.00") == "$250.00"
        assert _strip_rules("-10.00") == "-10.00"
        assert _strip_rules("CHR-ERG-01") == "CHR-ERG-01"


class TestHeaderMerge:
    """Combining header readings taken at different magnifications."""

    @staticmethod
    def _word(text, x, width=20.0):
        from fakturama_automation.extraction.geometry import Box
        from fakturama_automation.extraction.ocr import OcrWord

        return OcrWord(text=text, box=Box(x, 5.0, width, 12.0))

    def _merge(self, *readings):
        from fakturama_automation.uia.screen_grid import _merge_header_words

        return [w.text for w in _merge_header_words(readings)]

    def test_a_caption_only_one_pass_saw_is_added(self):
        """Only the x2 pass recognises 'Qty.' on the Order's item grid."""
        coarse = [self._word("Pos.", 6), self._word("Name", 146)]
        fine = [self._word("Pos.", 6), self._word("Qty.", 46),
                self._word("Name", 146)]
        assert self._merge(coarse, fine) == ["Pos.", "Qty.", "Name"]

    def test_the_capitalised_reading_wins(self):
        """x3 reads 'Pos.' as 'pos.'; the better-cased reading should survive."""
        lower = [self._word("pos.", 6)]
        upper = [self._word("Pos.", 6)]
        assert self._merge(lower, upper) == ["Pos."]

    def test_the_longer_reading_wins(self):
        assert self._merge([self._word("U.Pric", 346, 36)],
                           [self._word("U.Price", 346, 36)]) == ["U.Price"]

    def test_distinct_columns_are_never_merged(self):
        """Overlap decides identity, so neighbours stay separate."""
        reading = [self._word("Qty.", 46), self._word("Name", 146)]
        assert self._merge(reading, reading) == ["Qty.", "Name"]

    def test_merging_keeps_left_to_right_order(self):
        assert self._merge([self._word("Price", 446)],
                           [self._word("Pos.", 6)]) == ["Pos.", "Price"]


class TestPaintedCanvasDetection:
    """Telling a grid that draws itself from a pane that holds a real control.

    Both report children, so the test is how much of the pane a child fills:
    a NatTable cell editor is tiny inside its grid, while the Remarks box is
    filled by its Edit.  Going by control type instead picked the Remarks box
    as the order's item grid.
    """

    class _Rect:
        def __init__(self, width, height):
            self.width, self.height = width, height

    class _Child:
        def __init__(self, control_type, width, height):
            self.element_info = type("I", (), {"control_type": control_type})()
            self._w, self._h = width, height

    class _Pane:
        def __init__(self, children):
            self._children = children

        def children(self):
            return self._children

    def _is_canvas(self, pane_size, children, monkeypatch):
        import fakturama_automation.flow.product as product

        monkeypatch.setattr(
            product, "rect_of",
            lambda c: TestPaintedCanvasDetection._Rect(c._w, c._h))
        return product._is_painted_canvas(
            self._Pane(children), self._Rect(*pane_size))

    def test_an_empty_pane_is_a_canvas(self, monkeypatch):
        assert self._is_canvas((1994, 286), [], monkeypatch)

    def test_a_cell_editor_does_not_disqualify_the_grid(self, monkeypatch):
        """Selecting a product leaves a combo on the VAT cell: 99x19."""
        editor = self._Child("Pane", 99, 19)
        assert self._is_canvas((1994, 286), [editor], monkeypatch)

    def test_scrollbars_do_not_disqualify_the_grid(self, monkeypatch):
        """A NatTable publishes its scrollbars but not one of its rows."""
        bar = self._Child("ScrollBar", 20, 286)
        assert self._is_canvas((1994, 286), [bar], monkeypatch)

    def test_a_pane_filled_by_its_control_is_not_a_canvas(self, monkeypatch):
        """The Remarks box: one Edit, nearly its full size."""
        edit = self._Child("Edit", 900, 230)
        assert not self._is_canvas((912, 234), [edit], monkeypatch)

    def test_a_zero_sized_pane_is_not_a_canvas(self, monkeypatch):
        assert not self._is_canvas((0, 0), [], monkeypatch)
