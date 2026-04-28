from __future__ import annotations

from pathlib import Path

from codex_thread_bridge.config import BridgeConfig
from codex_thread_bridge.gateway import Gateway
from codex_thread_bridge.models import ConversationType, IncomingMessage, SenderRole
from codex_thread_bridge.stores import BridgeStore

from tests.test_gateway_private import FakeControllerClient


def group_msg(
    text: str,
    raw_ref: str = "m-group",
    sender_id: str = "member-1",
    conversation_id: str = "group-1",
) -> IncomingMessage:
    return IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.GROUP,
        conversation_id=conversation_id,
        thread_key=conversation_id,
        sender_id=sender_id,
        sender_role=SenderRole.MEMBER,
        text=text,
        attachments=(),
        raw_ref=raw_ref,
    )


def owner_private(text: str, raw_ref: str = "m-owner") -> IncomingMessage:
    return IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.PRIVATE,
        conversation_id="owner-chat",
        thread_key="owner-chat",
        sender_id="owner-1",
        sender_role=SenderRole.OWNER,
        text=text,
        attachments=(),
        raw_ref=raw_ref,
    )


def _gateway_for(tmp_path: Path) -> tuple[Gateway, BridgeConfig, BridgeStore, FakeControllerClient]:
    config = BridgeConfig.local_dev(tmp_path, {"owner-1"})
    store = BridgeStore(config.sqlite_path)
    store.initialize()
    controller = FakeControllerClient()
    return Gateway(config, store, controller), config, store, controller


def test_unapproved_group_records_pending_without_codex_call(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)

    reply = gateway.handle(group_msg("@Bot hello"))
    pending = store.get_group_by_id("group-1")

    assert "not enabled" in reply.text
    assert pending is not None
    assert pending["status"] == "pending"
    assert controller.starts == []


def test_unapproved_group_chatter_is_ignored_without_pending_record(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)

    reply = gateway.handle(group_msg("hello everyone"))

    assert reply.text == ""
    assert store.get_group_by_id("group-1") is None
    assert controller.starts == []


def test_unapproved_group_forbidden_command_still_records_pending(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)

    reply = gateway.handle(group_msg("@Bot /send code run tests"))
    pending = store.get_group_by_id("group-1")

    assert "not enabled" in reply.text
    assert pending is not None
    assert pending["group_alias"] == "group-1"
    assert pending["status"] == "pending"
    assert controller.starts == []


def test_owner_approves_group_and_group_uses_read_only_qa_session(tmp_path: Path) -> None:
    gateway, config, store, controller = _gateway_for(tmp_path)

    gateway.handle(group_msg("@Bot hello", raw_ref="m-group-pending"))

    approve_reply = gateway.handle(
        owner_private("/group approve group-1 friends", raw_ref="m-owner-approve")
    )
    group_reply = gateway.handle(group_msg("@Bot what is this project?", raw_ref="m-group-qa"))
    group_record = store.get_group_by_alias("friends")

    assert "friends" in approve_reply.text
    assert group_reply.text == "done"
    assert group_record is not None
    assert group_record["status"] == "active"
    assert group_record["qa_session_id"] == "created-session-1"
    assert len(controller.starts) == 2
    approval_call = controller.starts[0]
    dispatch_call = controller.starts[1]
    assert approval_call["session_id"] is None
    assert approval_call["cwd"] == str(config.group_qa_cwd)
    assert approval_call["owner"] == "ctb-group-qa:friends"
    assert approval_call["policy"].sandbox == "read-only"
    assert approval_call["policy"].approval_policy == "never"
    assert dispatch_call["session_id"] == "created-session-1"
    assert dispatch_call["cwd"] == str(config.group_qa_cwd)
    assert dispatch_call["message"] == "what is this project?"
    assert dispatch_call["owner"] == "ctb-group-qa:friends"
    assert dispatch_call["policy"].sandbox == "read-only"
    assert dispatch_call["policy"].approval_policy == "never"
    assert dispatch_call["expected_session_head"] == "head-1"


