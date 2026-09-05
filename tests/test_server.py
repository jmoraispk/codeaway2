import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from http.server import ThreadingHTTPServer
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from PIL import Image

import codeaway.desktop as desktop_module

from codeaway.agents import (
    AgentRegistry,
    AgentSnapshot,
    AgentTarget,
    ClickAction,
    NavigationAction,
    ProjectSnapshot,
    SurfaceMap,
    TaskSnapshot,
)
from codeaway.config import AppConfig, load_config
from codeaway.desktop import DesktopWindow, FractionalRegion, PixelRegion
from codeaway.server import AppState, Application, make_handler


@dataclass
class FakeDesktop:
    windows: list[DesktopWindow]
    capture_calls: list[PixelRegion] = field(default_factory=list)
    capture_color: str = "#123456"

    def list_windows(self):
        return list(self.windows)

    def capture(self, region):
        self.capture_calls.append(region)
        return Image.new("RGB", (region.width, region.height), self.capture_color)


@dataclass
class FakeAgent:
    id: str = "fake"
    calls: list[tuple[object, ...]] = field(default_factory=list)

    def matches(self, window):
        return window.process_path.endswith("agent.exe")

    def default_surfaces(self, window):
        del window
        return SurfaceMap(
            FractionalRegion(0, 0, 0.2, 1),
            FractionalRegion(0.2, 0, 0.8, 0.8),
            FractionalRegion(0.3, 0.8, 0.6, 0.2),
        )

    def inspect(self, desktop, target):
        del desktop, target
        return AgentSnapshot(
            available=True,
            source="fake",
            projects=(
                ProjectSnapshot(
                    "Project",
                    None,
                    False,
                    "idle",
                    True,
                    (TaskSnapshot("Task", "idle"),),
                ),
            ),
            captured_at="2026-09-04T00:00:00+00:00",
        )

    def navigate(self, desktop, target, action):
        del desktop, target
        self.calls.append(("navigate", action))

    def click(self, desktop, target, action):
        del desktop, target
        self.calls.append(("click", action))

    def scroll(self, desktop, target, amount):
        del desktop, target
        self.calls.append(("scroll", amount))

    def send(self, desktop, target, text):
        del desktop, target
        self.calls.append(("send", text))


@pytest.fixture
def selected_window():
    return DesktopWindow(
        "window-1",
        42,
        "Agent Window",
        "C:/Apps/agent.exe",
        PixelRegion(100, 50, 1000, 800),
    )


@pytest.fixture
def app(tmp_path, selected_window):
    desktop = FakeDesktop([selected_window])
    agent = FakeAgent()
    target = AgentTarget("fake", selected_window, agent.default_surfaces(selected_window))
    application = Application(
        AppState(AppConfig(), target),
        AgentRegistry([agent]),
        {agent.id: agent},
        desktop,
        tmp_path / "config.json",
    )
    application.fake_desktop = desktop
    application.fake_agent = agent
    return application


def payload(response):
    return json.loads(response.body)


def json_headers(**extra):
    return {"Host": "127.0.0.1:8765", "Content-Type": "application/json", **extra}


