# CodeAway MVP Implementation Plan

> **For agentic workers:** Follow this plan in order with test-first changes and a commit after every completed task. Do not copy AutoPress modules or add AutoPress as a dependency.

**Goal:** Ship a minimal `uvx codeaway` service that lets a phone inspect and control one visible Codex Desktop window on a Windows laptop, persists its IP and calibrated regions, and only opens the setup browser when saved setup is missing or unusable.

**Architecture:** The HTTP service composes two independent contracts: `AgentBackend` in `agents.py` for Codex semantics and `DesktopBackend` in `desktop.py` for Windows mechanics. Configuration stores stable window hints and fractional Sidebar, Conversation, and Composer rectangles. A bundled, framework-free browser UI provides first-run setup and the phone workspace.

**Tech Stack:** Python 3.11+, `http.server.ThreadingHTTPServer`, Pillow, Windows-only `uiautomation`, plain HTML/CSS/JavaScript, pytest, Node's built-in test runner, Hatchling, uv.

**Spec:** `docs/superpowers/specs/2026-09-04-codeaway-mvp-design.md`

## Global constraints

- Keep `src/codeaway/agents.py` for application backends and `src/codeaway/desktop.py` for operating-system backends. Codex-specific selectors must not enter `desktop.py`; Win32/UIA objects must not leave `desktop.py`.
- Require all three fractional surfaces—`sidebar`, `conversation`, and `composer`—before considering setup complete.
- Resolve saved windows from agent ID, executable path, and title hint on every process start. Never persist or reuse a native window handle.
- Activate and verify the exact selected window before every click, accessibility action, scroll, paste, or key event.
- Use 40 Win32 wheel units per logical scroll step. Let the phone convert swipe distance to logical steps.
- Serve only a fixed asset map, limit JSON bodies to 64 KiB, require JSON on state-changing routes, and reject a browser `Origin` whose authority differs from the request `Host`.
- Use `apply_patch` for hand edits. Run the focused red test before each implementation, then the focused green test, then the broader suite before each task commit.
- Keep runtime dependencies to Pillow and `uiautomation; sys_platform == 'win32'`. Do not add FastAPI, Uvicorn, PySide6, NumPy, OpenCV, PyAutoGUI, or a frontend build tool.

---

## Task 1: Scaffold the package and define both backend contracts

**Files:**

- Create: `pyproject.toml`
- Create: `src/codeaway/__init__.py`
- Create: `src/codeaway/__main__.py`
- Create: `src/codeaway/desktop.py`
- Create: `src/codeaway/agents.py`
- Create: `tests/test_desktop.py`
- Create: `tests/test_agents.py`

**Produces:** Stable domain dataclasses, `DesktopBackend`, `AgentBackend`, and `AgentRegistry`.

**Consumes:** Nothing; this is the dependency root for later tasks.

### Step 1: Add the package metadata and failing geometry/registry tests

Create `pyproject.toml` with this project surface:

```toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "codeaway"
version = "0.1.0"
description = "Control a local coding agent from your phone"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
  "Pillow>=11.0",
  "uiautomation>=2.0.20; sys_platform == 'win32'",
]

[project.optional-dependencies]
test = ["pytest>=8.0"]

[project.scripts]
codeaway = "codeaway.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/codeaway"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

In `tests/test_desktop.py`, assert that a fractional region maps into an offset window:

```python
from codeaway.desktop import FractionalRegion, PixelPoint, PixelRegion


def test_fractional_region_resolves_inside_window():
    window = PixelRegion(100, 50, 1000, 800)
    surface = FractionalRegion(0.2, 0.1, 0.5, 0.75)

    assert surface.resolve(window) == PixelRegion(300, 130, 500, 600)
    assert surface.resolve(window).center == PixelPoint(550, 430)
```

In `tests/test_agents.py`, use a tiny fake agent and desktop to prove registry discovery combines the two axes without a concrete import:

```python
def test_registry_discovers_matching_agent(fake_desktop, fake_agent, desktop_window):
    registry = AgentRegistry([fake_agent])

    assert registry.discover(fake_desktop) == [
        AgentTarget("fake", desktop_window, fake_agent.default_surfaces(desktop_window))
    ]
```

Run:

```powershell
uv run pytest tests/test_desktop.py tests/test_agents.py -q
```

Expected: collection fails because `codeaway.desktop` and `codeaway.agents` do not exist.

### Step 2: Implement the domain objects and protocols

In `desktop.py`, add frozen dataclasses for:

```python
@dataclass(frozen=True)
class PixelPoint:
    x: int
    y: int