def test_group_qa_not_ready_prevents_dispatch(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)

    gateway.handle(group_msg("@Bot hello", raw_ref="m-group-pending"))
    gateway.handle(owner_private("/group approve group-1 friends", raw_ref="m-owner-approve"))
    controller.status_by_session["created-session-1"] = {
        "session_id": "created-session-1",
        "locked": True,
        "dirty": False,
        "reconcile_required": False,
        "session_head": "head-9",
    }

    reply = gateway.handle(group_msg("@Bot what is this project?", raw_ref="m-group-qa"))

    assert "not ready" in reply.text
    assert len(controller.starts) == 1


def test_group_qa_dirty_or_reconcile_required_prevents_dispatch(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)

    gateway.handle(group_msg("@Bot hello", raw_ref="m-group-pending"))
    gateway.handle(owner_private("/group approve group-1 friends", raw_ref="m-owner-approve"))
    controller.status_by_session["created-session-1"] = {
        "session_id": "created-session-1",
        "locked": False,
        "dirty": True,
        "reconcile_required": True,
        "session_head": "head-10",
    }

    reply = gateway.handle(group_msg("@Bot what changed?", raw_ref="m-group-qa"))

    assert "not ready" in reply.text
    assert len(controller.starts) == 1


def test_group_qa_ready_forwards_non_default_session_head(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)

    gateway.handle(group_msg("@Bot hello", raw_ref="m-group-pending"))
    gateway.handle(owner_private("/group approve group-1 friends", raw_ref="m-owner-approve"))
    controller.status_by_session["created-session-1"] = {
        "session_id": "created-session-1",
        "locked": False,
        "dirty": False,
        "reconcile_required": False,
        "session_head": "group-head-9",
    }

    reply = gateway.handle(group_msg("@Bot what is this project?", raw_ref="m-group-qa"))

    assert reply.text == "done"
    assert controller.starts[-1]["expected_session_head"] == "group-head-9"


def test_group_work_command_is_forbidden_even_after_approval(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)

    gateway.handle(group_msg("@Bot hello", raw_ref="m-group-pending"))
    gateway.handle(owner_private("/group approve group-1 friends", raw_ref="m-owner-approve"))

    reply = gateway.handle(group_msg("@Bot /send code run tests", raw_ref="m-group-send"))

    assert "cannot dispatch" in reply.text
    assert len(controller.starts) == 1


def test_approved_group_chatter_is_ignored_without_controller_call(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)

    gateway.handle(group_msg("@Bot hello", raw_ref="m-group-pending"))
    gateway.handle(owner_private("/group approve group-1 friends", raw_ref="m-owner-approve"))

    reply = gateway.handle(group_msg("hello everyone", raw_ref="m-group-chatter"))

    assert reply.text == ""
    assert len(controller.starts) == 1


def test_group_list_status_disable_and_group_qa_status(tmp_path: Path) -> None:
    gateway, _config, _store, controller = _gateway_for(tmp_path)

    gateway.handle(group_msg("@Bot hello", raw_ref="m-group-pending"))
    gateway.handle(owner_private("/group approve group-1 friends", raw_ref="m-owner-approve"))

    list_reply = gateway.handle(owner_private("/group list", raw_ref="m-owner-list"))
    status_reply = gateway.handle(owner_private("/group status friends", raw_ref="m-owner-status"))
    qa_status_reply = gateway.handle(group_msg("@Bot /qa status", raw_ref="m-group-status"))
    disable_reply = gateway.handle(owner_private("/group disable friends", raw_ref="m-owner-disable"))
    disabled_group_reply = gateway.handle(group_msg("@Bot what now?", raw_ref="m-group-disabled"))

    assert "friends" in list_reply.text
    assert "active" in status_reply.text
    assert "created-session-1" in status_reply.text
    assert qa_status_reply.text == "QA enabled: friends"
    assert "friends" in disable_reply.text
    assert "not enabled" in disabled_group_reply.text
    assert len(controller.starts) == 1


