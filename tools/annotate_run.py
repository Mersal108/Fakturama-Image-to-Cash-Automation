"""Draw callouts onto the screenshots a run captured.

The run already saves one screenshot per stage, but a reviewer opening them
sees a dense accounting application and has to hunt for the thing that
matters. This marks it: a numbered box around each value the automation wrote
or checked, and a caption listing what the numbers point at.

The boxes are not hardcoded coordinates. Each one is found by running OCR over
the screenshot and locating the text itself, which is the same
label-anchoring the rest of the project is built on -- so the annotations
survive a different window size, a different theme, or a re-run that happens
to lay the editor out slightly differently. A target that cannot be found is
reported and skipped rather than drawn in the wrong place.

Usage:

    python tools/annotate_run.py docs/screenshots
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fakturama_automation.extraction.geometry import Box  # noqa: E402
from fakturama_automation.extraction.ocr import get_engine  # noqa: E402
from fakturama_automation.extraction.normalize import (  # noqa: E402
    ocr_equivalent,
)

MARK = (214, 40, 40)
MARK_FILL = (214, 40, 40)
PANEL_BG = (16, 42, 67)
PANEL_INK = (255, 255, 255)
PANEL_DIM = (158, 180, 202)
BAND_BG = (11, 29, 47)
ACCENT = (86, 180, 233)
PAD = 7
BOX_WIDTH = 4
CAPTION_SIZE = 25
BADGE_SIZE = 26
TITLE_SIZE = 40
BODY_SIZE = 27


def _font(size: int, bold: bool = False):
    for name in (("segoeuib.ttf", "arialbd.ttf") if bold
                 else ("segoeui.ttf", "arial.ttf")):
        path = os.path.join(r"C:\Windows\Fonts", name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _key(text: str) -> str:
    """Comparable form: no spaces, no currency marks, lower case."""
    out = "".join(text.split()).casefold()
    for ch in "$€":
        out = out.replace(ch, "")
    return out


def _same(a: str, b: str) -> bool:
    """Equal, or equal once OCR's letter/digit confusions are allowed for."""
    return a == b or ocr_equivalent(a, b)


def _find(words, needle: str, occurrence: int = 1) -> Optional[Box]:
    """The box around a phrase, matched across consecutive words.

    Matching is tolerant of the confusions this project documents elsewhere --
    'CUST000001' is read off a screenshot as 'CUSTOOOOOI', and a leading '$'
    as an 'S'. Being strict here would simply mean no annotation appears over
    the very values most worth pointing at.
    """
    target = _key(needle)
    if not target:
        return None
    # Some captions have to point at one particular instance of a repeated
    # value: '315.35' is the Invoice total, the payment amount AND both rows
    # of the Documents grid. `occurrence` selects which, in reading order.
    # It is an index into what was recognised, not a screen coordinate.
    seen: List[Box] = []

    def _take(box):
        if not any(abs(box.x - s.x) < 5 and abs(box.y - s.y) < 5 for s in seen):
            seen.append(box)
        return seen[occurrence - 1] if len(seen) >= occurrence else None

    for start in range(len(words)):
        joined = ""
        box = None
        for word in words[start:start + 8]:
            joined += _key(word.text)
            box = word.box if box is None else box.union(word.box)
            if len(joined) == len(target) and _same(joined, target):
                hit = _take(box)
                if hit is not None:
                    return hit
                break
            # A currency mark read as a letter ('$250.00' -> 'S250.00')
            # leaves one extra character on the front; the text caret of a
            # focused field is read as a trailing '1' ('...-B04' -> '...-B041').
            # Allow one stray character at either end.
            if len(joined) == len(target) + 1 and (
                    _same(joined[1:], target) or _same(joined[:-1], target)):
                hit = _take(box)
                if hit is not None:
                    return hit
                break
            if len(joined) > len(target):
                break
    return None


