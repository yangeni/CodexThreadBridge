from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Optional, Sequence, Union

from codex_thread_bridge.config import BridgeConfig
from codex_thread_bridge.controller_client import ControllerRunResult
from codex_thread_bridge.gateway import Gateway
from codex_thread_bridge.models import (
    ConversationType,
    ExecutionPolicy,
    IncomingMessage,
    SenderRole,
)
from codex_thread_bridge.stores import BridgeStore


def build_message(
    text: str,
    conversation_type: Union[str, ConversationType],
    sender_id: str,
    sequence: Optional[int] = None,
) -> IncomingMessage:
    normalized_type = ConversationType(conversation_type)
    if normalized_type == ConversationType.PRIVATE:
        conversation_id = "local-owner"
    elif normalized_type == ConversationType.GROUP:
        conversation_id = "local-group"
    else:
        raise ValueError("local simulator supports private or group conversations")

    return IncomingMessage(
        platform="local",
        conversation_type=normalized_type,
        conversation_id=conversation_id,
        thread_key=conversation_id,
        sender_id=sender_id,
        sender_role=_sender_role(sender_id),
        text=text,
        attachments=(),
        raw_ref=_raw_ref(
            text,
            normalized_type,
            sender_id,
            conversation_id,
            sequence,
        ),
    )


class EchoController:
    def __init__(self, project_root: Path) -> None:
        self.cwd = str(project_root)

    def status(self, session_id: str) -> dict:
        return {
            "session_id": session_id,
            "locked": False,
            "dirty": False,
            "reconcile_required": False,
            "session_head": "local-head",
            "cwd": self.cwd,
        }

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
        result_session_id = session_id or "local-session"
        return ControllerRunResult(
            run_id=_stable_id("run", idempotency_key, result_session_id),
            session_id=result_session_id,
            session_head=_stable_id("head", idempotency_key, result_session_id),
            status="completed",
            text="LOCAL: %s" % message,
            approval_summary=None,
        )

    def recover_session(
        self,
        *,
        session_id: str,
        owner: str,
        human_authorized: bool,
    ) -> dict:
        return {
            "session_id": session_id,
            "owner": owner,
            "human_authorized": human_authorized,
            "released": True,
            "acked_runs": [],
            "cancelled_runs": [],
            "closed_runs": [],
        }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local bridge simulator.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--sender-id", default="owner-1")
    parser.add_argument(
        "--conversation-type",
        choices=(ConversationType.PRIVATE.value, ConversationType.GROUP.value),
        default=ConversationType.PRIVATE.value,
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    config = BridgeConfig.local_dev(project_root, {args.sender_id})
    store = BridgeStore(config.sqlite_path)
    store.initialize()
    gateway = Gateway(config, store, EchoController(project_root))

    print(
        "Local simulator ready: project_root=%s sender_id=%s conversation_type=%s"
        % (config.project_root, args.sender_id, args.conversation_type)
    )
    for sequence, line in enumerate(sys.stdin, start=1):
        message = build_message(
            line.rstrip("\n"),
            args.conversation_type,
            args.sender_id,
            sequence=sequence,
        )
        reply = gateway.handle(message)
        print(reply.text)
    return 0


def _sender_role(sender_id: str) -> SenderRole:
    if sender_id == "owner-1":
        return SenderRole.OWNER
    return SenderRole.MEMBER


def _raw_ref(
    text: str,
    conversation_type: ConversationType,
    sender_id: str,
    conversation_id: str,
    sequence: Optional[int],
) -> str:
    return _stable_id(
        "msg",
        conversation_type.value,
        sender_id,
        conversation_id,
        str(sequence or 0),
        text,
    )


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return "%s-%s" % (prefix, digest.hexdigest()[:16])


if __name__ == "__main__":
    raise SystemExit(main())
