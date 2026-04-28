from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from codex_thread_bridge.models import ExecutionPolicy, ThreadAlias


class BridgeStore:
    def __init__(self, sqlite_path: Path):
        self.sqlite_path = Path(sqlite_path)

    def initialize(self) -> None:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS thread_aliases (
                    alias TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    default_cwd TEXT NOT NULL,
                    sandbox TEXT NOT NULL,
                    approval_policy TEXT NOT NULL,
                    writable_roots_json TEXT NOT NULL,
                    model TEXT,
                    effort TEXT,
                    refresh_offset INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS contexts (
                    context_key TEXT PRIMARY KEY,
                    alias TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS wechat_groups (
                    group_id TEXT PRIMARY KEY,
                    group_alias TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    qa_session_id TEXT,
                    created_by TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_message_id TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifact_runs (
                    alias TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                """
            )

    def upsert_alias(
        self,
        alias,
        session_id,
        label,
        default_cwd,
        policy,
        created_by,
    ) -> None:
        now = self._now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT session_id FROM thread_aliases WHERE alias = ?",
                (alias,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO thread_aliases (
                    alias,
                    session_id,
                    label,
                    default_cwd,
                    sandbox,
                    approval_policy,
                    writable_roots_json,
                    model,
                    effort,
                    created_by,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alias) DO UPDATE SET
                    session_id=excluded.session_id,
                    label=excluded.label,
                    default_cwd=excluded.default_cwd,
                    sandbox=excluded.sandbox,
                    approval_policy=excluded.approval_policy,
                    writable_roots_json=excluded.writable_roots_json,
                    model=excluded.model,
                    effort=excluded.effort,
                    updated_at=excluded.updated_at
                """,
                (
                    alias,
                    session_id,
                    label,
                    default_cwd,
                    policy.sandbox,
                    policy.approval_policy,
                    json.dumps(list(policy.writable_roots)),
                    policy.model,
                    policy.effort,
                    created_by,
                    now,
                    now,
                ),
            )
            if existing is not None and str(existing["session_id"]) != str(session_id):
                connection.execute(
                    "DELETE FROM artifact_runs WHERE alias = ?",
                    (alias,),
                )

    def get_alias(self, alias: str) -> Optional[ThreadAlias]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM thread_aliases WHERE alias = ?",
                (alias,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_alias(row)

    def list_aliases(self) -> List[ThreadAlias]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM thread_aliases ORDER BY alias ASC"
            ).fetchall()
        return [self._row_to_alias(row) for row in rows]

    def remove_alias(self, alias: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM thread_aliases WHERE alias = ?",
                (alias,),
            )
            if cursor.rowcount > 0:
                connection.execute(
                    "DELETE FROM contexts WHERE alias = ?",
                    (alias,),
                )
                connection.execute(
                    "DELETE FROM artifact_runs WHERE alias = ?",
                    (alias,),
                )
        return cursor.rowcount > 0

    def set_active_alias(
        self, context_key, alias: str, owner_user_id: str
    ) -> None:
        now = self._now()
        serialized_key = self._serialize_context_key(context_key)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO contexts (
                    context_key,
                    alias,
                    owner_user_id,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(context_key) DO UPDATE SET
                    alias=excluded.alias,
                    owner_user_id=excluded.owner_user_id,
                    updated_at=excluded.updated_at
                """,
                (serialized_key, alias, owner_user_id, now, now),
            )

    def get_active_alias(self, context_key) -> Optional[str]:
        serialized_key = self._serialize_context_key(context_key)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT alias FROM contexts WHERE context_key = ?",
                (serialized_key,),
            ).fetchone()
        if row is None:
            return None
        return str(row["alias"])

    def record_pending_group(
        self, group_id: str, group_alias: str, created_by: str
    ) -> None:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO wechat_groups (
                    group_id,
                    group_alias,
                    status,
                    qa_session_id,
                    created_by,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    group_alias=excluded.group_alias,
                    status=excluded.status,
                    qa_session_id=excluded.qa_session_id,
                    updated_at=excluded.updated_at
                """,
                (group_id, group_alias, "pending", None, created_by, now, now),
            )

    def activate_group(self, group_alias: str, qa_session_id: str) -> None:
        self._set_group_status(group_alias, "active", qa_session_id)

    def disable_group(self, group_alias: str) -> None:
        self._set_group_status(group_alias, "disabled", None)

    def get_group_by_alias(self, group_alias: str) -> Optional[Dict[str, object]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM wechat_groups WHERE group_alias = ?",
                (group_alias,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def get_group_by_id(self, group_id: str) -> Optional[Dict[str, object]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM wechat_groups WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_groups(self) -> List[Dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM wechat_groups ORDER BY group_alias ASC, group_id ASC"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_refresh_offset(self, alias: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT refresh_offset FROM thread_aliases WHERE alias = ?",
                (alias,),
            ).fetchone()
        if row is None:
            return 0
        return int(row["refresh_offset"])

    def set_refresh_offset(self, alias: str, line_number: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE thread_aliases SET refresh_offset = ?, updated_at = ? WHERE alias = ?",
                (line_number, self._now(), alias),
            )

    def record_artifact(
        self,
        run_id,
        alias,
        session_id,
        local_path,
        mime_type,
        size_bytes,
        status,
        reason,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO artifacts (
                    run_id,
                    alias,
                    session_id,
                    local_path,
                    mime_type,
                    size_bytes,
                    status,
                    reason,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    alias,
                    session_id,
                    local_path,
                    mime_type,
                    size_bytes,
                    status,
                    reason,
                    self._now(),
                ),
            )
        return int(cursor.lastrowid)

    def record_artifact_run(self, alias: str, run_id: str, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifact_runs (
                    alias,
                    run_id,
                    session_id,
                    updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(alias) DO UPDATE SET
                    run_id=excluded.run_id,
                    session_id=excluded.session_id,
                    updated_at=excluded.updated_at
                """,
                (alias, run_id, session_id, self._now()),
            )

    def get_latest_artifact_run(self, alias: str) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id FROM artifact_runs WHERE alias = ?",
                (alias,),
            ).fetchone()
        if row is None:
            return None
        return str(row["run_id"])

    def list_artifacts(self, alias: Optional[str] = None) -> List[Dict[str, object]]:
        with self._connect() as connection:
            if alias is None:
                rows = connection.execute(
                    "SELECT * FROM artifacts ORDER BY id ASC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM artifacts WHERE alias = ? ORDER BY id ASC",
                    (alias,),
                ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.sqlite_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _row_to_alias(self, row: sqlite3.Row) -> ThreadAlias:
        policy = ExecutionPolicy(
            sandbox=str(row["sandbox"]),
            approval_policy=str(row["approval_policy"]),
            writable_roots=tuple(json.loads(str(row["writable_roots_json"]))),
            model=row["model"],
            effort=row["effort"],
        )
        return ThreadAlias(
            alias=str(row["alias"]),
            session_id=str(row["session_id"]),
            label=str(row["label"]),
            default_cwd=str(row["default_cwd"]),
            policy=policy,
        )

    def _set_group_status(
        self, group_alias: str, status: str, qa_session_id: Optional[str]
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE wechat_groups
                SET status = ?, qa_session_id = ?, updated_at = ?
                WHERE group_alias = ?
                """,
                (status, qa_session_id, self._now(), group_alias),
            )

    def _serialize_context_key(self, context_key) -> str:
        if isinstance(context_key, str):
            return json.dumps(context_key)
        return json.dumps(list(context_key), ensure_ascii=True)

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, object]:
        return {key: row[key] for key in row.keys()}

    def _now(self) -> int:
        return int(time.time())
