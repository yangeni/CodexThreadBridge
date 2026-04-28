from __future__ import annotations

from typing import Optional

from codex_thread_bridge.commands import CommandKind, parse_command
from codex_thread_bridge.models import (
    ConversationType,
    ExecutionPolicy,
    IncomingMessage,
    OutgoingMessage,
)
from codex_thread_bridge.policy import PolicyEngine


class Gateway:
    def __init__(self, config, store, controller) -> None:
        self.config = config
        self.store = store
        self.controller = controller
        self.policy = PolicyEngine(config)

    def handle(self, msg: IncomingMessage) -> OutgoingMessage:
        if msg.conversation_type == ConversationType.GROUP:
            return self._handle_group(msg)

        decision = self.policy.can_use_private_console(msg)
        if not decision.allowed:
            return OutgoingMessage(msg.conversation_id, "Rejected: %s" % decision.reason)

        parsed = parse_command(msg.text, msg.conversation_type)

        if parsed.kind == CommandKind.ADD_ALIAS:
            alias, session_id = parsed.args
            status = self.controller.status(session_id)
            cwd = self._workspace_from_status(status)
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

        if parsed.kind == CommandKind.LIST_ALIASES:
            aliases = self.store.list_aliases()
            if not aliases:
                return OutgoingMessage(msg.conversation_id, "No aliases.")
            lines = ["%s -> %s" % (alias.alias, alias.session_id) for alias in aliases]
            return OutgoingMessage(msg.conversation_id, "\n".join(lines))

        if parsed.kind == CommandKind.STATUS:
            return self._handle_status(msg, parsed.args)

        if parsed.kind == CommandKind.PLAIN_TEXT:
            return self._dispatch_to_active_alias(msg, parsed.args[0])

        return OutgoingMessage(
            msg.conversation_id,
            "Command recognized but not available in this task yet.",
        )

    def _dispatch_to_active_alias(
        self, msg: IncomingMessage, text: str
    ) -> OutgoingMessage:
        active_alias = self.store.get_active_alias(msg.context_key)
        if active_alias is None:
            return OutgoingMessage(
                msg.conversation_id,
                "No active thread. Use /use <alias> first.",
            )

        alias = self.store.get_alias(active_alias)
        if alias is None:
            return OutgoingMessage(
                msg.conversation_id,
                "Active alias no longer exists: %s" % active_alias,
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

        result = self.controller.start_or_send(
            session_id=alias.session_id,
            cwd=alias.default_cwd,
            message=text,
            owner="ctb-private:%s" % msg.sender_id,
            policy=alias.policy,
            idempotency_key="%s:%s" % (msg.raw_ref, alias.alias),
            expected_session_head=status.get("session_head"),
        )
        if result.approval_summary:
            return OutgoingMessage(msg.conversation_id, result.approval_summary)
        return OutgoingMessage(msg.conversation_id, result.text)

    def _handle_group(self, msg: IncomingMessage) -> OutgoingMessage:
        return OutgoingMessage(
            msg.conversation_id,
            "Group handling is added in a later task.",
        )

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

    def _workspace_from_status(self, status: dict) -> Optional[str]:
        for key in ("cwd", "workspace_root", "project_root", "default_cwd"):
            value = status.get(key)
            if value:
                return str(value)
        return None
