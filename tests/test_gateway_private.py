from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Optional, get_type_hints

from codex_thread_bridge.controller_client import ControllerRunResult
from codex_thread_bridge.config import BridgeConfig
from codex_thread_bridge.gateway import Gateway
from codex_thread_bridge.models import (
    ConversationType,
    ExecutionPolicy,
    IncomingMessage,
    SenderRole,
)
from codex_thread_bridge.stores import BridgeStore


@dataclass
class FakeControllerClient:
    starts: list[dict[str, object]] = field(default_factory=list)
    recovers: list[dict[str, object]] = field(default_factory=list)
    status_by_session: dict[str, dict] = field(default_factory=dict)
    created_session_count: int = 0

    def status(self, session_id: str) -> dict:
        return self.status_by_session.get(
            session_id,
            {
                "session_id": session_id,
                "locked": False,
                "dirty": False,
                "reconcile_required": False,
                "session_head": "head-1",
            },
        )

    def start_or_send(
        self,
        *,
        session_id: Optional[str],
        cwd: str,
        message: str,
        owner: str,
        policy: ExecutionPolicy,
        idempotency_key: str,
        expected_session_head: Optional[str],
        intent: str = "direct_message",
    ) -> ControllerRunResult:
        kwargs = {
            "session_id": session_id,
            "cwd": cwd,
            "message": message,
            "owner": owner,
            "policy": policy,
            "idempotency_key": idempotency_key,
            "expected_session_head": expected_session_head,
            "intent": intent,
        }
        self.starts.append(kwargs)
        if session_id is None:
            self.created_session_count += 1
            result_session_id = "created-session-%d" % self.created_session_count
        else:
            result_session_id = session_id
        return ControllerRunResult(
            run_id="run-1",
            session_id=result_session_id,
            session_head="head-2",
            status="completed",
            text="done",
            approval_summary=None,
        )

    def recover_session(
        self,
        *,
        session_id: str,
        owner: str,
        human_authorized: bool,
    ) -> dict:
        result = {
            "session_id": session_id,
            "owner": owner,
            "human_authorized": human_authorized,
            "released": True,
            "acked_runs": ["run-old"],
            "cancelled_runs": [],
            "closed_runs": ["run-old"],
        }
        self.recovers.append(result)
        return result


def test_controller_result_model() -> None:
    result = ControllerRunResult(
        run_id="run-1",
        session_id="session-1",
        session_head="head-2",
        status="completed",
        text="done",
        approval_summary=None,
    )

    assert result.text == "done"


def test_controller_run_result_type_hints_resolve_optional_str() -> None:
    hints = get_type_hints(ControllerRunResult)

    assert hints["approval_summary"] == Optional[str]


def test_fake_controller_client_records_calls_and_defaults_status() -> None:
    client = FakeControllerClient()

    status = client.status("session-1")
    result = client.start_or_send(
        session_id="session-1",
        cwd="/tmp/work",
        message="hello",
        owner="owner-1",
        policy=ExecutionPolicy.work_default("/tmp/work"),
        idempotency_key="id-1",
        expected_session_head=None,
    )

    assert status == {
        "session_id": "session-1",
        "locked": False,
        "dirty": False,
        "reconcile_required": False,
        "session_head": "head-1",
    }
    assert client.starts[0]["message"] == "hello"
    assert result.session_id == "session-1"


def test_fake_controller_client_creates_session_id_when_missing() -> None:
    client = FakeControllerClient()

    result = client.start_or_send(
        session_id=None,
        cwd="/tmp/work",
        message="hello",
        owner="owner-1",
        policy=ExecutionPolicy.work_default("/tmp/work"),
        idempotency_key="id-1",
        expected_session_head=None,
    )

    assert client.starts[0]["session_id"] is None
    assert result.session_id == "created-session-1"


def test_fake_controller_client_returns_configured_status() -> None:
    client = FakeControllerClient(
        status_by_session={
            "session-2": {
                "session_id": "session-2",
                "locked": True,
                "dirty": True,
                "reconcile_required": True,
                "session_head": "head-x",
            }
        }
    )

    assert client.status("session-2") == {
        "session_id": "session-2",
        "locked": True,
        "dirty": True,
        "reconcile_required": True,
        "session_head": "head-x",
    }


