# Fakturama Image-to-Cash Automation

Turns a single order image into a saved, verified **Order** and a linked **Invoice** inside
Fakturama — extracting the source data, resolving or creating the Debtor, payment method,
VAT rates and Products, and applying the extracted payment status. No hardcoded screen
coordinates, no assumption of a fixed window layout.

- **[DESIGN.md](DESIGN.md)** — the design document (Part 1 of the brief).
- **[docs/BRIEF-COVERAGE.md](docs/BRIEF-COVERAGE.md)** — every numbered step of the brief
  mapped to the code that implements it, including the three places where I deliberately
  did something other than the literal instruction, and why.
- **[docs/annotated-extraction.png](docs/annotated-extraction.png)** — what the parser
  actually anchored on, drawn over the source image.

---

## Status: read this first

| Part | State |
|---|---|
| Image extraction + validation | **Working and tested end to end** against the supplied order image |
| Test suite | **120 tests, all passing** (`python -m pytest`) |
| Fakturama UI automation | **Written in full, not yet executed against a live Fakturama** |

I could not install Fakturama on the machine this was built on. Its installer is an
install4j-wrapped MSI that *requires elevation*; the account is an administrator but UAC
prompts could not be answered in the environment I was working in. I verified this is a
hard requirement rather than a configuration mistake — the MSI carries `I4J.ELEVATION=1`,
an administrative install (`msiexec /a`) fails because the package has no Media table, and
the embedded payload is not an extractable archive.

