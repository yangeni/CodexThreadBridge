from __future__ import annotations

from typing import Iterator, Protocol

from codex_thread_bridge.models import AttachmentRef, IncomingMessage


class WeChatChannelPort(Protocol):
    def iter_messages(self) -> Iterator[IncomingMessage]:
        ...

    def download_attachment(self, descriptor: dict) -> AttachmentRef:
        ...

    def send_text(self, conversation_id: str, text: str) -> None:
        ...

    def send_file(self, conversation_id: str, path: str, mime_type: str) -> None:
        ...

    def send_typing(self, conversation_id: str, enabled: bool) -> None:
        ...
