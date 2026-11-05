"""Shared state and conventions for the flow steps.

Each step announces itself, does one thing, records the evidence it checked,
and captures a screenshot.  Stopping for manual review goes through
:meth:`RunContext.stop`, so every halt is logged, screenshotted and carries
the reason -- which is what the brief asks for whenever a match is ambiguous.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Dict, List, NoReturn, Optional

from ..config import Settings
from ..errors import ManualReviewRequired
from ..logging_setup import get_logger
from ..models import OrderDocument
from ..uia.app import FakturamaApp

log = get_logger("flow")


class RunContext:
    def __init__(
        self,
        settings: Settings,
        app: FakturamaApp,
        order: OrderDocument,
        extraction_notes: Optional[List[str]] = None,
    ):
        self.settings = settings
        self.app = app
        self.order = order
        self.extraction_notes = list(extraction_notes or [])
        self.evidence: Dict[str, Any] = {}
        self.completed: List[str] = []
        self.created: Dict[str, List[str]] = {
            "debtors": [], "products": [], "vats": [], "payments": [],
        }
        self.current_step = "start"
        # The Order editor's tab caption, remembered when it is opened so the
        # flow can come back to it after each master-data detour (brief 1.8).
        self.order_tab: str = ""
        # The Debtor editor's tab caption, so the flow can return to it after
        # the payment-method detour opens an editor of its own.
        self.debtor_tab: str = ""

    # --------------------------------------------------------------- steps

    @contextmanager
    def step(self, step_id: str, title: str):
        self.current_step = step_id
        log.info("=" * 70)
        log.info("STEP %s  %s", step_id, title)
        try:
            yield self
        except ManualReviewRequired:
            raise
        except Exception:
            self.app.screenshot(f"{step_id}-failed", f"{title} (failed)")
            raise
        else:
            self.completed.append(step_id)
            self.app.screenshot(step_id, title)

    def stop(self, reason: str, **evidence) -> NoReturn:
        """Halt the run and hand control to a person."""
        shot = self.app.screenshot(f"{self.current_step}-manual-review", reason)
        log.error("MANUAL REVIEW at %s: %s", self.current_step, reason)
        raise ManualReviewRequired(self.current_step, reason, evidence, shot)

    # ------------------------------------------------------------ evidence

    def record(self, key: str, value: Any) -> None:
        self.evidence[key] = value
        log.info("  verified %s = %r", key, value)

    def note_created(self, kind: str, identifier: str) -> None:
        self.created.setdefault(kind, []).append(identifier)
        log.info("  created %s: %s", kind[:-1] if kind.endswith("s") else kind,
                 identifier)

    # ------------------------------------------------------------- summary

    def summary(self) -> Dict[str, Any]:
        return {
            "order": self.order.to_dict(),
            "extraction_notes": self.extraction_notes,
            "steps_completed": self.completed,
            "created": self.created,
            "evidence": self.evidence,
            "screenshots": self.app.recorder.captured,
        }

    def write_summary(self, filename: str = "run-summary.json") -> str:
        path = os.path.join(self.settings.artifacts_dir, filename)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.summary(), handle, indent=2, default=str)
        log.info("run summary written to %s", path)
        return path