@contextmanager
def running_server(application):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(application))
    host, port = server.server_address
    application.state.config = replace(
        application.state.config, bind_ip=host, port=port
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_status_reports_selection_readiness_and_revision(app):
    response = app.dispatch("GET", "/api/status", {}, b"")

    assert response.status == 200
    assert payload(response) == {
        "bind_ip": "127.0.0.1",
        "port": 8765,
        "ready": False,
        "revision": 0,
        "setup_complete": False,
        "target": {"agent_id": "fake", "title": "Agent Window"},
    }


def test_status_advances_revision_when_conversation_pixels_change_without_action(app):
    first = payload(app.dispatch("GET", "/api/status", {}, b""))
    unchanged = payload(app.dispatch("GET", "/api/status", {}, b""))
    app.fake_desktop.capture_color = "#abcdef"
    changed = payload(app.dispatch("GET", "/api/status", {}, b""))
    capture_count = len(app.fake_desktop.capture_calls)

    screenshot = app.dispatch("GET", "/api/screenshot/conversation", {}, b"")

    assert [first["revision"], unchanged["revision"], changed["revision"]] == [
        0,
        0,
        1,
    ]
    assert len(app.fake_desktop.capture_calls) == capture_count
    with Image.open(BytesIO(screenshot.body)) as image:
        assert image.getpixel((0, 0)) == (171, 205, 239)

    action = app.dispatch(
        "POST", "/api/action", json_headers(), b'{"kind":"scroll","amount":-2}'
    )
    app.fake_desktop.capture_color = "#fedcba"
    refreshed = app.dispatch("GET", "/api/screenshot/conversation", {}, b"")

    assert payload(action)["revision"] == 2
    assert len(app.fake_desktop.capture_calls) == capture_count + 1
    with Image.open(BytesIO(refreshed.body)) as image:
        assert image.getpixel((0, 0)) == (254, 220, 186)


def test_windows_returns_compatible_discovery_results(app):
    response = app.dispatch("GET", "/api/windows", {}, b"")

    assert response.status == 200
    assert payload(response)["windows"] == [
        {
            "agent_id": "fake",
            "current": True,
            "id": "window-1",
            "process_path": "C:/Apps/agent.exe",
            "surfaces": {
                "sidebar": [0, 0, 0.2, 1],
                "conversation": [0.2, 0, 0.8, 0.8],
                "composer": [0.3, 0.8, 0.6, 0.2],
            },
            "title": "Agent Window",
        }
    ]


def test_windows_marks_current_target_and_returns_its_saved_surfaces(app):
    saved = SurfaceMap(
        FractionalRegion(0, 0, 0.25, 1),
        FractionalRegion(0.25, 0, 0.75, 0.7),
        FractionalRegion(0.35, 0.7, 0.5, 0.25),
    )
    app.state.target = AgentTarget("fake", app.state.target.window, saved)
    app.fake_desktop.windows.append(
        DesktopWindow(
            "window-2",
            43,
            "Other Agent Window",
            "C:/Apps/agent.exe",
            PixelRegion(200, 100, 800, 600),
        )
    )

    windows = payload(app.dispatch("GET", "/api/windows", {}, b""))["windows"]

    assert [(window["title"], window["current"]) for window in windows] == [
        ("Agent Window", True),
        ("Other Agent Window", False),
    ]
    assert windows[0]["surfaces"] == {
        "sidebar": [0, 0, 0.25, 1],
        "conversation": [0.25, 0, 0.75, 0.7],
        "composer": [0.35, 0.7, 0.5, 0.25],
    }


def test_selecting_the_current_window_preserves_saved_surfaces(app):
    saved = SurfaceMap(
        FractionalRegion(0, 0, 0.25, 1),
        FractionalRegion(0.25, 0, 0.75, 0.7),
        FractionalRegion(0.35, 0.7, 0.5, 0.25),
    )
    app.state.target = AgentTarget("fake", app.state.target.window, saved)
    window_id = payload(app.dispatch("GET", "/api/windows", {}, b""))["windows"][0]["id"]

    response = app.dispatch(
        "POST",
        "/api/select",
        json_headers(),
        json.dumps({"window_id": window_id}).encode(),
    )

    assert response.status == 200
    assert app.state.target.surfaces == saved


def test_select_chooses_a_discovered_window(app):
    app.state.target = None

    response = app.dispatch(
        "POST", "/api/select", json_headers(), b'{"window_id":"window-1"}'
    )

    assert response.status == 200
    assert app.state.target.window.id == "window-1"
    assert app.state.revision == 1


def test_select_resolves_a_window_even_when_discovery_ids_are_ephemeral(app):
    windows = payload(app.dispatch("GET", "/api/windows", {}, b""))["windows"]
    old_id = windows[0]["id"]
    previous = app.fake_desktop.windows[0]
    app.fake_desktop.windows = [
        DesktopWindow(
            "window-new-id",
            previous.native_handle,
            previous.title,
            previous.process_path,
            previous.region,
        )
    ]

    response = app.dispatch(
        "POST",
        "/api/select",
        json_headers(),
        json.dumps({"window_id": old_id}).encode(),
    )

    assert response.status == 200
    assert app.state.target.window.id == "window-new-id"


def test_select_rejects_replacement_window_with_same_executable(app):
    windows = payload(app.dispatch("GET", "/api/windows", {}, b""))["windows"]
    old_id = windows[0]["id"]
    app.state.target = None
    app.fake_desktop.windows = [
        DesktopWindow(
            "replacement",
            99,
            "Different Agent Window",
            "C:/Apps/agent.exe",
            PixelRegion(100, 50, 1000, 800),
        )
    ]

    response = app.dispatch(
        "POST",
        "/api/select",
        json_headers(),
        json.dumps({"window_id": old_id}).encode(),
    )

    assert response.status == 409
    assert app.state.target is None


def test_cross_origin_action_is_rejected(app):
    response = app.dispatch(
        "POST",
        "/api/action",
        {
            "Host": "127.0.0.1:8765",
            "Origin": "http://evil.example",
            "Content-Type": "application/json",
        },
        b'{"kind":"scroll","amount":-2}',
    )

    assert response.status == 403
    assert payload(response)["error"]["code"] == "origin_mismatch"
    assert app.fake_agent.calls == []


def test_matching_host_and_origin_are_rejected_when_authority_is_not_configured(app):
    response = app.dispatch(
        "POST",
        "/api/action",
        {
            "Host": "attacker.example:8765",
            "Origin": "http://attacker.example:8765",
            "Content-Type": "application/json",
        },
        b'{"kind":"scroll","amount":-2}',
    )

    assert response.status == 403
    assert payload(response)["error"]["code"] == "host_mismatch"
    assert app.fake_agent.calls == []


def test_malformed_origin_returns_structured_forbidden_response(app):
    response = app.dispatch(
        "POST",
        "/api/action",
        json_headers(Origin="http://["),
        b'{"kind":"scroll","amount":-2}',
    )

    assert response.status == 403
    assert payload(response)["error"]["code"] == "origin_mismatch"
    assert app.fake_agent.calls == []


@pytest.mark.parametrize("origin", ["https://127.0.0.1:8765", "not-an-origin"])
def test_non_http_or_malformed_origin_is_rejected(app, origin):
    response = app.dispatch(
        "POST",
        "/api/action",
        json_headers(Origin=origin),
        b'{"kind":"scroll","amount":-2}',
    )

    assert response.status == 403


def test_same_http_origin_with_json_charset_is_accepted(app):
    response = app.dispatch(
        "POST",
        "/api/action",
        {
            "Host": "127.0.0.1:8765",
            "Origin": "http://127.0.0.1:8765",
            "Content-Type": "application/json; charset=utf-8",
        },
        b'{"kind":"scroll","amount":-2}',
    )

    assert response.status == 200


def test_json_content_type_rejects_parameters_other_than_optional_charset(app):
    response = app.dispatch(
        "POST",
        "/api/action",
        {
            "Host": "127.0.0.1:8765",
            "Content-Type": "application/json; boundary=something",
        },
        b'{"kind":"scroll","amount":-2}',
    )

    assert response.status == 415
    assert app.fake_agent.calls == []


def test_state_changing_route_rejects_wrong_content_type(app):
    response = app.dispatch(
        "POST", "/api/action", {"Content-Type": "text/plain"}, b"{}"
    )

    assert response.status == 415
    assert payload(response)["error"]["code"] == "unsupported_media_type"


def test_state_changing_route_rejects_malformed_json(app):
    response = app.dispatch(
        "POST", "/api/action", json_headers(), b'{"kind":'
    )

    assert response.status == 400
    assert payload(response)["error"]["code"] == "invalid_json"


def test_json_integer_over_interpreter_limit_returns_structured_bad_request(app):
    body = b'{"kind":"scroll","amount":' + b"9" * 5000 + b"}"

    response = app.dispatch("POST", "/api/action", json_headers(), body)

    assert response.status == 400
    assert payload(response)["error"]["code"] == "invalid_json"
    assert app.fake_agent.calls == []


def test_state_changing_route_rejects_body_over_64_kib_before_decoding(app):
    response = app.dispatch(
        "POST", "/api/action", json_headers(), b"{" + b"x" * 65536
    )

    assert response.status == 413
    assert payload(response)["error"]["code"] == "body_too_large"


def test_body_at_64_kib_is_not_rejected_as_too_large(app):
    body = b'{"kind":"send","text":"' + b"x" * (65536 - 25) + b'"}'
    assert len(body) == 65536

    response = app.dispatch("POST", "/api/action", json_headers(), body)

    assert response.status == 200


def test_fixed_asset_map_rejects_traversal(app):
    assert app.dispatch("GET", "/../../README.md", {}, b"").status == 404


def test_setup_resource_contains_the_capture_editor_and_assets(app):
    response = app.dispatch("GET", "/setup", {}, b"")

    assert response.status == 200
    page = response.body.decode("utf-8")
    assert 'data-region="sidebar"' in page
    assert 'data-region="conversation"' in page
    assert 'data-region="composer"' in page
    assert "/app.js" in page
    assert "/style.css" in page


def test_phone_resource_contains_the_workspace_in_document_order(app):
    response = app.dispatch("GET", "/", {}, b"")

    assert response.status == 200
    page = response.body.decode("utf-8")
    status = page.index('id="phone-status"')
    navigator = page.index('id="navigator"')
    conversation = page.index('id="conversation"')
    composer = page.index('id="composer"')
    assert status < navigator < conversation < composer
    assert "/app.js" in page
    assert "/style.css" in page


@pytest.mark.parametrize("path", ["/api/screenshot/composer", "/api/screenshot/nope"])
def test_screenshot_rejects_unapproved_surface_names(app, path):
    assert app.dispatch("GET", path, {}, b"").status == 404


def test_screenshot_returns_png_for_calibrated_conversation(app):
    response = app.dispatch("GET", "/api/screenshot/conversation", {}, b"")

    assert response.status == 200
    assert response.content_type == "image/png"
    assert response.body.startswith(b"\x89PNG\r\n\x1a\n")
    assert app.fake_desktop.capture_calls == [PixelRegion(300, 50, 800, 640)]


def test_disappeared_target_returns_conflict_without_dispatching_action(app):
    app.fake_desktop.windows = []

    response = app.dispatch(
        "POST", "/api/action", json_headers(), b'{"kind":"scroll","amount":-2}'
    )

    assert response.status == 409
    assert payload(response)["error"]["code"] == "target_unavailable"
    assert app.fake_agent.calls == []


def test_replacement_window_with_same_executable_is_not_targeted(app):
    app.fake_desktop.windows = [
        DesktopWindow(
            "replacement",
            99,
            "Different Agent Window",
            "C:/Apps/agent.exe",
            PixelRegion(100, 50, 1000, 800),
        )
    ]

    response = app.dispatch(
        "POST", "/api/action", json_headers(), b'{"kind":"scroll","amount":-2}'
    )

    assert response.status == 409
    assert app.fake_agent.calls == []


def test_action_dispatch_increments_revision(app):
    response = app.dispatch(
        "POST", "/api/action", json_headers(), b'{"kind":"scroll","amount":-2}'
    )

    assert response.status == 200
    assert payload(response) == {"revision": 1}
    assert app.state.revision == 1
    assert app.fake_agent.calls == [("scroll", -2)]


def test_backend_io_failure_returns_structured_service_error(app):
    def fail_scroll(desktop, target, amount):
        del desktop, target, amount
        raise OSError("clipboard unavailable")

    app.fake_agent.scroll = fail_scroll

    response = app.dispatch(
        "POST", "/api/action", json_headers(), b'{"kind":"scroll","amount":-2}'
    )

    assert response.status == 503
    assert payload(response)["error"]["code"] == "backend_error"
    assert app.state.revision == 0


def test_click_rejects_invalid_surface_without_dispatching(app):
    response = app.dispatch(
        "POST",
        "/api/action",
        json_headers(),
        b'{"kind":"click","surface":"composer","x":0.5,"y":0.5}',
    )

    assert response.status == 400
    assert app.fake_agent.calls == []


def test_click_dispatches_validated_fractional_coordinates(app):
    response = app.dispatch(
        "POST",
        "/api/action",
        json_headers(),
        b'{"kind":"click","surface":"conversation","x":0.25,"y":0.75}',
    )

    assert response.status == 200
    assert app.fake_agent.calls == [("click", ClickAction("conversation", 0.25, 0.75))]


@pytest.mark.parametrize("amount", [0, -13, 13])
def test_scroll_rejects_zero_or_out_of_range_amounts(app, amount):
    response = app.dispatch(
        "POST",
        "/api/action",
        json_headers(),
        json.dumps({"kind": "scroll", "amount": amount}).encode(),
    )

    assert response.status == 400
    assert payload(response)["error"]["code"] == "invalid_action"
    assert app.fake_agent.calls == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            b'{"kind":"navigate","target":"project","project":"Project","expanded":false}',
            NavigationAction("project", "Project", expanded=False),
        ),
        (
            b'{"kind":"navigate","target":"task","project":"Project","title":"Task"}',
            NavigationAction("task", "Project", title="Task"),
        ),
    ],
)
def test_navigate_dispatches_validated_semantic_action(app, body, expected):
    response = app.dispatch("POST", "/api/action", json_headers(), body)

    assert response.status == 200
    assert app.fake_agent.calls == [("navigate", expected)]


