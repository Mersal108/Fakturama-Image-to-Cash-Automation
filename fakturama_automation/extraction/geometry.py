"""Small spatial primitives shared by the OCR layer and the document parser.

The parser never refers to absolute pixel positions.  It asks questions like
"what is directly below this label?" or "which words fall inside this table
column?", and those questions are expressed with the helpers here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class Box:
    """An axis-aligned rectangle in image coordinates (origin top-left)."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2

    def overlaps_x(self, other: "Box", tolerance: float = 0.0) -> bool:
        return (
            self.x - tolerance < other.right
            and other.x - tolerance < self.right
        )

    def overlaps_y(self, other: "Box", tolerance: float = 0.0) -> bool:
        return (
            self.y - tolerance < other.bottom
            and other.y - tolerance < self.bottom
        )

    def vertical_overlap_ratio(self, other: "Box") -> float:
        """Fraction of the shorter box's height that overlaps the other.

        Used to decide whether two words belong to the same printed line,
        which is more reliable than comparing raw y coordinates when fonts of
        different sizes sit side by side.
        """
        top = max(self.y, other.y)
        bottom = min(self.bottom, other.bottom)
        if bottom <= top:
            return 0.0
        shorter = min(self.height, other.height) or 1.0
        return (bottom - top) / shorter

    def union(self, other: "Box") -> "Box":
        x = min(self.x, other.x)
        y = min(self.y, other.y)
        return Box(x, y, max(self.right, other.right) - x, max(self.bottom, other.bottom) - y)

    def expanded(
        self,
        left: float = 0.0,
        top: float = 0.0,
        right: float = 0.0,
        bottom: float = 0.0,
    ) -> "Box":
        return Box(
            self.x - left,
            self.y - top,
            self.width + left + right,
            self.height + top + bottom,
        )

    def contains_point(self, px: float, py: float) -> bool:
        return self.x <= px <= self.right and self.y <= py <= self.bottom


def union_all(boxes: Iterable[Box]) -> Optional[Box]:
    result: Optional[Box] = None
    for box in boxes:
        result = box if result is None else result.union(box)
    return result
