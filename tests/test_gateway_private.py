from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, get_type_hints

from codex_thread_bridge.controller_client import ControllerRunResult
from codex_thread_bridge.models import ExecutionPolicy


@dataclass
class FakeControllerClient:
    starts: list[dict[str, object]] = field(default_factory=list)
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
    ) -> ControllerRunResult:
        kwargs = {
            "session_id": session_id,
            "cwd": cwd,
            "message": message,
            "owner": owner,
            "policy": policy,
            "idempotency_key": idempotency_key,
            "expected_session_head": expected_session_head,
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