So the honest summary: **everything up to and including the validation gate is proven;
the UI layer is complete and carefully written but has not had its first contact with the
real application.** Expect to spend time adapting `uia/selectors.py` on the first live run —
see [Adapting to your Fakturama build](#adapting-to-your-fakturama-build), which is exactly
the workflow that module and the `--dump-tree` command exist for. I would rather say this
plainly than imply a green run I have not seen.

---

## Setup

Requires **Windows 10/11** and **Python 3.9+** (built and tested on 3.14).

```powershell
git clone <this repo>
cd TJM
python -m pip install -r requirements.txt
```

No OCR engine to install: the default is `Windows.Media.Ocr`, which ships with Windows.
No API key, no network access.

Then install Fakturama — this raises **one UAC prompt, which you must accept**:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_fakturama.ps1
```

Launch Fakturama once by hand afterwards and complete its first-run setup (it creates its
database and asks for a workspace folder). The automation does not attempt to drive that
wizard.

## Running it

```powershell
# The full flow: image -> Order -> Invoice, verified at each step
python -m fakturama_automation --image samples\order_sample.png

# Just the image pipeline - no Fakturama needed. Start here.
python -m fakturama_automation --image samples\order_sample.png --extract-only

# Write Fakturama's UI Automation tree to artifacts\uia-tree.txt
python -m fakturama_automation --dump-tree
```

Useful flags: `--engine {windows,tesseract,auto}`, `--no-refine` (skip the magnified
per-cell re-read), `--fakturama <path>` (or set `FAKTURAMA_EXE`), `--close`, `--timeout`.

Every run writes to `artifacts/`: `run.log`, `extracted-order.json`, `run-summary.json`,
and a numbered screenshot per step.

**Exit codes:** `0` success · `10` stopped for manual review · `11` extraction failed ·
`12` automation failed · `2`/`3` bad input or Fakturama not found.

## Tests

```powershell
python -m pytest            # 120 tests, ~1s
```

- `test_extraction.py` runs the real OCR engine against `samples/order_sample.png` and
  asserts every extracted field against hand-written ground truth.
- `test_validation.py` is the interesting one: each test corrupts a single value the way
  OCR would — `250.00` read as `256.00`, a dropped discount, a wrong VAT rate — and asserts
  the run stops.
- `test_uia_logic.py` covers the decisions inside the UI layer that do not need a running
  Fakturama: caption normalisation, the label-anchoring geometry, numeric comparison,
  keystroke escaping, date-format inference, and the exact-match rule for selector rows.
- `test_normalize.py` / `test_models.py` cover value parsing and the derived prices the
  Fakturama flow writes.

---

## How it works

```
order image
   │
   ├─ 1. EXTRACT    label-anchored OCR; items table re-read cell by cell
   ├─ 2. VALIDATE   arithmetic + business gates  ──► stop for manual review
   │                                                  (nothing has touched Fakturama yet)
   ├─ 3. AUTOMATE   Order-first UIA flow, select-or-create for each record
   └─ 4. VERIFY     read the saved rows back out of Data > Documents
```

### Extraction grounds on labels, not coordinates

Fields are found by asking "what is underneath `ORDER DATE`?" A field's column is bounded
by the labels it shares a row with, so a wide value can never be captured by its neighbour.
The items table's columns are derived from its own header row. Nothing refers to a pixel
position.

`docs/annotated-extraction.png` shows exactly what the parser anchored on — blue for the
labels, green for the values read from them, orange for the derived table columns, purple
for the item rows. Regenerate it with:

```powershell
python tools\annotate_extraction.py samples\order_sample.png docs\annotated-extraction.png
```

### The items table gets a second, closer look

A full-page OCR pass optimises for the page as a whole and reliably drops small isolated
tokens. On the supplied image it loses the `0%` discount on line 2 and the `19%` VAT on
line 1 outright, and reads the `10%` discount as `1096`. Upscaling the whole page does not
fix this — different scales simply drop different cells.

So once the columns are known, each numeric cell is cropped to its own ink and re-read
magnified. Those reads are stable and identical across zoom levels. Windows OCR renders
`%` as `0/0`, which is unambiguous and decoded as such.

### The validation gate is the real safety net

The source document is deliberately redundant: it prints the line items **and** the totals
they add up to. Those must agree, so a single mis-read digit almost always breaks one of:

- per line: `line_net == qty × unit_net × (1 − discount%)`
- `net_total == Σ line_net` · `vat_total == Σ line_net × vat%` · `gross == net + vat`
- payment method maps to a known Fakturama payment code
- `PAID` requires a payment date; anything else must not have one
- no duplicate SKUs, no blank address fields, quantities positive, rates in range

This runs **before Fakturama is launched**. It converts OCR imprecision from a correctness
risk into an availability one: a bad read can stop the run, but it cannot write a
plausible-looking wrong invoice into an accounting system.

### Control discovery: a resolution ladder

Fakturama is Eclipse RCP/SWT, so its widgets are real native controls and UI Automation can
both drive *and read* them. Controls are resolved cheapest-first:

1. **UIA properties** — control type plus name or automation id.
2. **Label anchoring** — SWT text fields frequently expose no accessible name, but the
   visible label beside them is its own element. "The Edit nearest to the right of the Text
   reading `Cust.Ref.`" survives resizing, DPI changes and layout reflow. This is the same
   idea as the OCR parser, applied to a different surface.
3. **Keyboard** — focus plus Tab/F2 where the tree is too thin, always with a read-back.
4. **On-screen text** — last resort; still anchored on words, never on fixed pixels.

A few controls are unnamed icons — notably the pair beside **Addresses**, where the upper
one opens the existing-contact selector and the lower green `+` would start a new Debtor and
skip the existence check entirely. Those are addressed by their relationship to a named
anchor and their visual order among siblings (`flow/widgets.py:icons_near`).

### Every write is verified

There is no `sleep()` in the flow. Every step waits on a condition — a window exists, a
result list stopped changing, a control became enabled — and every value written is read
back and compared before the flow advances. Creating a Debtor or Product is only considered
done when the still-open Order can find and select it; a Save button click is not evidence.

### Stopping beats guessing

Ambiguity halts the run with a reason, the evidence, and a screenshot
(`ManualReviewRequired`). In an accounting system a confident wrong match — wrong debtor,
wrong VAT rate — costs far more than a paused run.

---

## Repository layout

```
fakturama_automation/
  cli.py                  entry point and flags
  config.py               settings; Fakturama discovery
  models.py               typed order document (Decimal money, derived prices)
  errors.py               ManualReviewRequired vs Extraction vs Automation failures
  extraction/
    ocr.py                engines (Windows / Tesseract) + CellReader
    geometry.py           boxes and spatial predicates
    parser.py             label-anchored parsing, items table, derivation fallbacks
    normalize.py          Decimal/percent/date parsing, OCR confusion repairs
    validation.py         the arithmetic and business gates
  uia/
    selectors.py          EVERY Fakturama UI caption, in one place
    locators.py           the resolution ladder
    actions.py            write-then-read-back interactions
    waits.py              condition-based waiting
    app.py                launch/attach, dialogs, UIA tree dump
    screenshots.py        per-step capture
  flow/
    runner.py             the whole flow in order
    context.py            step logging, evidence, manual-review stops
    order.py  debtor.py  payment_terms.py  vat.py  product.py  invoice.py  verify.py
    widgets.py            unnamed-icon and date-field helpers
tools/annotate_extraction.py      renders what the parser anchored on
scripts/install_fakturama.ps1     downloads and installs Fakturama (one UAC prompt)
samples/order_sample.png          the supplied order image
docs/annotated-extraction.png     the annotated extraction result
tests/                            120 tests
```

## Adapting to your Fakturama build

Every caption the automation depends on lives in `fakturama_automation/uia/selectors.py`,
as tuples of acceptable alternatives. The flow logic never hardcodes a string. To adapt:

```powershell
python -m fakturama_automation --dump-tree    # writes artifacts\uia-tree.txt
```

Then match the real names against `selectors.py` and edit that one file.

---

## What I skipped, and known limitations

Stated plainly, as the brief asks.

1. **The UI flow has never run against a live Fakturama** (see [Status](#status-read-this-first)).
   The selectors are derived from the brief's screenshots and from how Eclipse RCP/SWT
   applications expose themselves, not from a dumped tree of the real application. This is
   the single biggest gap.
2. **The items grid is the known technical risk.** Fakturama draws it with Nebula NatTable,
   a custom-painted SWT canvas that exposes far less to UI Automation than ordinary native
   controls. `flow/product.py` tries the direct cell control, falls back to activating the
   cell and typing, and then verifies by reading the row back — and stops for manual review
   if it cannot confirm. If anything in this project needs rework on first contact, it is
   this. I called this risk out in the design document before writing any code.
3. **A differing delivery address is flagged, not fully automated.** The brief spells out
   only the case where billing and delivery are identical (give the one Main address both
   roles). The sample document has a *different* delivery address, which genuinely needs a
   second address record. The run records a note rather than silently inventing a procedure
   the brief does not describe.
4. **The SKU correction is a heuristic.** `CHR-ERG-01` reads as `CHR-ERG-OI` in this font at
   every scale and preprocessing variant. It is resolved from a sibling row's digit pattern
   and is always reported in the run notes and `extracted-order.json`. A SKU that genuinely
   ended in letters, in a document whose other SKUs ended in digits, would be corrupted —
   the mitigation is that the correction is visible, never silent.
5. **Fakturama's first-run wizard is not automated.** Launch it once by hand first.
6. **Only the happy path and the documented stop conditions are covered.** No retry after a
   partial save, no resume of an interrupted run, no concurrent-instance handling.
7. **Tesseract is implemented but not exercised** — the binary could not be installed here,
   so `--engine tesseract` is untested.
8. **Only one document layout is supported.** The parser targets the TJM "SALES ORDER INPUT"
   family. Labels are configurable in code, but a structurally different document would need
   new field groups.

---

## Written question: what would I do with 3 more hours?

In priority order — the first item is most of it.

**1. Get Fakturama running and do the first live pass (~90 min).** Everything else is
speculation until the UI layer meets the real application. Concretely: install it, run
`--dump-tree`, reconcile `selectors.py` against the actual UIA tree, and walk the flow one
stage at a time with `--extract-only` off but stopping after the Order header. I expect the
unnamed icons beside **Addresses** and the NatTable items grid to need the most work, and I
would spike the grid first because it is the one place where the fallback might not be
enough. If NatTable proves genuinely opaque, the escape hatch is the keyboard path plus
OCR-on-screenshot for read-back — both already stubbed in.

**2. Make the run resumable and idempotent (~45 min).** Right now a failure halfway through
leaves a partially built Order and the next run starts from scratch, which on a second
attempt would try to create a Debtor that now exists. The select-or-create branches already
make most of this safe, but I would add a run ledger keyed on the external reference, check
for an existing Order with that `Cust.Ref.` before creating one, and make each stage skip
cleanly when its postcondition already holds.

**3. Read-only database verification as a second channel (~30 min).** Fakturama stores data
in HSQLDB. Verifying the saved Order and Invoice by querying the database — never writing to
it — would confirm the records independently of the UI that created them, catching the class
of bug where a value looks right on screen but is not what was persisted.

**4. Harden extraction against documents that are not this one (~30 min).** Generate variants
of the sample — rotated a few degrees, JPEG-compressed, lower resolution, a third line item,
a second VAT rate, an UNPAID status — and make them part of the test suite. The parser's
structure supports all of these; what I lack is evidence. I would also add the vision-LLM
extractor behind the existing engine interface and use it as a tie-breaker for low-confidence
cells, which is the clean answer to the `CHR-ERG-01` problem.

**5. The recording (~15 min).** A short screen capture of a full green run is a better
deliverable than the per-step screenshots, and only becomes possible once (1) is done.

If I had to cut to one thing: **(1)**. A design that has not met its target application is a
hypothesis, and the honest value of the remaining time is in testing it.
