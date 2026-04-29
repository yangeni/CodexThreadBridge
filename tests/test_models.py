from __future__ import annotations

import configparser
from pathlib import Path
from typing import Optional, get_type_hints

import pytest

from codex_thread_bridge import __version__
from codex_thread_bridge.config import BridgeConfig
from codex_thread_bridge.models import (
    AttachmentDescriptor,
    AttachmentRef,
    ConversationType,
    ExecutionPolicy,
    IncomingMessage,
    OutgoingMessage,
    SenderRole,
)


def test_package_version_available() -> None:
    assert __version__ == "0.4.0"


def test_setup_cfg_version_matches_runtime_version() -> None:
    parser = configparser.ConfigParser()
    parser.read(Path(__file__).resolve().parents[1] / "setup.cfg")

    assert parser["metadata"]["version"] == __version__


def test_bridge_config_local_dev_derives_paths(tmp_path: Path) -> None:
    config = BridgeConfig.local_dev(tmp_path, {"owner-1"})

    assert config.project_root == tmp_path
    assert config.data_dir == tmp_path / "data"
    assert config.sqlite_path == tmp_path / "data" / "bridge.sqlite3"
    assert config.attachments_dir == tmp_path / "data" / "attachments"
    assert config.artifact_roots == (tmp_path / "exports",)
    assert config.owner_user_ids == frozenset({"owner-1"})
    assert config.group_qa_cwd == tmp_path
    assert config.max_artifact_bytes == 25 * 1024 * 1024
    assert ".ssh" in config.sensitive_path_markers


def test_bridge_config_type_hints_resolve_on_supported_python() -> None:
    hints = get_type_hints(BridgeConfig)

    assert hints["default_group_model"] == Optional[str]


def test_incoming_message_context_key_includes_bridge_identity() -> None:
    message = IncomingMessage(
        platform="wecom",
        conversation_type=ConversationType.PRIVATE,
        conversation_id="conversation-1",
        thread_key="thread-1",
        sender_id="user-1",
        sender_role=SenderRole.OWNER,
        text="hello",
        attachments=(),
        raw_ref={"id": "raw-1"},
    )

    assert message.context_key == ("wecom", "private", "conversation-1", "thread-1")


def test_execution_policy_work_default_uses_codex_compatible_workspace_write() -> None:
    policy = ExecutionPolicy.work_default(default_cwd="/tmp/work")

    assert policy.sandbox == "workspace-write"
    assert policy.approval_policy == "on-request"
    assert policy.writable_roots == ("/tmp/work",)


def test_attachment_ref_records_inbound_direction() -> None:
    attachment = AttachmentRef(
        source_message_id="msg-1",
        local_path="/tmp/file.txt",
        mime_type="text/plain",
        original_name="file.txt",
        direction="inbound",
    )

    assert attachment.direction == "inbound"


def test_attachment_descriptor_copies_descriptor_payload() -> None:
    descriptor = {"id": "att-1"}
    attachment = AttachmentDescriptor(
        source_message_id="msg-1",
        descriptor=descriptor,
        mime_type="image/png",
        original_name="image.png",
    )

    descriptor["id"] = "mutated"

    assert dict(attachment.descriptor) == {"id": "att-1"}
    assert isinstance(attachment.descriptor, dict)
    assert attachment.direction == "inbound"


def test_tuple_fields_are_normalized_and_isolated_from_external_mutation() -> None:
    attachments = [
        AttachmentRef(
            source_message_id="msg-1",
            local_path="/tmp/file.txt",
            mime_type="text/plain",
            original_name="file.txt",
            direction="inbound",
        )
    ]
    roots = ["/tmp/work"]

    incoming = IncomingMessage(
        platform="wecom",
        conversation_type=ConversationType.GROUP,
        conversation_id="conversation-2",
        thread_key="thread-2",
        sender_id="user-2",
        sender_role=SenderRole.MEMBER,
        text="hello",
        attachments=attachments,
        raw_ref={},
    )
    outgoing = OutgoingMessage(
        conversation_id="conversation-3",
        text="reply",
        attachments=attachments,
    )
    policy = ExecutionPolicy(
        sandbox="workspace-write",
        approval_policy="on-request",
        writable_roots=roots,
    )

    assert isinstance(incoming.attachments, tuple)
    assert isinstance(outgoing.attachments, tuple)
    assert isinstance(policy.writable_roots, tuple)

    attachments.append(
        AttachmentRef(
            source_message_id="msg-2",
            local_path="/tmp/other.txt",
            mime_type="text/plain",
            original_name="other.txt",
            direction="outbound",
        )
    )
    roots.append("/tmp/other")

    assert len(incoming.attachments) == 1
    assert len(outgoing.attachments) == 1
    assert policy.writable_roots == ("/tmp/work",)


def test_execution_policy_group_qa_returns_read_only_never() -> None:
    policy = ExecutionPolicy.group_qa()

    assert policy.sandbox == "read-only"
    assert policy.approval_policy == "never"
    assert policy.writable_roots == ()


@pytest.mark.parametrize(
    ("sandbox", "approval_policy"),
    [
        ("workspace-read", "on-request"),
        ("workspace-write", "ask"),
    ],
)
def test_execution_policy_rejects_invalid_raw_strings(
    sandbox: str, approval_policy: str
) -> None:
    with pytest.raises(ValueError):
        ExecutionPolicy(
            sandbox=sandbox,
            approval_policy=approval_policy,
            writable_roots=(),
        )


def test_execution_policy_rejects_bare_string_writable_roots() -> None:
    with pytest.raises(ValueError):
        ExecutionPolicy(
            sandbox="workspace-write",
            approval_policy="on-request",
            writable_roots="/tmp/work",
        )
