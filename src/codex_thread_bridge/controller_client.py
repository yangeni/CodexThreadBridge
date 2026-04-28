from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from codex_thread_bridge.models import ExecutionPolicy


@dataclass(frozen=True)
class ControllerRunResult:
    run_id: str
    session_id: str
    session_head: str
    status: str
    text: str
    approval_summary: Optional[str]


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
    ) -> ControllerRunResult:
        ...