@dataclass(frozen=True)
class PixelRegion:
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> PixelPoint:
        return PixelPoint(self.x + self.width // 2, self.y + self.height // 2)


@dataclass(frozen=True)
class FractionalRegion:
    x: float
    y: float
    width: float
    height: float

    def resolve(self, parent: PixelRegion) -> PixelRegion:
        return PixelRegion(
            parent.x + round(parent.width * self.x),
            parent.y + round(parent.height * self.y),
            round(parent.width * self.width),
            round(parent.height * self.height),
        )
```

Also add `DesktopWindow`, `AccessibilityNode`, and a string enum with `INVOKE`, `EXPAND`, and `COLLAPSE`. Define `DesktopBackend` with exactly these capabilities:

```python
@dataclass(frozen=True)
class DesktopWindow:
    id: str
    native_handle: int
    title: str
    process_path: str
    region: PixelRegion


@dataclass(frozen=True)
class AccessibilityNode:
    id: str
    role: str
    name: str
    class_name: str
    region: PixelRegion
    depth: int = 0
    expanded: bool | None = None
    actions: frozenset[AccessibilityAction] = frozenset()


class DesktopBackend(Protocol):
    id: str

    def list_windows(self) -> list[DesktopWindow]: ...
    def activate(self, window: DesktopWindow) -> bool: ...
    def is_foreground(self, window: DesktopWindow) -> bool: ...
    def capture(self, region: PixelRegion) -> Image.Image: ...
    def accessibility_tree(self, window: DesktopWindow) -> list[AccessibilityNode]: ...
    def accessibility_action(
        self, node: AccessibilityNode, action: AccessibilityAction
    ) -> None: ...
    def click(self, point: PixelPoint) -> None: ...
    def scroll(self, point: PixelPoint, amount: int) -> None: ...
    def paste_and_submit(self, point: PixelPoint, text: str) -> None: ...
```

In `agents.py`, define the agent-owned values and protocol explicitly:

```python
@dataclass(frozen=True)
class SurfaceMap:
    sidebar: FractionalRegion
    conversation: FractionalRegion
    composer: FractionalRegion


@dataclass(frozen=True)
class AgentTarget:
    agent_id: str
    window: DesktopWindow
    surfaces: SurfaceMap


@dataclass(frozen=True)
class TaskSnapshot:
    title: str
    state: Literal["done", "busy", "idle", "unknown"]
    worktree: bool = False
    selected: bool = False


@dataclass(frozen=True)
class ProjectSnapshot:
    name: str
    host: str | None
    connected: bool
    state: Literal["connected", "busy", "idle"]
    expanded: bool
    tasks: tuple[TaskSnapshot, ...]


@dataclass(frozen=True)
class AgentSnapshot:
    available: bool
    source: str
    projects: tuple[ProjectSnapshot, ...]
    captured_at: str
    error: str | None = None


@dataclass(frozen=True)
class NavigationAction:
    kind: Literal["project", "task"]
    project: str
    title: str | None = None
    expanded: bool | None = None


@dataclass(frozen=True)
class ClickAction:
    surface: Literal["sidebar", "conversation"]
    x: float
    y: float


class AgentBackend(Protocol):
    id: str

    def matches(self, window: DesktopWindow) -> bool: ...
    def default_surfaces(self, window: DesktopWindow) -> SurfaceMap: ...
    def inspect(self, desktop: DesktopBackend, target: AgentTarget) -> AgentSnapshot: ...
    def navigate(
        self, desktop: DesktopBackend, target: AgentTarget, action: NavigationAction
    ) -> None: ...
    def click(
        self, desktop: DesktopBackend, target: AgentTarget, action: ClickAction
    ) -> None: ...
    def scroll(self, desktop: DesktopBackend, target: AgentTarget, amount: int) -> None: ...
    def send(self, desktop: DesktopBackend, target: AgentTarget, text: str) -> None: ...
```

`AgentRegistry.discover()` must call `desktop.list_windows()` once, ask every registered agent to match each window, and create targets with the matching agent's default surfaces. Add `resolve(desktop, agent_id, process_path, title_hint, surfaces) -> AgentTarget | None`; it must restrict candidates to the named agent, compare `os.path.normcase(process_path)`, prefer an exact title match, and then allow a case-insensitive title substring. The saved surfaces replace the discovery defaults in the returned target.

Add `__version__ = "0.1.0"` and make `__main__.py` call `cli.main()` without implementing `cli.py` yet.

Run:

```powershell
uv run pytest tests/test_desktop.py tests/test_agents.py -q
```

Expected: all tests pass.

### Step 3: Commit the scaffold

```powershell
git add pyproject.toml src tests/test_desktop.py tests/test_agents.py
git commit -m "feat: define backend contracts"
```

---

## Task 2: Persist bind preferences, window hints, and calibration

**Files:**

- Create: `src/codeaway/config.py`
- Create: `tests/test_config.py`
- Modify: `src/codeaway/desktop.py`

**Produces:** Validated `AppConfig`, atomic storage, stable target hints, and a reusable setup-completeness decision.

**Consumes:** `FractionalRegion` and `SurfaceMap` from Task 1.

### Step 1: Specify defaults, strict calibration validation, and round trips

Write tests covering:

```python
def test_missing_config_uses_laptop_only_defaults(tmp_path):
    loaded = load_config(tmp_path / "config.json")
    assert loaded.config.bind_ip == "127.0.0.1"
    assert loaded.config.port == 8765
    assert loaded.config.selected_window is None
    assert loaded.config.surfaces is None
    assert loaded.warnings == ()


def test_calibration_round_trip_is_setup_complete(tmp_path):
    config = AppConfig(
        bind_ip="100.90.80.70",
        port=8765,
        selected_agent="codex",
        selected_window=WindowHint(
            process_path=r"C:\\Program Files\\WindowsApps\\OpenAI.Codex_1\\Codex.exe",
            title_hint="ChatGPT",
        ),
        surfaces=SurfaceMap(
            sidebar=FractionalRegion(0, 0, 0.21, 1),
            conversation=FractionalRegion(0.21, 0.05, 0.79, 0.73),
            composer=FractionalRegion(0.32, 0.78, 0.56, 0.18),
        ),
    )
    save_config(tmp_path / "config.json", config)

    loaded = load_config(tmp_path / "config.json")
    assert loaded.config == config
    assert loaded.config.setup_complete is True
```

Add parametrized rejection tests for strings where numbers are required, zero/negative dimensions, values outside `[0, 1]`, and `x + width` or `y + height` greater than `1`. A malformed file must return defaults with one warning and leave the original file untouched.

Run:

```powershell
uv run pytest tests/test_config.py -q
```

Expected: import fails because `codeaway.config` does not exist.

### Step 2: Implement immutable configuration and atomic writes

Use these data shapes:

```python
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
```

`default_config_path()` must return `Path(os.environ["LOCALAPPDATA"]) / "CodeAway" / "config.json"` on Windows and `Path.home() / ".config" / "codeaway" / "config.json"` elsewhere so unit tests and wheel inspection remain portable.

Serialize surfaces exactly as `[x, y, width, height]`. Write JSON to a sibling path with suffix `.tmp`, call `flush()` and `os.fsync()`, then replace with `os.replace()`. Ensure the parent directory exists. Return `ConfigLoad(config, warnings)` from the loader instead of printing inside this module.

Run:

```powershell
uv run pytest tests/test_config.py tests/test_desktop.py tests/test_agents.py -q
```

Expected: all tests pass.

### Step 3: Commit persistent configuration

```powershell
git add src/codeaway/config.py src/codeaway/desktop.py tests/test_config.py
git commit -m "feat: persist setup calibration"
```

---

## Task 3: Implement the generic Windows desktop backend

**Files:**

- Modify: `src/codeaway/desktop.py`
- Modify: `tests/test_desktop.py`

**Produces:** `WindowsDesktop`, the only module allowed to touch Win32, UI Automation, pixels, clipboard, mouse, or keyboard.

**Consumes:** Task 1 desktop contracts.

### Step 1: Add failing tests around a fake native boundary

Inject a private `_WindowsNative` object into `WindowsDesktop` so tests cannot move the real mouse. Cover these behaviors:

```python
def test_input_stops_when_exact_window_cannot_be_activated(native, desktop_window):
    native.activate_result = False
    desktop = WindowsDesktop(native)

    assert desktop.activate(desktop_window) is False
    assert native.input_calls == []


def test_scroll_uses_forty_units_per_logical_step(native):
    desktop = WindowsDesktop(native)
    desktop.scroll(PixelPoint(500, 400), -3)

    assert native.wheel_calls == [(500, 400, -120)]
```

Also test that window enumeration excludes invisible/zero-area windows, `accessibility_tree()` turns native controls into serializable `AccessibilityNode` values, accessibility actions select the requested UIA pattern, and `capture()` passes the exact bounding box to the screenshot boundary.

Run:

```powershell
uv run pytest tests/test_desktop.py -q
```

Expected: failures report that `WindowsDesktop` is missing.

### Step 2: Implement window discovery, activation, capture, and UIA conversion

Keep imports of `uiautomation` inside `_WindowsNative` methods so importing CodeAway on another OS remains safe. Implement:

- `EnumWindows`, visibility and rectangle checks, title extraction, PID lookup, and executable-path lookup.
- Foreground-safe activation using restore/show, `AttachThreadInput`, `SetForegroundWindow`, and a final `GetForegroundWindow() == native_handle` check.
- Pillow `ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)`.
- A UIA walk with maximum depth 40 that skips off-screen and zero-area controls and produces node IDs scoped to the latest tree read.
- Generic action capability discovery and dispatch through Invoke or ExpandCollapse patterns.

The public `DesktopWindow.id` should be an opaque runtime string; `native_handle` may exist on the frozen object but must never be serialized into configuration.

Run:

```powershell
uv run pytest tests/test_desktop.py -q
```

Expected: discovery, activation, screenshot, and accessibility tests pass.

### Step 3: Implement native click, wheel, and paste/submit

Use `SetCursorPos` plus `mouse_event` for click and wheel. `scroll(point, amount)` must move the pointer to the conversation center and inject `amount * 40` as wheel data; it must not reinterpret Codex semantics. Use the Win32 Unicode clipboard followed by UIA `SendKeys("{Ctrl}v{Enter}")` for submission.

Keep activation outside these primitive methods so the agent backend can enforce a single, visible guard before a semantic action. Tests must continue to call only the fake native boundary.

Run:

```powershell
uv run pytest tests/test_desktop.py -q
```

Expected: all desktop tests pass and no live input occurs.

### Step 4: Commit Windows mechanics

```powershell
git add src/codeaway/desktop.py tests/test_desktop.py
git commit -m "feat: add Windows desktop backend"
```

---

## Task 4: Implement Codex discovery, navigator parsing, and safe actions

**Files:**

- Modify: `src/codeaway/agents.py`
- Modify: `tests/test_agents.py`

**Produces:** `CodexAgent`, including the structured navigator and all Codex-specific action translation.

**Consumes:** Generic windows, accessibility nodes, screenshots, and input primitives from Task 3.

### Step 1: Write fixtures and failing matching/navigator tests

Build frozen node fixtures that represent:

- A project button with class `group/folder-row sidebar-item` and name `SummonLab private_3`.
- A sibling button named `Start new chat in SummonLab`.
- A connected image named `Connected`.
- A selected task with class `sidebar-item py-row-y bg-primary-ghost-hover`.
- A worktree image whose class contains `icon-2xs text-codex-description no-drag shrink-0`.
- A running task with a row image whose class contains `icon-xs shrink-0`.
- An in-memory Pillow image with a blue completion dot in the final 64 pixels of the selected task row.

Assert this exact semantic result:

```python
sidebar = Image.new("RGB", (300, 140), "#101010")
for x in range(274, 282):
    for y in range(52, 60):
        sidebar.putpixel((x, y), (45, 120, 245))