def test_navigator_serializes_agent_snapshot(app):
    response = app.dispatch("GET", "/api/navigator", {}, b"")

    assert response.status == 200
    assert payload(response)["projects"][0]["tasks"] == [
        {"selected": False, "state": "idle", "title": "Task", "worktree": False}
    ]


def test_accessibility_failure_returns_unavailable_navigator_without_blocking_pixels(
    app,
):
    error_type = getattr(desktop_module, "AccessibilityUnavailable", RuntimeError)

    def fail_inspect(desktop, target):
        del desktop, target
        raise error_type("UI Automation unavailable")

    app.fake_agent.inspect = fail_inspect

    navigator = app.dispatch("GET", "/api/navigator", {}, b"")
    screenshot = app.dispatch("GET", "/api/screenshot/conversation", {}, b"")
    click = app.dispatch(
        "POST",
        "/api/action",
        json_headers(),
        b'{"kind":"click","surface":"conversation","x":0.5,"y":0.5}',
    )

    assert navigator.status == 200
    assert payload(navigator)["available"] is False
    assert payload(navigator)["projects"] == []
    assert "accessibility" in payload(navigator)["error"].casefold()
    assert screenshot.status == 200
    assert click.status == 200


def test_concurrent_navigator_reads_are_serialized(app):
    first_entered = threading.Event()
    release_first = threading.Event()
    inspection_lock = threading.Lock()
    active_inspections = 0
    overlap = False
    results = [None, None]
    original_inspect = app.fake_agent.inspect

    def blocked_inspect(desktop, target):
        nonlocal active_inspections, overlap
        with inspection_lock:
            active_inspections += 1
            overlap = overlap or active_inspections > 1
            first = not first_entered.is_set()
            if first:
                first_entered.set()
        try:
            if first:
                assert release_first.wait(2)
            return original_inspect(desktop, target)
        finally:
            with inspection_lock:
                active_inspections -= 1

    def read_navigator(index):
        results[index] = app.dispatch("GET", "/api/navigator", {}, b"")

    app.fake_agent.inspect = blocked_inspect
    threads = [
        threading.Thread(target=read_navigator, args=(index,)) for index in range(2)
    ]
    threads[0].start()
    try:
        assert first_entered.wait(2)
        threads[1].start()
        threads[1].join(0.1)
        assert threads[1].is_alive()
    finally:
        release_first.set()
        for thread in threads:
            thread.join(2)

    assert all(not thread.is_alive() for thread in threads)
    assert [response.status for response in results] == [200, 200]
    assert overlap is False


