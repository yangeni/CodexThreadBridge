from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pytest

from codex_thread_bridge.adapters.openilink import (
    OpeniLinkAdapterError,
    OpeniLinkChannelAdapter,
    normalize_openilink_event,
)
from codex_thread_bridge.models import (
    AttachmentDescriptor,
    AttachmentRef,
    ConversationType,
    SenderRole,
)


def load_fixture() -> Dict[str, Any]:
    path = Path(__file__).parent / "fixtures" / "openilink_text_message.json"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def test_private_text_event_normalizes_to_incoming_message() -> None:
    message = normalize_openilink_event(load_fixture(), {"owner-1"})

    assert message.platform == "wechat"
    assert message.conversation_type == ConversationType.PRIVATE
    assert message.conversation_id == "owner-chat"
    assert message.thread_key == "owner-chat"
    assert message.sender_id == "owner-1"
    assert message.sender_role == SenderRole.OWNER
    assert message.text == "/list"
    assert message.attachments == ()
    assert message.raw_ref == "wx-m-1"


def test_member_sender_normalizes_as_member() -> None:
    payload = load_fixture()
    payload["sender_id"] = "member-1"

    message = normalize_openilink_event(payload, {"owner-1"})

    assert message.sender_role == SenderRole.MEMBER


def test_thread_key_falls_back_to_conversation_id_or_uses_explicit_value() -> None:
    fallback = normalize_openilink_event(load_fixture(), {"owner-1"})
    payload = load_fixture()
    payload["thread_key"] = "thread-123"
    blank_payload = load_fixture()
    blank_payload["thread_key"] = "   "

    explicit = normalize_openilink_event(payload, {"owner-1"})
    blank = normalize_openilink_event(blank_payload, {"owner-1"})

    assert fallback.thread_key == "owner-chat"
    assert explicit.thread_key == "thread-123"
    assert blank.thread_key == "owner-chat"


@pytest.mark.parametrize("payload", [None, [], "x"])
def test_non_object_payload_raises_clear_adapter_error(payload: object) -> None:
    with pytest.raises(OpeniLinkAdapterError, match="payload"):
        normalize_openilink_event(payload, {"owner-1"})


@pytest.mark.parametrize("thread_key", [123, {}, []])
def test_non_string_thread_key_raises_clear_adapter_error(thread_key: object) -> None:
    payload = load_fixture()
    payload["thread_key"] = thread_key

    with pytest.raises(OpeniLinkAdapterError, match="thread_key"):
        normalize_openilink_event(payload, {"owner-1"})


def test_missing_text_defaults_to_empty_when_attachments_are_empty() -> None:
    payload = load_fixture()
    del payload["text"]

    message = normalize_openilink_event(payload, {"owner-1"})

    assert message.text == ""


@pytest.mark.parametrize("text", [None, 123])
def test_present_non_string_text_raises_clear_adapter_error(text: object) -> None:
    payload = load_fixture()
    payload["text"] = text

    with pytest.raises(OpeniLinkAdapterError, match="text"):
        normalize_openilink_event(payload, {"owner-1"})


def test_non_empty_attachments_normalize_to_download_descriptors() -> None:
    payload = load_fixture()
    payload["text"] = ""
    payload["attachments"] = [
        {"id": "att-1", "name": "image.png", "mime_type": "image/png"}
    ]

    message = normalize_openilink_event(payload, {"owner-1"})

    assert len(message.attachments) == 1
    attachment = message.attachments[0]
    assert isinstance(attachment, AttachmentDescriptor)
    assert attachment.source_message_id == "wx-m-1"
    assert attachment.mime_type == "image/png"
    assert attachment.original_name == "image.png"
    assert dict(attachment.descriptor) == {
        "id": "att-1",
        "name": "image.png",
        "mime_type": "image/png",
        "message_id": "wx-m-1",
    }
    assert isinstance(attachment.descriptor, dict)


def test_attachment_message_id_must_match_parent_message() -> None:
    payload = load_fixture()
    payload["attachments"] = [{"id": "att-1", "message_id": "other-message"}]

    with pytest.raises(OpeniLinkAdapterError, match="message_id"):
        normalize_openilink_event(payload, {"owner-1"})


@pytest.mark.parametrize(
    "attachments",
    [
        {},
        ["att-1"],
        [{"id": "att-1", "name": ""}],
        [{"id": "att-1", "message_id": ""}],
        [{"id": "att-1", "message_id": 123}],
    ],
)
def test_invalid_attachments_raise_clear_adapter_error(attachments: object) -> None:
    payload = load_fixture()
    payload["attachments"] = attachments

    with pytest.raises(OpeniLinkAdapterError, match="attachment"):
        normalize_openilink_event(payload, {"owner-1"})


def test_missing_required_field_raises_clear_adapter_error() -> None:
    payload = load_fixture()
    del payload["message_id"]

    with pytest.raises(OpeniLinkAdapterError, match="message_id"):
        normalize_openilink_event(payload, {"owner-1"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("message_id", None),
        ("conversation_id", ""),
        ("conversation_id", "   "),
        ("sender_id", ""),
    ],
)
def test_invalid_required_identifier_raises_clear_adapter_error(
    field: str,
    value: object,
) -> None:
    payload = load_fixture()
    payload[field] = value

    with pytest.raises(OpeniLinkAdapterError, match=field):
        normalize_openilink_event(payload, {"owner-1"})


def test_invalid_conversation_type_raises_clear_adapter_error() -> None:
    payload = load_fixture()
    payload["conversation_type"] = "channel"

    with pytest.raises(OpeniLinkAdapterError, match="conversation_type"):
        normalize_openilink_event(payload, {"owner-1"})


