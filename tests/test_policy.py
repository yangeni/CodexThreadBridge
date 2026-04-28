from __future__ import annotations

from codex_thread_bridge.models import ConversationType, IncomingMessage, SenderRole
from codex_thread_bridge.policy import PolicyEngine


class FakeConversationType:
    def __init__(self, value: str) -> None:
        self.value = value


def test_owner_private_message_is_allowed(bridge_config) -> None:
    policy = PolicyEngine(bridge_config)
    msg = IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.PRIVATE,
        conversation_id="owner-chat",
        thread_key="owner-chat",
        sender_id="owner-1",
        sender_role=SenderRole.OWNER,
        text="/list",
        attachments=(),
        raw_ref="m-1",
    )
    assert policy.can_use_private_console(msg).allowed is True


def test_group_message_is_denied_for_private_console(bridge_config) -> None:
    policy = PolicyEngine(bridge_config)
    msg = IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.GROUP,
        conversation_id="group-chat",
        thread_key="group-chat",
        sender_id="owner-1",
        sender_role=SenderRole.OWNER,
        text="@Bot /list",
        attachments=(),
        raw_ref="m-1g",
    )
    decision = policy.can_use_private_console(msg)
    assert decision.allowed is False
    assert decision.reason == "private console only"


def test_non_owner_private_message_is_denied(bridge_config) -> None:
    policy = PolicyEngine(bridge_config)
    msg = IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.PRIVATE,
        conversation_id="member-chat",
        thread_key="member-chat",
        sender_id="member-1",
        sender_role=SenderRole.MEMBER,
        text="/list",
        attachments=(),
        raw_ref="m-2",
    )
    decision = policy.can_use_private_console(msg)
    assert decision.allowed is False
    assert decision.reason == "sender is not owner"


def test_raw_string_conversation_type_is_denied(bridge_config) -> None:
    policy = PolicyEngine(bridge_config)
    msg = IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.PRIVATE,
        conversation_id="string-chat",
        thread_key="string-chat",
        sender_id="owner-1",
        sender_role=SenderRole.OWNER,
        text="/list",
        attachments=(),
        raw_ref="m-4",
    )
    object.__setattr__(msg, "conversation_type", "private")
    decision = policy.can_use_private_console(msg)
    assert decision.allowed is False
    assert decision.reason == "private console only"


def test_fake_value_object_is_denied(bridge_config) -> None:
    policy = PolicyEngine(bridge_config)
    msg = IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.PRIVATE,
        conversation_id="fake-chat",
        thread_key="fake-chat",
        sender_id="owner-1",
        sender_role=SenderRole.OWNER,
        text="/list",
        attachments=(),
        raw_ref="m-5",
    )
    object.__setattr__(msg, "conversation_type", FakeConversationType("private"))
    decision = policy.can_use_private_console(msg)
    assert decision.allowed is False
    assert decision.reason == "private console only"


def test_group_cannot_dispatch_work_alias(bridge_config) -> None:
    policy = PolicyEngine(bridge_config)
    msg = IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.GROUP,
        conversation_id="group-1",
        thread_key="group-1",
        sender_id="owner-1",
        sender_role=SenderRole.OWNER,
        text="@Bot /send code run tests",
        attachments=(),
        raw_ref="m-3",
    )
    decision = policy.can_group_dispatch_work(msg)
    assert decision.allowed is False
    assert decision.reason == "group chat cannot dispatch work aliases"
