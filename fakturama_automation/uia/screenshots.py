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
            self._clear_previous_run()

    # Numbered in execution order, the sequence *is* the deliverable, so a
    # previous run's files have to go: left in place they interleave with this
    # run's by number and a reader is shown two contradictory histories.  Only
    # this class's own output is removed -- files matching the 'NN-slug.png'
    # it writes, plus its captions file -- so nothing else in the directory
    # can be caught by it.
    _OWN_FILE = re.compile(r"^\d{2,}-[a-z0-9-]+\.png$")

    def _clear_previous_run(self) -> None:
        try:
            names = os.listdir(self.dir)
        except OSError:
            return
        for name in names:
            if name != "captions.txt" and not self._OWN_FILE.match(name):
                continue
            try:
                os.remove(os.path.join(self.dir, name))
            except OSError as exc:
                log.debug("could not clear %s: %s", name, exc)

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
