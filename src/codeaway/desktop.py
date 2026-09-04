from dataclasses import dataclass
from enum import Enum
import math
from numbers import Real
from typing import Protocol

from PIL import Image


@dataclass(frozen=True)
class PixelPoint:
    x: int
    y: int


@dataclass(frozen=True)
class PixelRegion:
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> PixelPoint:
        return PixelPoint(self.x + self.width // 2, self.y + self.height // 2)


@dataclass(frozen=True)
class FractionalRegion:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if any(isinstance(value, bool) or not isinstance(value, Real) for value in values):
            raise TypeError("fractional region values must be numbers")
        if any(not math.isfinite(value) for value in values):
            raise ValueError("fractional region values must be finite")
        if not 0 <= self.x <= 1 or not 0 <= self.y <= 1:
            raise ValueError("fractional region origin must be between 0 and 1")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("fractional region dimensions must be positive")
        if self.width > 1 or self.height > 1:
            raise ValueError("fractional region dimensions must be at most 1")
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("fractional region must fit inside its parent")

    def resolve(self, parent: PixelRegion) -> PixelRegion:
        return PixelRegion(
            parent.x + round(parent.width * self.x),
            parent.y + round(parent.height * self.y),
            round(parent.width * self.width),
            round(parent.height * self.height),
        )


@dataclass(frozen=True)
class DesktopWindow:
    id: str
    native_handle: int
    title: str
    process_path: str
    region: PixelRegion


class AccessibilityAction(str, Enum):
    INVOKE = "invoke"
    EXPAND = "expand"
    COLLAPSE = "collapse"


@dataclass(frozen=True)
class AccessibilityNode:
    id: str
    role: str
    name: str
    class_name: str
    region: PixelRegion
    depth: int = 0
    expanded: bool | None = None
    actions: frozenset[AccessibilityAction] = frozenset()


class DesktopBackend(Protocol):
    id: str

    def list_windows(self) -> list[DesktopWindow]: ...

    def activate(self, window: DesktopWindow) -> bool: ...

    def is_foreground(self, window: DesktopWindow) -> bool: ...

    def capture(self, region: PixelRegion) -> Image.Image: ...

    def accessibility_tree(self, window: DesktopWindow) -> list[AccessibilityNode]: ...

    def accessibility_action(
        self, node: AccessibilityNode, action: AccessibilityAction
    ) -> None: ...

    def click(self, point: PixelPoint) -> None: ...

    def scroll(self, point: PixelPoint, amount: int) -> None: ...

    def paste_and_submit(self, point: PixelPoint, text: str) -> None: ...
