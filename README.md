# CodeAway

CodeAway is a thin, browser-based remote control for local AI coding agents.
The source repository is `codeaway2`; the Python package and command are
`codeaway`.

> Status: MVP design. The package is not published yet.

## Architecture

CodeAway deliberately separates two independent backend axes:

| Module | Backends | Responsibility |
| --- | --- | --- |
| `agents.py` | Codex first; Claude Code and Cursor later | Agent-specific detection, state, navigation, surfaces, and actions |
| `desktop.py` | Windows first; macOS and Linux later | OS-specific windows, screenshots, accessibility, mouse, keyboard, and clipboard operations |

The server composes one agent backend with one desktop backend. For the first
release that pair is Codex Desktop on Windows. Agent backends do not contain
Windows APIs, and desktop backends do not contain Codex-specific behavior.

## Intended usage

After publication, run CodeAway in the foreground with:

```powershell
uvx codeaway
```

The default bind address is `127.0.0.1`. To reach CodeAway from a phone, bind
it explicitly to an address on the laptop, typically its Tailscale IP:

```powershell
uvx codeaway --ip 100.x.x.x
```

The selected address is cached for later launches. CodeAway falls back to
`127.0.0.1` with a warning if a cached address is no longer available.

See the [MVP design](docs/superpowers/specs/2026-09-04-codeaway-mvp-design.md)
for the approved scope and data flow.
