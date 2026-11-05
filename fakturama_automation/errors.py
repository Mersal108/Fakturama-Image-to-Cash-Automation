"""Error types for the image-to-cash pipeline.

The automation distinguishes three kinds of failure, because they call for
different human responses:

* :class:`ManualReviewRequired` -- the system worked correctly but found a
  situation the brief says a human must resolve (ambiguous debtor match,
  conflicting VAT definition, totals that do not reconcile).  This is an
  expected, designed outcome, not a crash.
* :class:`ExtractionError`     -- the source image could not be read well
  enough to produce a trustworthy record.
* :class:`AutomationError`     -- Fakturama's UI did not behave as expected
  (a control never appeared, a value did not persist).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


class FakturamaAutomationError(Exception):
    """Base class for every error raised by this package."""


class ManualReviewRequired(FakturamaAutomationError):
    """Raised when the flow must stop and hand control back to a person.

    Carries the step it stopped at, a human-readable reason, and whatever
    evidence was on hand, so the operator can see *why* without re-running.
    """

    def __init__(
        self,
        step: str,
        reason: str,
        evidence: Optional[Mapping[str, Any]] = None,
        screenshot: Optional[str] = None,
    ) -> None:
        self.step = step
        self.reason = reason
        self.evidence = dict(evidence or {})
        self.screenshot = screenshot
        detail = "; ".join(f"{k}={v!r}" for k, v in self.evidence.items())
        message = f"[{step}] MANUAL REVIEW: {reason}"
        if detail:
            message += f" ({detail})"
        if screenshot:
            message += f" [screenshot: {screenshot}]"
        super().__init__(message)


class ExtractionError(FakturamaAutomationError):
    """The order image could not be turned into a usable record."""


class AutomationError(FakturamaAutomationError):
    """Fakturama's UI did not respond as the flow expected."""


class ControlNotFoundError(AutomationError):
    """No control matched a locator within the timeout."""


class VerificationError(AutomationError):
    """A value written into the UI did not read back as expected."""
