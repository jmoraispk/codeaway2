from dataclasses import dataclass, field, replace

import codeaway.agents as agents_module

import pytest
from PIL import Image

from codeaway.agents import (
    AgentRegistry,
    AgentTarget,
    ClickAction,
    CodexAgent,
    NavigationAction,
    ProjectSnapshot,
    SurfaceMap,
    TaskSnapshot,
    TargetUnavailable,
)
from codeaway.desktop import (
    AccessibilityAction,
    AccessibilityNode,
    DesktopWindow,
    FractionalRegion,
    PixelPoint,
    PixelRegion,
)


@dataclass
class FakeDesktop:
    windows: list[DesktopWindow]
    list_windows_calls: int = 0

    def list_windows(self):
        self.list_windows_calls += 1
        return self.windows


@dataclass
class FakeAgent:
    id: str = "fake"

    def matches(self, window):
        return window.process_path.casefold().endswith("fake.exe")

    def default_surfaces(self, window):
        del window
        return SurfaceMap(
            FractionalRegion(0.0, 0.0, 0.2, 1.0),
            FractionalRegion(0.2, 0.0, 0.6, 1.0),
            FractionalRegion(0.2, 0.9, 0.6, 0.1),
        )


@dataclass
class SelectiveFakeAgent(FakeAgent):
    def matches(self, window):
        return super().matches(window) and window.title == "Accepted"


@pytest.fixture
def codex_window():
    return DesktopWindow(
        "codex-window",
        41,
        "ChatGPT",
        r"C:\Program Files\WindowsApps\OpenAI.Codex_1.2.3\Codex.exe",
        PixelRegion(100, 50, 1000, 700),
    )


@pytest.fixture
def navigator_nodes():
    return (
        AccessibilityNode(
            "project",
            "Button",
            "SummonLab private_3",
            "group/folder-row sidebar-item",
            PixelRegion(100, 50, 300, 30),
            expanded=True,
            actions=frozenset(
                {AccessibilityAction.EXPAND, AccessibilityAction.COLLAPSE}
            ),
        ),
        AccessibilityNode(
            "new-chat",
            "Button",
            "Start new chat in SummonLab",
            "",
            PixelRegion(340, 50, 30, 30),
            actions=frozenset({AccessibilityAction.INVOKE}),
        ),
        AccessibilityNode(
            "connected",
            "ImageControl",
            "Connected",
            "",
            PixelRegion(372, 50, 16, 30),
        ),
        AccessibilityNode(
            "finished",
            "Button",
            "Finished task",
            "sidebar-item py-row-y bg-primary-ghost-hover",
            PixelRegion(100, 90, 300, 28),
            actions=frozenset({AccessibilityAction.INVOKE}),
        ),
        AccessibilityNode(
            "worktree",
            "Image",
            "",
            "icon-2xs text-codex-description no-drag shrink-0",
            PixelRegion(110, 96, 12, 12),
        ),
        AccessibilityNode(
            "pin",
            "Button",
            "Pin chat",
            "sidebar-item py-row-y",
            PixelRegion(350, 90, 20, 28),
        ),
        AccessibilityNode(
            "running",
            "Button",
            "Running task",
            "sidebar-item py-row-y",
            PixelRegion(100, 122, 300, 28),
            actions=frozenset({AccessibilityAction.INVOKE}),
        ),
        AccessibilityNode(
            "running-marker",
            "Image",
            "",
            "icon-xs shrink-0",
            PixelRegion(375, 128, 12, 12),
        ),
        AccessibilityNode(
            "archive",
            "Button",
            "Archive chat",
            "sidebar-item py-row-y",
            PixelRegion(350, 122, 20, 28),
        ),
        AccessibilityNode(
            "header-title",
            "Button",
            "Finished task",
            "",
            PixelRegion(450, 65, 240, 30),
            actions=frozenset({AccessibilityAction.INVOKE}),
        ),
        AccessibilityNode(
            "title-editor",
            "EditControl",
            "Chat title",
            "",
            PixelRegion(448, 63, 260, 34),
        ),
    )