assert snapshot.projects[0] == ProjectSnapshot(
    name="SummonLab",
    host="private_3",
    connected=True,
    state="connected",
    expanded=True,
    tasks=(
        TaskSnapshot("Finished task", "done", worktree=True, selected=True),
        TaskSnapshot("Running task", "busy", worktree=False, selected=False),
    ),
)
```

Add matching tests for a process path containing `OpenAI.Codex_`, a path containing `\OpenAI\Codex\`, and a visible title of `ChatGPT`. Confirm that unrelated `ChatGPT.exe` and browser windows do not match unless their executable path is Codex.

Run:

```powershell
uv run pytest tests/test_agents.py -q
```

Expected: failures report that `CodexAgent` is missing.

### Step 2: Implement matching, surface defaults, and navigator interpretation

Implement default editor rectangles as unsaved starting suggestions:

```python
SurfaceMap(
    sidebar=FractionalRegion(0.0, 0.0, 0.21, 1.0),
    conversation=FractionalRegion(0.21, 0.05, 0.79, 0.73),
    composer=FractionalRegion(0.32, 0.78, 0.56, 0.18),
)
```

Sort project and task rows by screen position. Assign a task to the closest preceding project until the next project row. Exclude buttons named `Pin chat` and `Archive chat`. Derive project identity from the `Start new chat in ...` or `Project actions for ...` sibling, leaving the remaining row-name suffix as the host.

Only capture Sidebar pixels for the unnamed blue marker while the selected window is foreground. With Pillow, inspect the task row's rightmost 64 pixels and report `done` when at least six pixels satisfy `blue >= 150`, `blue >= red + 45`, and `blue >= green + 20`. If pixels are unavailable, preserve `busy` from UIA and otherwise report `unknown`, never `idle` by assumption.

Run:

```powershell
uv run pytest tests/test_agents.py -q
```

Expected: matching and navigator tests pass.

### Step 3: Add failing tests for activation, navigation, click offset, scrolling, and send

Cover these invariants:

```python
def test_failed_activation_prevents_every_input(fake_desktop, codex_target):
    fake_desktop.activate_result = False
    agent = CodexAgent()

    with pytest.raises(TargetUnavailable):
        agent.scroll(fake_desktop, codex_target, -4)

    assert fake_desktop.calls == [("activate", codex_target.window)]


