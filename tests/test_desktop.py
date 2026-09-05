import ctypes
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

import codeaway.desktop as desktop_module

from codeaway.desktop import (
    AccessibilityAction,
    AccessibilityNode,
    DesktopWindow,
    FractionalRegion,
    InputUnavailable,
    PixelPoint,
    PixelRegion,
    WindowsDesktop,
    _WindowsNative,
    _NativeControl,
    _NativeWindow,
)


class Win32Function:
    def __init__(self, callback):
        self.callback = callback
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


def mock_native_activation(monkeypatch, foreground_handle, show_window=None):
    calls = []

    def thread_id(native_handle, thread_pointer):
        ctypes.cast(thread_pointer, ctypes.POINTER(ctypes.c_ulong))[0] = 77
        return 77

    def attach(first_thread, second_thread, attach):
        calls.append(("AttachThreadInput", first_thread, second_thread, attach))
        return True

    def set_foreground(native_handle):
        calls.append(("SetForegroundWindow", native_handle))
        return True

    def show(native_handle, command):
        calls.append(("ShowWindow", native_handle, command))
        if show_window is not None:
            show_window()
        return True

    user32 = SimpleNamespace(
        IsWindow=Win32Function(lambda native_handle: native_handle == 10),
        GetForegroundWindow=Win32Function(lambda: foreground_handle),
        GetWindowThreadProcessId=Win32Function(thread_id),
        AttachThreadInput=Win32Function(attach),
        SetForegroundWindow=Win32Function(set_foreground),
        ShowWindow=Win32Function(show),
    )
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(
            user32=user32,
            kernel32=SimpleNamespace(GetCurrentThreadId=lambda: 77),
        ),
        raising=False,
    )
    return calls


@dataclass
class FakeWindowsNative:
    windows: list[_NativeWindow] = field(default_factory=list)
    controls: list[_NativeControl] = field(default_factory=list)
    accessibility_error: Exception | None = None
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
        if self.accessibility_error is not None:
            raise self.accessibility_error
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


def test_activation_and_coordinate_input_pin_dpi_before_native_operations(
    monkeypatch, native, desktop_window
):
    events = []

    monkeypatch.setattr(
        desktop_module,
        "_pin_thread_v2_dpi",
        lambda: events.append("dpi") or True,
    )
    monkeypatch.setattr(
        native,
        "activate",
        lambda native_handle: events.append(("activate", native_handle)) or True,
    )
    monkeypatch.setattr(
        native,
        "is_foreground",
        lambda native_handle: events.append(("foreground", native_handle)) or True,
    )
    monkeypatch.setattr(
        native,
        "click",
        lambda native_handle, x, y: events.append(("click", native_handle, x, y)) or True,
    )
    monkeypatch.setattr(
        native,
        "scroll",
        lambda native_handle, x, y, amount: events.append(
            ("scroll", native_handle, x, y, amount)
        )
        or True,
    )

    desktop = WindowsDesktop(native)
    assert desktop.activate(desktop_window) is True
    desktop.click(desktop_window, PixelPoint(2560, 100))
    desktop.scroll(desktop_window, PixelPoint(2560, 100), -3)

    assert events == [
        "dpi",
        ("activate", 10),
        "dpi",
        ("foreground", 10),
        ("click", 10, 2560, 100),
        "dpi",
        ("foreground", 10),
        ("scroll", 10, 2560, 100, -240),
    ]


@pytest.mark.parametrize("operation", ["activate", "click", "scroll"])
def test_failed_dpi_pin_stops_operations_before_the_native_boundary(
    monkeypatch, native, desktop_window, operation
):
    calls = []

    monkeypatch.setattr(
        desktop_module,
        "_pin_thread_v2_dpi",
        lambda: calls.append("dpi") or False,
    )
    monkeypatch.setattr(
        native,
        "activate",
        lambda native_handle: calls.append(("activate", native_handle)) or True,
    )
    monkeypatch.setattr(
        native,
        "is_foreground",
        lambda native_handle: calls.append(("foreground", native_handle)) or True,
    )
    monkeypatch.setattr(
        native,
        "click",
        lambda native_handle, x, y: calls.append(("click", native_handle, x, y)) or True,
    )
    monkeypatch.setattr(
        native,
        "scroll",
        lambda native_handle, x, y, amount: calls.append(
            ("scroll", native_handle, x, y, amount)
        )
        or True,
    )
    desktop = WindowsDesktop(native)

    if operation == "activate":
        assert desktop.activate(desktop_window) is False
    elif operation == "click":
        with pytest.raises(InputUnavailable, match="DPI"):
            desktop.click(desktop_window, PixelPoint(2560, 100))
    else:
        with pytest.raises(InputUnavailable, match="DPI"):
            desktop.scroll(desktop_window, PixelPoint(2560, 100), -3)

    assert calls == ["dpi"]