@dataclass
class NavigatorDesktop:
    nodes: tuple[AccessibilityNode, ...]
    sidebar: Image.Image
    foreground: bool = True
    capture_regions: list[PixelRegion] = field(default_factory=list)
    capture_error: Exception | None = None

    def accessibility_tree(self, window):
        del window
        return list(reversed(self.nodes))

    def is_foreground(self, window):
        del window
        return self.foreground

    def capture(self, region):
        self.capture_regions.append(region)
        if self.capture_error is not None:
            raise self.capture_error
        return self.sidebar


@dataclass
class ActionDesktop:
    nodes: tuple[AccessibilityNode, ...]
    activate_result: bool = True
    calls: list[tuple[object, ...]] = field(default_factory=list)
    last_click: PixelPoint | None = None

    def activate(self, window):
        self.calls.append(("activate", window))
        return self.activate_result

    def accessibility_tree(self, window):
        self.calls.append(("accessibility_tree", window))
        return list(reversed(self.nodes))

    def accessibility_action(self, node, action):
        self.calls.append(("accessibility_action", node, action))

    def click(self, window, point):
        self.last_click = point
        self.calls.append(("click", window, point))

    def scroll(self, window, point, amount):
        self.calls.append(("scroll", window, point, amount))

    def paste_and_submit(self, window, point, text):
        self.calls.append(("paste_and_submit", window, point, text))

    def replace_and_submit(self, window, point, text):
        self.calls.append(("replace_and_submit", window, point, text))


@dataclass
class StagedActionDesktop(ActionDesktop):
    stages: tuple[tuple[AccessibilityNode, ...], ...] = ()
    stage: int = 0

    def accessibility_tree(self, window):
        self.calls.append(("accessibility_tree", window))
        nodes = self.stages[min(self.stage, len(self.stages) - 1)]
        self.stage += 1
        return list(reversed(nodes))


@pytest.fixture
def fake_desktop(navigator_nodes):
    return ActionDesktop(navigator_nodes)


@pytest.fixture
def codex_target(codex_window):
    return AgentTarget(
        "codex",
        codex_window,
        SurfaceMap(
            sidebar=FractionalRegion(0.0, 0.0, 0.3, 1.0),
            conversation=FractionalRegion(0.3, 0.1, 0.6, 0.65),
            composer=FractionalRegion(0.32, 0.78, 0.56, 0.18),
        ),
    )


def test_registry_discovers_matching_agent():
    desktop_window = DesktopWindow(
        "window-1", 1, "Fake", "fake.exe", PixelRegion(0, 0, 1000, 800)
    )
    fake_desktop = FakeDesktop([desktop_window])
    fake_agent = FakeAgent()
    registry = AgentRegistry([fake_agent])

    assert registry.discover(fake_desktop) == [
        AgentTarget("fake", desktop_window, fake_agent.default_surfaces(desktop_window))
    ]
    assert fake_desktop.list_windows_calls == 1


def test_registry_resolve_prefers_exact_title_and_saved_surfaces():
    exact_window = DesktopWindow(
        "exact", 1, "Fake Editor", "C:/Apps/Fake.exe", PixelRegion(0, 0, 1000, 800)
    )
    substring_window = DesktopWindow(
        "substring", 2, "Fake Editor - other", "C:/Apps/Fake.exe", PixelRegion(0, 0, 1000, 800)
    )
    desktop = FakeDesktop([substring_window, exact_window])
    registry = AgentRegistry([FakeAgent()])
    saved = SurfaceMap(
        FractionalRegion(0.0, 0.0, 0.1, 1.0),
        FractionalRegion(0.1, 0.0, 0.8, 0.8),
        FractionalRegion(0.1, 0.8, 0.8, 0.2),
    )

    result = registry.resolve(
        desktop, "fake", "c:/apps/fake.exe", "Fake Editor", saved
    )

    assert result == AgentTarget("fake", exact_window, saved)


