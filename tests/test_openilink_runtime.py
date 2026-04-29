from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

import pytest

from codex_thread_bridge.adapters import openilink_runtime as runtime_module
from codex_thread_bridge.adapters.openilink_runtime import (
    OpeniLinkRuntime,
    build_runtime_from_env,
    main,
)
from codex_thread_bridge.models import OutgoingMessage
from codex_thread_bridge.stores import BridgeStore


@dataclass
class FakeGateway:
    replies: list[OutgoingMessage]
    seen_texts: list[str]

    def handle(self, msg):
        self.seen_texts.append(msg.text)
        return self.replies.pop(0)


class FakeIlinkClient:
    def __init__(self, batch: dict) -> None:
        self.batch = batch
        self.contexts: list[tuple[str, str, str]] = []
        self.sent: list[tuple[str, str]] = []

    def get_updates(self, cursor: str, timeout_seconds: Optional[float] = None) -> dict:
        assert cursor == ""
        return self.batch

    def remember_context(
        self,
        conversation_id: str,
        *,
        to_user_id: str,
        context_token: str,
    ) -> None:
        self.contexts.append((conversation_id, to_user_id, context_token))

    def send_text(self, *, conversation_id: str, text: str) -> None:
        self.sent.append((conversation_id, text))


def _private_message(text: str = "/help") -> dict:
    return {
        "message_id": 12345,
        "from_user_id": "owner-1",
        "to_user_id": "bot-1",
        "message_state": 2,
        "context_token": "ctx-owner-1",
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }


def _batch(*msgs: dict) -> dict:
    return {
        "ret": 0,
        "get_updates_buf": "cursor-2",
        "msgs": list(msgs),
    }


def test_process_one_batch_handles_private_message_sends_reply_and_advances_cursor(tmp_path) -> None:
    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()
    client = FakeIlinkClient(_batch(_private_message()))
    gateway = FakeGateway([OutgoingMessage("owner-1", "help text")], [])
    runtime = OpeniLinkRuntime(
        client=client,
        gateway=gateway,
        store=store,
        owner_user_ids={"owner-1"},
    )

    result = runtime.process_one_batch()

    assert result.messages_seen == 1
    assert result.replies_sent == 1
    assert result.cursor == "cursor-2"
    assert gateway.seen_texts == ["/help"]
    assert client.contexts == [("owner-1", "owner-1", "ctx-owner-1")]
    assert client.sent == [("owner-1", "help text")]
    assert store.get_runtime_state("ilink.cursor") == "cursor-2"


def test_empty_gateway_reply_does_not_send_text_but_advances_cursor(tmp_path) -> None:
    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()
    client = FakeIlinkClient(_batch(_private_message("")))
    gateway = FakeGateway([OutgoingMessage("owner-1", "")], [])
    runtime = OpeniLinkRuntime(
        client=client,
        gateway=gateway,
        store=store,
        owner_user_ids={"owner-1"},
    )

    result = runtime.process_one_batch()

    assert result.messages_seen == 1
    assert result.replies_sent == 0
    assert client.sent == []
    assert store.get_runtime_state("ilink.cursor") == "cursor-2"


def test_send_failure_records_event_and_does_not_advance_cursor(tmp_path) -> None:
    class FailingSendClient(FakeIlinkClient):
        def send_text(self, *, conversation_id: str, text: str) -> None:
            raise RuntimeError("network timeout secret-token")

    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()
    client = FailingSendClient(_batch(_private_message()))
    gateway = FakeGateway([OutgoingMessage("owner-1", "help text")], [])
    runtime = OpeniLinkRuntime(
        client=client,
        gateway=gateway,
        store=store,
        owner_user_ids={"owner-1"},
        redacted_values={"secret-token"},
    )

    result = runtime.process_one_batch()

    assert result.messages_seen == 1
    assert result.replies_sent == 0
    assert result.cursor == "cursor-2"
    assert store.get_runtime_state("ilink.cursor") is None
    events = store.list_events("delivery_failed")
    assert len(events) == 1
    payload = json.loads(str(events[0]["payload_json"]))
    assert payload == {
        "conversation_id": "owner-1",
        "reason": "network timeout [redacted]",
    }


