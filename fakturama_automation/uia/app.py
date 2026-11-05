"""Launching, attaching to, and addressing the Fakturama application."""

from __future__ import annotations

import os
import time
from typing import List, Optional, Sequence, Tuple

from ..config import Settings
from ..errors import AutomationError, ControlNotFoundError
from ..logging_setup import get_logger
from .actions import click
from .locators import ControlFinder, safe_name
from .screenshots import ScreenshotRecorder
from .selectors import MAIN_WINDOW_TITLE_RE
from .waits import wait_until

log = get_logger("app")

# Validation notices Fakturama raises while a document is deliberately still
# incomplete.  The brief's flow requires the Order to stay open while its
# Debtor, payment method, VAT rates and Products are created, so Fakturama
# re-validates the half-finished Order every time focus leaves it and puts up a
# modal notice.  Clearing these is not "ignoring errors": the condition they
# report is one the flow has deliberately created and will resolve, and every
# dismissal is logged and recorded in the run summary.
BENIGN_NOTICES: Tuple[str, ...] = (
    "is invalid, please check it",
)


def _fakturama_windows() -> List[str]:
    """Titles of visible Fakturama shell windows.

    Deliberately a *window* check rather than a process check: a failed launch
    can leave a windowless Fakturama process behind, and that zombie holds no
    workspace lock, so it must not stop a legitimate launch.  Only a real
    window means a real instance is in the way.
    """
    from pywinauto import Desktop

    titles: List[str] = []
    try:
        for window in Desktop(backend="uia").windows():
            try:
                title = window.window_text() or ""
            except Exception:  # noqa: BLE001
                continue
            if title.startswith("Fakturama - "):
                titles.append(title)
    except Exception:  # noqa: BLE001
        return []
    return titles


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
        """Attach to a running Fakturama, or launch one.

        The attach is retried before giving up.  Resolving the shell means
        querying a large UIA tree, which can intermittently take longer than a
        single short timeout allows; treating one slow query as "nothing is
        running" used to start a *second* Fakturama against a workspace the
        first one already holds a lock on.
        """
        attached = False
        for attempt in range(1, 4):
            if self._attach(quiet=True):
                attached = True
                break
            if attempt < 3:
                log.debug("attach attempt %d found no running instance; retrying",
                          attempt)
                time.sleep(1.0)

        if attached:
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

        # Fakturama locks its workspace, so a second instance can only fail --
        # and it fails *confusingly*, leaving a stuck process behind.  If one is
        # already running, the attach above was wrong rather than the app being
        # absent, and saying so is far more useful than launching a doomed copy.
        blocking = _fakturama_windows()
        if blocking:
            raise AutomationError(
                f"Fakturama already has a window open ({blocking[0]!r}) but could "
                f"not be attached to -- it is most likely behind a modal dialog. "
                f"Bring Fakturama to the front, dismiss the dialog and re-run. "
                f"Refusing to launch a second instance, which the workspace lock "
                f"would block anyway."
            )

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
        """The top-level Fakturama shell, ignoring the splash screen.

        The shell is identified by *structure*, not size: it is a real Window
        element carrying the application menu bar, whereas Eclipse's splash is
        a bare Pane holding a progress bar.  An earlier version of this ranked
        candidates by area, which quietly broke when the shell was minimized --
        a minimized window reports a zero-sized rectangle, so the real shell
        looked like a splash screen, the attach failed, and a *second* copy of
        Fakturama was launched against a workspace that was already locked.
        """
        if self._app is None:
            return None
        try:
            windows = self._app.windows(title_re=MAIN_WINDOW_TITLE_RE, top_level_only=True)
        except Exception:  # noqa: BLE001
            return None

        best = None
        best_score = (-1, 0)
        for window in windows:
            try:
                is_window = (window.element_info.control_type or "") == "Window"
                # children(), never descendants(): the menu bar is a direct
                # child of the shell, and walking the whole subtree here is
                # slow enough to blow the attach timeout -- which silently
                # launches a second Fakturama against a locked workspace.
                has_menu = any(
                    (child.element_info.control_type or "") == "MenuBar"
                    for child in window.children()
                ) if is_window else False
                rect = window.rectangle()
                area = max(0, rect.width()) * max(0, rect.height())
            except Exception:  # noqa: BLE001
                continue
            # A Window with a menu bar is the shell even when it is minimized.
            score = (2 if (is_window and has_menu) else 1 if is_window else 0, area)
            if score > best_score:
                best, best_score = window, score

        if best is None or best_score[0] == 0:
            return None
        return best

    def restore(self) -> None:
        """Un-minimize the shell so it can be driven and screenshotted."""
        try:
            if self.window.is_minimized():
                log.info("the Fakturama window was minimized; restoring it")
                self.window.restore()
        except Exception as exc:  # noqa: BLE001
            log.debug("could not restore the window: %s", exc)

    def maximize(self) -> None:
        self.restore()
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


    # ----------------------------------------------------- modal notices

    def notices(self) -> List[Tuple[object, str]]:
        """Modal message dialogs currently covering the application."""
        found: List[Tuple[object, str]] = []
        try:
            dialogs = self.finder.descendants(control_type="Window")
        except Exception:  # noqa: BLE001
            return found
        for dialog in dialogs:
            try:
                texts = [
                    safe_name(t) for t in dialog.descendants(control_type="Text")
                ]
            except Exception:  # noqa: BLE001
                continue
            message = " ".join(t for t in texts if t and t.strip())
            if not message.strip():
                continue
            try:
                buttons = [
                    safe_name(b) for b in dialog.descendants(control_type="Button")
                ]
            except Exception:  # noqa: BLE001
                buttons = []
            if "OK" in buttons:
                found.append((dialog, message.strip()))
        return found

    def dismiss_notices(self, patterns: Sequence[str] = BENIGN_NOTICES) -> List[str]:
        """Close modal notices whose text matches a known-benign pattern.

        Anything that does *not* match is left alone, so a genuine problem
        still blocks the run and gets reported rather than clicked away.
        """
        dismissed: List[str] = []
        for dialog, message in self.notices():
            if not any(p.casefold() in message.casefold() for p in patterns):
                continue
            try:
                for button in dialog.descendants(control_type="Button"):
                    if safe_name(button) == "OK":
                        click(button, description=f"notice OK ({message[:60]!r})")
                        break
            except Exception as exc:  # noqa: BLE001
                log.debug("could not dismiss notice %r: %s", message, exc)
                continue
            log.info("dismissed Fakturama notice: %s", message)
            dismissed.append(message)
        return dismissed

    def describe_blocking_notices(self) -> str:
        messages = [m for _d, m in self.notices()]
        if not messages:
            return ""
        return "; ".join(messages)

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
        # A control can be unfindable simply because a modal notice is covering
        # the window.  Clear the benign ones and try once more before failing,
        # and if something non-benign is up, say so in the error -- "control not
        # found" is a maddening way to report "a dialog is in the way".
        if self.dismiss_notices():
            for name in names:
                try:
                    return self.finder.find(
                        control_type=control_type, name=name, exact=exact,
                        timeout=per_name, root=root,
                        description=f"{control_type or 'control'} {name!r}",
                    )
                except Exception:  # noqa: BLE001
                    continue

        blocking = self.describe_blocking_notices()
        suffix = f"; a dialog is blocking the application: {blocking}" if blocking else ""
        raise ControlNotFoundError(
            f"no control matched any of {list(names)} ({'; '.join(errors)}){suffix}"
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
        if self.dismiss_notices():
            for label in labels:
                try:
                    return self.finder.by_label(
                        label, control_types=control_types, direction=direction,
                        timeout=per_label, root=root, occurrence=occurrence,
                    )
                except Exception:  # noqa: BLE001
                    continue

        blocking = self.describe_blocking_notices()
        suffix = f"; a dialog is blocking the application: {blocking}" if blocking else ""
        raise ControlNotFoundError(
            f"no input found beside any label in {list(labels)} "
            f"({'; '.join(errors)}){suffix}"
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