def test_registry_resolve_rejects_missing_title_hint_even_with_one_matching_window():
    rejected_window = DesktopWindow(
        "rejected", 1, "Rejected", "C:/Apps/Fake.exe", PixelRegion(0, 0, 1000, 800)
    )
    accepted_window = DesktopWindow(
        "accepted", 2, "Accepted", "C:/Apps/Fake.exe", PixelRegion(0, 0, 1000, 800)
    )
    desktop = FakeDesktop([rejected_window, accepted_window])
    registry = AgentRegistry([SelectiveFakeAgent()])
    saved = SurfaceMap(
        FractionalRegion(0.0, 0.0, 0.1, 1.0),
        FractionalRegion(0.1, 0.0, 0.8, 0.8),
        FractionalRegion(0.1, 0.8, 0.8, 0.2),
    )

    result = registry.resolve(desktop, "fake", "c:/apps/fake.exe", None, saved)

    assert result is None


def test_registry_resolve_rejects_a_different_same_process_window():
    different_window = DesktopWindow(
        "different", 1, "Different Editor", "C:/Apps/Fake.exe", PixelRegion(0, 0, 1000, 800)
    )
    desktop = FakeDesktop([different_window])
    registry = AgentRegistry([FakeAgent()])
    saved = SurfaceMap(
        FractionalRegion(0.0, 0.0, 0.1, 1.0),
        FractionalRegion(0.1, 0.0, 0.8, 0.8),
        FractionalRegion(0.1, 0.8, 0.8, 0.2),
    )

    result = registry.resolve(
        desktop, "fake", "c:/apps/fake.exe", "Saved Editor", saved
    )

    assert result is None


def test_registry_resolve_rejects_duplicate_exact_title_windows():
    windows = [
        DesktopWindow(
            f"duplicate-{index}",
            index,
            "Saved Editor",
            "C:/Apps/Fake.exe",
            PixelRegion(0, 0, 1000, 800),
        )
        for index in (1, 2)
    ]
    desktop = FakeDesktop(windows)
    registry = AgentRegistry([FakeAgent()])
    saved = SurfaceMap(
        FractionalRegion(0.0, 0.0, 0.1, 1.0),
        FractionalRegion(0.1, 0.0, 0.8, 0.8),
        FractionalRegion(0.1, 0.8, 0.8, 0.2),
    )

    result = registry.resolve(
        desktop, "fake", "c:/apps/fake.exe", "Saved Editor", saved
    )

    assert result is None


@pytest.mark.parametrize(
    ("process_path", "title", "expected"),
    [
        (
            r"C:\Program Files\WindowsApps\OpenAI.Codex_1.2.3\Codex.exe",
            "ChatGPT",
            True,
        ),
        (r"C:\dev\OpenAI\Codex\Codex.exe", "ChatGPT", True),
        (r"C:\Program Files\ChatGPT\ChatGPT.exe", "ChatGPT", False),
        (r"C:\Program Files\Browser\browser.exe", "ChatGPT", False),
    ],
)
def test_codex_matching_requires_a_codex_executable(process_path, title, expected):
    window = DesktopWindow("window", 1, title, process_path, PixelRegion(0, 0, 800, 600))

    assert CodexAgent().matches(window) is expected


def test_codex_default_surfaces_are_editor_starting_suggestions(codex_window):
    assert CodexAgent().default_surfaces(codex_window) == SurfaceMap(
        sidebar=FractionalRegion(0.0, 0.03, 0.21, 0.97),
        conversation=FractionalRegion(0.21, 0.08, 0.79, 0.82),
        composer=FractionalRegion(0.33, 0.90, 0.55, 0.06),
    )


