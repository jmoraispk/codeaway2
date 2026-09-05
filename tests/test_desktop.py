from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from codeaway.desktop import (
    AccessibilityAction,
    AccessibilityNode,
    DesktopWindow,
    FractionalRegion,
    PixelPoint,
    PixelRegion,
    WindowsDesktop,
    _WindowsNative,
    _NativeControl,
    _NativeWindow,
)


@dataclass
class FakeWindowsNative:
    windows: list[_NativeWindow] = field(default_factory=list)
    controls: list[_NativeControl] = field(default_factory=list)
    activate_result: bool = True
    foreground_handle: int | None = None
    foreground_results: list[bool] = field(default_factory=list)
    click_result: bool = True
    input_calls: list[tuple[object, ...]] = field(default_factory=list)
    wheel_calls: list[tuple[int, int, int]] = field(default_factory=list)
    capture_calls: list[tuple[int, int, int, int]] = field(default_factory=list)
    action_calls: list[tuple[str, AccessibilityAction]] = field(default_factory=list)
    clipboard_values: list[str] = field(default_factory=list)

    def list_windows(self):
        return self.windows

    def activate(self, native_handle):
        del native_handle
        return self.activate_result

    def is_foreground(self, native_handle):
        if self.foreground_results:
            return self.foreground_results.pop(0)
        return self.foreground_handle == native_handle

    def capture(self, bounding_box):
        self.capture_calls.append(bounding_box)
        return "image"

    def accessibility_tree(self, native_handle):
        assert native_handle == 10
        return self.controls

    def accessibility_action(self, control_id, action):
        self.action_calls.append((control_id, action))

    def click(self, native_handle, x, y):
        self.input_calls.append(("click", native_handle, x, y))
        return self.click_result

    def scroll(self, native_handle, x, y, wheel_data):
        self.wheel_calls.append((native_handle, x, y, wheel_data))
        return True

    def set_clipboard_text(self, text):
        self.clipboard_values.append(text)

    def send_paste_and_submit(self, native_handle):
        self.input_calls.append(("send_paste_and_submit", native_handle))
        return True


@pytest.fixture
def native():
    return FakeWindowsNative()


@pytest.fixture
def desktop_window():
    return DesktopWindow("window-10", 10, "Editor", "C:/Apps/editor.exe", PixelRegion(5, 6, 700, 500))


def test_fractional_region_resolves_inside_window():
    window = PixelRegion(100, 50, 1000, 800)
    surface = FractionalRegion(0.2, 0.1, 0.5, 0.75)

    assert surface.resolve(window) == PixelRegion(300, 130, 500, 600)
    assert surface.resolve(window).center == PixelPoint(550, 430)


def test_input_stops_when_exact_window_cannot_be_activated(native, desktop_window):
    native.activate_result = False
    desktop = WindowsDesktop(native)

    assert desktop.activate(desktop_window) is False
    assert native.input_calls == []


def test_scroll_uses_forty_units_per_logical_step(native):
    window = DesktopWindow(
        "window-10", 10, "Editor", "C:/Apps/editor.exe", PixelRegion(0, 0, 800, 600)
    )
    native.foreground_handle = 10
    desktop = WindowsDesktop(native)

    desktop.scroll(window, PixelPoint(500, 400), -3)

    assert native.wheel_calls == [(10, 500, 400, -120)]


def test_list_windows_excludes_invisible_and_zero_area_windows(native):
    native.windows = [
        _NativeWindow(10, "Visible", "C:/Apps/editor.exe", PixelRegion(0, 0, 640, 480), True),
        _NativeWindow(11, "Hidden", "C:/Apps/hidden.exe", PixelRegion(0, 0, 640, 480), False),
        _NativeWindow(12, "Flat", "C:/Apps/flat.exe", PixelRegion(0, 0, 0, 480), True),
    ]

    windows = WindowsDesktop(native).list_windows()

    assert [(window.title, window.native_handle) for window in windows] == [("Visible", 10)]
    assert isinstance(windows[0].id, str)


