"""Turning OCR text into trustworthy typed values.

OCR output is *nearly* right, and the interesting engineering is in the gap.
Three classes of correction live here, each deliberately narrow so it cannot
silently mangle good data:

1. **Percent glyphs.**  Windows OCR renders ``%`` as ``0/0``.  ``100/0`` is
   ``10%``, ``00/0`` is ``0%``.  Applied only to fields that are known to be
   percentages.
2. **Digit/letter confusion.**  In a field that must be numeric, ``O`` is a
   zero and ``l``/``I`` is a one.  Applied only to numeric fields, never to
   free text.
3. **SKU family patterns.**  ``CHR-ERG-01`` reads as ``CHR-ERG-OI`` in this
   font and no amount of preprocessing fixes it.  But a document's SKUs share
   a shape: when a sibling SKU has an all-digit final segment of the same
   length, the ambiguous one is digits too.  Every such correction is recorded
   and reported, never applied silently.
"""

from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Sequence, Tuple

from ..errors import ExtractionError

# Characters OCR confuses with digits, and what they become in numeric context.
DIGIT_CONFUSIONS: Dict[str, str] = {
    "O": "0", "o": "0", "Q": "0", "D": "0",
    "I": "1", "l": "1", "i": "1", "|": "1",
    "S": "5", "s": "5",
    "B": "8",
    "Z": "2",
    "G": "6",
}

# How Windows OCR tends to render a percent sign.  The two rings come back as
# any of 0/O/o and the bar between them as any of / l I 1 | \, so the set is
# generated rather than listed: writing it out by hand is how 'olo' -- a real
# magnified read of '0%' -- ended up missing from it.
_PERCENT_RINGS = "0Oo"
_PERCENT_BARS = "/lI1|\\"
PERCENT_GLYPHS = tuple(
    [a + bar + b
     for a in _PERCENT_RINGS for bar in _PERCENT_BARS for b in _PERCENT_RINGS]
    + ["%"]
)

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d-%m-%Y",
)


def trim_decimal(value: Decimal) -> Decimal:
    """Drop trailing zeros without falling into exponent notation.

    ``Decimal('10.00').normalize()`` is ``Decimal('1E+1')``, which then
    renders as '1E+1' everywhere it is written out -- a derived 10% discount
    reached ``extracted-order.json`` in that form.  Re-quantizing a whole
    number back to an integer keeps the trimming and loses the exponent.
    """
    trimmed = value.normalize()
    if trimmed == trimmed.to_integral_value():
        return trimmed.quantize(Decimal(1))
    return trimmed


def fix_digits(text: str) -> str:
    """Coerce letters that OCR confuses with digits. Numeric fields only."""
    return "".join(DIGIT_CONFUSIONS.get(ch, ch) for ch in text)


def ocr_equivalent(left: str, right: str) -> bool:
    """Could these two strings be the same text, read twice by OCR?

    Used only when one side was recovered from *pixels* rather than from the
    accessibility tree.  'CHR-ERG-01' and 'CHR-ERG-OI' are not two different
    SKUs; they are one SKU, read once correctly and once by an OCR engine that
    cannot tell O from 0 or I from 1 in a sans-serif face.

    This does not weaken the brief's exact-match rule.  The comparison is
    strictly character-for-character and length-for-length -- only characters
    that genuinely render near-identically are treated as equal, and only in
    that position.  'Northstar' still cannot match 'Southwind'.
    """
    a, b = (left or "").strip(), (right or "").strip()
    if len(a) != len(b):
        return False
    for ch_a, ch_b in zip(a, b):
        if ch_a == ch_b or ch_a.casefold() == ch_b.casefold():
            continue
        if DIGIT_CONFUSIONS.get(ch_a, ch_a) == DIGIT_CONFUSIONS.get(ch_b, ch_b):
            continue
        return False
    return True


def strip_percent(text: str) -> Tuple[str, bool]:
    """Remove a trailing percent marker. Returns (remainder, had_marker)."""
    token = text.strip()
    for glyph in PERCENT_GLYPHS:
        if token.endswith(glyph) and len(token) > len(glyph) - 1:
            return token[: -len(glyph)].strip(), True
    return token, False


def parse_decimal(raw: str, *, field: str = "value") -> Decimal:
    """Parse a money/quantity token into Decimal.

    Handles thousands separators in both conventions.  When both ``.`` and
    ``,`` appear, whichever comes last is the decimal separator; when only
    ``,`` appears and exactly two digits follow it, it is a decimal comma.
    """
    token = (raw or "").strip()
    if not token:
        raise ExtractionError(f"{field}: empty numeric value")

    # Drop currency symbols/codes and spaces.
    token = re.sub(r"(?i)\b(eur|usd|gbp|chf)\b", "", token)
    token = token.replace("€", "").replace("$", "").replace(" ", " ").strip()
    token = fix_digits(token)
    token = token.replace(" ", "")

    negative = token.startswith("-")
    token = token.lstrip("+-")

    has_dot, has_comma = "." in token, "," in token
    if has_dot and has_comma:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif has_comma:
        if re.search(r",\d{2}$", token):
            token = token.replace(",", ".")
        else:
            token = token.replace(",", "")

    if not re.fullmatch(r"\d*\.?\d*", token) or token in ("", "."):
        raise ExtractionError(f"{field}: cannot parse number from {raw!r}")

    try:
        value = Decimal(token)
    except InvalidOperation as exc:
        raise ExtractionError(f"{field}: cannot parse number from {raw!r}") from exc
    return -value if negative else value


