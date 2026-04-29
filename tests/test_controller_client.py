from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any, Optional

import pytest

from codex_thread_bridge.controller_client import (
    McpControllerClient,
    McpControllerClientError,
    _StdioJsonRpcTransport,
    build_start_payload,
)
from codex_thread_bridge.models import ExecutionPolicy


EXPECTED_INITIALIZE_PARAMS = {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {
        "name": "codex-thread-bridge",
        "version": "0.2.0",
    },
}


def test_build_start_payload_uses_alias_policy_and_controller_defaults() -> None:
    payload = build_start_payload(
        session_id="019-code",
        cwd="/tmp/project",
        message="hello",
        owner="ctb-private:owner-1",
        policy=ExecutionPolicy(
            sandbox="workspace-write",
            approval_policy="on-request",
            writable_roots=("/tmp/project",),
            model="gpt-5",
            effort="medium",
        ),
        idempotency_key="m-1:code",
        expected_session_head="head-1",
        lease_seconds=900,
    )

    assert payload["session_id"] == "019-code"
    assert payload["cwd"] == "/tmp/project"
    assert payload["message"] == "hello"
    assert payload["owner"] == "ctb-private:owner-1"
    assert payload["intent"] == "plan_review"
    assert payload["transport"] == "app_server"
    assert payload["plan_capability"] == "protocol"
    assert payload["sandbox"] == "workspace-write"
    assert payload["approval_policy"] == "on-request"
    assert payload["writable_roots"] == ["/tmp/project"]
    assert payload["model"] == "gpt-5"
    assert payload["effort"] == "medium"
    assert payload["idempotency_key"] == "m-1:code"
    assert payload["expected_session_head"] == "head-1"
    assert payload["attachments"] == []
    assert payload["authorization_grant"] is None
    assert payload["acceptance_criteria"] == []
    assert payload["parent_run_id"] is None
    assert payload["lease_seconds"] == 900


def test_mcp_controller_client_status_normalizes_controller_session() -> None:
    transport = FakeTransport(
        [
            {"capabilities": {"tools": {}}},
            tool_result(
                {
                    "runs": [],
                    "session": {
                        "session_id": "019-code",
                        "owner": "ctb-private:owner-1",
                        "transport": "app_server",
                        "status": "active",
                        "dirty": True,
                        "reconcile_required": False,
                        "session_head": "head-1",
                    },
                }
            ),
        ]
    )
    client = McpControllerClient(["fake-mcp"], transport_factory=lambda: transport)

    status = client.status("019-code")

    assert transport.requests == [
        ("initialize", EXPECTED_INITIALIZE_PARAMS),
        ("notifications/initialized", {}),
        (
            "tools/call",
            {
                "name": "cross_thread_status",
                "arguments": {"run_ids": [], "session_id": "019-code"},
            },
        ),
    ]
    assert transport.closed
    assert status["session_id"] == "019-code"
    assert status["locked"] is True
    assert status["dirty"] is True
    assert status["reconcile_required"] is False
    assert status["session_head"] == "head-1"
    assert status["owner"] == "ctb-private:owner-1"
    assert status["transport"] == "app_server"
    assert status["status"] == "active"
    assert "cwd" not in status
    assert status["controller_status"]["session"]["session_id"] == "019-code"


def test_mcp_controller_client_status_preserves_workspace_metadata() -> None:
    transport = FakeTransport(
        [
            {"capabilities": {"tools": {}}},
            tool_result(
                {
                    "runs": [],
                    "session": {
                        "session_id": "019-code",
                        "status": "released",
                        "session_head": "head-1",
                        "cwd": "/tmp/thread-cwd",
                    },
                    "workspace_root": "/tmp/workspace-root",
                }
            ),
        ]
    )
    client = McpControllerClient(["fake-mcp"], transport_factory=lambda: transport)

    status = client.status("019-code")

    assert status["cwd"] == "/tmp/thread-cwd"
    assert status["workspace_root"] == "/tmp/workspace-root"


