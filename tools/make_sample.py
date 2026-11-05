"""Render a synthetic order document in the layout the parser expects.

The supplied `samples/order_sample.png` is the only document this project had
to work from, which makes it impossible to tell a parser that generalises from
one that has memorised a single picture.  This renders more of them from a
JSON spec, so the extraction layer can be tested against documents it has
never seen: a different number of item lines, a second VAT rate, an UNPAID
status, a line with no discount, and the same document photographed badly.

What matters here is not visual fidelity but *structure*.  The parser anchors
on label text and on the items-table header row, so a generated document is a
fair test exactly when it reproduces those anchors and the spatial
relationships between them:

* every field value is drawn **below** its label, inside the horizontal band
  its label owns, because `_value_below` bounds a field by the labels it
  shares a row with;
* the items table carries the full header vocabulary -- SKU, Description,
  Qty, Unit, Unit net, Disc., VAT, Line net -- with the `(EUR)` sub-headings
  under the two money columns, because column boundaries are derived as the
  midpoints between header centres;
* the item rows sit between that header and the `NET TOTAL` label, which is
  what bounds the row-band search.

Usage:

    python tools/make_sample.py specs/no-discount.json samples/order_no_discount.png
    python tools/make_sample.py spec.json out.png --rotate 1.5 --jpeg-quality 60

`--rotate`, `--jpeg-quality` and `--scale` degrade the finished render the way
a scan or a phone photograph would, so the same spec can produce both a clean
document and a mistreated one.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from decimal import Decimal
from typing import Dict, Optional, Sequence

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1489, 2048
MARGIN = 92

INK = (16, 42, 67)
INK_SOFT = (90, 105, 120)
ACCENT = (31, 110, 189)
BAR = (16, 42, 67)
PANEL = (233, 240, 249)
PANEL_EDGE = (214, 225, 238)
WHITE = (255, 255, 255)
ROW_ALT = (247, 250, 253)
PAID_BG = (223, 244, 230)
PAID_EDGE = (122, 191, 146)
PAID_INK = (24, 102, 56)

_FONT_DIRS = (r"C:\Windows\Fonts", "/usr/share/fonts/truetype/dejavu")
_REGULAR = ("segoeui.ttf", "arial.ttf", "calibri.ttf", "DejaVuSans.ttf")
_BOLD = ("segoeuib.ttf", "arialbd.ttf", "calibrib.ttf", "DejaVuSans-Bold.ttf")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for directory in _FONT_DIRS:
        for name in (_BOLD if bold else _REGULAR):
            path = os.path.join(directory, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _money(value) -> str:
    return f"{Decimal(str(value)):.2f}"


class _Canvas:
    def __init__(self) -> None:
        self.img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
        self.d = ImageDraw.Draw(self.img)

    def text(self, xy, s, font, fill=INK, anchor=None):
        self.d.text(xy, s, font=font, fill=fill, anchor=anchor)

    def label(self, xy, s, size=15):
        """A field label. Values are drawn below these; the parser keys on them."""
        self.d.text(xy, s.upper(), font=_font(size, bold=True), fill=INK_SOFT)

    def panel(self, box, fill=PANEL, edge=PANEL_EDGE, radius=10):
        self.d.rounded_rectangle(box, radius=radius, fill=fill, outline=edge, width=1)

    def section(self, y, title) -> int:
        self.d.text((MARGIN, y), title.upper(), font=_font(19, bold=True), fill=ACCENT)
        return y + 30


def _tracked(c: "_Canvas", xy, s: str, font, fill, tracking: float = 3.0,
             anchor: str = "ma") -> None:
    """Draw text with extra letter spacing, as the source document's table
    headers are set.

    This is not decoration.  Without tracking, Segoe UI Bold at header size
    kerns 'K' and 'U' close enough that Windows OCR reads 'SKU' as 'SW', and
    the parser anchors its whole items table on that caption.  Spacing the
    glyphs is what the original document does and what keeps them separable.
    """
    widths = [font.getlength(ch) for ch in s]
    total = sum(widths) + tracking * (len(s) - 1)
    x, y = xy
    if anchor.startswith("m"):
        x -= total / 2
    elif anchor.startswith("r"):
        x -= total
    for ch, w in zip(s, widths):
        c.d.text((x, y), ch, font=font, fill=fill)
        x += w + tracking


def _field(c: _Canvas, x: int, y: int, label: str, value: str,
           value_font: ImageFont.FreeTypeFont) -> None:
    """A label with its value directly beneath it -- the parser's core shape."""
    c.label((x, y), label)
    c.text((x, y + 26), value, value_font)