def test_sidebar_click_left_offsets_a_blue_dot(fake_desktop, codex_target):
    agent = CodexAgent()
    agent.click(fake_desktop, codex_target, ClickAction("sidebar", 0.97, 0.4))

    clicked = fake_desktop.last_click
    assert clicked.x < codex_target.surfaces.sidebar.resolve(codex_target.window.region).x + round(
        codex_target.window.region.width * 0.97
    )
```

Also assert that project expansion is idempotent, a task uses `INVOKE`, scroll uses the center of the calibrated Conversation, send uses the center of the calibrated Composer, and direct Conversation taps preserve their proportional coordinates.

Run:

```powershell
uv run pytest tests/test_agents.py -q
```

Expected: action tests fail before methods are implemented.

### Step 4: Implement semantic actions behind one activation guard

Create a private helper that activates the target and raises `TargetUnavailable` unless `desktop.activate(target.window)` succeeds. Call it before every action.

For a project action, re-read the tree, locate the matching project, compare its current `expanded` field, and request only `EXPAND` or `COLLAPSE` when needed. For a task action, locate the row under its named project and request `INVOKE`.

Map direct clicks through the named calibrated surface. In Sidebar only, if the tap falls in the right marker gutter, move it left by `max(24, round(window.width * 0.025))` so tapping a done marker opens the task instead of activating Archive. Map Conversation taps exactly. Scroll at the Conversation center with the supplied logical amount. Submit at the Composer center; do not treat the whole horizontal toolbar as the composer.

Run:

```powershell
uv run pytest tests/test_agents.py tests/test_desktop.py -q
```

Expected: all backend tests pass.

### Step 5: Commit Codex behavior

```powershell
git add src/codeaway/agents.py tests/test_agents.py
git commit -m "feat: add Codex agent backend"
```

---

## Task 5: Build the narrow HTTP application and route contract

**Files:**

- Create: `src/codeaway/server.py`
- Create: `tests/test_server.py`

**Produces:** Testable request dispatcher, application state, fixed static serving, HTTP handler, and API routes.

**Consumes:** Registry, selected target, configuration store, and both backend contracts.

### Step 1: Add failing route/security tests with fake backends

Define a `Response(status, content_type, body, headers)` value and an `Application.dispatch()` boundary so most behavior can be tested without a socket. Use one real `ThreadingHTTPServer` test on `127.0.0.1` with port `0` to verify the handler adapter.

Cover:

```python
def test_cross_origin_action_is_rejected(app):
    response = app.dispatch(
        "POST",
        "/api/action",
        {"Host": "127.0.0.1:8765", "Origin": "http://evil.example", "Content-Type": "application/json"},
        b'{"kind":"scroll","amount":-2}',
    )
    assert response.status == 403


