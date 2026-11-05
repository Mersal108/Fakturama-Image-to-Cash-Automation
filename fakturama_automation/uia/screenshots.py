"""Screenshot capture for the run's audit trail.

Each state in the flow captures the window after it acts.  The sequence is
both the debugging aid and the "annotated screenshots" deliverable, so the
files are numbered in execution order and named after the step.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

from ..logging_setup import get_logger

log = get_logger("shot")


class ScreenshotRecorder:
    def __init__(self, artifacts_dir: str, enabled: bool = True):
        self.dir = os.path.join(artifacts_dir, "screenshots")
        self.enabled = enabled
        self.counter = 0
        self.captured: List[str] = []
        if enabled:
            os.makedirs(self.dir, exist_ok=True)

    def capture(self, window, name: str, caption: str = "") -> Optional[str]:
        """Save a PNG of ``window``; never let a capture failure break a run."""
        if not self.enabled or window is None:
            return None
        self.counter += 1
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
        path = os.path.join(self.dir, f"{self.counter:02d}-{slug}.png")
        try:
            image = window.capture_as_image()
            image.save(path)
        except Exception as exc:  # noqa: BLE001
            log.debug("screenshot %s failed: %s", name, exc)
            return None

        self.captured.append(path)
        if caption:
            with open(
                os.path.join(self.dir, "captions.txt"), "a", encoding="utf-8"
            ) as handle:
                handle.write(f"{os.path.basename(path)}\t{caption}\n")
        log.debug("screenshot saved: %s", path)
        return path
