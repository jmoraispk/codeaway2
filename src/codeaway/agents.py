import os
from dataclasses import dataclass
from typing import Literal, Protocol

from .desktop import DesktopBackend, DesktopWindow, FractionalRegion


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
        windows = desktop.list_windows()
        candidates = [
            window
            for window in windows
            if os.path.normcase(window.process_path) == os.path.normcase(process_path)
            and any(agent.id == agent_id for agent in self._agents)
        ]
        if not candidates:
            return None

        selected = None
        if title_hint is not None:
            selected = next((window for window in candidates if window.title == title_hint), None)
            if selected is None:
                title_lower = title_hint.casefold()
                selected = next(
                    (window for window in candidates if title_lower in window.title.casefold()),
                    None,
                )
        if selected is None:
            selected = candidates[0]
        return AgentTarget(agent_id, selected, surfaces)
