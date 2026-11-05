"""Finding Fakturama's controls without pixel coordinates.

Fakturama is an Eclipse RCP / SWT application.  SWT wraps real native Win32
controls rather than painting its own, so most of the UI is visible to
Microsoft UI Automation and can be both *driven* and *read back*.

Controls are resolved with a ladder, cheapest and most reliable first:

1. **UIA properties** -- control type plus name or automation id.
2. **Label anchoring** -- SWT text fields frequently expose no accessible
   name of their own, but the visible label beside them is its own element in
   the tree.  "The Edit nearest to the right of the Text that reads
   ``Cust.Ref.``" is stable across window sizes, DPI and layout reflow.
3. **Keyboard** -- focus plus Tab/accelerators where the tree is too thin to
   address a control directly, always followed by reading the value back.
4. **On-screen text** -- a last resort that OCRs the window and clicks the
   words it finds.  Still anchored on text, never on a fixed coordinate.

Nothing in this module returns a point; it returns controls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from ..errors import ControlNotFoundError
from ..logging_setup import get_logger
from .waits import wait_until

log = get_logger("locate")

TEXT_TYPES = ("Text", "Static")
INPUT_TYPES = ("Edit", "ComboBox", "Document", "Spinner")


def normalize(text: Optional[str]) -> str:
    """Comparable form of a UI string.

    Strips the ``&`` accelerator markers Windows uses, trailing colons and
    stars (Eclipse marks dirty editors with ``*``), collapses whitespace and
    casefolds.
    """
    if not text:
        return ""
    cleaned = text.replace("&", "").replace("​", "")
    cleaned = cleaned.strip().strip("*").strip()
    cleaned = cleaned.rstrip(":").strip()
    return re.sub(r"\s+", " ", cleaned).casefold()


def matches(actual: Optional[str], expected: str, exact: bool = True) -> bool:
    a, b = normalize(actual), normalize(expected)
    if not b:
        return False
    return a == b if exact else b in a


@dataclass
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    def vertical_overlap(self, other: "Rect") -> float:
        top, bottom = max(self.top, other.top), min(self.bottom, other.bottom)
        if bottom <= top:
            return 0.0
        return (bottom - top) / max(1, min(self.height, other.height))

    def horizontal_overlap(self, other: "Rect") -> float:
        left, right = max(self.left, other.left), min(self.right, other.right)
        if right <= left:
            return 0.0
        return (right - left) / max(1, min(self.width, other.width))


def rect_of(control) -> Rect:
    r = control.rectangle()
    return Rect(r.left, r.top, r.right, r.bottom)


def safe_name(control) -> str:
    for getter in ("window_text", "element_info"):
        try:
            if getter == "window_text":
                value = control.window_text()
            else:
                value = control.element_info.name
            if value:
                return value
        except Exception:  # noqa: BLE001
            continue
    return ""


def control_type_of(control) -> str:
    try:
        return control.element_info.control_type or ""
    except Exception:  # noqa: BLE001
        return ""


def automation_id_of(control) -> str:
    try:
        return control.element_info.automation_id or ""
    except Exception:  # noqa: BLE001
        return ""


def is_usable(control) -> bool:
    """Visible, non-zero-sized and not explicitly offscreen."""
    try:
        if not control.is_visible():
            return False
        r = rect_of(control)
        return r.width > 0 and r.height > 0
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------- the ladder


class ControlFinder:
    """Resolves controls inside one window."""

    def __init__(self, root, timeout: float = 20.0, interval: float = 0.25):
        self.root = root
        self.timeout = timeout
        self.interval = interval

    # -- rung 1: UIA properties ------------------------------------------

    def descendants(
        self,
        control_type: Optional[str] = None,
        name: Optional[str] = None,
        automation_id: Optional[str] = None,
        exact: bool = True,
        root=None,
    ) -> List[Any]:
        parent = root if root is not None else self.root
        try:
            found = parent.descendants(control_type=control_type) if control_type \
                else parent.descendants()
        except Exception as exc:  # noqa: BLE001
            log.debug("descendants() failed: %s", exc)
            return []

        results = []
        for control in found:
            if not is_usable(control):
                continue
            if name is not None and not matches(safe_name(control), name, exact):
                continue
            if automation_id is not None and \
                    normalize(automation_id_of(control)) != normalize(automation_id):
                continue
            results.append(control)
        return results

    def find(
        self,
        *,
        control_type: Optional[str] = None,
        name: Optional[str] = None,
        automation_id: Optional[str] = None,
        exact: bool = True,
        timeout: Optional[float] = None,
        description: Optional[str] = None,
        root=None,
    ):
        """Rung 1. Wait for exactly one matching control and return it."""
        label = description or (
            f"{control_type or 'control'} named {name!r}" if name
            else f"{control_type or 'control'} id={automation_id!r}"
        )

        def attempt():
            hits = self.descendants(
                control_type=control_type, name=name,
                automation_id=automation_id, exact=exact, root=root,
            )
            return hits[0] if hits else None

        return wait_until(
            attempt,
            timeout=self.timeout if timeout is None else timeout,
            interval=self.interval,
            description=label,
        )

    def find_all(self, **kwargs) -> List[Any]:
        return self.descendants(**kwargs)

    def exists(self, *, timeout: float = 1.0, **kwargs) -> bool:
        try:
            self.find(timeout=timeout, **kwargs)
            return True
        except Exception:  # noqa: BLE001
            return False

    # -- rung 2: label anchoring -----------------------------------------

    def find_labels(self, label: str, exact: bool = True, root=None) -> List[Any]:
        """Every static-text element whose caption reads ``label``."""
        found: List[Any] = []
        for control_type in TEXT_TYPES:
            found.extend(
                self.descendants(
                    control_type=control_type, name=label, exact=exact, root=root
                )
            )
        return found

    def by_label(
        self,
        label: str,
        *,
        control_types: Sequence[str] = INPUT_TYPES,
        direction: str = "right",
        exact: bool = True,
        max_distance: int = 600,
        timeout: Optional[float] = None,
        root=None,
        occurrence: int = 0,
    ):
        """Rung 2. The input control that belongs to a visible label.

        ``direction`` is ``"right"`` for the usual ``Label: [field]`` pairing
        or ``"below"`` for stacked layouts.
        """
        def attempt():
            labels = self.find_labels(label, exact=exact, root=root)
            if not labels:
                return None
            candidates: List[Any] = []
            for control_type in control_types:
                candidates.extend(
                    self.descendants(control_type=control_type, root=root)
                )
            if not candidates:
                return None

            scored: List[Tuple[float, Any]] = []
            for label_control in labels:
                lrect = rect_of(label_control)
                for candidate in candidates:
                    crect = rect_of(candidate)
                    distance = _directional_distance(lrect, crect, direction)
                    if distance is None or distance > max_distance:
                        continue
                    scored.append((distance, candidate))

            if not scored:
                return None
            scored.sort(key=lambda pair: pair[0])
            if occurrence >= len(scored):
                return None
            return scored[occurrence][1]

        return wait_until(
            attempt,
            timeout=self.timeout if timeout is None else timeout,
            interval=self.interval,
            description=f"input {direction} of label {label!r}",
        )

    # -- convenience ------------------------------------------------------

    def toolbar_button(self, name: str, *, timeout: Optional[float] = None, root=None):
        """A toolbar/menu button by its accessible name."""
        for control_type in ("Button", "MenuItem", "SplitButton", "ListItem"):
            try:
                return self.find(
                    control_type=control_type, name=name,
                    timeout=timeout if timeout is not None else 3.0, root=root,
                    description=f"{control_type} {name!r}",
                )
            except Exception:  # noqa: BLE001
                continue
        raise ControlNotFoundError(f"no toolbar control named {name!r}")

    def dump(self, root=None, max_depth: int = 12) -> str:
        """Readable dump of the UIA tree -- the tool for adapting to a new build."""
        lines: List[str] = []

        def walk(control, depth: int):
            if depth > max_depth:
                return
            try:
                rect = rect_of(control)
                lines.append(
                    f"{'  ' * depth}{control_type_of(control) or '?':<14} "
                    f"name={safe_name(control)!r:<42} "
                    f"id={automation_id_of(control)!r:<22} "
                    f"[{rect.left},{rect.top},{rect.right},{rect.bottom}]"
                )
            except Exception:  # noqa: BLE001
                return
            try:
                children = control.children()
            except Exception:  # noqa: BLE001
                children = []
            for child in children:
                walk(child, depth + 1)

        walk(root if root is not None else self.root, 0)
        return "\n".join(lines)


def _directional_distance(label: Rect, candidate: Rect, direction: str) -> Optional[float]:
    """How far a candidate input sits from its label, or None if misaligned."""
    if direction == "right":
        if candidate.left < label.right - 8:
            return None
        if label.vertical_overlap(candidate) < 0.35:
            return None
        return (candidate.left - label.right) + abs(candidate.center_y - label.center_y) * 0.2
    if direction == "below":
        if candidate.top < label.bottom - 8:
            return None
        if label.horizontal_overlap(candidate) < 0.25:
            return None
        return (candidate.top - label.bottom) + abs(candidate.left - label.left) * 0.2
    if direction == "left":
        if candidate.right > label.left + 8:
            return None
        if label.vertical_overlap(candidate) < 0.35:
            return None
        return label.left - candidate.right
    raise ValueError(f"unknown direction {direction!r}")
