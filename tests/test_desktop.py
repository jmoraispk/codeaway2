from dataclasses import dataclass, field

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
    input_calls: list[tuple[object, ...]] = field(default_factory=list)
    wheel_calls: list[tuple[int, int, int]] = field(default_factory=list)
    capture_calls: list[tuple[int, int, int, int]] = field(default_factory=list)
    action_calls: list[tuple[str, AccessibilityAction]] = field(default_factory=list)

    def list_windows(self):
        return self.windows

    def activate(self, native_handle):
        del native_handle
        return self.activate_result

    def is_foreground(self, native_handle):
        return self.foreground_handle == native_handle

    def capture(self, bounding_box):
        self.capture_calls.append(bounding_box)
        return "image"

    def accessibility_tree(self, native_handle):
        assert native_handle == 10
        return self.controls

    def accessibility_action(self, control_id, action):
        self.action_calls.append((control_id, action))

    def click(self, x, y):
        self.input_calls.append(("click", x, y))

    def scroll(self, x, y, wheel_data):
        self.wheel_calls.append((x, y, wheel_data))

    def paste_and_submit(self, x, y, text):
        self.input_calls.append(("paste_and_submit", x, y, text))


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
    desktop = WindowsDesktop(native)

    desktop.scroll(PixelPoint(500, 400), -3)

    assert native.wheel_calls == [(500, 400, -120)]


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


def test_click_and_paste_submit_use_only_the_native_boundary(native):
    desktop = WindowsDesktop(native)

    desktop.click(PixelPoint(11, 12))
    desktop.paste_and_submit(PixelPoint(13, 14), "hello")

    assert native.input_calls == [
        ("click", 11, 12),
        ("paste_and_submit", 13, 14, "hello"),
    ]


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
