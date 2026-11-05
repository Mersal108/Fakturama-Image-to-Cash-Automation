"""Reading the controls that Microsoft UI Automation cannot see.

Two of Fakturama's widgets are drawn rather than built, and both are blind
spots in the same way:

* **Nebula NatTable** backs every grid -- the contact and product selectors,
  the list views, and the Order's item lines.  Such a table exposes *nothing*:
  zero children, no Grid pattern, no Table pattern.  Every row a person can
  plainly read is, to the accessibility tree, not there at all.
* **SWT CCombo** -- the Debtor's payment dropdown -- reports itself as an
  ordinary ComboBox and then paints its list into a borderless top-level
  shell that also publishes no children.

This is the last rung of the resolution ladder described in DESIGN.md: when a
control cannot be addressed through its properties, read the pixels.  Rows and
options are recovered by running the same OCR engine used for the order image
over a screenshot, then rebuilding columns from the header row -- the
identical label-anchoring technique the invoice parser uses, applied to a
screenshot instead of a scan.

Three things make this honest rather than a hack:

* Nothing here uses a fixed coordinate.  Column boundaries are derived from
  where the *headers* are drawn, and a row is clicked at the centre of the
  text that was actually recognised there.  Resize the window or change the
  font and the geometry is simply recomputed.
* No single reading is trusted to be complete.  Which captions survive depends
  on both the magnification and how much of the grid is in the picture, so the
  header is read several ways and the union is used -- see GRID_SCALES.
* A row read this way is still subject to the same exact-match rule as a row
  read through UIA.  OCR widens what can be *seen*; it does not loosen what
  counts as a match.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from ..extraction.geometry import Box
from ..extraction.ocr import BaseOcrEngine, OcrWord, get_engine
from ..logging_setup import get_logger
from .actions import bring_to_front
from .locators import rect_of, safe_name

log = get_logger("grid")

# Words closer than this horizontally belong to the same header caption.
# Measured on the Order's item grid, whose narrowest neighbours are the worst
# case: 'Pos.' ends at x=26 and 'Qty.' begins at x=46, so anything above ~19
# welds two columns into one caption, while the words *inside* a caption such
# as 'Item No.' or 'address type addit' sit 4-8px apart.  14 separates them
# with room on both sides.
HEADER_GAP = 14.0

# Different magnifications drop different tokens -- the same effect the
# order-image parser compensates for with its magnified second pass.  On the
# Order's item grid only x2 recognises 'Qty.', while x3 reads the body more
# reliably, so rows come from the first of these and the header from a union
# of every reading taken at all of them.
GRID_SCALES = (3.0, 2.0)
# Words closer than this belong to the same cell when there are no headers.
CELL_GAP = 34.0


@dataclass
class ScreenRow:
    """One visually-read row, clickable at the text it was read from."""

    cells: List[str]
    box: Box
    _origin: tuple = field(default=(0.0, 0.0), repr=False)

    @property
    def text(self) -> str:
        return " ".join(c for c in self.cells if c)

    def screen_point(self) -> tuple:
        ox, oy = self._origin
        return int(ox + self.box.center_x), int(oy + self.box.center_y)

    # The selection helpers accept anything that can be clicked.
    def click_input(self, **_kwargs) -> None:
        from pywinauto import mouse

        mouse.click(coords=self.screen_point())

    def select(self) -> None:
        self.click_input()

    def invoke(self) -> None:
        self.click_input()


# Column rules recognised as text.  Stripping these is safe in a way that
# stripping punctuation generally is not: a pipe glyph never carries meaning
# in a Fakturama cell, whereas '%', '$' and '-' all do.
_RULE_GLYPHS = "|│┃‖｜"


def _strip_rules(text: str) -> str:
    """Drop the column separators OCR reads as pipe characters."""
    return text.strip().strip(_RULE_GLYPHS).strip()


def _cluster(words: Sequence[OcrWord], gap: float) -> List[List[OcrWord]]:
    """Group words on one line into runs separated by more than ``gap``."""
    ordered = sorted(words, key=lambda w: w.box.x)
    groups: List[List[OcrWord]] = []
    for word in ordered:
        if groups and word.box.x - groups[-1][-1].box.right <= gap:
            groups[-1].append(word)
        else:
            groups.append([word])
    return groups


def _column_bounds(header: Sequence[OcrWord], width: float) -> List[tuple]:
    """Column edges, taken as the midpoints between header captions."""
    clusters = _cluster(header, HEADER_GAP)
    if not clusters:
        return []
    centres = [
        (min(w.box.x for w in c) + max(w.box.right for w in c)) / 2 for c in clusters
    ]
    bounds: List[tuple] = []
    for index, centre in enumerate(centres):
        left = 0.0 if index == 0 else (centres[index - 1] + centre) / 2
        right = width if index == len(centres) - 1 else (centres[index + 1] + centre) / 2
        bounds.append((left, right))
    return bounds


def rows_from_lines(
    lines: Sequence[Sequence[OcrWord]],
    width: float,
    *,
    origin: tuple = (0.0, 0.0),
    has_header: bool = True,
) -> List[ScreenRow]:
    """Turn recognised lines of text into grid rows.

    Split out from the screen capture so the part that can be reasoned about
    -- which line is the header, which column a word falls in, which lines are
    not rows at all -- can be tested without a running Fakturama.
    """
    lines = list(lines)
    if not lines:
        return []

    bounds: List[tuple] = []
    body = lines
    if has_header:
        # Line 0 is the header whether or not anything follows it.  Guarding
        # this on "more than one line" would make an *empty* grid report its
        # own column headings as a data row, and a search that found nothing
        # would come back claiming one candidate.
        bounds = _column_bounds(lines[0], width)
        body = lines[1:]
        log.debug("grid headers: %r", [w.text for w in lines[0]])

    rows: List[ScreenRow] = []
    for line in body:
        if not line:
            continue
        if bounds:
            cells = []
            for left, right in bounds:
                inside = [w for w in line if left <= w.box.center_x < right]
                cells.append(_strip_rules(" ".join(
                    w.text for w in sorted(inside, key=lambda w: w.box.x))))
        else:
            cells = [
                _strip_rules(" ".join(w.text for w in group))
                for group in _cluster(line, CELL_GAP)
            ]
        # Drop lines that carry no real content: a NatTable's gridlines and
        # partially-scrolled rows produce lines of stray punctuation.
        if not any(any(ch.isalnum() for ch in c) for c in cells):
            continue
        box = Box(
            min(w.box.x for w in line),
            min(w.box.y for w in line),
            max(w.box.right for w in line) - min(w.box.x for w in line),
            max(w.box.bottom for w in line) - min(w.box.y for w in line),
        )
        rows.append(ScreenRow(cells=cells, box=box, _origin=origin))
    return rows


def read_screen_grid(
    pane,
    *,
    engine: Optional[BaseOcrEngine] = None,
    engine_name: str = "windows",
    has_header: bool = True,
    raise_window: bool = True,
) -> List[ScreenRow]:
    """Recover the visible rows of a custom-painted grid by reading its pixels.

    ``raise_window`` is turned off for transient shells such as a dropdown
    popup: those are already the topmost window, and trying to raise one
    dismisses it, which would capture the list that was being read.
    """
    if raise_window and not bring_to_front(pane):
        log.debug("could not raise the window; a screen read would capture "
                  "whatever is covering it, so skipping")
        return []

    rect = rect_of(pane)
    if rect.width < 20 or rect.height < 20:
        return []

    # Grid text is small -- roughly 11px glyphs -- and Windows OCR drops short
    # tokens at that size.  Magnifying before recognition is the same trick the
    # order-image parser uses for its per-cell second pass.
    engine = engine or get_engine(engine_name, scale=3.0)
    path = os.path.join(tempfile.gettempdir(), "fakturama-grid.png")
    try:
        pane.capture_as_image().save(path)
        document = engine.recognize(path)
    except Exception as exc:  # noqa: BLE001
        log.debug("could not read the grid from screen: %s", exc)
        return []
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    rows = rows_from_lines(
        document.lines(), float(document.width),
        origin=(rect.left, rect.top), has_header=has_header,
    )
    log.info("read %d row(s) from the screen (%s)", len(rows), document.engine)
    return rows


@dataclass
class GridGeometry:
    """Where a painted grid's headers and rows actually are, in pane pixels."""

    pane: object
    origin: tuple
    headers: List[tuple]          # (caption, Box)
    rows: List[Box]
    width: float
    height: float
    words: List[OcrWord] = field(default_factory=list)

    def column(self, names: Sequence[str]):
        """The header Box whose caption matches one of ``names``."""
        from ..extraction.normalize import ocr_equivalent

        for caption, box in self.headers:
            cleaned = caption.strip().rstrip(".").casefold()
            for name in names:
                wanted = name.strip().rstrip(".").casefold()
                if cleaned == wanted or ocr_equivalent(cleaned, wanted):
                    return box
        return None

    def column_span(self, names: Sequence[str]):
        """Left and right edges of a column, midway to its neighbours."""
        box = self.column(names)
        if box is None:
            return None
        centres = sorted(b.x for _c, b in self.headers)
        starts = [c for c in centres if c <= box.x]
        after = [c for c in centres if c > box.x]
        left = box.x - 4 if len(starts) <= 1 else box.x - 4
        right = after[0] - 4 if after else self.width
        return left, right

    def cell_text(self, row_index: int, names: Sequence[str]) -> str:
        """The text drawn in one cell, read from the recognised words."""
        span = self.column_span(names)
        if span is None or row_index >= len(self.rows):
            return ""
        left, right = span
        row = self.rows[row_index]
        picked = [
            w for w in self.words
            if left <= w.box.center_x < right
            and row.y - 2 <= w.box.center_y <= row.bottom + 2
        ]
        picked.sort(key=lambda w: w.box.x)
        return _strip_rules(" ".join(w.text for w in picked))

    def cell_point(self, row_index: int, names: Sequence[str]):
        """Screen coordinates of one cell, derived from header and row text."""
        box = self.column(names)
        if box is None or row_index >= len(self.rows):
            return None
        row = self.rows[row_index]
        ox, oy = self.origin
        # A few pixels inside the column's left edge: header captions are
        # drawn left-aligned at the start of their column, while the values
        # beneath them are often right-aligned, so the caption's own position
        # is the dependable one.
        return int(ox + box.x + 6), int(oy + row.center_y)


