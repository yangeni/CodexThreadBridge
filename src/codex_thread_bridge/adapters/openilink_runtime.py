from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from codex_thread_bridge.adapters.ilink_client import IlinkHttpClient
from codex_thread_bridge.adapters.ilink_events import IlinkEventError, map_update_batch
from codex_thread_bridge.adapters.openilink import normalize_openilink_event
from codex_thread_bridge.config import OpeniLinkRuntimeConfig
from codex_thread_bridge.controller_client import McpControllerClient
from codex_thread_bridge.gateway import Gateway
from codex_thread_bridge.stores import BridgeStore


@dataclass(frozen=True)
class RuntimeBatchResult:
    messages_seen: int
    replies_sent: int
    cursor: str


_GROUP_MANAGEMENT_DISABLED_REPLY = "Group QA runtime is not enabled in v0.3."


class OpeniLinkRuntime:
    def __init__(
        self,
        *,
        client,
        gateway,
        store: BridgeStore,
        owner_user_ids: Iterable[str],
        redacted_values: Iterable[str] = (),
        poll_timeout_seconds: float = 35.0,
    ) -> None:
        self.client = client
        self.gateway = gateway
        self.store = store
        self.owner_user_ids = set(owner_user_ids)
        self.redacted_values = tuple(value for value in redacted_values if value)
        self.poll_timeout_seconds = float(poll_timeout_seconds)

    def process_one_batch(
        self,
        timeout_seconds: Optional[float] = None,
    ) -> RuntimeBatchResult:
        cursor = self.store.get_runtime_state("ilink.cursor") or ""
        batch = self.client.get_updates(cursor, timeout_seconds=timeout_seconds)
        events = map_update_batch(batch, on_error=self._record_malformed_message)
        replies_sent = 0
        delivery_failed = False

        for event in events:
            message_id = str(event.payload["message_id"])
            if self.store.get_runtime_state(_processed_message_key(message_id)):
                continue

            self.client.remember_context(
                event.context.conversation_id,
                to_user_id=event.context.to_user_id,
                context_token=event.context.context_token,
            )
            if event.payload.get("conversation_type") != "private":
                self.store.record_event(
                    "ignored_non_private_message",
                    {"conversation_id": event.context.conversation_id},
                )
                self._mark_message_processed(message_id)
                continue

            outbox_key = _outbox_message_key(message_id)
            reply_text = self.store.get_runtime_state(outbox_key)
            reply_conversation_id = event.context.conversation_id
            if reply_text is None:
                if _is_group_management_command(str(event.payload.get("text", ""))):
                    reply_text = _GROUP_MANAGEMENT_DISABLED_REPLY
                else:
                    msg = normalize_openilink_event(event.payload, self.owner_user_ids)
                    reply = self.gateway.handle(msg)
                    reply_text = reply.text
                    reply_conversation_id = reply.conversation_id
                if reply_text:
                    self.store.set_runtime_state(outbox_key, reply_text)

            if not reply_text:
                self._mark_message_processed(message_id)
                continue
            try:
                self.client.send_text(
                    conversation_id=reply_conversation_id,
                    text=reply_text,
                )
            except Exception as exc:
                delivery_failed = True
                self.store.record_event(
                    "delivery_failed",
                    {
                        "conversation_id": reply_conversation_id,
                        "reason": _sanitize_error(exc, self.redacted_values),
                    },
                )
                break
            self._mark_message_processed(message_id)
            replies_sent += 1

        new_cursor = str(batch.get("get_updates_buf") or cursor)
        if not delivery_failed:
            self.store.set_runtime_state("ilink.cursor", new_cursor)
        return RuntimeBatchResult(
            messages_seen=len(events),
            replies_sent=replies_sent,
            cursor=new_cursor,
        )

    def run_forever(
        self,
        poll_timeout_seconds: float,
        idle_sleep_seconds: float = 1.0,
        max_batches: Optional[int] = None,
    ) -> None:
        batches_seen = 0
        while max_batches is None or batches_seen < max_batches:
            try:
                result = self.process_one_batch(timeout_seconds=poll_timeout_seconds)
            except Exception as exc:
                self.store.record_event(
                    "runtime_error",
                    {"reason": _sanitize_error(exc, self.redacted_values)},
                )
                time.sleep(idle_sleep_seconds)
            else:
                if result.messages_seen == 0:
                    time.sleep(idle_sleep_seconds)
            batches_seen += 1

    def _record_malformed_message(self, index: int, error: IlinkEventError) -> None:
        self.store.record_event(
            "malformed_ilink_message",
            {"index": index, "reason": _sanitize_error(error, self.redacted_values)},
        )

    def _mark_message_processed(self, message_id: str) -> None:
        self.store.set_runtime_state(_processed_message_key(message_id), "1")


def build_runtime_from_env() -> OpeniLinkRuntime:
    config = OpeniLinkRuntimeConfig.from_env()
    if not config.controller_command:
        raise ValueError("CTB_CONTROLLER_COMMAND must be set for real runtime")

    store = BridgeStore(config.bridge.sqlite_path)
    store.initialize()
    controller = McpControllerClient(
        command=config.controller_command,
        request_timeout_seconds=config.request_timeout_seconds,
    )
    gateway = Gateway(config.bridge, store, controller)
    client = IlinkHttpClient(
        config.base_url,
        config.bot_token,
        default_timeout_seconds=config.request_timeout_seconds,
    )
    return OpeniLinkRuntime(
        client=client,
        gateway=gateway,
        store=store,
        owner_user_ids=config.owner_user_ids,
        redacted_values={config.bot_token},
        poll_timeout_seconds=config.poll_timeout_seconds,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run CodexThreadBridge OpeniLink runtime"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="process one update batch then exit",
    )
    args = parser.parse_args(argv)
    runtime = build_runtime_from_env()
    if args.once:
        runtime.process_one_batch(timeout_seconds=runtime.poll_timeout_seconds)
        return 0
    runtime.run_forever(poll_timeout_seconds=runtime.poll_timeout_seconds)
    return 0


def _sanitize_error(exc: Exception, redacted_values: Iterable[str]) -> str:
    text = str(exc)
    for value in redacted_values:
        text = text.replace(value, "[redacted]")
    return text[:300]


def _is_group_management_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return stripped.split(None, 1)[0].lower() == "/group"


def _processed_message_key(message_id: str) -> str:
    return "ilink.processed.%s" % message_id


def _outbox_message_key(message_id: str) -> str:
    return "ilink.outbox.%s" % message_id


if __name__ == "__main__":
    raise SystemExit(main())
