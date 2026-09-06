# CodeAway

![CodeAway sends commands to desktop coding agents and returns replies and live status](https://raw.githubusercontent.com/jmoraispk/codeaway2/main/docs/assets/codeaway-hero.png)

CodeAway is a thin, browser-based remote control for local AI coding agents.
Version 0.1 supports one pairing only: **Windows with Codex Desktop**. The
source repository is `codeaway2`; the Python package and command are
`codeaway`.

> Status: early alpha. The Windows/Codex Desktop phone flow has been
> smoke-tested; expect rough edges and breaking changes.

## Quick start

### 1. Install uv and Tailscale

On the Windows laptop, open PowerShell and run:

```powershell
winget install --id astral-sh.uv --exact
winget install --id Tailscale.Tailscale --exact
```

Install the [Tailscale app](https://tailscale.com/download) on your phone too.
Sign in to Tailscale on both devices with the same account, then open a new
PowerShell window on the laptop.

### 2. Start CodeAway

Open Codex Desktop, keep its window visible, and run:

```powershell
$ip = tailscale ip -4 | Select-Object -First 1
uvx codeaway --ip $ip
```

`uvx` downloads and runs CodeAway; there is no separate CodeAway installation
step. Keep this PowerShell window open while using CodeAway.

### 3. Calibrate on the laptop

CodeAway opens the setup page in the laptop browser. If it does not open, use:

```text
http://<TAILSCALE-IP>:8765/setup
```

Choose the visible Codex Desktop window and draw the three labeled areas:

- **Sidebar:** the projects and tasks on the left.
- **Conversation:** the right pane above the input.
- **Composer:** the chat box only; it may grow taller.

Select **Save calibration**.

### 4. Open CodeAway on the phone

With Tailscale connected on the phone, open the workspace URL printed in
PowerShell:

```text
http://<TAILSCALE-IP>:8765/
```

That is it. On later launches, run `uvx codeaway`; CodeAway reuses the saved
Tailscale address and calibration.

## Platform support

Windows with Codex Desktop is the supported pairing. macOS and Linux can
install the package, but desktop control for those platforms is not implemented
yet.

## Command options

Pass `--ip <IPv4-address>` to use a different Tailscale or LAN address,
`--port PORT` to choose another port, or `--no-browser` to suppress the setup
page. CodeAway saves a successfully bound address and port for later launches.

> **Security:** A non-loopback endpoint grants full desktop mouse and keyboard
> input control to every device that can reach it. CodeAway v0.1 has no pairing
> or authentication, so expose it only on a network boundary you trust.

## Architecture

CodeAway deliberately separates two independent backend axes:

| Module | Backends | Responsibility |
| --- | --- | --- |
| `agents.py` | Codex first; Claude Code and Cursor later | Owns application semantics: detection, projects and tasks, navigation, calibrated surfaces, and actions |
| `desktop.py` | Windows first; macOS and Linux later | Owns OS mechanics: windows, screenshots, accessibility, mouse, keyboard, and clipboard operations |

The server composes one agent backend with one desktop backend. For the first
release that pair is Codex Desktop on Windows. Agent backends do not contain
Windows APIs, and desktop backends do not contain Codex-specific behavior.
