"""Launching, attaching to, and addressing the Fakturama application."""

from __future__ import annotations

import os
from typing import Optional, Sequence

from ..config import Settings
from ..errors import AutomationError, ControlNotFoundError
from ..logging_setup import get_logger
from .actions import click
from .locators import ControlFinder, safe_name
from .screenshots import ScreenshotRecorder
from .selectors import MAIN_WINDOW_TITLE_RE
from .waits import wait_until

log = get_logger("app")


class FakturamaApp:
    """Owns the connection to Fakturama and resolves controls inside it."""

    def __init__(self, settings: Settings, recorder: Optional[ScreenshotRecorder] = None):
        self.settings = settings
        self.recorder = recorder or ScreenshotRecorder(
            settings.artifacts_dir, settings.screenshots
        )
        self._app = None
        self._window = None
        self._finder: Optional[ControlFinder] = None

    # ----------------------------------------------------------- lifecycle

    def start(self) -> "FakturamaApp":
        """Attach to a running Fakturama, or launch one."""
        if self._attach(quiet=True):
            log.info("attached to a Fakturama instance that was already running")
        else:
            self._launch()
        self._finder = ControlFinder(
            self.window,
            timeout=self.settings.timeout,
            interval=self.settings.poll_interval,
        )
        self.maximize()
        return self

    def _launch(self) -> None:
        exe = self.settings.fakturama_exe
        if not exe or not os.path.isfile(exe):
            raise AutomationError(
                "Fakturama.exe was not found. Install Fakturama (see "
                "scripts/install_fakturama.ps1) or set FAKTURAMA_EXE to its path."
            )
        from pywinauto.application import Application

        log.info("launching %s", exe)
        self._app = Application(backend="uia").start(
            f'"{exe}"', timeout=self.settings.launch_timeout
        )
        # Eclipse shows a splash screen first; wait for the real shell.
        self._window = wait_until(
            self._main_window_candidate,
            timeout=self.settings.launch_timeout,
            interval=0.5,
            description="the Fakturama main window",
        )
        log.info("main window ready: %r", safe_name(self._window))

    def _attach(self, quiet: bool = False) -> bool:
        from pywinauto.application import Application

        try:
            self._app = Application(backend="uia").connect(
                title_re=MAIN_WINDOW_TITLE_RE, timeout=3, found_index=0
            )
            self._window = self._main_window_candidate()
            return self._window is not None
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                log.debug("attach failed: %s", exc)
            return False

    def _main_window_candidate(self):
        """The top-level Fakturama shell, ignoring the splash screen."""
        if self._app is None:
            return None
        try:
            windows = self._app.windows(title_re=MAIN_WINDOW_TITLE_RE, top_level_only=True)
        except Exception:  # noqa: BLE001
            return None
        best = None
        best_area = 0
        for window in windows:
            try:
                if not window.is_visible():
                    continue
                rect = window.rectangle()
                area = rect.width() * rect.height()
                # The splash is small; the shell is the large one.
                if area > best_area:
                    best, best_area = window, area
            except Exception:  # noqa: BLE001
                continue
        return best if best_area > 200_000 else None

    def maximize(self) -> None:
        try:
            self.window.maximize()
            self.window.set_focus()
        except Exception as exc:  # noqa: BLE001
            log.debug("could not maximize: %s", exc)

    def close(self) -> None:
        if self.settings.keep_open:
            log.info("leaving Fakturama open for inspection")
            return
        try:
            self.window.close()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- access

    @property
    def window(self):
        if self._window is None:
            raise AutomationError("Fakturama is not connected; call start() first")
        return self._window

    @property
    def finder(self) -> ControlFinder:
        if self._finder is None:
            raise AutomationError("Fakturama is not connected; call start() first")
        return self._finder

    def screenshot(self, name: str, caption: str = "", window=None) -> Optional[str]:
        return self.recorder.capture(window or self._window, name, caption)

    # --------------------------------------- caption-alternatives helpers

    def find_any(
        self,
        names: Sequence[str],
        *,
        control_type: Optional[str] = None,
        timeout: Optional[float] = None,
        root=None,
        exact: bool = True,
    ):
        """First control matching any of ``names`` (see selectors.py)."""
        per_name = max(1.0, (timeout or self.settings.short_timeout) / max(1, len(names)))
        errors = []
        for name in names:
            try:
                return self.finder.find(
                    control_type=control_type, name=name, exact=exact,
                    timeout=per_name, root=root,
                    description=f"{control_type or 'control'} {name!r}",
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc.__class__.__name__}")
        raise ControlNotFoundError(
            f"no control matched any of {list(names)} ({'; '.join(errors)})"
        )

    def by_label_any(
        self,
        labels: Sequence[str],
        *,
        control_types: Sequence[str] = ("Edit", "ComboBox", "Document"),
        direction: str = "right",
        timeout: Optional[float] = None,
        root=None,
        occurrence: int = 0,
    ):
        per_label = max(1.0, (timeout or self.settings.short_timeout) / max(1, len(labels)))
        errors = []
        for label in labels:
            try:
                return self.finder.by_label(
                    label, control_types=control_types, direction=direction,
                    timeout=per_label, root=root, occurrence=occurrence,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: {exc.__class__.__name__}")
        raise ControlNotFoundError(
            f"no input found beside any label in {list(labels)} ({'; '.join(errors)})"
        )

    def click_any(self, names: Sequence[str], *, description: str = "",
                  control_type: Optional[str] = None, timeout: Optional[float] = None,
                  root=None) -> None:
        control = self.find_any(
            names, control_type=control_type, timeout=timeout, root=root
        )
        click(control, description=description or str(names[0]))

    def dialog(self, titles: Sequence[str], *, timeout: Optional[float] = None):
        """Wait for a modal dialog by title and return its window wrapper."""
        wait = timeout or self.settings.timeout

        def attempt():
            for title in titles:
                try:
                    windows = self._app.windows(title=title, top_level_only=False)
                except Exception:  # noqa: BLE001
                    windows = []
                for window in windows:
                    try:
                        if window.is_visible():
                            return window
                    except Exception:  # noqa: BLE001
                        continue
            # Some SWT dialogs are children of the shell rather than top level.
            for title in titles:
                hits = self.finder.descendants(control_type="Window", name=title)
                if hits:
                    return hits[0]
            return None

        return wait_until(
            attempt, timeout=wait, interval=self.settings.poll_interval,
            description=f"dialog {list(titles)}",
        )

    def dialog_closed(self, titles: Sequence[str]) -> bool:
        for title in titles:
            if self.finder.exists(control_type="Window", name=title, timeout=0.4):
                return False
        return True

    def dump_tree(self, path: Optional[str] = None) -> str:
        """Write the UIA tree to a file -- the tool for adapting selectors."""
        text = self.finder.dump()
        target = path or os.path.join(self.settings.artifacts_dir, "uia-tree.txt")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)
        log.info("UIA tree written to %s (%d lines)", target, text.count("\n") + 1)
        return target
