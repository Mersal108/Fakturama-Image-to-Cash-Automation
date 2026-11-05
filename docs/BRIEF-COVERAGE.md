# Brief coverage map

Every numbered step of the task, and where it is implemented. Written so a reviewer can
check completeness without reading the whole codebase.

Legend — **done**: implemented and observed working against a live Fakturama 2.2.0 ·
**written**: implemented, but not yet reached by a live run · **blocked**: implemented and
reached, but this build cannot perform it — see the note · **note**: deliberate decision,
explained.

States were set from the run logs in `artifacts/run.log`, not from reading the code.

## 1. Extract the image and open a New Order

| Step | Where | State |
|---|---|---|
| 1.1 OCR / LLM extraction and normalisation | `extraction/ocr.py`, `extraction/normalize.py` | done |
| 1.2 Extract date, reference, debtor, addresses, payment, all item fields | `extraction/parser.py` | done |
| 1.3 Click **Order**, wait for the New Order editor | `flow/order.py::open_new_order` | done |
| 1.4 Leave the proposed **No.** unchanged | `flow/order.py::_read_number` — reads it for the record, never writes | done |
| 1.5 Set **Date** to the extracted order date | `flow/order.py::set_order_header` → `widgets.set_date_field` | done |
| 1.6 Enter the external reference in **Cust.Ref.** | `flow/order.py::set_order_header` | done |
| 1.7 Price mode **Net**, VAT **With VAT** | `flow/order.py::_set_price_mode` | done |
| 1.8 Keep the Order tab open throughout | `flow/runner.py` never closes it; every master-data detour returns to it | done |

## 2. Select or create the Debtor

| Step | Where | State |
|---|---|---|
| 2.1 Click the **upper** existing-contact icon, not the green `+` | `flow/debtor.py::open_address_selector` → `widgets.icons_near` (visual order, index 0) | done |
| 2.2 Search by company and wait for the list to stabilise | `flow/debtor.py::search_and_settle` → `waits.wait_for_stable` | done |
| 2.3 Exact match on Company / First / Name / ZIP / City; ambiguous → stop; none → Cancel and create | `flow/debtor.py::try_select_debtor` + `widgets.row_contains_all` | done |
| 2.4 Confirm the populated invoice and delivery addresses | `flow/debtor.py::confirm_addresses_populated` | done |
| 2.5 **New Contact** from the left panel, Order still open | `flow/debtor.py::create_debtor` | done |
| 2.6 Keep the proposed Customer ID; Company, First Name, Last Name; Salutation `---` | `create_debtor`, `_set_names` | done |
| 2.7 Main address: Street, ZIP, City, Country, E-Mail, Telephone | `_fill_main_address`, `_set_zip_city` | done |
| 2.8 Assign the Invoice role; add Delivery too when the addresses are identical | `_assign_address_roles` | **note** — the sample's delivery address *differs*, which needs a second address record; the brief describes only the identical case, so the run records a note instead of inventing a procedure |
| 2.9 Miscellaneous: Alias, Discount 0%, Net | `_fill_miscellaneous` | done |
| 2.10 Select the exact payment method, creating it if absent | `payment_terms.py::ensure_payment_term` (create) + `::ensure_payment_method` (select) | done — **note**: the creation runs *before* the Debtor editor is opened. SWT fills that combo when the editor is constructed, so a term created afterwards never appears in it; measured directly on this build. Same end state, order forced by the toolkit |
| 2.10.1 Open Data ▸ terms of payment and search | `create_payment_method` | done |
| 2.10.2 One exact → reuse; several → stop; none → `+` | `create_payment_method` | done |
| 2.10.3 Name and Description = method; Account blank | `create_payment_method` | done |
| 2.10.4 Payment-code mapping | `extraction/validation.py::PAYMENT_CODE_MAP` (tested) | done |
| 2.10.5 Cash discount / Discount Days / Net Days = 0; texts blank; not standard | `create_payment_method` | done |
| 2.10.6 Save once, return, select | `create_payment_method` + `ensure_payment_method` | done |
| 2.11 Save the Debtor once | `create_debtor` | done |
| 2.12 Back to the Order, reopen the selector, select the new Debtor | `resolve_debtor` → `try_select_debtor` | done |
| 2.13 Confirm the addresses populate — this *is* the proof the Debtor saved | `confirm_addresses_populated` | done |

## 3. Select or create each Product