def test_scroll_uses_eighty_units_per_logical_step(native):
    window = DesktopWindow(
        "window-10", 10, "Editor", "C:/Apps/editor.exe", PixelRegion(0, 0, 800, 600)
    )
    native.foreground_handle = 10
    desktop = WindowsDesktop(native)

    desktop.scroll(window, PixelPoint(500, 400), -3)

    assert native.wheel_calls == [(10, 500, 400, -240)]


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


def test_windows_dpi_pin_requests_per_monitor_aware_v2():
    contexts = []

    class Setter:
        argtypes = None
        restype = None

        def __call__(self, context):
            contexts.append(context.value)
            return 1

    pin = getattr(desktop_module, "_pin_thread_v2_dpi", None)

    assert pin is not None
    assert pin(SimpleNamespace(SetThreadDpiAwarenessContext=Setter())) is True
    assert contexts == [ctypes.c_void_p(-4).value]


def test_list_windows_reads_physical_rectangles_after_dpi_pin(monkeypatch, native):
    dpi = {"physical": False}

    def pin():
        dpi["physical"] = True
        return True

    def list_windows():
        region = (
            PixelRegion(2560, 0, 2560, 2076)
            if dpi["physical"]
            else PixelRegion(1463, 0, 1463, 1186)
        )
        return [_NativeWindow(10, "ChatGPT", "C:/Apps/ChatGPT.exe", region, True)]

    monkeypatch.setattr(desktop_module, "_pin_thread_v2_dpi", pin, raising=False)
    monkeypatch.setattr(native, "list_windows", list_windows)

    windows = WindowsDesktop(native).list_windows()

    assert windows[0].region == PixelRegion(2560, 0, 2560, 2076)


def test_capture_uses_physical_pixel_context(monkeypatch, native):
    dpi = {"physical": False}

    def pin():
        dpi["physical"] = True
        return True

    def capture(bounding_box):
        del bounding_box
        return "physical" if dpi["physical"] else "virtualized"

    monkeypatch.setattr(desktop_module, "_pin_thread_v2_dpi", pin, raising=False)
    monkeypatch.setattr(native, "capture", capture)

    result = WindowsDesktop(native).capture(PixelRegion(2560, 0, 2560, 2076))

    assert result == "physical"


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


def test_accessibility_tree_raises_typed_unavailable_error(native, desktop_window):
    native.accessibility_error = RuntimeError("UI Automation unavailable")
    expected_type = getattr(desktop_module, "AccessibilityUnavailable", RuntimeError)

    with pytest.raises(expected_type) as raised:
        WindowsDesktop(native).accessibility_tree(desktop_window)

    assert type(raised.value).__name__ == "AccessibilityUnavailable"
    assert "accessibility" in str(raised.value).casefold()


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


def test_native_activation_does_not_restore_or_move_visible_window(monkeypatch):
    region = [PixelRegion(2560, 0, 2560, 2076)]
    calls = mock_native_activation(
        monkeypatch,
        10,
        lambda: region.__setitem__(0, PixelRegion(2000, 35, 2762, 2079)),
    )

    assert _WindowsNative().activate(10) is True
    assert calls == [("SetForegroundWindow", 10)]
    assert region[0] == PixelRegion(2560, 0, 2560, 2076)


def test_native_activation_rejects_a_different_foreground_window(monkeypatch):
    mock_native_activation(monkeypatch, 11)

    assert _WindowsNative().activate(10) is False