def test_fixed_asset_map_rejects_traversal(app):
    assert app.dispatch("GET", "/../../README.md", {}, b"").status == 404
```

Also test wrong content type, malformed JSON, a 65 KiB body, invalid surface names, screenshot PNG responses, disappeared targets returning `409`, and action dispatch incrementing the state revision.

Run:

```powershell
uv run pytest tests/test_server.py -q
```

Expected: import fails because `codeaway.server` does not exist.

### Step 2: Implement locked application state and validated dispatch

Use:

```python
@dataclass
class AppState:
    config: AppConfig
    target: AgentTarget | None
    revision: int = 0
    state_lock: threading.RLock = field(default_factory=threading.RLock)
    action_lock: threading.Lock = field(default_factory=threading.Lock)
```

Build a fixed resource map for `/`, `/setup`, `/app.js`, and `/style.css` through `importlib.resources.files("codeaway.web")`. Never join request path text onto a filesystem path.

For state-changing requests, accept `Content-Type: application/json` with an optional charset, reject bodies over 65,536 bytes before decoding, and—when `Origin` exists—require an HTTP origin whose `netloc` equals the request `Host`. Return errors as `{"error":{"code":"...","message":"..."}}`.

Implement the seven approved API routes. `PUT /api/calibration` must require all three surfaces, validate them through the config parser, save the selected agent/window hint and fractional rectangles atomically, update `state.target`, and increment `revision`.

Run:

```powershell
uv run pytest tests/test_server.py -q
```

Expected: route and security tests pass.

### Step 3: Add the HTTP adapter and live-server test

Create `make_handler(application)` with `do_GET`, `do_POST`, and `do_PUT`. Silence default access logging except through an injected logger. Add:

```python
with running_server(app) as url:
    payload = json.load(urlopen(f"{url}/api/status"))