def test_capture_passes_exact_bounding_box_to_native_boundary(native):
    desktop = WindowsDesktop(native)

    assert desktop.capture(PixelRegion(12, 34, 56, 78)) == "image"
    assert native.capture_calls == [(12, 34, 68, 112)]


def test_accessibility_tree_converts_native_controls_to_serializable_nodes(native, desktop_window):
    native.controls = [
        _NativeControl(
            "button-1",
            "Button",
            "Open",
            "ToolbarButton",
            PixelRegion(10, 20, 30, 40),
            2,
            None,
            frozenset({AccessibilityAction.INVOKE}),
        )
    ]

    nodes = WindowsDesktop(native).accessibility_tree(desktop_window)

    assert nodes == [
        AccessibilityNode(
            id="tree-1-0",
            role="Button",
            name="Open",
            class_name="ToolbarButton",
            region=PixelRegion(10, 20, 30, 40),
            depth=2,
            actions=frozenset({AccessibilityAction.INVOKE}),
        )
    ]
    assert nodes[0].id.startswith("tree-1-")


def test_accessibility_action_dispatches_requested_pattern(native, desktop_window):
    native.controls = [
        _NativeControl(
            "toggle-1",
            "TreeItem",
            "Workspace",
            "",
            PixelRegion(10, 20, 30, 40),
            0,
            False,
            frozenset({AccessibilityAction.EXPAND, AccessibilityAction.COLLAPSE}),
        )
    ]
    desktop = WindowsDesktop(native)
    node = desktop.accessibility_tree(desktop_window)[0]

    desktop.accessibility_action(node, AccessibilityAction.EXPAND)

    assert native.action_calls == [("toggle-1", AccessibilityAction.EXPAND)]


def test_click_and_paste_submit_recheck_the_target_at_each_input_boundary(
    native, desktop_window
):
    native.foreground_handle = desktop_window.native_handle
    desktop = WindowsDesktop(native)

    desktop.click(desktop_window, PixelPoint(11, 12))
    desktop.paste_and_submit(desktop_window, PixelPoint(13, 14), "hello")

    assert native.input_calls == [
        ("click", 10, 11, 12),
        ("click", 10, 13, 14),
        ("send_paste_and_submit", 10),
    ]
    assert native.clipboard_values == ["hello"]


def test_focus_loss_after_composer_click_prevents_keyboard_injection(
    native, desktop_window
):
    native.foreground_results = [True, False]
    desktop = WindowsDesktop(native)

    with pytest.raises(RuntimeError, match="foreground"):
        desktop.paste_and_submit(desktop_window, PixelPoint(13, 14), "hello")

    assert native.input_calls == [("click", 10, 13, 14)]
    assert native.clipboard_values == ["hello"]


def test_cursor_placement_failure_aborts_the_click(native, desktop_window):
    native.foreground_handle = desktop_window.native_handle
    native.click_result = False
    desktop = WindowsDesktop(native)

    with pytest.raises(RuntimeError, match="cursor"):
        desktop.click(desktop_window, PixelPoint(11, 12))

    assert native.input_calls == [("click", 10, 11, 12)]


def test_native_cursor_placement_failure_emits_no_mouse_event(monkeypatch):
    mouse_events = []
    user32 = SimpleNamespace(
        SetCursorPos=lambda x, y: 0,
        mouse_event=lambda *values: mouse_events.append(values),
    )
    import ctypes

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    native = _WindowsNative()
    monkeypatch.setattr(native, "is_foreground", lambda native_handle: True)

    assert native.click(10, 11, 12) is False
    assert mouse_events == []