def _capture_send_input(monkeypatch, results):
    class MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class KeyboardInput(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class InputUnion(ctypes.Union):
        _fields_ = [("mi", MouseInput), ("ki", KeyboardInput)]

    class Input(ctypes.Structure):
        _anonymous_ = ("input",)
        _fields_ = [("type", ctypes.c_ulong), ("input", InputUnion)]

    batches = []

    def send_input(count, inputs, size):
        assert size == ctypes.sizeof(Input)
        raw = ctypes.string_at(ctypes.addressof(inputs), count * size)
        batches.append(
            [
                (
                    item.type,
                    item.ki.wVk,
                    item.ki.dwFlags,
                )
                for item in (
                    Input.from_buffer_copy(raw, offset * size)
                    for offset in range(count)
                )
            ]
        )
        return results.pop(0)

    user32 = SimpleNamespace(SendInput=Win32Function(send_input))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setitem(
        __import__("sys").modules,
        "uiautomation",
        SimpleNamespace(SendKeys=lambda _: None),
    )
    return batches


def test_native_send_input_pastes_then_submits_once(monkeypatch):
    batches = _capture_send_input(monkeypatch, [4, 2])
    native = _WindowsNative()
    foreground = iter([True, True])
    monkeypatch.setattr(native, "is_foreground", lambda _: next(foreground))

    assert native.send_paste_and_submit(10) is True
    assert batches == [
        [(1, 0x11, 0), (1, 0x56, 0), (1, 0x56, 2), (1, 0x11, 2)],
        [(1, 0x0D, 0), (1, 0x0D, 2)],
    ]


@pytest.mark.parametrize(
    ("results", "expected_batches"),
    [
        ([0], [[(1, 0x11, 0), (1, 0x56, 0), (1, 0x56, 2), (1, 0x11, 2)]]),
        (
            [1, 0],
            [
                [(1, 0x11, 0), (1, 0x56, 0), (1, 0x56, 2), (1, 0x11, 2)],
                [(1, 0x11, 2)],
            ],
        ),
        (
            [2, 0, 1],
            [
                [(1, 0x11, 0), (1, 0x56, 0), (1, 0x56, 2), (1, 0x11, 2)],
                [(1, 0x56, 2)],
                [(1, 0x11, 2)],
            ],
        ),
        (
            [3, 0],
            [
                [(1, 0x11, 0), (1, 0x56, 0), (1, 0x56, 2), (1, 0x11, 2)],
                [(1, 0x11, 2)],
            ],
        ),
    ],
)
def test_native_partial_paste_input_does_not_submit(
    monkeypatch, results, expected_batches
):
    batches = _capture_send_input(monkeypatch, results)
    native = _WindowsNative()
    monkeypatch.setattr(native, "is_foreground", lambda _: True)

    assert native.send_paste_and_submit(10) is False
    assert batches == expected_batches


@pytest.mark.parametrize(
    ("results", "expected_batches"),
    [
        (
            [4, 0],
            [
                [(1, 0x11, 0), (1, 0x56, 0), (1, 0x56, 2), (1, 0x11, 2)],
                [(1, 0x0D, 0), (1, 0x0D, 2)],
            ],
        ),
        (
            [4, 1, 0],
            [
                [(1, 0x11, 0), (1, 0x56, 0), (1, 0x56, 2), (1, 0x11, 2)],
                [(1, 0x0D, 0), (1, 0x0D, 2)],
                [(1, 0x0D, 2)],
            ],
        ),
    ],
)
def test_native_partial_enter_input_releases_enter(
    monkeypatch, results, expected_batches
):
    batches = _capture_send_input(monkeypatch, results)
    native = _WindowsNative()
    foreground = iter([True, True])
    monkeypatch.setattr(native, "is_foreground", lambda _: next(foreground))

    assert native.send_paste_and_submit(10) is False
    assert batches == expected_batches


def test_paste_input_failure_reports_neutral_error(monkeypatch, native, desktop_window):
    native.foreground_handle = desktop_window.native_handle
    monkeypatch.setattr(desktop_module, "_pin_thread_v2_dpi", lambda: True)
    monkeypatch.setattr(native, "send_paste_and_submit", lambda _: False)

    with pytest.raises(InputUnavailable, match="input injection failed"):
        WindowsDesktop(native).paste_and_submit(desktop_window, PixelPoint(13, 14), "hello")


def test_native_foreground_loss_after_paste_does_not_submit(monkeypatch):
    batches = _capture_send_input(monkeypatch, [4])
    native = _WindowsNative()
    foreground = iter([True, False])
    monkeypatch.setattr(native, "is_foreground", lambda _: next(foreground))

    assert native.send_paste_and_submit(10) is False
    assert batches == [[(1, 0x11, 0), (1, 0x56, 0), (1, 0x56, 2), (1, 0x11, 2)]]


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
