"""Turn a page of OCR words into a typed :class:`OrderDocument`.

Nothing here refers to a pixel coordinate.  Fields are found by their printed
label ("what is underneath ``ORDER DATE``?") and table cells by the column
their header defines.  That is the same semantic-anchoring idea the UI layer
uses to find controls, and it is what lets the parser survive a document whose
layout shifts.

The items table gets a second, closer look: once the columns are known, every
numeric cell is re-read from a magnified crop (see
:class:`~.ocr.CellReader`), because a full-page pass reliably drops small
isolated tokens such as a ``0%`` discount.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from ..errors import ExtractionError
from ..models import (
    Address,
    Contact,
    LineItem,
    OrderDocument,
    PaidStatus,
    PaymentInfo,
)
from .geometry import Box, union_all
from .normalize import (
    clean_text,
    normalize_sku_family,
    parse_date,
    parse_decimal,
    parse_percent,
)
from .ocr import BaseOcrEngine, CellReader, OcrDocument, get_engine

PAD = 10


@dataclass
class ExtractionResult:
    """The parsed order plus a record of every judgement call made."""

    order: OrderDocument
    engine: str
    notes: List[str] = field(default_factory=list)
    sku_corrections: List[dict] = field(default_factory=list)

    @property
    def had_corrections(self) -> bool:
        return bool(self.notes or self.sku_corrections)


@dataclass
class _Column:
    key: str
    header: Box
    left: float = 0.0
    right: float = 0.0

    @property
    def center(self) -> float:
        return self.header.center_x


class OrderImageParser:
    """Parses the TJM 'SALES ORDER INPUT' document family."""

    def __init__(
        self,
        image_path: str,
        engine: Optional[BaseOcrEngine] = None,
        refine_cells: bool = True,
    ):
        self.image_path = image_path
        self.engine = engine or get_engine("windows")
        self.refine_cells = refine_cells
        self.notes: List[str] = []
        self._doc: Optional[OcrDocument] = None
        self._cells: Optional[CellReader] = None

    # ------------------------------------------------------------------ API

    def parse(self) -> ExtractionResult:
        doc = self.engine.recognize(self.image_path)
        self._doc = doc
        if self.refine_cells:
            self._cells = CellReader(self.image_path, self.engine)

        external_reference, order_date, customer_id, currency = self._parse_order_block()
        company, alias, contact = self._parse_customer_block()
        billing, delivery = self._parse_addresses()
        payment = self._parse_payment()
        items, sku_corrections = self._parse_items()
        net_total, vat_total, gross_total = self._parse_totals()

        order = OrderDocument(
            external_reference=external_reference,
            order_date=order_date,
            customer_id=customer_id,
            currency=currency,
            company=company,
            alias=alias,
            contact=contact,
            billing_address=billing,
            delivery_address=delivery,
            payment=payment,
            items=items,
            net_total=net_total,
            vat_total=vat_total,
            gross_total=gross_total,
        )
        return ExtractionResult(
            order=order,
            engine=doc.engine,
            notes=list(self.notes),
            sku_corrections=sku_corrections,
        )

    # ------------------------------------------------------- label plumbing

    @property
    def doc(self) -> OcrDocument:
        if self._doc is None:  # pragma: no cover - guarded by parse()
            raise ExtractionError("OCR has not run yet")
        return self._doc

    def _bounds(self, label: Box, siblings: Sequence[Box]) -> Tuple[float, float]:
        """Horizontal extent of the column a label owns.

        Bounded by the nearest sibling label on each side, so a wide value
        can never be stolen by the neighbouring field.
        """
        left = 0.0
        right = float(self.doc.width)
        for sib in siblings:
            if sib.right <= label.x:
                # Split the gap between the two labels, not hard against this
                # one: a value is often wider than the label that names it.
                left = max(left, (sib.right + label.x) / 2)
            elif sib.x >= label.right:
                right = min(right, (label.right + sib.x) / 2)
        return left, max(right, left + 1)

    def _lines_below(
        self,
        label: Box,
        siblings: Sequence[Box] = (),
        *,
        max_gap: float = 70.0,
        max_lines: int = 1,
    ) -> List[str]:
        left, right = self._bounds(label, siblings)
        region = Box(left, label.bottom + 1, right - left, max_gap)
        words = self.doc.words_in(region)
        rows = self.doc.lines(words)[:max_lines]
        return [clean_text(" ".join(w.text for w in row)) for row in rows]

    def _value_below(
        self,
        phrase: str,
        siblings: Sequence[Box] = (),
        *,
        max_gap: float = 70.0,
        required: bool = True,
    ) -> str:
        label = self.doc.find_phrase(phrase)
        if label is None:
            if required:
                raise ExtractionError(f"label {phrase!r} not found on the page")
            return ""
        lines = self._lines_below(label, siblings, max_gap=max_gap, max_lines=1)
        value = lines[0] if lines else ""
        if required and not value:
            raise ExtractionError(f"no value found beneath label {phrase!r}")
        return value

    def _label(self, phrase: str) -> Box:
        return self.doc.require_phrase(phrase)

    # ---------------------------------------------------------- the sections

    def _parse_order_block(self):
        ext = self._label("EXTERNAL REFERENCE")
        date_l = self._label("ORDER DATE")
        cust = self._label("CUSTOMER ID")
        curr = self._label("CURRENCY")
        family = [ext, date_l, cust, curr]

        external_reference = self._value_below("EXTERNAL REFERENCE", family)
        order_date = parse_date(
            self._value_below("ORDER DATE", family), field="order date"
        )
        customer_id = self._value_below("CUSTOMER ID", family)
        currency = self._value_below("CURRENCY", family)
        return external_reference, order_date, customer_id, currency

    def _parse_customer_block(self):
        company_l = self._label("COMPANY")
        alias_l = self._label("CUSTOMER ALIAS")
        name_l = self._label("CONTACT NAME")
        email_l = self._label("EMAIL")
        phone_l = self._label("PHONE")

        left_family = [name_l, email_l, phone_l]
        right_family = [company_l, alias_l]

        company = self._value_below("COMPANY", left_family)
        alias = self._value_below("CUSTOMER ALIAS", left_family)
        full_name = self._value_below("CONTACT NAME", right_family)
        email = self._value_below("EMAIL", right_family, required=False)
        phone = self._value_below("PHONE", right_family, required=False)

        first_name, last_name = self._split_name(full_name)
        return company, alias, Contact(first_name, last_name, email, phone)

    @staticmethod
    def _split_name(full_name: str) -> Tuple[str, str]:
        parts = clean_text(full_name).split()
        if not parts:
            return "", ""
        if len(parts) == 1:
            return "", parts[0]
        return " ".join(parts[:-1]), parts[-1]

    def _parse_addresses(self) -> Tuple[Address, Address]:
        billing_l = self._label("BILLING ADDRESS")
        delivery_l = self._label("DELIVERY ADDRESS")

        billing_lines = self._lines_below(
            billing_l, [delivery_l], max_gap=200, max_lines=6
        )
        delivery_lines = self._lines_below(
            delivery_l, [billing_l], max_gap=200, max_lines=6
        )
        return (
            self._address_from_lines(billing_lines, "billing"),
            self._address_from_lines(delivery_lines, "delivery"),
        )

    @staticmethod
    def _address_from_lines(lines: Sequence[str], which: str) -> Address:
        rows = [clean_text(line) for line in lines if clean_text(line)]
        if len(rows) < 3:
            raise ExtractionError(f"{which} address: expected at least 3 lines, got {rows!r}")

        zip_index = None
        zip_code = city = ""
        for idx, row in enumerate(rows[1:], start=1):
            match = re.match(r"^([A-Z]{0,3}[-\s]?\d{4,6})\s+(.+)$", row)
            if match:
                zip_index = idx
                zip_code, city = match.group(1).strip(), match.group(2).strip()
                break
        if zip_index is None:
            raise ExtractionError(f"{which} address: no 'ZIP City' line in {rows!r}")

        name = rows[0]
        street = " ".join(rows[1:zip_index]).strip()
        country = " ".join(rows[zip_index + 1:]).strip()
        if not street:
            raise ExtractionError(f"{which} address: no street line in {rows!r}")
        return Address(
            name=name, street=street, zip_code=zip_code, city=city, country=country
        )

    def _parse_payment(self) -> PaymentInfo:
        method_l = self._label("PAYMENT METHOD")
        status_l = self._label("PAID STATUS")
        date_l = self._label("PAYMENT DATE")
        family = [method_l, status_l, date_l]

        method = self._value_below("PAYMENT METHOD", family)
        status_raw = self._value_below("PAID STATUS", family)
        date_raw = self._value_below("PAYMENT DATE", family, required=False)

        try:
            status = PaidStatus.parse(status_raw)
        except ValueError as exc:
            raise ExtractionError(str(exc)) from exc

        payment_date: Optional[_dt.date] = None
        if status is PaidStatus.PAID:
            if not date_raw:
                raise ExtractionError("document is PAID but carries no payment date")
            payment_date = parse_date(date_raw, field="payment date")
        elif date_raw:
            # Never invent a payment; but do not throw away a printed date either.
            self.notes.append(
                f"payment date {date_raw!r} present while status is {status.value}; "
                "ignored, per the rule that an unpaid invoice gets no date or value"
            )
        return PaymentInfo(method=clean_text(method), status=status, payment_date=payment_date)

    def _parse_totals(self) -> Tuple[Decimal, Decimal, Decimal]:
        net_l = self._label("NET TOTAL")
        vat_l = self._label("VAT TOTAL")
        gross_l = self._label("GROSS TOTAL")
        family = [net_l, vat_l, gross_l]
        return (
            parse_decimal(self._value_below("NET TOTAL", family), field="net total"),
            parse_decimal(self._value_below("VAT TOTAL", family), field="vat total"),
            parse_decimal(self._value_below("GROSS TOTAL", family), field="gross total"),
        )

    # ----------------------------------------------------------- items table

    def _item_columns(self) -> List[_Column]:
        """Locate the table's columns from its header row."""
        doc = self.doc
        unit_net = doc.find_phrase("Unit net")
        line_net = doc.find_phrase("Line net")
        if unit_net is None or line_net is None:
            raise ExtractionError("items table: 'Unit net' / 'Line net' headers not found")

        # "Unit" appears twice: as its own column and as the start of "Unit net".
        unit_candidates = [
            b for b in doc.find_all_phrases("Unit")
            if not (abs(b.x - unit_net.x) < 4 and abs(b.y - unit_net.y) < 4)
        ]
        if not unit_candidates:
            raise ExtractionError("items table: 'Unit' column header not found")
        unit = min(unit_candidates, key=lambda b: b.x)

        # The '(EUR)' sub-headings sit under their column and widen it.
        for box in doc.find_all_phrases("(EUR)"):
            if abs(box.center_x - unit_net.center_x) < 60:
                unit_net = unit_net.union(box)
            elif abs(box.center_x - line_net.center_x) < 60:
                line_net = line_net.union(box)

        columns = [
            _Column("sku", self._label("SKU")),
            _Column("description", self._label("Description")),
            _Column("quantity", self._label("Qty")),
            _Column("unit", unit),
            _Column("unit_net", unit_net),
            _Column("discount", self._label("Disc")),
            _Column("vat", self._label("VAT")),
            _Column("line_net", line_net),
        ]
        columns.sort(key=lambda c: c.center)

        # Boundaries are the midpoints between neighbouring header centres.
        for idx, col in enumerate(columns):
            col.left = (
                0.0 if idx == 0
                else (columns[idx - 1].center + col.center) / 2
            )
            col.right = (
                float(self.doc.width) if idx == len(columns) - 1
                else (col.center + columns[idx + 1].center) / 2
            )
        return columns

    def _item_row_bands(self, columns: Sequence[_Column]) -> List[Box]:
        """Vertical bands holding one item row each."""
        header_bottom = max(c.header.bottom for c in columns)
        net_total = self._label("NET TOTAL")
        region = Box(
            0,
            header_bottom + 4,
            float(self.doc.width),
            net_total.y - header_bottom - 8,
        )
        rows = self.doc.lines(self.doc.words_in(region))

        bands: List[Box] = []
        sku_col = next(c for c in columns if c.key == "sku")
        for row in rows:
            box = union_all(w.box for w in row)
            if box is None:
                continue
            # A real item row has something in the SKU column.
            if not any(sku_col.left <= w.box.center_x <= sku_col.right for w in row):
                continue
            bands.append(box)
        return bands

    def _cell_text(self, column: _Column, band: Box, *, refine: bool) -> str:
        """Read one table cell, preferring a magnified re-read when asked."""
        cell = Box(column.left, band.y, column.right - column.left, band.height)
        page_words = self.doc.words_in(cell)
        page_text = clean_text(" ".join(w.text for w in page_words))
        if not refine or self._cells is None:
            return page_text

        # Magnify only the ink that is actually in this cell.  Enlarging the
        # whole column would drag in the first letters of the neighbouring
        # cell, which the crop's padding would then feed to the recogniser.
        target = cell
        if page_words:
            tight = union_all(w.box for w in page_words)
            if tight is not None:
                target = tight
        refined = clean_text(self._cells.read(target))
        return refined or page_text

    def _parse_items(self) -> Tuple[List[LineItem], List[dict]]:
        columns = self._item_columns()
        bands = self._item_row_bands(columns)
        if not bands:
            raise ExtractionError("items table: no item rows found")

        by_key = {c.key: c for c in columns}
        raw_rows: List[dict] = []

        for position, band in enumerate(bands, start=1):
            row: dict = {"position": position}
            for key in ("sku", "quantity", "unit_net", "discount", "vat", "line_net"):
                row[key] = self._cell_text(by_key[key], band, refine=True)
            for key in ("description", "unit"):
                row[key] = self._cell_text(by_key[key], band, refine=False)
            row["position"], row["sku"] = self._split_position_and_sku(
                row["sku"], position
            )
            raw_rows.append(row)

        skus = [r["sku"] for r in raw_rows]
        corrected, corrections = normalize_sku_family(skus)
        for row, sku in zip(raw_rows, corrected):
            row["sku"] = sku
        for corr in corrections:
            self.notes.append(
                f"SKU {corr['before']!r} read as {corr['after']!r}: a sibling row "
                "establishes an all-digit segment of that length, so the "
                "digit-lookalike characters were resolved to digits"
            )

        items = self._build_items(raw_rows)
        return items, corrections

    @staticmethod
    def _split_position_and_sku(cell: str, fallback: int) -> Tuple[int, str]:
        """Separate the row number from the SKU.

        The '#' column header is a single narrow glyph that OCR usually misses
        entirely, so no column can be derived for it and the row number lands
        in the SKU cell.  Rather than depend on which token comes first -- the
        order is not stable across crops -- pick the token *shaped* like an
        identifier (SKUs contain hyphens) and read any bare integer beside it
        as the row number.
        """
        tokens = clean_text(cell).split()
        if not tokens:
            return fallback, ""
        if len(tokens) == 1:
            return fallback, tokens[0]

        hyphenated = [t for t in tokens if "-" in t]
        sku = max(hyphenated or tokens, key=len)
        position = fallback
        for token in tokens:
            if token is not sku and token.isdigit():
                position = int(token)
                break
        return position, sku

    def _build_items(self, raw_rows: Sequence[dict]) -> List[LineItem]:
        items: List[LineItem] = []
        for row in raw_rows:
            sku = row["sku"]
            if not sku:
                raise ExtractionError(f"item row {row['position']}: no SKU could be read")

            quantity = parse_decimal(row["quantity"], field=f"{sku} quantity")
            unit_net = parse_decimal(row["unit_net"], field=f"{sku} unit net price")
            line_net = parse_decimal(row["line_net"], field=f"{sku} line total")

            discount = self._percent_or_derive(
                row["discount"],
                field=f"{sku} discount",
                derive=lambda: self._derive_discount(quantity, unit_net, line_net),
            )
            vat = self._percent_or_derive(
                row["vat"],
                field=f"{sku} VAT",
                derive=lambda: self._derive_vat(),
            )

            items.append(
                LineItem(
                    position=row["position"],
                    sku=sku,
                    description=clean_text(row["description"]),
                    quantity=quantity,
                    unit=clean_text(row["unit"]),
                    unit_net=unit_net,
                    discount_pct=discount,
                    vat_pct=vat,
                    line_net=line_net,
                )
            )
        return items

    def _percent_or_derive(self, raw: str, *, field: str, derive) -> Decimal:
        """Parse a percentage cell, falling back to the document's own arithmetic.

        The source document is redundant: a discount can be recovered from
        quantity, unit price and line total.  When OCR loses the cell entirely,
        recomputing is safer than guessing -- but it is always reported, never
        silent, and the value still has to survive validation afterwards.
        """
        try:
            return parse_percent(raw, field=field)
        except ExtractionError as exc:
            derived = derive()
            if derived is None:
                raise ExtractionError(
                    f"{field}: unreadable ({exc}) and not recoverable from the "
                    "document's other values"
                ) from exc
            self.notes.append(
                f"{field}: cell unreadable (OCR gave {raw!r}); derived "
                f"{derived}% from the document's own totals"
            )
            return derived

    @staticmethod
    def _derive_discount(
        quantity: Decimal, unit_net: Decimal, line_net: Decimal
    ) -> Optional[Decimal]:
        gross_line = quantity * unit_net
        if gross_line == 0:
            return None
        ratio = Decimal(1) - (line_net / gross_line)
        percent = (ratio * Decimal(100)).quantize(Decimal("0.01"))
        if percent < 0 or percent > 100:
            return None
        return percent.normalize()

    def _derive_vat(self) -> Optional[Decimal]:
        """Recover a missing VAT rate from the printed VAT and net totals."""
        try:
            net_l = self._label("NET TOTAL")
            vat_l = self._label("VAT TOTAL")
            gross_l = self._label("GROSS TOTAL")
            family = [net_l, vat_l, gross_l]
            net = parse_decimal(self._value_below("NET TOTAL", family), field="net total")
            vat = parse_decimal(self._value_below("VAT TOTAL", family), field="vat total")
        except ExtractionError:
            return None
        if net == 0:
            return None
        percent = (vat / net * Decimal(100)).quantize(Decimal("0.01"))
        if percent < 0 or percent > 100:
            return None
        return percent.normalize()


def extract_order(
    image_path: str,
    engine_name: str = "windows",
    refine_cells: bool = True,
) -> ExtractionResult:
    """Convenience wrapper: image path in, validated-ready result out."""
    return OrderImageParser(
        image_path, engine=get_engine(engine_name), refine_cells=refine_cells
    ).parse()
