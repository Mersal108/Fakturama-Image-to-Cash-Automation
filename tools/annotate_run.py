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
PAD = 6


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


def _find(words, needle: str) -> Optional[Box]:
    """The box around a phrase, matched across consecutive words.

    Matching is tolerant of the confusions this project documents elsewhere --
    'CUST000001' is read off a screenshot as 'CUSTOOOOOI', and a leading '$'
    as an 'S'. Being strict here would simply mean no annotation appears over
    the very values most worth pointing at.
    """
    target = _key(needle)
    if not target:
        return None
    for start in range(len(words)):
        joined = ""
        box = None
        for word in words[start:start + 8]:
            joined += _key(word.text)
            box = word.box if box is None else box.union(word.box)
            if len(joined) == len(target) and _same(joined, target):
                return box
            # A currency mark read as a letter ('$250.00' -> 'S250.00')
            # leaves one extra character on the front.
            if (len(joined) == len(target) + 1
                    and _same(joined[1:], target)):
                return box
            if len(joined) > len(target):
                break
    return None


def annotate(image_path: str, targets: Sequence[Tuple[str, str]],
             out_path: str) -> List[str]:
    """Box each target's text and write a numbered legend beneath the image."""
    image = Image.open(image_path).convert("RGB")
    document = get_engine("windows", scale=2.0).recognize(image_path)
    words = list(document.words)

    found: List[Tuple[int, Box, str]] = []
    missing: List[str] = []
    for needle, caption in targets:
        box = _find(words, needle)
        if box is None:
            missing.append(needle)
            continue
        found.append((len(found) + 1, box, caption))

    legend_h = 44 + 30 * len(found)
    canvas = Image.new("RGB", (image.width, image.height + legend_h),
                       (255, 255, 255))
    canvas.paste(image, (0, 0))
    draw = ImageDraw.Draw(canvas)

    for number, box, _caption in found:
        x0, y0 = box.x - PAD, box.y - PAD
        x1, y1 = box.right + PAD, box.bottom + PAD
        draw.rectangle((x0, y0, x1, y1), outline=MARK, width=3)
        tag = str(number)
        tf = _font(20, bold=True)
        tw = draw.textlength(tag, font=tf)
        draw.rectangle((x0 - 1, y0 - 26, x0 + tw + 13, y0), fill=MARK_FILL)
        draw.text((x0 + 6, y0 - 24), tag, font=tf, fill=(255, 255, 255))

    draw.rectangle((0, image.height, canvas.width, canvas.height), fill=PANEL_BG)
    y = image.height + 14
    for number, _box, caption in found:
        draw.text((24, y), f"{number}", font=_font(19, bold=True), fill=MARK)
        draw.text((56, y), caption, font=_font(19), fill=PANEL_INK)
        y += 30

    canvas.save(out_path)
    return missing


# What to point at in each capture, as (text to find on screen, caption).
# Keyed by the slug the run gives its screenshots.
PLANS: Dict[str, List[Tuple[str, str]]] = {
    "1-order": [
        ("PO000001", "1.4  Document number proposed by Fakturama, left untouched"),
        ("Sep. 21, 2026", "1.5  Order date, typed into a segmented date widget"),
        ("WEB-2026-0921-B04", "1.6  External reference from the image, as Cust.Ref."),
        ("Net", "1.7  Price mode set to Net"),
        ("With VAT", "1.7  VAT mode left as With VAT"),
    ],
    "2-payment-method": [
        ("Bank Transfer", "2.10.3  Name and Description set to the extracted method"),
        ("Credit transfer", "2.10.4  The code the brief maps Bank Transfer to"),
        ("Set as standard", "2.10.5  Deliberately not clicked"),
    ],
    "2-debtor-saved": [
        ("CUST000001", "2.6  Customer ID proposed by Fakturama, left unchanged"),
        ("Lindgren Buero AG", "2.6  Company from the image"),
        ("LINDGREN-HAMBURG", "2.9  Alias name"),
        ("Bank Transfer", "2.10  Payment method, created before this editor opened"),
    ],
    "2-debtor": [
        ("Lindgren Buero AG", "2.13  Address block populated by selecting the Debtor"),
        ("Jonas Vogel", "2.13  Contact carried across from the saved record"),
    ],
    "3-vat-created": [
        ("VAT 19%", "3.6  Name is 'VAT' followed by the extracted percentage"),
        ("S (Standard rate)", "3.6  E-invoice VAT code required by the brief"),
    ],
    "3-product-1": [
        ("LMP-DSK-07", "3.8  Item Number is the extracted SKU"),
        ("LED Desk Lamp", "3.8  Name and Description are the item description"),
        ("53.55", "3.9  Gross = 45.00 x 1.19"),
    ],
    "3-item-1": [
        ("4.00", "3.13  Qty written into the NatTable grid and read back"),
        ("LED Desk Lamp", "3.12  Product selected back into the Order"),
    ],
    "3-product-2": [
        ("CBL-USB-33", "3.8  Second item's SKU"),
        ("USB-C Cable 2m", "3.8  Name and Description"),
        ("10.12", "3.9  Gross = 8.50 x 1.19"),
    ],
    "3-item-2": [
        ("10.00", "3.13  Second line's Qty"),
        ("USB-C Cable 2m", "3.12  Second Product on the Order"),
    ],
    "4-order-save": [
        ("WEB-2026-0921-B04", "4.1  Reference still matches the source document"),
        ("Free shipping", "4.2  Shipping left free, order discount left at 0%"),
        ("265.00", "4.3  Total net matches the document's printed net total"),
        ("315.35", "4.3  Gross total matches, so 4.4 may save"),
    ],
    "4-order-verify": [
        ("Documents", "4.5  The saved row, read back out of Data > Documents"),
        ("open", "4.5  The Order is in the 'open' state"),
    ],
    "5-invoice": [
        ("WEB-2026-0921-B04", "5.1  Cust.Ref. copied from the Order"),
        ("315.35", "5.1  Totals copied from the Order"),
        ("Bank Transfer", "5.2  Payment method matches the extracted one"),
    ],
    "5-invoice-verify": [
        ("Documents", "5.5  Both rows confirmed in Data > Documents"),
        ("open", "5.5  The source Order is still open"),
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
        missing = annotate(src, plan, dst)
        report[name] = missing
        state = "ok" if not missing else f"missing {missing}"
        print(f"  {name}: {state}")
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots"))