def test_native_capabilities_read_expand_collapse_pattern_state():
    class ExpandCollapsePattern:
        ExpandCollapseState = 0

    class Control:
        def GetInvokePattern(self):
            raise RuntimeError("not supported")

        def GetExpandCollapsePattern(self):
            return ExpandCollapsePattern()

    actions, expanded = _WindowsNative()._capabilities(Control(), object())

    assert actions == frozenset({AccessibilityAction.EXPAND, AccessibilityAction.COLLAPSE})
    assert expanded is False


def test_native_process_lookup_uses_pointer_sized_process_handles(monkeypatch):
    import ctypes

    class Function:
        def __init__(self, result):
            self.result = result
            self.restype = None

        def __call__(self, *args):
            return self.result(*args) if callable(self.result) else self.result

    def set_path(_, __, path, ___):
        path.value = "C:/Apps/editor.exe"
        return True

    class Kernel32:
        OpenProcess = Function(123)
        QueryFullProcessImageNameW = Function(set_path)
        CloseHandle = Function(True)

    class Windll:
        kernel32 = Kernel32()

    monkeypatch.setattr(ctypes, "windll", Windll())

    assert _WindowsNative()._process_path(77) == "C:/Apps/editor.exe"
    assert Windll.kernel32.OpenProcess.restype is ctypes.c_void_p


def test_native_accessibility_action_selects_requested_uia_pattern(monkeypatch):
    import sys

    class ExpandCollapsePattern:
        def __init__(self):
            self.called = None

        def Expand(self):
            self.called = "expand"

        def Collapse(self):
            self.called = "collapse"

    class Control:
        def __init__(self, pattern):
            self.pattern = pattern

        def GetExpandCollapsePattern(self):
            return self.pattern

    pattern = ExpandCollapsePattern()
    native_boundary = _WindowsNative()
    native_boundary._controls["toggle-1"] = Control(pattern)
    monkeypatch.setitem(sys.modules, "uiautomation", object())

    native_boundary.accessibility_action("toggle-1", AccessibilityAction.EXPAND)

    assert pattern.called == "expand"


def test_native_clipboard_keeps_large_hglobal_pointer_sized(monkeypatch):
    import ctypes

    large_handle = 0x182D8C80008
    calls = []

    class Function:
        def __init__(self, name, result):
            self.name = name
            self.result = result
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            if (
                self.name in {"GlobalLock", "GlobalUnlock", "GlobalFree", "SetClipboardData"}
                and any(argument == large_handle for argument in args)
                and self.argtypes is None
            ):
                raise ctypes.ArgumentError(OverflowError("int too long to convert"))
            calls.append((self.name, args))
            return self.result

    class Kernel32:
        GlobalAlloc = Function("GlobalAlloc", large_handle)
        GlobalLock = Function("GlobalLock", large_handle)
        GlobalUnlock = Function("GlobalUnlock", True)
        GlobalFree = Function("GlobalFree", None)

    class User32:
        OpenClipboard = Function("OpenClipboard", True)
        EmptyClipboard = Function("EmptyClipboard", True)
        SetClipboardData = Function("SetClipboardData", large_handle)
        CloseClipboard = Function("CloseClipboard", True)

    class Windll:
        kernel32 = Kernel32()
        user32 = User32()

    monkeypatch.setattr(ctypes, "windll", Windll())
    monkeypatch.setattr(ctypes, "memmove", lambda *args: calls.append(("memmove", args)))

    _WindowsNative._set_clipboard_text("hello")

    assert [call[0] for call in calls] == [
        "OpenClipboard",
        "EmptyClipboard",
        "GlobalAlloc",
        "GlobalLock",
        "memmove",
        "GlobalUnlock",
        "SetClipboardData",
        "CloseClipboard",
    ]
    assert Windll.kernel32.GlobalLock.argtypes == (ctypes.c_void_p,)
    assert Windll.kernel32.GlobalUnlock.argtypes == (ctypes.c_void_p,)
    assert Windll.kernel32.GlobalFree.argtypes == (ctypes.c_void_p,)
    assert Windll.user32.SetClipboardData.argtypes == (ctypes.c_uint, ctypes.c_void_p)
