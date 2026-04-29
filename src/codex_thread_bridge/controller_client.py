from __future__ import annotations

import json
import os
import select
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, Sequence

from codex_thread_bridge.models import ExecutionPolicy


DEFAULT_LEASE_SECONDS = 1800
DEFAULT_TIMEOUT_MS = 30000
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "blocked"})
INITIALIZE_PARAMS = {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {
        "name": "codex-thread-bridge",
        "version": "0.2.0",
    },
}


@dataclass(frozen=True)
class ControllerRunResult:
    run_id: str
    session_id: str
    session_head: str
    status: str
    text: str
    approval_summary: Optional[str]


class McpControllerClientError(RuntimeError):
    """Raised when the cross-thread-controller MCP boundary fails."""


class ControllerClient(Protocol):
    def status(self, session_id: str) -> dict:
        ...

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
        ...

    def recover_session(
        self,
        *,
        session_id: str,
        owner: str,
        human_authorized: bool,
    ) -> dict:
        ...


class _JsonRpcTransport(Protocol):
    def request(self, method: str, params: dict) -> dict:
        ...

    def notify(self, method: str, params: dict) -> None:
        ...

    def close(self) -> None:
        ...


def build_start_payload(
    *,
    session_id: Optional[str],
    cwd: str,
    message: str,
    owner: str,
    policy: ExecutionPolicy,
    idempotency_key: str,
    expected_session_head: Optional[str],
    intent: str = "direct_message",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict:
    return {
        "session_id": session_id,
        "cwd": cwd,
        "message": message,
        "owner": owner,
        "intent": intent,
        "transport": "app_server",
        "plan_capability": "protocol",
        "sandbox": policy.sandbox,
        "approval_policy": policy.approval_policy,
        "writable_roots": list(policy.writable_roots),
        "model": policy.model,
        "effort": policy.effort,
        "idempotency_key": idempotency_key,
        "expected_session_head": expected_session_head,
        "attachments": [],
        "authorization_grant": None,
        "acceptance_criteria": [],
        "parent_run_id": None,
        "lease_seconds": lease_seconds,
    }


class _StdioJsonRpcTransport:
    def __init__(
        self,
        command: Sequence[str],
        process: Optional[Any] = None,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        select_fn: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.command = tuple(command)
        self._next_id = 0
        self._stdout_buffer = b""
        self.request_timeout_seconds = float(request_timeout_seconds)
        self._select = select_fn or select.select
        if process is None:
            if not self.command:
                raise ValueError("MCP command must not be empty")
            self.process = subprocess.Popen(
                list(self.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        else:
            self.process = process

    def request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        request_id = self._next_id
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except Exception as exc:
            raise McpControllerClientError(
                "failed to write MCP request %s: %s" % (method, exc)
            )

        return self._read_response(method, request_id)

    def _read_response(self, method: str, request_id: int) -> dict:
        deadline = time.monotonic() + self.request_timeout_seconds
        while True:
            line = self._next_stdout_line()
            if line is not None:
                if not line.strip():
                    continue
                response = self._parse_response_line(method, line)
                if "id" not in response:
                    continue
                if response.get("id") != request_id:
                    continue
                error = response.get("error")
                if error is not None:
                    if isinstance(error, dict):
                        message = error.get("message") or error.get("code") or error
                    else:
                        message = error
                    raise McpControllerClientError(
                        "MCP %s failed: %s" % (method, message)
                    )
                if "result" not in response:
                    raise McpControllerClientError(
                        "JSON-RPC response for %s did not include result" % method
                    )
                result = response["result"]
                if not isinstance(result, dict):
                    raise McpControllerClientError(
                        "JSON-RPC result for %s was not an object" % method
                    )
                return result

            remaining = deadline - time.monotonic()
            if remaining < 0:
                remaining = 0
            if not self._stdout_ready(remaining):
                if self.process.poll() is not None:
                    raise McpControllerClientError(
                        "MCP process exited before %s response" % method
                    )
                if time.monotonic() >= deadline:
                    raise McpControllerClientError(
                        "MCP %s response timed out" % method
                    )
                continue

            chunk = self._read_stdout_chunk()
            if not chunk:
                if self.process.poll() is not None:
                    raise McpControllerClientError(
                        "MCP process exited before %s response" % method
                    )
                if time.monotonic() >= deadline:
                    raise McpControllerClientError(
                        "MCP %s response timed out" % method
                    )
                continue
            self._stdout_buffer += chunk

    def _stdout_ready(self, timeout: float) -> bool:
        try:
            readable, _writable, _errors = self._select(
                [self.process.stdout],
                [],
                [],
                timeout,
            )
            return bool(readable)
        except (AttributeError, TypeError, ValueError):
            return True

    def _read_stdout_chunk(self) -> bytes:
        # Keep response reads on the raw fd path; text readline() can block on
        # partial frames even after select() reports readable bytes.
        try:
            return os.read(self.process.stdout.fileno(), 4096)
        except AttributeError:
            read_available = getattr(self.process.stdout, "read_available", None)
            if read_available is not None:
                return read_available()
            raise

    def _next_stdout_line(self) -> Optional[str]:
        newline_index = self._stdout_buffer.find(b"\n")
        if newline_index < 0:
            return None
        line = self._stdout_buffer[:newline_index]
        self._stdout_buffer = self._stdout_buffer[newline_index + 1 :]
        return line.decode("utf-8")

    def _parse_response_line(self, method: str, line: str) -> dict:
        try:
            response = json.loads(line)
        except ValueError as exc:
            raise McpControllerClientError(
                "malformed JSON-RPC response for %s: %s" % (method, exc)
            )
        if not isinstance(response, dict):
            raise McpControllerClientError(
                "malformed JSON-RPC response for %s" % method
            )
        return response

    def notify(self, method: str, params: dict) -> None:
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        try:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except Exception as exc:
            raise McpControllerClientError(
                "failed to write MCP notification %s: %s" % (method, exc)
            )

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=1)
        except Exception:
            kill = getattr(self.process, "kill", None)
            if kill is not None:
                try:
                    kill()
                except Exception:
                    pass


class McpControllerClient:
    def __init__(
        self,
        command: Sequence[str],
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        transport_factory: Optional[Callable[[], _JsonRpcTransport]] = None,
    ) -> None:
        self.command = tuple(command)
        self.timeout_ms = int(timeout_ms)
        self.lease_seconds = int(lease_seconds)
        self.request_timeout_seconds = float(request_timeout_seconds)
        self._transport_factory = transport_factory

    def status(self, session_id: str) -> dict:
        transport = self._open_transport()
        try:
            self._initialize(transport)
            controller_status = self._call_tool(
                transport,
                "cross_thread_status",
                {"run_ids": [], "session_id": session_id},
            )
            return self._normalize_status(session_id, controller_status)
        finally:
            transport.close()

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
        transport = self._open_transport()
        run_id = None
        lock_token = None
        result_session_id = session_id
        delivered = False
        try:
            self._initialize(transport)
            payload = build_start_payload(
                session_id=session_id,
                cwd=cwd,
                message=message,
                owner=owner,
                policy=policy,
                idempotency_key=idempotency_key,
                expected_session_head=expected_session_head,
                intent=intent,
                lease_seconds=self.lease_seconds,
            )
            started = self._call_tool(transport, "cross_thread_start", payload)
            run_id = self._run_id(started)

            try:
                lock_token = self._lock_token_or_none(started)
                if not lock_token:
                    replay = self._terminal_replay_result(
                        transport,
                        started,
                        run_id,
                        result_session_id,
                    )
                    if replay is not None:
                        return replay
                    raise McpControllerClientError(
                        "controller start result missing lock_token"
                    )
                result_session_id = self._session_id(started, session_id)
                after_seq = self._last_event_seq(started)

                waited = self._call_tool(
                    transport,
                    "cross_thread_wait_any",
                    {
                        "run_ids": [run_id],
                        "after_seq": after_seq,
                        "timeout_ms": self.timeout_ms,
                        "include_progress": False,
                    },
                )
                if waited.get("status") != "ready":
                    wait_status = self._first_str(waited.get("status"), "unknown")
                    if wait_status == "timeout":
                        raise McpControllerClientError(
                            "controller run %s timed out" % run_id
                        )
                    raise McpControllerClientError(
                        "controller run %s not ready: %s" % (run_id, wait_status)
                    )
                result = self._call_tool(
                    transport,
                    "cross_thread_read_result",
                    {"run_id": run_id},
                )
                run_result = self._run_result_from_read_result(
                    started,
                    result,
                    result_session_id,
                    run_id,
                )
                result_session_id = run_result.session_id

                self._call_tool(
                    transport,
                    "cross_thread_delivery_ack",
                    {"run_id": run_id},
                )
                delivered = True
                if not result_session_id or not lock_token:
                    raise McpControllerClientError(
                        "controller start result did not include session_id and lock_token"
                    )
                self._close_and_release(
                    transport,
                    run_id,
                    result_session_id,
                    lock_token,
                )
            except Exception:
                if not delivered:
                    self._cleanup_started_run(
                        transport,
                        run_id,
                        result_session_id,
                        lock_token,
                    )
                raise

            return ControllerRunResult(
                run_id=run_result.run_id,
                session_id=run_result.session_id,
                session_head=run_result.session_head,
                status=run_result.status,
                text=run_result.text,
                approval_summary=run_result.approval_summary,
            )
        finally:
            transport.close()

    def recover_session(
        self,
        *,
        session_id: str,
        owner: str,
        human_authorized: bool,
    ) -> dict:
        transport = self._open_transport()
        lock_token = None
        acked_runs: list[str] = []
        cancelled_runs: list[str] = []
        closed_runs: list[str] = []
        try:
            self._initialize(transport)
            recovered = self._call_tool(
                transport,
                "cross_thread_force_recover",
                {
                    "session_id": session_id,
                    "owner": owner,
                    "transport": "app_server",
                    "lease_seconds": self.lease_seconds,
                    "human_authorized": bool(human_authorized),
                },
            )
            lock_token = self._lock_token(recovered)
            runs = self._call_tool(
                transport,
                "cross_thread_list_runs",
                {
                    "owner": None,
                    "status": None,
                    "session_id": session_id,
                    "limit": 100,
                },
            ).get("runs")
            if not isinstance(runs, list):
                runs = []

            for run in runs:
                if not isinstance(run, dict):
                    continue
                run_id = self._first_str(run.get("run_id"))
                if not run_id:
                    continue
                status = self._first_str(run.get("status"))
                if status == "in_progress":
                    self._call_tool(
                        transport,
                        "cross_thread_cancel",
                        {"run_id": run_id, "reason": "manual recover"},
                    )
                    cancelled_runs.append(run_id)
                if status in TERMINAL_RUN_STATUSES or status == "in_progress":
                    if not run.get("delivery_ack_at") and not run.get("transferred_to"):
                        self._call_tool(
                            transport,
                            "cross_thread_delivery_ack",
                            {"run_id": run_id},
                        )
                        acked_runs.append(run_id)
                    if not run.get("closed_at"):
                        self._call_tool(
                            transport,
                            "cross_thread_close",
                            {"run_id": run_id},
                        )
                        closed_runs.append(run_id)

            self._call_tool(
                transport,
                "cross_thread_release",
                {"session_id": session_id, "lock_token": lock_token},
            )
            return {
                "session_id": session_id,
                "owner": owner,
                "human_authorized": bool(human_authorized),
                "released": True,
                "acked_runs": acked_runs,
                "cancelled_runs": cancelled_runs,
                "closed_runs": closed_runs,
            }
        finally:
            transport.close()

    def _close_and_release(
        self,
        transport: _JsonRpcTransport,
        run_id: str,
        session_id: str,
        lock_token: str,
    ) -> None:
        close_error = None
        try:
            self._call_tool(transport, "cross_thread_close", {"run_id": run_id})
        except Exception as exc:
            close_error = exc
        try:
            self._call_tool(
                transport,
                "cross_thread_release",
                {"session_id": session_id, "lock_token": lock_token},
            )
        except Exception:
            if close_error is not None:
                raise close_error
            raise
        if close_error is not None:
            raise close_error

    def _cleanup_started_run(
        self,
        transport: _JsonRpcTransport,
        run_id: Optional[str],
        session_id: Optional[str],
        lock_token: Optional[str],
    ) -> None:
        if not run_id:
            return
        cleanup_steps = [
            (
                "cross_thread_cancel",
                {"run_id": run_id, "reason": "client cleanup after failure"},
            ),
            ("cross_thread_delivery_ack", {"run_id": run_id}),
            ("cross_thread_close", {"run_id": run_id}),
        ]
        if session_id and lock_token:
            cleanup_steps.append(
                (
                    "cross_thread_release",
                    {"session_id": session_id, "lock_token": lock_token},
                )
            )
        for name, arguments in cleanup_steps:
            try:
                self._call_tool(transport, name, arguments)
            except Exception:
                continue

    def _open_transport(self) -> _JsonRpcTransport:
        if self._transport_factory is not None:
            return self._transport_factory()
        return _StdioJsonRpcTransport(
            self.command,
            request_timeout_seconds=self.request_timeout_seconds,
        )

    def _initialize(self, transport: _JsonRpcTransport) -> None:
        transport.request("initialize", INITIALIZE_PARAMS)
        transport.notify("notifications/initialized", {})

    def _call_tool(
        self,
        transport: _JsonRpcTransport,
        name: str,
        arguments: dict,
    ) -> dict:
        result = transport.request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        return self._decode_tool_result(name, result)

    def _decode_tool_result(self, tool_name: str, result: dict) -> dict:
        content = result.get("content")
        if not isinstance(content, list) or not content:
            raise McpControllerClientError(
                "malformed MCP content for %s" % tool_name
            )
        first = content[0]
        if not isinstance(first, dict):
            raise McpControllerClientError(
                "malformed MCP content for %s" % tool_name
            )
        text = first.get("text")
        if not isinstance(text, str):
            raise McpControllerClientError(
                "malformed MCP content for %s" % tool_name
            )
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise McpControllerClientError(
                "malformed MCP content for %s: %s" % (tool_name, exc)
            )
        if not isinstance(parsed, dict):
            raise McpControllerClientError(
                "malformed MCP content for %s" % tool_name
            )
        return parsed

    def _normalize_status(self, session_id: str, controller_status: dict) -> dict:
        session = controller_status.get("session")
        if not isinstance(session, dict):
            session = {}
        lock_status = session.get("status")
        if lock_status is None:
            locked = bool(session)
        else:
            locked = lock_status != "released"
        status = {
            "session_id": self._first_str(session.get("session_id"), session_id),
            "locked": locked,
            "dirty": bool(session.get("dirty", False)),
            "reconcile_required": bool(session.get("reconcile_required", False)),
            "session_head": session.get("session_head"),
            "owner": session.get("owner"),
            "transport": session.get("transport"),
            "status": lock_status,
            "controller_status": controller_status,
        }
        for key in ("cwd", "workspace_root", "project_root", "default_cwd"):
            value = self._first_str(session.get(key), controller_status.get(key))
            if value:
                status[key] = value
        return status

    def _run_id(self, result: dict) -> str:
        run = self._run(result)
        run_id = self._first_str(result.get("run_id"), run.get("run_id"))
        if not run_id:
            raise McpControllerClientError("controller start result missing run_id")
        return run_id

    def _session_id(self, result: dict, fallback: Optional[str]) -> str:
        run = self._run(result)
        session = result.get("session")
        if not isinstance(session, dict):
            session = {}
        session_id = self._first_str(
            result.get("session_id"),
            run.get("session_id"),
            session.get("session_id"),
            fallback,
        )
        if not session_id:
            raise McpControllerClientError("controller start result missing session_id")
        return session_id

    def _lock_token(self, result: dict) -> str:
        lock_token = self._lock_token_or_none(result)
        if not lock_token:
            raise McpControllerClientError("controller start result missing lock_token")
        return lock_token

    def _lock_token_or_none(self, result: dict) -> str:
        run = self._run(result)
        session = result.get("session")
        if not isinstance(session, dict):
            session = {}
        lock_token = self._first_str(
            result.get("lock_token"),
            session.get("lock_token"),
            run.get("lock_token"),
        )
        return lock_token

    def _terminal_replay_result(
        self,
        transport: _JsonRpcTransport,
        started: dict,
        run_id: str,
        fallback_session_id: Optional[str],
    ) -> Optional[ControllerRunResult]:
        status = self._run_status(started)
        if status not in TERMINAL_RUN_STATUSES:
            return None
        result = self._call_tool(
            transport,
            "cross_thread_read_result",
            {"run_id": run_id},
        )
        return self._run_result_from_read_result(
            started,
            result,
            fallback_session_id,
            run_id,
        )

    def _run_result_from_read_result(
        self,
        started: dict,
        result: dict,
        fallback_session_id: Optional[str],
        run_id: str,
    ) -> ControllerRunResult:
        run = self._run(result)
        session_id = self._first_str(
            run.get("session_id"),
            result.get("session_id"),
            started.get("session_id"),
            fallback_session_id,
        )
        session_head = self._first_str(
            run.get("actual_session_head"),
            run.get("session_head"),
            result.get("session_head"),
            started.get("session_head"),
            "",
        )
        status = self._first_str(
            run.get("status"),
            result.get("status"),
            self._run_status(started),
            "unknown",
        )
        text = result.get("result_text")
        if text is None:
            text = result.get("text", "")
        if not isinstance(text, str):
            text = str(text)
        if not text and status in {"failed", "cancelled"}:
            text = (
                "previous controller run %s is %s; send a new message to retry "
                "this request."
            ) % (run_id, status)
        approval_summary = self._approval_summary(run)
        return ControllerRunResult(
            run_id=run_id,
            session_id=session_id,
            session_head=session_head,
            status=status,
            text=text,
            approval_summary=approval_summary,
        )

    def _run_status(self, result: dict) -> str:
        run = self._run(result)
        return self._first_str(run.get("status"), result.get("status"))

    def _last_event_seq(self, result: dict) -> Optional[int]:
        run = self._run(result)
        value = result.get("last_event_seq")
        if value is None:
            value = run.get("last_event_seq")
        if value is None:
            return None
        return int(value)

    def _run(self, result: dict) -> dict:
        run = result.get("run")
        if isinstance(run, dict):
            return run
        return {}

    def _approval_summary(self, run: dict) -> Optional[str]:
        if run.get("status") != "blocked":
            return None
        reason = self._first_str(
            run.get("block_reason"),
            run.get("completion_reason"),
            "approval required",
        )
        return "Controller run blocked: %s" % reason

    def _first_str(self, *values: Any) -> str:
        for value in values:
            if value is None:
                continue
            text = str(value)
            if text:
                return text
        return ""
