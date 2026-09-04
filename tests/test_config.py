import json
import os

import pytest

from codeaway.agents import SurfaceMap
from codeaway.config import AppConfig, WindowHint, default_config_path, load_config, save_config
from codeaway.desktop import FractionalRegion


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


@pytest.mark.parametrize(
    "values",
    [
        ("0", 0, 0.2, 0.2),
        (0, "0", 0.2, 0.2),
        (0, 0, "0.2", 0.2),
        (0, 0, 0.2, "0.2"),
        (0, 0, 0, 0.2),
        (0, 0, 0.2, 0),
        (0, 0, -0.2, 0.2),
        (0, 0, 0.2, -0.2),
        (-0.1, 0, 0.2, 0.2),
        (0, -0.1, 0.2, 0.2),
        (1.1, 0, 0.2, 0.2),
        (0, 1.1, 0.2, 0.2),
        (0.9, 0, 0.2, 0.2),
        (0, 0.9, 0.2, 0.2),
    ],
)
def test_fractional_region_rejects_invalid_calibration(values):
    with pytest.raises((TypeError, ValueError)):
        FractionalRegion(*values)


@pytest.mark.parametrize(
    "surface_values",
    [
        ["0", 0, 0.2, 0.2],
        [0, 0, "0.2", 0.2],
        [0, 0, 0, 0.2],
        [0, 0, -0.2, 0.2],
        [-0.1, 0, 0.2, 0.2],
        [0.9, 0, 0.2, 0.2],
    ],
)
def test_malformed_file_returns_defaults_with_one_warning_and_preserves_file(
    tmp_path, surface_values
):
    path = tmp_path / "config.json"
    original = {"selected_agent": "codex", "surfaces": {"sidebar": surface_values}}
    original_bytes = json.dumps(original).encode()
    path.write_bytes(original_bytes)

    loaded = load_config(path)

    assert loaded.config == AppConfig()
    assert len(loaded.warnings) == 1
    assert path.read_bytes() == original_bytes


def test_save_serializes_surface_rectangles_as_arrays(tmp_path):
    config = AppConfig(
        surfaces=SurfaceMap(
            FractionalRegion(0, 0, 0.2, 1),
            FractionalRegion(0.2, 0, 0.8, 0.8),
            FractionalRegion(0.3, 0.8, 0.6, 0.2),
        )
    )

    save_config(tmp_path / "config.json", config)

    assert json.loads((tmp_path / "config.json").read_text()) == {
        "bind_ip": "127.0.0.1",
        "port": 8765,
        "selected_agent": None,
        "selected_window": None,
        "surfaces": {
            "sidebar": [0, 0, 0.2, 1],
            "conversation": [0.2, 0, 0.8, 0.8],
            "composer": [0.3, 0.8, 0.6, 0.2],
        },
    }


def test_default_config_path_is_portable(monkeypatch):
    from pathlib import Path

    if os.name == "nt":
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")
        expected = Path(os.environ["LOCALAPPDATA"]) / "CodeAway" / "config.json"
    else:
        expected = Path.home() / ".config" / "codeaway" / "config.json"
    assert default_config_path() == expected