def private_msg(text: str, raw_ref: str = "m-private") -> IncomingMessage:
    return IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.PRIVATE,
        conversation_id="owner-chat",
        thread_key="owner-chat",
        sender_id="owner-1",
        sender_role=SenderRole.OWNER,
        text=text,
        attachments=(),
        raw_ref=raw_ref,
    )


def _gateway_for(tmp_path: Path) -> tuple[Gateway, BridgeConfig, BridgeStore, FakeControllerClient]:
    config = BridgeConfig.local_dev(tmp_path, {"owner-1"})
    store = BridgeStore(config.sqlite_path)
    store.initialize()
    controller = FakeControllerClient()
    return Gateway(config, store, controller), config, store, controller


def _ready_status(**overrides: object) -> dict[str, object]:
    status = {
        "locked": False,
        "dirty": False,
        "reconcile_required": False,
        "session_head": "head-1",
        "cwd": "/tmp/target-workspace",
    }
    status.update(overrides)
    return status


def test_private_add_use_and_plain_dispatch(tmp_path: Path) -> None:
    gateway, config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code"
    )

    add_reply = gateway.handle(private_msg("/add code 019-code"))
    use_reply = gateway.handle(private_msg("/use code"))
    dispatch_reply = gateway.handle(private_msg("continue implementation"))
    alias = store.get_alias("code")

    assert "code" in add_reply.text
    assert "code" in use_reply.text
    assert dispatch_reply.text == "done"
    assert alias is not None
    assert controller.starts[0]["session_id"] == "019-code"
    assert controller.starts[0]["cwd"] == "/tmp/codex-target/code"
    assert controller.starts[0]["owner"] == "ctb-private:owner-1"
    assert controller.starts[0]["idempotency_key"] == "m-private:code"
    assert controller.starts[0]["expected_session_head"] is None
    assert controller.starts[0]["intent"] == "direct_message"
    assert controller.starts[0]["policy"].writable_roots == ("/tmp/codex-target/code",)
    assert controller.starts[0]["policy"].approval_policy == "on-request"
    assert alias.default_cwd == "/tmp/codex-target/code"
    assert alias.policy.writable_roots == ("/tmp/codex-target/code",)


def test_private_dispatch_does_not_fence_on_released_status_head_after_app_edits(
    tmp_path: Path,
) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    store.upsert_alias(
        "code",
        "019-code",
        "019-code",
        "/tmp/codex-target/code",
        ExecutionPolicy.work_default("/tmp/codex-target/code"),
        "owner-1",
    )
    store.set_active_alias(private_msg("/use code").context_key, "code", "owner-1")
    controller.status_by_session["019-code"] = _ready_status(
        status="released",
        session_head="stale-before-app-message",
    )

    reply = gateway.handle(private_msg("continue from phone"))

    assert reply.text == "done"
    assert controller.starts[0]["expected_session_head"] is None


