"""Text-to-value normalisation, including the OCR quirks it exists to absorb."""

import datetime as _dt
from decimal import Decimal

import pytest

from fakturama_automation.errors import ExtractionError
from fakturama_automation.extraction.normalize import (
    fix_digits,
    normalize_sku_family,
    parse_date,
    parse_decimal,
    parse_percent,
    strip_percent,
)


class TestParseDecimal:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("250.00", "250.00"),
            ("EUR 570.00", "570.00"),
            ("1,234.56", "1234.56"),      # thousands comma
            ("1.234,56", "1234.56"),      # European convention
            ("40,00", "40.00"),           # decimal comma
            ("1 234.50", "1234.50"),
            ("-12.50", "-12.50"),
        ],
    )
    def test_formats(self, raw, expected):
        assert parse_decimal(raw) == Decimal(expected)

    def test_repairs_letters_that_ocr_confuses_with_digits(self):
        assert parse_decimal("25O.OO") == Decimal("250.00")
        assert parse_decimal("l0.00") == Decimal("10.00")

    @pytest.mark.parametrize("raw", ["", "   ", "abc", "--"])
    def test_rejects_nonsense(self, raw):
        with pytest.raises(ExtractionError):
            parse_decimal(raw)


class TestParsePercent:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("19%", "19"),
            ("0%", "0"),
            ("7.5%", "7.5"),
            # Windows OCR renders '%' as '0/0'.
            ("190/0", "19"),
            ("100/0", "10"),
            ("00/0", "0"),
        ],
    )
    def test_percent_glyphs(self, raw, expected):
        assert parse_percent(raw) == Decimal(expected)

    def test_rejects_percent_with_no_number(self):
        with pytest.raises(ExtractionError):
            parse_percent("0/0")

    def test_rejects_implausible_bare_number(self):
        with pytest.raises(ExtractionError):
            parse_percent("4500")

    def test_strip_percent_reports_whether_a_marker_was_present(self):
        assert strip_percent("19%") == ("19", True)
        assert strip_percent("19") == ("19", False)


class TestParseDate:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2026-07-14", _dt.date(2026, 7, 14)),
            ("14.07.2026", _dt.date(2026, 7, 14)),
            ("2026/07/14", _dt.date(2026, 7, 14)),
        ],
    )
    def test_formats(self, raw, expected):
        assert parse_date(raw) == expected

    def test_rejects_unparseable(self):
        with pytest.raises(ExtractionError):
            parse_date("someday")


class TestSkuFamily:
    def test_resolves_letter_lookalikes_using_a_sibling_row(self):
        """The real failure from the sample image: CHR-ERG-01 read as CHR-ERG-OI."""
        fixed, corrections = normalize_sku_family(["CHR-ERG-OI", "MAT-DESK-02"])
        assert fixed == ["CHR-ERG-01", "MAT-DESK-02"]
        assert corrections == [{"before": "CHR-ERG-OI", "after": "CHR-ERG-01"}]

    def test_leaves_sku_alone_when_no_sibling_establishes_a_digit_pattern(self):
        fixed, corrections = normalize_sku_family(["CHR-ERG-OI"])
        assert fixed == ["CHR-ERG-OI"]
        assert corrections == []

    def test_does_not_touch_segments_of_a_different_length(self):
        fixed, _ = normalize_sku_family(["AAA-BBB-OIX", "AAA-BBB-02"])
        assert fixed[0] == "AAA-BBB-OIX"

    def test_does_not_touch_genuine_words(self):
        fixed, corrections = normalize_sku_family(["CHR-ERG-AB", "MAT-DESK-02"])
        assert fixed == ["CHR-ERG-AB", "MAT-DESK-02"]
        assert corrections == []

    def test_fix_digits_maps_the_usual_confusions(self):
        assert fix_digits("OISB") == "0158"
