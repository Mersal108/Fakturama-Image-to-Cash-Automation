# Run screenshots

One capture per completed stage of a real run, numbered in execution order.
`captions.txt` is the machine-written index the run itself produces.

**The annotated versions are in [`annotated/`](annotated/)**, and the walkthrough that
uses them is the *How the flow works* section of the main
[README](../../README.md#how-the-flow-works-annotated-screenshots).

Each annotated capture has three parts: a band above the screenshot naming the stage,
what the automation is doing and what to check; numbered boxes with leader lines around
every value written or read back; and a legend below mapping each number to the brief
step it satisfies.

The boxes are found by OCR over the screenshot, not drawn at fixed coordinates, so they
follow the application rather than assuming a layout. `annotated/report.json` records any
value the annotator could not locate — it is currently empty for all thirteen captures,
so every box on every image is one the annotator actually found.

These are from `samples/order_no_discount.png`, which runs the brief end to end. A run on
the supplied `samples/order_sample.png` stops at step 3.15 with exit code 10, because its
first line carries a 10% discount and this build of Fakturama has no Discount column —
see the main README.

Regenerate with:

```powershell
python -m fakturama_automation --image samples\order_no_discount.png
python tools\annotate_run.py docs\screenshots
```
