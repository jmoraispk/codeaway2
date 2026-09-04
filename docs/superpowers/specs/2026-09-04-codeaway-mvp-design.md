# CodeAway MVP Design

**Status:** Approved in conversation on 2026-09-04

## Purpose

CodeAway lets someone inspect and control a local AI coding agent from a phone.
It is a small Python service with a bundled browser interface, not a native
desktop application. The first release supports one visible Codex Desktop
window on Windows and runs in the foreground until the user presses `Ctrl+C`.

The source repository is `codeaway2`. The PyPI distribution and executable
command are both named `codeaway`.

## MVP Scope

The first release provides:

- Codex Desktop detection on Windows, including windows whose visible title is
  `ChatGPT`.
- A local setup page that shows a full-window screenshot and lets the user
  select Sidebar, Conversation, and Composer rectangles.
- Persistent target selection and calibration, so later launches can start
  without reopening the setup page.
- A small labeled diagram on the setup page showing exactly which portions of
  an agent window belong to Sidebar, Conversation, and Composer.
- A responsive phone workspace with a structured project/task navigator, a
  conversation screenshot, direct clicking, proportional scrolling, and text
  submission.
- Automatic browser polling so the phone view updates without manual refresh.
- Explicit bind-address selection through `--ip`, with the selected address
  cached for later launches.

The MVP excludes rules, automatic prompt approval, queued messages,
notifications, OCR, complete text extraction, multiple simultaneous selected
windows, Cursor, Claude Code, macOS, Linux, a native control window, a detached
background service, and AutoPress compatibility.

## Design Principles

1. Keep the runtime and user flow minimal.
2. Keep agent-specific behavior independent from operating-system behavior.
3. Reimplement only the proven behavior needed from AutoPress; do not depend on
   AutoPress as a package or submodule.
4. Stop an action if its target window cannot be activated. Never inject input
   into whichever application happens to be foreground.
5. Store calibrated rectangles as fractions of the selected window so ordinary
   resizing does not invalidate them.

## Architecture

CodeAway has two independent backend axes:

| Axis | Module | First implementation | Future implementations |
| --- | --- | --- | --- |
| Agent application | `agents.py` | `CodexAgent` | `ClaudeAgent`, `CursorAgent` |
| Desktop operating system | `desktop.py` | `WindowsDesktop` | `MacDesktop`, `LinuxDesktop` |

`agents.py` owns agent semantics: matching application windows, interpreting
accessibility nodes, identifying projects and tasks, deriving readiness,
declaring calibrated surfaces, and translating browser intentions into desktop
operations.

`desktop.py` owns OS mechanics: enumerating and activating windows, capturing
pixels, reading a generic accessibility tree, invoking accessibility actions,
moving and clicking the mouse, scrolling, accessing the clipboard, and sending
keyboard input. It contains no Codex-specific names or selectors.

The server asks the agent registry to find compatible targets using the active
desktop implementation. Once a target is selected, each request is handled by
the selected agent and desktop pair. Neither module imports concrete classes
from the other axis.

## Source Layout

```text
src/codeaway/
├── __init__.py
├── __main__.py       # supports `python -m codeaway`
├── cli.py            # arguments, bind selection, browser launch, shutdown
├── config.py         # JSON configuration and atomic persistence
├── agents.py         # AgentBackend contract, registry, and agent implementations
├── desktop.py        # DesktopBackend contract and OS implementations
├── server.py         # HTTP routes and static-resource serving
└── web/
    ├── setup.html
    ├── index.html
    ├── app.js
    └── style.css

tests/
├── test_agents.py
├── test_cli.py
├── test_config.py
├── test_desktop.py
├── test_server.py
└── test_package.py
```

The two backend modules remain single files for the MVP. They can become
packages later without changing their public contracts if additional backends
make either file difficult to understand.

## Backend Contracts

The contracts describe capabilities rather than concrete libraries.

`DesktopBackend` provides these operations:

```python
class DesktopBackend(Protocol):
    id: str

    def list_windows(self) -> list[DesktopWindow]: ...
    def activate(self, window: DesktopWindow) -> bool: ...
    def is_foreground(self, window: DesktopWindow) -> bool: ...
    def capture(self, region: PixelRegion) -> Image.Image: ...
    def accessibility_tree(self, window: DesktopWindow) -> list[AccessibilityNode]: ...
    def accessibility_action(self, node: AccessibilityNode, action: AccessibilityAction) -> None: ...
    def click(self, point: PixelPoint) -> None: ...
    def scroll(self, point: PixelPoint, amount: int) -> None: ...
    def paste_and_submit(self, point: PixelPoint, text: str) -> None: ...
```

`AgentBackend` provides these operations:

```python
class AgentBackend(Protocol):
    id: str

    def matches(self, window: DesktopWindow) -> bool: ...
    def default_surfaces(self, window: DesktopWindow) -> SurfaceMap: ...
    def inspect(self, desktop: DesktopBackend, target: AgentTarget) -> AgentSnapshot: ...
    def navigate(self, desktop: DesktopBackend, target: AgentTarget, action: NavigationAction) -> None: ...
    def click(self, desktop: DesktopBackend, target: AgentTarget, action: ClickAction) -> None: ...
    def scroll(self, desktop: DesktopBackend, target: AgentTarget, amount: int) -> None: ...
    def send(self, desktop: DesktopBackend, target: AgentTarget, text: str) -> None: ...
```

The concrete data objects are frozen dataclasses defined beside their owning
contract. They contain serializable primitives and do not expose
`uiautomation`, `ctypes`, or HTTP objects across module boundaries.

## Runtime and Dependencies

CodeAway requires Python 3.11 or newer. It uses standard-library `argparse`,
`http.server.ThreadingHTTPServer`, `json`, `webbrowser`, and `importlib.resources`.
The only runtime dependencies are:

- Pillow for screenshots and PNG encoding.
- `uiautomation` on Windows for accessibility discovery and invocation.

There is no PySide6, FastAPI, Uvicorn, NumPy, OpenCV, PyAutoGUI, frontend build
system, or JavaScript framework.

## Startup and Network Selection

The command surface is intentionally small:

```text
codeaway [--ip ADDRESS] [--port PORT] [--no-browser]
```

The default port is `8765`. Bind selection follows this precedence:

1. If `--ip ADDRESS` is provided, CodeAway attempts to bind that address and
   saves it after the bind succeeds.
2. Without `--ip`, CodeAway attempts to reuse the cached address.
3. Without a cached address, CodeAway binds to `127.0.0.1`.
4. If a cached address cannot be bound, CodeAway warns and falls back to
   `127.0.0.1`, then saves the fallback so later launches do not repeat a
   stale bind attempt.
5. If an explicitly supplied address cannot be bound, startup exits with an
   actionable error instead of silently choosing another interface.
6. Passing `--ip 127.0.0.1` explicitly returns the saved configuration to
   laptop-only mode.

CodeAway treats LAN and Tailscale addresses alike. A non-loopback bind prints a
warning that every device allowed to reach that address and port receives full
mouse and keyboard control. The MVP relies on the user's network boundary and
includes no application pairing or authentication.

The service runs in the foreground and stops cleanly on `Ctrl+C`. When the
saved target and all three calibrated surfaces are still valid, startup prints
the phone workspace URL and does not open a laptop browser. When calibration
is missing or the saved target cannot be resolved, startup opens `/setup` in
the default browser unless `--no-browser` is passed.

## Configuration

Configuration is stored at `%LOCALAPPDATA%\CodeAway\config.json` on Windows.
Writes use a temporary sibling file followed by `os.replace` so interruption
cannot leave a partially written document.

The stored fields are:

```json
{
  "bind_ip": "127.0.0.1",
  "port": 8765,
  "selected_agent": "codex",
  "selected_window": {
    "process_path": "...",
    "title_hint": "ChatGPT"
  },
  "surfaces": {
    "sidebar": [0.0, 0.0, 0.2, 1.0],
    "conversation": [0.2, 0.0, 0.8, 0.8],
    "composer": [0.3, 0.8, 0.6, 0.2]
  }
}
```

Surface arrays are `[x, y, width, height]` fractions in the inclusive range
zero through one. The loader rejects malformed types, non-positive dimensions,
and rectangles extending outside the window. The selected window is resolved
again at startup from its agent identifier, process path, and title hint; an
operating-system window handle is never treated as persistent identity.

## Browser Interfaces

The Python package embeds plain HTML, CSS, and JavaScript files under
`codeaway.web`. `importlib.resources` loads them from both editable installs
and built wheels.

`/setup` provides:

- Detected compatible agent windows.
- Selection of the single active window.
- A current full-window screenshot.
- A compact schematic of an agent window with Sidebar on the left,
  Conversation in the right pane above the input, and Composer tightly around
  the chat input box. The diagram explains that Composer may grow vertically
  and must not include the full-width toolbar or status row.
- A rectangle editor for Sidebar, Conversation, and Composer.
- Save and recapture actions.
- The active bind address and phone URL.

`/` provides:

- The selected agent name and state.
- A structured, collapsible project/task navigator.
- A conversation screenshot that supports direct taps and swipe scrolling.
- A composer for text submission.
- A link back to setup.

The browser polls every two seconds while visible. State responses include a
revision; the browser fetches a new conversation PNG only when that revision
changes or after an action. Polling pauses while the document is hidden and
resumes immediately when it becomes visible.

## HTTP API

