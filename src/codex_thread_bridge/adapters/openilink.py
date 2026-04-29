from __future__ import annotations

from typing import Any, Iterable, Iterator

from codex_thread_bridge.models import (
    AttachmentDescriptor,
    AttachmentRef,
    ConversationType,
    IncomingMessage,
    SenderRole,
)


class OpeniLinkAdapterError(ValueError):
    pass


def normalize_openilink_event(
    payload: Any,
    owner_user_ids: set[str],
) -> IncomingMessage:
    payload = _payload_object(payload)
    message_id = _required_identifier(payload, "message_id")
    conversation_id = _required_identifier(payload, "conversation_id")
    conversation_type = _conversation_type(_required(payload, "conversation_type"))
    sender_id = _required_identifier(payload, "sender_id")

    text = _text(payload)

    return IncomingMessage(
        platform="wechat",
        conversation_type=conversation_type,
        conversation_id=conversation_id,
        thread_key=_thread_key(payload, fallback=conversation_id),
        sender_id=sender_id,
        sender_role=(
            SenderRole.OWNER if sender_id in owner_user_ids else SenderRole.MEMBER
        ),
        text=text,
        attachments=_attachments(payload, message_id),
        raw_ref=message_id,
    )


class OpeniLinkChannelAdapter:
    def __init__(self, client: Any, owner_user_ids: Iterable[str]) -> None:
        self._client = client
        self._owner_user_ids = set(owner_user_ids)

    def iter_messages(self) -> Iterator[IncomingMessage]:
        try:
            for payload in self._client.iter_events():
                yield normalize_openilink_event(payload, self._owner_user_ids)
        except OpeniLinkAdapterError:
            raise
        except Exception as exc:
            raise OpeniLinkAdapterError("OpeniLink iter_events failed") from exc

    def download_attachment(self, descriptor: dict) -> AttachmentRef:
        download_attachment = getattr(self._client, "download_attachment", None)
        if download_attachment is None:
            raise NotImplementedError(
                "OpeniLink client does not support download_attachment"
            )
        try:
            return download_attachment(descriptor=descriptor)
        except Exception as exc:
            raise OpeniLinkAdapterError("OpeniLink download_attachment failed") from exc

    def send_text(self, conversation_id: str, text: str) -> None:
        try:
            self._client.send_text(conversation_id=conversation_id, text=text)
        except Exception as exc:
            raise OpeniLinkAdapterError("OpeniLink send_text failed") from exc

    def send_file(self, conversation_id: str, path: str, mime_type: str) -> None:
        try:
            self._client.send_file(
                conversation_id=conversation_id,
                path=path,
                mime_type=mime_type,
            )
        except Exception as exc:
            raise OpeniLinkAdapterError("OpeniLink send_file failed") from exc

    def send_typing(self, conversation_id: str, enabled: bool) -> None:
        send_typing = getattr(self._client, "send_typing", None)
        if send_typing is not None:
            try:
                send_typing(conversation_id=conversation_id, enabled=enabled)
            except Exception as exc:
                raise OpeniLinkAdapterError("OpeniLink send_typing failed") from exc


def _payload_object(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise OpeniLinkAdapterError("invalid payload: expected object")
    return payload


def _required(payload: dict, field: str) -> Any:
    try:
        return payload[field]
    except KeyError:
        raise OpeniLinkAdapterError("missing required field: %s" % field) from None


def _required_identifier(payload: dict, field: str) -> str:
    value = _required(payload, field)
    if not isinstance(value, str) or not value.strip():
        raise OpeniLinkAdapterError("invalid %s: expected non-empty string" % field)
    return value


def _conversation_type(value: Any) -> ConversationType:
    if not isinstance(value, str):
        raise OpeniLinkAdapterError("invalid conversation_type: expected string")
    try:
        return ConversationType(value)
    except ValueError:
        raise OpeniLinkAdapterError(
            "invalid conversation_type: %s" % value
        ) from None


def _text(payload: dict) -> str:
    if "text" not in payload:
        return ""
    value = payload["text"]
    if not isinstance(value, str):
        raise OpeniLinkAdapterError("invalid text: expected string")
    return value


def _thread_key(payload: dict, fallback: str) -> str:
    if "thread_key" not in payload or payload["thread_key"] is None:
        return fallback
    value = payload["thread_key"]
    if not isinstance(value, str):
        raise OpeniLinkAdapterError("invalid thread_key: expected string")
    if not value.strip():
        return fallback
    return value


def _attachments(payload: dict, message_id: str) -> tuple[AttachmentDescriptor, ...]:
    value = payload.get("attachments", ())
    if value is None:
        return ()
    if not isinstance(value, list):
        raise OpeniLinkAdapterError("invalid attachments: expected list")
    return tuple(_attachment_descriptor(item, message_id) for item in value)


def _attachment_descriptor(
    value: Any,
    message_id: str,
) -> AttachmentDescriptor:
    if not isinstance(value, dict):
        raise OpeniLinkAdapterError("invalid attachments: expected object entries")
    descriptor = dict(value)
    _normalize_attachment_message_id(descriptor, message_id)
    return AttachmentDescriptor(
        source_message_id=message_id,
        descriptor=descriptor,
        mime_type=_optional_string(
            value,
            ("mime_type", "mimeType"),
            default="application/octet-stream",
        ),
        original_name=_optional_string(
            value,
            ("original_name", "filename", "file_name", "name"),
            default="attachment",
        ),
    )


def _normalize_attachment_message_id(descriptor: dict, message_id: str) -> None:
    value = descriptor.get("message_id")
    if value is None:
        descriptor["message_id"] = message_id
        return
    if not isinstance(value, str) or not value.strip():
        raise OpeniLinkAdapterError(
            "invalid attachment message_id: expected non-empty string"
        )
    if value != message_id:
        raise OpeniLinkAdapterError("invalid attachment message_id: message mismatch")


def _optional_string(
    payload: dict,
    keys: tuple[str, ...],
    default: str,
) -> str:
    for key in keys:
        if key in payload and payload[key] is not None:
            value = payload[key]
            if not isinstance(value, str) or not value.strip():
                raise OpeniLinkAdapterError(
                    "invalid attachment %s: expected non-empty string" % key
                )
            return value
    return default
