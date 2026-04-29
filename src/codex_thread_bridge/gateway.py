from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Optional

from codex_thread_bridge.artifacts import ArtifactService
from codex_thread_bridge.commands import CommandKind, parse_command
from codex_thread_bridge.models import (
    ConversationType,
    ExecutionPolicy,
    IncomingMessage,
    OutgoingMessage,
)
from codex_thread_bridge.policy import PolicyEngine
from codex_thread_bridge.refresh import read_new_items


class Gateway:
    def __init__(self, config, store, controller) -> None:
        self.config = config
        self.store = store
        self.controller = controller
        self.policy = PolicyEngine(config)
        self.artifacts = ArtifactService(config)

    def handle(self, msg: IncomingMessage) -> OutgoingMessage:
        if msg.conversation_type == ConversationType.GROUP:
            return self._handle_group(msg)

        decision = self.policy.can_use_private_console(msg)
        if not decision.allowed:
            return OutgoingMessage(msg.conversation_id, "Rejected: %s" % decision.reason)

        parsed = parse_command(msg.text, msg.conversation_type)

        if parsed.kind == CommandKind.ADD_ALIAS:
            alias, session_id = parsed.args
            return self._handle_add_alias(msg, alias, session_id)

        if parsed.kind == CommandKind.BIND_COMPAT:
            session_id = parsed.args[0]
            add_reply = self._handle_add_alias(msg, "default", session_id)
            if not add_reply.text.startswith("Added alias"):
                return add_reply
            self.store.set_active_alias(msg.context_key, "default", msg.sender_id)
            return OutgoingMessage(
                msg.conversation_id,
                "Bound default -> %s" % session_id,
            )

        if parsed.kind == CommandKind.USE_ALIAS:
            alias_name = parsed.args[0]
            alias = self.store.get_alias(alias_name)
            if alias is None:
                return OutgoingMessage(
                    msg.conversation_id,
                    "Unknown alias: %s" % alias_name,
                )
            self.store.set_active_alias(msg.context_key, alias_name, msg.sender_id)
            return OutgoingMessage(
                msg.conversation_id,
                "Using alias %s" % alias_name,
            )

        if parsed.kind == CommandKind.REMOVE_ALIAS:
            alias_name = parsed.args[0]
            if self.store.remove_alias(alias_name):
                return OutgoingMessage(
                    msg.conversation_id,
                    "Removed alias %s" % alias_name,
                )
            return OutgoingMessage(
                msg.conversation_id,
                "Unknown alias: %s" % alias_name,
            )

        if parsed.kind == CommandKind.LIST_ALIASES:
            aliases = self.store.list_aliases()
            if not aliases:
                return OutgoingMessage(msg.conversation_id, "No aliases.")
            lines = ["%s -> %s" % (alias.alias, alias.session_id) for alias in aliases]
            return OutgoingMessage(msg.conversation_id, "\n".join(lines))

        if parsed.kind == CommandKind.STATUS:
            return self._handle_status(msg, parsed.args)

        if parsed.kind == CommandKind.REFRESH:
            return self._handle_refresh(msg, parsed.args)

        if parsed.kind == CommandKind.RECOVER:
            return self._handle_recover(msg, parsed.args[0])

        if parsed.kind == CommandKind.SEND_ONCE:
            alias_name, text = parsed.args
            return self._dispatch_to_alias(msg, alias_name, text, active_context=False)

        if parsed.kind == CommandKind.PLAN_REVIEW:
            return self._dispatch_to_active_alias(
                msg,
                parsed.args[0],
                intent="plan_review",
            )

        if parsed.kind == CommandKind.ARTIFACTS:
            return self._handle_artifacts(msg, parsed.args)

        if parsed.kind == CommandKind.SEND_FILE:
            return self._handle_sendfile(msg, parsed.args[0])

        if parsed.kind == CommandKind.HELP:
            return OutgoingMessage(msg.conversation_id, _help_text())

        if parsed.kind == CommandKind.GROUP_APPROVE:
            return self._handle_group_approve(msg, parsed.args)

        if parsed.kind == CommandKind.GROUP_LIST:
            return self._handle_group_list(msg)

        if parsed.kind == CommandKind.GROUP_STATUS:
            return self._handle_group_status(msg, parsed.args[0])

        if parsed.kind == CommandKind.GROUP_RESET:
            return self._handle_group_reset(msg, parsed.args[0])

        if parsed.kind == CommandKind.GROUP_DISABLE:
            return self._handle_group_disable(msg, parsed.args[0])

        if parsed.kind == CommandKind.GROUP_PENDING:
            return self._handle_group_pending(msg)

        if parsed.kind == CommandKind.PLAIN_TEXT:
            return self._dispatch_to_active_alias(msg, parsed.args[0])

        return OutgoingMessage(
            msg.conversation_id,
            "Command recognized but not available in this task yet.",
        )

    def _dispatch_to_active_alias(
        self,
        msg: IncomingMessage,
        text: str,
        intent: str = "direct_message",
    ) -> OutgoingMessage:
        active_alias = self.store.get_active_alias(msg.context_key)
        if active_alias is None:
            return OutgoingMessage(
                msg.conversation_id,
                "No active thread. Use /use <alias> first.",
            )
        return self._dispatch_to_alias(
            msg,
            active_alias,
            text,
            active_context=True,
            intent=intent,
        )

    def _dispatch_to_alias(
        self,
        msg: IncomingMessage,
        alias_name: str,
        text: str,
        active_context: bool,
        intent: str = "direct_message",
    ) -> OutgoingMessage:
        alias = self.store.get_alias(alias_name)
        if alias is None:
            if active_context:
                return OutgoingMessage(
                    msg.conversation_id,
                    "Active alias no longer exists: %s" % alias_name,
                )
            return OutgoingMessage(
                msg.conversation_id,
                "Unknown alias: %s" % alias_name,
            )

        status = self.controller.status(alias.session_id)
        if (
            status.get("locked")
            or status.get("dirty")
            or status.get("reconcile_required")
        ):
            return OutgoingMessage(
                msg.conversation_id,
                "%s is not ready. Run /status %s." % (alias.alias, alias.alias),
            )

        run_started_at = time.time()
        result = self.controller.start_or_send(
            session_id=alias.session_id,
            cwd=alias.default_cwd,
            message=text,
            owner="ctb-private:%s" % msg.sender_id,
            policy=alias.policy,
            idempotency_key="%s:%s" % (msg.raw_ref, alias.alias),
            expected_session_head=None,
            intent=intent,
        )
        self.store.record_artifact_run(
            alias=alias.alias,
            run_id=result.run_id,
            session_id=result.session_id,
        )
        for candidate in self.artifacts.detect(result.text, run_started_at):
            self.store.record_artifact(
                run_id=result.run_id,
                alias=alias.alias,
                session_id=result.session_id,
                local_path=str(candidate.path),
                mime_type=candidate.mime_type,
                size_bytes=candidate.size_bytes,
                status=candidate.status,
                reason=candidate.reason,
            )
        if result.approval_summary:
            return OutgoingMessage(msg.conversation_id, result.approval_summary)
        return OutgoingMessage(msg.conversation_id, result.text)

    def _handle_recover(self, msg: IncomingMessage, alias_name: str) -> OutgoingMessage:
        alias = self.store.get_alias(alias_name)
        if alias is None:
            return OutgoingMessage(
                msg.conversation_id,
                "Unknown alias: %s" % alias_name,
            )
        result = self.controller.recover_session(
            session_id=alias.session_id,
            owner="ctb-private:%s" % msg.sender_id,
            human_authorized=True,
        )
        return OutgoingMessage(
            msg.conversation_id,
            "Recovered %s: released=%s acked=%s cancelled=%s closed=%s"
            % (
                alias.alias,
                bool(result.get("released")),
                len(result.get("acked_runs") or []),
                len(result.get("cancelled_runs") or []),
                len(result.get("closed_runs") or []),
            ),
        )

    def _handle_add_alias(
        self,
        msg: IncomingMessage,
        alias: str,
        session_id: str,
    ) -> OutgoingMessage:
        status = self.controller.status(session_id)
        cwd = self._workspace_from_status(status) or self._workspace_from_session_meta(
            session_id
        )
        if cwd is None:
            return OutgoingMessage(
                msg.conversation_id,
                "Alias cannot be added because the session workspace/cwd is unknown.",
            )
        policy = ExecutionPolicy.work_default(cwd)
        self.store.upsert_alias(
            alias=alias,
            session_id=session_id,
            label=alias,
            default_cwd=cwd,
            policy=policy,
            created_by=msg.sender_id,
        )
        return OutgoingMessage(
            msg.conversation_id,
            "Added alias %s -> %s" % (alias, session_id),
        )

    def _handle_group(self, msg: IncomingMessage) -> OutgoingMessage:
        parsed = parse_command(msg.text, msg.conversation_type)
        if parsed.kind == CommandKind.GROUP_IGNORED:
            return OutgoingMessage(msg.conversation_id, "")
        group = self.store.get_group_by_id(msg.conversation_id)
        if group is None:
            self.store.record_pending_group(msg.conversation_id, msg.conversation_id, "system")
            return OutgoingMessage(
                msg.conversation_id,
                "This group is not enabled. Ask the owner to approve it in private chat.",
            )
        if group.get("status") != "active" or not group.get("qa_session_id"):
            return OutgoingMessage(
                msg.conversation_id,
                "This group is not enabled. Ask the owner to approve it in private chat.",
            )

        group_alias = str(group["group_alias"])
        if parsed.kind == CommandKind.GROUP_FORBIDDEN_COMMAND:
            return OutgoingMessage(
                msg.conversation_id,
                "Group chat cannot dispatch work aliases.",
            )
        if parsed.kind == CommandKind.GROUP_QA_STATUS:
            return OutgoingMessage(
                msg.conversation_id,
                "QA enabled: %s" % group_alias,
            )

        qa_session_id = str(group["qa_session_id"])
        status = self.controller.status(qa_session_id)
        if (
            status.get("locked")
            or status.get("dirty")
            or status.get("reconcile_required")
        ):
            return OutgoingMessage(
                msg.conversation_id,
                "%s is not ready. Ask the owner to reconcile the QA session."
                % group_alias,
            )

        text = self._strip_group_leading_mention(msg.text)
        result = self.controller.start_or_send(
            session_id=qa_session_id,
            cwd=str(self.config.group_qa_cwd),
            message=text,
            owner="ctb-group-qa:%s" % group_alias,
            policy=ExecutionPolicy.group_qa(),
            idempotency_key="%s:%s" % (msg.raw_ref, group_alias),
            expected_session_head=None,
            intent="direct_message",
        )
        if result.approval_summary:
            return OutgoingMessage(msg.conversation_id, result.approval_summary)
        return OutgoingMessage(msg.conversation_id, result.text)

    def _handle_group_approve(
        self, msg: IncomingMessage, args: tuple[str, ...]
    ) -> OutgoingMessage:
        group_ref = args[0]
        existing = self._find_group(group_ref)
        group_id = group_ref
        if existing is not None:
            group_id = str(existing["group_id"])
        if len(args) > 1:
            group_alias = args[1]
        elif existing is not None:
            group_alias = str(existing["group_alias"])
        else:
            group_alias = group_ref

        alias_owner = self.store.get_group_by_alias(group_alias)
        if alias_owner is not None and str(alias_owner["group_id"]) != group_id:
            return OutgoingMessage(
                msg.conversation_id,
                "Group alias already exists: %s" % group_alias,
            )

        self.store.record_pending_group(group_id, group_alias, msg.sender_id)
        result = self.controller.start_or_send(
            session_id=None,
            cwd=str(self.config.group_qa_cwd),
            message=self._group_qa_seed_text(group_alias),
            owner="ctb-group-qa:%s" % group_alias,
            policy=ExecutionPolicy.group_qa(),
            idempotency_key="group-approve:%s" % group_alias,
            expected_session_head=None,
            intent="direct_message",
        )
        self.store.activate_group(group_alias, result.session_id)
        return OutgoingMessage(
            msg.conversation_id,
            "Approved group QA for %s." % group_alias,
        )

    def _handle_group_list(self, msg: IncomingMessage) -> OutgoingMessage:
        groups = self.store.list_groups()
        if not groups:
            return OutgoingMessage(msg.conversation_id, "No groups.")
        lines = []
        for group in groups:
            lines.append(
                "%s (%s): status=%s qa_session_id=%s"
                % (
                    group["group_alias"],
                    group["group_id"],
                    group["status"],
                    group["qa_session_id"],
                )
            )
        return OutgoingMessage(msg.conversation_id, "\n".join(lines))

    def _handle_group_status(self, msg: IncomingMessage, group_ref: str) -> OutgoingMessage:
        group = self._find_group(group_ref)
        if group is None:
            return OutgoingMessage(
                msg.conversation_id,
                "Unknown group: %s" % group_ref,
            )
        return OutgoingMessage(
            msg.conversation_id,
            "%s (%s): status=%s qa_session_id=%s"
            % (
                group["group_alias"],
                group["group_id"],
                group["status"],
                group["qa_session_id"],
            ),
        )

    def _handle_group_reset(self, msg: IncomingMessage, group_ref: str) -> OutgoingMessage:
        group = self._find_group(group_ref)
        if group is None:
            return OutgoingMessage(
                msg.conversation_id,
                "Unknown group: %s" % group_ref,
            )
        self.store.record_pending_group(
            str(group["group_id"]),
            str(group["group_alias"]),
            msg.sender_id,
        )
        return OutgoingMessage(
            msg.conversation_id,
            "Reset group %s to pending." % group["group_alias"],
        )

    def _handle_group_disable(self, msg: IncomingMessage, group_ref: str) -> OutgoingMessage:
        group = self._find_group(group_ref)
        if group is None:
            return OutgoingMessage(
                msg.conversation_id,
                "Unknown group: %s" % group_ref,
            )
        self.store.disable_group(str(group["group_alias"]))
        return OutgoingMessage(
            msg.conversation_id,
            "Disabled group %s." % group["group_alias"],
        )

    def _handle_group_pending(self, msg: IncomingMessage) -> OutgoingMessage:
        pending_groups = [
            group
            for group in self.store.list_groups()
            if str(group.get("status")) == "pending"
        ]
        if not pending_groups:
            return OutgoingMessage(msg.conversation_id, "No pending groups.")
        lines = []
        for group in pending_groups:
            lines.append("%s (%s)" % (group["group_alias"], group["group_id"]))
        return OutgoingMessage(msg.conversation_id, "\n".join(lines))

    def _find_group(self, group_ref: str) -> Optional[dict]:
        group = self.store.get_group_by_alias(group_ref)
        if group is not None:
            return group
        return self.store.get_group_by_id(group_ref)

    def _group_qa_seed_text(self, group_alias: str) -> str:
        return (
            "You are the isolated QA session for WeChat group '%s'. "
            "Answer questions about the current project in read-only mode only."
            % group_alias
        )

    def _strip_group_leading_mention(self, text: str) -> str:
        stripped = text.lstrip()
        if not stripped.startswith("@Bot"):
            return stripped
        parts = stripped.split(None, 1)
        if not parts:
            return stripped
        if parts[0] != "@Bot":
            return stripped
        if len(parts) == 1:
            return ""
        return parts[1].lstrip()

    def _handle_status(self, msg: IncomingMessage, args: tuple[str, ...]) -> OutgoingMessage:
        if args:
            alias_name = args[0]
        else:
            alias_name = self.store.get_active_alias(msg.context_key)
            if alias_name is None:
                return OutgoingMessage(
                    msg.conversation_id,
                    "No active thread. Use /use <alias> first.",
                )

        alias = self.store.get_alias(alias_name)
        if alias is None:
            return OutgoingMessage(
                msg.conversation_id,
                "Unknown alias: %s" % alias_name,
            )

        status = self.controller.status(alias.session_id)
        text = (
            "%s: locked=%s dirty=%s reconcile_required=%s session_head=%s"
            % (
                alias.alias,
                bool(status.get("locked")),
                bool(status.get("dirty")),
                bool(status.get("reconcile_required")),
                status.get("session_head"),
            )
        )
        return OutgoingMessage(msg.conversation_id, text)

    def _handle_refresh(
        self,
        msg: IncomingMessage,
        args: tuple[str, ...],
    ) -> OutgoingMessage:
        if args:
            alias_name = args[0]
        else:
            alias_name = self.store.get_active_alias(msg.context_key)
            if alias_name is None:
                return OutgoingMessage(
                    msg.conversation_id,
                    "No active thread. Use /use <alias> first.",
                )

        alias = self.store.get_alias(alias_name)
        if alias is None:
            return OutgoingMessage(
                msg.conversation_id,
                "Unknown alias: %s" % alias_name,
            )

        history_path = self._session_history_path(alias.session_id)
        if history_path is None:
            return OutgoingMessage(
                msg.conversation_id,
                "Unable to read local session history for %s." % alias.alias,
            )

        last_seen_line = self.store.get_refresh_offset(alias.alias)
        try:
            result = read_new_items(history_path, last_seen_line)
        except OSError:
            return OutgoingMessage(
                msg.conversation_id,
                "Unable to read local session history for %s." % alias.alias,
            )

        if not result.source_truncated:
            self.store.set_refresh_offset(alias.alias, result.next_line)
        return OutgoingMessage(msg.conversation_id, result.summary)

    def _handle_artifacts(
        self, msg: IncomingMessage, args: tuple[str, ...]
    ) -> OutgoingMessage:
        alias_name = self._artifact_alias_from_args(msg, args)
        if alias_name is None:
            return OutgoingMessage(
                msg.conversation_id,
                "No active thread. Use /use <alias> first.",
            )
        alias = self.store.get_alias(alias_name)
        if alias is None:
            return OutgoingMessage(
                msg.conversation_id,
                "Unknown alias: %s" % alias_name,
            )
        artifacts = self._latest_run_artifacts(alias_name, alias.session_id)
        if not artifacts:
            return OutgoingMessage(msg.conversation_id, "No artifacts.")
        lines = []
        for artifact in artifacts:
            lines.append(
                "%s %s %s"
                % (
                    artifact["id"],
                    artifact["status"],
                    artifact["local_path"],
                )
            )
        return OutgoingMessage(msg.conversation_id, "\n".join(lines))

    def _handle_sendfile(self, msg: IncomingMessage, artifact_id: str) -> OutgoingMessage:
        alias_name = self._artifact_alias_from_args(msg, ())
        if alias_name is None:
            return OutgoingMessage(
                msg.conversation_id,
                "No active thread. Use /use <alias> first.",
            )
        alias = self.store.get_alias(alias_name)
        if alias is None:
            return OutgoingMessage(
                msg.conversation_id,
                "Active alias no longer exists: %s" % alias_name,
            )
        latest_artifacts = self._latest_run_artifacts(alias_name, alias.session_id)
        if artifact_id == "all":
            allowed_artifacts = [
                artifact
                for artifact in latest_artifacts
                if str(artifact.get("status")) == "allowed"
            ]
            if not allowed_artifacts:
                return OutgoingMessage(
                    msg.conversation_id,
                    "Rejected: latest run has no allowed artifacts.",
                )
            lines = [
                "Would send artifact %s: %s"
                % (artifact["id"], artifact["local_path"])
                for artifact in allowed_artifacts
            ]
            return OutgoingMessage(msg.conversation_id, "\n".join(lines))
        artifact = self._find_artifact(latest_artifacts, artifact_id)
        if artifact is None:
            return OutgoingMessage(
                msg.conversation_id,
                "Rejected: artifact not found: %s" % artifact_id,
            )
        if str(artifact["status"]) != "allowed":
            return OutgoingMessage(
                msg.conversation_id,
                "Rejected: artifact %s is %s (%s)"
                % (artifact["id"], artifact["status"], artifact["reason"]),
            )
        return OutgoingMessage(
            msg.conversation_id,
            "Would send artifact %s: %s"
            % (artifact["id"], artifact["local_path"]),
        )

    def _find_artifact(self, artifacts: list[dict], artifact_id: str) -> Optional[dict]:
        for artifact in artifacts:
            if str(artifact.get("id")) == artifact_id:
                return artifact
        return None

    def _latest_run_artifacts(self, alias_name: str, session_id: str) -> list[dict]:
        artifacts = [
            artifact
            for artifact in self.store.list_artifacts(alias_name)
            if str(artifact.get("session_id")) == str(session_id)
        ]
        latest_run_id = self.store.get_latest_artifact_run(alias_name)
        if latest_run_id is None:
            if not artifacts:
                return []
            latest_run_id = str(artifacts[-1]["run_id"])
        return [
            artifact
            for artifact in artifacts
            if str(artifact.get("run_id")) == str(latest_run_id)
        ]

    def _artifact_alias_from_args(
        self, msg: IncomingMessage, args: tuple[str, ...]
    ) -> Optional[str]:
        if args:
            return args[0]
        return self.store.get_active_alias(msg.context_key)

    def _workspace_from_status(self, status: dict) -> Optional[str]:
        for key in ("cwd", "workspace_root", "project_root", "default_cwd"):
            value = status.get(key)
            if value:
                return str(value)
        return None

    def _workspace_from_session_meta(self, session_id: str) -> Optional[str]:
        history_path = self._session_history_path(session_id)
        if history_path is None:
            return None
        try:
            with history_path.open(encoding="utf-8") as handle:
                for _index in range(50):
                    line = handle.readline()
                    if not line:
                        return None
                    try:
                        payload = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if payload.get("type") != "session_meta":
                        continue
                    meta = payload.get("payload")
                    if not isinstance(meta, dict):
                        continue
                    cwd = meta.get("cwd")
                    if isinstance(cwd, str) and cwd.strip():
                        return cwd
        except OSError:
            return None
        return None

    def _session_history_path(self, session_id: str) -> Optional[Path]:
        local_path = self.config.data_dir / "sessions" / ("%s.jsonl" % session_id)
        if local_path.exists():
            return local_path

        codex_sessions = Path.home() / ".codex" / "sessions"
        if not codex_sessions.exists():
            return None

        matches = [
            path
            for path in codex_sessions.glob("**/*.jsonl")
            if path.name == "%s.jsonl" % session_id
            or path.name.endswith("-%s.jsonl" % session_id)
        ]
        if not matches:
            return None
        return max(matches, key=lambda path: path.stat().st_mtime)


def _help_text() -> str:
    return "\n".join(
        [
            "/add <alias> <session_id>",
            "/bind <session_id>",
            "/use <alias>",
            "/list",
            "/remove <alias>",
            "/status [alias]",
            "/refresh [alias]",
            "/recover <alias>",
            "/plan <message>",
            "/send <alias> <message>",
            "/artifacts [alias]",
            "/sendfile <artifact_id|all>",
            "/group approve <group> [alias]",
            "/group list",
            "/group status <group|alias>",
            "/group reset <group|alias>",
            "/group disable <group|alias>",
        ]
    )
