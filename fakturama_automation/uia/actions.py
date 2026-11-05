"""Interactions that verify themselves.

Every write is followed by a read of the same control.  Typing into a desktop
application fails silently in a dozen ordinary ways -- the field was not
focused, a dialog stole focus, the widget reformatted the value, the editor
was read-only -- and an automation that assumes success will cheerfully build
an invoice out of values that were never entered.

So: write, read back, compare, and raise if they disagree.
"""

from __future__ import annotations

import ctypes
import re
import time
from ctypes import wintypes
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
    """Give a control keyboard focus, confirming it actually took.

    set_focus() returning without raising does not mean the control is
    focused -- several of Fakturama's smaller SWT fields accept the call and
    quietly ignore it, after which every keystroke goes somewhere else.  So
    the result is checked, and a real mouse click is used as the fallback.
    """
    for attempt in (lambda: control.set_focus(), lambda: control.click_input()):
        try:
            attempt()
        except Exception:  # noqa: BLE001
            continue
        time.sleep(0.1)
        if _has_focus(control) is not False:
            return
    # Last resort: report whatever state we ended in and let the caller's
    # read-back decide, rather than refusing to proceed at all.
    if _has_focus(control) is False:
        log.debug("control %r still does not report keyboard focus",
                  safe_name(control))
        return
    raise AutomationError(f"could not focus control {safe_name(control)!r}")


# ------------------------------------------------------- foreground safety


def _process_of(control) -> Optional[int]:
    try:
        return control.element_info.process_id
    except Exception:  # noqa: BLE001
        return None


def _foreground_process() -> Optional[int]:
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value or None
    except Exception:  # noqa: BLE001
        return None


def _foreground_title() -> str:
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:  # noqa: BLE001
        return "<unknown>"


def require_foreground(control, *, description: str, retries: int = 3) -> None:
    """Refuse to synthesize keystrokes unless they will reach ``control``.

    Two separate hazards, checked at two different levels of precision:

    * **Another application is in front.**  Synthesized keys go to whatever has
      focus, so without this check a run could type a customer's details into a
      chat window that happened to be covering Fakturama.  That is unrecoverable
      and silent, so it is a hard failure.
    * **The wrong control inside Fakturama has focus** -- typically because the
      application raised a modal dialog.  This is checked by asking the control
      whether it actually holds keyboard focus, which is the only question that
      really matters before typing.  It is *not* fatal: the caller reads the
      value back and retries, so a warning plus another focus attempt is the
      proportionate response.  Treating it as fatal would also break the common
      and perfectly correct case of typing into a modal dialog, whose owning
      top-level window is the shell rather than the dialog itself.
    """
    target = _process_of(control)
    if target is None:
        log.debug("%s: cannot determine the control's process; skipping the "
                  "foreground check", description)
        return

    for attempt in range(1, retries + 1):
        if _foreground_process() == target and _has_focus(control) is not False:
            return
        log.debug("%s: foreground is %r / control focused=%r (attempt %d)",
                  description, _foreground_title(), _has_focus(control), attempt)
        try:
            control.set_focus()
        except Exception:  # noqa: BLE001
            try:
                control.top_level_parent().set_focus()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.3)

    if _foreground_process() != target:
        raise AutomationError(
            f"{description}: refusing to type because {_foreground_title()!r} "
            f"holds the foreground, not Fakturama. Keystrokes would go to the "
            f"wrong application. Bring Fakturama to the front and re-run, and "
            f"leave the machine alone while the flow is running."
        )

    if _has_focus(control) is False:
        log.warning("%s: the control does not report keyboard focus; typing "
                    "anyway and relying on the read-back to catch it",
                    description)


def _has_focus(control) -> Optional[bool]:
    """Whether the control holds keyboard focus, or None if it cannot say."""
    try:
        return bool(control.has_keyboard_focus())
    except Exception:  # noqa: BLE001
        return None


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
        require_foreground(control, description=description)
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


def bring_to_front(control) -> bool:
    """Raise the control's window.  True when it actually holds the foreground."""
    target = _process_of(control)
    for raiser in ("top_level_parent", None):
        try:
            window = control.top_level_parent() if raiser else control
            try:
                if window.is_minimized():
                    window.restore()
            except Exception:  # noqa: BLE001
                pass
            window.set_focus()
            time.sleep(0.25)
        except Exception:  # noqa: BLE001
            continue
        if target is None or _foreground_process() == target:
            return True
    return target is not None and _foreground_process() == target


