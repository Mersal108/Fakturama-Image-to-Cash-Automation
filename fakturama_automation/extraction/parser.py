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
import os
import tempfile
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
    looks_numeric,
    parse_percent,
    trim_decimal,
)
from ..logging_setup import get_logger
from .ocr import (
    BaseOcrEngine, CellReader, OcrDocument, get_engine,
)

log = get_logger("parse")

PAD = 10


def _discard_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


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
        self._escalated: set = set()
        self._fallback_docs: List[OcrDocument] = []
        self._temp_images: List[str] = []
        self.engine_name = getattr(self.engine, "name", "windows")

    # ------------------------------------------------------------------ API

    # A page tilted by less than this is left alone: straightening it would
    # resample every glyph for no gain.
    DESKEW_THRESHOLD_DEGREES = 0.3

    def parse(self) -> ExtractionResult:
        doc = self.engine.recognize(self.image_path)
        doc = self._deskew_if_crooked(doc)
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
        refine: bool = False,
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
        if refine and value:
            value = self._refine_value(label, siblings, value, max_gap=max_gap)
        return value

    def _refine_value(self, label: Box, siblings: Sequence[Box], value: str,
                      *, max_gap: float) -> str:
        """Take a second, closer look at an opaque identifier.

        Most fields are checked against something: the line totals against
        their quantities, the document totals against the lines, a SKU
        against the other SKUs' shape.  An external reference has nothing to
        check it against -- it is an arbitrary string, and a misread one is
        undetectable and gets written straight into Fakturama as the
        Cust.Ref.

        So the fields with no redundancy get the same magnified second read
        the numeric table cells get. Measured: 'WEB-2026-0921-B04' came off
        the page as '...-804', and reads correctly when magnified.
        """
        if self._cells is None:
            return value
        left, right = self._bounds(label, siblings)
        words = self.doc.words_in(
            Box(left, label.bottom + 1, right - left, max_gap))
        if not words:
            return value
        region = union_all(w.box for w in words)
        if region is None:
            return value
        closer = clean_text(self._cells.read(region))
        if not closer or closer == value:
            return value
        self.notes.append(
            f"value under {self.doc.text_in(label)!r} read as {value!r} from "
            f"the page and {closer!r} magnified; kept the magnified reading"
        )
        return closer

    def _deskew_if_crooked(self, doc: OcrDocument) -> OcrDocument:
        """Straighten a crooked scan and read it again.

        A page fed through a scanner at an angle breaks the parser in a way
        magnification cannot fix: the cell crops are axis-aligned, so a tilted
        row leaks its neighbours' ink into every close-up read.  On a sample
        tilted 1.2 degrees, a '0%' discount came back as '96'.

        The angle does not have to be guessed.  Windows OCR measures it while
        it reads and reports it, so the page is rotated back by exactly that
        and read once more.  Everything downstream -- including the per-cell
        re-reads -- then works from the straightened image.
        """
        angle = getattr(doc, "text_angle", 0.0) or 0.0
        if abs(angle) < self.DESKEW_THRESHOLD_DEGREES:
            return doc

        try:
            from PIL import Image

            with Image.open(self.image_path) as image:
                straight = image.convert("RGB").rotate(
                    angle, resample=Image.BICUBIC, expand=True,
                    fillcolor=(255, 255, 255),
                )
            handle, path = tempfile.mkstemp(suffix=".png", prefix="deskewed_")
            os.close(handle)
            straight.save(path)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not straighten the page: %s", exc)
            return doc

        try:
            rotated_doc = self.engine.recognize(path)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not re-read the straightened page: %s", exc)
            _discard_file(path)
            return doc

        self._temp_images.append(path)
        self.image_path = path
        self.notes.append(
            f"the page was tilted {angle:+.1f} degrees; it was straightened "
            "and read again before any value was taken from it"
        )
        log.info("straightened a page tilted %+.1f degrees", angle)
        return rotated_doc

    # Magnifications tried, in order, when a required label cannot be found
    # at the size the page was first read.
    PAGE_ESCALATIONS = (2.0, 3.0)

    def _label(self, phrase: str) -> Box:
        box = self._find_phrase(phrase)
        if box is not None:
            return box

        # A label the first pass missed is not necessarily absent.  A
        # compressed or slightly soft scan loses words at one size and reads
        # them cleanly at another -- 'NET TOTAL' arrived as the single word
        # 'NETTOTAL' from a JPEG of the sample, and 'SKU' went missing from a
        # crisp render, each at one magnification only.
        for scale in self.PAGE_ESCALATIONS:
            if scale in self._escalated:
                continue
            self._escalated.add(scale)
            if not self._merge_page_at(scale):
                continue
            box = self._find_phrase(phrase)
            if box is None:
                continue
            self.notes.append(
                f"label {phrase!r} was not legible at the size the page was "
                f"first read; found it by re-reading the page at {scale:g}x"
            )
            return box

        return self.doc.require_phrase(phrase)

    def _merge_page_at(self, scale: float) -> bool:
        """Re-read the whole page magnified, and keep it if it reads better.

        Not a merge of the two passes.  Compression and softness do not just
        drop words, they *join* them: a JPEG of the sample read 'NET TOTAL' as
        the single word 'NETTOTAL', and any overlap-based merge then discards
        the correctly-split reading as a duplicate of it.  Swapping the whole
        document avoids inventing a reading that neither pass actually saw.

        The previous document is kept, and a lookup falls back to it, so a
        word only the first pass saw is not lost either.
        """
        try:
            bigger = get_engine(self.engine_name, scale=scale).recognize(
                self.image_path)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not re-read the page at %sx: %s", scale, exc)
            return False
        if not bigger.words:
            return False
        self._fallback_docs.append(self.doc)
        self._doc = bigger
        log.debug("page re-read at %sx (%d words)", scale, len(bigger.words))
        return True

    def _find_phrase(self, phrase: str) -> Optional[Box]:
        """Look for a phrase in the current read, then in earlier ones."""
        for document in [self.doc] + list(reversed(self._fallback_docs)):
            try:
                return document.require_phrase(phrase)
            except ExtractionError:
                continue
        return None

    # ---------------------------------------------------------- the sections

    def _parse_order_block(self):
        ext = self._label("EXTERNAL REFERENCE")
        date_l = self._label("ORDER DATE")
        cust = self._label("CUSTOMER ID")
        curr = self._label("CURRENCY")
        family = [ext, date_l, cust, curr]

        external_reference = self._value_below("EXTERNAL REFERENCE", family,
                                               refine=True)
        order_date = parse_date(
            self._value_below("ORDER DATE", family), field="order date"
        )
        customer_id = self._value_below("CUSTOMER ID", family, refine=True)
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
        alias = self._value_below("CUSTOMER ALIAS", left_family, refine=True)
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

        # A first approximation: the midpoint between neighbouring header
        # centres.  Then moved to where the ink actually divides, below.
        for idx, col in enumerate(columns):
            col.left = (
                0.0 if idx == 0
                else (columns[idx - 1].center + col.center) / 2
            )
            col.right = (
                float(self.doc.width) if idx == len(columns) - 1
                else (col.center + columns[idx + 1].center) / 2
            )
        self._snap_bounds_to_ink(columns)
        return columns

    def _snap_bounds_to_ink(self, columns: List[_Column]) -> None:
        """Move each column boundary into the blank gap between the columns.

        A midpoint between two header centres only divides the columns when
        the values are centred under their headings, and in this table they
        are not: the Description column is left-aligned under a centred
        heading, so the midpoint sits well to the right of where the text
        begins.  A long first word survives that and a short one does not --
        'Ergonomic' was kept and 'LED' was swallowed by the SKU column, which
        is the kind of bug that only appears on a document the parser was not
        built against.

        A boundary is only moved when **both** neighbouring columns have text
        the page pass actually recognised.  That condition is the whole safety
        of this: a column whose value was not recognised looks like empty
        space, and an earlier version happily moved the boundary into the
        middle of it -- slicing the '%' off a '19%' that was there all along
        and turning a readable cell into an unreadable one.  No evidence means
        no adjustment.
        """
        body = self._table_body_words(columns)
        if not body:
            return

        # Which columns were read in *every* row.  A column the page pass read
        # in some rows but not others gives an incomplete picture of where its
        # content sits, and moving a boundary on that evidence narrowed the
        # VAT column onto rows whose '19%' had not been recognised -- clipping
        # the cell and turning a value that could be recovered by magnifying
        # into one that could not.
        rows = [r for r in self.doc.lines(body) if r]
        complete = []
        for col in columns:
            complete.append(all(
                any(col.left <= w.box.center_x < col.right for w in row)
                for row in rows
            ) if rows else False)

        for idx in range(len(columns) - 1):
            if not (complete[idx] and complete[idx + 1]):
                continue
            lo = columns[idx].center
            hi = columns[idx + 1].center

            # Every piece of recognised ink between the two headings, merged
            # into runs so that a gap means "nothing was read here".
            runs: List[List[float]] = []
            for word in sorted(body, key=lambda w: w.box.x):
                start_x = max(word.box.x, lo)
                end_x = min(word.box.right, hi)
                if end_x <= start_x:
                    continue
                if runs and start_x <= runs[-1][1] + 1.0:
                    runs[-1][1] = max(runs[-1][1], end_x)
                else:
                    runs.append([start_x, end_x])

            # Only a gap with ink on *both* sides is evidence of a boundary.
            # A column whose value the page pass failed to read looks like
            # empty space, and an earlier version moved the boundary into the
            # middle of one -- slicing the '%' off a '19%' that was there all
            # along, and turning a readable cell into an unreadable one.
            best_gap, boundary = 4.0, None
            for left_run, right_run in zip(runs, runs[1:]):
                gap = right_run[0] - left_run[1]
                if gap > best_gap:
                    best_gap = gap
                    boundary = (left_run[1] + right_run[0]) / 2
            if boundary is None:
                continue
            columns[idx].right = boundary
            columns[idx + 1].left = boundary

    def _table_body_words(self, columns: Sequence[_Column]):
        """Every word between the items header and the totals block."""
        try:
            header_bottom = max(c.header.bottom for c in columns)
            net_total = self._label("NET TOTAL")
        except ExtractionError:
            return []
        region = Box(0, header_bottom + 4, float(self.doc.width),
                     max(0.0, net_total.y - header_bottom - 8))
        return self.doc.words_in(region)

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

    def _cell_text(self, column: _Column, band: Box, *, refine: bool,
                   numeric: bool = False) -> str:
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
        if not refined:
            return page_text

        # A magnified re-read is meant to recover detail, not lose it.  When
        # the close read comes back with no digits at all but the page read
        # had some, the magnified pass has misrecognised the cell and the
        # page read is the better evidence.  Measured case: '0%' re-read as
        # 'olo', which the letter-to-digit repair would have turned into a
        # 10% discount on a line that had none.
        if (any(ch.isdigit() for ch in page_text)
                and not any(ch.isdigit() for ch in refined)):
            self.notes.append(
                f"cell at ({target.x:.0f},{target.y:.0f}): magnified re-read "
                f"gave {refined!r} with no digits; kept the full-page read "
                f"{page_text!r}"
            )
            return page_text

        # For a cell that must hold a number, a reading that cannot be one is
        # not the better reading.  On a page scanned slightly crooked the
        # close-up read '45.00' as '45.0C'; the whole-page pass had it right.
        if numeric and not looks_numeric(refined) and looks_numeric(page_text):
            self.notes.append(
                f"cell at ({target.x:.0f},{target.y:.0f}): magnified re-read "
                f"gave {refined!r}, which is not a number; kept the "
                f"full-page read {page_text!r}"
            )
            return page_text
        return refined

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
                row[key] = self._cell_text(by_key[key], band, refine=True,
                                           numeric=key != "sku")
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
        return trim_decimal(percent)

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
        return trim_decimal(percent)


def extract_order(
    image_path: str,
    engine_name: str = "windows",
    refine_cells: bool = True,
) -> ExtractionResult:
    """Convenience wrapper: image path in, validated-ready result out."""
    return OrderImageParser(
        image_path, engine=get_engine(engine_name), refine_cells=refine_cells
    ).parse()
