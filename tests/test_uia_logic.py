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
from fakturama_automation.flow.widgets import infer_date_format, row_contains_all
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