def test_codex_inspect_builds_sorted_projects_and_tasks_from_generic_inputs(
    codex_window, navigator_nodes
):
    sidebar = Image.new("RGB", (300, 140), "#101010")
    for x in range(274, 282):
        for y in range(20, 28):
            sidebar.putpixel((x, y), (45, 120, 245))
    desktop = NavigatorDesktop(navigator_nodes, sidebar)
    target = AgentTarget("codex", codex_window, CodexAgent().default_surfaces(codex_window))

    snapshot = CodexAgent().inspect(desktop, target)

    assert snapshot.projects[0] == ProjectSnapshot(
        name="SummonLab",
        host="private_3",
        connected=True,
        state="connected",
        expanded=True,
        tasks=(
            TaskSnapshot("Finished task", "done", worktree=True, selected=True, task_id="0"),
            TaskSnapshot("Running task", "busy", worktree=False, selected=False, task_id="1"),
        ),
    )
    assert desktop.capture_regions == [target.surfaces.sidebar.resolve(codex_window.region)]


def test_codex_inspect_accepts_interleaved_live_class_tokens(
    codex_window, navigator_nodes
):
    class_names = {
        "project": "sidebar-item px-1 group/folder-row flex",
        "finished": "py-row-y rounded sidebar-item bg-primary-ghost-hover",
        "worktree": "text-codex-description icon-2xs size-4 no-drag shrink-0",
        "pin": "py-row-y toolbar-button sidebar-item",
        "running": "py-row-y rounded sidebar-item",
        "running-marker": "shrink-0 animate-spin icon-xs",
        "archive": "py-row-y toolbar-button sidebar-item",
    }
    nodes = tuple(
        replace(node, class_name=class_names.get(node.id, node.class_name))
        for node in navigator_nodes
    )
    sidebar = Image.new("RGB", (300, 140), "#101010")
    for x in range(274, 282):
        for y in range(20, 28):
            sidebar.putpixel((x, y), (45, 120, 245))
    desktop = NavigatorDesktop(nodes, sidebar)
    target = AgentTarget("codex", codex_window, CodexAgent().default_surfaces(codex_window))

    snapshot = CodexAgent().inspect(desktop, target)

    assert snapshot.projects == (
        ProjectSnapshot(
            name="SummonLab",
            host="private_3",
            connected=True,
            state="connected",
            expanded=True,
            tasks=(
                TaskSnapshot("Finished task", "done", worktree=True, selected=True, task_id="0"),
                TaskSnapshot("Running task", "busy", worktree=False, selected=False, task_id="1"),
            ),
        ),
    )


def test_codex_inspect_never_captures_or_assumes_idle_when_not_foreground(
    codex_window, navigator_nodes
):
    desktop = NavigatorDesktop(
        navigator_nodes,
        Image.new("RGB", (300, 140), "#101010"),
        foreground=False,
    )
    target = AgentTarget("codex", codex_window, CodexAgent().default_surfaces(codex_window))

    snapshot = CodexAgent().inspect(desktop, target)

    assert snapshot.projects[0].tasks == (
        TaskSnapshot("Finished task", "unknown", worktree=True, selected=True, task_id="0"),
        TaskSnapshot("Running task", "busy", worktree=False, selected=False, task_id="1"),
    )
    assert desktop.capture_regions == []


def test_codex_inspect_keeps_uia_results_when_optional_marker_capture_fails(
    codex_window, navigator_nodes
):
    desktop = NavigatorDesktop(
        navigator_nodes,
        Image.new("RGB", (300, 140), "#101010"),
        capture_error=OSError("screen capture unavailable"),
    )
    target = AgentTarget("codex", codex_window, CodexAgent().default_surfaces(codex_window))

    snapshot = CodexAgent().inspect(desktop, target)

    assert snapshot.available is True
    assert snapshot.source == "accessibility"
    assert snapshot.projects[0].tasks == (
        TaskSnapshot("Finished task", "unknown", worktree=True, selected=True, task_id="0"),
        TaskSnapshot("Running task", "busy", worktree=False, selected=False, task_id="1"),
    )