def test_start_or_send_runs_controller_lifecycle_and_returns_text() -> None:
    transport = FakeTransport(
        [
            {"capabilities": {"tools": {}}},
            tool_result(
                {
                    "run_id": "run-1",
                    "session_id": "019-code",
                    "session_head": "head-start",
                    "lock_token": "lock-1",
                    "last_event_seq": 7,
                    "run": {"run_id": "run-1", "session_id": "019-code"},
                    "session": {"session_id": "019-code", "status": "active"},
                }
            ),
            tool_result(
                {
                    "status": "ready",
                    "runs": [
                        {
                            "run_id": "run-1",
                            "session_id": "019-code",
                            "status": "completed",
                            "last_event_seq": 8,
                        }
                    ],
                }
            ),
            tool_result(
                {
                    "run": {
                        "run_id": "run-1",
                        "session_id": "019-code",
                        "status": "completed",
                        "actual_session_head": "head-2",
                    },
                    "events": [],
                    "result_text": "done text",
                }
            ),
            tool_result({"run": {"run_id": "run-1", "status": "completed"}}),
            tool_result({"run": {"run_id": "run-1", "closed_at": 1.0}}),
            tool_result({"session": {"session_id": "019-code", "status": "released"}}),
        ]
    )
    client = McpControllerClient(
        ["fake-mcp"],
        timeout_ms=1234,
        lease_seconds=456,
        transport_factory=lambda: transport,
    )

    result = client.start_or_send(
        session_id="019-code",
        cwd="/tmp/project",
        message="hello",
        owner="ctb-private:owner-1",
        policy=ExecutionPolicy.work_default("/tmp/project"),
        idempotency_key="m-1:code",
        expected_session_head="head-1",
    )

    tool_calls = [
        params["name"]
        for method, params in transport.requests
        if method == "tools/call"
    ]
    assert transport.requests[0] == ("initialize", EXPECTED_INITIALIZE_PARAMS)
    assert transport.requests[1] == ("notifications/initialized", {})
    assert transport.requests[2][0] == "tools/call"
    assert transport.requests[2][1]["name"] == "cross_thread_start"
    assert tool_calls == [
        "cross_thread_start",
        "cross_thread_wait_any",
        "cross_thread_read_result",
        "cross_thread_delivery_ack",
        "cross_thread_close",
        "cross_thread_release",
    ]
    start_args = transport.requests[2][1]["arguments"]
    assert start_args["lease_seconds"] == 456
    assert start_args["attachments"] == []
    assert transport.requests[3][1]["arguments"] == {
        "run_ids": ["run-1"],
        "after_seq": 7,
        "timeout_ms": 1234,
        "include_progress": False,
    }
    assert transport.requests[6][1]["arguments"] == {"run_id": "run-1"}
    assert transport.requests[7][1]["arguments"] == {
        "session_id": "019-code",
        "lock_token": "lock-1",
    }
    assert transport.closed
    assert result.run_id == "run-1"
    assert result.session_id == "019-code"
    assert result.session_head == "head-2"
    assert result.status == "completed"
    assert result.text == "done text"
    assert result.approval_summary is None


def test_start_or_send_releases_after_close_failure_when_ack_succeeded() -> None:
    transport = FakeTransport(
        [
            {"capabilities": {"tools": {}}},
            tool_result(
                {
                    "run_id": "run-1",
                    "session_id": "019-code",
                    "session_head": "head-start",
                    "lock_token": "lock-1",
                    "last_event_seq": 7,
                    "run": {"run_id": "run-1", "session_id": "019-code"},
                }
            ),
            tool_result({"status": "ready", "runs": [{"run_id": "run-1"}]}),
            tool_result(
                {
                    "run": {
                        "run_id": "run-1",
                        "session_id": "019-code",
                        "status": "completed",
                        "actual_session_head": "head-2",
                    },
                    "events": [],
                    "result_text": "done text",
                }
            ),
            tool_result({"run": {"run_id": "run-1", "status": "completed"}}),
            McpControllerClientError("close failed"),
            McpControllerClientError("release failed"),
        ]
    )
    client = McpControllerClient(["fake-mcp"], transport_factory=lambda: transport)

    with pytest.raises(McpControllerClientError, match="close failed"):
        client.start_or_send(
            session_id="019-code",
            cwd="/tmp/project",
            message="hello",
            owner="ctb-private:owner-1",
            policy=ExecutionPolicy.work_default("/tmp/project"),
            idempotency_key="m-1:code",
            expected_session_head="head-1",
        )

    tool_calls = [
        params["name"]
        for method, params in transport.requests
        if method == "tools/call"
    ]
    assert tool_calls == [
        "cross_thread_start",
        "cross_thread_wait_any",
        "cross_thread_read_result",
        "cross_thread_delivery_ack",
        "cross_thread_close",
        "cross_thread_release",
    ]


