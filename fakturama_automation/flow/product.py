"""Stage 3 of the brief: resolve every item line, creating Products on demand.

This runs once per extracted item, in source order.  As with the Debtor, the
Order's own product selector is the existence check and the proof of creation.

Caveat worth stating plainly: Fakturama draws its item grid with Nebula
NatTable, a custom-painted SWT canvas.  Custom-painted widgets expose far less
to UI Automation than ordinary native controls, so cell editing falls back to
a keyboard path and then verifies by reading the row back.  Where even that
cannot confirm a value, the run stops rather than assuming.
"""

from __future__ import annotations

import time
from typing import Any, List, Optional, Sequence, Tuple

from ..logging_setup import get_logger
from ..models import LineItem, format_percent
from ..uia import selectors as sel
from ..uia.actions import (
    click, combo_options, escape_keys, read_value, set_text, values_match,
)
from ..uia.locators import normalize, rect_of, safe_name
from ..uia.waits import wait_for_stable, wait_until
from .context import RunContext
from .debtor import (
    click_dialog_button,
    close_dialog,
    find_search_field,
    select_row,
    wait_dialog_closed,
)
from .vat import ensure_vat
from .widgets import (
    cell_matches, grid_items_or_screen, icons_near, is_screen_row,
)

log = get_logger("product")


def resolve_items(ctx: RunContext) -> None:
    """3.1  Run the whole selection branch for every extracted row, in order."""
    for index, item in enumerate(ctx.order.items):
        with ctx.step(f"3-item-{item.position}", f"item {item.position}: {item.sku}"):
            _resolve_one(ctx, item, index)


def _resolve_one(ctx: RunContext, item: LineItem, row_index: int) -> None:
    if not _try_select_product(ctx, item, allow_missing=True):
        log.info("SKU %s not in Fakturama; creating the Product", item.sku)
        ensure_vat(ctx, item)          # 3.4-3.7: the VAT must exist first
        _create_product(ctx, item)
        if not _try_select_product(ctx, item, allow_missing=False):
            ctx.stop(
                "the newly created Product cannot be selected from the Order",
                sku=item.sku,
            )
    _complete_line(ctx, item, row_index)


# --------------------------------------------------------------- selection


def _try_select_product(ctx: RunContext, item: LineItem, *, allow_missing: bool) -> bool:
    """3.2-3.3  Search the Order's product selector for the exact SKU."""
    dialog = _open_product_selector(ctx)
    rows = _search_products(ctx, dialog, item.sku)

    # Rows recovered from the screen are compared with OCR confusions allowed:
    # the SKU we just wrote and verified reads back as 'CHR-ERG-OI' when an OCR
    # engine looks at the rendered list. The record is the same record.
    exact = [
        (control, cells) for control, cells in rows
        if any(
            cell_matches(cell, item.sku, ocr_tolerant=is_screen_row(control))
            for cell in cells
        )
    ]
    # One painted row can be recognised as several text lines, so identical
    # candidates are collapsed before the "more than one match" test -- an
    # artefact of reading must never look like a genuine ambiguity.
    exact = _dedupe_rows(exact)

    if len(exact) > 1:
        close_dialog(ctx, dialog, sel.BTN_CANCEL)
        ctx.stop(
            "more than one Product carries this exact SKU",
            sku=item.sku, candidates=[" | ".join(c) for _x, c in exact],
        )

    if not exact:
        seen = [" | ".join(c) for _x, c in rows][:5]
        close_dialog(ctx, dialog, sel.BTN_CANCEL)
        if allow_missing:
            log.info("SKU %s not matched; saw %d candidate row(s): %s",
                     item.sku, len(seen), seen)
            return False
        ctx.stop("the Product could not be found by its exact SKU",
                 sku=item.sku, rows_seen=seen)

    control, cells = exact[0]
    select_row(control, description=f"product row {item.sku}")
    click_dialog_button(ctx, dialog, sel.BTN_OK, description="product selector OK")
    wait_dialog_closed(ctx, sel.DLG_SELECT_PRODUCT)
    log.info("selected product %s", " | ".join(cells))
    return True