def test_project_identity_uses_only_the_sibling_label_on_its_row(codex_window):
    nodes = (
        AccessibilityNode(
            "alpha-project",
            "ButtonControl",
            "Alpha Research private_1",
            "group/folder-row sidebar-item",
            PixelRegion(100, 50, 300, 30),
            expanded=True,
        ),
        AccessibilityNode(
            "alpha-actions",
            "ButtonControl",
            "Project actions for Alpha",
            "",
            PixelRegion(350, 50, 30, 30),
        ),
        AccessibilityNode(
            "alpha-decoy",
            "ButtonControl",
            "Project actions for Alpha Research",
            "",
            PixelRegion(800, 50, 30, 30),
        ),
        AccessibilityNode(
            "research-project",
            "ButtonControl",
            "Alpha Research private_2",
            "group/folder-row sidebar-item",
            PixelRegion(100, 150, 300, 30),
            expanded=True,
        ),
        AccessibilityNode(
            "research-actions",
            "ButtonControl",
            "Project actions for Alpha Research",
            "",
            PixelRegion(350, 150, 30, 30),
        ),
    )
    desktop = NavigatorDesktop(
        nodes,
        Image.new("RGB", (300, 140), "#101010"),
        foreground=False,
    )
    target = AgentTarget("codex", codex_window, CodexAgent().default_surfaces(codex_window))

    snapshot = CodexAgent().inspect(desktop, target)

    assert [(project.name, project.host) for project in snapshot.projects] == [
        ("Alpha", "Research private_1"),
        ("Alpha Research", "private_2"),
    ]


def test_task_markers_ignore_same_y_icons_outside_the_task_row(
    codex_window, navigator_nodes
):
    nodes = tuple(node for node in navigator_nodes if node.id != "running-marker") + (
        AccessibilityNode(
            "conversation-icon",
            "ImageControl",
            "",
            (
                "icon-xs shrink-0 "
                "icon-2xs text-codex-description no-drag shrink-0"
            ),
            PixelRegion(800, 128, 12, 12),
        ),
    )
    desktop = NavigatorDesktop(
        nodes,
        Image.new("RGB", (300, 140), "#101010"),
        foreground=False,
    )
    target = AgentTarget("codex", codex_window, CodexAgent().default_surfaces(codex_window))

    snapshot = CodexAgent().inspect(desktop, target)

    assert snapshot.projects[0].tasks[1] == TaskSnapshot(
        "Running task", "unknown", worktree=False, selected=False, task_id="1"
    )


def test_connected_marker_must_overlap_its_project_row_horizontally(codex_window):
    nodes = (
        AccessibilityNode(
            "project",
            "Button",
            "Project",
            "group/folder-row sidebar-item",
            PixelRegion(100, 50, 300, 30),
            expanded=True,
        ),
        AccessibilityNode(
            "distant-connected",
            "ImageControl",
            "Connected",
            "",
            PixelRegion(800, 50, 16, 30),
        ),
    )
    desktop = NavigatorDesktop(
        nodes,
        Image.new("RGB", (300, 140), "#101010"),
        foreground=False,
    )
    target = AgentTarget("codex", codex_window, CodexAgent().default_surfaces(codex_window))

    snapshot = CodexAgent().inspect(desktop, target)

    assert snapshot.projects[0].connected is False
    assert snapshot.projects[0].state == "idle"


@pytest.mark.parametrize("action_name", ["navigate", "click", "scroll", "send"])
def test_failed_activation_prevents_every_input(
    action_name, fake_desktop, codex_target
):
    fake_desktop.activate_result = False
    agent = CodexAgent()

    with pytest.raises(TargetUnavailable):
        if action_name == "navigate":
            agent.navigate(
                fake_desktop,
                codex_target,
                NavigationAction(
                    "task", "SummonLab", host="private_3", title="Finished task"
                ),
            )
        elif action_name == "click":
            agent.click(fake_desktop, codex_target, ClickAction("conversation", 0.5, 0.5))
        elif action_name == "scroll":
            agent.scroll(fake_desktop, codex_target, -4)
        else:
            agent.send(fake_desktop, codex_target, "hello")

    assert fake_desktop.calls == [("activate", codex_target.window)]