def test_start_or_send_cleans_up_lock_after_wait_failure() -> None:
    transport = FakeTransport(
        [
            {"capabilities": {"tools": {}}},
            tool_result(
                {
                    "run_id": "run-1",
                    "session_id": "019-code",
                    "session_head": "head-start",
                    "lock_token": "lock-1",
                    "last_event_seq": 7,
                    "run": {"run_id": "run-1", "session_id": "019-code"},
                }
            ),
            McpControllerClientError("wait failed"),
            tool_result({"run": {"run_id": "run-1", "status": "cancelled"}}),
            tool_result({"run": {"run_id": "run-1", "status": "cancelled"}}),
            tool_result({"run": {"run_id": "run-1", "closed_at": 1.0}}),
            tool_result({"session": {"session_id": "019-code", "status": "released"}}),
        ]
    )
    client = McpControllerClient(["fake-mcp"], transport_factory=lambda: transport)

    with pytest.raises(McpControllerClientError, match="wait failed"):
        client.start_or_send(
            session_id="019-code",
            cwd="/tmp/project",
            message="hello",
            owner="ctb-private:owner-1",
            policy=ExecutionPolicy.work_default("/tmp/project"),
            idempotency_key="m-1:code",
            expected_session_head="head-1",
        )

    tool_calls = [
        params["name"]
        for method, params in transport.requests
        if method == "tools/call"
    ]
    assert tool_calls == [
        "cross_thread_start",
        "cross_thread_wait_any",
        "cross_thread_cancel",
        "cross_thread_delivery_ack",
        "cross_thread_close",
        "cross_thread_release",
    ]


def test_start_or_send_cleans_up_after_malformed_start_missing_lock_token() -> None:
    transport = FakeTransport(
        [
            {"capabilities": {"tools": {}}},
            tool_result(
                {
                    "run_id": "run-1",
                    "session_id": "019-code",
                    "session_head": "head-start",
                    "last_event_seq": 7,
                    "run": {"run_id": "run-1", "session_id": "019-code"},
                }
            ),
            tool_result({"run": {"run_id": "run-1", "status": "cancelled"}}),
            tool_result({"run": {"run_id": "run-1", "status": "cancelled"}}),
            tool_result({"run": {"run_id": "run-1", "closed_at": 1.0}}),
        ]
    )
    client = McpControllerClient(["fake-mcp"], transport_factory=lambda: transport)

    with pytest.raises(McpControllerClientError, match="lock_token"):
        client.start_or_send(
            session_id="019-code",
            cwd="/tmp/project",
            message="hello",
            owner="ctb-private:owner-1",
            policy=ExecutionPolicy.work_default("/tmp/project"),
            idempotency_key="m-1:code",
            expected_session_head="head-1",
        )

    tool_calls = [
        params["name"]
        for method, params in transport.requests
        if method == "tools/call"
    ]
    assert tool_calls == [
        "cross_thread_start",
        "cross_thread_cancel",
        "cross_thread_delivery_ack",
        "cross_thread_close",
    ]


def test_start_or_send_reads_lock_token_from_session_when_top_level_missing() -> None:
    transport = FakeTransport(
        [
            {"capabilities": {"tools": {}}},
            tool_result(
                {
                    "run_id": "run-1",
                    "session_id": "019-code",
                    "session_head": "head-start",
                    "last_event_seq": 7,
                    "run": {"run_id": "run-1", "session_id": "019-code"},
                    "session": {
                        "session_id": "019-code",
                        "lock_token": "lock-from-session",
                        "status": "active",
                    },
                }
            ),
            tool_result(
                {
                    "status": "ready",
                    "runs": [{"run_id": "run-1", "status": "completed"}],
                }
            ),
            tool_result(
                {
                    "run": {
                        "run_id": "run-1",
                        "session_id": "019-code",
                        "status": "completed",
                        "actual_session_head": "head-2",
                    },
                    "result_text": "done",
                }
            ),
            tool_result({"run": {"run_id": "run-1", "status": "completed"}}),
            tool_result({"run": {"run_id": "run-1", "closed_at": 1.0}}),
            tool_result({"session": {"session_id": "019-code", "status": "released"}}),
        ]
    )
    client = McpControllerClient(["fake-mcp"], transport_factory=lambda: transport)

    result = client.start_or_send(
        session_id="019-code",
        cwd="/tmp/project",
        message="hello",
        owner="ctb-private:owner-1",
        policy=ExecutionPolicy.work_default("/tmp/project"),
        idempotency_key="m-1:code",
        expected_session_head="head-1",
    )

    assert result.text == "done"
    assert transport.requests[-1][1]["arguments"] == {
        "session_id": "019-code",
        "lock_token": "lock-from-session",
    }


