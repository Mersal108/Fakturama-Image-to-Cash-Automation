# Design Doc — Fakturama Image-to-Cash Automation

**Author:** Abdelrhman Mersal · **Date:** 2026-11-04

## 1. Goal and shape of the system

Turn a single order image into a saved, verified **Order** and linked **Invoice** inside Fakturama, creating any missing master data (Debtor, payment method, VAT rate, Products) along the way — with no hardcoded screen coordinates and no assumptions about a fixed window layout.

The system is a pipeline of three layers, each with a hard validation gate before the next one runs:

```
Order image ──► 1. EXTRACT ──► 2. VALIDATE ──► 3. AUTOMATE (UIA state machine) ──► 4. VERIFY
                OCR + parsing   typed model +     select-or-create flow against      read back saved
                                arithmetic checks  Fakturama's UI                     records via UIA
```

A core principle throughout: **never trust an action succeeded — read the result back.** Every write into the UI is followed by a read of the same control (or of the saved record list) before the flow advances. If any check fails or is ambiguous, the flow stops in a named `MANUAL_REVIEW` state with a screenshot and a reason, rather than guessing.

## 2. Image-extraction strategy

The input is a rendered/scanned order document with labeled fields and an items table.

**Primary: local OCR, anchored on field labels, not positions.**

1. **OCR with word geometry** — the engine must return a bounding box per word, not a text blob; everything below depends on that. Two engines sit behind one interface: **`Windows.Media.Ocr`**, built into Windows 10/11, is the default (no installer, no API key, no network, and fast); **Tesseract** is the alternative when the binary is available. The parser never knows which one ran.
2. **Preprocess where it pays** — upscaling, grayscale and thresholding help on genuinely noisy scans. On a crisp rendered document they mostly do not, so preprocessing is a per-call option rather than an unconditional step, and the magnified second pass below does the heavy lifting instead.
3. **Label-anchored field parsing** — find label tokens (`EXTERNAL REFERENCE`, `ORDER DATE`, `PAYMENT METHOD`, `PAID STATUS`, …) and take the value from the box *below/beside* the label. A field's column is bounded by the labels it shares a row with, so a wide value can never be stolen by its neighbour. This survives layout shifts because it keys on semantics, not pixel coordinates — the same grounding philosophy used later for the UI.
4. **Table extraction** — detect the items-table header row (`SKU`, `Description`, `Qty`, …) and derive column boundaries as the midpoints between header centres, then bucket each row's words into columns. Repeats naturally for any number of line items.
5. **A second, closer look at every numeric cell** — a full-page pass optimises for the page as a whole and reliably drops small isolated tokens; on the sample document it loses a `0%` discount and a `19%` VAT entirely. So once the columns are known, each numeric cell is cropped to its own ink and re-read magnified. Those reads are stable and repeatable where the full-page ones are not.
6. **Normalization** — dates → ISO, amounts → `Decimal` (never float for money, or rounding drift would fail the checks below), percentages → numbers. Two narrow, documented repairs: Windows OCR renders `%` as `0/0`, and in a field that must be numeric `O` is a zero and `I` a one. Both are applied only where the field type is already known.

**Why not an LLM as the primary extractor?** The brief allows OCR *and/or* LLMs. I chose deterministic local OCR as the primary path: it is reproducible, free, runs offline for whoever reviews the repo, and its failure modes are inspectable. The design keeps a clean seam — extraction returns one typed `OrderDocument` — so a vision-LLM extractor can be swapped in, or used as a *fallback for low-confidence fields*, without touching the automation layer. On messy real-world photos that hybrid is what I would ship; the validation gate below makes either extractor safe.

**Where OCR simply cannot win.** In this document's font, `CHR-ERG-01` reads as `CHR-ERG-OI` at every scale and under every preprocessing variant. No engine setting fixes it, so the design resolves it from the document's own structure: a document's SKUs share a shape, and when a sibling row has an all-digit segment of the same length, the digit-lookalike characters in the ambiguous one are digits too. The point is not the heuristic — it is that the correction is **reported, never silent**, so a reviewer sees exactly what was inferred and why.

**Validation gate (the real safety net).** Extraction output is parsed into a strict typed model (Pydantic) and must pass arithmetic cross-checks that the document itself provides:

- per line: `line_net == qty × unit_net × (1 − discount%)`
- `net_total == Σ line_net`, `vat_total == Σ line_net × vat%`, `gross == net + vat`
- enum checks: payment method ∈ known set, paid status ∈ {PAID, UNPAID}, currency = EUR

Because the totals are redundant with the line items, a single OCR misread (e.g., `250.00` → `256.00`) breaks an equation and is caught *before* anything touches Fakturama. On failure: stop for manual review with the offending field named. This turns "OCR is imperfect" from a correctness risk into an availability tradeoff.

## 3. Control-discovery / grounding strategy

