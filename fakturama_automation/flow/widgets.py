"""Layout-relative helpers for widgets that expose no usable name.

Some of Fakturama's controls are bare icons -- the pair beside **Addresses**,
where the upper one opens the existing-contact selector and the lower green
``+`` starts a brand new debtor.  The brief is explicit that picking the wrong
one breaks the flow.

These helpers address such controls by their *relationship* to a named anchor
("the first button to the right of the Addresses label, topmost first"), never
by a screen coordinate.  Order among siblings is a layout fact that survives
window resizing and DPI changes; a pixel position is not.
"""

from __future__ import annotations

import datetime as _dt
import time as _time
from typing import Any, List, Optional, Sequence

from ..errors import ControlNotFoundError, VerificationError
from ..logging_setup import get_logger
from ..uia.actions import (
    escape_keys, focus, read_value, require_foreground,
)
from ..extraction.normalize import ocr_equivalent
from ..uia.locators import normalize, rect_of, safe_name

log = get_logger("widget")

# Formats to *type* into a date control, best first.
#
# Fakturama's date field is a segmented widget (Nebula CDateTime), not a plain
# text box: it consumes a fixed number of digits per segment.  Measured against
# the real control, '07/14/2026' is accepted and renders as 'Jul. 14, 2026',
# while the unpadded '7/14/2026' silently lands the digits in the wrong
# segments and yields 'Feb. 06, 2026'.  Zero-padded, four-digit-year forms
# therefore come first, and nothing here may rely on free-text parsing.
INPUT_FORMATS = (
    "%m/%d/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y",
    "%b. %d, %Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y",
)

# Formats used to *read a date back*.  Four-digit years only: a control showing
# 'Jul. 14, 26' cannot distinguish 2026 from the year 26, and accepting that
# ambiguity is exactly how a mis-entered date would reach a saved invoice.
DISPLAY_FORMATS = (
    "%b. %d, %Y", "%b %d, %Y", "%B %d, %Y", "%B. %d, %Y",
    "%m/%d/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y",
)

# Two-digit-year renderings are accepted only to confirm a value we typed with
# an explicit four-digit year, so the century came from us and not from the
# widget guessing.
AMBIGUOUS_DISPLAY_FORMATS = (
    "%b. %d, %y", "%b %d, %y", "%B %d, %y",
    "%m/%d/%y", "%d.%m.%y", "%d/%m/%y", "%y-%m-%d",
)

# Kept for format inference and for callers that want the whole vocabulary.
DATE_FORMATS = DISPLAY_FORMATS + AMBIGUOUS_DISPLAY_FORMATS


# SWT renders these small clickable icons inconsistently: the pair beside
# *Addresses* and the column beside *Items* come through UIA as Image elements,
# while the follow-up document icons are real toolbar Buttons.  Searching all
# of the plausible types keeps the caller from having to know which is which.
ICON_TYPES = ("Button", "Image", "SplitButton", "Hyperlink")


def icons_near(
    app,
    anchor_labels: Sequence[str],
    *,
    control_types: Sequence[str] = ICON_TYPES,
    max_distance: int = 220,
    root=None,
) -> List[Any]:
    """Clickable icons positioned next to a named label, ordered top-to-bottom.

    Used for the two unnamed icons beside *Addresses* and the one beside the
    items table.  Returns them in visual order so callers can say "the upper
    one" without knowing where on screen it is.
    """
    anchors: List[Any] = []
    for label in anchor_labels:
        anchors.extend(app.finder.find_labels(label, root=root))
    if not anchors:
        raise ControlNotFoundError(
            f"anchor label {list(anchor_labels)} not found, so its icons cannot be located"
        )

    def icons_under(scope) -> List[Any]:
        found: List[Any] = []
        for control_type in control_types:
            found.extend(app.finder.descendants(control_type=control_type, root=scope))
        return found

    chosen: List[Any] = []
    for anchor in anchors:
        # Preferred: the icons that share the label's own container.  SWT groups
        # a caption with the icons it names, so the parent is a far tighter and
        # more meaningful scope than a distance in pixels -- the items icons sit
        # less than 100px below the Addresses label and would otherwise be
        # mistaken for address icons.
        scoped: List[Any] = []
        try:
            parent = anchor.parent()
        except Exception:  # noqa: BLE001
            parent = None
        if parent is not None:
            scoped = icons_under(parent)
        if scoped:
            chosen.extend(scoped)
            continue

        # Fallback: nearest by distance, for layouts that do not group this way.
        arect = rect_of(anchor)
        for button in icons_under(root):
            brect = rect_of(button)
            dx = max(0, max(arect.left - brect.right, brect.left - arect.right))
            dy = max(0, max(arect.top - brect.bottom, brect.top - arect.bottom))
            if dx <= max_distance and dy <= max_distance:
                chosen.append(button)

    unique: List[Any] = []
    seen = set()
    for button in chosen:
        rect = rect_of(button)
        key = (rect.left, rect.top, rect.right, rect.bottom)
        if key in seen:
            continue
        seen.add(key)
        unique.append(button)

    unique.sort(key=lambda b: (rect_of(b).top, rect_of(b).left))
    log.debug("found %d icon(s) near %s", len(unique), list(anchor_labels))
    return unique