def _wrap(draw, text: str, font, width: int) -> List[str]:
    """Greedy wrap, because the bands are a fixed width and captions vary."""
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _header(canvas, draw, stage, width: int) -> int:
    """Title band: which stage this is, what it does, what to look at."""
    title_f, body_f, label_f = (_font(TITLE_SIZE, bold=True),
                                _font(BODY_SIZE), _font(BODY_SIZE, bold=True))
    margin = 34
    inner = width - 2 * margin
    doing = _wrap(draw, stage["doing"], body_f, inner - 150)
    check = _wrap(draw, stage["checking"], body_f, inner - 150)
    height = 30 + TITLE_SIZE + 16 + (len(doing) + len(check)) * 36 + 26
    draw.rectangle((0, 0, width, height), fill=BAND_BG)
    draw.rectangle((0, height - 5, width, height), fill=ACCENT)
    y = 26
    draw.text((margin, y), stage["title"], font=title_f, fill=PANEL_INK)
    y += TITLE_SIZE + 14
    for label, body in (("Doing", doing), ("Check", check)):
        draw.text((margin, y), f"{label}:", font=label_f, fill=ACCENT)
        for i, line in enumerate(body):
            draw.text((margin + 110, y), line, font=body_f,
                      fill=PANEL_INK if label == "Doing" else PANEL_DIM)
            y += 36
            if i < len(body) - 1:
                continue
    return height


