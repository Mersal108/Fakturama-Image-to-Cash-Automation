"""The whole image-to-cash flow, in order.

Extraction and validation run first and completely.  Fakturama is only
launched once the extracted document has been proven internally consistent,
so a bad OCR read can never reach the accounting system.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from ..config import Settings
from ..errors import ManualReviewRequired
from ..extraction.parser import extract_order
from ..extraction.validation import validate_order
from ..logging_setup import get_logger, setup_logging
from ..uia.app import FakturamaApp
from ..uia.screenshots import ScreenshotRecorder
from .context import RunContext
from .debtor import resolve_debtor
from .invoice import (
    apply_payment,
    confirm_copied_fields,
    create_linked_invoice,
    save_invoice,
)
from .order import confirm_totals, open_new_order, save_order, set_order_header
from .product import resolve_items
from .verify import verify_invoice_row, verify_order_row

log = get_logger("run")


def extract_and_validate(settings: Settings) -> Dict[str, Any]:
    """Stage 1: image -> typed record -> arithmetic gate."""
    log.info("extracting %s with the %r OCR engine",
             settings.image_path, settings.ocr_engine)
    result = extract_order(
        settings.image_path,
        engine_name=settings.ocr_engine,
        refine_cells=settings.refine_cells,
    )

    for note in result.notes:
        log.warning("extraction note: %s", note)

    checks = validate_order(result.order)
    for check in checks:
        log.info("validated: %s", check)

    path = os.path.join(settings.artifacts_dir, "extracted-order.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "order": result.order.to_dict(),
                "engine": result.engine,
                "notes": result.notes,
                "sku_corrections": result.sku_corrections,
                "validation": checks,
            },
            handle,
            indent=2,
        )
    log.info("extracted order written to %s", path)

    return {"result": result, "checks": checks, "json_path": path}


def run(settings: Settings) -> Dict[str, Any]:
    """Run the complete flow and return a summary dictionary."""
    setup_logging(settings.artifacts_dir)
    log.info("artifacts directory: %s", settings.artifacts_dir)

    extraction = extract_and_validate(settings)
    result = extraction["result"]

    if settings.extract_only:
        log.info("extract-only mode: stopping before touching Fakturama")
        return {
            "mode": "extract-only",
            "order": result.order.to_dict(),
            "notes": result.notes,
            "validation": extraction["checks"],
        }

    recorder = ScreenshotRecorder(settings.artifacts_dir, settings.screenshots)
    app = FakturamaApp(settings, recorder).start()
    ctx = RunContext(settings, app, result.order, result.notes)

    try:
        with ctx.step("1-order", "open a New Order and fill its header"):
            open_new_order(ctx)
            set_order_header(ctx)

        with ctx.step("2-debtor", "select or create the Debtor"):
            resolve_debtor(ctx)

        resolve_items(ctx)          # stage 3 logs one step per item

        with ctx.step("4-order-save", "confirm totals and save the Order"):
            confirm_totals(ctx)
            save_order(ctx)

        with ctx.step("4-order-verify", "confirm the Order row in Data > Documents"):
            verify_order_row(ctx)

        with ctx.step("5-invoice", "create and complete the linked Invoice"):
            create_linked_invoice(ctx)
            confirm_copied_fields(ctx)
            apply_payment(ctx)
            save_invoice(ctx)

        with ctx.step("5-invoice-verify", "confirm the Invoice and the open Order"):
            verify_invoice_row(ctx)

        log.info("flow complete")
        summary = ctx.summary()
        summary["status"] = "completed"

    except ManualReviewRequired as exc:
        log.error("%s", exc)
        summary = ctx.summary()
        summary["status"] = "manual-review"
        summary["manual_review"] = {
            "step": exc.step,
            "reason": exc.reason,
            "evidence": exc.evidence,
            "screenshot": exc.screenshot,
        }
        ctx.write_summary(extra={k: summary[k] for k in ("status", "manual_review")})
        app.close()
        return summary

    ctx.write_summary(extra={"status": summary["status"]})
    app.close()
    return summary