def test_start_or_send_cleans_up_after_malformed_start_missing_new_session_id() -> None:
    transport = FakeTransport(
        [
            {"capabilities": {"tools": {}}},
            tool_result(
                {
                    "run_id": "run-1",
                    "session_head": "head-start",
                    "lock_token": "lock-1",
                    "last_event_seq": 7,
                    "run": {"run_id": "run-1"},
                }
            ),
            tool_result({"run": {"run_id": "run-1", "status": "cancelled"}}),
            tool_result({"run": {"run_id": "run-1", "status": "cancelled"}}),
            tool_result({"run": {"run_id": "run-1", "closed_at": 1.0}}),
        ]
    )
    client = McpControllerClient(["fake-mcp"], transport_factory=lambda: transport)

    with pytest.raises(McpControllerClientError, match="session_id"):
        client.start_or_send(
            session_id=None,
            cwd="/tmp/project",
            message="hello",
            owner="ctb-private:owner-1",
            policy=ExecutionPolicy.work_default("/tmp/project"),
            idempotency_key="m-1:code",
            expected_session_head=None,
        )

    tool_calls = [
        params["name"]
        for method, params in transport.requests
        if method == "tools/call"
    ]
    assert tool_calls == [
        "cross_thread_start",
        "cross_thread_cancel",
        "cross_thread_delivery_ack",
        "cross_thread_close",
    ]


def test_start_or_send_cleans_up_after_delivery_ack_failure() -> None:
    transport = FakeTransport(
        [
            {"capabilities": {"tools": {}}},
            tool_result(
                {
                    "run_id": "run-1",
                    "session_id": "019-code",
                    "session_head": "head-start",
                    "lock_token": "lock-1",
                    "last_event_seq": 7,
                    "run": {"run_id": "run-1", "session_id": "019-code"},
                }
            ),
            tool_result({"status": "ready", "runs": [{"run_id": "run-1"}]}),
            tool_result(
                {
                    "run": {
                        "run_id": "run-1",
                        "session_id": "019-code",
                        "status": "completed",
                        "actual_session_head": "head-2",
                    },
                    "events": [],
                    "result_text": "done text",
                }
            ),
            McpControllerClientError("ack failed"),
            tool_result({"run": {"run_id": "run-1", "status": "cancelled"}}),
            tool_result({"run": {"run_id": "run-1", "status": "cancelled"}}),
            tool_result({"run": {"run_id": "run-1", "closed_at": 1.0}}),
            tool_result({"session": {"session_id": "019-code", "status": "released"}}),
        ]
    )
    client = McpControllerClient(["fake-mcp"], transport_factory=lambda: transport)

    with pytest.raises(McpControllerClientError, match="ack failed"):
        client.start_or_send(
            session_id="019-code",
            cwd="/tmp/project",
            message="hello",
            owner="ctb-private:owner-1",
            policy=ExecutionPolicy.work_default("/tmp/project"),
            idempotency_key="m-1:code",
            expected_session_head="head-1",
        )

    tool_calls = [
        params["name"]
        for method, params in transport.requests
        if method == "tools/call"
    ]
    assert tool_calls == [
        "cross_thread_start",
        "cross_thread_wait_any",
        "cross_thread_read_result",
        "cross_thread_delivery_ack",
        "cross_thread_cancel",
        "cross_thread_delivery_ack",
        "cross_thread_close",
        "cross_thread_release",
    ]
    assert transport.requests[-1][1]["arguments"] == {
        "session_id": "019-code",
        "lock_token": "lock-1",
    }


