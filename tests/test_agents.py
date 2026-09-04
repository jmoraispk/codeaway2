from dataclasses import dataclass

from codeaway.agents import AgentRegistry, AgentTarget, SurfaceMap
from codeaway.desktop import DesktopWindow, FractionalRegion, PixelRegion


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


def test_registry_resolve_rejects_same_process_window_not_matching_agent():
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

    assert result == AgentTarget("fake", accepted_window, saved)
