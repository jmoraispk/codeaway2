from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agents import SurfaceMap
from .desktop import FractionalRegion


@dataclass(frozen=True)
class WindowHint:
    process_path: str
    title_hint: str


@dataclass(frozen=True)
class AppConfig:
    bind_ip: str = "127.0.0.1"
    port: int = 8765
    selected_agent: str | None = None
    selected_window: WindowHint | None = None
    surfaces: SurfaceMap | None = None

    @property
    def setup_complete(self) -> bool:
        return (
            self.selected_agent is not None
            and self.selected_window is not None
            and self.surfaces is not None
        )


@dataclass(frozen=True)
class ConfigLoad:
    config: AppConfig
    warnings: tuple[str, ...]


def default_config_path() -> Path:
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "CodeAway" / "config.json"
    return Path.home() / ".config" / "codeaway" / "config.json"


def _surface_values(surface: FractionalRegion) -> list[float]:
    return [surface.x, surface.y, surface.width, surface.height]


def _config_values(config: AppConfig) -> dict[str, Any]:
    selected_window = None
    if config.selected_window is not None:
        selected_window = {
            "process_path": config.selected_window.process_path,
            "title_hint": config.selected_window.title_hint,
        }

    surfaces = None
    if config.surfaces is not None:
        surfaces = {
            "sidebar": _surface_values(config.surfaces.sidebar),
            "conversation": _surface_values(config.surfaces.conversation),
            "composer": _surface_values(config.surfaces.composer),
        }

    return {
        "bind_ip": config.bind_ip,
        "port": config.port,
        "selected_agent": config.selected_agent,
        "selected_window": selected_window,
        "surfaces": surfaces,
    }


def save_config(path: str | os.PathLike[str], config: AppConfig) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.tmp")

    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(_config_values(config), handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)


def _number(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    return value


def _surface(value: Any) -> FractionalRegion:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("surface must be an array of four numbers")
    return FractionalRegion(
        _number(value[0], "x"),
        _number(value[1], "y"),
        _number(value[2], "width"),
        _number(value[3], "height"),
    )


def _parse_config(value: Any) -> AppConfig:
    if not isinstance(value, dict):
        raise ValueError("configuration must be an object")

    bind_ip = value.get("bind_ip", AppConfig.bind_ip)
    if not isinstance(bind_ip, str):
        raise ValueError("bind_ip must be a string")

    port = value.get("port", AppConfig.port)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")

    selected_agent = value.get("selected_agent")
    if selected_agent is not None and not isinstance(selected_agent, str):
        raise ValueError("selected_agent must be a string or null")

    selected_window_value = value.get("selected_window")
    selected_window = None
    if selected_window_value is not None:
        if not isinstance(selected_window_value, dict):
            raise ValueError("selected_window must be an object or null")
        process_path = selected_window_value.get("process_path")
        title_hint = selected_window_value.get("title_hint")
        if not isinstance(process_path, str) or not isinstance(title_hint, str):
            raise ValueError("window hints must be strings")
        selected_window = WindowHint(process_path, title_hint)

    surfaces_value = value.get("surfaces")
    surfaces = None
    if surfaces_value is not None:
        if not isinstance(surfaces_value, dict):
            raise ValueError("surfaces must be an object or null")
        surfaces = SurfaceMap(
            sidebar=_surface(surfaces_value["sidebar"]),
            conversation=_surface(surfaces_value["conversation"]),
            composer=_surface(surfaces_value["composer"]),
        )

    return AppConfig(bind_ip, port, selected_agent, selected_window, surfaces)


def load_config(path: str | os.PathLike[str]) -> ConfigLoad:
    source = Path(path)
    if not source.exists():
        return ConfigLoad(AppConfig(), ())
    try:
        with source.open("r", encoding="utf-8") as handle:
            config = _parse_config(json.load(handle))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return ConfigLoad(AppConfig(), ("Invalid configuration; using defaults.",))
    return ConfigLoad(config, ())
