from __future__ import annotations

from dataclasses import dataclass

from codex_thread_bridge.models import ConversationType

@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""


class PolicyEngine:
    def __init__(self, config):
        self.config = config

    def can_use_private_console(self, msg):
        if not isinstance(msg.conversation_type, ConversationType):
            return PolicyDecision(False, "private console only")
        if msg.conversation_type != ConversationType.PRIVATE:
            return PolicyDecision(False, "private console only")
        if msg.sender_id not in self.config.owner_user_ids:
            return PolicyDecision(False, "sender is not owner")
        return PolicyDecision(True)

    def can_group_dispatch_work(self, msg):
        return PolicyDecision(False, "group chat cannot dispatch work aliases")
