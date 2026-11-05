"""Interactions that verify themselves.

Every write is followed by a read of the same control.  Typing into a desktop
application fails silently in a dozen ordinary ways -- the field was not
focused, a dialog stole focus, the widget reformatted the value, the editor
was read-only -- and an automation that assumes success will cheerfully build
an invoice out of values that were never entered.

So: write, read back, compare, and raise if they disagree.
"""

from __future__ import annotations

import re
import time
from typing import List, Optional, Sequence

from ..errors import AutomationError, VerificationError
from ..logging_setup import get_logger
from .locators import normalize, safe_name

log = get_logger("act")

# Characters send_keys() treats as syntax rather than literal text.
_SPECIAL = set("^+%~(){}[]")


def escape_keys(text: str) -> str:
    return "".join("{" + ch + "}" if ch in _SPECIAL else ch for ch in text)


# ------------------------------------------------------------------- reading


def read_value(control) -> str:
    """Best-effort current value of an input control.

    Tries the UIA Value pattern first (authoritative for Edit/ComboBox), then
    falls back to the element's name/legacy value.
    """
    for reader in (
        lambda: control.get_value(),
        lambda: control.iface_value.CurrentValue,
        lambda: control.window_text(),
        lambda: control.legacy_properties().get("Value"),
    ):
        try:
            value = reader()
            if value is not None and str(value) != "":
                return str(value)
        except Exception:  # noqa: BLE001
            continue
    # An empty field is a legitimate answer, not a failure.
    try:
        return str(control.get_value() or "")
    except Exception:  # noqa: BLE001
        return ""


def values_match(actual: str, expected: str, *, numeric: bool = False) -> bool:
    if numeric:
        return _numeric_key(actual) == _numeric_key(expected)
    return normalize(actual) == normalize(expected)


def _numeric_key(text: str) -> str:
    """Compare numbers by value, ignoring currency, spaces and separators."""
    cleaned = re.sub(r"[^\d,.\-]", "", text or "")
    cleaned = cleaned.replace(",", ".")
    # Collapse thousands separators: keep only the final decimal point.
    parts = cleaned.split(".")
    if len(parts) > 2:
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    try:
        from decimal import Decimal

        return str(Decimal(cleaned or "0").normalize())
    except Exception:  # noqa: BLE001
        return cleaned


# ------------------------------------------------------------------- writing


def focus(control) -> None:
    for attempt in (control.set_focus, control.click_input):
        try:
            attempt()
            return
        except Exception:  # noqa: BLE001
            continue
    raise AutomationError(f"could not focus control {safe_name(control)!r}")


def set_text(
    control,
    value: str,
    *,
    description: str,
    numeric: bool = False,
    retries: int = 2,
    settle: float = 0.15,
) -> str:
    """Type ``value`` into ``control`` and confirm it stuck.

    Typing (rather than setting the value property directly) is deliberate:
    SWT fires its modify listeners on real input, and Fakturama relies on
    those to recalculate totals and mark the editor dirty.
    """
    last_seen = ""
    for attempt in range(1, retries + 1):
        focus(control)
        try:
            control.type_keys("^a{DEL}", set_foreground=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            control.type_keys(escape_keys(value), with_spaces=True, set_foreground=False)
        except Exception as exc:  # noqa: BLE001
            raise AutomationError(f"{description}: typing failed ({exc})") from exc

        # Let SWT's modify listeners run, then read the control back.
        time.sleep(settle)
        last_seen = read_value(control)
        if values_match(last_seen, value, numeric=numeric):
            log.info("%s = %r (verified)", description, value)
            return last_seen
        log.debug("%s: attempt %d read back %r, expected %r",
                  description, attempt, last_seen, value)

    raise VerificationError(
        f"{description}: wrote {value!r} but the control reads {last_seen!r}"
    )


def set_text_via_value_pattern(control, value: str, *, description: str) -> str:
    """Fallback write path for controls that refuse synthesized keystrokes."""
    try:
        control.set_edit_text(value)
    except Exception as exc:  # noqa: BLE001
        raise AutomationError(f"{description}: set_edit_text failed ({exc})") from exc
    time.sleep(0.15)
    seen = read_value(control)
    if not values_match(seen, value):
        raise VerificationError(
            f"{description}: wrote {value!r} via value pattern but it reads {seen!r}"
        )
    return seen


def click(control, *, description: str, settle: float = 0.1) -> None:
    for attempt in (
        lambda: control.invoke(),
        lambda: control.click_input(),
        lambda: control.click(),
    ):
        try:
            attempt()
            time.sleep(settle)
            log.info("clicked %s", description)
            return
        except Exception:  # noqa: BLE001
            continue
    raise AutomationError(f"could not click {description}")


def select_combo(control, value: str, *, description: str, retries: int = 2) -> str:
    """Choose ``value`` in a combo box and verify the selection took."""
    options: List[str] = []
    for attempt in range(1, retries + 1):
        try:
            options = [str(t) for t in control.texts() if t]
        except Exception:  # noqa: BLE001
            options = []

        chosen = _best_option(options, value)
        try:
            if chosen is not None:
                control.select(chosen)
            else:
                focus(control)
                control.type_keys(escape_keys(value), with_spaces=True,
                                  set_foreground=False)
        except Exception as exc:  # noqa: BLE001
            log.debug("%s: select attempt %d failed (%s)", description, attempt, exc)

        time.sleep(0.2)
        seen = read_value(control)
        if values_match(seen, value):
            log.info("%s = %r (verified)", description, value)
            return seen

    raise VerificationError(
        f"{description}: could not select {value!r}; "
        f"control reads {read_value(control)!r}, options were {options!r}"
    )


def _best_option(options: Sequence[str], value: str) -> Optional[str]:
    target = normalize(value)
    for option in options:
        if normalize(option) == target:
            return option
    for option in options:
        if target and target in normalize(option):
            return option
    return None


def set_checkbox(control, checked: bool, *, description: str) -> None:
    """Put a checkbox into a known state and confirm it."""
    def state() -> Optional[int]:
        try:
            return control.get_toggle_state()
        except Exception:  # noqa: BLE001
            return None

    want = 1 if checked else 0
    if state() == want:
        log.info("%s already %s", description, "checked" if checked else "unchecked")
        return

    try:
        control.toggle()
    except Exception:  # noqa: BLE001
        click(control, description=description)

    time.sleep(0.15)
    if state() != want:
        raise VerificationError(
            f"{description}: wanted {'checked' if checked else 'unchecked'}, "
            f"state is {state()!r}"
        )
    log.info("%s = %s (verified)", description, "checked" if checked else "unchecked")


def row_texts(control) -> List[str]:
    """Flat text of every row in a list/table control, for stability checks."""
    rows: List[str] = []
    try:
        items = control.descendants(control_type="DataItem") or \
                control.descendants(control_type="ListItem")
    except Exception:  # noqa: BLE001
        items = []
    for item in items:
        cells: List[str] = []
        try:
            for cell in item.children():
                text = safe_name(cell)
                if text:
                    cells.append(text)
        except Exception:  # noqa: BLE001
            pass
        rows.append(" | ".join(cells) if cells else safe_name(item))
    return rows