def test_private_plan_command_uses_plan_review_intent(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    store.upsert_alias(
        "code",
        "019-code",
        "019-code",
        "/tmp/codex-target/code",
        ExecutionPolicy.work_default("/tmp/codex-target/code"),
        "owner-1",
    )
    store.set_active_alias(private_msg("/use code").context_key, "code", "owner-1")

    reply = gateway.handle(private_msg("/plan 请评估这个操作", raw_ref="m-plan"))

    assert reply.text == "done"
    assert controller.starts[0]["message"] == "请评估这个操作"
    assert controller.starts[0]["intent"] == "plan_review"
    assert controller.starts[0]["idempotency_key"] == "m-plan:code"


def test_private_recover_alias_recovers_without_model_turn(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    store.upsert_alias(
        "code",
        "019-code",
        "019-code",
        "/tmp/codex-target/code",
        ExecutionPolicy.work_default("/tmp/codex-target/code"),
        "owner-1",
    )

    reply = gateway.handle(private_msg("/recover code"))

    assert "Recovered code" in reply.text
    assert controller.starts == []
    assert controller.recovers == [
        {
            "session_id": "019-code",
            "owner": "ctb-private:owner-1",
            "human_authorized": True,
            "released": True,
            "acked_runs": ["run-old"],
            "cancelled_runs": [],
            "closed_runs": ["run-old"],
        }
    ]


def test_private_plain_message_without_active_alias_is_rejected(tmp_path: Path) -> None:
    gateway, _config, _store, _controller = _gateway_for(tmp_path)

    reply = gateway.handle(private_msg("continue implementation"))

    assert "/use" in reply.text


def test_private_use_unknown_alias_is_rejected(tmp_path: Path) -> None:
    gateway, _config, _store, _controller = _gateway_for(tmp_path)

    reply = gateway.handle(private_msg("/use missing"))

    assert "Unknown alias" in reply.text


def test_private_add_reads_workspace_from_local_session_meta_when_status_lacks_it(
    tmp_path: Path,
) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    session_path = _config.data_dir / "sessions" / "019-code.jsonl"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": "019-code",
                    "cwd": "/tmp/codex-target/from-session-meta",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    reply = gateway.handle(private_msg("/add code 019-code"))
    alias = store.get_alias("code")

    assert "Added alias code -> 019-code" in reply.text
    assert alias is not None
    assert alias.default_cwd == "/tmp/codex-target/from-session-meta"


def test_private_add_fails_closed_without_workspace_metadata(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(cwd=None)

    reply = gateway.handle(private_msg("/add code 019-code"))

    assert "workspace" in reply.text.lower()
    assert store.get_alias("code") is None


def test_private_add_prefers_cwd_over_other_workspace_keys(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/preferred-cwd",
        workspace_root="/tmp/fallback-workspace-root",
        project_root="/tmp/fallback-project-root",
        default_cwd="/tmp/fallback-default-cwd",
    )

    gateway.handle(private_msg("/add code 019-code"))

    alias = store.get_alias("code")

    assert alias is not None
    assert alias.default_cwd == "/tmp/preferred-cwd"
    assert alias.policy.writable_roots == ("/tmp/preferred-cwd",)


def test_private_add_accepts_workspace_root_fallback(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(
        cwd=None,
        workspace_root="/tmp/workspace-root-only",
        project_root=None,
        default_cwd=None,
    )

    gateway.handle(private_msg("/add code 019-code"))

    alias = store.get_alias("code")

    assert alias is not None
    assert alias.default_cwd == "/tmp/workspace-root-only"
    assert alias.policy.writable_roots == ("/tmp/workspace-root-only",)


def test_private_locked_alias_is_not_dispatched(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code",
        locked=True,
        session_head="head-9",
    )

    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))

    reply = gateway.handle(private_msg("continue implementation"))

    assert "not ready" in reply.text
    assert controller.starts == []


def test_private_approval_summary_is_returned(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code"
    )
    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))

    def _start_or_send(**kwargs):
        controller.starts.append(kwargs)
        return ControllerRunResult(
            run_id="run-1",
            session_id="019-code",
            session_head="head-2",
            status="waiting_approval",
            text="done",
            approval_summary="Needs approval",
        )

    controller.start_or_send = _start_or_send

    reply = gateway.handle(private_msg("continue implementation"))

    assert reply.text == "Needs approval"


def test_private_non_owner_message_is_rejected(tmp_path: Path) -> None:
    gateway, _config, _store, _controller = _gateway_for(tmp_path)
    msg = IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.PRIVATE,
        conversation_id="member-chat",
        thread_key="member-chat",
        sender_id="member-1",
        sender_role=SenderRole.MEMBER,
        text="/list",
        attachments=(),
        raw_ref="msg-2",
    )

    reply = gateway.handle(msg)

    assert reply.text == "Rejected: sender is not owner"


def test_private_list_aliases_formats_entries_and_empty_state(tmp_path: Path) -> None:
    gateway, _config, _store, _controller = _gateway_for(tmp_path)
    _controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code"
    )

    empty_reply = gateway.handle(private_msg("/list"))
    gateway.handle(private_msg("/add code 019-code"))
    list_reply = gateway.handle(private_msg("/list"))

    assert empty_reply.text == "No aliases."
    assert "code -> 019-code" in list_reply.text


def test_private_remove_alias_clears_alias_and_active_context(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code"
    )

    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))
    reply = gateway.handle(private_msg("/remove code"))
    dispatch_reply = gateway.handle(private_msg("continue implementation"))

    assert "Removed alias code" in reply.text
    assert store.get_alias("code") is None
    assert "No active thread" in dispatch_reply.text
    assert controller.starts == []