@pytest.mark.parametrize("sensitive_route", ["navigator", "select", "calibration"])
def test_target_sensitive_routes_wait_for_an_active_action(app, sensitive_route):
    windows = payload(app.dispatch("GET", "/api/windows", {}, b""))["windows"]
    action_entered = threading.Event()
    release_action = threading.Event()
    sensitive_started = threading.Event()
    sensitive_finished = threading.Event()
    results = {}

    def blocked_scroll(desktop, target, amount):
        del desktop, target, amount
        action_entered.set()
        assert release_action.wait(2)

    def run_action():
        results["action"] = app.dispatch(
            "POST", "/api/action", json_headers(), b'{"kind":"scroll","amount":-2}'
        )

    requests = {
        "navigator": ("GET", "/api/navigator", {}, b""),
        "select": (
            "POST",
            "/api/select",
            json_headers(),
            json.dumps({"window_id": windows[0]["id"]}).encode(),
        ),
        "calibration": (
            "PUT",
            "/api/calibration",
            json_headers(),
            json.dumps(
                {
                    "surfaces": {
                        "sidebar": [0, 0, 0.2, 1],
                        "conversation": [0.2, 0, 0.8, 0.8],
                        "composer": [0.3, 0.8, 0.6, 0.2],
                    }
                }
            ).encode(),
        ),
    }

    def run_sensitive():
        sensitive_started.set()
        results["sensitive"] = app.dispatch(*requests[sensitive_route])
        sensitive_finished.set()

    app.fake_agent.scroll = blocked_scroll
    action_thread = threading.Thread(target=run_action)
    sensitive_thread = threading.Thread(target=run_sensitive)
    action_thread.start()
    try:
        assert action_entered.wait(2)
        sensitive_thread.start()
        assert sensitive_started.wait(2)
        assert not sensitive_finished.wait(0.1)
    finally:
        release_action.set()
        action_thread.join(2)
        sensitive_thread.join(2)

    assert not action_thread.is_alive()
    assert not sensitive_thread.is_alive()
    assert results["action"].status == 200
    assert results["sensitive"].status == 200


