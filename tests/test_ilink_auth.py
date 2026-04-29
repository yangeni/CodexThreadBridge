from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_thread_bridge.adapters.ilink_auth import (
    IlinkAuthClient,
    IlinkAuthError,
    IlinkCredentialStore,
    IlinkCredentials,
    IlinkLoginTimeoutError,
    render_env_lines,
    summarize_update_senders,
)


class RecordingGetTransport:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, float]] = []

    def get_json(self, url: str, timeout: float) -> dict:
        self.requests.append((url, timeout))
        return self.responses.pop(0)


class RecordingSleeper:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_start_login_fetches_qrcode_from_default_ilink_host() -> None:
    transport = RecordingGetTransport(
        [
            {
                "ret": 0,
                "qrcode": "qr-1",
                "qrcode_img_content": "https://scan.example/qr",
            }
        ]
    )
    client = IlinkAuthClient(transport=transport)

    result = client.start_login(timeout_seconds=12.0)

    assert result.qrcode == "qr-1"
    assert result.qrcode_url == "https://scan.example/qr"
    assert transport.requests == [
        (
            "https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode?bot_type=3",
            12.0,
        )
    ]


def test_poll_login_returns_credentials_when_confirmed() -> None:
    transport = RecordingGetTransport(
        [
            {
                "ret": 0,
                "status": "confirmed",
                "bot_token": "token-1",
                "ilink_bot_id": "bot-1",
                "baseurl": "https://ilinkai.weixin.qq.com/ilink/bot",
            }
        ]
    )
    client = IlinkAuthClient(transport=transport)

    result = client.poll_status("qr-1", timeout_seconds=5.0)

    assert result.status == "confirmed"
    assert result.credentials == IlinkCredentials(
        bot_token="token-1",
        account_id="bot-1",
        base_url="https://ilinkai.weixin.qq.com/ilink/bot",
    )
    assert transport.requests == [
        (
            "https://ilinkai.weixin.qq.com/ilink/bot/get_qrcode_status?qrcode=qr-1",
            5.0,
        )
    ]


def test_wait_for_login_ignores_waiting_status_and_returns_confirmed() -> None:
    transport = RecordingGetTransport(
        [
            {"ret": 0, "status": "waiting"},
            {
                "ret": 0,
                "status": "confirmed",
                "bot_token": "token-1",
                "ilink_bot_id": "bot-1",
                "baseurl": "https://ilinkai.weixin.qq.com/ilink/bot",
            },
        ]
    )
    sleeper = RecordingSleeper()
    client = IlinkAuthClient(transport=transport)

    credentials = client.wait_for_login(
        "qr-1",
        timeout_seconds=10.0,
        poll_interval_seconds=0.1,
        request_timeout_seconds=2.0,
        monotonic=sleeper.monotonic,
        sleep=sleeper.sleep,
    )

    assert credentials.bot_token == "token-1"
    assert sleeper.sleeps == [0.1]


def test_wait_for_login_raises_on_expired_qrcode() -> None:
    client = IlinkAuthClient(
        transport=RecordingGetTransport([{"ret": 0, "status": "expired"}])
    )

    with pytest.raises(IlinkAuthError, match="expired"):
        client.wait_for_login("qr-1", sleep=lambda _: None)


def test_wait_for_login_times_out() -> None:
    transport = RecordingGetTransport(
        [
            {"ret": 0, "status": "waiting"},
            {"ret": 0, "status": "waiting"},
            {"ret": 0, "status": "waiting"},
        ]
    )
    sleeper = RecordingSleeper()
    client = IlinkAuthClient(transport=transport)

    with pytest.raises(IlinkLoginTimeoutError):
        client.wait_for_login(
            "qr-1",
            timeout_seconds=0.2,
            poll_interval_seconds=0.1,
            monotonic=sleeper.monotonic,
            sleep=sleeper.sleep,
        )


def test_credential_store_writes_private_json(tmp_path: Path) -> None:
    path = tmp_path / "ilink_credentials.json"
    store = IlinkCredentialStore(path)

    store.save(IlinkCredentials("token-1", "bot-1", "https://host/ilink/bot"))

    assert store.load() == IlinkCredentials(
        bot_token="token-1",
        account_id="bot-1",
        base_url="https://host/ilink/bot",
    )
    assert json.loads(path.read_text()) == {
        "bot_token": "token-1",
        "account_id": "bot-1",
        "base_url": "https://host/ilink/bot",
    }
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_credential_store_rejects_missing_fields(tmp_path: Path) -> None:
    path = tmp_path / "ilink_credentials.json"
    path.write_text(json.dumps({"bot_token": "token-1"}))

    with pytest.raises(IlinkAuthError, match="credentials"):
        IlinkCredentialStore(path).load()


def test_render_env_lines_redacts_token() -> None:
    lines = render_env_lines(
        credentials_path=Path("/private/ctb/ilink_credentials.json"),
        owner_user_ids=("owner-1",),
    )

    assert "ILINK_CREDENTIALS_PATH=/private/ctb/ilink_credentials.json" in lines
    assert "ILINK_OWNER_USER_IDS=owner-1" in lines
    assert "ILINK_BOT_TOKEN" not in lines


def test_summarize_update_senders_includes_owner_discovery_fields() -> None:
    text = summarize_update_senders(
        {
            "msgs": [
                {
                    "message_id": 123,
                    "from_user_id": "owner-1",
                    "conversation_type": "private",
                    "item_list": [
                        {"type": 1, "text_item": {"text": "hello bridge"}},
                    ],
                },
                {
                    "message_id": "456",
                    "from_user_id": "group-user",
                    "to_user_id": "group-1",
                    "conversation_type": "group",
                    "item_list": [],
                },
            ]
        }
    )

    assert "from_user_id=owner-1" in text
    assert "conversation_type=private" in text
    assert "message_id=123" in text
    assert "hello bridge" in text
    assert "from_user_id=group-user" in text
    assert "conversation_type=group" in text