def test_private_bind_compat_adds_default_alias_and_selects_it(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code"
    )

    bind_reply = gateway.handle(private_msg("/bind 019-code"))
    dispatch_reply = gateway.handle(private_msg("continue implementation"))
    alias = store.get_alias("default")

    assert "Bound default -> 019-code" in bind_reply.text
    assert alias is not None
    assert dispatch_reply.text == "done"
    assert controller.starts[0]["session_id"] == "019-code"


def test_private_send_once_dispatches_without_switching_active_alias(
    tmp_path: Path,
) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code"
    )

    gateway.handle(private_msg("/add code 019-code"))
    reply = gateway.handle(private_msg("/send code run smoke"))

    assert reply.text == "done"
    assert controller.starts[0]["message"] == "run smoke"
    assert store.get_active_alias(private_msg("check").context_key) is None


def test_private_send_once_unknown_alias_is_clear(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)

    reply = gateway.handle(private_msg("/send missing run smoke"))

    assert "Unknown alias: missing" in reply.text
    assert controller.starts == []


def test_private_help_lists_available_commands(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)

    reply = gateway.handle(private_msg("/help"))

    assert "/add <alias> <session_id>" in reply.text
    assert "/refresh [alias]" in reply.text
    assert controller.starts == []


def test_private_missing_active_alias_is_reported(tmp_path: Path) -> None:
    gateway, config, _store, _controller = _gateway_for(tmp_path)
    _controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code"
    )

    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))
    with sqlite3.connect(config.sqlite_path) as connection:
        connection.execute(
            "DELETE FROM thread_aliases WHERE alias = ?",
            ("code",),
        )

    reply = gateway.handle(private_msg("continue implementation"))

    assert "Active alias no longer exists: code" in reply.text


def test_private_status_for_named_alias_reports_flags_without_dispatch(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code",
        locked=True,
        dirty=True,
        reconcile_required=False,
        session_head="head-7",
    )

    gateway.handle(private_msg("/add code 019-code"))
    reply = gateway.handle(private_msg("/status code"))

    assert "code" in reply.text
    assert "locked=True" in reply.text
    assert "dirty=True" in reply.text
    assert "reconcile_required=False" in reply.text
    assert "session_head=head-7" in reply.text
    assert controller.starts == []


def test_private_status_without_active_alias_is_clear(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)

    reply = gateway.handle(private_msg("/status"))

    assert "No active thread" in reply.text
    assert controller.starts == []


def test_private_refresh_reads_local_session_without_model_turn(tmp_path: Path) -> None:
    gateway, config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code"
    )
    session_path = config.data_dir / "sessions" / "019-code.jsonl"
    session_path.parent.mkdir(parents=True)
    rows = [
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hello"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "world"}],
            },
        },
    ]
    session_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    gateway.handle(private_msg("/add code 019-code"))
    reply = gateway.handle(private_msg("/refresh code"))

    assert reply.text == "user: hello\nassistant: world"
    assert store.get_refresh_offset("code") == 2
    assert controller.starts == []


def test_private_refresh_missing_history_does_not_advance_offset(
    tmp_path: Path,
) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code"
    )
    gateway.handle(private_msg("/add code 019-code"))
    store.set_refresh_offset("code", 7)

    reply = gateway.handle(private_msg("/refresh code"))

    assert "Unable to read local session history" in reply.text
    assert store.get_refresh_offset("code") == 7
    assert controller.starts == []


def test_private_refresh_without_active_alias_is_clear(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)

    reply = gateway.handle(private_msg("/refresh"))

    assert "No active thread" in reply.text
    assert controller.starts == []


def test_refresh_history_lookup_uses_exact_session_id_suffix(
    tmp_path: Path,
    monkeypatch,
) -> None:
    gateway, _config, _store, _controller = _gateway_for(tmp_path)
    home = tmp_path / "home"
    sessions = home / ".codex" / "sessions" / "2026" / "04" / "28"
    sessions.mkdir(parents=True)
    wrong = sessions / "rollout-2026-04-28T00-00-00-prefixabc.jsonl"
    right = sessions / "rollout-2026-04-28T00-00-01-abc.jsonl"
    wrong.write_text("wrong\n", encoding="utf-8")
    right.write_text("right\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    assert gateway._session_history_path("abc") == right


def test_private_status_uses_active_alias_without_dispatch(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code",
        locked=False,
        dirty=False,
        reconcile_required=True,
        session_head="head-active",
    )

    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))
    reply = gateway.handle(private_msg("/status"))

    assert "code" in reply.text
    assert "locked=False" in reply.text
    assert "dirty=False" in reply.text
    assert "reconcile_required=True" in reply.text
    assert "session_head=head-active" in reply.text
    assert controller.starts == []


