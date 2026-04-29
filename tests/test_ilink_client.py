from __future__ import annotations

import socket
import base64

import pytest
import urllib.request

from codex_thread_bridge.adapters.ilink_client import (
    IlinkClientError,
    IlinkClientFatalError,
    IlinkHttpClient,
    IlinkClientTransientError,
    UrllibJsonTransport,
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
    assert body == {
        "get_updates_buf": "cursor-1",
        "base_info": {"channel_version": "2.1.7"},
    }
    assert headers["Authorization"] == "Bearer secret"
    assert headers["AuthorizationType"] == "ilink_bot_token"
    decoded_uin = base64.b64decode(headers["X-WECHAT-UIN"]).decode("utf-8")
    assert decoded_uin.isdigit()
    assert headers["iLink-App-Id"] == "bot"
    assert headers["iLink-App-ClientVersion"] == "131335"
    assert timeout == 12.0


def test_custom_wechat_uin_is_base64_encoded_in_headers() -> None:
    transport = RecordingTransport([{"ret": 0, "msgs": [], "get_updates_buf": ""}])
    client = IlinkHttpClient(
        "https://ilink.example.test/bot",
        "secret",
        transport=transport,
        wechat_uin="1234567890",
    )

    client.get_updates("", timeout_seconds=1.0)

    assert transport.requests[0][2]["X-WECHAT-UIN"] == "MTIzNDU2Nzg5MA=="


def test_get_updates_accepts_real_ilink_response_without_ret() -> None:
    transport = RecordingTransport(
        [
            {
                "msgs": [{"message_id": 1}],
                "sync_buf": "sync-1",
                "get_updates_buf": "cursor-2",
            }
        ]
    )
    client = IlinkHttpClient(
        "https://ilink.example.test/bot",
        "secret",
        transport=transport,
    )

    response = client.get_updates("cursor-1", timeout_seconds=12.0)

    assert response["msgs"] == [{"message_id": 1}]
    assert response["get_updates_buf"] == "cursor-2"


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
            "from_user_id": "",
            "to_user_id": "owner-1",
            "client_id": transport.requests[0][1]["msg"]["client_id"],
            "message_type": 2,
            "message_state": 2,
            "context_token": "ctx-1",
            "item_list": [
                {"type": 1, "text_item": {"text": "hello"}},
            ],
        },
        "base_info": {"channel_version": "2.1.7"},
    }
    assert transport.requests[0][1]["msg"]["client_id"].startswith("ctb-")


def test_send_text_accepts_real_ilink_response_without_ret() -> None:
    transport = RecordingTransport([{}])
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

    assert "bad" in str(excinfo.value)
    assert "secret-token" not in str(excinfo.value)


def test_send_text_missing_ret_with_error_field_raises() -> None:
    client = IlinkHttpClient(
        "https://ilink.example.test/bot",
        "secret-token",
        transport=RecordingTransport([{"errmsg": "bad secret-token"}]),
    )
    client.remember_context(
        "owner-chat",
        to_user_id="owner-1",
        context_token="ctx-1",
    )

    with pytest.raises(IlinkClientFatalError) as excinfo:
        client.send_text(conversation_id="owner-chat", text="hello")

    assert "bad" in str(excinfo.value)
    assert "secret-token" not in str(excinfo.value)


def test_malformed_ret_raises_error() -> None:
    client = IlinkHttpClient(
        "https://ilink.example.test/bot",
        "secret",
        transport=RecordingTransport([{"ret": "0"}]),
    )

    with pytest.raises(IlinkClientFatalError, match="ret"):
        client.get_updates("", timeout_seconds=1.0)


def test_urllib_transport_wraps_socket_timeout(monkeypatch) -> None:
    def raise_timeout(*args, **kwargs):
        raise socket.timeout("read timed out")

    monkeypatch.setattr(urllib.request, "urlopen", raise_timeout)

    with pytest.raises(IlinkClientTransientError, match="read timed out"):
        UrllibJsonTransport().post_json(
            "https://ilink.example.test/bot/getupdates",
            {},
            {},
            1.0,
        )
