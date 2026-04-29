from __future__ import annotations

from pathlib import Path

import pytest

from codex_thread_bridge.config import OpeniLinkRuntimeConfig


def test_openilink_runtime_config_loads_required_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CTB_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ILINK_BASE_URL", "https://ilink.example.test/bot/")
    monkeypatch.setenv("ILINK_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("ILINK_OWNER_USER_IDS", "owner-1, owner-2")

    config = OpeniLinkRuntimeConfig.from_env()

    assert config.bridge.project_root == tmp_path
    assert config.base_url == "https://ilink.example.test/bot"
    assert config.bot_token == "secret-token"
    assert config.owner_user_ids == frozenset({"owner-1", "owner-2"})
    assert config.poll_timeout_seconds == 35.0
    assert config.request_timeout_seconds == 30.0
    assert config.controller_command == ()


def test_openilink_runtime_config_masks_secret_in_repr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CTB_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ILINK_BASE_URL", "https://ilink.example.test/bot")
    monkeypatch.setenv("ILINK_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("ILINK_OWNER_USER_IDS", "owner-1")

    text = repr(OpeniLinkRuntimeConfig.from_env())

    assert "secret-token" not in text
    assert "bot_token='***'" in text


@pytest.mark.parametrize("missing", ["ILINK_BASE_URL", "ILINK_BOT_TOKEN", "ILINK_OWNER_USER_IDS"])
def test_openilink_runtime_config_requires_channel_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    monkeypatch.setenv("CTB_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ILINK_BASE_URL", "https://ilink.example.test/bot")
    monkeypatch.setenv("ILINK_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("ILINK_OWNER_USER_IDS", "owner-1")
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ValueError, match=missing):
        OpeniLinkRuntimeConfig.from_env()
