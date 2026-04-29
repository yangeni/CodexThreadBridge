from __future__ import annotations

import json
from pathlib import Path

from codex_thread_bridge.refresh import read_new_items


def _response_item(
    *,
    role: str,
    content: list[dict[str, object]],
    payload_type: str = "message",
) -> dict[str, object]:
    return {
        "type": "response_item",
        "payload": {
            "type": payload_type,
            "role": role,
            "content": content,
        },
    }


def _legacy_item_row(
    *,
    role: str,
    content: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "type": "response_item",
        "item": {
            "role": role,
            "content": content,
        },
    }


def test_read_new_items_from_jsonl_after_line_offset(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    rows = [
        _response_item(
            role="user",
            content=[{"type": "output_text", "text": "hi"}],
        ),
        _response_item(
            role="assistant",
            content=[{"type": "output_text", "text": "hello"}],
        ),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = read_new_items(path, last_seen_line=1)

    assert result.next_line == 2
    assert result.summary == "assistant: hello"


def test_read_new_items_ignores_invalid_json_and_advances(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    _response_item(
                        role="user",
                        content=[{"type": "input_text", "text": "first"}],
                    )
                ),
                "{not-json",
                json.dumps(
                    _response_item(
                        role="assistant",
                        content=[{"type": "output_text", "text": "second"}],
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=0)

    assert result.next_line == 3
    assert result.summary == "user: first\nassistant: second"


def test_read_new_items_leaves_incomplete_trailing_row_for_retry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    _response_item(
                        role="assistant",
                        content=[{"type": "output_text", "text": "kept"}],
                    )
                ),
                "{not-json",
            ]
        ),
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=0)

    assert result.next_line == 1
    assert result.summary == "assistant: kept"


def test_read_new_items_advances_past_malformed_final_row_with_newline(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    _response_item(
                        role="assistant",
                        content=[{"type": "output_text", "text": "kept"}],
                    )
                ),
                "{not-json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=0)

    assert result.next_line == 2
    assert result.summary == "assistant: kept"


def test_read_new_items_returns_no_new_messages_when_offset_is_at_end(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            _response_item(
                role="assistant",
                content=[{"type": "output_text", "text": "done"}],
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=1)

    assert result.next_line == 1
    assert result.summary == "No new messages."


def test_read_new_items_signals_truncation_when_source_shrinks(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            _response_item(
                role="assistant",
                content=[{"type": "output_text", "text": "kept"}],
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=3)

    assert result.next_line == 3
    assert result.source_truncated is True
    assert result.summary == "Source truncated; refresh offset preserved."


def test_read_new_items_ignores_non_user_non_assistant_roles(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    _response_item(
                        role="system",
                        content=[{"type": "output_text", "text": "ignored"}],
                    )
                ),
                json.dumps(
                    _response_item(
                        role="assistant",
                        content=[{"type": "output_text", "text": "kept"}],
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=0)

    assert result.next_line == 2
    assert result.summary == "assistant: kept"


def test_read_new_items_concatenates_multiple_text_parts(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            _response_item(
                role="assistant",
                content=[
                    {"type": "output_text", "text": "part"},
                    {"type": "output_text", "text": " one"},
                    {"type": "input_text", "text": " two"},
                ],
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=0)

    assert result.next_line == 1
    assert result.summary == "assistant: part one two"


def test_read_new_items_concatenates_text_parts_without_added_separators(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            _response_item(
                role="assistant",
                content=[
                    {"type": "output_text", "text": "ab"},
                    {"type": "output_text", "text": "cd"},
                    {"type": "input_text", "text": " ef"},
                ],
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=0)

    assert result.next_line == 1
    assert result.summary == "assistant: abcd ef"


def test_read_new_items_preserves_whitespace_only_text(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            _response_item(
                role="assistant",
                content=[{"type": "output_text", "text": "   "}],
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=0)

    assert result.next_line == 1
    assert result.summary == "assistant:    "


def test_read_new_items_ignores_non_message_payloads(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    _response_item(
                        role="assistant",
                        content=[{"type": "output_text", "text": "ignore"}],
                        payload_type="tool_call",
                    )
                ),
                json.dumps(
                    _response_item(
                        role="assistant",
                        content=[{"type": "output_text", "text": "kept"}],
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=0)

    assert result.next_line == 2
    assert result.summary == "assistant: kept"


def test_read_new_items_ignores_non_text_content_parts(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            _response_item(
                role="assistant",
                content=[
                    {"type": "reasoning", "text": "ignored"},
                    {"type": "output_text", "text": "kept"},
                    {"type": "tool_result", "text": "ignored too"},
                ],
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=0)

    assert result.next_line == 1
    assert result.summary == "assistant: kept"


def test_read_new_items_ignores_non_string_text_values(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            _response_item(
                role="assistant",
                content=[
                    {"type": "output_text", "text": 1},
                    {"type": "output_text", "text": "kept"},
                    {"type": "input_text", "text": None},
                ],
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=0)

    assert result.next_line == 1
    assert result.summary == "assistant: kept"


def test_read_new_items_ignores_non_response_item_rows(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "event",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "ignored"}],
                        },
                    }
                ),
                json.dumps(
                    _response_item(
                        role="assistant",
                        content=[{"type": "output_text", "text": "kept"}],
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=0)

    assert result.next_line == 2
    assert result.summary == "assistant: kept"


def test_read_new_items_ignores_legacy_item_rows(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    _legacy_item_row(
                        role="assistant",
                        content=[{"type": "output_text", "text": "ignored"}],
                    )
                ),
                json.dumps(
                    _response_item(
                        role="assistant",
                        content=[{"type": "output_text", "text": "kept"}],
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_new_items(path, last_seen_line=0)

    assert result.next_line == 2
    assert result.summary == "assistant: kept"