assert payload["revision"] == 0
```

Ensure the context helper always calls `shutdown()` and `server_close()`.

Run:

```powershell
uv run pytest tests/test_server.py tests/test_config.py -q
```

Expected: all server and persistence tests pass.

### Step 4: Commit the service core

```powershell
git add src/codeaway/server.py tests/test_server.py
git commit -m "feat: add CodeAway HTTP service"
```

---

## Task 6: Build first-run browser setup with an explanatory capture diagram

**Files:**

- Create: `src/codeaway/web/__init__.py`
- Create: `src/codeaway/web/setup.html`
- Create: `src/codeaway/web/app.js`
- Create: `src/codeaway/web/style.css`
- Create: `tests/test_web_ui.cjs`
- Modify: `tests/test_server.py`

**Produces:** A local setup page that detects windows, draws/saves calibration, and teaches the three capture regions.

**Consumes:** `/api/windows`, `/api/select`, `/api/screenshot/window`, `/api/calibration`, and `/api/status`.

### Step 1: Pin the diagram and coordinate behavior in failing tests

Use Node's built-in runner and export pure helpers from `app.js` when `module.exports` exists:

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");
const { normalizeRectangle, setupDiagramLabels } = require("../src/codeaway/web/app.js");

test("setup diagram names every required capture", () => {
  assert.deepEqual(setupDiagramLabels, ["Sidebar", "Conversation", "Composer"]);
});

test("drag coordinates normalize against the displayed screenshot", () => {
  assert.deepEqual(normalizeRectangle({x: 20, y: 10}, {x: 120, y: 60}, 200, 100), {
    x: 0.1, y: 0.1, width: 0.5, height: 0.5
  });
});
```

Add a Python static-resource test asserting `/setup` contains `data-region="sidebar"`, `data-region="conversation"`, and `data-region="composer"` and includes `/app.js` and `/style.css`.

Run:

```powershell
node --test tests/test_web_ui.cjs
uv run pytest tests/test_server.py -q
```

Expected: files or exports are missing.

### Step 2: Implement the compact visual explanation

In `setup.html`, place an inline HTML/CSS schematic before the calibration editor:

```html
<figure class="capture-guide" aria-labelledby="capture-guide-title">
  <figcaption id="capture-guide-title">Capture these three areas</figcaption>
  <div class="agent-window-diagram">
    <div data-region="sidebar">Sidebar<br><small>projects and tasks</small></div>
    <div data-region="conversation">Conversation<br><small>right pane above the input</small></div>
    <div data-region="composer">Composer<br><small>chat box only; it may grow taller</small></div>
  </div>
  <p>Do not include the full-width toolbar or status row in Composer.</p>
</figure>
```

Make the regions distinct with borders and labels; do not use generated imagery or introduce an asset pipeline. At narrow widths, keep the same left/sidebar and stacked right/conversation/composer relationship rather than flattening it into an ambiguous list.

Run the Node and Python tests again. Expected: diagram assertions pass; rectangle-editor behavior remains red.

### Step 3: Implement window selection and three-rectangle calibration

On setup load:

1. Fetch `/api/windows` and show compatible choices with agent, title, and process basename.
2. POST the selected ID to `/api/select`.
3. Load `/api/screenshot/window` with a cache-busting revision.
4. Overlay three absolutely positioned rectangles on the image.
5. Let the user select a label and drag to replace that rectangle.
6. Normalize pointer coordinates against the rendered image's content box, clamp to `[0, 1]`, and reject a rectangle smaller than 1% in either dimension.
7. PUT all three rectangles to `/api/calibration` in one request.
8. On success, show the phone URL and a link to `/`.

Prepopulate the editor from existing saved calibration or the Codex suggestions, but do not mark setup complete until the user saves. Preserve a valid saved calibration if the window is temporarily unavailable.

Run:

```powershell
node --test tests/test_web_ui.cjs
uv run pytest tests/test_server.py tests/test_config.py -q
```

Expected: setup tests pass.

### Step 4: Commit browser setup

```powershell
git add src/codeaway/web tests/test_web_ui.cjs tests/test_server.py
git commit -m "feat: add persistent browser setup"
```

---

## Task 7: Build the minimal phone workspace and tune interaction

**Files:**

- Create: `src/codeaway/web/index.html`
- Modify: `src/codeaway/web/app.js`
- Modify: `src/codeaway/web/style.css`
- Modify: `tests/test_web_ui.cjs`
- Modify: `tests/test_server.py`

**Produces:** Responsive navigator, conversation image, proportional swiping, direct taps, composer, and automatic refresh.

**Consumes:** `/api/status`, `/api/navigator`, `/api/screenshot/conversation`, and `/api/action`.

### Step 1: Add failing pure-JavaScript interaction tests

Pin the phone transformations:

```javascript
test("conversation tap maps to image fractions", () => {
  assert.deepEqual(pointToFraction(150, 100, {left: 50, top: 50, width: 200, height: 100}), {
    x: 0.5, y: 0.5
  });
});

test("swipe distance maps proportionally to bounded logical steps", () => {
  assert.equal(swipeToSteps(240), 10);
  assert.equal(swipeToSteps(-240), -10);
  assert.equal(swipeToSteps(2000), 12);
});

test("project expansion is local UI state", () => {
  const next = toggleProject({SummonLab: true}, "SummonLab");
  assert.deepEqual(next, {SummonLab: false});
});
```