def annotate(image_path: str, targets: Sequence[tuple],
             out_path: str, stage: Optional[dict] = None) -> List[str]:
    """Box each target's text, with a stage band above and a legend below."""
    image = Image.open(image_path).convert("RGB")
    document = get_engine("windows", scale=2.0).recognize(image_path)
    words = list(document.words)

    found: List[Tuple[int, Box, str]] = []
    missing: List[str] = []
    for target in targets:
        needle, caption = target[0], target[1]
        box = _find(words, needle, target[2] if len(target) > 2 else 1)
        if box is None:
            missing.append(needle)
            continue
        found.append((len(found) + 1, box, caption))

    # Measure both bands before building the canvas.
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    caption_f = _font(CAPTION_SIZE)
    wrapped = [(n, _wrap(probe, c, caption_f, image.width - 220))
               for n, _b, c in found]
    legend_h = 34 + sum(len(w) for _n, w in wrapped) * 36 + 26
    band_h = 0
    if stage:
        band_h = _header(Image.new("RGB", (8, 8)), probe, stage, image.width)

    canvas = Image.new("RGB", (image.width, band_h + image.height + legend_h),
                       (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    if stage:
        _header(canvas, draw, stage, image.width)
    canvas.paste(image, (0, band_h))

    # Badges go beside their box, not above it: targets like the totals column
    # sit ~20px apart, and a badge stacked above each one covers the value
    # above it. Each badge steps sideways until it clears the ones already
    # placed, and a leader line keeps it attached to its box.
    tf = _font(BADGE_SIZE, bold=True)
    placed: List[Tuple[int, int, int, int]] = []

    def _clear(rect, others) -> bool:
        ax0, ay0, ax1, ay1 = rect
        return all(ax1 < bx0 or ax0 > bx1 or ay1 < by0 or ay0 > by1
                   for bx0, by0, bx1, by1 in others)

    for number, box, _caption in found:
        x0, y0 = box.x - PAD, box.y - PAD + band_h
        x1, y1 = box.right + PAD, box.bottom + PAD + band_h
        draw.rectangle((x0, y0, x1, y1), outline=MARK, width=BOX_WIDTH)

        tag = str(number)
        bw = int(draw.textlength(tag, font=tf)) + 20
        bh = BADGE_SIZE + 10
        cy = (y0 + y1) // 2
        top = cy - bh // 2

        spot = None
        # Walk left in badge-width steps, then right, then above.
        for step in range(0, 7):
            left = x0 - 10 - bw - step * (bw + 8)
            if left < 4:
                break
            rect = (left, top, left + bw, top + bh)
            if _clear(rect, placed):
                spot = rect
                break
        if spot is None:
            for step in range(0, 7):
                left = x1 + 10 + step * (bw + 8)
                if left + bw > canvas.width - 4:
                    break
                rect = (left, top, left + bw, top + bh)
                if _clear(rect, placed):
                    spot = rect
                    break
        if spot is None:
            spot = (x0 - 2, max(band_h, y0 - bh), x0 - 2 + bw,
                    max(band_h, y0 - bh) + bh)

        bx0, by0, bx1, by1 = spot
        # Leader line from the badge to the nearer edge of the box.
        if bx1 <= x0:
            draw.line((bx1, cy, x0, cy), fill=MARK, width=3)
        elif bx0 >= x1:
            draw.line((x1, cy, bx0, cy), fill=MARK, width=3)
        draw.rectangle(spot, fill=MARK_FILL)
        draw.text((bx0 + 10, by0 + 4), tag, font=tf, fill=(255, 255, 255))
        placed.append(spot)

    top = band_h + image.height
    draw.rectangle((0, top, canvas.width, canvas.height), fill=PANEL_BG)
    y = top + 24
    for (number, lines), (_n, _box, _c) in zip(wrapped, found):
        draw.text((34, y), f"{number}", font=_font(CAPTION_SIZE, bold=True),
                  fill=MARK)
        for line in lines:
            draw.text((90, y), line, font=caption_f, fill=PANEL_INK)
            y += 36

    canvas.save(out_path)
    return missing


# One band per capture: what this stage is doing, and what a reviewer should
# check on the picture. Keyed by the slug the run gives its screenshots.
STAGES: Dict[str, dict] = {
    "1-order": {
        "title": "Stage 1 — open the Order first, and leave it open",
        "doing": "Data > New Order, then fill the header from the image: order date, "
                 "the external reference into Cust.Ref., price mode Net. The Order "
                 "editor is opened before any master data exists, because Fakturama "
                 "fills a new editor's combo boxes when it is constructed.",
        "checking": "The document number and date are Fakturama's own fields, not "
                    "text typed over a guess; the reference matches the source image "
                    "exactly.",
    },
    "2-payment-method": {
        "title": "Stage 2a — create the payment method before the Debtor editor",
        "doing": "The extracted method 'Bank Transfer' is created as a term of "
                 "payment and mapped to the e-invoice code the brief requires. This "
                 "has to happen now: a Debtor editor built earlier would not list it.",
        "checking": "'Set as standard' is deliberately left alone — the brief asks "
                    "for this order's method, not a new workspace default.",
    },
    "2-debtor-saved": {
        "title": "Stage 2b — the Debtor, saved",
        "doing": "A new Contact is created from the image's customer block: company, "
                 "contact name, address, alias, and the payment method from 2a. "
                 "Fakturama proposes the customer ID and it is left untouched.",
        "checking": "Every field was written and then read back before the save; the "
                    "payment method is populated, which is the proof that ordering "
                    "2a before 2b was necessary.",
    },
    "2-debtor": {
        "title": "Stage 2c — select the Debtor back onto the Order",
        "doing": "Back on the Order editor, the saved Debtor is selected so that "
                 "Fakturama itself populates the billing and delivery address block.",
        "checking": "The address was filled in by the application from its own "
                    "record, not typed in by the automation — that is what makes it "
                    "evidence the Debtor really saved.",
    },
    "3-vat-created": {
        "title": "Stage 3a — the VAT record the line items need",
        "doing": "A VAT record is created for the rate found on the document, named "
                 "from the extracted percentage, with the e-invoice category code.",
        "checking": "The rate comes from the image, not a default. A document with "
                    "two rates creates two records.",
    },
    "3-product-1": {
        "title": "Stage 3b — the first Product",
        "doing": "The SKU, description and price are written into a new Product. "
                 "Fakturama's price field here is gross, so the net unit price from "
                 "the image is converted using that line's own VAT rate.",
        "checking": "45.00 net x 1.19 = 53.55 gross. The conversion is arithmetic on "
                    "extracted values, and the result is read back from the field.",
    },
    "3-item-1": {
        "title": "Stage 3c — the first item line on the Order",
        "doing": "The Product is selected into the Order's items table and the "
                 "quantity from the image is written into the grid.",
        "checking": "This grid is a Nebula NatTable: it publishes no rows at all to "
                    "UI Automation, so the value is written through the cell editor "
                    "and then confirmed by reading the rendered grid.",
    },
    "3-product-2": {
        "title": "Stage 3d — the second Product",
        "doing": "The same conversion for the second line of the document.",
        "checking": "8.50 net x 1.19 = 10.12 gross, rounded half-up to the cent.",
    },
    "3-item-2": {
        "title": "Stage 3e — the second item line",
        "doing": "The second Product is placed on the Order with its quantity.",
        "checking": "Two lines now, in document order, each with the quantity from "
                    "the image.",
    },
    "4-order-save": {
        "title": "Stage 4 — check the totals against the document, then save",
        "doing": "Shipping is left free and the order-level discount at zero, then "
                 "Fakturama's own computed totals are compared with the totals "
                 "printed on the image. The Order is saved only if they agree.",
        "checking": "Net 265.00, VAT 50.35, gross 315.35 — these are Fakturama's "
                    "numbers, and they match the image. This is the arithmetic gate: "
                    "a mismatch stops the run instead of saving a wrong Order.",
    },
    "4-order-verify": {
        "title": "Stage 4b — read the saved Order back out of the database view",
        "doing": "Data > Documents is opened and filtered by the external reference, "
                 "and the row is read back from the list.",
        "checking": "The evidence is a row in Fakturama's own list, not the fact that "
                    "a Save button was clicked. State is 'open', total 315.35.",
    },
    "5-invoice": {
        "title": "Stage 5 — the linked Invoice",
        "doing": "An Invoice is created from the saved Order, so Fakturama copies the "
                 "lines, totals and reference across. The payment method is set and, "
                 "because the image says PAID, the payment is recorded with the date "
                 "and amount from the document.",
        "checking": "The reference and totals were carried over by the application. "
                    "An UNPAID document leaves the payment untouched — no invented "
                    "date, no invented amount.",
    },
    "5-invoice-verify": {
        "title": "Stage 5b — both documents confirmed, Order still open",
        "doing": "Data > Documents is read once more: the Invoice must be present and "
                 "paid, and the source Order must still be listed as open.",
        "checking": "Two rows, one reference. The Order staying 'open' after the "
                    "Invoice is saved is the specific condition the brief asks for.",
    },
}


# What to point at in each capture, as (text to find on screen, caption).
# Keyed by the slug the run gives its screenshots.
PLANS: Dict[str, List[tuple]] = {
    "1-order": [
        ("PO000001", "1.4  Document number proposed by Fakturama, left untouched"),
        ("Sep. 21, 2026", "1.5  Order date from the image, typed into a segmented date widget"),
        ("WEB-2026-0921-B04", "1.6  External reference from the image, written into Cust.Ref."),
        ("Net", "1.7  Price mode set to Net, because the document prints net unit prices"),
        ("With VAT", "1.7  VAT mode left as With VAT"),
    ],
    "2-payment-method": [
        ("Bank Transfer", "2.10.3  Name and Description set to the method extracted from the image"),
        ("Credit transfer", "2.10.4  The e-invoice payment code the brief maps Bank Transfer to"),
        ("Set as standard", "2.10.5  Deliberately NOT clicked: this order's method, not a new workspace default"),
        ("Discount", "2.10  Payment discount left at zero -- the document grants none"),
    ],
    "2-debtor-saved": [
        ("CUST000001", "2.6  Customer ID proposed by Fakturama, left unchanged"),
        ("Lindgren Buero AG", "2.6  Company name from the image's customer block"),
        ("LINDGREN-HAMBURG", "2.9  Alias name, as the brief specifies"),
        ("Bank Transfer", "2.10  Payment method -- populated because it was created BEFORE this editor was built"),
    ],
    "2-debtor": [
        ("Lindgren Buero AG", "2.13  Address block populated by Fakturama when the Debtor was selected"),
        ("Jonas Vogel", "2.13  Contact name carried across from the saved record"),
    ],
    "3-vat-created": [
        ("VAT 19%", "3.6  Name is 'VAT' followed by the percentage read off the document"),
        ("S (Standard rate)", "3.6  E-invoice VAT category code required by the brief"),
    ],
    "3-product-1": [
        ("LMP-DSK-07", "3.8  Item Number is the SKU extracted from the image"),
        ("LED Desk Lamp", "3.8  Name and Description are the item description"),
        ("53.55", "3.9  Gross price = 45.00 net x 1.19, because this field is gross"),
        ("VAT 19%", "3.9  The VAT record created at 3.6, selected by name"),
    ],
    "3-item-1": [
        ("LED Desk Lamp", "3.12  Product selected back into the Order's items table"),
        ("4.00", "3.13  Qty from the image, written through the cell editor and read back"),
        ("(19.0%)", "3.12  The line picked up the VAT record created at 3.6 -- note the items table has no SKU column"),
    ],
    "3-product-2": [
        ("CBL-USB-33", "3.8  Second item's SKU"),
        ("USB-C Cable 2m", "3.8  Name and Description"),
        ("10.12", "3.9  Gross = 8.50 net x 1.19, rounded half-up to the cent"),
    ],
    "3-item-2": [
        ("USB-C Cable 2m", "3.12  Second Product on the Order"),
        ("10.00", "3.13  Second line's Qty"),
        ("8.50", "3.13  U.Price, read back from the grid -- the '$' in front of it is recognised as an 's', which is why matching tolerates one stray character"),
    ],
    "4-order-save": [
        ("WEB-2026-0921-B04", "4.1  Reference still matches the source document"),
        ("Free shipping", "4.2  Shipping left free and the order discount at 0%, as the brief requires"),
        ("265.00", "4.3  Total net -- Fakturama's figure, equal to the document's printed net total"),
        ("50.35", "4.3  VAT total matches the document"),
        ("315.35", "4.3  Gross total matches, which is what permits the save at 4.4"),
    ],
    "4-order-verify": [
        ("Documents", "4.5  Data > Documents -- the list the saved Order is read back from"),
        ("WEB-2026-0921-B04", "4.5  The reference on the Order editor, which is what the list is filtered by"),
        ("PO000001", "4.5  The saved Order's row in the list", 3),
        ("open", "4.5  Its state is 'open'"),
        ("315.35", "4.5  The stored gross total on that row agrees with the document", 2),
    ],
    "5-invoice": [
        ("WEB-2026-0921-B04", "5.1  Cust.Ref. copied across by Fakturama when the Invoice was created from the Order"),
        ("265.00", "5.1  Net total carried over from the Order, not recomputed here"),
        ("315.35", "5.1  Gross total, likewise carried over", 2),
        ("Bank Transfer", "5.2  Payment method on the Invoice's payment row, matching the extracted one", 2),
        ("Sep. 25, 2026", "5.3  Payment date from the image -- recorded only because the document says PAID"),
        ("315.35", "5.4  Payment amount recorded against the Invoice", 1),
    ],
    "5-invoice-verify": [
        ("Documents", "5.5  Data > Documents -- the list both rows are read back from"),
        ("paid", "5.5  The Invoice row's state is 'paid', matching the document's PAID status", 2),
        ("315.35", "5.5  ...and its total is the document's gross total", 3),
        ("open", "5.5  The source Order is STILL 'open' -- the specific condition the brief asks for"),
        ("315.35", "5.5  ...carrying the same total, on the same reference", 4),
    ],
}


def main(directory: str) -> int:
    out_dir = os.path.join(directory, "annotated")
    os.makedirs(out_dir, exist_ok=True)
    report = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".png"):
            continue
        slug = name.split("-", 1)[1].rsplit(".", 1)[0] if "-" in name else name
        plan = PLANS.get(slug)
        if not plan:
            continue
        src = os.path.join(directory, name)
        dst = os.path.join(out_dir, name)
        missing = annotate(src, plan, dst, STAGES.get(slug))
        report[name] = missing
        state = "ok" if not missing else f"missing {missing}"
        print(f"  {name}: {state}")
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots"))
