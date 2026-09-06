import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from PIL import Image

from .desktop import (
    AccessibilityAction,
    AccessibilityNode,
    DesktopBackend,
    DesktopWindow,
    FractionalRegion,
    PixelPoint,
)


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
    task_id: str | None = None
    display_title: str | None = None


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
    host: str | None = None
    title: str | None = None
    task_id: str | None = None
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

    def create_chat(
        self,
        desktop: DesktopBackend,
        target: AgentTarget,
        project: str,
        host: str | None,
        text: str,
    ) -> None: ...


class AgentRegistry:
    def __init__(self, agents: list[AgentBackend] | tuple[AgentBackend, ...]):
        self._agents = tuple(agents)

    def discover(self, desktop: DesktopBackend) -> list[AgentTarget]:
        windows = desktop.list_windows()
        return [
            AgentTarget(agent.id, window, agent.default_surfaces(window))
            for window in windows
            for agent in self._agents
            if agent.matches(window)
        ]

    def resolve(
        self,
        desktop: DesktopBackend,
        agent_id: str,
        process_path: str,
        title_hint: str | None,
        surfaces: SurfaceMap,
    ) -> AgentTarget | None:
        agent = next((agent for agent in self._agents if agent.id == agent_id), None)
        if agent is None:
            return None
        windows = desktop.list_windows()
        normalized_process_path = os.path.normcase(os.path.normpath(process_path))
        candidates = [
            window
            for window in windows
            if os.path.normcase(os.path.normpath(window.process_path))
            == normalized_process_path
            and agent.matches(window)
        ]
        if title_hint is None:
            return None
        exact_matches = [window for window in candidates if window.title == title_hint]
        if len(exact_matches) != 1:
            return None
        return AgentTarget(agent_id, exact_matches[0], surfaces)


class TargetUnavailable(RuntimeError):
    """The selected window could not be activated safely."""


@dataclass(frozen=True)
class _ProjectRow:
    node: AccessibilityNode
    name: str
    host: str | None
    tasks: tuple[AccessibilityNode, ...]


