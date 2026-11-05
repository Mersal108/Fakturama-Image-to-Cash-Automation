"""Command-line entry point.

    python -m fakturama_automation --image samples/order_sample.png
    python -m fakturama_automation --image ... --extract-only
    python -m fakturama_automation --dump-tree
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .config import Settings, discover_fakturama_exe
from .errors import ExtractionError, FakturamaAutomationError, ManualReviewRequired
from .logging_setup import setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fakturama_automation",
        description="Turn an order image into a saved, verified Order and linked "
                    "Invoice in Fakturama.",
    )
    parser.add_argument(
        "--image", "-i", default=os.path.join("samples", "order_sample.png"),
        help="path to the order image (default: samples/order_sample.png)",
    )
    parser.add_argument(
        "--artifacts", "-a", default="artifacts",
        help="where screenshots, logs and the run summary are written",
    )
    parser.add_argument(
        "--engine", "-e", default="windows",
        choices=("windows", "tesseract", "auto"),
        help="OCR engine (default: windows, which needs no installation)",
    )
    parser.add_argument(
        "--no-refine", action="store_true",
        help="skip the magnified per-cell re-read of the items table",
    )
    parser.add_argument(
        "--extract-only", action="store_true",
        help="extract and validate the image, then stop without opening Fakturama",
    )
    parser.add_argument(
        "--fakturama", default=None,
        help="path to Fakturama.exe (otherwise auto-discovered, or $FAKTURAMA_EXE)",
    )
    parser.add_argument(
        "--close", action="store_true",
        help="close Fakturama when the run finishes (default: leave it open)",
    )
    parser.add_argument(
        "--no-screenshots", action="store_true",
        help="do not capture screenshots",
    )
    parser.add_argument(
        "--dump-tree", action="store_true",
        help="attach to Fakturama, write its UI Automation tree, and exit; "
             "use this to adapt uia/selectors.py to a different build",
    )
    parser.add_argument(
        "--discount-into-unit-price", action="store_true",
        help="when the items grid has no Discount column, enter the "
             "discounted unit price instead of stopping. The line and "
             "document totals then match the source document, but U.Price "
             "will not -- every affected line is reported in the run summary",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0,
        help="default per-control timeout in seconds (default: 30)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    settings = Settings(
        image_path=os.path.abspath(args.image),
        fakturama_exe=args.fakturama,
        artifacts_dir=args.artifacts,
        ocr_engine=args.engine,
        refine_cells=not args.no_refine,
        extract_only=args.extract_only,
        keep_open=not args.close,
        screenshots=not args.no_screenshots,
        discount_into_unit_price=args.discount_into_unit_price,
        timeout=args.timeout,
    )

    log = setup_logging(settings.artifacts_dir)

    if args.dump_tree:
        return _dump_tree(settings, log)

    if not os.path.isfile(settings.image_path):
        log.error("order image not found: %s", settings.image_path)
        return 2

    if not settings.extract_only and not settings.fakturama_exe:
        log.error(
            "Fakturama.exe was not found. Run scripts/install_fakturama.ps1, "
            "pass --fakturama <path>, or set FAKTURAMA_EXE. "
            "Use --extract-only to run just the image pipeline."
        )
        return 3

    from .flow.runner import run

    try:
        summary = run(settings)
    except ManualReviewRequired as exc:
        log.error("%s", exc)
        return 10
    except ExtractionError as exc:
        log.error("extraction failed: %s", exc)
        return 11
    except FakturamaAutomationError as exc:
        log.error("automation failed: %s", exc)
        return 12

    status = summary.get("status", "extract-only")
    if status == "manual-review":
        review = summary.get("manual_review", {})
        log.error("stopped for manual review at %s: %s",
                  review.get("step"), review.get("reason"))
        return 10

    print(json.dumps(summary, indent=2, default=str))
    return 0


def _dump_tree(settings: Settings, log) -> int:
    if not settings.fakturama_exe:
        settings.fakturama_exe = discover_fakturama_exe()
    from .uia.app import FakturamaApp

    try:
        app = FakturamaApp(settings).start()
    except FakturamaAutomationError as exc:
        log.error("%s", exc)
        return 3
    path = app.dump_tree()
    log.info("wrote %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