def test_start_or_send_returns_approval_summary_for_blocked_result() -> None:
    transport = FakeTransport(
        [
            {"capabilities": {"tools": {}}},
            tool_result(
                {
                    "run_id": "run-1",
                    "session_id": "019-code",
                    "session_head": "head-start",
                    "lock_token": "lock-1",
                    "last_event_seq": 7,
                    "run": {"run_id": "run-1", "session_id": "019-code"},
                }
            ),
            tool_result(
                {
                    "status": "ready",
                    "runs": [{"run_id": "run-1", "status": "blocked"}],
                }
            ),
            tool_result(
                {
                    "run": {
                        "run_id": "run-1",
                        "session_id": "019-code",
                        "status": "blocked",
                        "block_reason": "approval_required",
                        "actual_session_head": "head-2",
                    },
                    "events": [],
                    "result_text": "",
                }
            ),
            tool_result({"run": {"run_id": "run-1", "status": "blocked"}}),
            tool_result({"run": {"run_id": "run-1", "closed_at": 1.0}}),
            tool_result({"session": {"session_id": "019-code", "status": "released"}}),
        ]
    )
    client = McpControllerClient(["fake-mcp"], transport_factory=lambda: transport)

    result = client.start_or_send(
        session_id="019-code",
        cwd="/tmp/project",
        message="hello",
        owner="ctb-private:owner-1",
        policy=ExecutionPolicy.work_default("/tmp/project"),
        idempotency_key="m-1:code",
        expected_session_head="head-1",
    )

    tool_calls = [
        params["name"]
        for method, params in transport.requests
        if method == "tools/call"
    ]
    assert tool_calls[-3:] == [
        "cross_thread_delivery_ack",
        "cross_thread_close",
        "cross_thread_release",
    ]
    assert result.status == "blocked"
    assert result.approval_summary == "Controller run blocked: approval_required"


def test_start_or_send_attempts_cleanup_when_wait_times_out() -> None:
    transport = FakeTransport(
        [
            {"capabilities": {"tools": {}}},
            tool_result(
                {
                    "run_id": "run-1",
                    "session_id": "019-code",
                    "session_head": "head-start",
                    "lock_token": "lock-1",
                    "last_event_seq": 7,
                    "run": {"run_id": "run-1", "session_id": "019-code"},
                }
            ),
            tool_result(
                {
                    "status": "timeout",
                    "runs": [
                        {
                            "run_id": "run-1",
                            "session_id": "019-code",
                            "status": "in_progress",
                        }
                    ],
                }
            ),
        ]
    )
    client = McpControllerClient(["fake-mcp"], transport_factory=lambda: transport)

    with pytest.raises(McpControllerClientError, match="timed out"):
        client.start_or_send(
            session_id="019-code",
            cwd="/tmp/project",
            message="hello",
            owner="ctb-private:owner-1",
            policy=ExecutionPolicy.work_default("/tmp/project"),
            idempotency_key="m-1:code",
            expected_session_head="head-1",
        )

    tool_calls = [
        params["name"]
        for method, params in transport.requests
        if method == "tools/call"
    ]
    assert tool_calls == [
        "cross_thread_start",
        "cross_thread_wait_any",
        "cross_thread_cancel",
        "cross_thread_delivery_ack",
        "cross_thread_close",
        "cross_thread_release",
    ]
    assert transport.closed


def test_json_rpc_error_is_decoded_as_client_exception() -> None:
    process = FakeProcess(
        [
            json_line(1, result={"capabilities": {"tools": {}}}),
            json_line(2, error={"code": -32000, "message": "unknown tool"}),
        ]
    )
    client = McpControllerClient(
        ["fake-mcp"],
        transport_factory=lambda: _StdioJsonRpcTransport(["fake-mcp"], process=process),
    )

    with pytest.raises(McpControllerClientError, match="unknown tool"):
        client.status("019-code")


def test_stdio_notify_writes_notification_without_waiting_for_response() -> None:
    process = FakeProcess([])
    transport = _StdioJsonRpcTransport(["fake-mcp"], process=process)

    transport.notify("notifications/initialized", {})

    written = json.loads(process.stdin.writes[0])
    assert written == {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    assert process.stdout.read_count == 0


def test_stdio_request_skips_interleaved_notification_frame() -> None:
    process = FakeProcess(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {"message": "working"},
                }
            )
            + "\n",
            json_line(1, result={"ok": True}),
        ]
    )
    transport = _StdioJsonRpcTransport(
        ["fake-mcp"],
        process=process,
        select_fn=always_ready,
    )

    result = transport.request("initialize", {})

    assert result == {"ok": True}
    assert process.stdout.read_count == 2


