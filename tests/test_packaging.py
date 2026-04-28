from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from codex_thread_bridge import __version__

pytestmark = pytest.mark.packaging


def test_installed_package_imports_with_matching_metadata(tmp_path: Path) -> None:
    target = tmp_path / "install"
    project_root = Path(__file__).resolve().parents[1]

    subprocess.run(
        [sys.executable, "-m", "pip", "install", ".", "--no-deps", "--target", str(target)],
        cwd=project_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    code = (
        "import importlib.metadata; "
        "import codex_thread_bridge; "
        "print(codex_thread_bridge.__version__); "
        "print(importlib.metadata.version('codex-thread-bridge'))"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(target)
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    assert result.stdout.splitlines() == [__version__, __version__]
