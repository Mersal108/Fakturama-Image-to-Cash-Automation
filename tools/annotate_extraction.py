"""Draw what the parser actually saw onto the order image.

Produces an annotated PNG showing the labels it anchored on, the values it
pulled from each, the columns it derived from the items-table header, and the
row bands it re-read at high magnification.  Useful as documentation and as a
first debugging step when a new document layout misbehaves.

    python tools/annotate_extraction.py samples/order_sample.png docs/annotated-extraction.png
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PIL import Image, ImageDraw, ImageFont

from fakturama_automation.extraction.geometry import Box
from fakturama_automation.extraction.parser import OrderImageParser

# Mirrors how parser.py groups labels: a field's column is bounded by the
# labels it shares a row or a panel with, never by every label on the page.
ORDER_BLOCK = ["EXTERNAL REFERENCE", "ORDER DATE", "CUSTOMER ID", "CURRENCY"]
CUSTOMER_LEFT = ["COMPANY", "CUSTOMER ALIAS"]
CUSTOMER_RIGHT = ["CONTACT NAME", "EMAIL", "PHONE"]
ADDRESSES = ["BILLING ADDRESS", "DELIVERY ADDRESS"]
PAYMENT_BLOCK = ["PAYMENT METHOD", "PAID STATUS", "PAYMENT DATE"]
TOTALS_BLOCK = ["NET TOTAL", "VAT TOTAL", "GROSS TOTAL"]

# (labels, sibling labels, how many lines the value may span)
FIELD_GROUPS = [
    (ORDER_BLOCK, ORDER_BLOCK, 1),
    (CUSTOMER_LEFT, CUSTOMER_RIGHT, 1),
    (CUSTOMER_RIGHT, CUSTOMER_LEFT, 1),
    (["BILLING ADDRESS"], ["DELIVERY ADDRESS"], 6),
    (["DELIVERY ADDRESS"], ["BILLING ADDRESS"], 6),
    (PAYMENT_BLOCK, PAYMENT_BLOCK, 1),
    (TOTALS_BLOCK, TOTALS_BLOCK, 1),
]

LABEL_COLOR = (21, 101, 192)      # blue   - the anchor
VALUE_COLOR = (46, 125, 50)       # green  - what was read from it
COLUMN_COLOR = (239, 108, 0)      # orange - table column boundaries
ROW_COLOR = (123, 31, 162)        # purple - item row bands


def font(size: int):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def rect(draw, box: Box, color, width=3, label="", label_font=None, above=True):
    draw.rectangle([box.x, box.y, box.right, box.bottom], outline=color, width=width)
    if label:
        ty = box.y - 20 if above else box.bottom + 4
        draw.text((box.x, max(0, ty)), label, fill=color, font=label_font)


def main(src: str, dest: str) -> int:
    parser = OrderImageParser(src)
    result = parser.parse()
    doc = parser.doc

    page = Image.open(src).convert("RGB")
    # Add a clean strip at the top so the legend never sits on the document.
    strip = 112
    image = Image.new("RGB", (page.width, page.height + strip), (255, 255, 255))
    image.paste(page, (0, strip))
    draw = ImageDraw.Draw(image)
    small, tiny = font(17), font(14)

    class _Offset:
        """Shift parser coordinates down by the legend strip."""

        @staticmethod
        def box(b: Box) -> Box:
            return Box(b.x, b.y + strip, b.width, b.height)

    shift = _Offset.box

    # 1. Every label the parser anchored on, and the value it took from it.
    from fakturama_automation.extraction.geometry import union_all

    for captions, sibling_captions, max_lines in FIELD_GROUPS:
        siblings = [b for c in sibling_captions
                    for b in [doc.find_phrase(c)] if b is not None]
        for caption in captions:
            label_box = doc.find_phrase(caption)
            if label_box is None:
                continue
            rect(draw, shift(label_box), LABEL_COLOR, width=2)

            family = siblings + [b for c in captions if c != caption
                                 for b in [doc.find_phrase(c)] if b is not None]
            left, right = parser._bounds(label_box, family)
            depth = 200 if max_lines > 1 else 70
            words = doc.words_in(
                Box(left, label_box.bottom + 1, right - left, depth)
            )
            for row in doc.lines(words)[:max_lines]:
                value_box = union_all(w.box for w in row)
                if value_box is not None:
                    rect(draw, shift(value_box), VALUE_COLOR, width=2)

    # 2. The item table's derived columns.
    columns = parser._item_columns()
    bands = parser._item_row_bands(columns)
    if bands:
        top = min(b.y for b in bands) - 70 + strip
        bottom = max(b.bottom for b in bands) + 16 + strip
        for column in columns:
            draw.line([(column.left, top), (column.left, bottom)],
                      fill=COLUMN_COLOR, width=2)
            draw.text((column.left + 4, top - 18), column.key,
                      fill=COLUMN_COLOR, font=tiny)
        draw.line([(columns[-1].right, top), (columns[-1].right, bottom)],
                  fill=COLUMN_COLOR, width=2)

    # 3. The row bands that were re-read cell by cell.
    for index, band in enumerate(bands, start=1):
        padded = shift(band).expanded(left=6, right=6, top=6, bottom=6)
        rect(draw, padded, ROW_COLOR, width=2,
             label=f"item row {index}", label_font=tiny)

    # 4. Legend, in the clean strip above the document.
    draw.text((12, 8), "What the parser anchored on", fill=(20, 20, 20),
              font=font(19))
    legend_y = 34
    for color, text in (
        (LABEL_COLOR, "label anchored on"),
        (VALUE_COLOR, "value read from that label"),
        (COLUMN_COLOR, "column derived from the table header"),
        (ROW_COLOR, "item row re-read at high magnification"),
    ):
        draw.rectangle([12, legend_y, 36, legend_y + 15], fill=color)
        draw.text((44, legend_y - 2), text, fill=(20, 20, 20), font=small)
        legend_y += 20

    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    image.save(dest)

    print(f"wrote {dest}")
    print(f"  engine      : {result.engine}")
    print(f"  item rows   : {len(result.order.items)}")
    print(f"  columns     : {', '.join(c.key for c in columns)}")
    for note in result.notes:
        print(f"  note        : {note}")
    return 0


if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else "samples/order_sample.png"
    target = sys.argv[2] if len(sys.argv) > 2 else "docs/annotated-extraction.png"
    sys.exit(main(source, target))
