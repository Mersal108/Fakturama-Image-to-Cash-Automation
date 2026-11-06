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
    trim_decimal,
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


class TestPercentGlyphVariants:
    """The percent sign as OCR actually renders it.

    Windows OCR draws '%' as two rings with a bar between them, and reads each
    of those three parts as whatever it resembles. Listing the combinations by
    hand is how 'olo' -- a real magnified read of '0%' -- went unrecognised.
    """

    @pytest.mark.parametrize("glyph", ["0/0", "O/O", "o/o", "0l0", "oIo",
                                       "O|O", "0I0", "olO"])
    def test_a_bare_percent_glyph_has_no_value(self, glyph):
        """A percent sign alone is not a number, whichever way it was read."""
        with pytest.raises(ExtractionError):
            parse_percent(glyph)

    @pytest.mark.parametrize("raw,expected", [
        ("00/0", 0), ("0olo", 0), ("190/0", 19), ("19o/o", 19),
        ("100/0", 10), ("7.50/0", Decimal("7.5")),
    ])
    def test_a_value_in_front_of_the_glyph_is_read(self, raw, expected):
        assert parse_percent(raw) == Decimal(str(expected))


class TestPercentRefusesInventedDigits:
    """A number must be read, not conjured from letter shapes.

    A magnified re-read of a '0%' discount cell came back as 'olo'. The
    letter-to-digit repair turned that into '010' and the run proceeded with a
    confident 10% discount on a line that had none.
    """

    @pytest.mark.parametrize("raw", ["olo", "oIo", "OlO", "ll", "oo"])
    def test_a_digit_free_token_is_refused(self, raw):
        with pytest.raises(ExtractionError):
            parse_percent(raw)

    def test_the_refusal_names_the_problem(self):
        with pytest.raises(ExtractionError) as exc:
            parse_percent("olo", field="discount")
        assert "discount" in str(exc.value)

    def test_a_single_real_digit_is_enough(self):
        """The rule is 'no digits at all', not 'mostly letters'."""
        assert parse_percent("1O/O") == Decimal(1)


class TestTrimDecimal:
    """Trailing zeros go; exponent notation must not arrive instead."""

    @pytest.mark.parametrize("raw,expected", [
        ("10", "10"), ("10.00", "10"), ("100", "100"), ("1000", "1000"),
        ("0", "0"), ("7.50", "7.5"), ("0.5", "0.5"), ("19", "19"),
    ])
    def test_round_numbers_keep_their_plain_form(self, raw, expected):
        """Decimal('10').normalize() is Decimal('1E+1'), which reached the
        extracted-order JSON as a derived discount of '1E+1'."""
        assert str(trim_decimal(Decimal(raw))) == expected


class TestStrayLowercaseInSku:
    """A lowercase letter inside an otherwise uppercase SKU is a misread.

    From a generated document the parser had not seen: 'STD-RIS-09' came back
    as 'STD-RlS-09'. The digit-pattern repair cannot help, because the broken
    segment is letters rather than digits.
    """

    def test_lowercase_l_becomes_uppercase_i(self):
        """'l' and 'I' are the same stroke; uppercasing would give 'L'."""
        fixed, corrections = normalize_sku_family(
            ["STD-RlS-09", "MON-27Q-11", "BOK-DSG-04"])
        assert fixed[0] == "STD-RIS-09"
        assert corrections == [{"before": "STD-RlS-09", "after": "STD-RIS-09"}]

    def test_other_stray_lowercase_is_simply_uppercased(self):
        fixed, _ = normalize_sku_family(["ABC-DeF-01", "ABC-GHI-02"])
        assert fixed[0] == "ABC-DEF-01"

    def test_an_all_lowercase_sku_is_left_alone(self):
        """With no uppercase majority there is nothing to appeal to."""
        fixed, corrections = normalize_sku_family(["abc-def-01"])
        assert fixed == ["abc-def-01"]
        assert corrections == []

    def test_a_sku_with_no_letters_is_left_alone(self):
        fixed, _ = normalize_sku_family(["123-456"])
        assert fixed == ["123-456"]

    def test_the_digit_repair_still_runs(self):
        """The two repairs are independent and must not shadow each other."""
        fixed, _ = normalize_sku_family(["CHR-ERG-OI", "MAT-DESK-02"])
        assert fixed[0] == "CHR-ERG-01"


class TestOcrEquivalentIsCaseInsensitive:
    """The confusion table lists upper-case forms; comparison lower-cases.

    From a live run: Fakturama listed the reference 'WEB-2026-0921-B04'
    correctly, the screen read came back as '...-804', and the two compared
    as different because 'b' never reached the 'B' -> '8' entry. The saved
    Order could not be confirmed as a result.
    """

    def test_lowercase_b_matches_eight(self):
        from fakturama_automation.extraction.normalize import ocr_equivalent

        assert ocr_equivalent("web-2026-0921-b04", "web-2026-0921-804")

    def test_uppercase_still_matches(self):
        from fakturama_automation.extraction.normalize import ocr_equivalent

        assert ocr_equivalent("WEB-B04", "WEB-804")

    def test_a_genuinely_different_character_does_not_match(self):
        from fakturama_automation.extraction.normalize import ocr_equivalent

        assert not ocr_equivalent("WEB-C04", "WEB-804")
        assert not ocr_equivalent("WEB-B04", "WEB-B05")

    def test_different_lengths_never_match(self):
        from fakturama_automation.extraction.normalize import ocr_equivalent

        assert not ocr_equivalent("WEB-B04", "WEB-8040")