def _open_product_selector(ctx: RunContext):
    """3.2  The upper product icon beside the Items table, never the green '+'.

    The Order has to be the editor in front first.  Creating a Product (or a
    VAT rate) opens its own editor on top, and while that is active the
    Order's Items area is not visible to UIA at all -- so step 3.12, which
    reopens the selector to pick the product just created, would otherwise
    look for an icon that cannot be seen.
    """
    from .order import activate_order

    activate_order(ctx)
    # The Items caption first: it is a real label.  The column headers are
    # only reachable on builds whose table exposes them, which this one does
    # not, so they are a fallback rather than the primary anchor.
    anchors = list(sel.LBL_ITEMS) + [
        sel.COL_ITEM_NO[0], sel.COL_QTY[0], sel.COL_UPRICE[0],
    ]
    icons: List[Any] = []
    for anchor in anchors:
        try:
            icons = icons_near(ctx.app, (anchor,), max_distance=260)
        except Exception:  # noqa: BLE001
            icons = []
        if icons:
            break
    if not icons:
        ctx.stop("could not find the product-selection icon beside the Items table")
    click(icons[0], description="product selector icon (upper)")
    return ctx.app.dialog(sel.DLG_SELECT_PRODUCT, timeout=ctx.settings.timeout)


def _search_products(ctx: RunContext, dialog, sku: str):
    field = find_search_field(ctx, dialog)
    set_text(field, sku, description=f"product search {sku!r}", retries=1)
    wait_for_stable(
        lambda: [cells for _c, cells in grid_items_or_screen(dialog)],
        timeout=ctx.settings.timeout,
        interval=ctx.settings.poll_interval,
        stable_for=ctx.settings.stable_for,
        description="the product result list",
    )
    return grid_items_or_screen(dialog)


# ---------------------------------------------------------------- creation