def test_failed_delivery_replays_stored_reply_without_rerunning_gateway(tmp_path) -> None:
    class FailsOnceClient(FakeIlinkClient):
        def __init__(self, batch: dict) -> None:
            super().__init__(batch)
            self.should_fail = True

        def send_text(self, *, conversation_id: str, text: str) -> None:
            if self.should_fail:
                self.should_fail = False
                raise RuntimeError("network timeout")
            self.sent.append((conversation_id, text))

    @dataclass
    class SideEffectingGateway:
        calls: int = 0

        def handle(self, msg):
            self.calls += 1
            return OutgoingMessage(msg.conversation_id, "reply-%d" % self.calls)

    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()
    client = FailsOnceClient(_batch(_private_message("side effect")))
    gateway = SideEffectingGateway()
    runtime = OpeniLinkRuntime(
        client=client,
        gateway=gateway,
        store=store,
        owner_user_ids={"owner-1"},
    )

    first_result = runtime.process_one_batch()

    assert first_result.replies_sent == 0
    assert store.get_runtime_state("ilink.outbox.12345") == "reply-1"

    second_result = runtime.process_one_batch()

    assert second_result.replies_sent == 1
    assert gateway.calls == 1
    assert client.sent == [("owner-1", "reply-1")]
    assert store.get_runtime_state("ilink.cursor") == "cursor-2"
    assert store.get_runtime_state("ilink.processed.12345") == "1"


def test_partial_batch_failure_marks_successful_message_and_skips_duplicate_on_replay(tmp_path) -> None:
    class FailsSecondReplyOnceClient(FakeIlinkClient):
        def __init__(self, batch: dict) -> None:
            super().__init__(batch)
            self.fail_second_reply = True

        def get_updates(
            self, cursor: str, timeout_seconds: Optional[float] = None
        ) -> dict:
            assert cursor == "cursor-1"
            return self.batch

        def send_text(self, *, conversation_id: str, text: str) -> None:
            if text == "second reply" and self.fail_second_reply:
                self.fail_second_reply = False
                raise RuntimeError("network timeout")
            self.sent.append((conversation_id, text))

    first = _private_message("first")
    first["message_id"] = "msg-1"
    second = _private_message("second")
    second["message_id"] = "msg-2"
    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()
    store.set_runtime_state("ilink.cursor", "cursor-1")
    client = FailsSecondReplyOnceClient(_batch(first, second))
    gateway = FakeGateway(
        [
            OutgoingMessage("owner-1", "first reply"),
            OutgoingMessage("owner-1", "second reply"),
        ],
        [],
    )
    runtime = OpeniLinkRuntime(
        client=client,
        gateway=gateway,
        store=store,
        owner_user_ids={"owner-1"},
    )

    first_result = runtime.process_one_batch()

    assert first_result.replies_sent == 1
    assert store.get_runtime_state("ilink.cursor") == "cursor-1"
    assert store.get_runtime_state("ilink.processed.msg-1") == "1"
    assert store.get_runtime_state("ilink.processed.msg-2") is None

    second_result = runtime.process_one_batch()

    assert second_result.replies_sent == 1
    assert gateway.seen_texts == ["first", "second"]
    assert client.sent == [("owner-1", "first reply"), ("owner-1", "second reply")]
    assert store.get_runtime_state("ilink.cursor") == "cursor-2"
    assert store.get_runtime_state("ilink.processed.msg-2") == "1"


