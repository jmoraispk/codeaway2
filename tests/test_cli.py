from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from codeaway.agents import AgentTarget, SurfaceMap
from codeaway.cli import StartResult, start
from codeaway.config import AppConfig, WindowHint, load_config, save_config
from codeaway.desktop import DesktopWindow, FractionalRegion, PixelRegion


def complete_config(bind_ip: str = "127.0.0.1") -> AppConfig:
    return AppConfig(
        bind_ip=bind_ip,
        port=8765,
        selected_agent="codex",
        selected_window=WindowHint(
            process_path=r"C:\Program Files\WindowsApps\OpenAI.Codex_1\Codex.exe",
            title_hint="ChatGPT",
        ),
        surfaces=SurfaceMap(
            sidebar=FractionalRegion(0, 0, 0.21, 1),
            conversation=FractionalRegion(0.21, 0.05, 0.79, 0.73),
            composer=FractionalRegion(0.32, 0.78, 0.56, 0.18),
        ),
    )


def codex_target(config: AppConfig | None = None) -> AgentTarget:
    selected = config or complete_config()
    assert selected.surfaces is not None
    return AgentTarget(
        "codex",
        DesktopWindow(
            "window-1",
            42,
            "ChatGPT",
            r"C:\Program Files\WindowsApps\OpenAI.Codex_1\Codex.exe",
            PixelRegion(100, 50, 1000, 800),
        ),
        selected.surfaces,
    )


@dataclass
class FakeRegistry:
    resolved_target: AgentTarget | None = None
    resolve_calls: list[tuple[object, ...]] = field(default_factory=list)

    def resolve(self, desktop, agent_id, process_path, title_hint, surfaces):
        self.resolve_calls.append(
            (desktop, agent_id, process_path, title_hint, surfaces)
        )
        return self.resolved_target


@dataclass
class FakeServer:
    server_address: tuple[str, int]
    raise_keyboard_interrupt: bool = False
    serve_calls: int = 0
    shutdown_calls: int = 0
    close_calls: int = 0

    def serve_forever(self):
        self.serve_calls += 1
        if self.raise_keyboard_interrupt:
            raise KeyboardInterrupt

    def shutdown(self):
        self.shutdown_calls += 1

    def server_close(self):
        self.close_calls += 1


@dataclass
class RuntimeHarness:
    config_path: Path
    registry: FakeRegistry = field(default_factory=FakeRegistry)
    desktop: object = field(default_factory=object)
    browser_urls: list[str] = field(default_factory=list)
    bind_failures: set[str] = field(default_factory=set)
    server_addresses: list[tuple[str, int]] = field(default_factory=list)
    servers: list[FakeServer] = field(default_factory=list)
    interrupt_server: bool = False
    desktop_error: Exception | None = None

    @property
    def config(self) -> AppConfig:
        return load_config(self.config_path).config

    @config.setter
    def config(self, value: AppConfig) -> None:
        save_config(self.config_path, value)

    def config_path_factory(self):
        return self.config_path

    def desktop_factory(self):
        if self.desktop_error is not None:
            raise self.desktop_error
        return self.desktop

    def registry_factory(self, agents):
        assert [agent.id for agent in agents] == ["codex"]
        return self.registry

    def server_factory(self, address, handler):
        del handler
        self.server_addresses.append(address)
        if address[0] in self.bind_failures:
            raise OSError(10049, "The requested address is not valid")
        server = FakeServer(address, self.interrupt_server)
        self.servers.append(server)
        return server

    def browser_open(self, url):
        self.browser_urls.append(url)
        return True


@pytest.fixture
def runtime(tmp_path):
    return RuntimeHarness(tmp_path / "config.json")


def test_valid_saved_target_and_calibration_do_not_open_setup(runtime):
    runtime.config = complete_config()
    runtime.registry.resolved_target = codex_target()

    result = start(["--no-serve"], runtime)

    assert result == StartResult(0, "http://127.0.0.1:8765/")
    assert runtime.browser_urls == []


def test_saved_target_resolution_uses_stable_hints_and_calibration(runtime):
    runtime.config = complete_config()
    runtime.registry.resolved_target = codex_target()

    start(["--no-serve"], runtime)

    assert runtime.registry.resolve_calls == [
        (
            runtime.desktop,
            "codex",
            r"C:\Program Files\WindowsApps\OpenAI.Codex_1\Codex.exe",
            "ChatGPT",
            runtime.config.surfaces,
        )
    ]


