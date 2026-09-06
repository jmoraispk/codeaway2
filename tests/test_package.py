from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import zipfile
from email import message_from_bytes
from importlib import metadata, resources
import importlib
from pathlib import PurePosixPath

import pytest


@pytest.fixture(scope="session")
def built_artifacts(tmp_path_factory):
    uv = shutil.which("uv")
    assert uv is not None
    output_dir = tmp_path_factory.mktemp("built-artifacts")

    result = subprocess.run(
        [uv, "build", "--out-dir", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    return output_dir


def test_built_wheel_exposes_release_metadata_and_license(built_artifacts):
    wheel = next(built_artifacts.glob("codeaway-*.whl"))

    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = message_from_bytes(archive.read(metadata_name))
        names = archive.namelist()

    assert metadata["License-Expression"] == "MIT"
    assert set(metadata.get_all("Project-URL", [])) >= {
        "Homepage, https://github.com/jmoraispk/codeaway2",
        "Repository, https://github.com/jmoraispk/codeaway2",
        "Issues, https://github.com/jmoraispk/codeaway2/issues",
    }
    assert {
        "Development Status :: 3 - Alpha",
        "Operating System :: Microsoft :: Windows",
    }.issubset(metadata.get_all("Classifier", []))
    assert any(name.endswith("/LICENSE") for name in names)


def test_public_version_comes_from_installed_distribution_metadata(monkeypatch):
    import codeaway

    with monkeypatch.context() as context:
        context.setattr(metadata, "version", lambda name: "test-metadata-version")
        package = importlib.reload(codeaway)
        assert package.__version__ == "test-metadata-version"

    importlib.reload(codeaway)


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


def test_sdist_contains_web_assets_without_internal_artifacts(built_artifacts):
    archive = next(built_artifacts.glob("codeaway-*.tar.gz"))

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