def test_disabled_group_keeps_alias_when_group_speaks_again(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)

    gateway.handle(group_msg("@Bot hello", raw_ref="m-group-pending"))
    gateway.handle(owner_private("/group approve group-1 friends", raw_ref="m-owner-approve"))
    gateway.handle(owner_private("/group disable friends", raw_ref="m-owner-disable"))

    disabled_reply = gateway.handle(group_msg("@Bot hello again", raw_ref="m-group-disabled"))
    status_reply = gateway.handle(owner_private("/group status friends", raw_ref="m-owner-status"))
    group_record = store.get_group_by_id("group-1")

    assert "not enabled" in disabled_reply.text
    assert "friends" in status_reply.text
    assert "disabled" in status_reply.text
    assert group_record is not None
    assert group_record["group_alias"] == "friends"
    assert group_record["status"] == "disabled"
    assert len(controller.starts) == 1


def test_reapprove_by_group_id_without_alias_keeps_existing_alias(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)

    gateway.handle(group_msg("@Bot hello", raw_ref="m-group-pending"))
    gateway.handle(owner_private("/group approve group-1 friends", raw_ref="m-owner-approve-1"))
    gateway.handle(owner_private("/group disable friends", raw_ref="m-owner-disable"))

    reapprove_reply = gateway.handle(
        owner_private("/group approve group-1", raw_ref="m-owner-approve-2")
    )
    status_reply = gateway.handle(owner_private("/group status friends", raw_ref="m-owner-status"))
    group_record = store.get_group_by_id("group-1")

    assert "friends" in reapprove_reply.text
    assert "friends" in status_reply.text
    assert "active" in status_reply.text
    assert group_record is not None
    assert group_record["group_alias"] == "friends"
    assert group_record["status"] == "active"
    assert len(controller.starts) == 2


def test_reapprove_with_explicit_alias_overrides_existing_alias(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)

    gateway.handle(group_msg("@Bot hello", raw_ref="m-group-pending"))
    gateway.handle(owner_private("/group approve group-1 friends", raw_ref="m-owner-approve-1"))

    reapprove_reply = gateway.handle(
        owner_private("/group approve group-1 renamed", raw_ref="m-owner-approve-2")
    )
    renamed_status_reply = gateway.handle(
        owner_private("/group status renamed", raw_ref="m-owner-status-renamed")
    )
    group_record = store.get_group_by_id("group-1")

    assert "renamed" in reapprove_reply.text
    assert "renamed" in renamed_status_reply.text
    assert group_record is not None
    assert group_record["group_alias"] == "renamed"
    assert group_record["status"] == "active"
    assert len(controller.starts) == 2


def test_reapprove_same_group_with_same_explicit_alias_succeeds(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)

    gateway.handle(group_msg("@Bot hello", raw_ref="m-group-pending"))
    first_reply = gateway.handle(
        owner_private("/group approve group-1 friends", raw_ref="m-owner-approve-1")
    )
    second_reply = gateway.handle(
        owner_private("/group approve group-1 friends", raw_ref="m-owner-approve-2")
    )
    status_reply = gateway.handle(owner_private("/group status friends", raw_ref="m-owner-status"))
    group_record = store.get_group_by_id("group-1")

    assert "friends" in first_reply.text
    assert "friends" in second_reply.text
    assert "already exists" not in second_reply.text
    assert "friends" in status_reply.text
    assert group_record is not None
    assert group_record["group_alias"] == "friends"
    assert group_record["status"] == "active"
    assert len(controller.starts) == 2


def test_group_alias_collision_returns_controlled_reply(tmp_path: Path) -> None:
    gateway, _config, store, controller = _gateway_for(tmp_path)

    gateway.handle(group_msg("@Bot hello", raw_ref="m-group-1-pending", conversation_id="group-1"))
    gateway.handle(owner_private("/group approve group-1 friends", raw_ref="m-owner-approve-1"))
    collision_reply = gateway.handle(
        owner_private("/group approve group-2 friends", raw_ref="m-owner-approve-2")
    )

    assert "already exists" in collision_reply.text
    assert store.get_group_by_id("group-2") is None
    assert len(controller.starts) == 1
