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

# How Windows OCR tends to render a percent sign.
PERCENT_GLYPHS = ("0/0", "O/O", "o/o", "0/O", "O/0", "%")

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d-%m-%Y",
)


def fix_digits(text: str) -> str:
    """Coerce letters that OCR confuses with digits. Numeric fields only."""
    return "".join(DIGIT_CONFUSIONS.get(ch, ch) for ch in text)


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

    corrected = ["-".join(parts) for parts in split]
    corrections = [
        {"before": before, "after": after}
        for before, after in zip(skus, corrected)
        if before != after
    ]
    return corrected, corrections
