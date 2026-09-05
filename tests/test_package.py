from __future__ import annotations

import shutil
import subprocess
import sys
from importlib import resources

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
    assert "--ip ADDRESS" in result.stdout
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
    assert "--ip ADDRESS" in result.stdout