def test_private_artifacts_lists_allowed_result_from_dispatch(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)
    export_dir = tmp_path / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    report_path = export_dir / "report.md"
    controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code"
    )
    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))

    def _start_or_send(**kwargs):
        controller.starts.append(kwargs)
        report_path.write_text("# Report\n", encoding="utf-8")
        return ControllerRunResult(
            run_id="run-1",
            session_id="019-code",
            session_head="head-2",
            status="completed",
            text="Created %s" % report_path,
            approval_summary=None,
        )

    controller.start_or_send = _start_or_send

    gateway.handle(private_msg("continue implementation"))
    reply = gateway.handle(private_msg("/artifacts code"))

    assert "allowed" in reply.text
    assert str(report_path.resolve()) in reply.text


def test_private_artifacts_without_active_alias_requires_use_guidance(tmp_path: Path) -> None:
    gateway, _config, _store, _controller = _gateway_for(tmp_path)

    reply = gateway.handle(private_msg("/artifacts"))

    assert reply.text == "No active thread. Use /use <alias> first."


def test_private_artifacts_unknown_alias_is_rejected(tmp_path: Path) -> None:
    gateway, _config, _store, _controller = _gateway_for(tmp_path)

    reply = gateway.handle(private_msg("/artifacts missing"))

    assert reply.text == "Unknown alias: missing"


def test_private_artifacts_without_alias_uses_current_active_alias_scope(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(cwd="/tmp/codex-target/code")
    controller.status_by_session["019-paper"] = _ready_status(cwd="/tmp/codex-target/paper")
    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/add paper 019-paper"))
    gateway.handle(private_msg("/use code"))

    code_path = str((tmp_path / "exports" / "code.md").resolve())
    paper_path = str((tmp_path / "exports" / "paper.md").resolve())
    store.record_artifact(
        run_id="run-code",
        alias="code",
        session_id="019-code",
        local_path=code_path,
        mime_type="application/octet-stream",
        size_bytes=4,
        status="allowed",
        reason="within allowed artifact roots",
    )
    store.record_artifact(
        run_id="run-paper",
        alias="paper",
        session_id="019-paper",
        local_path=paper_path,
        mime_type="application/octet-stream",
        size_bytes=5,
        status="allowed",
        reason="within allowed artifact roots",
    )

    reply = gateway.handle(private_msg("/artifacts"))

    assert code_path in reply.text
    assert paper_path not in reply.text


def test_private_artifacts_lists_only_latest_run_for_alias(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(cwd="/tmp/codex-target/code")
    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))

    older_path = str((tmp_path / "exports" / "older.md").resolve())
    latest_path = str((tmp_path / "exports" / "latest.md").resolve())
    store.record_artifact(
        run_id="run-old",
        alias="code",
        session_id="019-code",
        local_path=older_path,
        mime_type="application/octet-stream",
        size_bytes=4,
        status="allowed",
        reason="within allowed artifact roots",
    )
    store.record_artifact(
        run_id="run-new",
        alias="code",
        session_id="019-code",
        local_path=latest_path,
        mime_type="application/octet-stream",
        size_bytes=5,
        status="allowed",
        reason="within allowed artifact roots",
    )

    reply = gateway.handle(private_msg("/artifacts"))

    assert latest_path in reply.text
    assert older_path not in reply.text


def test_private_sendfile_returns_placeholder_for_allowed_artifact(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(cwd="/tmp/codex-target/code")
    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))
    artifact_id = store.record_artifact(
        run_id="run-1",
        alias="code",
        session_id="019-code",
        local_path=str((tmp_path / "exports" / "report.md").resolve()),
        mime_type="application/octet-stream",
        size_bytes=7,
        status="allowed",
        reason="within allowed artifact roots",
    )

    reply = gateway.handle(private_msg("/sendfile %s" % artifact_id))

    assert "would send" in reply.text.lower()
    assert str(artifact_id) in reply.text


