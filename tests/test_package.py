from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
from importlib import resources
from pathlib import PurePosixPath

import pytest


@pytest.mark.parametrize("name", ["setup.html", "index.html", "app.js", "style.css"])
def test_packaged_web_resource_is_readable(name):
    content = resources.files("codeaway.web").joinpath(name).read_bytes()

    assert content


def test_console_entry_point_help_exposes_only_public_options():
    executable = shutil.which("codeaway")
    assert executable is not None

    result = subprocess.run(
        [executable, "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--ip IPV4_ADDRESS" in result.stdout
    assert "--port PORT" in result.stdout
    assert "--no-browser" in result.stdout
    assert "--no-serve" not in result.stdout


def test_module_entry_point_help_returns_zero():
    result = subprocess.run(
        [sys.executable, "-m", "codeaway", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--ip IPV4_ADDRESS" in result.stdout


def test_sdist_contains_web_assets_without_internal_artifacts(tmp_path):
    uv = shutil.which("uv")
    assert uv is not None
    result = subprocess.run(
        [uv, "build", "--sdist", "--out-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    archive = next(tmp_path.glob("codeaway-*.tar.gz"))

    with tarfile.open(archive, "r:gz") as source:
        names = source.getnames()

    relative_parts = [PurePosixPath(name).parts[1:] for name in names]
    for asset in ("setup.html", "index.html", "app.js", "style.css"):
        assert any(parts == ("src", "codeaway", "web", asset) for parts in relative_parts)
    forbidden = {".superpowers", "output", "dist", "uv.lock"}
    cache_names = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
    assert not any(
        forbidden.intersection(parts) or cache_names.intersection(parts)
        for parts in relative_parts
    )