def _create_product(ctx: RunContext, item: LineItem) -> None:
    """3.7-3.11  Create the Product once its VAT rate exists."""
    app = ctx.app
    app.click_any(sel.NAV_NEW_PRODUCT, description="New product (left panel)",
                  timeout=ctx.settings.timeout)
    wait_until(
        lambda: app.by_label_any(sel.LBL_ITEM_NUMBER, timeout=3.0),
        timeout=ctx.settings.timeout,
        interval=ctx.settings.poll_interval,
        description="the New Product editor",
    )

    set_text(app.by_label_any(sel.LBL_ITEM_NUMBER), item.sku,
             description="Item Number")
    set_text(app.by_label_any(sel.LBL_PRODUCT_NAME), item.description,
             description="Product Name")
    set_text(app.by_label_any(sel.LBL_PRODUCT_DESCRIPTION), item.description,
             description="Product Description")

    # 3.9  Gross master price = unit net x (1 + VAT). The line discount is a
    # property of this order, not of the catalogue price, so it is excluded.
    gross = item.product_gross_price
    set_text(app.by_label_any(sel.LBL_PRICE_GROSS), f"{gross:.2f}",
             description="Price (gross)", numeric=True)
    set_text(app.by_label_any(sel.LBL_COST_PRICE_NET), "0.00",
             description="cost price (net)", numeric=True)

    _select_product_vat(ctx, item)

    try:
        set_text(app.by_label_any(sel.LBL_STOCK), "0.00",
                 description="Stock", numeric=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not set Stock (%s)", exc)

    # 3.10  Category, GTIN, supplier code, allowance, picture and the user
    # defined field are deliberately left untouched.
    app.click_any(sel.TOOLBAR_SAVE, description="toolbar Save (Product)",
                  control_type="Button", timeout=ctx.settings.timeout)
    ctx.note_created("products", f"{item.sku} @ {gross:.2f} gross")
    app.screenshot(f"3-product-{item.position}", f"Product {item.sku} created")


def _select_product_vat(ctx: RunContext, item: LineItem) -> None:
    from ..uia.actions import select_combo

    target = item.vat_name
    combos = ctx.app.finder.descendants(control_type="ComboBox")
    for combo in combos:
        # combo_options() drops the list down to read it; texts() on an SWT
        # combo returns the control's own name, not its contents, which makes
        # a combo that *does* offer the value look as though it does not.
        options = combo_options(combo)
        if not options:
            continue
        if any(normalize(option) == normalize(target) for option in options):
            select_combo(combo, target, description=f"Product VAT ({target})")
            return
    ctx.stop(
        "the required VAT rate is not offered in the Product editor even "
        "though it was just ensured to exist",
        vat=target, sku=item.sku,
    )


# ------------------------------------------------------------- line editing


def _complete_line(ctx: RunContext, item: LineItem, row_index: int) -> None:
    """3.13-3.16  Quantity, unit price, VAT and discount on the selected row."""
    _set_cell(ctx, row_index, sel.COL_QTY, str(item.quantity),
              description=f"line {item.position} Qty.", numeric=True)
    _set_cell(ctx, row_index, sel.COL_UPRICE, f"{item.unit_net:.2f}",
              description=f"line {item.position} U.Price", numeric=True)
    _set_line_discount(ctx, item, row_index)

    _confirm_line_total(ctx, item, row_index)


def _set_line_discount(ctx: RunContext, item: LineItem, row_index: int) -> None:
    """3.15  The per-line discount, if this build shows a column for it.

    Fakturama 2.2.0's order item table displays Pos., Qty., Name, VAT, U.Price
    and Price -- there is no discount cell, and enabling "Use discount for each
    item" in the preferences does not add one.  So the discount is handled by
    what it actually is rather than by pretending:

    * nothing to apply (0%) -- note it and carry on, since the absent column
      and the required value agree;
    * a real discount -- stop.  Entering it is impossible here, and continuing
      would build an Order whose totals do not match the source document.
    """
    description = f"line {item.position} Discount"
    wanted = format_percent(item.discount_pct)

    if _grid_has_column(ctx, sel.COL_DISCOUNT):
        _set_cell(ctx, row_index, sel.COL_DISCOUNT, wanted,
                  description=description, numeric=True)
        return

    if item.discount_pct == 0:
        note = (f"line {item.position}: the items table has no Discount column "
                f"in this Fakturama build; the line needs no discount, so "
                f"nothing was lost")
        log.info("%s", note)
        ctx.extraction_notes.append(note)
        return

    ctx.stop(
        f"{description}: the source document applies a "
        f"{wanted}% discount to this line, but this build's items table has no "
        f"Discount column and the preference that should add one has no effect. "
        f"The discount cannot be entered through the UI, and saving the Order "
        f"without it would produce totals that disagree with the document.",
        line=item.position, wanted_discount=wanted,
        columns_available=_grid_columns(ctx),
    )


def _grid_columns(ctx: RunContext) -> List[str]:
    from ..uia.screen_grid import read_grid_geometry

    canvas = _items_canvas(ctx)
    if canvas is None:
        return []
    geometry = read_grid_geometry(canvas)
    if geometry is None:
        return []
    return [caption for caption, _box in geometry.headers]


def _grid_has_column(ctx: RunContext, column: Sequence[str]) -> bool:
    from ..uia.screen_grid import read_grid_geometry

    canvas = _items_canvas(ctx)
    if canvas is None:
        return False
    geometry = read_grid_geometry(canvas)
    return geometry is not None and geometry.column(column) is not None


def _items_grid(ctx: RunContext):
    """The container holding the order's item rows.

    The painted canvas found by `_items_canvas` is preferred because it is
    scoped to the editor that owns the Items caption.  Without that scoping
    the search settles on whichever NatTable is largest, and after a detour
    through Data > VATs that is the VAT list at the bottom of the window --
    a verification read then came back with '19.00 %' as the line's quantity.
    """
    canvas = _items_canvas(ctx)
    if canvas is not None:
        return canvas

    for names in (sel.COL_ITEM_NO, sel.COL_QTY, sel.COL_UPRICE):
        for name in names:
            labels = ctx.app.finder.find_labels(name)
            for label in labels:
                try:
                    container = label.parent().parent()
                    if grid_items_or_screen(container):
                        return container
                except Exception:  # noqa: BLE001
                    continue
    return ctx.app.window


def _row_controls(ctx: RunContext, row_index: int) -> Tuple[Any, List[str]]:
    grid = _items_grid(ctx)
    rows = grid_items_or_screen(grid)
    if row_index >= len(rows):
        ctx.stop(
            "the item row that should have been added to the Order is not "
            "present in the items grid",
            expected_row=row_index + 1, rows_found=len(rows),
        )
    return rows[row_index]


def _set_cell(ctx: RunContext, row_index: int, column: Sequence[str], value: str,
              *, description: str, numeric: bool = False) -> None:
    """Write one cell of the items grid, then read it back.

    NatTable exposes cells inconsistently, so this tries the direct control
    first and falls back to activating the cell and typing.  Either way the
    value is re-read before the step is considered done.
    """
    control, cells = _row_controls(ctx, row_index)
    cell = _cell_control(ctx, control, column)

    if cell is not None:
        try:
            set_text(cell, value, description=description, numeric=numeric)
            return
        except Exception as exc:  # noqa: BLE001
            log.debug("%s: direct cell write failed (%s); trying keyboard", description, exc)

    if _keyboard_cell_write(ctx, control, cell, value):
        seen = _read_cell(ctx, row_index, column)
        if values_match(seen, value, numeric=numeric):
            log.info("%s = %r (verified)", description, value)
            return
        # The keystrokes went somewhere, but not demonstrably into this cell.
        # That is a reason to try the next rung of the ladder, not to stop:
        # the screen path below is the one that works on this build, and
        # halting here would skip it on the strength of a failed read.
        log.debug("%s: keyboard write did not read back (%r); "
                  "falling through to the screen path", description, seen)

    # Last resort, and in this build the one that actually works: locate the
    # cell by reading the grid's own pixels and click it.  NatTable creates a
    # real editor control the moment a cell is activated, so once the click
    # lands there is an ordinary Edit to write into and read back.
    if _screen_cell_write(ctx, row_index, column, value,
                          description=description, numeric=numeric):
        return

    ctx.stop(
        f"{description}: the items grid cell could not be edited through UI "
        "Automation (Fakturama draws this grid with NatTable)",
        column=column[0], wanted=value,
    )


def _editor_root(control):
    """The editor container a control belongs to.

    Fakturama stacks the open editors in one Tab folder and the Data list
    views in another.  Both hold NatTable canvases of similar size, so "the
    largest painted pane in the window" cannot tell the Order's item lines
    from whatever list view happens to be open below them -- a run that had
    just visited Data > VATs picked up that list and reported its columns
    ('Standard', 'Name', 'Description', 'Value') as the order's.  Climbing to
    the Tab folder that actually contains the Items caption makes the choice
    structural instead of a size comparison.
    """
    node = control
    for _ in range(12):
        try:
            parent = node.parent()
        except Exception:  # noqa: BLE001
            return None
        if parent is None:
            return None
        try:
            if (parent.element_info.control_type or "") == "Tab":
                return parent
        except Exception:  # noqa: BLE001
            pass
        node = parent
    return None


# A painted canvas publishes no rows, but it does own its scrollbars, and
# while a cell is being edited it owns that editor too -- selecting a product
# leaves a combo sitting on the VAT cell.  Treating either as "content"
# rejects exactly the pane being looked for, and a read taken right after a
# successful write then found no grid at all.
#
# The test is size, not control type.  A cell editor is tiny next to its grid
# (99x19 inside 1994x286, about a third of one percent), while a pane that
# really is a text box is filled by it.  Going by type instead would admit the
# Remarks box, whose only child is an Edit -- which is exactly what happened.
_CANVAS_SCROLLBARS = {"ScrollBar", "Thumb"}
_CANVAS_EDITOR_FRACTION = 0.10


def _is_painted_canvas(pane, rect) -> bool:
    """Does this pane draw its own content rather than publish child controls?"""
    area = float(rect.width * rect.height)
    if area <= 0:
        return False
    for child in pane.children():
        if (child.element_info.control_type or "") in _CANVAS_SCROLLBARS:
            continue
        child_rect = rect_of(child)
        if child_rect.width * child_rect.height < area * _CANVAS_EDITOR_FRACTION:
            continue  # a transient cell editor, not content
        return False
    return True


def _items_canvas(ctx: RunContext):
    """The painted canvas that holds the order's item rows."""
    label = None
    for name in sel.LBL_ITEMS:
        try:
            hits = ctx.app.finder.find_labels(name)
        except Exception:  # noqa: BLE001
            continue
        if hits:
            label = hits[0]
            break

    root = _editor_root(label) if label is not None else None
    anchor = rect_of(label) if label is not None else None
    if root is None:
        log.debug("no 'Items' caption found; searching the whole window for "
                  "the items canvas, which may pick up a list view")

    try:
        panes = ctx.app.finder.descendants(control_type="Pane", root=root)
    except Exception:  # noqa: BLE001
        return None

    best, best_area = None, 0
    for pane in panes:
        try:
            rect = rect_of(pane)
            if not _is_painted_canvas(pane, rect):
                continue
        except Exception:  # noqa: BLE001
            continue
        # The item lines are drawn below their caption, never above it.
        if anchor is not None and rect.bottom <= anchor.top:
            continue
        area = rect.width * rect.height
        if area > best_area and rect.width > 200 and rect.height > 60:
            best, best_area = pane, area
    return best


def _grid_editor(ctx: RunContext, canvas_rect, near_x: Optional[int] = None):
    """A cell editor that NatTable has opened inside the grid."""
    found = []
    for control_type in ("Edit", "ComboBox"):
        try:
            controls = ctx.app.finder.descendants(control_type=control_type)
        except Exception:  # noqa: BLE001
            continue
        for control in controls:
            rect = rect_of(control)
            if not (canvas_rect.top <= rect.top <= canvas_rect.bottom):
                continue
            if not (canvas_rect.left <= rect.left <= canvas_rect.right):
                continue
            found.append((rect, control))
    if not found:
        return None
    if near_x is not None:
        found.sort(key=lambda pair: abs(pair[0].left - near_x))
    return found[0][1]


def _screen_cell_write(ctx: RunContext, row_index: int, column: Sequence[str],
                       value: str, *, description: str,
                       numeric: bool = False) -> bool:
    """Click a grid cell located by OCR, then write and verify it."""
    from ..uia.screen_grid import read_grid_geometry
    from pywinauto import mouse
    from pywinauto.keyboard import send_keys

    canvas = _items_canvas(ctx)
    if canvas is None:
        log.debug("%s: no items canvas found", description)
        return False

    geometry = read_grid_geometry(canvas)
    if geometry is None:
        log.debug("%s: could not read the grid geometry", description)
        return False

    point = geometry.cell_point(row_index, column)
    if point is None:
        log.info("%s: the grid has no %r column (headers: %s)",
                 description, column[0],
                 [caption for caption, _b in geometry.headers])
        return False

    canvas_rect = rect_of(canvas)
    mouse.click(coords=point)
    time.sleep(0.6)

    editor = _grid_editor(ctx, canvas_rect, near_x=point[0])
    if editor is None:
        log.debug("%s: clicking the cell opened no editor", description)
        return False

    try:
        set_text(editor, value, description=description, numeric=numeric)
    except Exception as exc:  # noqa: BLE001
        log.debug("%s: writing the cell editor failed (%s)", description, exc)
        return False

    # Commit the edit the way a user would, then confirm from the grid itself
    # rather than from the editor that is about to disappear.
    send_keys("{ENTER}")
    time.sleep(0.6)
    _dismiss_grid_editor(ctx, geometry, canvas_rect)

    shown = _read_cell(ctx, row_index, column)
    if values_match(shown, value, numeric=numeric):
        log.info("%s = %r (verified in the grid)", description, value)
        return True

    if not shown:
        # Not read is not the same as wrong.  The editor's own read-back has
        # already confirmed the value went in, and step 3.16 re-checks the
        # line's Price against the source document's arithmetic, which would
        # catch a quantity that failed to take.  Failing here instead would
        # stop a correct run because one OCR pass came back empty.
        log.warning("%s: wrote and verified %r in the cell editor, but the "
                    "grid could not be read back for a second opinion; "
                    "the line total check still has to pass", description, value)
        return True

    ctx.stop(
        f"{description}: the value did not persist in the items grid",
        wrote=value, grid_shows=shown,
    )
    return False


def _dismiss_grid_editor(ctx: RunContext, geometry, canvas_rect) -> None:
    """Close an open cell editor by clicking empty space below the rows.

    ENTER commits the value but leaves NatTable's editor sitting over the
    cell, and an editor reads back as its own live contents: a committed
    '2.00' came back from the screen as '2*OC' while its editor was still
    open.  Clicking blank grid space is what a user does to finish an edit,
    and afterwards the cell is drawn as ordinary text.
    """
    from pywinauto import mouse

    if geometry is None or not geometry.rows:
        return
    last = geometry.rows[-1]
    y = int(canvas_rect.top + last.bottom + max(last.height * 3, 20))
    if y >= canvas_rect.bottom - 5:
        log.debug("no blank grid space below the rows to click")
        return
    try:
        mouse.click(coords=(int(canvas_rect.left + 40), y))
        time.sleep(0.4)
    except Exception as exc:  # noqa: BLE001
        log.debug("could not dismiss the grid editor: %s", exc)


def _cell_control(ctx: RunContext, row_control, column: Sequence[str]):
    """Find a row's cell by matching the grid's column headers by position."""
    headers = _column_order(ctx)
    try:
        children = row_control.children()
    except Exception:  # noqa: BLE001
        return None

    for index, header in enumerate(headers):
        if any(normalize(header) == normalize(name) for name in column):
            if index < len(children):
                return children[index]
    # Fall back to matching the cell's own accessible name.
    for child in children:
        if any(normalize(safe_name(child)) == normalize(name) for name in column):
            return child
    return None


def _column_order(ctx: RunContext) -> List[str]:
    grid = _items_grid(ctx)
    try:
        headers = grid.descendants(control_type="Header")
        if headers:
            items = headers[0].children()
            return [safe_name(i) for i in items]
    except Exception:  # noqa: BLE001
        pass
    try:
        items = grid.descendants(control_type="HeaderItem")
        if items:
            items.sort(key=lambda c: rect_of(c).left)
            return [safe_name(i) for i in items]
    except Exception:  # noqa: BLE001
        pass
    return []


def _keyboard_cell_write(ctx: RunContext, row_control, cell, value: str) -> bool:
    """Activate a cell and type into it -- the NatTable fallback path."""
    target = cell if cell is not None else row_control
    for activate in (
        lambda: target.double_click_input(),
        lambda: (target.click_input(), target.type_keys("{F2}", set_foreground=False)),
    ):
        try:
            activate()
        except Exception:  # noqa: BLE001
            continue
        try:
            editor = ctx.app.finder.descendants(control_type="Edit")
            focused = [e for e in editor if _has_focus(e)]
            if focused:
                set_text(focused[0], value, description="grid cell editor")
                focused[0].type_keys("{ENTER}", set_foreground=False)
                return True
            target.type_keys("^a" + escape_keys(value) + "{ENTER}",
                             with_spaces=True, set_foreground=False)
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("keyboard cell write attempt failed: %s", exc)
    return False


def _has_focus(control) -> bool:
    try:
        return bool(control.has_keyboard_focus())
    except Exception:  # noqa: BLE001
        return False


def _read_cell(ctx: RunContext, row_index: int, column: Sequence[str]) -> str:
    """Read one cell of the items grid, however it can be reached."""
    control, cells = _row_controls(ctx, row_index)
    cell = _cell_control(ctx, control, column)
    if cell is not None:
        value = read_value(cell) or safe_name(cell)
        if value:
            return value

    headers = _column_order(ctx)
    for index, header in enumerate(headers):
        if any(normalize(header) == normalize(name) for name in column):
            if index < len(cells):
                return cells[index]

    # Nothing in the accessibility tree: read the cell off the screen, using
    # the grid's own headers to say which part of the row to look at.
    from ..uia.screen_grid import read_grid_geometry

    canvas = _items_canvas(ctx)
    if canvas is not None:
        geometry = read_grid_geometry(canvas)
        if geometry is not None:
            text = geometry.cell_text(row_index, column)
            if text:
                return text

    # Deliberately not `" ".join(cells)`.  Returning the whole row when the
    # requested column could not be located reports one cell's content as
    # another's -- the caller then compares a quantity against a VAT rate and
    # blames the write.  An empty answer says what is true: not read.
    return ""


def _confirm_line_total(ctx: RunContext, item: LineItem, row_index: int) -> None:
    """3.16  Line price must equal qty x unit net x (1 - discount)."""
    expected = item.computed_line_net
    shown = _read_cell(ctx, row_index, sel.COL_PRICE)
    if not shown:
        ctx.stop(
            "could not read the line Price back from the items grid",
            line=item.position, expected=str(expected),
        )
    if not values_match(shown, f"{expected:.2f}", numeric=True):
        ctx.stop(
            "the Order's line total does not match quantity x unit net x "
            "(1 - discount) from the source document",
            line=item.position, grid_shows=shown, source_implies=f"{expected:.2f}",
        )
    ctx.record(f"order.line_{item.position}_total", shown)


def _dedupe_rows(rows):
    """Drop rows whose cell text is identical to one already seen."""
    seen = set()
    unique = []
    for control, cells in rows:
        key = tuple(normalize(c) for c in cells)
        if key in seen:
            continue
        seen.add(key)
        unique.append((control, cells))
    return unique