The MVP API remains local and deliberately narrow:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/status` | Selected target, readiness, revision, and configuration state |
| `GET` | `/api/windows` | Compatible agent windows discovered by the registry |
| `POST` | `/api/select` | Select one agent window |
| `GET` | `/api/screenshot/{surface}` | Return `window`, `sidebar`, or `conversation` PNG |
| `PUT` | `/api/calibration` | Validate and persist the three surface rectangles |
| `GET` | `/api/navigator` | Return structured projects and tasks |
| `POST` | `/api/action` | Execute one validated navigate, click, scroll, or send action |

JSON request bodies have explicit size limits. Static paths are selected from a
fixed asset map rather than joined to user-controlled filesystem paths.
State-changing routes accept only JSON and reject browser `Origin` values that
do not match the active CodeAway origin. This blocks unrelated websites from
using a permitted browser as a relay without adding accounts or pairing.

The server owns one small application-state object containing the selection,
configuration, and revision. A lock protects state changes, and a separate
action lock serializes activation and input so concurrent phone requests cannot
interleave mouse or keyboard events.

## Data Flow

Startup:

1. `cli.py` loads configuration and resolves the bind address.
2. `desktop.py` selects `WindowsDesktop` from `sys.platform`.
3. `agents.py` registers `CodexAgent`.
4. The registry attempts to resolve the saved agent target from stable window
   hints rather than a stale native handle.
5. `server.py` starts. If target resolution and calibration both succeed, it
   prints the workspace URL without opening a laptop browser. Otherwise the
   browser opens `/setup` unless `--no-browser` was supplied.

Setup:

1. The browser requests compatible windows.
2. The registry obtains generic windows from `WindowsDesktop` and asks each
   registered agent whether it matches.
3. The user selects a window and draws three rectangles over its screenshot.
4. The server normalizes the rectangles, validates them, and saves the target
   and surfaces.

Phone refresh:

1. The browser requests status and navigator data.
2. `CodexAgent` interprets the generic accessibility tree and sampled pixels
   supplied by `WindowsDesktop`.
3. The server serializes the agent snapshot and exposes the cropped
   conversation PNG.

Action:

1. The server validates the action body and resolves the selected target.
2. `CodexAgent` converts the semantic action and fractional coordinates into a
   desktop operation.
3. `WindowsDesktop` activates the exact target window.
4. Only after successful activation does it inject input or invoke an
   accessibility action.
5. The server increments the revision so the phone refreshes its view.

## Error Handling

- No compatible window: setup remains usable and offers Refresh.
- Saved target or calibration unavailable at startup: the service remains
  running and directs the user to setup; it does not discard valid saved
  rectangles merely because the target is temporarily closed.
- Selected window disappears: actions return conflict status, discovery runs
  again, and no input is injected.
- Activation fails: the action returns conflict status and no click, scroll,
  paste, or key event occurs.
- Screenshot fails: the endpoint returns a service error; the browser retains
  the last image and labels it stale.
- Accessibility is unavailable: the navigator explains the limitation and the
  screenshot remains usable for direct clicks.
- Invalid calibration or action input: the server returns a structured client
  error without modifying configuration or desktop state.
- Explicit bind failure: startup exits nonzero with the address and operating
  system error.
- Cached bind failure: startup warns, uses loopback, and replaces the cached
  value with loopback so the next launch starts cleanly.

## Testing

Development follows test-first implementation. Tests use concrete domain
objects and inject fake boundary implementations only where real desktop input
would affect the user's machine.

- `test_agents.py`: Codex matching, navigator interpretation, state markers,
  surface mapping, and refusal to act after activation failure.
- `test_desktop.py`: region math, window identity, wheel strength, and generic
  accessibility-node conversion. Native input calls are intercepted at the
  final OS boundary.
- `test_config.py`: defaults, normalization, atomic writes, cached-address
  precedence, persisted surface calibration, and malformed-file recovery.
- `test_server.py`: real HTTP server requests, body limits, fixed static assets,
  error responses, and action dispatch through both backend contracts.
- Browser tests: capture diagram labels, setup rectangle behavior, persisted
  calibration startup, polling visibility rules, navigator expansion, click
  mapping, scrolling, and composer submission.
- `test_package.py`: wheel contents, embedded assets, console entry point, and
  `python -m codeaway` behavior.

Before release, CI builds the wheel, installs it into a clean environment, and
runs `codeaway --help`. A manual Windows smoke test covers live Codex discovery,
calibration, phone access through an explicitly selected Tailscale IP,
navigation, scrolling, clicking, and sending a harmless message.

## Release

The project uses a standard `pyproject.toml` with a `src` layout and a
`codeaway = "codeaway.cli:main"` console entry point. Bundled web assets are
included in both the source distribution and wheel. After publication, the
primary command is:

```powershell
uvx codeaway
```

Frequent users can install it persistently with `uv tool install codeaway`.