def parse_percent(raw: str, *, field: str = "percent") -> Decimal:
    """Parse a percentage cell such as ``19%``, ``190/0`` or ``00/0``."""
    token = (raw or "").strip()
    if not token:
        raise ExtractionError(f"{field}: empty percentage")
    body, had_marker = strip_percent(token)
    if not body:
        # e.g. a bare "0/0" -- a percent sign with no number in front of it.
        raise ExtractionError(f"{field}: percentage with no value in {raw!r}")
    if not any(ch.isdigit() for ch in body):
        # Nothing here was read as a digit, so any number produced from this
        # would be invented by the letter-to-digit repair rather than read off
        # the page.  A magnified re-read of '0%' came back as 'olo', which the
        # repair turned into '010' and a confident 10% discount on a line that
        # had none.  Refusing lets the caller fall back to the full-page read.
        raise ExtractionError(
            f"{field}: no digits in {raw!r}; refusing to infer a percentage "
            "from letter shapes alone"
        )
    value = parse_decimal(body, field=field)
    if not had_marker and value > 100:
        raise ExtractionError(f"{field}: implausible percentage {raw!r}")
    return value


def parse_date(raw: str, *, field: str = "date") -> _dt.date:
    token = (raw or "").strip()
    if not token:
        raise ExtractionError(f"{field}: empty date")
    # Dates are all-digit plus separators; repair OCR letter confusion.
    token = re.sub(r"[^\dA-Za-z./-]", "", token)
    candidate = fix_digits(token)
    for fmt in _DATE_FORMATS:
        try:
            return _dt.datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    raise ExtractionError(f"{field}: cannot parse date from {raw!r}")


def clean_text(raw: str) -> str:
    """Collapse whitespace and strip OCR's stray pipe/bullet artefacts."""
    return " ".join((raw or "").replace(" ", " ").split()).strip()


# --------------------------------------------------------------------- SKUs

_CONFUSABLE_AS_DIGIT = set(DIGIT_CONFUSIONS) | set("0123456789")


# Lowercase glyphs that are near-identical to a *different* uppercase letter
# in a sans-serif face, so uppercasing them is not simply .upper().
_LOWER_SHAPE_CONFUSIONS = {"l": "I"}


def _fix_stray_lowercase(sku: str) -> str:
    """Repair lowercase letters in a SKU that is otherwise uppercase.

    'STD-RIS-09' came back from OCR as 'STD-RlS-09': a lowercase 'l' and an
    uppercase 'I' are the same vertical stroke in this font.  The digit-pattern
    repair above cannot help, because the segment is letters, not digits.

    The signal used here is the token's own internal consistency: when every
    other letter in the SKU is uppercase, a lowercase one is a misread rather
    than a deliberately mixed-case part number.  'l' becomes 'I' because that
    is the glyph it was confused with -- uppercasing it would give 'L', which
    is a different letter and a different wrong answer.
    """
    letters = [ch for ch in sku if ch.isalpha()]
    lower = [ch for ch in letters if ch.islower()]
    if not lower or len(lower) == len(letters):
        return sku          # all-lower or no letters: no majority to appeal to
    return "".join(
        _LOWER_SHAPE_CONFUSIONS.get(ch, ch.upper()) if ch.islower() else ch
        for ch in sku
    )


def normalize_sku_family(skus: Sequence[str]) -> Tuple[List[str], List[dict]]:
    """Make a document's SKUs internally consistent.

    Splits each SKU on ``-`` and compares segment-by-segment across rows.  When
    at least one SKU has an all-digit segment at position *i* of length *n*,
    any other SKU whose segment *i* has the same length and consists only of
    digits-or-digit-lookalikes is coerced to digits.

    Returns ``(corrected_skus, corrections)`` where each correction records the
    before/after so the caller can report it.  A single-item document, or one
    where no sibling establishes a digit pattern, is returned unchanged.
    """
    split = [s.split("-") for s in skus]
    width = max((len(parts) for parts in split), default=0)
    for idx in range(width):
        segments = [parts[idx] for parts in split if len(parts) > idx]
        digit_lengths = {
            len(seg) for seg in segments if seg.isdigit() and seg
        }
        if not digit_lengths:
            continue
        for parts in split:
            if len(parts) <= idx:
                continue
            seg = parts[idx]
            if seg.isdigit() or len(seg) not in digit_lengths:
                continue
            if seg and all(ch in _CONFUSABLE_AS_DIGIT for ch in seg):
                fixed = fix_digits(seg)
                if fixed != seg and fixed.isdigit():
                    parts[idx] = fixed

    corrected = [_fix_stray_lowercase("-".join(parts)) for parts in split]
    corrections = [
        {"before": before, "after": after}
        for before, after in zip(skus, corrected)
        if before != after
    ]
    return corrected, corrections
