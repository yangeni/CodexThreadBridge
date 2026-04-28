from __future__ import annotations

import configparser
from pathlib import Path
from typing import Optional, get_type_hints

from codex_thread_bridge import __version__
from codex_thread_bridge.config import BridgeConfig


def test_package_version_available() -> None:
    assert __version__ == "0.2.0"


def test_setup_cfg_version_matches_runtime_version() -> None:
    parser = configparser.ConfigParser()
    parser.read(Path(__file__).resolve().parents[1] / "setup.cfg")

    assert parser["metadata"]["version"] == __version__


def test_bridge_config_local_dev_derives_paths(tmp_path: Path) -> None:
    config = BridgeConfig.local_dev(tmp_path, {"owner-1"})

    assert config.project_root == tmp_path
    assert config.data_dir == tmp_path / "data"
    assert config.sqlite_path == tmp_path / "data" / "bridge.sqlite3"
    assert config.attachments_dir == tmp_path / "data" / "attachments"
    assert config.artifact_roots == (tmp_path / "exports",)
    assert config.owner_user_ids == frozenset({"owner-1"})
    assert config.group_qa_cwd == tmp_path
    assert config.max_artifact_bytes == 25 * 1024 * 1024
    assert ".ssh" in config.sensitive_path_markers


def test_bridge_config_type_hints_resolve_on_supported_python() -> None:
    hints = get_type_hints(BridgeConfig)

    assert hints["default_group_model"] == Optional[str]
