from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional


class ConversationType(str, Enum):
    PRIVATE = "private"
    GROUP = "group"
    FEISHU_THREAD = "future_feishu_thread"


class SenderRole(str, Enum):
    OWNER = "owner"
    MEMBER = "member"
    UNKNOWN = "unknown"


def _normalize_tuple(value: Any, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError("%s must be a collection, not a string" % field_name)
    return tuple(value)


@dataclass(frozen=True)
class AttachmentRef:
    source_message_id: str
    local_path: str
    mime_type: str
    original_name: str
    direction: Literal["inbound", "outbound"]


@dataclass(frozen=True)
class IncomingMessage:
    platform: str
    conversation_type: ConversationType
    conversation_id: str
    thread_key: str
    sender_id: str
    sender_role: SenderRole
    text: str
    attachments: tuple[AttachmentRef, ...]
    raw_ref: Any

    def __post_init__(self) -> None:
        object.__setattr__(self, "attachments", _normalize_tuple(self.attachments, "attachments"))

    @property
    def context_key(self) -> tuple[str, str, str, str]:
        return (
            self.platform,
            self.conversation_type.value,
            self.conversation_id,
            self.thread_key,
        )


@dataclass(frozen=True)
class OutgoingMessage:
    conversation_id: str
    text: str
    attachments: tuple[AttachmentRef, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attachments", _normalize_tuple(self.attachments, "attachments"))


@dataclass(frozen=True)
class ExecutionPolicy:
    _ALLOWED_SANDBOXES = frozenset({"workspace-write", "read-only"})
    _ALLOWED_APPROVAL_POLICIES = frozenset({"on-request", "never"})

    sandbox: str
    approval_policy: str
    writable_roots: tuple[str, ...]
    model: Optional[str] = None
    effort: Optional[str] = None

    def __post_init__(self) -> None:
        if self.sandbox not in self._ALLOWED_SANDBOXES:
            raise ValueError("unsupported sandbox policy: %s" % self.sandbox)
        if self.approval_policy not in self._ALLOWED_APPROVAL_POLICIES:
            raise ValueError("unsupported approval policy: %s" % self.approval_policy)
        object.__setattr__(
            self,
            "writable_roots",
            _normalize_tuple(self.writable_roots, "writable_roots"),
        )

    @classmethod
    def work_default(cls, default_cwd: str) -> "ExecutionPolicy":
        return cls(
            sandbox="workspace-write",
            approval_policy="on-request",
            writable_roots=(default_cwd,),
        )

    @classmethod
    def group_qa(cls) -> "ExecutionPolicy":
        return cls(
            sandbox="read-only",
            approval_policy="never",
            writable_roots=(),
        )


@dataclass(frozen=True)
class ThreadAlias:
    alias: str
    session_id: str
    label: str
    default_cwd: str
    policy: ExecutionPolicy