@pytest.mark.parametrize(
    ("current", "requested", "expected"),
    [
        (False, True, AccessibilityAction.EXPAND),
        (True, False, AccessibilityAction.COLLAPSE),
        (True, True, None),
        (False, False, None),
    ],
)
def test_project_navigation_changes_only_a_different_expansion_state(
    current, requested, expected, fake_desktop, codex_target
):
    project = next(node for node in fake_desktop.nodes if node.id == "project")
    fake_desktop.nodes = tuple(
        AccessibilityNode(
            node.id,
            node.role,
            node.name,
            node.class_name,
            node.region,
            node.depth,
            current,
            node.actions,
        )
        if node is project
        else node
        for node in fake_desktop.nodes
    )

    CodexAgent().navigate(
        fake_desktop,
        codex_target,
        NavigationAction(
            "project", "SummonLab", host="private_3", expanded=requested
        ),
    )

    action_calls = [call for call in fake_desktop.calls if call[0] == "accessibility_action"]
    if expected is None:
        assert action_calls == []
    else:
        assert action_calls == [
            ("accessibility_action", fake_desktop.nodes[0], expected)
        ]


def test_task_navigation_invokes_the_task_under_the_named_project(
    fake_desktop, codex_target
):
    finished = next(node for node in fake_desktop.nodes if node.id == "finished")

    CodexAgent().navigate(
        fake_desktop,
        codex_target,
        NavigationAction(
            "task", "SummonLab", host="private_3", task_id="0", title="Finished task"
        ),
    )

    assert fake_desktop.calls[-1] == (
        "accessibility_action",
        finished,
        AccessibilityAction.INVOKE,
    )


def test_create_chat_invokes_the_project_action_then_submits_the_prompt(
    fake_desktop, codex_target
):
    new_chat = next(node for node in fake_desktop.nodes if node.id == "new-chat")
    composer = AccessibilityNode(
        "composer", "EditControl", "Message", "", PixelRegion(450, 650, 300, 30)
    )
    ready = tuple(
        replace(node, class_name=node.class_name.replace(" bg-primary-ghost-hover", ""))
        for node in fake_desktop.nodes
    ) + (composer,)
    fake_desktop = StagedActionDesktop(fake_desktop.nodes, stages=(ready,))

    CodexAgent().create_chat(
        fake_desktop,
        codex_target,
        project="SummonLab",
        host="private_3",
        text="Investigate the regression",
    )

    assert fake_desktop.calls == [
        ("activate", codex_target.window),
        ("accessibility_tree", codex_target.window),
        ("accessibility_action", new_chat, AccessibilityAction.INVOKE),
        ("accessibility_tree", codex_target.window),
        (
            "paste_and_submit",
            codex_target.window,
            PixelPoint(700, 659),
            "Investigate the regression",
        ),
    ]


def test_rename_chat_selects_the_task_then_replaces_its_header_title(
    fake_desktop, codex_target
):
    task = next(node for node in fake_desktop.nodes if node.id == "finished")
    header = next(node for node in fake_desktop.nodes if node.id == "header-title")

    CodexAgent().rename_chat(
        fake_desktop,
        codex_target,
        project="SummonLab",
        host="private_3",
        task_id="0",
        title="Finished task",
        new_title="Clearer title",
    )

    assert fake_desktop.calls == [
        ("activate", codex_target.window),
        ("accessibility_tree", codex_target.window),
        ("accessibility_action", task, AccessibilityAction.INVOKE),
        ("accessibility_tree", codex_target.window),
        ("accessibility_action", header, AccessibilityAction.INVOKE),
        ("accessibility_tree", codex_target.window),
        (
            "replace_and_submit",
            codex_target.window,
            PixelPoint(578, 80),
            "Clearer title",
        ),
    ]