def test_target_resolution_does_not_overwrite_a_concurrent_selection(app):
    entered_resolution = threading.Event()
    release_resolution = threading.Event()
    original_window = app.fake_desktop.windows[0]
    result = {}

    def blocked_list_windows():
        entered_resolution.set()
        assert release_resolution.wait(2)
        return list(app.fake_desktop.windows)

    app.fake_desktop.list_windows = blocked_list_windows
    request_thread = threading.Thread(
        target=lambda: result.update(
            response=app.dispatch("GET", "/api/navigator", {}, b"")
        )
    )
    request_thread.start()
    try:
        assert entered_resolution.wait(2)
        replacement_window = DesktopWindow(
            "window-2",
            77,
            "Second Agent Window",
            "C:/Apps/agent.exe",
            PixelRegion(100, 50, 1000, 800),
        )
        replacement_target = AgentTarget(
            "fake", replacement_window, app.state.target.surfaces
        )
        app.fake_desktop.windows = [original_window, replacement_window]
        with app.state.state_lock:
            app.state.target = replacement_target
            app.state.revision += 1
    finally:
        release_resolution.set()
        request_thread.join(2)

    assert not request_thread.is_alive()
    assert result["response"].status == 409
    assert app.state.target is replacement_target


def test_calibration_requires_all_three_surfaces_without_saving(app):
    response = app.dispatch(
        "PUT",
        "/api/calibration",
        json_headers(),
        b'{"surfaces":{"sidebar":[0,0,0.2,1]}}',
    )

    assert response.status == 400
    assert not app.config_path.exists()
    assert app.state.revision == 0


