from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_thread_bridge.config import OpeniLinkRuntimeConfig


def _set_required_openilink_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTB_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ILINK_BASE_URL", "https://ilink.example.test/bot")
    monkeypatch.setenv("ILINK_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("ILINK_OWNER_USER_IDS", "owner-1")


def test_openilink_runtime_config_loads_required_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_required_openilink_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ILINK_BASE_URL", "https://ilink.example.test/bot/")
    monkeypatch.setenv("ILINK_OWNER_USER_IDS", "owner-1, owner-2")

    config = OpeniLinkRuntimeConfig.from_env()

    assert config.bridge.project_root == tmp_path
    assert config.base_url == "https://ilink.example.test/bot"
    assert config.bot_token == "secret-token"
    assert config.owner_user_ids == frozenset({"owner-1", "owner-2"})
    assert config.poll_timeout_seconds == 35.0
    assert config.request_timeout_seconds == 30.0
    assert config.controller_command == ()


def test_openilink_runtime_config_loads_credentials_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "ilink_credentials.json"
    credentials_path.write_text(
        json.dumps(
            {
                "bot_token": "token-1",
                "account_id": "bot-1",
                "base_url": "https://ilinkai.weixin.qq.com/ilink/bot",
            }
        )
    )
    monkeypatch.setenv("CTB_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ILINK_CREDENTIALS_PATH", str(credentials_path))
    monkeypatch.setenv("ILINK_OWNER_USER_IDS", "owner-1")

    config = OpeniLinkRuntimeConfig.from_env()

    assert config.base_url == "https://ilinkai.weixin.qq.com/ilink/bot"
    assert config.bot_token == "token-1"


def test_openilink_runtime_config_resolves_relative_credentials_from_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "data" / "local" / "ilink_credentials.json"
    credentials_path.parent.mkdir(parents=True)
    credentials_path.write_text(
        json.dumps(
            {
                "bot_token": "token-1",
                "account_id": "bot-1",
                "base_url": "https://ilinkai.weixin.qq.com/ilink/bot",
            }
        )
    )
    monkeypatch.setenv("CTB_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ILINK_CREDENTIALS_PATH", "data/local/ilink_credentials.json")
    monkeypatch.setenv("ILINK_OWNER_USER_IDS", "owner-1")

    config = OpeniLinkRuntimeConfig.from_env()

    assert config.base_url == "https://ilinkai.weixin.qq.com/ilink/bot"
    assert config.bot_token == "token-1"


def test_openilink_runtime_config_masks_secret_in_repr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_required_openilink_env(tmp_path, monkeypatch)

    text = repr(OpeniLinkRuntimeConfig.from_env())

    assert "secret-token" not in text
    assert "bot_token='***'" in text


def test_openilink_runtime_config_accepts_positive_timeout_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_required_openilink_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ILINK_POLL_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("ILINK_REQUEST_TIMEOUT_SECONDS", "12.75")

    config = OpeniLinkRuntimeConfig.from_env()

    assert config.poll_timeout_seconds == 0.5
    assert config.request_timeout_seconds == 12.75


@pytest.mark.parametrize(
    "missing",
    ["CTB_PROJECT_ROOT", "ILINK_BASE_URL", "ILINK_BOT_TOKEN", "ILINK_OWNER_USER_IDS"],
)
def test_openilink_runtime_config_requires_channel_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    _set_required_openilink_env(tmp_path, monkeypatch)
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ValueError, match=missing):
        OpeniLinkRuntimeConfig.from_env()


@pytest.mark.parametrize("blank", ["", "   "])
def test_openilink_runtime_config_rejects_blank_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    blank: str,
) -> None:
    _set_required_openilink_env(tmp_path, monkeypatch)
    monkeypatch.setenv("CTB_PROJECT_ROOT", blank)

    with pytest.raises(ValueError, match="CTB_PROJECT_ROOT"):
        OpeniLinkRuntimeConfig.from_env()


@pytest.mark.parametrize(
    "name",
    ["ILINK_POLL_TIMEOUT_SECONDS", "ILINK_REQUEST_TIMEOUT_SECONDS"],
)
@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1"])
def test_openilink_runtime_config_rejects_invalid_timeout_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    _set_required_openilink_env(tmp_path, monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        OpeniLinkRuntimeConfig.from_env()
