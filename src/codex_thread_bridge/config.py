from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class BridgeConfig:
    project_root: Path
    data_dir: Path
    sqlite_path: Path
    attachments_dir: Path
    artifact_roots: tuple[Path, ...]
    owner_user_ids: frozenset[str]
    group_qa_cwd: Path
    default_group_model: Optional[str] = None
    default_group_effort: Optional[str] = None
    max_artifact_bytes: int = 25 * 1024 * 1024
    sensitive_path_markers: tuple[str, ...] = field(
        default=(".ssh", ".codex", ".env", "keychain", "Library/Application Support")
    )

    @classmethod
    def local_dev(cls, project_root: Path, owner_user_ids: set[str]) -> "BridgeConfig":
        data_dir = project_root / "data"
        attachments_dir = data_dir / "attachments"
        return cls(
            project_root=project_root,
            data_dir=data_dir,
            sqlite_path=data_dir / "bridge.sqlite3",
            attachments_dir=attachments_dir,
            artifact_roots=(project_root / "exports",),
            owner_user_ids=frozenset(owner_user_ids),
            group_qa_cwd=project_root,
        )


@dataclass(frozen=True)
class OpeniLinkRuntimeConfig:
    bridge: BridgeConfig
    base_url: str
    bot_token: str
    owner_user_ids: frozenset[str]
    poll_timeout_seconds: float = 35.0
    request_timeout_seconds: float = 30.0
    controller_command: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "OpeniLinkRuntimeConfig":
        project_root = Path(_required_env("CTB_PROJECT_ROOT")).resolve()
        base_url = _required_env("ILINK_BASE_URL").rstrip("/")
        bot_token = _required_env("ILINK_BOT_TOKEN")
        owner_user_ids = _parse_csv_env("ILINK_OWNER_USER_IDS")
        bridge = BridgeConfig.local_dev(project_root, set(owner_user_ids))
        return cls(
            bridge=bridge,
            base_url=base_url,
            bot_token=bot_token,
            owner_user_ids=frozenset(owner_user_ids),
            poll_timeout_seconds=_float_env("ILINK_POLL_TIMEOUT_SECONDS", 35.0),
            request_timeout_seconds=_float_env("ILINK_REQUEST_TIMEOUT_SECONDS", 30.0),
            controller_command=tuple(_parse_csv_env("CTB_CONTROLLER_COMMAND", required=False)),
        )

    def __repr__(self) -> str:
        return (
            "OpeniLinkRuntimeConfig("
            "bridge=%r, base_url=%r, bot_token='***', owner_user_ids=%r, "
            "poll_timeout_seconds=%r, request_timeout_seconds=%r, controller_command=%r)"
            % (
                self.bridge,
                self.base_url,
                self.owner_user_ids,
                self.poll_timeout_seconds,
                self.request_timeout_seconds,
                self.controller_command,
            )
        )


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ValueError("missing required environment variable: %s" % name)
    return value.strip()


def _parse_csv_env(name: str, required: bool = True) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None or not value.strip():
        if required:
            raise ValueError("missing required environment variable: %s" % name)
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError:
        raise ValueError("invalid float environment variable: %s" % name) from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("invalid positive finite float environment variable: %s" % name)
    return parsed
