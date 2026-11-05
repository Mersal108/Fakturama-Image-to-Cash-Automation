"""OCR engines and the word-level document they produce.

Two engines are provided behind one interface:

``windows``    Windows.Media.Ocr, the OCR engine built into Windows 10/11.
               Zero install, no API key, deterministic, and it returns a
               bounding box per word -- which is what the label-anchored
               parser needs.  This is the default.
``tesseract``  Tesseract via ``pytesseract``, used when the binary is present.
               Kept because it is the portable choice and lets the same parser
               be exercised against a second engine.

Both return an :class:`OcrDocument`, so :mod:`..parser` never knows which one
ran.  The same seam is where a vision-LLM extractor would plug in.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from ..errors import ExtractionError
from .geometry import Box, union_all


@dataclass(frozen=True)
class OcrWord:
    """A single recognised word and where it sits on the page."""

    text: str
    box: Box
    confidence: float = 1.0

    @property
    def normalised(self) -> str:
        """Lower-cased, punctuation-trimmed form used for matching labels."""
        return self.text.strip().strip(".,:;").casefold()


class OcrDocument:
    """All words found on one image, with spatial query helpers."""

    def __init__(self, words: Sequence[OcrWord], width: int, height: int, engine: str):
        self.words: List[OcrWord] = list(words)
        self.width = width
        self.height = height
        self.engine = engine

    # ------------------------------------------------------------------ text

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def words_in(self, region: Box) -> List[OcrWord]:
        """Words whose centre point falls inside ``region``, in reading order."""
        hits = [
            w for w in self.words
            if region.contains_point(w.box.center_x, w.box.center_y)
        ]
        return sort_reading_order(hits)

    def text_in(self, region: Box) -> str:
        return " ".join(w.text for w in self.words_in(region))

    # ----------------------------------------------------------------- lines

    def lines(self, words: Optional[Sequence[OcrWord]] = None) -> List[List[OcrWord]]:
        """Group words into printed lines by vertical overlap.

        Grouping on overlap rather than on a fixed y tolerance keeps lines
        intact when a row mixes font sizes (the items table does exactly that).
        """
        pool = sorted(
            list(self.words if words is None else words),
            key=lambda w: (w.box.center_y, w.box.x),
        )
        rows: List[List[OcrWord]] = []
        for word in pool:
            placed = False
            for row in rows:
                if row[0].box.vertical_overlap_ratio(word.box) >= 0.4:
                    row.append(word)
                    placed = True
                    break
            if not placed:
                rows.append([word])
        for row in rows:
            row.sort(key=lambda w: w.box.x)
        rows.sort(key=lambda r: min(w.box.y for w in r))
        return rows

    # ---------------------------------------------------------------- labels

    def find_phrase(
        self,
        phrase: str,
        *,
        occurrence: int = 0,
        region: Optional[Box] = None,
    ) -> Optional[Box]:
        """Locate a run of consecutive words matching ``phrase``.

        Returns the union box of the matched words, or ``None``.  Matching is
        case-insensitive and ignores trailing punctuation, so ``"Disc."`` on
        the page matches a query of ``"disc"``.
        """
        matches = self.find_all_phrases(phrase, region=region)
        if occurrence < len(matches):
            return matches[occurrence]
        return None

    def find_all_phrases(self, phrase: str, *, region: Optional[Box] = None) -> List[Box]:
        """Every place ``phrase`` appears as consecutive words on one line.

        Matching runs inside line groups so a phrase can never be assembled
        across a line break.  Within a line, words that do not vertically
        overlap the first matched word are skipped rather than breaking the
        match -- stacked table headers such as ``Unit net`` above ``(EUR)``
        can otherwise interleave by x and hide the phrase.
        """
        tokens = [t.strip().strip(".,:;").casefold() for t in phrase.split() if t.strip()]
        if not tokens:
            return []
        pool = self.words_in(region) if region is not None else list(self.words)

        results: List[Box] = []
        for row in self.lines(pool):
            for start in range(len(row)):
                if row[start].normalised != tokens[0]:
                    continue
                anchor = row[start].box
                matched = [row[start]]
                cursor = start + 1
                while cursor < len(row) and len(matched) < len(tokens):
                    candidate = row[cursor]
                    if anchor.vertical_overlap_ratio(candidate.box) < 0.5:
                        cursor += 1           # belongs to a neighbouring line
                        continue
                    if candidate.normalised != tokens[len(matched)]:
                        break
                    matched.append(candidate)
                    cursor += 1
                if len(matched) == len(tokens):
                    box = union_all(w.box for w in matched)
                    if box is not None:
                        results.append(box)
        results.sort(key=lambda b: (b.y, b.x))
        return results

    def require_phrase(self, phrase: str, *, region: Optional[Box] = None) -> Box:
        box = self.find_phrase(phrase, region=region)
        if box is None:
            raise ExtractionError(f"label {phrase!r} not found on the page")
        return box


def sort_reading_order(words: Sequence[OcrWord]) -> List[OcrWord]:
    """Left-to-right within a line, top-to-bottom between lines."""
    return sorted(words, key=lambda w: (round(w.box.center_y / 8), w.box.x))


# --------------------------------------------------------------------- engines


class BaseOcrEngine:
    name = "base"

    def recognize(self, image_path: str) -> OcrDocument:  # pragma: no cover
        raise NotImplementedError


class WindowsMediaOcrEngine(BaseOcrEngine):
    """Windows' built-in OCR (``Windows.Media.Ocr``).

    Needs no installation and no network.  ``scale`` upsamples the image before
    recognition for small or noisy scans; boxes are divided back down so the
    parser always works in original-image coordinates.
    """

    name = "windows"

    def __init__(self, language: str = "en-US", scale: float = 1.0):
        self.language = language
        self.scale = scale

    def recognize(self, image_path: str) -> OcrDocument:
        if not os.path.isfile(image_path):
            raise ExtractionError(f"image not found: {image_path}")
        try:
            return asyncio.run(self._recognize_async(os.path.abspath(image_path)))
        except ExtractionError:
            raise
        except Exception as exc:  # pragma: no cover - environment dependent
            raise ExtractionError(f"Windows OCR failed: {exc}") from exc

    async def _recognize_async(self, image_path: str) -> OcrDocument:
        import winrt.windows.globalization as wg
        import winrt.windows.graphics.imaging as wgi
        import winrt.windows.media.ocr as wocr
        import winrt.windows.storage as ws

        work_path, scale_used = self._prepare(image_path)
        try:
            storage_file = await ws.StorageFile.get_file_from_path_async(work_path)
            stream = await storage_file.open_async(ws.FileAccessMode.READ)
            decoder = await wgi.BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()

            engine = wocr.OcrEngine.try_create_from_language(wg.Language(self.language))
            if engine is None:
                engine = wocr.OcrEngine.try_create_from_user_profile_languages()
            if engine is None:
                raise ExtractionError(
                    "no Windows OCR language pack available; install one under "
                    "Settings > Time & language, or use the tesseract engine"
                )

            result = await engine.recognize_async(bitmap)
            words: List[OcrWord] = []
            for line in result.lines:
                for word in line.words:
                    rect = word.bounding_rect
                    words.append(
                        OcrWord(
                            text=word.text,
                            box=Box(
                                rect.x / scale_used,
                                rect.y / scale_used,
                                rect.width / scale_used,
                                rect.height / scale_used,
                            ),
                        )
                    )
            return OcrDocument(
                words,
                int(bitmap.pixel_width / scale_used),
                int(bitmap.pixel_height / scale_used),
                self.name,
            )
        finally:
            if work_path != image_path and os.path.exists(work_path):
                try:
                    os.remove(work_path)
                except OSError:
                    pass

    def _prepare(self, image_path: str):
        """Optionally upscale the image; returns (path_to_use, scale_applied)."""
        if self.scale == 1.0:
            return image_path, 1.0
        from PIL import Image

        import tempfile

        with Image.open(image_path) as img:
            img = img.convert("RGB")
            target = (int(img.width * self.scale), int(img.height * self.scale))
            resized = img.resize(target, Image.LANCZOS)
            handle, temp_path = tempfile.mkstemp(suffix=".png", prefix="ocr_scaled_")
            os.close(handle)
            resized.save(temp_path)
        return temp_path, self.scale


class TesseractOcrEngine(BaseOcrEngine):
    """Tesseract via pytesseract, used when the binary is on PATH.

    Runs in TSV mode so every word arrives with a bounding box and a
    confidence, matching what :class:`WindowsMediaOcrEngine` provides.
    """

    name = "tesseract"

    def __init__(self, scale: float = 2.0, min_confidence: float = 30.0,
                 binary: Optional[str] = None):
        self.scale = scale
        self.min_confidence = min_confidence
        self.binary = binary or shutil.which("tesseract")

    def available(self) -> bool:
        return bool(self.binary)

    def recognize(self, image_path: str) -> OcrDocument:
        if not self.available():
            raise ExtractionError(
                "tesseract binary not found on PATH; use the 'windows' engine"
            )
        try:
            import pytesseract
            from PIL import Image, ImageOps
        except ImportError as exc:
            raise ExtractionError(f"pytesseract/Pillow not installed: {exc}") from exc

        pytesseract.pytesseract.tesseract_cmd = self.binary

        with Image.open(image_path) as img:
            prepared = img.convert("L")
            if self.scale != 1.0:
                prepared = prepared.resize(
                    (int(prepared.width * self.scale), int(prepared.height * self.scale)),
                    Image.LANCZOS,
                )
            prepared = ImageOps.autocontrast(prepared)
            data = pytesseract.image_to_data(
                prepared, output_type=pytesseract.Output.DICT
            )
            width, height = img.width, img.height

        words: List[OcrWord] = []
        for i, text in enumerate(data["text"]):
            if not text or not text.strip():
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            if conf < self.min_confidence:
                continue
            words.append(
                OcrWord(
                    text=text,
                    box=Box(
                        data["left"][i] / self.scale,
                        data["top"][i] / self.scale,
                        data["width"][i] / self.scale,
                        data["height"][i] / self.scale,
                    ),
                    confidence=conf / 100.0,
                )
            )
        return OcrDocument(words, width, height, self.name)


class CellReader:
    """Re-reads one small region of the page at high magnification.

    A full-page pass optimises for the page as a whole, and small isolated
    tokens are exactly what it drops or garbles -- the ``0%`` in a discount
    column, say.  Cropping that single cell and enlarging it hands the engine
    a clean, well-sized image, and the reads become stable and repeatable.

    Results are cached, so asking for the same cell twice costs one OCR pass.
    """

    def __init__(
        self,
        image_path: str,
        engine: Optional[BaseOcrEngine] = None,
        zoom: int = 6,
        pad: int = 8,
        min_side: int = 48,
    ):
        self.image_path = image_path
        self.engine = engine or WindowsMediaOcrEngine()
        self.zoom = zoom
        self.pad = pad
        self.min_side = min_side
        self._cache: Dict[tuple, List[str]] = {}

    def read(self, region: Box) -> str:
        return " ".join(self.read_words(region))

    def read_words(self, region: Box) -> List[str]:
        key = (
            round(region.x), round(region.y),
            round(region.width), round(region.height),
        )
        if key in self._cache:
            return self._cache[key]

        import tempfile

        from PIL import Image

        words: List[str] = []
        try:
            with Image.open(self.image_path) as img:
                page = img.convert("RGB")
                left = max(0, int(region.x) - self.pad)
                top = max(0, int(region.y) - self.pad)
                right = min(page.width, int(region.right) + self.pad)
                bottom = min(page.height, int(region.bottom) + self.pad)
                if right - left < 2 or bottom - top < 2:
                    self._cache[key] = []
                    return []
                crop = page.crop((left, top, right, bottom))

            zoom = self.zoom
            # Windows OCR rejects images smaller than roughly 40x40 pixels.
            while crop.width * zoom < self.min_side or crop.height * zoom < self.min_side:
                zoom += 1
            enlarged = crop.resize(
                (crop.width * zoom, crop.height * zoom), Image.LANCZOS
            )

            handle, temp_path = tempfile.mkstemp(suffix=".png", prefix="cell_")
            os.close(handle)
            try:
                enlarged.save(temp_path)
                result = self.engine.recognize(temp_path)
                words = [w.text for w in sort_reading_order(result.words)]
            finally:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        except ExtractionError:
            words = []
        except Exception:  # pragma: no cover - a cell read must never be fatal
            words = []

        self._cache[key] = words
        return words


_ENGINES: Dict[str, Callable[[], BaseOcrEngine]] = {
    "windows": WindowsMediaOcrEngine,
    "tesseract": TesseractOcrEngine,
}


def get_engine(name: str = "windows", **kwargs) -> BaseOcrEngine:
    key = (name or "windows").lower()
    if key == "auto":
        tess = TesseractOcrEngine(**kwargs)
        return tess if tess.available() else WindowsMediaOcrEngine()
    if key not in _ENGINES:
        raise ExtractionError(
            f"unknown OCR engine {name!r}; choose from {sorted(_ENGINES)} or 'auto'"
        )
    return _ENGINES[key](**kwargs)
