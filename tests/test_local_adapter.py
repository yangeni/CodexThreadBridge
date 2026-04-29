from __future__ import annotations

import io
import sys
from pathlib import Path

from codex_thread_bridge.adapters.local import build_message, main
from codex_thread_bridge.models import ConversationType, SenderRole
from codex_thread_bridge.stores import BridgeStore


def test_build_message_uses_stable_fields_and_sequence_raw_ref() -> None:
    first = build_message("hello", "private", "owner-1", sequence=1)
    second = build_message("hello", "private", "owner-1", sequence=2)

    assert first.platform == "local"
    assert first.conversation_type == ConversationType.PRIVATE
    assert first.conversation_id == "local-owner"
    assert first.thread_key == "local-owner"
    assert first.sender_id == "owner-1"
    assert first.sender_role == SenderRole.OWNER
    assert first.text == "hello"
    assert first.attachments == ()
    assert second.conversation_id == first.conversation_id
    assert second.thread_key == first.thread_key
    assert second.sender_role == first.sender_role
    assert second.raw_ref != first.raw_ref


def test_build_message_marks_non_default_sender_as_member() -> None:
    message = build_message("hello", "group", "member-1", sequence=1)

    assert message.conversation_type == ConversationType.GROUP
    assert message.conversation_id == "local-group"
    assert message.thread_key == "local-group"
    assert message.sender_role == SenderRole.MEMBER


def test_main_persists_project_root_as_alias_workspace(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project_root = tmp_path / "project-root"
    cwd = tmp_path / "different-cwd"
    project_root.mkdir()
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO("/add code 019-code\n/use code\nhello\n"),
    )

    result = main(["--project-root", str(project_root)])

    output = capsys.readouterr().out
    store = BridgeStore(project_root / "data" / "bridge.sqlite3")
    alias = store.get_alias("code")
    assert result == 0
    assert "Added alias code -> 019-code" in output
    assert "Using alias code" in output
    assert "LOCAL: hello" in output
    assert alias is not None
    assert alias.default_cwd == str(project_root.resolve())
    assert alias.policy.writable_roots == (str(project_root.resolve()),)