def infer_date_format(sample: str) -> Optional[str]:
    """Work out how this build renders dates, from a value already on screen.

    Date widgets arrive pre-filled with today's date.  Reading that and
    matching it against known patterns is more reliable than assuming a
    locale, and it means the same code works on a German or US install.
    """
    text = (sample or "").strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            _dt.datetime.strptime(text, fmt)
            return fmt
        except ValueError:
            continue
    return None


def set_date_field(control, value: _dt.date, *, description: str) -> str:
    """Write a date and confirm it by *meaning*, not by matching text.

    A date control reformats whatever it is given -- type '07/14/2026' and it
    will show 'Jul. 14, 2026'.  So the usual write-then-compare-the-text rule
    cannot apply here; insisting on it would reject the one input format that
    actually works.  Instead each candidate is typed and the readback is parsed
    back into a date, which is the only comparison that means anything.
    """
    current = read_value(control)
    inferred = infer_date_format(current)
    candidates: List[str] = []
    if inferred and inferred in INPUT_FORMATS:
        candidates.append(inferred)
    candidates += [f for f in INPUT_FORMATS if f not in candidates]

    attempts: List[str] = []
    for candidate in candidates:
        text = value.strftime(candidate)
        try:
            type_into(control, text, description=f"{description} [{candidate}]")
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"{candidate}: could not type ({exc.__class__.__name__})")
            continue

        seen = read_value(control)
        if _parse_display(seen, DISPLAY_FORMATS) == value:
            log.info("%s = %s (verified as %r)", description, value.isoformat(), seen)
            return seen

        # The widget rendered a two-digit year.  That is only trustworthy when
        # the text we typed carried the century itself.
        if "%Y" in candidate and _parse_display(seen, AMBIGUOUS_DISPLAY_FORMATS) == value:
            log.warning(
                "%s = %s, but the control renders a two-digit year (%r); accepted "
                "because the value typed carried an explicit four-digit year",
                description, value.isoformat(), seen,
            )
            return seen

        attempts.append(f"{candidate}: typed {text!r}, read back {seen!r}")

    raise VerificationError(
        f"{description}: could not set date {value.isoformat()}; "
        f"control reads {read_value(control)!r}. Attempts: " + "; ".join(attempts)
    )


def type_into(control, text: str, *, description: str) -> None:
    """Replace a control's contents, then commit the edit.

    No read-back comparison here -- the caller verifies semantically.  The
    trailing Tab matters for segmented widgets, which only commit the edited
    segment when focus leaves it.
    """
    focus(control)
    require_foreground(control, description=description)
    control.type_keys("^a{DEL}", set_foreground=False)
    control.type_keys(escape_keys(text), with_spaces=True, set_foreground=False)
    control.type_keys("{TAB}", set_foreground=False)
    _time.sleep(0.3)


def _parse_display(text: str, formats: Sequence[str]) -> Optional[_dt.date]:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    for fmt in formats:
        try:
            return _dt.datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _same_date(text: str, expected: _dt.date) -> bool:
    return _parse_display(text, DATE_FORMATS) == expected


def grid_items(container):
    """Rows of a list/table as ``(row_control, [cell text, ...])`` pairs.

    The control comes back with the text so a caller can both *decide* on a
    row and then *select* it, without searching the tree twice.
    """
    rows: List[Any] = []
    items: List[Any] = []
    for control_type in ("DataItem", "ListItem", "TreeItem"):
        try:
            found = container.descendants(control_type=control_type)
        except Exception:  # noqa: BLE001
            found = []
        if found:
            items = found
            break

    for item in items:
        cells: List[str] = []
        try:
            children = item.children()
        except Exception:  # noqa: BLE001
            children = []
        for cell in children:
            text = safe_name(cell) or read_value(cell)
            cells.append(text or "")
        if not cells:
            name = safe_name(item)
            cells = [name] if name else []
        if any(c.strip() for c in cells):
            rows.append((item, cells))
    return rows


def grid_rows(container) -> List[List[str]]:
    """Just the cell text of each row -- used for stability comparisons."""
    return [cells for _control, cells in grid_items(container)]


