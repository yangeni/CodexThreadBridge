from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class RefreshResult:
    next_line: int
    summary: str
    source_truncated: bool = False


def read_new_items(path: Path, last_seen_line: int) -> RefreshResult:
    raw_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    line_count = len(raw_lines)
    if last_seen_line > line_count:
        return RefreshResult(
            next_line=last_seen_line,
            summary="Source truncated; refresh offset preserved.",
            source_truncated=True,
        )

    summaries = []
    next_line = last_seen_line
    start_index = max(last_seen_line, 0)

    for index in range(start_index, line_count):
        raw_line = raw_lines[index]
        line = raw_line.rstrip("\r\n")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            if index == line_count - 1 and not raw_line.endswith(("\n", "\r")):
                break
            next_line = index + 1
            continue

        next_line = index + 1

        if not isinstance(payload, dict):
            continue

        if payload.get("type") != "response_item":
            continue

        item = _extract_message_payload(payload)
        if item is None:
            continue

        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue

        content = item.get("content")
        if not isinstance(content, list):
            continue

        text_parts = _extract_text_parts(content)
        text = "".join(text_parts)
        if text_parts:
            summaries.append("%s: %s" % (role, text))

    summary = "\n".join(summaries) if summaries else "No new messages."
    if not summaries and next_line == last_seen_line:
        next_line = min(last_seen_line, line_count)
    return RefreshResult(next_line=next_line, summary=summary)


def _extract_message_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    message_payload = payload.get("payload")
    if isinstance(message_payload, dict):
        if message_payload.get("type") == "message":
            return message_payload

    return None


def _extract_text_parts(content: Iterable[Any]) -> list[str]:
    text_parts = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") not in {"text", "output_text", "input_text"}:
            continue
        text = part.get("text")
        if isinstance(text, str):
            text_parts.append(text)
    return text_parts