@pytest.mark.parametrize("conversation_type", [None, 123])
def test_non_string_conversation_type_raises_clear_adapter_error(
    conversation_type: object,
) -> None:
    payload = load_fixture()
    payload["conversation_type"] = conversation_type

    with pytest.raises(OpeniLinkAdapterError, match="conversation_type"):
        normalize_openilink_event(payload, {"owner-1"})


def test_adapter_delegates_messages_and_sends() -> None:
    client = FakeOpeniLinkClient([load_fixture()])
    adapter = OpeniLinkChannelAdapter(client, owner_user_ids={"owner-1"})

    messages = list(adapter.iter_messages())
    adapter.send_text("owner-chat", "hello")
    adapter.send_file("owner-chat", "/tmp/report.txt", "text/plain")
    adapter.send_typing("owner-chat", True)

    assert len(messages) == 1
    assert messages[0].raw_ref == "wx-m-1"
    assert client.calls == [
        ("iter_events",),
        ("send_text", "owner-chat", "hello"),
        ("send_file", "owner-chat", "/tmp/report.txt", "text/plain"),
        ("send_typing", "owner-chat", True),
    ]


def test_send_typing_without_client_method_is_noop() -> None:
    client = NoTypingOpeniLinkClient()
    adapter = OpeniLinkChannelAdapter(client, owner_user_ids={"owner-1"})

    adapter.send_typing("owner-chat", True)

    assert client.calls == []


@pytest.mark.parametrize(
    ("method_name", "call_args"),
    [
        ("send_text", ("owner-chat", "hello")),
        ("send_file", ("owner-chat", "/tmp/report.txt", "text/plain")),
        ("send_typing", ("owner-chat", True)),
    ],
)
def test_client_call_signature_errors_are_wrapped(
    method_name: str,
    call_args: tuple,
) -> None:
    adapter = OpeniLinkChannelAdapter(BadSignatureClient(), owner_user_ids={"owner-1"})
    method = getattr(adapter, method_name)

    with pytest.raises(OpeniLinkAdapterError, match="OpeniLink"):
        method(*call_args)


def test_iter_messages_client_errors_are_wrapped() -> None:
    adapter = OpeniLinkChannelAdapter(BadEventsClient(), owner_user_ids={"owner-1"})

    with pytest.raises(OpeniLinkAdapterError, match="iter_events"):
        list(adapter.iter_messages())


def test_download_attachment_delegates_to_client() -> None:
    ref = AttachmentRef(
        source_message_id="wx-m-1",
        local_path="/tmp/image.png",
        mime_type="image/png",
        original_name="image.png",
        direction="inbound",
    )
    client = FakeOpeniLinkClient([], attachment_ref=ref)
    adapter = OpeniLinkChannelAdapter(client, owner_user_ids={"owner-1"})

    result = adapter.download_attachment({"message_id": "wx-m-1"})

    assert result == ref
    assert client.calls == [("download_attachment", {"message_id": "wx-m-1"})]


def test_download_attachment_without_client_method_raises_clear_error() -> None:
    client = NoTypingOpeniLinkClient()
    adapter = OpeniLinkChannelAdapter(client, owner_user_ids={"owner-1"})

    with pytest.raises(NotImplementedError, match="download_attachment"):
        adapter.download_attachment({"message_id": "wx-m-1"})


def test_download_attachment_client_errors_are_wrapped() -> None:
    adapter = OpeniLinkChannelAdapter(BadSignatureClient(), owner_user_ids={"owner-1"})

    with pytest.raises(OpeniLinkAdapterError, match="download_attachment"):
        adapter.download_attachment({"message_id": "wx-m-1"})


class FakeOpeniLinkClient:
    def __init__(
        self,
        events: Iterable[Dict[str, Any]],
        attachment_ref: Optional[AttachmentRef] = None,
    ) -> None:
        self._events = list(events)
        self._attachment_ref = attachment_ref
        self.calls: List[tuple] = []

    def iter_events(self) -> Iterable[Dict[str, Any]]:
        self.calls.append(("iter_events",))
        return iter(self._events)

    def send_text(self, *, conversation_id: str, text: str) -> None:
        self.calls.append(("send_text", conversation_id, text))

    def send_file(self, *, conversation_id: str, path: str, mime_type: str) -> None:
        self.calls.append(("send_file", conversation_id, path, mime_type))

    def send_typing(self, *, conversation_id: str, enabled: bool) -> None:
        self.calls.append(("send_typing", conversation_id, enabled))

    def download_attachment(self, *, descriptor: Dict[str, Any]) -> AttachmentRef:
        self.calls.append(("download_attachment", descriptor))
        if self._attachment_ref is None:
            raise AssertionError("test client missing attachment_ref")
        return self._attachment_ref


class NoTypingOpeniLinkClient:
    def __init__(self) -> None:
        self.calls: List[tuple] = []

    def iter_events(self) -> Iterable[Dict[str, Any]]:
        self.calls.append(("iter_events",))
        return iter(())


class BadSignatureClient:
    def iter_events(self) -> Iterable[Dict[str, Any]]:
        return iter(())

    def send_text(self) -> None:
        pass

    def send_file(self) -> None:
        pass

    def send_typing(self) -> None:
        pass

    def download_attachment(self) -> AttachmentRef:
        raise AssertionError("unreachable")


class BadEventsClient:
    def iter_events(self) -> Iterable[Dict[str, Any]]:
        raise TypeError("bad iter_events signature")