def test_private_sendfile_all_uses_only_allowed_artifacts_from_latest_run(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(cwd="/tmp/codex-target/code")
    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))

    older_allowed_path = str((tmp_path / "exports" / "older-allowed.md").resolve())
    latest_allowed_path = str((tmp_path / "exports" / "latest-allowed.md").resolve())
    latest_blocked_path = str((tmp_path / "exports" / "latest-blocked.md").resolve())
    store.record_artifact(
        run_id="run-old",
        alias="code",
        session_id="019-code",
        local_path=older_allowed_path,
        mime_type="application/octet-stream",
        size_bytes=4,
        status="allowed",
        reason="within allowed artifact roots",
    )
    latest_allowed_id = store.record_artifact(
        run_id="run-new",
        alias="code",
        session_id="019-code",
        local_path=latest_allowed_path,
        mime_type="application/octet-stream",
        size_bytes=5,
        status="allowed",
        reason="within allowed artifact roots",
    )
    store.record_artifact(
        run_id="run-new",
        alias="code",
        session_id="019-code",
        local_path=latest_blocked_path,
        mime_type="application/octet-stream",
        size_bytes=6,
        status="blocked",
        reason="outside allowed artifact roots",
    )

    reply = gateway.handle(private_msg("/sendfile all"))

    assert "would send" in reply.text.lower()
    assert str(latest_allowed_id) in reply.text
    assert latest_allowed_path in reply.text
    assert older_allowed_path not in reply.text
    assert latest_blocked_path not in reply.text


def test_private_sendfile_without_active_alias_requires_use_guidance(tmp_path: Path) -> None:
    gateway, _config, _store, _controller = _gateway_for(tmp_path)

    reply = gateway.handle(private_msg("/sendfile 1"))

    assert reply.text == "No active thread. Use /use <alias> first."


def test_private_sendfile_rejects_artifact_from_different_alias(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(cwd="/tmp/codex-target/code")
    controller.status_by_session["019-paper"] = _ready_status(cwd="/tmp/codex-target/paper")
    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/add paper 019-paper"))
    gateway.handle(private_msg("/use code"))
    paper_artifact_id = store.record_artifact(
        run_id="run-paper",
        alias="paper",
        session_id="019-paper",
        local_path=str((tmp_path / "exports" / "paper.md").resolve()),
        mime_type="application/octet-stream",
        size_bytes=5,
        status="allowed",
        reason="within allowed artifact roots",
    )

    reply = gateway.handle(private_msg("/sendfile %s" % paper_artifact_id))

    assert "would send" not in reply.text.lower()
    assert "artifact not found" in reply.text.lower()


def test_private_sendfile_all_rejects_when_latest_run_has_no_allowed_artifacts(
    tmp_path: Path,
) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(cwd="/tmp/codex-target/code")
    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))

    store.record_artifact(
        run_id="run-old",
        alias="code",
        session_id="019-code",
        local_path=str((tmp_path / "exports" / "older-allowed.md").resolve()),
        mime_type="application/octet-stream",
        size_bytes=4,
        status="allowed",
        reason="within allowed artifact roots",
    )
    store.record_artifact(
        run_id="run-new",
        alias="code",
        session_id="019-code",
        local_path=str((tmp_path / "exports" / "latest-blocked.md").resolve()),
        mime_type="application/octet-stream",
        size_bytes=6,
        status="blocked",
        reason="outside allowed artifact roots",
    )

    reply = gateway.handle(private_msg("/sendfile all"))

    assert "Rejected" in reply.text
    assert "no allowed artifacts" in reply.text.lower()


def test_private_latest_run_without_detected_artifacts_hides_older_artifacts(
    tmp_path: Path,
) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(cwd="/tmp/codex-target/code")
    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))
    old_id = store.record_artifact(
        run_id="run-old",
        alias="code",
        session_id="019-code",
        local_path=str((tmp_path / "exports" / "older-allowed.md").resolve()),
        mime_type="application/octet-stream",
        size_bytes=4,
        status="allowed",
        reason="within allowed artifact roots",
    )

    def _start_or_send(**kwargs):
        controller.starts.append(kwargs)
        return ControllerRunResult(
            run_id="run-new-empty",
            session_id="019-code",
            session_head="head-2",
            status="completed",
            text="Completed without artifact paths.",
            approval_summary=None,
        )

    controller.start_or_send = _start_or_send

    dispatch_reply = gateway.handle(private_msg("continue implementation"))
    artifacts_reply = gateway.handle(private_msg("/artifacts"))
    send_all_reply = gateway.handle(private_msg("/sendfile all"))
    send_old_reply = gateway.handle(private_msg("/sendfile %s" % old_id))

    assert dispatch_reply.text == "Completed without artifact paths."
    assert artifacts_reply.text == "No artifacts."
    assert "no allowed artifacts" in send_all_reply.text.lower()
    assert "artifact not found" in send_old_reply.text.lower()


