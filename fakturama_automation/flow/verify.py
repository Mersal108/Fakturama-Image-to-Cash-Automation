"""Reading saved records back out of Data > Documents.

Steps 4.5 and 5.5 of the brief.  This is the final evidence that the flow did
what it claims: not that a Save button was clicked, but that a row with the
right reference, state and total exists in Fakturama's own list.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from ..logging_setup import get_logger
from ..uia import selectors as sel
from ..uia.actions import set_text, values_match
from ..extraction.normalize import ocr_equivalent
from ..uia.locators import normalize, rect_of
from ..uia.waits import wait_for_stable
from .context import RunContext
from .widgets import grid_items_or_screen

log = get_logger("verify")


def open_documents(ctx: RunContext) -> List[List[str]]:
    """Open Data > Documents and return its rows once the list settles."""
    ctx.app.click_any(sel.NAV_DOCUMENTS, description="Data > Documents",
                      timeout=ctx.settings.timeout)
    try:
        field = ctx.app.by_label_any(sel.DLG_SEARCH_FIELD, timeout=4.0)
        set_text(field, ctx.order.external_reference,
                 description="Documents search", retries=1)
    except Exception as exc:  # noqa: BLE001
        log.debug("no search box on the Documents list (%s)", exc)

    try:
        wait_for_stable(
            lambda: _document_rows(ctx),
            timeout=ctx.settings.timeout,
            interval=ctx.settings.poll_interval,
            stable_for=ctx.settings.stable_for,
            description="the Documents list",
        )
    except Exception:  # noqa: BLE001
        pass
    return _document_rows(ctx)


# Headings that identify the Documents list rather than some other grid.
DOCUMENT_HEADINGS = ("cust.ref.", "document", "state")


def _document_rows(ctx: RunContext) -> List[List[str]]:
    """Rows of the Documents list, found by its own column headings.

    Picking "the largest painted pane" is not enough here.  The Documents
    view carries a filter strip that is also a painted pane, and reading that
    instead returned rows like '---' and 'not shipped | shipped' -- so a
    correctly saved Order looked absent.  Identifying the grid by the
    headings it draws is structural: whichever pane says 'Cust.Ref.' is the
    one holding documents, wherever it happens to sit.
    """
    from ..uia.screen_grid import read_grid_geometry

    best: List[List[str]] = []
    try:
        panes = ctx.app.finder.descendants(control_type="Pane")
    except Exception:  # noqa: BLE001
        panes = []

    for pane in panes:
        try:
            rect = rect_of(pane)
            if rect.width < 300 or rect.height < 80:
                continue
            if any(c.element_info.control_type not in ("ScrollBar", "Thumb")
                   for c in pane.children()):
                continue
        except Exception:  # noqa: BLE001
            continue

        geometry = read_grid_geometry(pane)
        if geometry is None or not geometry.headers:
            continue
        captions = {normalize(caption) for caption, _box in geometry.headers}
        if not any(h in captions for h in DOCUMENT_HEADINGS):
            continue

        rows = []
        for index in range(len(geometry.rows)):
            cells = [geometry.cell_text(index, (caption,))
                     for caption, _box in geometry.headers]
            if any(c.strip() for c in cells):
                rows.append(cells)
        if len(rows) > len(best):
            best = rows

    if best:
        log.info("read %d row(s) from the Documents list", len(best))
        return best

    log.debug("no pane carried the Documents headings; falling back to the "
              "largest painted grid")
    return [cells for _c, cells in grid_items_or_screen(ctx.app.window)]


def verify_order_row(ctx: RunContext) -> List[str]:
    """4.5  One Order row with the expected Cust.Ref., open state and total."""
    rows = open_documents(ctx)
    order = ctx.order

    candidates = [r for r in rows if _has(r, order.external_reference)]
    if not candidates:
        ctx.stop(
            "no document row carries the order's external reference",
            cust_ref=order.external_reference, rows=_preview(rows),
        )

    matching = [r for r in candidates
                if _has_any(r, sel.STATE_OPEN) and _has_amount(r, order.gross_total)]
    if not matching:
        ctx.stop(
            "the saved Order row does not show the expected open state and total",
            cust_ref=order.external_reference,
            expected_total=str(order.gross_total),
            rows=_preview(candidates),
        )
    if len(matching) > 1:
        ctx.stop(
            "more than one open Order carries this external reference",
            cust_ref=order.external_reference, rows=_preview(matching),
        )

    row = matching[0]
    ctx.record("order.documents_row", " | ".join(row))
    log.info("Order verified in Data > Documents: %s", " | ".join(row))
    return row


def verify_invoice_row(ctx: RunContext) -> Tuple[List[str], List[str]]:
    """5.5  The Invoice row has the expected state and total, Order still open."""
    rows = open_documents(ctx)
    order = ctx.order
    expect_paid = order.payment.status.value == "PAID"

    candidates = [r for r in rows if _has(r, order.external_reference)]
    if len(candidates) < 2:
        ctx.stop(
            "Data > Documents does not show both the Order and its linked Invoice",
            cust_ref=order.external_reference, rows=_preview(candidates),
        )

    invoice_rows = [r for r in candidates if _has_any(
        r, sel.STATE_PAID if expect_paid else ("open", "unpaid", "offen"))]
    order_rows = [r for r in candidates if _has_any(r, sel.STATE_OPEN)]

    if not invoice_rows:
        ctx.stop(
            f"no Invoice row shows the expected "
            f"{'paid' if expect_paid else 'unpaid'} state",
            rows=_preview(candidates),
        )
    if not order_rows:
        ctx.stop(
            "the source Order no longer appears as open after the Invoice was saved",
            rows=_preview(candidates),
        )

    invoice = invoice_rows[0]
    if not _has_amount(invoice, order.gross_total):
        ctx.stop(
            "the Invoice total does not match the source document",
            expected_total=str(order.gross_total), row=" | ".join(invoice),
        )

    ctx.record("invoice.documents_row", " | ".join(invoice))
    ctx.record("order.still_open_row", " | ".join(order_rows[0]))
    log.info("Invoice verified: %s", " | ".join(invoice))
    return invoice, order_rows[0]


# ------------------------------------------------------------------ helpers


def _has(row: Sequence[str], value: str) -> bool:
    """Does any cell of this row carry this value?

    These rows come off the screen -- Fakturama's Documents list is a
    NatTable and publishes nothing to UI Automation -- so the comparison
    allows for the confusions reading pixels introduces and nothing else.
    The reference 'WEB-2026-0921-B04' is listed correctly by Fakturama and
    read back as '...-804', because a 'B' and an '8' are the same shape at
    this size. Refusing that would mean a correctly saved Order could never
    be confirmed.
    """
    target = normalize(value)
    if not target:
        return False
    for cell in row:
        plain = normalize(cell)
        if target == plain or target in plain:
            return True
        if ocr_equivalent(target, plain):
            return True
        # The reference is often one word inside a wider recognised cell.
        for word in plain.split():
            if ocr_equivalent(target, word):
                return True
    return False


def _has_any(row: Sequence[str], values: Sequence[str]) -> bool:
    return any(_has(row, value) for value in values)


def _has_amount(row: Sequence[str], amount) -> bool:
    return any(values_match(cell, f"{amount:.2f}", numeric=True) for cell in row)


def _preview(rows: Sequence[Sequence[str]], limit: int = 6) -> List[str]:
    return [" | ".join(r) for r in rows[:limit]]
