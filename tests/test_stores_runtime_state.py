from __future__ import annotations

import json

from codex_thread_bridge.stores import BridgeStore


def test_runtime_state_round_trips_string_values(tmp_path) -> None:
    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()

    assert store.get_runtime_state("ilink.cursor") is None
    store.set_runtime_state("ilink.cursor", "cursor-1")

    assert store.get_runtime_state("ilink.cursor") == "cursor-1"


def test_runtime_event_records_sanitized_payload(tmp_path) -> None:
    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()

    event_id = store.record_event(
        "delivery_failed",
        {"conversation_id": "owner-1", "reason": "timeout"},
    )
    events = store.list_events("delivery_failed")

    assert event_id == 1
    assert events == [
        {
            "id": 1,
            "event_type": "delivery_failed",
            "payload_json": "{\"conversation_id\": \"owner-1\", \"reason\": \"timeout\"}",
        }
    ]


def test_runtime_event_stringifies_non_json_native_payload_values(tmp_path) -> None:
    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()

    store.record_event(
        "delivery_failed",
        {
            "attachment_path": tmp_path / "artifact.txt",
            "failure": RuntimeError("connection dropped"),
            "raw_response": b"not json",
        },
    )

    event = store.list_events("delivery_failed")[0]
    assert json.loads(str(event["payload_json"])) == {
        "attachment_path": str(tmp_path / "artifact.txt"),
        "failure": "connection dropped",
        "raw_response": "b'not json'",
    }