def test_group_message_is_ignored_by_v03_runtime_without_calling_gateway(tmp_path) -> None:
    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()
    client = FakeIlinkClient(
        _batch(
            {
                "message_id": 12345,
                "from_user_id": "member-1",
                "to_user_id": "group-1",
                "conversation_type": "group",
                "message_state": 2,
                "context_token": "ctx-group-1",
                "item_list": [{"type": 1, "text_item": {"text": "@Bot hello"}}],
            }
        )
    )
    gateway = FakeGateway([], [])
    runtime = OpeniLinkRuntime(
        client=client,
        gateway=gateway,
        store=store,
        owner_user_ids={"owner-1"},
    )

    result = runtime.process_one_batch()

    assert result.messages_seen == 1
    assert result.replies_sent == 0
    assert gateway.seen_texts == []
    assert client.sent == []
    assert store.get_runtime_state("ilink.cursor") == "cursor-2"
    events = store.list_events("ignored_non_private_message")
    assert len(events) == 1
    payload = json.loads(str(events[0]["payload_json"]))
    assert payload["conversation_id"] == "group-1"


def test_malformed_message_records_event_and_later_valid_message_still_advances_cursor(tmp_path) -> None:
    malformed = _private_message()
    del malformed["from_user_id"]
    valid = _private_message("valid")
    valid["message_id"] = "valid-2"
    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()
    client = FakeIlinkClient(_batch(malformed, valid))
    gateway = FakeGateway([OutgoingMessage("owner-1", "valid reply")], [])
    runtime = OpeniLinkRuntime(
        client=client,
        gateway=gateway,
        store=store,
        owner_user_ids={"owner-1"},
    )

    result = runtime.process_one_batch()

    assert result.messages_seen == 1
    assert result.replies_sent == 1
    assert gateway.seen_texts == ["valid"]
    assert client.sent == [("owner-1", "valid reply")]
    assert store.get_runtime_state("ilink.cursor") == "cursor-2"
    events = store.list_events("malformed_ilink_message")
    assert len(events) == 1
    payload = json.loads(str(events[0]["payload_json"]))
    assert payload["index"] == 0
    assert "from_user_id" in payload["reason"]


def test_build_runtime_from_env_requires_controller_command(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CTB_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ILINK_BASE_URL", "https://ilink.example.test/bot")
    monkeypatch.setenv("ILINK_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("ILINK_OWNER_USER_IDS", "owner-1")
    monkeypatch.delenv("CTB_CONTROLLER_COMMAND", raising=False)

    with pytest.raises(ValueError, match="CTB_CONTROLLER_COMMAND"):
        build_runtime_from_env()


def test_main_once_reuses_runtime_config_without_reloading_env(monkeypatch) -> None:
    class FakeRuntime:
        poll_timeout_seconds = 12.5

        def __init__(self) -> None:
            self.calls: list[Optional[float]] = []

        def process_one_batch(self, timeout_seconds: Optional[float] = None):
            self.calls.append(timeout_seconds)

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(runtime_module, "build_runtime_from_env", lambda: fake_runtime)
    monkeypatch.setattr(
        runtime_module.OpeniLinkRuntimeConfig,
        "from_env",
        lambda: pytest.fail("main should not reload OpeniLinkRuntimeConfig"),
    )

    assert main(["--once"]) == 0
    assert fake_runtime.calls == [12.5]


def test_run_forever_records_runtime_error_and_continues_to_next_batch(tmp_path) -> None:
    class RaisesOnceClient(FakeIlinkClient):
        def __init__(self, batch: dict) -> None:
            super().__init__(batch)
            self.calls = 0

        def get_updates(
            self, cursor: str, timeout_seconds: Optional[float] = None
        ) -> dict:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary outage secret-token")
            assert cursor == ""
            return self.batch

    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()
    client = RaisesOnceClient(_batch(_private_message("after outage")))
    gateway = FakeGateway([OutgoingMessage("owner-1", "processed later")], [])
    runtime = OpeniLinkRuntime(
        client=client,
        gateway=gateway,
        store=store,
        owner_user_ids={"owner-1"},
        redacted_values={"secret-token"},
    )

    runtime.run_forever(
        poll_timeout_seconds=0.0,
        idle_sleep_seconds=0.0,
        max_batches=2,
    )

    assert client.calls == 2
    assert gateway.seen_texts == ["after outage"]
    assert client.sent == [("owner-1", "processed later")]
    events = store.list_events("runtime_error")
    assert len(events) == 1
    payload = json.loads(str(events[0]["payload_json"]))
    assert payload == {"reason": "temporary outage [redacted]"}
