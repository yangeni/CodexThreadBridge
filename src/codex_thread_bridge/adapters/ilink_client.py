from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, Protocol


class IlinkClientError(RuntimeError):
    pass


class IlinkClientTransientError(IlinkClientError):
    pass


class IlinkClientFatalError(IlinkClientError):
    pass


class JsonTransport(Protocol):
    def post_json(
        self,
        url: str,
        body: dict,
        headers: dict[str, str],
        timeout: float,
    ) -> dict:
        ...


class UrllibJsonTransport:
    def post_json(
        self,
        url: str,
        body: dict,
        headers: dict[str, str],
        timeout: float,
    ) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise IlinkClientTransientError(
                "iLink HTTP request failed: %s" % reason
            ) from exc
        try:
            result = json.loads(payload)
        except ValueError as exc:
            raise IlinkClientTransientError("iLink response was not JSON") from exc
        if not isinstance(result, dict):
            raise IlinkClientTransientError("iLink response was not an object")
        return result


@dataclass(frozen=True)
class ConversationContext:
    to_user_id: str
    context_token: str


class IlinkHttpClient:
    def __init__(
        self,
        base_url: str,
        bot_token: str,
        transport: Optional[JsonTransport] = None,
        default_timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._bot_token = bot_token
        self._transport = transport or UrllibJsonTransport()
        self.default_timeout_seconds = float(default_timeout_seconds)
        self._contexts: dict[str, ConversationContext] = {}

    def get_updates(
        self,
        cursor: str,
        timeout_seconds: Optional[float] = None,
    ) -> dict:
        response = self._post(
            "getupdates",
            {"get_updates_buf": cursor},
            timeout_seconds=timeout_seconds,
        )
        self._raise_for_ret(response, "getupdates")
        return response

    def remember_context(
        self,
        conversation_id: str,
        *,
        to_user_id: str,
        context_token: str,
    ) -> None:
        if not conversation_id or not to_user_id:
            raise IlinkClientFatalError(
                "conversation context identifiers must not be empty"
            )
        self._contexts[conversation_id] = ConversationContext(
            to_user_id=to_user_id,
            context_token=context_token,
        )

    def send_text(self, *, conversation_id: str, text: str) -> None:
        context = self._contexts.get(conversation_id)
        if context is None:
            raise IlinkClientFatalError(
                "missing conversation context for %s" % conversation_id
            )
        response = self._post(
            "sendmessage",
            {
                "msg": {
                    "to_user_id": context.to_user_id,
                    "context_token": context.context_token,
                    "item_list": [
                        {"type": 1, "text_item": {"text": text}},
                    ],
                }
            },
        )
        self._raise_for_ret(response, "sendmessage")

    def send_file(self, *, conversation_id: str, path: str, mime_type: str) -> None:
        raise NotImplementedError("iLink file upload is not implemented in v0.3")

    def send_typing(self, *, conversation_id: str, enabled: bool) -> None:
        return None

    def _post(
        self,
        endpoint: str,
        body: dict,
        timeout_seconds: Optional[float] = None,
    ) -> dict:
        return self._transport.post_json(
            "%s/%s" % (self.base_url, endpoint),
            body,
            self._headers(),
            self.default_timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": "Bearer %s" % self._bot_token,
        }

    def _raise_for_ret(self, response: dict, operation: str) -> None:
        if "ret" not in response:
            message = (
                response.get("errmsg")
                or response.get("errcode")
                or response.get("error")
            )
            if message:
                raise IlinkClientFatalError(
                    "iLink %s failed: %s" % (operation, self._sanitize(message))
                )
            if operation == "getupdates" and _looks_like_getupdates_response(response):
                return
            if operation == "sendmessage":
                return
            raise IlinkClientFatalError(
                "iLink %s failed: missing integer ret" % operation
            )
        ret = response["ret"]
        if not isinstance(ret, int) or isinstance(ret, bool):
            raise IlinkClientFatalError(
                "iLink %s failed: malformed integer ret" % operation
            )
        if ret != 0:
            message = response.get("errmsg") or response.get("errcode") or ret
            raise IlinkClientFatalError(
                "iLink %s failed: %s" % (operation, self._sanitize(message))
            )

    def _sanitize(self, value: object) -> str:
        text = str(value)
        if self._bot_token:
            text = text.replace(self._bot_token, "***")
        return text


def _looks_like_getupdates_response(response: dict) -> bool:
    return (
        "msgs" in response
        or "get_updates_buf" in response
        or "sync_buf" in response
    )
