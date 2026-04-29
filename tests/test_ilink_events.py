from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from codex_thread_bridge.adapters.ilink_events import IlinkEventError, map_update_batch
from codex_thread_bridge.adapters.openilink import normalize_openilink_event


def _fixture(name: str) -> Dict[str, Any]:
    path = Path(__file__).parent / "fixtures" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_private_text_update_maps_to_openilink_payload_and_context() -> None:
    events = map_update_batch(_fixture("ilink_getupdates_text.json"))

    assert len(events) == 1
    event = events[0]
    assert event.payload == {
        "message_id": "12345",
        "conversation_id": "owner-1",
        "conversation_type": "private",
        "sender_id": "owner-1",
        "thread_key": "owner-1",
        "text": "/list",
        "mentions": [],
        "attachments": [],
    }
    normalized = normalize_openilink_event(event.payload, {"owner-1"})
    assert normalized.conversation_id == "owner-1"
    assert normalized.text == "/list"
    assert event.context.conversation_id == "owner-1"
    assert event.context.to_user_id == "owner-1"
    assert event.context.context_token == "ctx-owner-1"


def test_group_update_maps_as_group_but_runtime_can_ignore_it() -> None:
    events = map_update_batch(_fixture("ilink_getupdates_group_text.json"))

    assert events[0].payload["conversation_type"] == "group"
    assert events[0].payload["conversation_id"] == "group-1"
    assert events[0].payload["sender_id"] == "member-1"
    assert events[0].payload["text"] == "@Bot status?"


def test_non_finish_messages_are_skipped() -> None:
    payload = _fixture("ilink_getupdates_text.json")
    payload["msgs"][0]["message_state"] = 1

    assert map_update_batch(payload) == ()


def test_media_items_become_opaque_attachment_descriptors() -> None:
    payload = _fixture("ilink_getupdates_text.json")
    payload["msgs"][0]["item_list"] = [
        {"type": 2, "image_item": {"aes_key": "key", "cdn": "ref"}}
    ]

    events = map_update_batch(payload)

    assert events[0].payload["text"] == ""
    assert events[0].payload["attachments"] == [
        {
            "message_id": "12345",
            "mime_type": "application/octet-stream",
            "original_name": "attachment",
            "ilink_item": {"type": 2, "image_item": {"aes_key": "key", "cdn": "ref"}},
        }
    ]


def test_missing_required_message_field_raises_clear_error() -> None:
    payload = _fixture("ilink_getupdates_text.json")
    del payload["msgs"][0]["from_user_id"]

    with pytest.raises(IlinkEventError, match="from_user_id"):
        map_update_batch(payload)


def test_batch_mapping_can_skip_malformed_messages_with_error_callback() -> None:
    payload = _fixture("ilink_getupdates_text.json")
    malformed = dict(payload["msgs"][0])
    del malformed["from_user_id"]
    valid = dict(payload["msgs"][0])
    valid["message_id"] = "valid-2"
    valid["from_user_id"] = "owner-2"
    valid["context_token"] = "ctx-owner-2"
    valid["item_list"] = [{"type": 1, "text_item": {"text": "valid"}}]
    payload["msgs"] = [malformed, valid]
    errors = []

    events = map_update_batch(
        payload,
        on_error=lambda index, error: errors.append((index, error)),
    )

    assert len(events) == 1
    assert events[0].payload["message_id"] == "valid-2"
    assert events[0].payload["sender_id"] == "owner-2"
    assert events[0].payload["text"] == "valid"
    assert len(errors) == 1
    assert errors[0][0] == 0
    assert isinstance(errors[0][1], IlinkEventError)
    assert "from_user_id" in str(errors[0][1])


def test_missing_context_token_raises_clear_error() -> None:
    payload = _fixture("ilink_getupdates_text.json")
    del payload["msgs"][0]["context_token"]

    with pytest.raises(IlinkEventError, match="context_token"):
        map_update_batch(payload)


@pytest.mark.parametrize("context_token", ["", "   ", 123, {}, [], True])
def test_invalid_context_token_raises_clear_error(context_token: Any) -> None:
    payload = _fixture("ilink_getupdates_text.json")
    payload["msgs"][0]["context_token"] = context_token

    with pytest.raises(IlinkEventError, match="context_token"):
        map_update_batch(payload)


def test_string_message_id_maps_without_coercion() -> None:
    payload = _fixture("ilink_getupdates_text.json")
    payload["msgs"][0]["message_id"] = "msg-abc"

    events = map_update_batch(payload)

    assert events[0].payload["message_id"] == "msg-abc"


@pytest.mark.parametrize("message_id", ["", "   ", True, False, {}, []])
def test_invalid_message_id_raises_clear_error(message_id: Any) -> None:
    payload = _fixture("ilink_getupdates_text.json")
    payload["msgs"][0]["message_id"] = message_id

    with pytest.raises(IlinkEventError, match="message_id"):
        map_update_batch(payload)
