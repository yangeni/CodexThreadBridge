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