Use `swipeToSteps(deltaY) = clamp(round(deltaY / 24), -12, 12)`, with a minimum absolute value of one only after the gesture passes the tap threshold. This combines with 40 native units per logical step from Task 3.

Run:

```powershell
node --test tests/test_web_ui.cjs
```

Expected: the new helpers are missing.

### Step 2: Implement the document-flow layout

Lay out `index.html` in this order:

1. Compact status header and setup link.
2. Navigator.
3. Conversation screenshot.
4. Composer immediately after Conversation.

Do not make Navigator `position: sticky` or `position: fixed`; the phone page must scroll normally. Render project height from its complete task list rather than a fixed crop. Project arrows must share one aligned icon column and use an inline SVG chevron at least 16 CSS pixels square. Render the worktree marker with a small inline SVG, not an ASCII approximation.

Project disclosure changes only in-memory browser state and rerenders immediately. It must not request a fresh navigator or show `syncing…`. Clicking a task still sends a semantic navigation action.

### Step 3: Implement polling, screenshots, tap/swipe actions, and send

Poll status and navigator every two seconds while `document.visibilityState === "visible"`; stop the timer when hidden and refresh immediately on visibility return. Cache the last revision and replace the Conversation image only after revision changes or a successful action. Append the revision as a query parameter to bypass browser image caching.

For pointer interaction on Conversation:

- Movement under 8 CSS pixels is a direct tap and posts fractional `x`/`y`.
- Larger vertical movement posts a `scroll` action whose logical amount comes from `swipeToSteps()` with the sign chosen so an upward finger swipe reveals newer content.
- Disable another gesture until the current action returns, then fetch a fresh image.

The Composer posts non-empty text as a `send` action, clears only after success, and leaves text intact with a visible error after failure.

Run:

```powershell
node --test tests/test_web_ui.cjs
uv run pytest tests/test_server.py -q
```

Expected: phone behavior and API integration tests pass.

### Step 4: Commit the phone workspace

```powershell
git add src/codeaway/web tests/test_web_ui.cjs tests/test_server.py
git commit -m "feat: add phone agent workspace"
```

---

## Task 8: Wire startup policy, package assets, documentation, and release checks

**Files:**

- Create: `src/codeaway/cli.py`
- Modify: `src/codeaway/__main__.py`
- Modify: `pyproject.toml`
- Create: `tests/test_cli.py`
- Create: `tests/test_package.py`
- Create: `.github/workflows/test.yml`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-09-04-codeaway-mvp-design.md`

**Produces:** Foreground CLI, cached-IP behavior, saved-calibration startup behavior, complete wheel, CI, and operating instructions.

**Consumes:** Every prior task.

### Step 1: Add failing startup-policy tests

Inject factories for the server, browser opener, config path, desktop, and agent registry. Cover:

```python
def test_valid_saved_target_and_calibration_do_not_open_setup(runtime):
    runtime.config = complete_config()
    runtime.registry.resolved_target = codex_target()

    result = start(["--no-serve"], runtime)

    assert result.url == "http://127.0.0.1:8765/"
    assert runtime.browser_urls == []


def test_first_run_opens_setup(runtime):
    runtime.config = AppConfig()

    start(["--no-serve"], runtime)

    assert runtime.browser_urls == ["http://127.0.0.1:8765/setup"]