def test_first_run_opens_setup(runtime):
    runtime.config = AppConfig()

    start(["--no-serve"], runtime)

    assert runtime.browser_urls == ["http://127.0.0.1:8765/setup"]


def test_no_browser_suppresses_setup_browser_launch(runtime):
    runtime.config = AppConfig()

    start(["--no-browser", "--no-serve"], runtime)

    assert runtime.browser_urls == []


def test_explicit_ip_wins_and_is_cached_only_after_successful_bind(runtime):
    runtime.config = complete_config("100.64.0.10")
    runtime.registry.resolved_target = codex_target(runtime.config)

    result = start(["--ip", "192.168.1.50", "--no-serve"], runtime)

    assert result == StartResult(0, "http://192.168.1.50:8765/")
    assert runtime.server_addresses == [("192.168.1.50", 8765)]
    assert runtime.config.bind_ip == "192.168.1.50"
    assert runtime.config.surfaces == complete_config().surfaces


def test_cached_bind_failure_warns_falls_back_and_persists_loopback(runtime, capsys):
    original = complete_config("100.64.0.10")
    runtime.config = original
    runtime.registry.resolved_target = codex_target(original)
    runtime.bind_failures.add("100.64.0.10")

    result = start(["--no-serve"], runtime)

    assert result == StartResult(0, "http://127.0.0.1:8765/")
    assert runtime.server_addresses == [
        ("100.64.0.10", 8765),
        ("127.0.0.1", 8765),
    ]
    assert "Cached address 100.64.0.10 could not be bound" in capsys.readouterr().err
    assert runtime.config.bind_ip == "127.0.0.1"
    assert runtime.config.selected_window == original.selected_window
    assert runtime.config.surfaces == original.surfaces


def test_explicit_bind_failure_returns_nonzero_and_preserves_cache(runtime, capsys):
    original = complete_config("100.64.0.10")
    runtime.config = original
    runtime.bind_failures.add("192.168.1.50")

    result = start(["--ip", "192.168.1.50", "--no-serve"], runtime)

    assert result.exit_code != 0
    assert result.url is None
    assert runtime.server_addresses == [("192.168.1.50", 8765)]
    assert "Could not bind explicit address 192.168.1.50:8765" in capsys.readouterr().err
    assert runtime.config == original


def test_non_loopback_bind_prints_full_control_warning_and_phone_url(runtime, capsys):
    runtime.config = AppConfig()

    result = start(
        ["--ip", "100.64.0.10", "--no-browser", "--no-serve"], runtime
    )

    output = capsys.readouterr().out
    assert result.url == "http://100.64.0.10:8765/"
    assert "full desktop input control" in output
    assert "http://100.64.0.10:8765/" in output


def test_temporarily_absent_saved_target_opens_setup_without_erasing_calibration(runtime):
    original = complete_config()
    runtime.config = original
    runtime.registry.resolved_target = None

    start(["--no-serve"], runtime)

    assert runtime.browser_urls == ["http://127.0.0.1:8765/setup"]
    assert load_config(runtime.config_path).config == original


def test_loader_warnings_are_printed_before_startup(runtime, capsys):
    runtime.config_path.write_text("not json", encoding="utf-8")

    start(["--no-browser", "--no-serve"], runtime)

    assert "Invalid configuration; using defaults." in capsys.readouterr().err


def test_unsupported_desktop_exits_with_actionable_message(runtime, capsys):
    runtime.desktop_error = RuntimeError("Windows with Codex Desktop is required")

    result = start(["--no-serve"], runtime)

    assert result.exit_code != 0
    assert "Windows with Codex Desktop is required" in capsys.readouterr().err
    assert runtime.server_addresses == []


def test_port_override_is_bound_and_saved(runtime):
    runtime.config = AppConfig(port=9000)

    result = start(["--port", "9876", "--no-browser", "--no-serve"], runtime)

    assert result.url == "http://127.0.0.1:9876/"
    assert runtime.server_addresses == [("127.0.0.1", 9876)]
    assert runtime.config.port == 9876


def test_keyboard_interrupt_shuts_down_and_closes_server(runtime):
    runtime.interrupt_server = True

    result = start(["--no-browser"], runtime)

    assert result.exit_code == 0
    assert runtime.servers[0].serve_calls == 1
    assert runtime.servers[0].shutdown_calls == 1
    assert runtime.servers[0].close_calls == 1