def test_inspect_assigns_distinct_snapshot_relative_ids_to_duplicate_titles(
    codex_window, navigator_nodes
):
    duplicate = replace(
        next(node for node in navigator_nodes if node.id == "running"),
        id="duplicate-finished",
        name="Finished task",
    )
    desktop = NavigatorDesktop(
        navigator_nodes + (duplicate,), Image.new("RGB", (300, 140), "#101010"), foreground=False
    )
    target = AgentTarget("codex", codex_window, CodexAgent().default_surfaces(codex_window))

    snapshot = CodexAgent().inspect(desktop, target)

    assert [(task.task_id, task.title) for task in snapshot.projects[0].tasks] == [
        ("0", "Finished task"),
        ("1", "Finished task"),
        ("2", "Running task"),
    ]


def test_task_navigation_invokes_only_the_requested_duplicate_snapshot_row(
    fake_desktop, codex_target
):
    first = replace(next(node for node in fake_desktop.nodes if node.id == "finished"), name="Duplicate")
    second = replace(next(node for node in fake_desktop.nodes if node.id == "running"), name="Duplicate")
    fake_desktop.nodes = tuple(
        node for node in fake_desktop.nodes if node.id not in {"finished", "running"}
    ) + (first, second)

    CodexAgent().navigate(
        fake_desktop,
        codex_target,
        NavigationAction("task", "SummonLab", host="private_3", task_id="1", title="Duplicate"),
    )

    assert fake_desktop.calls[-1] == (
        "accessibility_action", second, AccessibilityAction.INVOKE
    )


def test_task_navigation_rejects_a_stale_identity_before_invoking_any_task(
    fake_desktop, codex_target
):
    with pytest.raises(TargetUnavailable):
        CodexAgent().navigate(
            fake_desktop,
            codex_target,
            NavigationAction("task", "SummonLab", host="private_3", task_id="9", title="Finished task"),
        )

    assert not any(call[0] == "accessibility_action" for call in fake_desktop.calls)


def test_create_chat_waits_for_a_new_composer_and_cleared_old_selection(
    navigator_nodes, codex_target
):
    composer = AccessibilityNode(
        "composer", "EditControl", "Message", "", PixelRegion(450, 650, 300, 30)
    )
    initially_selected = navigator_nodes
    stale_selection = navigator_nodes + (composer,)
    ready = tuple(
        replace(node, class_name=node.class_name.replace(" bg-primary-ghost-hover", ""))
        for node in navigator_nodes
    ) + (composer,)
    desktop = StagedActionDesktop(navigator_nodes, stages=(initially_selected, stale_selection, ready))

    CodexAgent().create_chat(
        desktop, codex_target, project="SummonLab", host="private_3", text="Start safely"
    )

    assert desktop.calls[-1] == (
        "paste_and_submit", codex_target.window, PixelPoint(700, 659), "Start safely"
    )
    assert len([call for call in desktop.calls if call[0] == "accessibility_tree"]) == 3


def test_create_chat_timeout_never_types_into_the_previous_chat(
    monkeypatch, navigator_nodes, codex_target
):
    desktop = StagedActionDesktop(navigator_nodes, stages=(navigator_nodes, navigator_nodes))
    clock = iter((0.0, 1.0))
    monkeypatch.setattr(agents_module.time, "monotonic", lambda: next(clock))

    with pytest.raises(TargetUnavailable):
        CodexAgent().create_chat(
            desktop, codex_target, project="SummonLab", host="private_3", text="Do not send"
        )

    assert not any(call[0] == "paste_and_submit" for call in desktop.calls)