def render(spec: Dict) -> Image.Image:
    c = _Canvas()
    reg, bold = _font(22), _font(22, bold=True)
    small = _font(19)

    # --- top bar -----------------------------------------------------------
    c.d.rectangle((0, 0, WIDTH, 108), fill=BAR)
    c.text((MARGIN, 42), "TJM Labs  |  Automation Assessment",
           _font(25, bold=True), fill=WHITE)
    c.text((WIDTH - MARGIN, 44), "SYNTHETIC TEST DATA",
           _font(19, bold=True), fill=(170, 195, 220), anchor="ra")

    # --- title + paid badge ------------------------------------------------
    c.text((MARGIN, 158), "SALES ORDER INPUT", _font(52, bold=True))
    c.text((MARGIN, 232), "Source document - extract all transaction values "
                          "from this image", _font(20), fill=INK_SOFT)

    status = str(spec["payment"]["status"]).upper()
    badge_w = 180
    c.d.rounded_rectangle((WIDTH - MARGIN - badge_w, 160, WIDTH - MARGIN, 204),
                          radius=22,
                          fill=PAID_BG if status == "PAID" else (245, 238, 230),
                          outline=PAID_EDGE if status == "PAID" else (205, 170, 130))
    c.text((WIDTH - MARGIN - badge_w / 2, 182), status, _font(21, bold=True),
           fill=PAID_INK if status == "PAID" else (140, 85, 20), anchor="mm")

    # --- order -------------------------------------------------------------
    y = c.section(300, "Order")
    c.panel((MARGIN, y, WIDTH - MARGIN, y + 140))
    cols = [MARGIN + 28, MARGIN + 390, MARGIN + 700, MARGIN + 975]
    _field(c, cols[0], y + 28, "External Reference",
           spec["external_reference"], bold)
    _field(c, cols[1], y + 28, "Order Date", spec["order_date"], reg)
    _field(c, cols[2], y + 28, "Customer ID", spec["customer_id"], reg)
    _field(c, cols[3], y + 28, "Currency", spec.get("currency", "EUR"), bold)

    # --- customer and contact ----------------------------------------------
    y = c.section(y + 180, "Customer and Contact")
    c.panel((MARGIN, y, WIDTH - MARGIN, y + 232), fill=WHITE)
    c.d.line((MARGIN + 640, y + 24, MARGIN + 640, y + 208), fill=PANEL_EDGE)
    _field(c, MARGIN + 28, y + 24, "Company", spec["company"], bold)
    _field(c, MARGIN + 28, y + 118, "Customer Alias", spec["alias"], reg)
    _field(c, MARGIN + 672, y + 24, "Contact Name", spec["contact"]["name"], reg)
    _field(c, MARGIN + 672, y + 94, "Email", spec["contact"]["email"], reg)
    _field(c, MARGIN + 672, y + 164, "Phone", spec["contact"]["phone"], reg)

    # --- addresses ----------------------------------------------------------
    y = c.section(y + 272, "Addresses")
    c.panel((MARGIN, y, WIDTH - MARGIN, y + 272), fill=WHITE)
    c.d.line((MARGIN + 640, y + 24, MARGIN + 640, y + 248), fill=PANEL_EDGE)
    for x, key, label in ((MARGIN + 28, "billing", "Billing Address"),
                          (MARGIN + 672, "delivery", "Delivery Address")):
        c.label((x, y + 28), label)
        for i, line in enumerate(spec["addresses"][key]):
            c.text((x, y + 66 + i * 34), line, reg)

    # --- payment ------------------------------------------------------------
    y = c.section(y + 312, "Payment")
    c.panel((MARGIN, y, WIDTH - MARGIN, y + 140))
    _field(c, MARGIN + 28, y + 28, "Payment Method",
           spec["payment"]["method"], bold)
    _field(c, MARGIN + 560, y + 28, "Paid Status", status, bold)
    if spec["payment"].get("date"):
        _field(c, MARGIN + 870, y + 28, "Payment Date",
               spec["payment"]["date"], reg)
    else:
        c.label((MARGIN + 870, y + 28), "Payment Date")
        c.text((MARGIN + 870, y + 54), "-", reg, fill=INK_SOFT)

    # --- items --------------------------------------------------------------
    y = c.section(y + 180, "Items")
    items = spec["items"]
    head_h, row_h = 64, 76
    table_rows = max(len(items), 1) + 2          # blank filler rows, as in the original
    table = (MARGIN, y, WIDTH - MARGIN, y + head_h + table_rows * row_h)

    c.d.rectangle((table[0], table[1], table[2], table[1] + head_h), fill=BAR)
    # Column centres drive the parser's boundary maths, so they are explicit.
    centres = {
        "#": MARGIN + 25, "SKU": MARGIN + 150, "Description": MARGIN + 435,
        "Qty": MARGIN + 662, "Unit": MARGIN + 738, "Unit net": MARGIN + 855,
        "Disc.": MARGIN + 985, "VAT": MARGIN + 1077, "Line net": MARGIN + 1215,
    }
    hfont = _font(19, bold=True)
    for caption, cx in centres.items():
        if caption in ("Unit net", "Line net"):
            # The '(EUR)' sub-heading sits under its column and widens it.
            _tracked(c, (cx, y + 16), caption, hfont, WHITE)
            _tracked(c, (cx, y + 40), "(EUR)", _font(15, bold=True),
                     (190, 210, 230), tracking=1.0)
        else:
            _tracked(c, (cx, y + 23), caption, hfont, WHITE)

    ry = y + head_h
    for idx in range(table_rows):
        if idx % 2:
            c.d.rectangle((table[0], ry, table[2], ry + row_h), fill=ROW_ALT)
        if idx < len(items):
            it = items[idx]
            mid = ry + row_h / 2
            c.text((centres["#"], mid), str(idx + 1), reg, anchor="mm")
            c.text((centres["SKU"], mid), it["sku"], bold, anchor="mm")
            c.text((MARGIN + 250, mid), it["description"], reg, anchor="lm")
            c.text((centres["Qty"], mid), str(it["quantity"]), reg, anchor="mm")
            c.text((centres["Unit"], mid), it.get("unit", "pcs"), reg, anchor="mm")
            c.text((centres["Unit net"], mid), _money(it["unit_net"]), reg, anchor="mm")
            c.text((centres["Disc."], mid), f"{it.get('discount', 0)}%", reg, anchor="mm")
            c.text((centres["VAT"], mid), f"{it['vat']}%", reg, anchor="mm")
            c.text((centres["Line net"], mid), _money(it["line_net"]), bold, anchor="mm")
        ry += row_h

    c.d.rectangle(table, outline=PANEL_EDGE, width=1)
    for cx in (MARGIN + 50, MARGIN + 250, MARGIN + 620, MARGIN + 705,
               MARGIN + 775, MARGIN + 935, MARGIN + 1035, MARGIN + 1120):
        c.d.line((cx, table[1], cx, table[3]), fill=PANEL_EDGE)

    # --- totals -------------------------------------------------------------
    ty = table[3] + 40
    c.panel((MARGIN, ty, WIDTH - MARGIN, ty + 128))
    cur = spec.get("currency", "EUR")
    thirds = [MARGIN + 210, MARGIN + 633, MARGIN + 1058]
    for cx, label, value, colour in (
        (thirds[0], "Net Total", spec["totals"]["net"], INK),
        (thirds[1], "VAT Total", spec["totals"]["vat"], INK),
        (thirds[2], "Gross Total", spec["totals"]["gross"], ACCENT),
    ):
        c.d.text((cx, ty + 30), label.upper(), font=_font(16, bold=True),
                 fill=INK_SOFT if colour is INK else ACCENT, anchor="ma")
        c.d.text((cx, ty + 62), f"{cur} {_money(value)}",
                 font=_font(30, bold=True), fill=colour, anchor="ma")
    for cx in (MARGIN + 422, MARGIN + 845):
        c.d.line((cx, ty + 22, cx, ty + 106), fill=(205, 219, 234))

    # --- footer -------------------------------------------------------------
    fy = ty + 180
    c.d.line((MARGIN, fy, WIDTH - MARGIN, fy), fill=PANEL_EDGE)
    c.text((MARGIN, fy + 20),
           "All data is synthetic  |  Prices are net  |  Dates: YYYY-MM-DD  "
           f"|  Currency: {cur}", small, fill=INK_SOFT)
    c.text((WIDTH - MARGIN, fy + 22), spec["external_reference"], small,
           fill=INK_SOFT, anchor="ra")
    return c.img


