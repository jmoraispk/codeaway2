from dataclasses import dataclass
from enum import Enum
import math
from numbers import Real
from typing import Any, Protocol
from uuid import uuid4

from PIL import Image, ImageGrab


def _pin_thread_v2_dpi(user32: Any | None = None) -> bool:
    """Make Win32 rectangles and screen captures use physical pixels."""
    import ctypes
    import sys

    if user32 is None:
        if not sys.platform.startswith("win"):
            return False
        user32 = ctypes.windll.user32
    try:
        setter = user32.SetThreadDpiAwarenessContext
        setter.argtypes = (ctypes.c_void_p,)
        setter.restype = ctypes.c_void_p
        return bool(setter(ctypes.c_void_p(-4)))  # PER_MONITOR_AWARE_V2
    except Exception:
        return False


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


class InputUnavailable(RuntimeError):
    """Global input was aborted because its exact target was not safe."""


class AccessibilityUnavailable(RuntimeError):
    """The desktop accessibility provider could not produce a control tree."""


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

    def click(self, window: DesktopWindow, point: PixelPoint) -> None: ...

    def scroll(
        self, window: DesktopWindow, point: PixelPoint, amount: int
    ) -> None: ...

    def paste_and_submit(
        self, window: DesktopWindow, point: PixelPoint, text: str
    ) -> None: ...


@dataclass(frozen=True)
class _NativeWindow:
    native_handle: int
    title: str
    process_path: str
    region: PixelRegion
    visible: bool


@dataclass(frozen=True)
class _NativeControl:
    id: str
    role: str
    name: str
    class_name: str
    region: PixelRegion
    depth: int
    expanded: bool | None
    actions: frozenset[AccessibilityAction]


class WindowsDesktop:
    """Windows desktop mechanics behind an injectable native boundary."""

    id = "windows"

    def __init__(self, native: "_WindowsNative | None" = None) -> None:
        self._native = native or _WindowsNative()
        self._tree_version = 0
        self._node_controls: dict[str, str] = {}

    def list_windows(self) -> list[DesktopWindow]:
        _pin_thread_v2_dpi()
        return [
            DesktopWindow(
                id=f"window-{uuid4().hex}",
                native_handle=window.native_handle,
                title=window.title,
                process_path=window.process_path,
                region=window.region,
            )
            for window in self._native.list_windows()
            if window.visible and window.region.width > 0 and window.region.height > 0
        ]

    def activate(self, window: DesktopWindow) -> bool:
        if not _pin_thread_v2_dpi():
            return False
        return self._native.activate(window.native_handle)

    def is_foreground(self, window: DesktopWindow) -> bool:
        return self._native.is_foreground(window.native_handle)

    def capture(self, region: PixelRegion) -> Image.Image:
        _pin_thread_v2_dpi()
        return self._native.capture(
            (region.x, region.y, region.x + region.width, region.y + region.height)
        )

    def accessibility_tree(self, window: DesktopWindow) -> list[AccessibilityNode]:
        try:
            controls = self._native.accessibility_tree(window.native_handle)
            self._tree_version += 1
            self._node_controls = {}
            nodes: list[AccessibilityNode] = []
            for index, control in enumerate(controls):
                node_id = f"tree-{self._tree_version}-{index}"
                self._node_controls[node_id] = control.id
                nodes.append(
                    AccessibilityNode(
                        id=node_id,
                        role=control.role,
                        name=control.name,
                        class_name=control.class_name,
                        region=control.region,
                        depth=control.depth,
                        expanded=control.expanded,
                        actions=control.actions,
                    )
                )
            return nodes
        except AccessibilityUnavailable:
            raise
        except Exception as error:
            raise AccessibilityUnavailable(
                "desktop accessibility is unavailable"
            ) from error

    def accessibility_action(
        self, node: AccessibilityNode, action: AccessibilityAction
    ) -> None:
        if action not in node.actions:
            raise ValueError(f"accessibility action {action.value!r} is unavailable")
        control_id = self._node_controls.get(node.id)
        if control_id is None:
            raise ValueError("accessibility node is not from the latest tree read")
        self._native.accessibility_action(control_id, action)

    def _require_foreground(self, window: DesktopWindow) -> None:
        if not self._native.is_foreground(window.native_handle):
            raise InputUnavailable("the exact target window is not foreground")

    def click(self, window: DesktopWindow, point: PixelPoint) -> None:
        if not _pin_thread_v2_dpi():
            raise InputUnavailable("physical DPI coordinate context is unavailable")
        self._require_foreground(window)
        if not self._native.click(window.native_handle, point.x, point.y):
            raise InputUnavailable(
                "cursor placement or foreground validation failed"
            )

    def scroll(
        self, window: DesktopWindow, point: PixelPoint, amount: int
    ) -> None:
        if not _pin_thread_v2_dpi():
            raise InputUnavailable("physical DPI coordinate context is unavailable")
        self._require_foreground(window)
        if not self._native.scroll(
            window.native_handle, point.x, point.y, amount * 40
        ):
            raise InputUnavailable(
                "cursor placement or foreground validation failed"
            )

    def paste_and_submit(
        self, window: DesktopWindow, point: PixelPoint, text: str
    ) -> None:
        self.click(window, point)
        self._native.set_clipboard_text(text)
        self._require_foreground(window)
        if not self._native.send_paste_and_submit(window.native_handle):
            raise InputUnavailable("the exact target window is not foreground")