def test_rename_waits_for_the_exact_duplicate_selection_then_header_editor(
    navigator_nodes, codex_target
):
    first = replace(next(node for node in navigator_nodes if node.id == "finished"), name="Duplicate")
    second = replace(
        next(node for node in navigator_nodes if node.id == "running"),
        name="Duplicate",
        class_name="sidebar-item py-row-y",
    )
    base = tuple(
        node for node in navigator_nodes if node.id not in {"finished", "running", "header-title", "title-editor"}
    ) + (first, second)
    selected = tuple(
        replace(node, class_name=f"{node.class_name} bg-primary-ghost-hover")
        if node.id == "running" else node
        for node in base
    ) + (
        replace(next(node for node in navigator_nodes if node.id == "header-title"), name="Duplicate"),
    )
    editor = selected + (next(node for node in navigator_nodes if node.id == "title-editor"),)
    desktop = StagedActionDesktop(base, stages=(base, selected, editor))

    CodexAgent().rename_chat(
        desktop,
        codex_target,
        project="SummonLab",
        host="private_3",
        task_id="1",
        title="Duplicate",
        new_title="Distinct title",
    )

    actions = [call for call in desktop.calls if call[0] == "accessibility_action"]
    assert actions == [
        ("accessibility_action", second, AccessibilityAction.INVOKE),
        ("accessibility_action", selected[-1], AccessibilityAction.INVOKE),
    ]
    assert desktop.calls[-1] == (
        "replace_and_submit", codex_target.window, PixelPoint(578, 80), "Distinct title"
    )


def test_rename_timeout_never_types_when_the_requested_task_is_not_selected(
    monkeypatch, navigator_nodes, codex_target
):
    unselected = tuple(
        replace(node, class_name=node.class_name.replace(" bg-primary-ghost-hover", ""))
        for node in navigator_nodes
    )
    desktop = StagedActionDesktop(unselected, stages=(unselected, unselected))
    clock = iter((0.0, 1.0))
    monkeypatch.setattr(agents_module.time, "monotonic", lambda: next(clock))

    with pytest.raises(TargetUnavailable):
        CodexAgent().rename_chat(
            desktop,
            codex_target,
            project="SummonLab",
            host="private_3",
            task_id="0",
            title="Finished task",
            new_title="Never type",
        )

    assert not any(call[0] == "replace_and_submit" for call in desktop.calls)


def test_sidebar_click_left_offsets_a_blue_dot(fake_desktop, codex_target):
    CodexAgent().click(
        fake_desktop, codex_target, ClickAction("sidebar", 0.97, 0.4)
    )

    assert fake_desktop.last_click == PixelPoint(366, 330)


def test_conversation_click_preserves_proportional_coordinates(
    fake_desktop, codex_target
):
    CodexAgent().click(
        fake_desktop, codex_target, ClickAction("conversation", 0.25, 0.75)
    )

    assert fake_desktop.last_click == PixelPoint(550, 461)


def test_conversation_click_at_fractional_edge_stays_inside_surface(
    fake_desktop, codex_target
):
    CodexAgent().click(
        fake_desktop, codex_target, ClickAction("conversation", 1, 1)
    )

    surface = codex_target.surfaces.conversation.resolve(codex_target.window.region)
    assert fake_desktop.last_click == PixelPoint(
        surface.x + surface.width - 1,
        surface.y + surface.height - 1,
    )


def test_scroll_uses_calibrated_conversation_center(fake_desktop, codex_target):
    CodexAgent().scroll(fake_desktop, codex_target, -4)

    assert fake_desktop.calls[-1] == (
        "scroll",
        codex_target.window,
        PixelPoint(700, 347),
        -4,
    )


def test_send_uses_calibrated_composer_center(fake_desktop, codex_target):
    CodexAgent().send(fake_desktop, codex_target, "hello")

    assert fake_desktop.calls[-1] == (
        "paste_and_submit",
        codex_target.window,
        PixelPoint(700, 659),
        "hello",
    )
