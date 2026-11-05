"""Prepare a Fakturama workspace so the flow can run.

A freshly created Fakturama workspace has no default shipping method, and
without one the application refuses to open *any* new document -- it raises
"No default value found for Shippings. Please set one from list!" and the New
Order editor never appears.  Fakturama seeds that default only during its very
first installation run, so a workspace whose database has been recreated needs
it putting back.

This creates a single zero-cost shipping method and marks it standard, which
also matches the brief's requirement that shipping stay free.

    python scripts/prepare_workspace.py [--reset]

``--reset`` moves the existing database aside first (it is renamed, never
deleted) so the run starts from an empty one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fakturama_automation.config import Settings, discover_fakturama_exe  # noqa: E402
from fakturama_automation.logging_setup import get_logger, setup_logging  # noqa: E402
from fakturama_automation.uia import selectors as sel  # noqa: E402
from fakturama_automation.uia.actions import click, read_value, set_text  # noqa: E402
from fakturama_automation.uia.app import FakturamaApp  # noqa: E402
from fakturama_automation.uia.locators import safe_name  # noqa: E402

log = get_logger("prepare")

SHIPPING_NAME = "Free shipping"


def default_workspace() -> str:
    return os.path.join(os.path.expanduser("~"), "Fakturama2")


def reset_database(workspace: str) -> None:
    database = os.path.join(workspace, "Database")
    if not os.path.isdir(database):
        log.info("no database at %s; nothing to reset", database)
        return
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = f"{database}.bak-{stamp}"
    shutil.move(database, target)
    log.info("existing database moved aside to %s (renamed, not deleted)", target)


def running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Fakturama.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:  # noqa: BLE001
        return False
    return "Fakturama.exe" in out


def create_default_shipping(app: FakturamaApp) -> None:
    window = app.window
    window.set_focus()
    time.sleep(0.8)

    existing = app.finder.descendants(control_type="TabItem", name=SHIPPING_NAME)
    if existing:
        log.info("a shipping method named %r already exists", SHIPPING_NAME)
        return

    app.click_any(("Shippings",), description="Data > Shippings", timeout=20)
    time.sleep(2.0)

    app.click_any(sel.BTN_NEW_SHIPPING, description="Create a new shipping method",
                  control_type="Button", timeout=20)
    time.sleep(3.0)

    set_text(app.find_any(("Name",), control_type="Edit", timeout=15),
             SHIPPING_NAME, description="Shipping Name")
    set_text(app.find_any(("Description",), control_type="Edit", timeout=10),
             SHIPPING_NAME, description="Shipping Description")

    gross = app.find_any(("Gross",), control_type="Edit", timeout=10)
    log.info("shipping cost reads %r (the brief requires shipping to be free)",
             read_value(gross))

    click(app.find_any(("Set as standard",), control_type="Button", timeout=10),
          description="Set as standard")
    time.sleep(1.0)

    app.click_any(sel.TOOLBAR_SAVE, description="toolbar Save (Shipping)",
                  control_type="Button", timeout=20)
    time.sleep(2.5)
    tabs = [safe_name(t) for t in window.descendants(control_type="TabItem")]
    if SHIPPING_NAME not in tabs:
        raise SystemExit(f"the shipping method was not saved; tabs are {tabs}")
    log.info("default shipping method %r created and saved", SHIPPING_NAME)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true",
                        help="move the existing database aside first")
    parser.add_argument("--workspace", default=default_workspace())
    args = parser.parse_args()

    setup_logging()

    if args.reset:
        if running():
            print("Close Fakturama before resetting its database, then re-run.")
            return 2
        reset_database(args.workspace)

    exe = discover_fakturama_exe()
    if not exe:
        print("Fakturama.exe not found; set FAKTURAMA_EXE or install it.")
        return 2

    settings = Settings(image_path="", artifacts_dir="artifacts", screenshots=False)
    app = FakturamaApp(settings).start()
    create_default_shipping(app)
    print("Workspace ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