def test_calibration_uses_config_validation_without_saving_invalid_regions(app):
    response = app.dispatch(
        "PUT",
        "/api/calibration",
        json_headers(),
        json.dumps(
            {
                "surfaces": {
                    "sidebar": [0, 0, 0.2, 1],
                    "conversation": [0.2, 0, 0.9, 0.8],
                    "composer": [0.3, 0.8, 0.6, 0.2],
                }
            }
        ).encode(),
    )

    assert response.status == 400
    assert not app.config_path.exists()
    assert app.state.revision == 0


def test_calibration_atomically_saves_target_and_all_surfaces(app):
    response = app.dispatch(
        "PUT",
        "/api/calibration",
        json_headers(),
        json.dumps(
            {
                "surfaces": {
                    "sidebar": [0, 0, 0.25, 1],
                    "conversation": [0.25, 0, 0.75, 0.75],
                    "composer": [0.3, 0.75, 0.6, 0.25],
                }
            }
        ).encode(),
    )

    assert response.status == 200
    assert payload(response) == {"revision": 1}
    assert app.state.target.surfaces.sidebar == FractionalRegion(0, 0, 0.25, 1)
    saved = load_config(app.config_path).config
    assert saved.selected_agent == "fake"
    assert saved.selected_window.process_path == "C:/Apps/agent.exe"
    assert saved.selected_window.title_hint == "Agent Window"
    assert saved.surfaces == app.state.target.surfaces
    assert app.state.config == saved
    assert app.state.revision == 1