def grid_items_or_screen(container, *, engine_name: str = "windows",
                         refine: bool = False):
    """Rows via UIA, falling back to reading them off the screen.

    Fakturama's lists are Nebula NatTables, which publish no rows to UIA at
    all.  An empty result from the accessibility tree therefore does not mean
    "the list is empty" -- it usually means "this list cannot be seen that
    way", and treating the two as the same is how a search for an existing
    record silently concludes nothing matched and creates a duplicate.
    """
    rows = grid_items(container)
    if rows:
        return rows

    from ..uia.screen_grid import read_screen_grid

    target = _grid_pane(container)
    if target is None:
        return []
    screen_rows = read_screen_grid(target, engine_name=engine_name,
                                   refine=refine)
    if screen_rows:
        log.info("UIA exposed no rows; read %d from the screen instead",
                 len(screen_rows))
    # match_cells() carries every reading of each cell, not just the one that
    # reads best; row_contains_all asks whether a required value is any of
    # them, which is what makes a rule-merged cell recoverable.
    return [(row, row.match_cells()) for row in screen_rows]


# A painted canvas still owns its scrollbars, so these do not count as content.
_CHROME_TYPES = {"ScrollBar", "Thumb", "Button"}


def _grid_pane(container):
    """The largest pane with no real content -- i.e. the painted canvas.

    Scrollbars are ignored when deciding whether a pane is "empty": a
    NatTable publishes its scrollbars to UIA but not one of its rows, so a
    strict childless test rejects exactly the pane being looked for and the
    read falls back to the whole dialog, dragging in the search box and the
    buttons as if they were data.
    """
    best, best_area = None, 0
    try:
        panes = container.descendants(control_type="Pane")
    except Exception:  # noqa: BLE001
        return container
    for pane in panes:
        try:
            content = [
                c for c in pane.children()
                if (c.element_info.control_type or "") not in _CHROME_TYPES
            ]
            if content:
                continue
            rect = rect_of(pane)
        except Exception:  # noqa: BLE001
            continue
        area = rect.width * rect.height
        if area > best_area:
            best, best_area = pane, area
    return best if best_area > 20_000 else container


# Markers a table draws when a value is too wide for its column.  Which glyph
# arrives depends on the renderer *and* on OCR: the same ellipsis Fakturama
# paints has been recognised here as U+2022 BULLET and as U+FFFD, never as
# U+2026.  Matching a fixed glyph is therefore not good enough.
_ELLIPSES = ("…", "...", "�", "»", "•", "·", "∙")
_MAX_MARKER_TOKEN = 3


def _truncation_marker(cell: str) -> str:
    """The trailing marker a cell ends with, or '' if it is complete."""
    stripped = (cell or "").rstrip()
    if not stripped:
        return ""
    for marker in _ELLIPSES:
        if stripped.endswith(marker):
            return marker
    # A short trailing token with no letters or digits in it is a rendering
    # artefact rather than data -- whatever glyph the OCR decided to call it.
    tail = stripped.split()[-1]
    if (
        len(tail) <= _MAX_MARKER_TOKEN
        and len(stripped.split()) > 1
        and not any(ch.isalnum() for ch in tail)
    ):
        return tail
    return ""


def _is_truncated(cell: str) -> bool:
    return bool(_truncation_marker(cell))


def _without_marker(cell: str) -> str:
    stripped = (cell or "").rstrip()
    marker = _truncation_marker(cell)
    if marker:
        return stripped[: -len(marker)].rstrip()
    return stripped


def is_screen_row(control) -> bool:
    """True when this row came from reading the screen rather than from UIA."""
    from ..uia.screen_grid import ScreenRow

    return isinstance(control, ScreenRow)


def cell_matches(cell: str, expected: str, *, ocr_tolerant: bool = False) -> bool:
    """Exact comparison, with OCR confusions allowed for screen-read text."""
    if normalize(cell) == normalize(expected):
        return True
    if ocr_tolerant and ocr_equivalent(normalize(cell), normalize(expected)):
        return True
    return False


def row_contains_all(row: Sequence[str], required: Sequence[str],
                     *, ocr_tolerant: bool = False) -> bool:
    """True when every required value appears in the row, compared exactly.

    The brief demands an *exact* match on the visible fields before a record
    may be reused, so comparison is on normalized whole cell values -- not a
    substring test that would let 'Berlin' match 'Berlin-Mitte'.

    One concession, and only one: a cell the table has *visibly truncated*
    ("Northstar Office ...") is matched as a prefix.  The full text is not on
    screen to compare against, so demanding equality there would mean no long
    value could ever be matched -- the record would be judged missing and a
    duplicate created every single run.  The prefix must still be a real
    prefix, and every other required field must still match exactly.
    """
    truncated = [normalize(_without_marker(c)) for c in row if _is_truncated(c)]
    for value in required:
        target = normalize(value)
        if not target:
            continue
        if any(cell_matches(c, value, ocr_tolerant=ocr_tolerant) for c in row):
            continue
        if any(stem and target.startswith(stem) for stem in truncated):
            continue
        if ocr_tolerant and any(
            stem and len(stem) <= len(target)
            and ocr_equivalent(target[: len(stem)], stem)
            for stem in truncated
        ):
            continue
        return False
    return True
