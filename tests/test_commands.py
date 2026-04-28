from __future__ import annotations

import pytest

from codex_thread_bridge.commands import CommandKind, parse_command
from codex_thread_bridge.models import ConversationType


def test_parse_private_add_and_send_commands() -> None:
    add = parse_command("/add code 019-code", ConversationType.PRIVATE)
    assert add.kind == CommandKind.ADD_ALIAS
    assert add.args == ("code", "019-code")

    send = parse_command("/send paper continue the plan", ConversationType.PRIVATE)
    assert send.kind == CommandKind.SEND_ONCE
    assert send.args == ("paper", "continue the plan")


def test_plain_private_message_is_not_a_command() -> None:
    parsed = parse_command("continue from the last step", ConversationType.PRIVATE)
    assert parsed.kind == CommandKind.PLAIN_TEXT
    assert parsed.args == ("continue from the last step",)


def test_group_rejects_work_commands() -> None:
    parsed = parse_command("@Bot /use code", ConversationType.GROUP)
    assert parsed.kind == CommandKind.GROUP_FORBIDDEN_COMMAND


def test_group_qa_status_is_allowed() -> None:
    parsed = parse_command("@Bot /qa status", ConversationType.GROUP)
    assert parsed.kind == CommandKind.GROUP_QA_STATUS
    assert parsed.args == ()


def test_bare_group_qa_status_is_forbidden() -> None:
    parsed = parse_command("/qa status", ConversationType.GROUP)
    assert parsed.kind == CommandKind.GROUP_FORBIDDEN_COMMAND


def test_group_qa_status_with_extra_args_is_forbidden() -> None:
    parsed = parse_command("@Bot /qa status extra", ConversationType.GROUP)
    assert parsed.kind == CommandKind.GROUP_FORBIDDEN_COMMAND


@pytest.mark.parametrize(
    "text",
    [
        "@Bot /status",
        "@Bot /help",
        "@Bot /group approve friends",
        "/",
        "/group",
    ],
)
def test_group_slash_like_commands_are_forbidden(text: str) -> None:
    parsed = parse_command(text, ConversationType.GROUP)
    assert parsed.kind == CommandKind.GROUP_FORBIDDEN_COMMAND


def test_group_non_slash_text_is_plain_text() -> None:
    parsed = parse_command("@Bot what is this?", ConversationType.GROUP)
    assert parsed.kind == CommandKind.PLAIN_TEXT
    assert parsed.args == ("what is this?",)


def test_group_chatter_is_ignored() -> None:
    parsed = parse_command("hello everyone", ConversationType.GROUP)
    assert parsed.kind == CommandKind.GROUP_IGNORED
    assert parsed.args == ()


def test_group_non_bot_mention_is_ignored() -> None:
    parsed = parse_command("@Alice /qa status", ConversationType.GROUP)
    assert parsed.kind == CommandKind.GROUP_IGNORED
    assert parsed.args == ()


def test_bind_maps_to_compatibility_command() -> None:
    parsed = parse_command("/bind 019-code", ConversationType.PRIVATE)
    assert parsed.kind == CommandKind.BIND_COMPAT
    assert parsed.args == ("019-code",)


def test_group_approve_maps_to_group_approve() -> None:
    parsed = parse_command("/group approve friends", ConversationType.PRIVATE)
    assert parsed.kind == CommandKind.GROUP_APPROVE
    assert parsed.args == ("friends",)


@pytest.mark.parametrize(
    "text",
    [
        "/add code 019 extra",
        "/bind 019 extra",
        "/use code extra",
        "/rm code extra",
        "/remove code extra",
        "/sendfile 123 extra",
        "/artifacts a b",
        "/status a b",
        "/refresh a b",
        "/group approve one two three",
        "/group status one two",
        "/group reset one two",
        "/group disable one two",
    ],
)
def test_extra_tokens_on_fixed_arity_commands_return_help(text: str) -> None:
    parsed = parse_command(text, ConversationType.PRIVATE)
    assert parsed.kind == CommandKind.HELP


@pytest.mark.parametrize(
    "text",
    [
        "/group approve",
        "/group status",
        "/group reset",
        "/group disable",
    ],
)
def test_incomplete_private_group_commands_return_help(text: str) -> None:
    parsed = parse_command(text, ConversationType.PRIVATE)
    assert parsed.kind == CommandKind.HELP


@pytest.mark.parametrize(
    "text",
    [
        "/",
        "/group",
        "/group nope",
        "/qa nope",
        "/unknown",
        "/add code",
        "/send paper",
    ],
)
def test_malformed_command_input_does_not_raise(text: str) -> None:
    parsed = parse_command(text, ConversationType.PRIVATE)
    assert parsed.kind != CommandKind.PLAIN_TEXT
    assert parsed.kind == CommandKind.HELP