def click(control, *, description: str, settle: float = 0.1) -> None:
    """Click a control, preferring the accessibility action over the mouse.

    ``invoke()`` is tried first because it needs neither focus nor a visible
    window.  SWT toolbar items, however, raise a COM error for it, so a real
    mouse click is the fallback -- and a mouse click is delivered to whatever
    is on screen at those coordinates.  Raising the window first is therefore
    not cosmetic: without it the click lands on whichever application happens
    to be covering Fakturama, and the automation reports success for an action
    that never reached the application.
    """
    try:
        control.invoke()
        time.sleep(settle)
        log.info("clicked %s (invoke)", description)
        return
    except Exception as exc:  # noqa: BLE001
        log.debug("%s: invoke() unavailable (%s); falling back to a mouse click",
                  description, exc.__class__.__name__)

    if not bring_to_front(control):
        raise AutomationError(
            f"could not click {description}: Fakturama will not come to the "
            f"foreground ({_foreground_title()!r} holds it), and a mouse click "
            f"would be delivered to that window instead."
        )

    # Lambdas, not bound attributes: not every UIA element exposes .click(),
    # and looking the attribute up eagerly would raise outside the try block.
    for attempt in (lambda: control.click_input(), lambda: control.click()):
        try:
            attempt()
            time.sleep(settle)
            log.info("clicked %s (mouse)", description)
            return
        except Exception:  # noqa: BLE001
            continue
    raise AutomationError(f"could not click {description}")


def combo_options(control) -> List[str]:
    """The real option list of a combo box.

    An SWT combo exposes no list items until it is dropped down -- ``texts()``
    just echoes the control's own name -- so the box has to be expanded before
    its contents can be read.  Getting this wrong is not a harmless miss: the
    caller then falls back to *typing* the value, and a combo's incremental
    matching silently selects whatever option happens to share a prefix.
    """
    options: List[str] = []
    expanded = False
    try:
        control.expand()
        expanded = True
        time.sleep(0.4)
    except Exception:  # noqa: BLE001
        pass

    try:
        options = [safe_name(i) for i in control.descendants(control_type="ListItem")]
        options = [o for o in options if o]
    except Exception:  # noqa: BLE001
        options = []

    if not options and expanded:
        # An SWT CCombo draws its list into a top-level shell that publishes
        # no children, so there is nothing to enumerate -- the options exist
        # only as pixels.  This has to happen while the list is still open.
        from .screen_grid import read_popup_rows

        try:
            options = [row.text for row in read_popup_rows(control) if row.text]
        except Exception as exc:  # noqa: BLE001
            log.debug("could not read the dropdown from screen: %s", exc)

    if expanded:
        try:
            control.collapse()
            time.sleep(0.2)
        except Exception:  # noqa: BLE001
            pass

    # There is deliberately no texts() fallback.  On an SWT combo texts()
    # returns the control's own caption and its chrome -- for the Debtor's
    # payment combo, an *empty* list reads back as ['Payment', 'Close'].
    # Inventing options that do not exist is worse than reporting none: the
    # caller would conclude the value is on offer and select something else.
    return options


def select_combo(control, value: str, *, description: str, retries: int = 2) -> str:
    """Choose ``value`` in a combo box and verify the selection took."""
    options: List[str] = []
    for attempt in range(1, retries + 1):
        options = combo_options(control)
        chosen = _best_option(options, value)

        try:
            if chosen is not None:
                _pick_option(control, chosen)
            else:
                focus(control)
                require_foreground(control, description=description)
                control.type_keys(escape_keys(value), with_spaces=True,
                                  set_foreground=False)
        except Exception as exc:  # noqa: BLE001
            log.debug("%s: select attempt %d failed (%s)", description, attempt, exc)

        time.sleep(0.25)
        seen = read_value(control)
        if values_match(seen, value):
            log.info("%s = %r (verified)", description, value)
            return seen

    raise VerificationError(
        f"{description}: could not select {value!r}; "
        f"control reads {read_value(control)!r}, options were {options!r}"
    )


def _pick_option(control, option: str) -> None:
    """Select a named option, by the list item itself where possible."""
    try:
        control.select(option)
        return
    except Exception:  # noqa: BLE001
        pass

    # Fall back to expanding and acting on the item directly.
    try:
        control.expand()
        time.sleep(0.4)
    except Exception:  # noqa: BLE001
        pass
    try:
        for item in control.descendants(control_type="ListItem"):
            if normalize(safe_name(item)) == normalize(option):
                for act in (lambda: item.select(), lambda: item.invoke(),
                            lambda: item.click_input()):
                    try:
                        act()
                        time.sleep(0.2)
                        return
                    except Exception:  # noqa: BLE001
                        continue

        # A custom-painted dropdown has no list items to act on, so the option
        # is chosen the only way the user can: by clicking the line it is
        # drawn on.  The row was located by recognising that text, not by a
        # fixed offset, so this follows the list wherever it is drawn.
        from .screen_grid import read_popup_rows

        for row in read_popup_rows(control):
            if normalize(row.text) == normalize(option):
                row.click_input()
                time.sleep(0.2)
                return
    finally:
        try:
            control.collapse()
        except Exception:  # noqa: BLE001
            pass


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