| Step | Where | State |
|---|---|---|
| 3.1 Run the whole branch per item, in source order | `flow/product.py::resolve_items` | done |
| 3.2 Click the **upper** product icon, not the green `+` | `_open_product_selector` | done |
| 3.3 Exact SKU → select; conflict → stop; none → Cancel and create | `_try_select_product` | done |
| 3.4 Check Data ▸ VATs for the exact name before New product | `flow/vat.py::ensure_vat` | done |
| 3.5 Reuse only when name, value and e-invoice code all agree | `vat.py::_verify_existing` | written |
| 3.6 Create the VAT with `S (Standard rate)`; save once | `vat.py::_create_vat` | done |
| 3.7 Open New product only after the VAT exists | `product.py::_resolve_one` (ordering) | done |
| 3.8 Item Number = SKU; Name and Description = item description | `_create_product` | done |
| 3.9 Price (gross) = unit net × (1 + VAT), line discount excluded | `models.py::LineItem.product_gross_price` (tested) | done |
| 3.10 cost price 0.00, exact VAT, Stock 0.00, rest untouched | `_create_product`, `_select_product_vat` | done |
| 3.11 Save once | `_create_product` | done |
| 3.12 Reopen the selector and select the new Product; absent → stop | `_resolve_one` | done |
| 3.13 Qty | `_complete_line` → `_screen_cell_write` | done — the grid is a NatTable, so the cell is located by reading the header row and clicking it; NatTable then opens a real `Edit` to write into. Verified twice: in the editor, then again from the grid once the editor is dismissed (`line 1 Qty. = '2' (verified in the grid)`) |
| 3.14 U.Price and VAT | `_complete_line` → `_screen_cell_write` | done — as 3.13. VAT needs no write: selecting the Product carries its VAT rate into the line |
| 3.15 Line discount | `_complete_line` → `_set_line_discount` | **blocked** — this build's item grid has no Discount column at all (`Pos. │ Qty. │ Name │ VAT │ U.Price │ Price`), and *Use discount for each item* is already enabled. A zero discount is noted and the run continues; a non-zero one stops rather than save an Order whose totals disagree with the source. See the README's limitations |
| 3.16 Confirm line price = qty × unit net × (1 − discount) | `_confirm_line_total`, compared against the **extracted** value | written — reached only once a line's discount is settled, so 3.15 gates it on this sample. The grid does recalculate correctly: with Qty 2 and U.Price 250 written, the line Price read back 500.00 and the Order's Total Net 500.00 |
| 3.17 Repeat for every item | `resolve_items` | written |

## 4. Complete and save the Order

| Step | Where | State |
|---|---|---|
| 4.1 Confirm addresses and every line against the image | `confirm_addresses_populated` + `_confirm_line_total` | written |
| 4.2 Keep overall Discount 0% and Shipping free | `order.py::confirm_totals` | **note** — enforced by *verifying the outcome* rather than setting the inputs: the image supplies no order-level discount or shipping, so if either had been applied the gross total would no longer match and 4.3 stops the run |
| 4.3 Confirm Total Net, VAT and Total | `order.py::confirm_totals` | written |
| 4.4 Save once | `order.py::save_order` | written |
| 4.5 Confirm the Order row in Data ▸ Documents | `flow/verify.py::verify_order_row` | written |
| 4.6 Invoice from **Create a follow-up document**, not the toolbar | `flow/invoice.py::_follow_up_invoice_control` — explicitly refuses the toolbar fallback | written |
| 4.7 Wait for the linked New Invoice editor | `invoice.py::create_linked_invoice` | written |

## 5. Complete and verify the linked Invoice

| Step | Where | State |
|---|---|---|
| 5.1 Leave the proposed numbers/dates; confirm copied fields | `invoice.py::confirm_copied_fields` | written |
| 5.2 Payment method must equal the extracted one, else stop | `invoice.py::_confirm_payment_method` | written |
| 5.3 PAID → tick paid, set the payment date and the full total; otherwise invent nothing | `invoice.py::apply_payment`, `_ensure_unpaid` | written |
| 5.4 Save once | `invoice.py::save_invoice` | written |
| 5.5 Confirm the Invoice row and that the Order is still open | `verify.py::verify_invoice_row` | written |
| 5.6 Reopen the Invoice only if needed | — | **note** — not implemented; 5.5 already reads the *persisted* Documents list rather than the editor's in-memory state, so the values are confirmed from storage |
| 5.7 Create no Delivery, Correction or Dunning document | those actions are never invoked anywhere in `flow/` | done |

## Cross-cutting requirements

| Requirement | Where |
|---|---|
| No hardcoded coordinates, no fixed layout | `uia/locators.py` (resolution ladder), `flow/widgets.py` (relative icon order), `extraction/parser.py` (label anchoring) |
| Verify each step before the next | `uia/actions.py` — every write is read back; `flow/context.py` records the evidence |
| Stop for manual review on ambiguity | `errors.py::ManualReviewRequired`, raised via `RunContext.stop` with reason, evidence and a screenshot |
| Microsoft UIA / OCR / LLM | UIA via `pywinauto`; OCR via `Windows.Media.Ocr` (Tesseract selectable); LLM deliberately not required — see DESIGN.md §2 |
