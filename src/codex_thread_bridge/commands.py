from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from codex_thread_bridge.models import ConversationType


class CommandKind(str, Enum):
    ADD_ALIAS = "ADD_ALIAS"
    USE_ALIAS = "USE_ALIAS"
    LIST_ALIASES = "LIST_ALIASES"
    REMOVE_ALIAS = "REMOVE_ALIAS"
    STATUS = "STATUS"
    REFRESH = "REFRESH"
    SEND_ONCE = "SEND_ONCE"
    ARTIFACTS = "ARTIFACTS"
    SEND_FILE = "SEND_FILE"
    HELP = "HELP"
    BIND_COMPAT = "BIND_COMPAT"
    GROUP_PENDING = "GROUP_PENDING"
    GROUP_APPROVE = "GROUP_APPROVE"
    GROUP_LIST = "GROUP_LIST"
    GROUP_STATUS = "GROUP_STATUS"
    GROUP_RESET = "GROUP_RESET"
    GROUP_DISABLE = "GROUP_DISABLE"
    GROUP_QA_STATUS = "GROUP_QA_STATUS"
    GROUP_FORBIDDEN_COMMAND = "GROUP_FORBIDDEN_COMMAND"
    GROUP_IGNORED = "GROUP_IGNORED"
    PLAIN_TEXT = "PLAIN_TEXT"


@dataclass(frozen=True)
class ParsedCommand:
    kind: CommandKind
    args: Tuple[str, ...]


_PRIVATE_WORK_COMMANDS = frozenset(
    {
        "add",
        "bind",
        "use",
        "list",
        "rm",
        "remove",
        "status",
        "refresh",
        "send",
        "artifacts",
        "sendfile",
    }
)

_GROUP_FORBIDDEN_COMMANDS = frozenset(
    {
        "add",
        "bind",
        "use",
        "list",
        "rm",
        "remove",
        "refresh",
        "send",
        "artifacts",
        "sendfile",
    }
)

_GROUP_KIND_BY_NAME = {
    "pending": CommandKind.GROUP_PENDING,
    "approve": CommandKind.GROUP_APPROVE,
    "list": CommandKind.GROUP_LIST,
    "status": CommandKind.GROUP_STATUS,
    "reset": CommandKind.GROUP_RESET,
    "disable": CommandKind.GROUP_DISABLE,
}


def _strip_leading_mention(text: str) -> str:
    if not text.startswith("@Bot"):
        return text
    parts = text.split(None, 1)
    if not parts:
        return text
    if parts[0] != "@Bot":
        return text
    if len(parts) == 1:
        return ""
    return parts[1].lstrip()


def _help_command() -> ParsedCommand:
    return ParsedCommand(CommandKind.HELP, ())


def _plain_text(text: str) -> ParsedCommand:
    return ParsedCommand(CommandKind.PLAIN_TEXT, (text,))


def _parse_tokens(text: str) -> Tuple[str, Tuple[str, ...]]:
    body = text.lstrip("/")
    if not body:
        return "", ()
    parts = body.split()
    if not parts:
        return "", ()
    return parts[0].lower(), tuple(parts[1:])


def _parse_private(text: str) -> ParsedCommand:
    if not text.startswith("/"):
        return _plain_text(text)

    command, args = _parse_tokens(text)
    if not command:
        return _help_command()

    if command == "add":
        if len(args) != 2:
            return _help_command()
        return ParsedCommand(CommandKind.ADD_ALIAS, (args[0], args[1]))

    if command == "bind":
        if len(args) != 1:
            return _help_command()
        return ParsedCommand(CommandKind.BIND_COMPAT, (args[0],))

    if command == "use":
        if len(args) != 1:
            return _help_command()
        return ParsedCommand(CommandKind.USE_ALIAS, (args[0],))

    if command == "list":
        return ParsedCommand(CommandKind.LIST_ALIASES, ())

    if command in ("rm", "remove"):
        if len(args) != 1:
            return _help_command()
        return ParsedCommand(CommandKind.REMOVE_ALIAS, (args[0],))

    if command == "status":
        if len(args) > 1:
            return _help_command()
        return ParsedCommand(CommandKind.STATUS, tuple(args))

    if command == "refresh":
        if len(args) > 1:
            return _help_command()
        return ParsedCommand(CommandKind.REFRESH, tuple(args))

    if command == "send":
        if len(args) < 2:
            return _help_command()
        return ParsedCommand(CommandKind.SEND_ONCE, (args[0], " ".join(args[1:])))

    if command == "artifacts":
        if len(args) > 1:
            return _help_command()
        return ParsedCommand(CommandKind.ARTIFACTS, tuple(args))

    if command == "sendfile":
        if len(args) != 1:
            return _help_command()
        return ParsedCommand(CommandKind.SEND_FILE, (args[0],))

    if command == "help":
        return ParsedCommand(CommandKind.HELP, tuple(args))

    if command == "group":
        if len(args) < 1:
            return _help_command()
        group_command = args[0].lower()
        group_args = args[1:]
        kind = _GROUP_KIND_BY_NAME.get(group_command)
        if kind is None:
            return _help_command()
        if kind == CommandKind.GROUP_PENDING or kind == CommandKind.GROUP_LIST:
            if len(group_args) != 0:
                return _help_command()
        elif kind in (
            CommandKind.GROUP_APPROVE,
        ):
            if len(group_args) < 1 or len(group_args) > 2:
                return _help_command()
        elif kind in (
            CommandKind.GROUP_STATUS,
            CommandKind.GROUP_RESET,
            CommandKind.GROUP_DISABLE,
        ):
            if len(group_args) != 1:
                return _help_command()
        if kind in (
            CommandKind.GROUP_APPROVE,
            CommandKind.GROUP_STATUS,
            CommandKind.GROUP_RESET,
            CommandKind.GROUP_DISABLE,
        ) and len(group_args) < 1:
            return _help_command()
        return ParsedCommand(kind, tuple(group_args))

    if command == "qa":
        if len(args) == 1 and args[0].lower() == "status":
            return ParsedCommand(CommandKind.GROUP_QA_STATUS, tuple(args[1:]))
        return _help_command()

    return _help_command()


def _parse_group(text: str) -> ParsedCommand:
    detection_text = _strip_leading_mention(text)
    if detection_text == text:
        if text.startswith("/"):
            return ParsedCommand(CommandKind.GROUP_FORBIDDEN_COMMAND, ())
        return ParsedCommand(CommandKind.GROUP_IGNORED, ())
    if not detection_text.startswith("/"):
        return _plain_text(detection_text)

    command, args = _parse_tokens(detection_text)
    if not command:
        return ParsedCommand(CommandKind.GROUP_FORBIDDEN_COMMAND, ())

    if command == "qa":
        if len(args) == 1 and args[0].lower() == "status":
            return ParsedCommand(CommandKind.GROUP_QA_STATUS, tuple(args[1:]))
        return ParsedCommand(CommandKind.GROUP_FORBIDDEN_COMMAND, tuple(args))

    if command in _GROUP_FORBIDDEN_COMMANDS:
        return ParsedCommand(CommandKind.GROUP_FORBIDDEN_COMMAND, tuple(args))

    return ParsedCommand(CommandKind.GROUP_FORBIDDEN_COMMAND, tuple(args))


def parse_command(text: str, conversation_type: ConversationType) -> ParsedCommand:
    stripped = text.strip()
    if not stripped:
        return _plain_text(stripped)

    if conversation_type == ConversationType.GROUP:
        return _parse_group(stripped)
    return _parse_private(stripped)