```

Keep `--no-serve` as a private test-only argument accepted by `start()` rather than the public parser. Also test:

- `--no-browser` suppresses setup browser launch.
- `--ip` wins and is cached only after successful bind.
- A cached bind failure warns, binds `127.0.0.1`, and persists the fallback.
- An explicit bind failure returns nonzero and preserves the previous cache.
- A non-loopback bind prints the full-control warning and phone URL.
- A saved target that is temporarily absent opens setup but leaves its calibration in the config file.

Run:

```powershell
uv run pytest tests/test_cli.py -q
```

Expected: import fails because `codeaway.cli` does not exist.

### Step 2: Implement the foreground CLI and exact browser decision

Expose only:

```text
codeaway [--ip ADDRESS] [--port PORT] [--no-browser]
```

Startup order must be:

1. Load configuration and print loader warnings.
2. Select `WindowsDesktop`; exit with an actionable message on unsupported OS.
3. Register `CodexAgent` and resolve a saved target by agent ID, case-normalized executable path, then title hint.
4. Attempt the explicit, cached, or default bind according to the approved precedence.
5. Save the successful IP and port without changing target/calibration.
6. Start the HTTP server.
7. Open `/setup` only when target resolution or three-surface calibration is missing and `--no-browser` is false.
8. Otherwise print the workspace URL without opening a laptop browser.
9. Run `serve_forever()` until `KeyboardInterrupt`, then call `shutdown()` and `server_close()`.

Run:

```powershell
uv run pytest tests/test_cli.py tests/test_config.py tests/test_server.py -q
```

Expected: startup-policy tests pass.

### Step 3: Verify packaged assets and both entry points

Configure Hatchling to include `src/codeaway/web/*.html`, `*.css`, and `*.js`. In `tests/test_package.py`, verify every resource is readable with `importlib.resources`, `codeaway --help` returns zero, and `python -m codeaway --help` returns zero.

Run:

```powershell
uv run pytest tests/test_package.py -q
uv build
uv run python -c "import zipfile,glob; p=glob.glob('dist/*.whl')[0]; names=zipfile.ZipFile(p).namelist(); assert 'codeaway/web/setup.html' in names; assert 'codeaway/web/index.html' in names"
```

Expected: package tests and wheel-content assertion pass.

### Step 4: Finish README and CI

Document:

- `uvx codeaway` is laptop-only on the first run.
- `uvx codeaway --ip <LAN-or-Tailscale-IP>` enables phone access and caches that address.
- Later `uvx codeaway` runs reuse both IP and calibration.
- Valid setup starts without opening the laptop browser; `/setup` remains available from the phone workspace.
- The diagram's exact Sidebar, Conversation, and chat-box-only Composer guidance.
- `agents.py` owns Codex/Claude/Cursor semantics; `desktop.py` owns Windows/macOS/Linux mechanics.
- The non-loopback endpoint grants full desktop input control to reachable devices.
- Windows + Codex Desktop is the only supported v0.1 pair.

Create a GitHub Actions matrix for Python 3.11 and 3.13 on `windows-latest` and Python 3.13 on `ubuntu-latest`. Run Python tests, Node tests, `uv build`, install the wheel into a clean uv environment, and run `codeaway --help`.

Run:

```powershell
uv run pytest -q
node --test tests/test_web_ui.cjs
uv build
```

Expected: the complete automated suite and package build pass.

### Step 5: Perform the manual Windows/Codex smoke test

With one visible Codex Desktop window titled `ChatGPT`:

```powershell
uv run codeaway --ip 100.64.0.10
```

Replace the example address with the laptop's actual Tailscale IPv4 address.

Verify:

1. `/setup` opens on the first run and its diagram clearly distinguishes Sidebar, Conversation, and the chat-box-only Composer.
2. Draw and save all three regions; `%LOCALAPPDATA%\CodeAway\config.json` contains fractional values and the stable window hint.
3. From the phone, confirm projects/tasks, connected/busy/done/worktree markers, immediate local project collapse, direct task navigation, a Conversation tap, proportional swipe scrolling, and one harmless submitted message.
4. Stop with `Ctrl+C`, run the same command again, and confirm the IP and calibration are reused and no laptop browser opens.
5. Close Codex, relaunch CodeAway, and confirm it directs the user to setup without deleting the saved calibration.
6. Reopen Codex and confirm the saved target resolves without a persisted native handle.

Record the command output and any exception before changing code; do not weaken activation checks to make the smoke test pass.

### Step 6: Commit the release-ready MVP

```powershell
git add src/codeaway/cli.py src/codeaway/__main__.py pyproject.toml tests/test_cli.py tests/test_package.py .github/workflows/test.yml README.md docs/superpowers/specs/2026-09-04-codeaway-mvp-design.md
git commit -m "feat: ship CodeAway MVP"
git status --short
git log --oneline --decorate -8
```

Expected: the working tree is clean and the eight task commits appear in order.

---

## Final verification and review gate

Run the entire release gate from a clean checkout:

```powershell
uv sync --extra test
uv run pytest -q
node --test tests/test_web_ui.cjs
uv build
uv run python -c "import zipfile,glob; p=glob.glob('dist/*.whl')[0]; names=zipfile.ZipFile(p).namelist(); assert all(f'codeaway/web/{n}' in names for n in ('setup.html','index.html','app.js','style.css'))"
git status --short
```

Then review the branch diff against the design spec, concentrating on:

- No AutoPress import, submodule, copied rules UI, or bridge machinery.
- No concrete OS dependency crossing from `desktop.py` into `agents.py`.
- No input after failed exact-window activation.
- No startup browser when saved target and three calibrated surfaces are valid.
- No loss of saved calibration when Codex is temporarily absent.
- No state-changing route without JSON/body/origin validation.
- No unbounded polling, fixed Navigator, or forced sync on local project disclosure.

Fix review findings with focused red-green tests, rerun the release gate, and create a final review-fix commit only if the diff changed.