class CodexAgent:
    id = "codex"

    _PROJECT_CLASS_TOKENS = frozenset({"group/folder-row", "sidebar-item"})
    _TASK_CLASS_TOKENS = frozenset({"sidebar-item", "py-row-y"})
    _WORKTREE_CLASS_TOKENS = frozenset(
        {"icon-2xs", "text-codex-description", "no-drag", "shrink-0"}
    )
    _BUSY_CLASS_TOKENS = frozenset({"icon-xs", "shrink-0"})
    _ACTION_WAIT_SECONDS = 0.5
    _ACTION_POLL_SECONDS = 0.05

    def matches(self, window: DesktopWindow) -> bool:
        path = window.process_path.replace("/", "\\").casefold()
        return "openai.codex_" in path or "\\openai\\codex\\" in path

    def default_surfaces(self, window: DesktopWindow) -> SurfaceMap:
        del window
        return SurfaceMap(
            sidebar=FractionalRegion(0.00, 0.03, 0.21, 0.97),
            conversation=FractionalRegion(0.21, 0.08, 0.79, 0.82),
            composer=FractionalRegion(0.33, 0.90, 0.55, 0.06),
        )

    @staticmethod
    def _position(node: AccessibilityNode) -> tuple[int, int]:
        return node.region.y, node.region.x

    @staticmethod
    def _same_row(first: AccessibilityNode, second: AccessibilityNode) -> bool:
        return (
            first.region.y < second.region.y + second.region.height
            and second.region.y < first.region.y + first.region.height
        )

    @staticmethod
    def _overlaps_horizontally(
        first: AccessibilityNode, second: AccessibilityNode
    ) -> bool:
        return (
            first.region.x < second.region.x + second.region.width
            and second.region.x < first.region.x + first.region.width
        )

    @staticmethod
    def _has_class_tokens(class_name: str, required_tokens: frozenset[str]) -> bool:
        return required_tokens.issubset(class_name.split())

    def _project_identity(
        self, project: AccessibilityNode, nodes: list[AccessibilityNode]
    ) -> tuple[str, str | None]:
        names: list[str] = []
        for node in nodes:
            if node.role.casefold() not in {"button", "buttoncontrol"}:
                continue
            if not self._same_row(project, node) or not self._overlaps_horizontally(
                project, node
            ):
                continue
            for prefix in ("Start new chat in ", "Project actions for "):
                if node.name.startswith(prefix):
                    names.append(node.name[len(prefix) :])
        candidates = [
            name
            for name in names
            if project.name == name or project.name.startswith(f"{name} ")
        ]
        name = max(candidates, key=len) if candidates else project.name
        suffix = project.name[len(name) :].strip()
        return name, suffix or None

    def _rows(self, nodes: list[AccessibilityNode]) -> tuple[_ProjectRow, ...]:
        projects = sorted(
            (
                node
                for node in nodes
                if self._has_class_tokens(node.class_name, self._PROJECT_CLASS_TOKENS)
            ),
            key=self._position,
        )
        tasks = sorted(
            (
                node
                for node in nodes
                if self._has_class_tokens(node.class_name, self._TASK_CLASS_TOKENS)
                and not self._has_class_tokens(
                    node.class_name, self._PROJECT_CLASS_TOKENS
                )
                and node.name not in {"Pin chat", "Archive chat"}
            ),
            key=self._position,
        )
        rows: list[_ProjectRow] = []
        for index, project in enumerate(projects):
            next_y = projects[index + 1].region.y if index + 1 < len(projects) else None
            project_tasks = tuple(
                task
                for task in tasks
                if task.region.y >= project.region.y
                and (next_y is None or task.region.y < next_y)
            )
            name, host = self._project_identity(project, nodes)
            rows.append(_ProjectRow(project, name, host, project_tasks))
        return tuple(rows)

    def _project_row(
        self,
        nodes: list[AccessibilityNode],
        project: str,
        host: str | None,
    ) -> _ProjectRow:
        projects = [
            row
            for row in self._rows(nodes)
            if row.name == project and row.host == host
        ]
        if len(projects) != 1:
            raise TargetUnavailable(f"project {project!r} is unavailable")
        return projects[0]

    @staticmethod
    def _task_identity(task: AccessibilityNode) -> str | None:
        return task.stable_id or None

    def _task_node(
        self,
        row: _ProjectRow,
        task_id: str | None,
        expected_title: str,
    ) -> AccessibilityNode:
        if task_id is None:
            raise TargetUnavailable("task identity is unavailable")
        tasks = [
            task
            for task in row.tasks
            if self._task_identity(task) == task_id
        ]
        if len(tasks) != 1 or tasks[0].name != expected_title:
            raise TargetUnavailable(f"task {expected_title!r} is unavailable")
        return tasks[0]

    @staticmethod
    def _is_selected(task: AccessibilityNode) -> bool:
        return "bg-primary-ghost-hover" in task.class_name

    @staticmethod
    def _overlaps_region(node: AccessibilityNode, region) -> bool:
        return (
            node.region.x < region.x + region.width
            and region.x < node.region.x + node.region.width
            and node.region.y < region.y + region.height
            and region.y < node.region.y + node.region.height
        )

    @staticmethod
    def _is_within_region(node: AccessibilityNode, region) -> bool:
        return (
            node.region.x >= region.x
            and node.region.y >= region.y
            and node.region.x + node.region.width <= region.x + region.width
            and node.region.y + node.region.height <= region.y + region.height
        )

    def _new_chat_fingerprint(
        self, nodes: list[AccessibilityNode], target: AgentTarget
    ) -> tuple[frozenset[str], frozenset[str], frozenset[str]] | None:
        selected: set[str] = set()
        for row in self._rows(nodes):
            for task in row.tasks:
                if not self._is_selected(task):
                    continue
                task_id = self._task_identity(task)
                if task_id is None or task_id in selected:
                    return None
                selected.add(task_id)
        sidebar = target.surfaces.sidebar.resolve(target.window.region)
        composer = target.surfaces.composer.resolve(target.window.region)
        header_bottom = target.window.region.y + max(
            80, round(target.window.region.height * 0.1)
        )
        headers: set[str] = set()
        composers: set[str] = set()
        for node in nodes:
            if (
                node.role.casefold() in {"button", "buttoncontrol"}
                and node.region.x >= sidebar.x + sidebar.width
                and node.region.y < header_bottom
            ):
                if node.stable_id is None or node.stable_id in headers:
                    return None
                headers.add(node.stable_id)
            if (
                node.role.casefold() in {"edit", "editcontrol"}
                and self._overlaps_region(node, composer)
            ):
                if node.stable_id is None or node.stable_id in composers:
                    return None
                composers.add(node.stable_id)
        return frozenset(selected), frozenset(headers), frozenset(composers)

    def _new_chat_action(
        self,
        row: _ProjectRow,
        nodes: list[AccessibilityNode],
        target: AgentTarget,
    ) -> AccessibilityNode | None:
        sidebar = target.surfaces.sidebar.resolve(target.window.region)
        actions = [
            node
            for node in nodes
            if node.name == f"Start new chat in {row.name}"
            and AccessibilityAction.INVOKE in node.actions
            and node.region.width > 0
            and node.region.height > 0
            and self._is_within_region(node, row.node.region)
            and self._is_within_region(node, sidebar)
            and self._is_within_region(node, target.window.region)
        ]
        return actions[0] if len(actions) == 1 else None

    def _connected_marker_occupies_action_slot(
        self,
        row: _ProjectRow,
        action: AccessibilityNode,
        nodes: list[AccessibilityNode],
    ) -> bool:
        return any(
            node.role.casefold() in {"image", "imagecontrol"}
            and node.name == "Connected"
            and self._same_row(row.node, node)
            and self._overlaps_region(node, action.region)
            for node in nodes
        )

    def _wait_for_tree(
        self,
        desktop: DesktopBackend,
        target: AgentTarget,
        condition,
        unavailable_message: str,
    ):
        deadline = time.monotonic() + self._ACTION_WAIT_SECONDS
        while True:
            nodes = desktop.accessibility_tree(target.window)
            result = condition(nodes)
            if result is not None:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TargetUnavailable(unavailable_message)
            time.sleep(min(self._ACTION_POLL_SECONDS, remaining))

    def _has_class_on_row(
        self,
        task: AccessibilityNode,
        nodes: list[AccessibilityNode],
        required_tokens: frozenset[str],
    ) -> bool:
        return any(
            self._has_class_tokens(node.class_name, required_tokens)
            and self._same_row(task, node)
            and self._overlaps_horizontally(task, node)
            for node in nodes
        )

    @staticmethod
    def _is_done(
        task: AccessibilityNode,
        sidebar_region,
        sidebar_image: Image.Image | None,
    ) -> bool:
        if sidebar_image is None:
            return False
        left = max(0, task.region.x - sidebar_region.x)
        right = min(sidebar_image.width, task.region.x + task.region.width - sidebar_region.x)
        top = max(0, task.region.y - sidebar_region.y)
        bottom = min(sidebar_image.height, task.region.y + task.region.height - sidebar_region.y)
        left = max(left, right - 64)
        if left >= right or top >= bottom:
            return False
        blue_pixels = 0
        pixels = sidebar_image.convert("RGB")
        for y in range(top, bottom):
            for x in range(left, right):
                red, green, blue = pixels.getpixel((x, y))
                if blue >= 150 and blue >= red + 45 and blue >= green + 20:
                    blue_pixels += 1
                    if blue_pixels >= 6:
                        return True
        return False

    def inspect(self, desktop: DesktopBackend, target: AgentTarget) -> AgentSnapshot:
        nodes = desktop.accessibility_tree(target.window)
        sidebar_region = target.surfaces.sidebar.resolve(target.window.region)
        sidebar_image = None
        if desktop.is_foreground(target.window):
            try:
                sidebar_image = desktop.capture(sidebar_region)
            except Exception:
                sidebar_image = None

        projects: list[ProjectSnapshot] = []
        for row in self._rows(nodes):
            tasks: list[TaskSnapshot] = []
            task_ids = [self._task_identity(task) for task in row.tasks]
            for task, task_id in zip(row.tasks, task_ids, strict=True):
                busy = self._has_class_on_row(task, nodes, self._BUSY_CLASS_TOKENS)
                state: Literal["done", "busy", "idle", "unknown"]
                if self._is_done(task, sidebar_region, sidebar_image):
                    state = "done"
                elif busy:
                    state = "busy"
                else:
                    state = "unknown"
                tasks.append(
                    TaskSnapshot(
                        title=task.name,
                        state=state,
                        worktree=self._has_class_on_row(
                            task, nodes, self._WORKTREE_CLASS_TOKENS
                        ),
                        selected=self._is_selected(task),
                        task_id=(
                            task_id
                            if task_id is not None and task_ids.count(task_id) == 1
                            else None
                        ),
                    )
                )
            connected = any(
                node.role.casefold() in {"image", "imagecontrol"}
                and node.name == "Connected"
                and self._same_row(row.node, node)
                and self._overlaps_horizontally(row.node, node)
                for node in nodes
            )
            project_state: Literal["connected", "busy", "idle"]
            if connected:
                project_state = "connected"
            elif any(task.state == "busy" for task in tasks):
                project_state = "busy"
            else:
                project_state = "idle"
            projects.append(
                ProjectSnapshot(
                    name=row.name,
                    host=row.host,
                    connected=connected,
                    state=project_state,
                    expanded=row.node.expanded is True,
                    tasks=tuple(tasks),
                )
            )
        return AgentSnapshot(
            available=True,
            source="accessibility+pixels" if sidebar_image is not None else "accessibility",
            projects=tuple(projects),
            captured_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _activate(desktop: DesktopBackend, target: AgentTarget) -> None:
        if not desktop.activate(target.window):
            raise TargetUnavailable("the selected Codex window is unavailable")

    def navigate(
        self,
        desktop: DesktopBackend,
        target: AgentTarget,
        action: NavigationAction,
    ) -> None:
        self._activate(desktop, target)
        nodes = desktop.accessibility_tree(target.window)
        project = self._project_row(nodes, action.project, action.host)
        if action.kind == "project":
            if action.expanded is None:
                raise ValueError("project navigation requires an expanded state")
            if project.node.expanded is action.expanded:
                return
            accessibility_action = (
                AccessibilityAction.EXPAND
                if action.expanded
                else AccessibilityAction.COLLAPSE
            )
            desktop.accessibility_action(project.node, accessibility_action)
            return
        if action.title is None:
            raise ValueError("task navigation requires a title")
        task = self._task_node(project, action.task_id, action.title)
        desktop.accessibility_action(task, AccessibilityAction.INVOKE)

    def click(
        self,
        desktop: DesktopBackend,
        target: AgentTarget,
        action: ClickAction,
    ) -> None:
        self._activate(desktop, target)
        surface = getattr(target.surfaces, action.surface).resolve(target.window.region)
        if surface.width <= 0 or surface.height <= 0:
            raise TargetUnavailable("the calibrated surface has no usable pixels")
        point = PixelPoint(
            surface.x + min(surface.width - 1, math.floor(surface.width * action.x)),
            surface.y + min(surface.height - 1, math.floor(surface.height * action.y)),
        )
        if action.surface == "sidebar" and surface.x + surface.width - point.x <= 64:
            offset = max(24, round(target.window.region.width * 0.025))
            point = PixelPoint(max(surface.x, point.x - offset), point.y)
        desktop.click(target.window, point)

    def scroll(
        self,
        desktop: DesktopBackend,
        target: AgentTarget,
        amount: int,
    ) -> None:
        self._activate(desktop, target)
        conversation = target.surfaces.conversation.resolve(target.window.region)
        desktop.scroll(target.window, conversation.center, amount)

    def send(
        self,
        desktop: DesktopBackend,
        target: AgentTarget,
        text: str,
    ) -> None:
        self._activate(desktop, target)
        composer = target.surfaces.composer.resolve(target.window.region)
        desktop.paste_and_submit(target.window, composer.center, text)

    def create_chat(
        self,
        desktop: DesktopBackend,
        target: AgentTarget,
        project: str,
        host: str | None,
        text: str,
    ) -> None:
        self._activate(desktop, target)
        nodes = desktop.accessibility_tree(target.window)
        row = self._project_row(nodes, project, host)
        action = self._new_chat_action(row, nodes, target)
        if action is None:
            raise TargetUnavailable(f"new chat action for {project!r} is unavailable")
        before_transition = self._new_chat_fingerprint(nodes, target)
        if before_transition is None:
            raise TargetUnavailable(f"new chat state for {project!r} is unavailable")
        desktop.move(target.window, action.region.center)

        def hover_ready(hover_nodes: list[AccessibilityNode]):
            try:
                hover_row = self._project_row(hover_nodes, project, host)
            except TargetUnavailable:
                return None
            hover_action = self._new_chat_action(hover_row, hover_nodes, target)
            if hover_action is None or self._connected_marker_occupies_action_slot(
                hover_row, hover_action, hover_nodes
            ):
                return None
            return hover_action

        action = self._wait_for_tree(
            desktop,
            target,
            hover_ready,
            f"new chat action for {project!r} is unavailable after hover",
        )
        desktop.click(target.window, action.region.center)
        composer = target.surfaces.composer.resolve(target.window.region)

        def new_chat_ready(post_action_nodes: list[AccessibilityNode]):
            post_transition = self._new_chat_fingerprint(post_action_nodes, target)
            if post_transition is None:
                return None
            if before_transition[0] and not before_transition[0].isdisjoint(
                post_transition[0]
            ):
                return None
            if post_transition == before_transition:
                return None
            return next(
                (
                    node
                    for node in post_action_nodes
                    if node.role.casefold() in {"edit", "editcontrol"}
                    and node.stable_id is not None
                    and self._overlaps_region(node, composer)
                ),
                None,
            )

        self._wait_for_tree(
            desktop,
            target,
            new_chat_ready,
            f"new chat composer for {project!r} is unavailable",
        )
        desktop.paste_and_submit(target.window, composer.center, text)