def test_dispatch_converts_unexpected_exception_to_structured_server_error(app):
    def fail_list_windows():
        raise RuntimeError("unexpected registry failure")

    app.fake_desktop.list_windows = fail_list_windows

    response = app.dispatch("GET", "/api/windows", {}, b"")

    assert response.status == 500
    assert payload(response)["error"]["code"] == "internal_error"


def test_http_handler_adapts_live_get_and_post_requests(app):
    with running_server(app) as url:
        with urlopen(f"{url}/api/status") as response:
            status_payload = json.load(response)
            assert response.headers["Content-Type"] == "application/json; charset=utf-8"
        request = Request(
            f"{url}/api/action",
            b'{"kind":"scroll","amount":-2}',
            {"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            action_payload = json.load(response)

    assert status_payload["revision"] == 0
    assert action_payload["revision"] == 1
    assert app.fake_agent.calls == [("scroll", -2)]


def test_http_handler_denies_framing(app):
    with running_server(app) as url:
        with urlopen(f"{url}/setup") as response:
            assert response.headers["X-Frame-Options"] == "DENY"
            assert response.headers["Content-Security-Policy"] == "frame-ancestors 'none'"


def test_http_handler_converts_unexpected_dispatch_exception_to_json(app):
    def fail_dispatch(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("unexpected dispatch failure")

    app.dispatch = fail_dispatch

    with running_server(app) as url:
        with pytest.raises(HTTPError) as raised:
            urlopen(f"{url}/api/status")
        error_payload = json.load(raised.value)

    assert raised.value.code == 500
    assert raised.value.headers["Content-Type"] == "application/json; charset=utf-8"
    assert error_payload["error"]["code"] == "internal_error"
