from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler
from importlib import resources
from io import BytesIO
from numbers import Real
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .agents import (
    AgentBackend,
    AgentRegistry,
    AgentTarget,
    ClickAction,
    NavigationAction,
    SurfaceMap,
    TargetUnavailable,
)
from .config import AppConfig, _parse_config, save_config
from .desktop import DesktopBackend, FractionalRegion, InputUnavailable


_MAX_JSON_BODY = 65_536
_STATIC_RESOURCES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/setup": ("setup.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
_SCREENSHOT_SURFACES = frozenset({"window", "sidebar", "conversation"})


@dataclass(frozen=True)
class Response:
    status: int
    content_type: str
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass
class AppState:
    config: AppConfig
    target: AgentTarget | None
    revision: int = 0
    state_lock: threading.RLock = field(default_factory=threading.RLock)
    action_lock: threading.Lock = field(default_factory=threading.Lock)


class Application:
    def __init__(
        self,
        state: AppState,
        registry: AgentRegistry,
        agents: Mapping[str, AgentBackend],
        desktop: DesktopBackend,
        config_path: str | Path,
    ) -> None:
        self.state = state
        self.registry = registry
        self.agents = dict(agents)
        self.desktop = desktop
        self.config_path = Path(config_path)
        self._discovered_targets: dict[str, AgentTarget] = {}

    @staticmethod
    def _json_response(status: int, value: Any) -> Response:
        return Response(
            status,
            "application/json; charset=utf-8",
            json.dumps(value, separators=(",", ":")).encode("utf-8"),
        )

    @classmethod
    def _error(cls, status: int, code: str, message: str) -> Response:
        return cls._json_response(
            status, {"error": {"code": code, "message": message}}
        )

    @staticmethod
    def _headers(headers: Mapping[str, str]) -> dict[str, str]:
        return {name.casefold(): value for name, value in headers.items()}

    def _request_json(
        self, headers: Mapping[str, str], body: bytes
    ) -> tuple[dict[str, Any] | None, Response | None]:
        normalized = self._headers(headers)
        content_type = normalized.get("content-type", "")
        content_type_parts = [part.strip() for part in content_type.split(";")]
        media_type = content_type_parts[0].casefold()
        parameters = content_type_parts[1:]
        charset_valid = not parameters
        if len(parameters) == 1:
            name, separator, value = parameters[0].partition("=")
            charset_valid = (
                separator == "="
                and name.strip().casefold() == "charset"
                and bool(value.strip())
            )
        if media_type != "application/json" or not charset_valid:
            return None, self._error(
                415, "unsupported_media_type", "Content-Type must be application/json."
            )
        if len(body) > _MAX_JSON_BODY:
            return None, self._error(
                413, "body_too_large", "JSON request body exceeds 65,536 bytes."
            )
        with self.state.state_lock:
            expected_authority = (
                f"{self.state.config.bind_ip}:{self.state.config.port}"
            ).casefold()
        host = normalized.get("host", "").strip().casefold()
        if host != expected_authority:
            return None, self._error(
                403,
                "host_mismatch",
                "Request Host must match the configured CodeAway address.",
            )
        origin = normalized.get("origin")
        if origin is not None:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                return None, self._error(
                    403,
                    "origin_mismatch",
                    "Request Origin must match the CodeAway Host.",
                )
            if (
                parsed.scheme.casefold() != "http"
                or not parsed.netloc
                or parsed.netloc.casefold() != expected_authority
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                return None, self._error(
                    403,
                    "origin_mismatch",
                    "Request Origin must match the CodeAway Host.",
                )
        try:
            value = json.loads(body)
        except (ValueError, RecursionError):
            return None, self._error(400, "invalid_json", "Request body is not valid JSON.")
        if not isinstance(value, dict):
            return None, self._error(400, "invalid_request", "JSON body must be an object.")
        return value, None

    @staticmethod
    def _surface_values(surface: FractionalRegion) -> list[int | float]:
        return [surface.x, surface.y, surface.width, surface.height]

    @classmethod
    def _surfaces_value(cls, surfaces: SurfaceMap) -> dict[str, list[int | float]]:
        return {
            "sidebar": cls._surface_values(surfaces.sidebar),
            "conversation": cls._surface_values(surfaces.conversation),
            "composer": cls._surface_values(surfaces.composer),
        }

    def _fixed_resource(self, path: str) -> Response:
        resource = _STATIC_RESOURCES.get(path)
        if resource is None:
            return self._error(404, "not_found", "Resource not found.")
        filename, content_type = resource
        try:
            body = resources.files("codeaway.web").joinpath(filename).read_bytes()
        except (ModuleNotFoundError, FileNotFoundError, TypeError):
            return self._error(404, "not_found", "Resource not found.")
        return Response(200, content_type, body)

    def _resolve_runtime_target(self, selected: AgentTarget) -> AgentTarget | None:
        backend = self._backend(selected)
        if backend is None:
            return None
        process_path = os.path.normcase(selected.window.process_path)
        window = next(
            (
                candidate
                for candidate in self.desktop.list_windows()
                if candidate.native_handle == selected.window.native_handle
                and os.path.normcase(candidate.process_path) == process_path
                and backend.matches(candidate)
            ),
            None,
        )
        if window is None:
            return None
        return AgentTarget(selected.agent_id, window, selected.surfaces)

    @staticmethod
    def _selection_token(target: AgentTarget | None) -> tuple[object, ...] | None:
        if target is None:
            return None
        return (
            target.agent_id,
            target.window.native_handle,
            os.path.normcase(target.window.process_path),
            target.surfaces,
        )

    @staticmethod
    def _same_window_identity(first: AgentTarget, second: AgentTarget) -> bool:
        return (
            first.agent_id == second.agent_id
            and first.window.native_handle == second.window.native_handle
            and os.path.normcase(os.path.normpath(first.window.process_path))
            == os.path.normcase(os.path.normpath(second.window.process_path))
        )

    def _current_target(self) -> AgentTarget | None:
        with self.state.state_lock:
            selected = self.state.target
            selection_token = self._selection_token(selected)
        if selected is None:
            return None
        current = self._resolve_runtime_target(selected)
        with self.state.state_lock:
            if self._selection_token(self.state.target) != selection_token:
                return None
            self.state.target = current
        return current

    def _backend(self, target: AgentTarget) -> AgentBackend | None:
        return self.agents.get(target.agent_id)

    def _status(self) -> Response:
        with self.state.state_lock:
            config = self.state.config
            target = self.state.target
            value = {
                "bind_ip": config.bind_ip,
                "port": config.port,
                "ready": config.setup_complete and target is not None,
                "revision": self.state.revision,
                "setup_complete": config.setup_complete,
                "target": (
                    None
                    if target is None
                    else {"agent_id": target.agent_id, "title": target.window.title}
                ),
            }
        return self._json_response(200, value)

    def _windows(self) -> Response:
        targets = self.registry.discover(self.desktop)
        with self.state.state_lock:
            current = self.state.target
            presented_targets = [
                AgentTarget(target.agent_id, target.window, current.surfaces)
                if current is not None
                and self._same_window_identity(target, current)
                else target
                for target in targets
            ]
            self._discovered_targets = {
                target.window.id: target for target in presented_targets
            }
        return self._json_response(
            200,
            {
                "windows": [
                    {
                        "agent_id": target.agent_id,
                        "current": (
                            current is not None
                            and self._same_window_identity(target, current)
                        ),
                        "id": target.window.id,
                        "process_path": target.window.process_path,
                        "surfaces": self._surfaces_value(target.surfaces),
                        "title": target.window.title,
                    }
                    for target in presented_targets
                ]
            },
        )

    def _select(self, value: dict[str, Any]) -> Response:
        with self.state.action_lock:
            return self._select_locked(value)

    def _select_locked(self, value: dict[str, Any]) -> Response:
        window_id = value.get("window_id")
        if not isinstance(window_id, str):
            return self._error(400, "invalid_request", "window_id must be a string.")
        with self.state.state_lock:
            selected = self._discovered_targets.get(window_id)
        if selected is None:
            discovered = self.registry.discover(self.desktop)
            with self.state.state_lock:
                self._discovered_targets = {
                    candidate.window.id: candidate for candidate in discovered
                }
            selected = next(
                (candidate for candidate in discovered if candidate.window.id == window_id),
                None,
            )
        if selected is None:
            return self._error(409, "target_unavailable", "Selected window is unavailable.")
        target = self._resolve_runtime_target(selected)
        if target is None:
            return self._error(409, "target_unavailable", "Selected window is unavailable.")
        with self.state.state_lock:
            self.state.target = target
            self.state.revision += 1
            revision = self.state.revision
        return self._json_response(200, {"revision": revision})

    def _screenshot(self, surface_name: str) -> Response:
        if surface_name not in _SCREENSHOT_SURFACES:
            return self._error(404, "not_found", "Screenshot surface not found.")
        target = self._current_target()
        if target is None:
            return self._error(409, "target_unavailable", "Selected window is unavailable.")
        region = (
            target.window.region
            if surface_name == "window"
            else getattr(target.surfaces, surface_name).resolve(target.window.region)
        )
        try:
            image = self.desktop.capture(region)
            output = BytesIO()
            image.save(output, format="PNG")
        except Exception:
            return self._error(503, "screenshot_failed", "Screenshot capture failed.")
        return Response(200, "image/png", output.getvalue(), {"Cache-Control": "no-store"})

    def _navigator(self) -> Response:
        with self.state.action_lock:
            return self._navigator_locked()

    def _navigator_locked(self) -> Response:
        target = self._current_target()
        if target is None:
            return self._error(409, "target_unavailable", "Selected window is unavailable.")
        backend = self._backend(target)
        if backend is None:
            return self._error(409, "target_unavailable", "Selected agent is unavailable.")
        try:
            snapshot = backend.inspect(self.desktop, target)
        except TargetUnavailable:
            return self._error(409, "target_unavailable", "Selected window is unavailable.")
        return self._json_response(200, asdict(snapshot))

    def _calibration(self, value: dict[str, Any]) -> Response:
        with self.state.action_lock:
            return self._calibration_locked(value)

    def _calibration_locked(self, value: dict[str, Any]) -> Response:
        surfaces = value.get("surfaces")
        if not isinstance(surfaces, dict) or set(surfaces) != {
            "sidebar",
            "conversation",
            "composer",
        }:
            return self._error(
                400,
                "invalid_calibration",
                "Calibration requires sidebar, conversation, and composer surfaces.",
            )
        target = self._current_target()
        if target is None:
            return self._error(409, "target_unavailable", "Selected window is unavailable.")
        with self.state.state_lock:
            current = self.state.config
        try:
            config = _parse_config(
                {
                    "bind_ip": current.bind_ip,
                    "port": current.port,
                    "selected_agent": target.agent_id,
                    "selected_window": {
                        "process_path": target.window.process_path,
                        "title_hint": target.window.title,
                    },
                    "surfaces": surfaces,
                }
            )
        except (KeyError, TypeError, ValueError):
            return self._error(400, "invalid_calibration", "Calibration is invalid.")
        calibrated_target = AgentTarget(target.agent_id, target.window, config.surfaces)
        with self.state.state_lock:
            try:
                save_config(self.config_path, config)
            except OSError:
                return self._error(500, "config_save_failed", "Configuration could not be saved.")
            self.state.config = config
            self.state.target = calibrated_target
            self.state.revision += 1
            revision = self.state.revision
        return self._json_response(200, {"revision": revision})

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError(f"{name} must be a number between 0 and 1")
        return float(value)

    def _perform_action(
        self, backend: AgentBackend, target: AgentTarget, value: dict[str, Any]
    ) -> None:
        kind = value.get("kind")
        if kind == "scroll":
            amount = value.get("amount")
            if (
                isinstance(amount, bool)
                or not isinstance(amount, int)
                or amount == 0
                or not -12 <= amount <= 12
            ):
                raise ValueError("amount must be a nonzero integer between -12 and 12")
            backend.scroll(self.desktop, target, amount)
            return
        if kind == "send":
            text = value.get("text")
            if not isinstance(text, str):
                raise ValueError("text must be a string")
            backend.send(self.desktop, target, text)
            return
        if kind == "click":
            surface = value.get("surface")
            if surface not in {"sidebar", "conversation"}:
                raise ValueError("surface must be sidebar or conversation")
            backend.click(
                self.desktop,
                target,
                ClickAction(
                    surface,
                    self._number(value.get("x"), "x"),
                    self._number(value.get("y"), "y"),
                ),
            )
            return
        if kind == "navigate":
            navigation_kind = value.get("target")
            project = value.get("project")
            if navigation_kind not in {"project", "task"} or not isinstance(project, str):
                raise ValueError("navigate requires a target and project")
            title = value.get("title")
            expanded = value.get("expanded")
            if navigation_kind == "task" and not isinstance(title, str):
                raise ValueError("task navigation requires a title")
            if navigation_kind == "project" and not isinstance(expanded, bool):
                raise ValueError("project navigation requires expanded")
            backend.navigate(
                self.desktop,
                target,
                NavigationAction(navigation_kind, project, title=title, expanded=expanded),
            )
            return
        raise ValueError("unknown action kind")

    def _action(self, value: dict[str, Any]) -> Response:
        with self.state.action_lock:
            target = self._current_target()
            if target is None:
                return self._error(409, "target_unavailable", "Selected window is unavailable.")
            backend = self._backend(target)
            if backend is None:
                return self._error(409, "target_unavailable", "Selected agent is unavailable.")
            try:
                self._perform_action(backend, target, value)
            except ValueError as error:
                return self._error(400, "invalid_action", str(error))
            except (InputUnavailable, TargetUnavailable):
                return self._error(409, "target_unavailable", "Selected window is unavailable.")
            with self.state.state_lock:
                self.state.revision += 1
                revision = self.state.revision
        return self._json_response(200, {"revision": revision})

    def _dispatch(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        route_path = urlsplit(path).path
        if method == "GET":
            if route_path == "/api/status":
                return self._status()
            if route_path == "/api/windows":
                return self._windows()
            if route_path == "/api/navigator":
                return self._navigator()
            prefix = "/api/screenshot/"
            if route_path.startswith(prefix):
                return self._screenshot(route_path[len(prefix) :])
            return self._fixed_resource(route_path)

        if method in {"POST", "PUT"}:
            value, error = self._request_json(headers, body)
            if error is not None:
                return error
            assert value is not None
            if method == "POST" and route_path == "/api/select":
                return self._select(value)
            if method == "POST" and route_path == "/api/action":
                return self._action(value)
            if method == "PUT" and route_path == "/api/calibration":
                return self._calibration(value)
        return self._error(404, "not_found", "Resource not found.")

    def dispatch(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        try:
            return self._dispatch(method, path, headers, body)
        except OSError:
            return self._error(503, "backend_error", "Backend operation failed.")
        except Exception:
            return self._error(500, "internal_error", "Internal server error.")


def make_handler(application: Application, logger: Any | None = None):
    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self) -> None:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 0:
                    raise ValueError
            except ValueError:
                response = application._error(
                    400, "invalid_request", "Content-Length must be a non-negative integer."
                )
            else:
                if content_length > _MAX_JSON_BODY:
                    body = b"\0" * (_MAX_JSON_BODY + 1)
                    self.close_connection = True
                else:
                    body = self.rfile.read(content_length)
                try:
                    response = application.dispatch(
                        self.command,
                        self.path,
                        dict(self.headers.items()),
                        body,
                    )
                except Exception:
                    if logger is not None:
                        logger.exception("Unhandled CodeAway request failure")
                    response = Application._error(
                        500, "internal_error", "Internal server error."
                    )
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(response.body)

        def do_GET(self) -> None:
            self._dispatch()

        def do_POST(self) -> None:
            self._dispatch()

        def do_PUT(self) -> None:
            self._dispatch()

        def log_message(self, format: str, *args: Any) -> None:
            if logger is not None:
                logger.info(format, *args)

    return Handler