def test_stdio_request_times_out_without_response_from_live_process() -> None:
    process = FakeProcess([])
    transport = _StdioJsonRpcTransport(
        ["fake-mcp"],
        process=process,
        request_timeout_seconds=0,
        select_fn=never_ready,
    )

    with pytest.raises(McpControllerClientError, match="timed out"):
        transport.request("initialize", {})


def test_stdio_request_times_out_on_partial_frame_without_newline() -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys, time; "
                "sys.stdout.write('{\"jsonrpc\":\"2.0\",\"id\":1,\"result\"'); "
                "sys.stdout.flush(); "
                "time.sleep(1)"
            ),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    transport = _StdioJsonRpcTransport(
        ["fake-mcp"],
        process=process,
        request_timeout_seconds=0.05,
    )
    started_at = time.monotonic()
    try:
        with pytest.raises(McpControllerClientError, match="timed out"):
            transport.request("initialize", {})
    finally:
        process.terminate()
        process.wait(timeout=1)

    assert time.monotonic() - started_at < 0.5


def test_stdio_request_reports_child_exit_before_response() -> None:
    process = FakeProcess([], poll_result=1)
    transport = _StdioJsonRpcTransport(
        ["fake-mcp"],
        process=process,
        select_fn=never_ready,
    )

    with pytest.raises(McpControllerClientError, match="process exited"):
        transport.request("initialize", {})


def test_stdio_transport_discards_child_stderr(monkeypatch) -> None:
    popen_kwargs = {}

    class DummyPopen:
        stdin = FakeStream()
        stdout = FakeStream()

        def __init__(self, command, **kwargs) -> None:
            popen_kwargs.update(kwargs)

        def poll(self) -> Optional[int]:
            return 0

    monkeypatch.setattr(
        "codex_thread_bridge.controller_client.subprocess.Popen",
        DummyPopen,
    )

    _StdioJsonRpcTransport(["fake-mcp"])

    assert popen_kwargs["stderr"] == subprocess.DEVNULL


def test_malformed_mcp_tool_content_raises_client_exception() -> None:
    transport = FakeTransport(
        [
            {"capabilities": {"tools": {}}},
            {"content": [{"type": "text", "text": "not-json"}]},
        ]
    )
    client = McpControllerClient(["fake-mcp"], transport_factory=lambda: transport)

    with pytest.raises(McpControllerClientError, match="malformed MCP content"):
        client.status("019-code")


def tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def json_line(
    request_id: int,
    result: Any = None,
    error: Any = None,
) -> str:
    payload = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return json.dumps(payload) + "\n"


def always_ready(reads, writes, errors, timeout):
    return reads, writes, errors


def never_ready(reads, writes, errors, timeout):
    return [], [], []


class FakeTransport:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((method, params))
        if not self.responses:
            raise AssertionError("unexpected request: %s" % method)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.requests.append((method, params))

    def close(self) -> None:
        self.closed = True


class FakeStream:
    def __init__(self, lines: Optional[list[str]] = None) -> None:
        self.lines = list(lines or [])
        self.writes: list[str] = []
        self.read_count = 0

    def write(self, text: str) -> int:
        self.writes.append(text)
        return len(text)

    def flush(self) -> None:
        return None

    def readline(self) -> str:
        self.read_count += 1
        if not self.lines:
            return ""
        return self.lines.pop(0)

    def read_available(self) -> bytes:
        self.read_count += 1
        if not self.lines:
            return b""
        return self.lines.pop(0).encode("utf-8")


class FakeProcess:
    def __init__(
        self,
        stdout_lines: list[str],
        poll_result: Optional[int] = None,
    ) -> None:
        self.stdin = FakeStream()
        self.stdout = FakeStream(stdout_lines)
        self.stderr = FakeStream()
        self.poll_result = poll_result
        self.terminated = False
        self.waited = False

    def poll(self) -> Optional[int]:
        return self.poll_result

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: Optional[float] = None) -> int:
        self.waited = True
        return 0
