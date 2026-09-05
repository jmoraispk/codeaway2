from __future__ import annotations

import argparse
import ipaddress
import sys
import webbrowser
from dataclasses import dataclass, replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Sequence

from .agents import AgentRegistry, CodexAgent
from .config import AppConfig, default_config_path, load_config, save_config
from .desktop import WindowsDesktop
from .server import AppState, Application, make_handler


class UnsupportedPlatform(RuntimeError):
    """The current platform has no supported desktop backend."""


def _desktop() -> WindowsDesktop:
    if sys.platform != "win32":
        raise UnsupportedPlatform(
            "CodeAway v0.1 requires Windows with Codex Desktop."
        )
    return WindowsDesktop()


def _registry(agents):
    return AgentRegistry(agents)


@dataclass
class Runtime:
    server_factory: Callable = ThreadingHTTPServer
    browser_open: Callable[[str], object] = webbrowser.open
    config_path_factory: Callable[[], str | Path] = default_config_path
    desktop_factory: Callable[[], object] = _desktop
    registry_factory: Callable[[Sequence[object]], object] = _registry


@dataclass(frozen=True)
class StartResult:
    exit_code: int
    url: str | None


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _ipv4_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("address must be an IPv4 address") from error
    if not isinstance(address, ipaddress.IPv4Address):
        raise argparse.ArgumentTypeError("CodeAway v0.1 requires an IPv4 address")
    return str(address)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codeaway",
        description="Control a local Codex Desktop window from your phone.",
    )
    parser.add_argument(
        "--ip",
        type=_ipv4_address,
        metavar="IPV4_ADDRESS",
        help="IPv4 address to bind and cache",
    )
    parser.add_argument("--port", type=_port, metavar="PORT", help="port to bind and cache")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="do not open the setup page in a laptop browser",
    )
    return parser


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _base_url(address: str, port: int) -> str:
    host = f"[{address}]" if ":" in address else address
    return f"http://{host}:{port}/"


def _print_bind_error(label: str, address: str, port: int, error: OSError) -> None:
    print(
        f"Could not bind {label} address {address}:{port}: {error}",
        file=sys.stderr,
    )


def start(
    argv: Sequence[str] | None = None,
    runtime: Runtime | None = None,
    *,
    _serve: bool = True,
) -> StartResult:
    options = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    runtime = runtime or Runtime()

    config_path = Path(runtime.config_path_factory())
    config_existed = config_path.exists()
    loaded = load_config(config_path)
    for warning in loaded.warnings:
        print(warning, file=sys.stderr)
    config = loaded.config

    try:
        desktop = runtime.desktop_factory()
    except (OSError, RuntimeError) as error:
        print(f"CodeAway cannot start: {error}", file=sys.stderr)
        return StartResult(1, None)

    agent = CodexAgent()
    registry = runtime.registry_factory([agent])
    target = None
    if config.setup_complete:
        assert config.selected_agent is not None
        assert config.selected_window is not None
        assert config.surfaces is not None
        target = registry.resolve(
            desktop,
            config.selected_agent,
            config.selected_window.process_path,
            config.selected_window.title_hint,
            config.surfaces,
        )

    address = options.ip if options.ip is not None else config.bind_ip
    port = options.port if options.port is not None else config.port
    try:
        parsed_address = ipaddress.ip_address(address)
    except ValueError:
        parsed_address = None
    if not isinstance(parsed_address, ipaddress.IPv4Address):
        print(
            "CodeAway v0.1 requires an IPv4 bind address; "
            "choose one with --ip IPV4_ADDRESS.",
            file=sys.stderr,
        )
        return StartResult(1, None)
    state = AppState(config, target)
    application = Application(
        state,
        registry,
        {agent.id: agent},
        desktop,
        config_path,
    )
    handler = make_handler(application)

    try:
        server = runtime.server_factory((address, port), handler)
    except OSError as error:
        if options.ip is not None:
            _print_bind_error("explicit", address, port, error)
            return StartResult(1, None)
        if _is_loopback(address):
            source = "cached" if config_existed else "default"
            _print_bind_error(source, address, port, error)
            return StartResult(1, None)
        print(
            f"Cached address {address} could not be bound: {error}; "
            "falling back to 127.0.0.1.",
            file=sys.stderr,
        )
        address = "127.0.0.1"
        try:
            server = runtime.server_factory((address, port), handler)
        except OSError as fallback_error:
            _print_bind_error("fallback", address, port, fallback_error)
            return StartResult(1, None)

    bound_port = int(server.server_address[1])
    persisted = replace(config, bind_ip=address, port=bound_port)
    try:
        save_config(config_path, persisted)
    except OSError as error:
        server.server_close()
        print(f"Could not save CodeAway configuration: {error}", file=sys.stderr)
        return StartResult(1, None)
    state.config = persisted

    url = _base_url(address, bound_port)
    if not _is_loopback(address):
        print(
            f"WARNING: Every device that can reach {url} receives full desktop input control."
        )
    print(f"CodeAway workspace: {url}")
    if target is None and not options.no_browser:
        runtime.browser_open(f"{url}setup")

    if not _serve:
        server.server_close()
        return StartResult(0, url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return StartResult(0, url)


def main(argv: Sequence[str] | None = None) -> int:
    return start(argv).exit_code
