# Run screenshots

One capture per completed stage of a real run, numbered in execution order.
`captions.txt` is the machine-written index the run itself produces.

**The annotated versions are in [`annotated/`](annotated/)**, and the walkthrough that
uses them is the *How the flow works* section of the main
[README](../../README.md#how-the-flow-works-annotated-screenshots). `annotated/report.json`
records any value the annotator could not locate on a screenshot, so a missing box is
visible rather than silently absent.

These are from `samples/order_no_discount.png`, which runs the brief end to end. A run on
the supplied `samples/order_sample.png` stops at step 3.15, because its first line carries
a 10% discount and this build of Fakturama has no Discount column — see the main README.

Regenerate with:

```powershell
python -m fakturama_automation --image samples\order_no_discount.png
python tools\annotate_run.py docs\screenshots
```
