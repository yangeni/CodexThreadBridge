from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


class IlinkEventError(ValueError):
    pass


@dataclass(frozen=True)
class IlinkConversationContext:
    conversation_id: str
    to_user_id: str
    context_token: str


@dataclass(frozen=True)
class MappedIlinkEvent:
    payload: Dict[str, Any]
    context: IlinkConversationContext


def map_update_batch(
    batch: Dict[str, Any],
    on_error: Optional[Callable[[int, IlinkEventError], None]] = None,
) -> Tuple[MappedIlinkEvent, ...]:
    if not isinstance(batch, dict):
        raise IlinkEventError("update batch must be an object")
    msgs = batch.get("msgs", ())
    if msgs is None:
        return ()
    if not isinstance(msgs, list):
        raise IlinkEventError("msgs must be a list")
    events: List[MappedIlinkEvent] = []
    for index, msg in enumerate(msgs):
        try:
            event = _map_message(msg)
        except IlinkEventError as error:
            if on_error is None:
                raise
            on_error(index, error)
            continue
        if event is not None:
            events.append(event)
    return tuple(events)


def _map_message(msg: Any) -> Optional[MappedIlinkEvent]:
    if not isinstance(msg, dict):
        raise IlinkEventError("message must be an object")
    if msg.get("message_state", 2) != 2:
        return None
    message_id = _message_id(msg)
    sender_id = _required_string(msg, "from_user_id")
    context_token = _required_string(msg, "context_token")
    conversation_type = _conversation_type(msg)
    conversation_id = _conversation_id(msg, conversation_type, sender_id)
    text, attachments = _items(msg.get("item_list", ()), message_id)
    return MappedIlinkEvent(
        payload={
            "message_id": message_id,
            "conversation_id": conversation_id,
            "conversation_type": conversation_type,
            "sender_id": sender_id,
            "thread_key": conversation_id,
            "text": text,
            "mentions": [],
            "attachments": attachments,
        },
        context=IlinkConversationContext(
            conversation_id=conversation_id,
            to_user_id=sender_id if conversation_type == "private" else conversation_id,
            context_token=context_token,
        ),
    )


def _conversation_type(msg: Dict[str, Any]) -> str:
    value = msg.get("conversation_type")
    if value in ("private", "group"):
        return str(value)
    to_user_id = msg.get("to_user_id")
    if isinstance(to_user_id, str) and to_user_id.startswith("group"):
        return "group"
    return "private"


def _conversation_id(
    msg: Dict[str, Any],
    conversation_type: str,
    sender_id: str,
) -> str:
    if conversation_type == "private":
        return sender_id
    return _required_string(msg, "to_user_id")


def _items(value: Any, message_id: str) -> Tuple[str, List[Dict[str, Any]]]:
    if value is None:
        return "", []
    if not isinstance(value, list):
        raise IlinkEventError("item_list must be a list")
    texts: List[str] = []
    attachments: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise IlinkEventError("item_list entries must be objects")
        if item.get("type") == 1:
            text_item = item.get("text_item")
            if not isinstance(text_item, dict):
                raise IlinkEventError("text_item must be an object")
            text = text_item.get("text", "")
            if not isinstance(text, str):
                raise IlinkEventError("text_item.text must be a string")
            texts.append(text)
        else:
            attachments.append(
                {
                    "message_id": message_id,
                    "mime_type": "application/octet-stream",
                    "original_name": "attachment",
                    "ilink_item": item,
                }
            )
    return "\n".join(texts), attachments


def _required(msg: Dict[str, Any], field: str) -> Any:
    if field not in msg:
        raise IlinkEventError("missing required message field: %s" % field)
    return msg[field]


def _message_id(msg: Dict[str, Any]) -> str:
    value = _required(msg, "message_id")
    if isinstance(value, bool):
        raise IlinkEventError("invalid message field: message_id")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value
    raise IlinkEventError("invalid message field: message_id")


def _required_string(msg: Dict[str, Any], field: str) -> str:
    value = _required(msg, field)
    if not isinstance(value, str) or not value.strip():
        raise IlinkEventError("invalid message field: %s" % field)
    return value
