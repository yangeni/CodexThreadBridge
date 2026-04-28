from __future__ import annotations

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
