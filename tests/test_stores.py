from __future__ import annotations

import sqlite3

from codex_thread_bridge.models import ExecutionPolicy
from codex_thread_bridge.stores import BridgeStore


def test_alias_context_and_group_lifecycle(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()

    store.upsert_alias(
        alias="code",
        session_id="019-code",
        label="Code",
        default_cwd="/tmp/project",
        policy=ExecutionPolicy.work_default("/tmp/project"),
        created_by="owner-1",
    )

    alias = store.get_alias("code")

    assert alias is not None
    assert alias.session_id == "019-code"
    assert alias.policy.approval_policy == "on-request"

    context_key = ("wechat", "private", "owner-chat", "owner-chat")
    store.set_active_alias(context_key, "code", "owner-1")

    assert store.get_active_alias(context_key) == "code"

    store.record_pending_group(
        group_id="group-1",
        group_alias="friends",
        created_by="owner-1",
    )

    group = store.get_group_by_alias("friends")

    assert group is not None
    assert group["status"] == "pending"
    assert group["qa_session_id"] is None

    store.activate_group("friends", "019-group-qa")

    group = store.get_group_by_id("group-1")

    assert group is not None
    assert group["status"] == "active"
    assert group["qa_session_id"] == "019-group-qa"


def test_artifact_statuses_are_persisted(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()

    artifact_id = store.record_artifact(
        run_id="run-1",
        alias="code",
        session_id="019-code",
        local_path="/tmp/project/exports/report.md",
        mime_type="text/markdown",
        size_bytes=10,
        status="allowed",
        reason="created during current run",
    )

    artifact = store.list_artifacts("code")[0]

    assert artifact["id"] == artifact_id
    assert artifact["status"] == "allowed"


def test_list_aliases_remove_alias_and_refresh_offset(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()

    store.upsert_alias(
        alias="b",
        session_id="019-b",
        label="B",
        default_cwd="/tmp/b",
        policy=ExecutionPolicy.work_default("/tmp/b"),
        created_by="owner-1",
    )
    store.upsert_alias(
        alias="a",
        session_id="019-a",
        label="A",
        default_cwd="/tmp/a",
        policy=ExecutionPolicy.work_default("/tmp/a"),
        created_by="owner-1",
    )

    assert [alias.alias for alias in store.list_aliases()] == ["a", "b"]
    assert store.get_refresh_offset("a") == 0

    store.set_refresh_offset("a", 12)

    assert store.get_refresh_offset("a") == 12
    assert store.remove_alias("b") is True
    assert store.remove_alias("missing") is False
    assert [alias.alias for alias in store.list_aliases()] == ["a"]


def test_remove_alias_clears_context_pointers(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()

    store.upsert_alias(
        alias="code",
        session_id="019-code",
        label="Code",
        default_cwd="/tmp/code",
        policy=ExecutionPolicy.work_default("/tmp/code"),
        created_by="owner-1",
    )
    context_key = ("wechat", "private", "owner-chat", "owner-chat")
    store.set_active_alias(context_key, "code", "owner-1")

    assert store.remove_alias("code") is True
    assert store.get_alias("code") is None
    assert store.get_active_alias(context_key) is None


def test_upsert_alias_preserves_creator_provenance(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()

    store.upsert_alias(
        alias="code",
        session_id="019-code",
        label="Code",
        default_cwd="/tmp/code",
        policy=ExecutionPolicy.work_default("/tmp/code"),
        created_by="owner-1",
    )
    store.upsert_alias(
        alias="code",
        session_id="019-code-2",
        label="Code 2",
        default_cwd="/tmp/code-2",
        policy=ExecutionPolicy.work_default("/tmp/code-2"),
        created_by="owner-2",
    )

    with sqlite3.connect(bridge_config.sqlite_path) as connection:
        row = connection.execute(
            "SELECT created_by, session_id, label, default_cwd FROM thread_aliases WHERE alias = ?",
            ("code",),
        ).fetchone()

    assert row == ("owner-1", "019-code-2", "Code 2", "/tmp/code-2")


def test_record_pending_group_preserves_creator_provenance(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()

    store.record_pending_group(
        group_id="group-1",
        group_alias="friends",
        created_by="owner-1",
    )
    store.record_pending_group(
        group_id="group-1",
        group_alias="friends-v2",
        created_by="owner-2",
    )

    group = store.get_group_by_id("group-1")

    assert group is not None
    assert group["created_by"] == "owner-1"
    assert group["group_alias"] == "friends-v2"
    assert group["status"] == "pending"


def test_list_artifacts_without_alias_returns_all(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()

    first_id = store.record_artifact(
        run_id="run-1",
        alias="code",
        session_id="019-code",
        local_path="/tmp/project/exports/one.md",
        mime_type="text/markdown",
        size_bytes=1,
        status="allowed",
        reason="one",
    )
    second_id = store.record_artifact(
        run_id="run-2",
        alias="chat",
        session_id="019-chat",
        local_path="/tmp/project/exports/two.md",
        mime_type="text/markdown",
        size_bytes=2,
        status="blocked",
        reason="two",
    )

    artifacts = store.list_artifacts()

    assert [artifact["id"] for artifact in artifacts] == [first_id, second_id]


def test_record_and_get_latest_artifact_run(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()

    assert store.get_latest_artifact_run("code") is None

    store.record_artifact_run(
        alias="code",
        run_id="run-1",
        session_id="019-code",
    )
    store.record_artifact_run(
        alias="code",
        run_id="run-2",
        session_id="019-code",
    )

    assert store.get_latest_artifact_run("code") == "run-2"


def test_rebinding_or_removing_alias_clears_latest_artifact_run(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()

    store.upsert_alias(
        alias="code",
        session_id="session-old",
        label="Code",
        default_cwd="/tmp/code-old",
        policy=ExecutionPolicy.work_default("/tmp/code-old"),
        created_by="owner-1",
    )
    store.record_artifact_run(
        alias="code",
        run_id="run-old",
        session_id="session-old",
    )

    store.upsert_alias(
        alias="code",
        session_id="session-new",
        label="Code",
        default_cwd="/tmp/code-new",
        policy=ExecutionPolicy.work_default("/tmp/code-new"),
        created_by="owner-1",
    )

    assert store.get_latest_artifact_run("code") is None

    store.record_artifact_run(
        alias="code",
        run_id="run-new",
        session_id="session-new",
    )
    assert store.get_latest_artifact_run("code") == "run-new"

    assert store.remove_alias("code") is True
    assert store.get_latest_artifact_run("code") is None
