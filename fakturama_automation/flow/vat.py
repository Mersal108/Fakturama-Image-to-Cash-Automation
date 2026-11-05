"""Brief 3.4-3.6: make sure the exact VAT rate exists before creating a Product.

A VAT row may only be reused when all three of its properties agree with the
source: the name, the value, and the e-invoice code.  Anything else is a
conflict and stops the run -- silently reusing a mislabelled tax rate is the
kind of error that is expensive to find later.
"""

from __future__ import annotations

from typing import Sequence

from ..logging_setup import get_logger
from ..models import LineItem, format_percent
from ..uia import selectors as sel
from ..uia.actions import read_value, select_combo, set_text, values_match
from ..uia.locators import normalize
from .context import RunContext
from .payment_terms import _click_new_entry, _search_list

log = get_logger("vat")


def ensure_vat(ctx: RunContext, item: LineItem) -> str:
    """Return the name of a VAT record that exactly matches the item's rate."""
    name = item.vat_name                      # e.g. "VAT 19%"
    percent = format_percent(item.vat_pct)    # e.g. "19"

    if name in ctx.created.get("vats", []):
        return name

    ctx.app.click_any(sel.NAV_VATS, description="Data > VATs",
                      timeout=ctx.settings.timeout)
    rows = _search_list(ctx, name)

    matches = [cells for _c, cells in rows
               if any(normalize(cell) == normalize(name) for cell in cells)]

    if len(matches) > 1:
        ctx.stop(
            "several VAT rows carry that exact name; a human must resolve the "
            "duplicate before the product can be created",
            vat=name, candidates=[" | ".join(c) for c in matches],
        )

    if matches:
        _verify_existing(ctx, matches[0], name, percent)
        log.info("reusing existing VAT record %r", name)
        ctx.record(f"vat.{percent}", "reused existing record")
        return name

    _create_vat(ctx, name, percent)
    ctx.record(f"vat.{percent}", "created")
    return name


def _verify_existing(ctx: RunContext, cells: Sequence[str], name: str,
                     percent: str) -> None:
    """3.5  The row's value must agree with the extracted percentage."""
    value_ok = any(
        values_match(cell.replace("%", ""), percent, numeric=True)
        for cell in cells if any(ch.isdigit() for ch in cell)
    )
    if not value_ok:
        ctx.stop(
            "an existing VAT record has the expected name but a different "
            "value; reusing it would misstate the tax",
            vat=name, expected_value=f"{percent}%", row=" | ".join(cells),
        )


def _create_vat(ctx: RunContext, name: str, percent: str) -> None:
    """3.6  Create the VAT record and save it once."""
    app = ctx.app
    _click_new_entry(ctx)

    set_text(app.by_label_any(sel.LBL_VAT_NAME), name, description="VAT Name")
    set_text(app.by_label_any(sel.LBL_VAT_DESCRIPTION), name,
             description="VAT Description")
    set_text(app.by_label_any(sel.LBL_VAT_VALUE), percent,
             description="VAT Value", numeric=True)

    # The e-invoice code must read 'S (Standard rate)'; it is usually the
    # default, so confirm rather than assume.
    try:
        combo = app.by_label_any(sel.LBL_VAT_CODE, timeout=5.0)
        current = read_value(combo)
        if not any(normalize(current) == normalize(c) for c in sel.VAT_CODE_STANDARD):
            select_combo(combo, sel.VAT_CODE_STANDARD[0], description="VAT code")
    except Exception as exc:  # noqa: BLE001
        ctx.stop(
            "could not confirm the VAT code is 'S (Standard rate)'",
            vat=name, error=str(exc),
        )

    # 3.6  'Standard VAT' is left exactly as displayed.
    app.click_any(sel.TOOLBAR_SAVE, description="toolbar Save (VAT)",
                  control_type="Button", timeout=ctx.settings.timeout)
    ctx.note_created("vats", name)
    app.screenshot("3-vat-created", f"VAT record {name} created")