def _discard(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _merge_header_words(
    candidates: Sequence[Sequence[OcrWord]],
) -> List[OcrWord]:
    """Combine header readings taken at different magnifications.

    No single magnification reads every caption: on the Order's item grid x2
    is the only one that recognises 'Qty.', while x3 reads 'Pos.' with its
    capital intact.  Since every reading reports boxes in the pane's own
    pixels, a word seen by one pass and missed by another can simply be added,
    and a word seen by both is kept once.

    Two readings describe the same word when their boxes overlap by more than
    half the narrower one -- position, not text, decides, because the whole
    point is that the text may differ.  Of two readings of one word the longer
    wins, and a capitalised form beats an uncapitalised one, which is how
    'Pos.' survives alongside a pass that read it as 'pos.'.
    """
    merged: List[OcrWord] = []
    for reading in candidates:
        for word in reading:
            for index, kept in enumerate(merged):
                overlap = (min(word.box.right, kept.box.right)
                           - max(word.box.x, kept.box.x))
                narrower = min(word.box.width, kept.box.width)
                if narrower <= 0 or overlap <= narrower / 2:
                    continue
                better = (len(word.text) > len(kept.text)
                          or (len(word.text) == len(kept.text)
                              and word.text[:1].isupper()
                              and not kept.text[:1].isupper()))
                if better:
                    merged[index] = word
                break
            else:
                merged.append(word)
    merged.sort(key=lambda w: w.box.x)
    return merged


def read_grid_geometry(
    pane,
    *,
    engine: Optional[BaseOcrEngine] = None,
    engine_name: str = "windows",
) -> Optional[GridGeometry]:
    """Read a painted grid's header and row geometry from its pixels."""
    if not bring_to_front(pane):
        return None
    rect = rect_of(pane)
    if rect.width < 20 or rect.height < 20:
        return None

    path = os.path.join(tempfile.gettempdir(), "fakturama-grid-geom.png")
    strip_path = os.path.join(tempfile.gettempdir(), "fakturama-grid-head.png")
    engines = ([engine] if engine is not None
               else [get_engine(engine_name, scale=s) for s in GRID_SCALES])
    try:
        # One screenshot, read several ways: capturing twice could catch the
        # grid mid-repaint and produce two different grids.
        image = pane.capture_as_image()
        image.save(path)
        document = engines[0].recognize(path)
    except Exception as exc:  # noqa: BLE001
        log.debug("could not read the grid geometry: %s", exc)
        _discard(path)
        return None

    lines = document.lines()
    if not lines:
        _discard(path)
        return None

    # The header is read again from a strip containing nothing but the header,
    # cropped to the header's own measured depth rather than a guessed one.
    # Isolating it matters: a selected row or an open cell editor changes the
    # picture enough that a caption read a moment ago goes missing, and the
    # captions are what every column boundary is derived from.
    header_words = lines[0]
    try:
        # Both the depth of the crop and the magnification change which
        # captions survive, and no single combination reads them all: on the
        # Order's item grid 'Qty.' appears only in a tight crop read at x2,
        # while 'Pos.' keeps its capital only at x3.  Each band is measured
        # from the header's own depth rather than guessed, and the union of
        # every reading is what the columns are then derived from.
        depth = max(w.box.bottom for w in lines[0])
        readings = [lines[0]]
        for band in (depth + 6.0, depth * 2.0):
            height = int(min(max(band, 8.0), image.height))
            image.crop((0, 0, image.width, height)).save(strip_path)
            for reader in engines:
                try:
                    strip = reader.recognize(strip_path)
                except Exception as exc:  # noqa: BLE001
                    log.debug("header strip read failed: %s", exc)
                    continue
                if strip.lines():
                    readings.append(strip.lines()[0])
        header_words = _merge_header_words(readings)
    except Exception as exc:  # noqa: BLE001
        log.debug("could not isolate the header strip: %s", exc)
    finally:
        _discard(strip_path)
    _discard(path)

    headers = []
    for group in _cluster(header_words, HEADER_GAP):
        caption = " ".join(w.text for w in group).strip()
        box = Box(
            min(w.box.x for w in group),
            min(w.box.y for w in group),
            max(w.box.right for w in group) - min(w.box.x for w in group),
            max(w.box.bottom for w in group) - min(w.box.y for w in group),
        )
        if caption:
            headers.append((caption, box))

    rows = []
    for line in lines[1:]:
        if not any(any(ch.isalnum() for ch in w.text) for w in line):
            continue
        rows.append(Box(
            min(w.box.x for w in line),
            min(w.box.y for w in line),
            max(w.box.right for w in line) - min(w.box.x for w in line),
            max(w.box.bottom for w in line) - min(w.box.y for w in line),
        ))

    log.debug("grid geometry: headers=%r, %d row(s)",
              [h for h, _b in headers], len(rows))
    return GridGeometry(
        pane=pane, origin=(rect.left, rect.top), headers=headers, rows=rows,
        width=float(document.width), height=float(document.height),
        words=list(document.words),
    )


# ------------------------------------------------------- dropdown popups


def popup_shell(control):
    """The dropdown shell an expanded combo opens, when it has one.

    Fakturama's payment-method combo is an SWT ``CCombo``: it draws its list
    into a borderless top-level shell that publishes *no children at all* to
    UI Automation -- the same custom-painted blind spot as NatTable, in a
    control that otherwise reports itself as an ordinary ComboBox.

    The shell carries no name and no automation id, so it is identified by the
    only thing that distinguishes it: it is an unnamed top-level pane sharing
    the combo's left edge and sitting immediately above or below it.  A list
    that would run off the bottom of the screen opens upwards, so both are
    accepted.
    """
    from pywinauto import Desktop

    rect = rect_of(control)
    for window in Desktop(backend="uia").windows():
        try:
            if (window.element_info.control_type or "") != "Pane":
                continue
            if safe_name(window):
                continue
            shell = rect_of(window)
        except Exception:  # noqa: BLE001
            continue
        if abs(shell.left - rect.left) > 40:
            continue
        below = rect.bottom - 6 <= shell.top <= rect.bottom + 10
        above = rect.top - 10 <= shell.bottom <= rect.top + 6
        if not (below or above):
            continue
        if shell.width < 20 or shell.height < 8:
            continue
        return window
    return None


def read_popup_rows(
    control,
    *,
    engine: Optional[BaseOcrEngine] = None,
    engine_name: str = "windows",
) -> List[ScreenRow]:
    """The options of an *already expanded* combo, read from its pixels.

    Each row is returned clickable at the text it was recognised from, so the
    caller can both read the list and choose from it without ever guessing a
    coordinate.  An empty result is a real answer here: a combo whose list has
    no entries draws an empty box, and reporting that honestly is what lets
    the caller say "this build does not offer that option" instead of falling
    back to typing and hoping.
    """
    shell = popup_shell(control)
    if shell is None:
        return []
    return read_screen_grid(
        shell, engine=engine, engine_name=engine_name,
        has_header=False, raise_window=False,
    )
