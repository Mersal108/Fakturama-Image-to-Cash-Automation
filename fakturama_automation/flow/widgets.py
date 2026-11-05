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
from typing import Any, List, Optional, Sequence

from ..errors import ControlNotFoundError, VerificationError
from ..logging_setup import get_logger
from ..uia.actions import read_value, set_text
from ..uia.locators import normalize, rect_of, safe_name

log = get_logger("widget")

# Date formats Fakturama might be using, depending on locale.
DATE_FORMATS = (
    "%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%d/%m/%Y",
    "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d.%m.%y", "%m/%d/%y",
)


def icons_near(
    app,
    anchor_labels: Sequence[str],
    *,
    control_type: str = "Button",
    max_distance: int = 220,
    root=None,
) -> List[Any]:
    """Buttons positioned next to a named label, ordered top-to-bottom.

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

    buttons = app.finder.descendants(control_type=control_type, root=root)
    chosen: List[Any] = []
    for anchor in anchors:
        arect = rect_of(anchor)
        for button in buttons:
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
    """Write a date, matching whatever format the control is already using."""
    current = read_value(control)
    fmt = infer_date_format(current)
    candidates = [fmt] if fmt else []
    candidates += [f for f in DATE_FORMATS if f != fmt]

    last_error: Optional[Exception] = None
    for candidate in candidates:
        text = value.strftime(candidate)
        try:
            set_text(control, text, description=f"{description} [{candidate}]")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
        seen = read_value(control)
        if _same_date(seen, value):
            log.info("%s = %s (verified as %r)", description, value.isoformat(), seen)
            return seen

    raise VerificationError(
        f"{description}: could not set date {value.isoformat()}; "
        f"control reads {read_value(control)!r} (last error: {last_error})"
    )


def _same_date(text: str, expected: _dt.date) -> bool:
    for fmt in DATE_FORMATS:
        try:
            if _dt.datetime.strptime((text or "").strip(), fmt).date() == expected:
                return True
        except ValueError:
            continue
    return False


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


def row_contains_all(row: Sequence[str], required: Sequence[str]) -> bool:
    """True when every required value appears in the row, compared exactly.

    The brief demands an *exact* match on the visible fields before a record
    may be reused, so comparison is on normalized whole cell values -- not a
    substring test that would let 'Berlin' match 'Berlin-Mitte'.
    """
    cells = [normalize(c) for c in row]
    for value in required:
        target = normalize(value)
        if not target:
            continue
        if not any(cell == target for cell in cells):
            return False
    return True
