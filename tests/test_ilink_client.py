from __future__ import annotations

import pytest

from codex_thread_bridge.adapters.ilink_client import (
    IlinkClientError,
    IlinkClientFatalError,
    IlinkHttpClient,
)


class RecordingTransport:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict, dict[str, str], float]] = []

    def post_json(
        self,
        url: str,
        body: dict,
        headers: dict[str, str],
        timeout: float,
    ) -> dict:
        self.requests.append((url, body, headers, timeout))
        return self.responses.pop(0)


def test_get_updates_posts_cursor_and_auth_headers() -> None:
    transport = RecordingTransport(
        [{"ret": 0, "msgs": [{"message_id": 1}], "get_updates_buf": "cursor-2"}]
    )
    client = IlinkHttpClient(
        "https://ilink.example.test/bot",
        "secret",
        transport=transport,
    )

    response = client.get_updates("cursor-1", timeout_seconds=12.0)

    assert response == {
        "ret": 0,
        "msgs": [{"message_id": 1}],
        "get_updates_buf": "cursor-2",
    }
    url, body, headers, timeout = transport.requests[0]
    assert url == "https://ilink.example.test/bot/getupdates"
    assert body == {"get_updates_buf": "cursor-1"}
    assert headers["Authorization"] == "Bearer secret"
    assert headers["AuthorizationType"] == "ilink_bot_token"
    assert timeout == 12.0


def test_send_text_uses_context_token_and_text_item() -> None:
    transport = RecordingTransport([{"ret": 0}])
    client = IlinkHttpClient(
        "https://ilink.example.test/bot",
        "secret",
        transport=transport,
    )
    client.remember_context(
        "owner-chat",
        to_user_id="owner-1",
        context_token="ctx-1",
    )

    client.send_text(conversation_id="owner-chat", text="hello")

    assert transport.requests[0][0] == "https://ilink.example.test/bot/sendmessage"
    assert transport.requests[0][1] == {
        "msg": {
            "to_user_id": "owner-1",
            "context_token": "ctx-1",
            "item_list": [
                {"type": 1, "text_item": {"text": "hello"}},
            ],
        }
    }


def test_send_text_requires_remembered_context() -> None:
    client = IlinkHttpClient(
        "https://ilink.example.test/bot",
        "secret",
        transport=RecordingTransport([]),
    )

    with pytest.raises(IlinkClientError, match="conversation context"):
        client.send_text(conversation_id="owner-chat", text="hello")


def test_non_zero_ret_raises_sanitized_error() -> None:
    client = IlinkHttpClient(
        "https://ilink.example.test/bot",
        "secret-token",
        transport=RecordingTransport(
            [{"ret": -14, "errmsg": "session timeout for secret-token"}]
        ),
    )

    with pytest.raises(IlinkClientFatalError) as excinfo:
        client.get_updates("", timeout_seconds=1.0)

    assert "session timeout" in str(excinfo.value)
    assert "secret-token" not in str(excinfo.value)


def test_missing_ret_raises_sanitized_error() -> None:
    client = IlinkHttpClient(
        "https://ilink.example.test/bot",
        "secret-token",
        transport=RecordingTransport([{"error": "bad secret-token"}]),
    )

    with pytest.raises(IlinkClientFatalError) as excinfo:
        client.get_updates("", timeout_seconds=1.0)

    assert "ret" in str(excinfo.value)
    assert "secret-token" not in str(excinfo.value)


def test_malformed_ret_raises_error() -> None:
    client = IlinkHttpClient(
        "https://ilink.example.test/bot",
        "secret",
        transport=RecordingTransport([{"ret": "0"}]),
    )

    with pytest.raises(IlinkClientFatalError, match="ret"):
        client.get_updates("", timeout_seconds=1.0)
