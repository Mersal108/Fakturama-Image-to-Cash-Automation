"""Extraction against documents the parser has never seen, badly reproduced.

The supplied sample is one picture. A parser that handles only that picture
is indistinguishable from one that has memorised it, so these tests generate
fresh documents from a spec and then mistreat them the way a scanner or a
phone camera would -- tilted on the glass, re-compressed, scanned at a lower
resolution.

Each case below is one that *failed* before the extraction layer was
hardened, and the comment on it says what broke. They run the real OCR
engine, so they are slower than the rest of the suite and are skipped where
Windows OCR is unavailable.
"""

import json
import os
import shutil
import tempfile
from decimal import Decimal

import pytest

from fakturama_automation.extraction.parser import extract_order
from fakturama_automation.extraction.validation import validate_order

from .conftest import needs_ocr

sys_path_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SPECS = os.path.join(sys_path_root, "samples", "specs")


@pytest.fixture(scope="module")
def workdir():
    """A scratch directory of our own.

    pytest's tmp_path is not usable here: creating its root under the user's
    Temp directory is denied on this machine.
    """
    path = tempfile.mkdtemp(prefix="fakturama_degraded_")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _render(workdir, spec_name, name, **degradation):
    """Render a document from its spec, optionally mistreated."""
    import sys

    if sys_path_root not in sys.path:
        sys.path.insert(0, sys_path_root)
    from tools.make_sample import degrade, derive_totals, render

    with open(os.path.join(SPECS, spec_name), encoding="utf-8") as handle:
        spec = derive_totals(json.load(handle))
    image = degrade(render(spec), **degradation)
    path = os.path.join(workdir, f"{name}.png")
    image.save(path)
    return path, spec


def _assert_matches_spec(result, spec):
    """Everything the document says must survive the round trip."""
    order = result.order
    assert order.external_reference == spec["external_reference"]
    assert order.company == spec["company"]
    assert order.net_total == Decimal(str(spec["totals"]["net"]))
    assert order.gross_total == Decimal(str(spec["totals"]["gross"]))
    assert len(order.items) == len(spec["items"])
    for got, wanted in zip(order.items, spec["items"]):
        assert got.sku == wanted["sku"]
        assert got.quantity == Decimal(str(wanted["quantity"]))
        assert got.unit_net == Decimal(str(wanted["unit_net"]))
        assert got.discount_pct == Decimal(str(wanted.get("discount", 0)))
    # The arithmetic gate must also pass, not merely the field comparison.
    validate_order(order)


@needs_ocr
class TestUnseenDocuments:
    """Documents with a different shape to the supplied sample."""

    def test_a_second_customer_and_products(self, workdir):
        path, spec = _render(workdir, "no-discount.json", "plain")
        _assert_matches_spec(extract_order(path), spec)

    def test_three_lines_two_vat_rates_and_unpaid(self, workdir):
        """Also covers 'UNPAID means invent no payment date'."""
        path, spec = _render(workdir, "mixed-vat-unpaid.json", "mixed")
        result = extract_order(path)
        _assert_matches_spec(result, spec)
        assert result.order.payment.payment_date is None
        assert {i.vat_pct for i in result.order.items} == {Decimal(19), Decimal(7)}


@needs_ocr
class TestMistreatedDocuments:
    """The same documents, reproduced badly."""

    def test_a_page_scanned_crooked(self, workdir):
        """Tilt leaked neighbouring ink into every close-up cell read.

        A '0%' discount came back as '96', and '45.00' as '45.0C'. The page
        is now straightened first, using the angle the recogniser reports.
        """
        path, spec = _render(workdir, "no-discount.json", "rot", rotate=1.2)
        _assert_matches_spec(extract_order(path), spec)

    def test_a_heavily_compressed_scan(self, workdir):
        """JPEG noise welded 'NET TOTAL' into one word, so the label vanished.

        It also lost the space in 'EUR 315.35'.
        """
        path, spec = _render(workdir, "no-discount.json", "jpg", jpeg_quality=55)
        _assert_matches_spec(extract_order(path), spec)

    def test_a_low_resolution_scan(self, workdir):
        path, spec = _render(workdir, "no-discount.json", "small", scale=0.72)
        _assert_matches_spec(extract_order(path), spec)

    def test_crooked_and_compressed_together(self, workdir):
        path, spec = _render(workdir, "mixed-vat-unpaid.json", "combo",
                             rotate=-1.8, jpeg_quality=50)
        _assert_matches_spec(extract_order(path), spec)


@needs_ocr
class TestKnownLimit:
    """Where it genuinely stops working, recorded rather than hidden."""

    def test_a_very_low_resolution_scan_is_refused_not_guessed(self, workdir):
        """Below about 0.5x the page stops being readable at all.

        Measured: 0.55x still extracts correctly, 0.45x does not. The point
        of this test is not the exact threshold but that crossing it *fails
        loudly* -- the run raises rather than quietly returning a half-read
        document with plausible-looking numbers in it."""
        from fakturama_automation.errors import ExtractionError, ManualReviewRequired

        path, _spec = _render(workdir, "no-discount.json", "tiny", scale=0.4)
        with pytest.raises((ExtractionError, ManualReviewRequired)):
            order = extract_order(path).order
            validate_order(order)