def degrade(img: Image.Image, *, rotate: float = 0.0, scale: float = 1.0,
            jpeg_quality: Optional[int] = None) -> Image.Image:
    """Mistreat a clean render the way a scanner or a phone camera would."""
    if scale != 1.0:
        img = img.resize((max(1, int(img.width * scale)),
                          max(1, int(img.height * scale))), Image.LANCZOS)
    if rotate:
        img = img.rotate(rotate, resample=Image.BICUBIC, expand=True,
                         fillcolor=WHITE)
    if jpeg_quality is not None:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=jpeg_quality)
        buf.seek(0)
        img = Image.open(buf).convert("RGB")
    return img


def derive_totals(spec: Dict) -> Dict:
    """Fill in line and document totals from the item rows.

    The document must be internally consistent, because the validation gate
    cross-checks exactly these numbers -- a generator that got them wrong
    would produce documents that can never pass, which tests nothing.
    """
    net = Decimal("0")
    vat = Decimal("0")
    for item in spec["items"]:
        qty = Decimal(str(item["quantity"]))
        unit = Decimal(str(item["unit_net"]))
        disc = Decimal(str(item.get("discount", 0)))
        rate = Decimal(str(item["vat"]))
        line = (qty * unit * (1 - disc / 100)).quantize(Decimal("0.01"))
        item["line_net"] = line
        net += line
        vat += (line * rate / 100).quantize(Decimal("0.01"))
    spec["totals"] = {"net": net, "vat": vat, "gross": net + vat}
    return spec


def main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spec", help="JSON document spec")
    ap.add_argument("out", help="output image path")
    ap.add_argument("--rotate", type=float, default=0.0,
                    help="degrees, as a sheet fed in crooked")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="resample factor, e.g. 0.6 for a low-resolution scan")
    ap.add_argument("--jpeg-quality", type=int, default=None,
                    help="re-encode as JPEG at this quality")
    args = ap.parse_args(argv)

    with open(args.spec, encoding="utf-8") as handle:
        spec = derive_totals(json.load(handle))

    img = degrade(render(spec), rotate=args.rotate, scale=args.scale,
                  jpeg_quality=args.jpeg_quality)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    img.save(args.out)

    print(f"wrote {args.out}  ({img.width}x{img.height})")
    print(f"  net {spec['totals']['net']}  vat {spec['totals']['vat']}  "
          f"gross {spec['totals']['gross']}")
    for item in spec["items"]:
        print(f"  {item['sku']}: {item['quantity']} x {item['unit_net']} "
              f"- {item.get('discount', 0)}% = {item['line_net']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