def test_private_sendfile_rejects_when_active_alias_no_longer_exists(tmp_path: Path) -> None:
    gateway, config, _store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(cwd="/tmp/codex-target/code")
    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))
    with sqlite3.connect(config.sqlite_path) as connection:
        connection.execute(
            "DELETE FROM thread_aliases WHERE alias = ?",
            ("code",),
        )

    send_all_reply = gateway.handle(private_msg("/sendfile all"))
    send_one_reply = gateway.handle(private_msg("/sendfile 1"))

    assert "no longer exists" in send_all_reply.text.lower()
    assert "no longer exists" in send_one_reply.text.lower()


def test_private_sendfile_rejects_blocked_artifact(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(cwd="/tmp/codex-target/code")
    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))
    artifact_id = store.record_artifact(
        run_id="run-1",
        alias="code",
        session_id="019-code",
        local_path="/tmp/blocked.txt",
        mime_type="application/octet-stream",
        size_bytes=3,
        status="blocked",
        reason="outside allowed artifact roots",
    )

    list_reply = gateway.handle(private_msg("/artifacts code"))
    send_reply = gateway.handle(private_msg("/sendfile %s" % artifact_id))

    assert "blocked" in list_reply.text
    assert "Rejected" in send_reply.text
    assert "outside allowed artifact roots" in send_reply.text


def test_private_artifacts_and_sendfile_ignore_old_session_after_alias_rebind(
    tmp_path: Path,
) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["session-old"] = _ready_status(cwd="/tmp/codex-target/old")
    controller.status_by_session["session-new"] = _ready_status(cwd="/tmp/codex-target/new")

    gateway.handle(private_msg("/add code session-old"))
    gateway.handle(private_msg("/use code"))
    old_path = str((tmp_path / "exports" / "old.md").resolve())
    old_id = store.record_artifact(
        run_id="run-old",
        alias="code",
        session_id="session-old",
        local_path=old_path,
        mime_type="application/octet-stream",
        size_bytes=4,
        status="allowed",
        reason="within allowed artifact roots",
    )
    store.record_artifact_run(
        alias="code",
        run_id="run-old",
        session_id="session-old",
    )

    gateway.handle(private_msg("/add code session-new"))
    gateway.handle(private_msg("/use code"))

    artifacts_after_rebind = gateway.handle(private_msg("/artifacts"))
    send_old_after_rebind = gateway.handle(private_msg("/sendfile %s" % old_id))

    assert artifacts_after_rebind.text == "No artifacts."
    assert "artifact not found" in send_old_after_rebind.text.lower()

    new_path = str((tmp_path / "exports" / "new.md").resolve())
    store.record_artifact(
        run_id="run-new",
        alias="code",
        session_id="session-new",
        local_path=new_path,
        mime_type="application/octet-stream",
        size_bytes=5,
        status="allowed",
        reason="within allowed artifact roots",
    )
    store.record_artifact_run(
        alias="code",
        run_id="run-new",
        session_id="session-new",
    )

    artifacts_after_new = gateway.handle(private_msg("/artifacts"))

    assert new_path in artifacts_after_new.text
    assert old_path not in artifacts_after_new.text


def test_private_remove_alias_forgets_old_artifacts_after_readd_same_session(
    tmp_path: Path,
) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)
    controller.status_by_session["019-code"] = _ready_status(
        cwd="/tmp/codex-target/code"
    )
    store.record_artifact(
        run_id="run-old",
        alias="code",
        session_id="019-code",
        local_path="/tmp/codex-target/code/old.txt",
        mime_type="text/plain",
        size_bytes=10,
        status="allowed",
        reason="ok",
    )

    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))
    gateway.handle(private_msg("/remove code"))
    gateway.handle(private_msg("/add code 019-code"))
    gateway.handle(private_msg("/use code"))
    reply = gateway.handle(private_msg("/artifacts"))

    assert reply.text == "No artifacts."