Fakturama is an Eclipse-RCP/SWT desktop app. SWT wraps native Win32 widgets, so most controls are visible to **Microsoft UI Automation (UIA)**. I drive it with Python + `pywinauto` (UIA backend).

**How controls are located — a resolution ladder, never coordinates:**

1. **By UIA properties** — control type + name/automation-id within the Fakturama main window (e.g., toolbar button named "Order", dialog titled "Select the address").
2. **By label anchoring** — SWT text fields often have no accessible name; their visual label (`Date`, `Cust.Ref.`) is a sibling static text in the UIA tree. Resolve "the Edit nearest-after the Text element whose name is `Cust.Ref.`" — the same semantic-anchor idea as in OCR. Robust to layout/version changes as long as labels keep their names.
3. **Keyboard-first interaction** — where the tree is thin (Eclipse tabs, embedded grids), use focus + `Tab`/accelerators and type, then *verify* by reading the control's value pattern back. Keyboard paths are layout-independent by construction.
4. **Screenshot-OCR fallback (last resort)** — for any widget that exposes nothing (see risk below): locate on-screen text via OCR of the window, click at the found text's box. Still anchored on words, not fixed pixels.

**Synchronization.** No blind `sleep`s: every step waits on an explicit condition (window exists, list stabilized — two consecutive identical reads, control enabled) with timeout → `MANUAL_REVIEW`. Eclipse apps repaint lazily; condition-based waits are what makes the run deterministic.

**Known risk, planned for:** Fakturama's item/record grids use Nebula **NatTable**, a custom-drawn SWT canvas with weak UIA exposure. Mitigations, in order: (a) do data entry through the app's own selector dialogs (which the required flow already mandates) — those dialogs use standard tables; (b) keyboard navigation within the grid with read-back where possible; (c) the OCR-on-screenshot fallback for verification reads. This is the single biggest implementation unknown and the first thing to spike.

## 4. Orchestration: a select-or-create state machine

The flow is Order-first, exactly as the brief specifies, encoded as explicit states with postconditions:

1. **Open New Order**; set Date, Cust.Ref., Net price mode. *Postcondition: fields read back equal extracted values.*
2. **Debtor:** open the Order's address selector, search by company. Exact match on Company/First/Last/ZIP/City → select. Ambiguous → `MANUAL_REVIEW`. None → create Debtor (addresses, alias, Net mode, payment method — creating the payment method first if missing, with the brief's code mapping), save, **re-select from the Order's own selector** (that successful selection *is* the existence proof).
3. **Per item:** search exact SKU in the Order's product selector. Missing → ensure exact `VAT n%` exists (create if not), create Product (gross = net × (1+VAT), master price undiscounted), save, re-select from the Order. Then set Qty/U.Price/Discount on the line and assert `line price == qty × unit_net × (1 − disc)` against the *extracted* value — tying the UI state back to the source document, not just to itself.
4. **Save Order**, verify totals, then confirm the saved row in *Data ▸ Documents* (number, date, Cust.Ref., open state, total).
5. **Invoice via the Order's "Create follow-up document"** (preserves linkage), confirm copied fields, set payment method, apply paid status (date + full value only when source says PAID — never invented), save, and verify both rows in *Data ▸ Documents*: Invoice paid with correct total, Order still open.

Every state logs its action, the evidence it checked, and a screenshot — producing an audit trail that doubles as the deliverable recording.

## 5. Tradeoffs and alternatives considered

- **UIA vs. pure vision automation** (screenshot → model → click): vision is layout-agnostic but slow, costly, and unverifiable (you can't *read back* a value reliably). UIA gives machine-readable state both to act and to verify. Chosen: UIA primary, vision-style OCR only as a narrow fallback.
- **UI automation vs. writing to Fakturama's database directly** (it's HSQLDB): direct DB writes would be faster and fully reliable, but bypass the app's business logic (document numbering, linkage, recalculation) and the point of the exercise. Rejected — though the DB is a legitimate *read-only* channel for final verification if time allows.
- **Deterministic OCR vs. LLM extraction**: covered in §2 — deterministic primary, LLM as a drop-in behind the same interface; the arithmetic validation gate makes either safe. Within the deterministic choice, the built-in Windows engine beat Tesseract on practical grounds: nothing to install (Tesseract's installer needs administrator rights, which a reviewer may not have), and it returns the same per-word geometry the parser needs. Tesseract remains selectable with `--engine tesseract`.
- **Python/pywinauto vs. C#/FlaUI**: C# has the richest UIA bindings, but Python iterates faster inside a 5-hour box and keeps OCR, validation, and automation in one small, readable codebase. The grounding strategy is identical in either.
- **Stop-for-review vs. best-effort guessing**: the flow prefers halting with evidence over plausible-but-wrong records. In an accounting system, a false match (wrong debtor, wrong VAT) costs far more than a paused run.

**What gets cut first under time pressure** (and documented in the README): the OCR-screenshot fallback layer, multi-address edge cases, and polishing the recording — never the validation gates or read-back verification, because those are what make the happy path trustworthy.
