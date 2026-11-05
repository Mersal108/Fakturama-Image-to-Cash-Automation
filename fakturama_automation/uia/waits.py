"""Condition-based waiting.

There is not a single ``sleep(2)`` in this automation.  Eclipse/SWT applications
repaint lazily and a fixed pause is simultaneously too short on a loaded machine
and wasted time on a fast one.  Every step instead waits for a *condition* --
a window exists, a control became enabled, a result list stopped changing --
and fails loudly when the condition never arrives.
"""

from __future__ import annotations

import time
from typing import Callable, Optional, TypeVar

from ..errors import AutomationError
from ..logging_setup import get_logger

T = TypeVar("T")
log = get_logger("wait")


def wait_until(
    predicate: Callable[[], Optional[T]],
    *,
    timeout: float = 30.0,
    interval: float = 0.25,
    description: str = "condition",
) -> T:
    """Poll ``predicate`` until it returns something truthy.

    Exceptions raised by the predicate are treated as "not ready yet", because
    a control that does not exist yet raises rather than returning None.
    """
    deadline = time.monotonic() + timeout
    last_error: Optional[BaseException] = None
    attempts = 0

    while time.monotonic() < deadline:
        attempts += 1
        try:
            value = predicate()
            if value:
                return value
        except Exception as exc:  # noqa: BLE001 - "not ready" looks like an error
            last_error = exc
        time.sleep(interval)

    detail = f" (last error: {last_error})" if last_error else ""
    raise AutomationError(
        f"timed out after {timeout:.0f}s waiting for {description}; "
        f"{attempts} attempts{detail}"
    )


def wait_for_stable(
    snapshot: Callable[[], object],
    *,
    timeout: float = 15.0,
    interval: float = 0.2,
    stable_for: float = 0.6,
    description: str = "content",
):
    """Wait until repeated reads of ``snapshot`` stop changing.

    This is how the flow knows a search-results list has "settled" before it
    decides whether a match is unique -- reading too early can see a partially
    filtered list and draw the wrong conclusion.
    """
    deadline = time.monotonic() + timeout
    previous = object()
    steady_since: Optional[float] = None

    while time.monotonic() < deadline:
        try:
            current = snapshot()
        except Exception:  # noqa: BLE001
            current = object()
            steady_since = None

        if current == previous:
            if steady_since is None:
                steady_since = time.monotonic()
            elif time.monotonic() - steady_since >= stable_for:
                return current
        else:
            steady_since = None
            previous = current
        time.sleep(interval)

    raise AutomationError(
        f"timed out after {timeout:.0f}s waiting for {description} to stop changing"
    )


def wait_gone(
    predicate: Callable[[], bool],
    *,
    timeout: float = 15.0,
    interval: float = 0.25,
    description: str = "element",
) -> None:
    """Wait until something stops being present (a dialog closing, say)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not predicate():
                return
        except Exception:  # noqa: BLE001 - gone often means "raises"
            return
        time.sleep(interval)
    raise AutomationError(f"timed out after {timeout:.0f}s waiting for {description} to disappear")
