from __future__ import annotations

from pathlib import Path

import pytest

from codex_thread_bridge.config import BridgeConfig


@pytest.fixture
def bridge_config(tmp_path: Path) -> BridgeConfig:
    return BridgeConfig.local_dev(tmp_path, {"owner-1"})