class _WindowsNative:
    """The only class that holds Win32 or UI Automation objects."""

    def __init__(self) -> None:
        self._controls: dict[str, Any] = {}

    def list_windows(self) -> list[_NativeWindow]:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        windows: list[_NativeWindow] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(native_handle: int, _: int) -> bool:
            if not user32.IsWindowVisible(native_handle):
                return True
            rect = wintypes.RECT()
            if not user32.GetWindowRect(native_handle, ctypes.byref(rect)):
                return True
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if width <= 0 or height <= 0:
                return True
            title_length = user32.GetWindowTextLengthW(native_handle)
            title_buffer = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(native_handle, title_buffer, len(title_buffer))
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(native_handle, ctypes.byref(process_id))
            windows.append(
                _NativeWindow(
                    int(native_handle),
                    title_buffer.value,
                    self._process_path(process_id.value),
                    PixelRegion(rect.left, rect.top, width, height),
                    True,
                )
            )
            return True

        user32.EnumWindows(visit, 0)
        return windows

    def _process_path(self, process_id: int) -> str:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        process_handle = kernel32.OpenProcess(0x1000, False, process_id)
        if not process_handle:
            return ""
        try:
            path = ctypes.create_unicode_buffer(32768)
            path_size = wintypes.DWORD(len(path))
            if not kernel32.QueryFullProcessImageNameW(
                process_handle, 0, path, ctypes.byref(path_size)
            ):
                return ""
            return path.value
        finally:
            kernel32.CloseHandle(process_handle)

    def activate(self, native_handle: int) -> bool:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        if not user32.IsWindow(native_handle):
            return False
        foreground_handle = user32.GetForegroundWindow()
        foreground_thread = wintypes.DWORD()
        if foreground_handle:
            foreground_thread_id = user32.GetWindowThreadProcessId(
                foreground_handle, ctypes.byref(foreground_thread)
            )
        else:
            foreground_thread_id = 0
        current_thread_id = kernel32.GetCurrentThreadId()
        attached = bool(
            foreground_thread_id
            and foreground_thread_id != current_thread_id
            and user32.AttachThreadInput(foreground_thread_id, current_thread_id, True)
        )
        try:
            user32.SetForegroundWindow(native_handle)
            return user32.GetForegroundWindow() == native_handle
        finally:
            if attached:
                user32.AttachThreadInput(foreground_thread_id, current_thread_id, False)

    def is_foreground(self, native_handle: int) -> bool:
        import ctypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        return user32.GetForegroundWindow() == native_handle

    def capture(self, bounding_box: tuple[int, int, int, int]) -> Image.Image:
        return ImageGrab.grab(bbox=bounding_box, all_screens=True)

    def accessibility_tree(self, native_handle: int) -> list[_NativeControl]:
        import uiautomation as auto

        self._controls = {}
        controls: list[_NativeControl] = []

        def walk(control: Any, depth: int) -> None:
            if depth > 40:
                return
            if not self._is_offscreen(control):
                region = self._control_region(control)
                if region is not None:
                    control_id = f"control-{len(controls)}"
                    actions, expanded = self._capabilities(control, auto)
                    self._controls[control_id] = control
                    controls.append(
                        _NativeControl(
                            control_id,
                            str(getattr(control, "ControlTypeName", "")),
                            str(getattr(control, "Name", "")),
                            str(getattr(control, "ClassName", "")),
                            region,
                            depth,
                            expanded,
                            actions,
                        )
                    )
            for child in self._children(control):
                walk(child, depth + 1)

        walk(auto.ControlFromHandle(native_handle), 0)
        return controls

    @staticmethod
    def _children(control: Any) -> list[Any]:
        try:
            return list(control.GetChildren())
        except Exception:
            return []

    @staticmethod
    def _is_offscreen(control: Any) -> bool:
        try:
            return bool(control.IsOffscreen)
        except Exception:
            return True

    @staticmethod
    def _control_region(control: Any) -> PixelRegion | None:
        try:
            rectangle = control.BoundingRectangle
            width = int(rectangle.right) - int(rectangle.left)
            height = int(rectangle.bottom) - int(rectangle.top)
            if width <= 0 or height <= 0:
                return None
            return PixelRegion(int(rectangle.left), int(rectangle.top), width, height)
        except Exception:
            return None

    def _capabilities(
        self, control: Any, auto: Any
    ) -> tuple[frozenset[AccessibilityAction], bool | None]:
        actions: set[AccessibilityAction] = set()
        if self._pattern(control, auto, "Invoke") is not None:
            actions.add(AccessibilityAction.INVOKE)
        expand_collapse = self._pattern(control, auto, "ExpandCollapse")
        if expand_collapse is None:
            return frozenset(actions), None
        actions.update({AccessibilityAction.EXPAND, AccessibilityAction.COLLAPSE})
        try:
            state = expand_collapse.ExpandCollapseState
        except Exception:
            return frozenset(actions), None
        state_name = str(state).casefold()
        if "expanded" in state_name or state == 1:
            return frozenset(actions), True
        if "collapsed" in state_name or state == 0:
            return frozenset(actions), False
        return frozenset(actions), None

    @staticmethod
    def _pattern(control: Any, auto: Any, name: str) -> Any | None:
        getter = getattr(control, f"Get{name}Pattern", None)
        if getter is not None:
            try:
                return getter()
            except Exception:
                pass
        pattern_id = getattr(getattr(auto, "PatternId", object()), f"{name}Pattern", None)
        get_pattern = getattr(control, "GetPattern", None)
        if pattern_id is not None and get_pattern is not None:
            try:
                return get_pattern(pattern_id)
            except Exception:
                pass
        return None

    def accessibility_action(self, control_id: str, action: AccessibilityAction) -> None:
        import uiautomation as auto

        control = self._controls.get(control_id)
        if control is None:
            raise ValueError("accessibility control is unavailable")
        pattern_name = (
            "Invoke"
            if action is AccessibilityAction.INVOKE
            else "ExpandCollapse"
        )
        pattern = self._pattern(control, auto, pattern_name)
        if pattern is None:
            raise ValueError(f"accessibility action {action.value!r} is unavailable")
        method_name = {
            AccessibilityAction.INVOKE: "Invoke",
            AccessibilityAction.EXPAND: "Expand",
            AccessibilityAction.COLLAPSE: "Collapse",
        }[action]
        getattr(pattern, method_name)()

    def click(self, native_handle: int, x: int, y: int) -> bool:
        import ctypes

        user32 = ctypes.windll.user32
        if not user32.SetCursorPos(x, y) or not self.is_foreground(native_handle):
            return False
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
        return True

    def scroll(self, native_handle: int, x: int, y: int, wheel_data: int) -> bool:
        import ctypes

        user32 = ctypes.windll.user32
        if not user32.SetCursorPos(x, y) or not self.is_foreground(native_handle):
            return False
        user32.mouse_event(0x0800, 0, 0, wheel_data, 0)  # MOUSEEVENTF_WHEEL
        return True

    def set_clipboard_text(self, text: str) -> None:
        self._set_clipboard_text(text)

    def send_paste_and_submit(self, native_handle: int) -> bool:
        import uiautomation as auto

        if not self.is_foreground(native_handle):
            return False
        auto.SendKeys("{Ctrl}v{Enter}")
        return True

    @staticmethod
    def _set_clipboard_text(text: str) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hglobal = ctypes.c_void_p
        user32.OpenClipboard.argtypes = (wintypes.HWND,)
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = ()
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = (wintypes.UINT, hglobal)
        user32.SetClipboardData.restype = hglobal
        user32.CloseClipboard.argtypes = ()
        user32.CloseClipboard.restype = wintypes.BOOL
        kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
        kernel32.GlobalAlloc.restype = hglobal
        kernel32.GlobalLock.argtypes = (hglobal,)
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = (hglobal,)
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = (hglobal,)
        kernel32.GlobalFree.restype = hglobal
        if not user32.OpenClipboard(None):
            raise OSError("could not open the clipboard")
        try:
            if not user32.EmptyClipboard():
                raise OSError("could not clear the clipboard")
            value = ctypes.create_unicode_buffer(text)
            memory = kernel32.GlobalAlloc(0x0002, ctypes.sizeof(value))
            if not memory:
                raise OSError("could not allocate clipboard memory")
            pointer = kernel32.GlobalLock(memory)
            if not pointer:
                kernel32.GlobalFree(memory)
                raise OSError("could not lock clipboard memory")
            try:
                ctypes.memmove(pointer, value, ctypes.sizeof(value))
            finally:
                kernel32.GlobalUnlock(memory)
            if not user32.SetClipboardData(13, memory):  # CF_UNICODETEXT
                kernel32.GlobalFree(memory)
                raise OSError("could not set clipboard text")
        finally:
            user32.CloseClipboard()
