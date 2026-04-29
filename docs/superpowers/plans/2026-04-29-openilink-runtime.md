# OpeniLink Runtime v0.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first real WeChat private-chat runtime for CodexThreadBridge using the iLink/OpenClaw-WeChat protocol shape.

**Architecture:** Keep Gateway Core unchanged as the owner of routing, policy, and Codex dispatch. Add a focused iLink runtime layer that loads local config, long-polls WeChat updates, maps iLink messages into existing `IncomingMessage` objects, sends Gateway replies back with `sendmessage`, and persists channel cursor state.

**Tech Stack:** Python stdlib only (`argparse`, `dataclasses`, `json`, `os`, `time`, `urllib.request`, `urllib.error`), existing `sqlite3` store, existing Gateway/Core tests, `pytest`.

---

## Scope Check

This plan implements one narrow v0.3 slice: real WeChat private-chat text runtime. It does not implement media upload, inbound media decryption, group QA runtime activation, WeChat-side Codex approval confirmation, Windows packaging, or launchd autostart.

The plan intentionally depends on the iLink/OpenClaw-WeChat protocol shape rather than `wechat-claude-code`. `wechat-claude-code` can remain an interaction reference, but no code or package from it should enter this project.

Post-review adjustment: the final implementation treats outbound `sendmessage`
transport exceptions as ambiguous delivery. It records `delivery_unknown`, scopes
runtime state by `<conversation_id>.<message_id>`, marks the source message
processed, and does not auto-replay the outbox. Explicit terminal iLink or
controller errors stop the runtime instead of being retried forever.

## Source References

- Spec: `docs/superpowers/specs/2026-04-29-openilink-runtime-design.md`
- Existing v0.2 adapter boundary: `src/codex_thread_bridge/adapters/openilink.py`
- Existing channel port: `src/codex_thread_bridge/adapters/wechat_channel.py`
- Existing Gateway Core: `src/codex_thread_bridge/gateway.py`
- Existing store: `src/codex_thread_bridge/stores.py`
- Primary external protocol reference: `https://github.com/Tencent/openclaw-weixin`

## File Structure

Create and modify these files:

```text
CodexThreadBridge/
├── .env.example
├── README.md
├── docs/
│   └── 03_v0.3_OpeniLink运行说明.md
├── src/codex_thread_bridge/
│   ├── config.py
│   ├── stores.py
│   └── adapters/
│       ├── ilink_client.py
│       ├── ilink_events.py
│       └── openilink_runtime.py
└── tests/
    ├── fixtures/
    │   ├── ilink_getupdates_text.json
    │   └── ilink_getupdates_group_text.json
    ├── test_ilink_config.py
    ├── test_ilink_client.py
    ├── test_ilink_events.py
    ├── test_openilink_runtime.py
    └── test_stores_runtime_state.py
```

Responsibilities:

- `config.py`: add `OpeniLinkRuntimeConfig` and environment loading without leaking token values.
- `stores.py`: add small key/value runtime state helpers for the iLink cursor and delivery failures.
- `adapters/ilink_client.py`: HTTP request wrapper for `getupdates`, `sendmessage`, optional `getconfig`, and optional `sendtyping`.
- `adapters/ilink_events.py`: map iLink protocol messages to the normalized payload accepted by `normalize_openilink_event`.
- `adapters/openilink_runtime.py`: loop over update batches, call Gateway, send text replies, and persist cursor safely.
- `.env.example`: document local config variable names using fake values.
- `docs/03_v0.3_OpeniLink运行说明.md`: operator runbook.

## Task 1: Runtime Config Loader

**Files:**
- Modify: `src/codex_thread_bridge/config.py`
- Create: `tests/test_ilink_config.py`
- Create: `.env.example`

- [ ] **Step 1: Write failing tests for env loading and token masking**

Create `tests/test_ilink_config.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from codex_thread_bridge.config import OpeniLinkRuntimeConfig


def test_openilink_runtime_config_loads_required_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTB_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ILINK_BASE_URL", "https://ilink.example.test/bot/")
    monkeypatch.setenv("ILINK_BOT_TOKEN", "redacted-token-sample")
    monkeypatch.setenv("ILINK_OWNER_USER_IDS", "owner-1, owner-2")

    config = OpeniLinkRuntimeConfig.from_env()

    assert config.bridge.project_root == tmp_path
    assert config.base_url == "https://ilink.example.test/bot"
    assert config.bot_token == "redacted-token-sample"
    assert config.owner_user_ids == frozenset({"owner-1", "owner-2"})
    assert config.poll_timeout_seconds == 35.0
    assert config.request_timeout_seconds == 30.0
    assert config.controller_command == ()


def test_openilink_runtime_config_masks_secret_in_repr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTB_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ILINK_BASE_URL", "https://ilink.example.test/bot")
    monkeypatch.setenv("ILINK_BOT_TOKEN", "redacted-token-sample")
    monkeypatch.setenv("ILINK_OWNER_USER_IDS", "owner-1")

    text = repr(OpeniLinkRuntimeConfig.from_env())

    assert "redacted-token-sample" not in text
    assert "bot_token='***'" in text


@pytest.mark.parametrize("missing", ["ILINK_BASE_URL", "ILINK_BOT_TOKEN", "ILINK_OWNER_USER_IDS"])
def test_openilink_runtime_config_requires_channel_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    monkeypatch.setenv("CTB_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ILINK_BASE_URL", "https://ilink.example.test/bot")
    monkeypatch.setenv("ILINK_BOT_TOKEN", "redacted-token-sample")
    monkeypatch.setenv("ILINK_OWNER_USER_IDS", "owner-1")
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ValueError, match=missing):
        OpeniLinkRuntimeConfig.from_env()
```

- [ ] **Step 2: Run the config tests and verify failure**

Run:

```bash
python3 -m pytest tests/test_ilink_config.py -q
```

Expected: failure because `OpeniLinkRuntimeConfig` does not exist.

- [ ] **Step 3: Add runtime config implementation**

Modify `src/codex_thread_bridge/config.py` by adding this dataclass and helpers after `BridgeConfig`:

```python
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OpeniLinkRuntimeConfig:
    bridge: BridgeConfig
    base_url: str
    bot_token: str
    owner_user_ids: frozenset[str]
    poll_timeout_seconds: float = 35.0
    request_timeout_seconds: float = 30.0
    controller_command: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "OpeniLinkRuntimeConfig":
        project_root = Path(os.environ.get("CTB_PROJECT_ROOT", ".")).resolve()
        base_url = _required_env("ILINK_BASE_URL").rstrip("/")
        bot_token = _required_env("ILINK_BOT_TOKEN")
        owner_user_ids = _parse_csv_env("ILINK_OWNER_USER_IDS")
        bridge = BridgeConfig.local_dev(project_root, set(owner_user_ids))
        return cls(
            bridge=bridge,
            base_url=base_url,
            bot_token=bot_token,
            owner_user_ids=frozenset(owner_user_ids),
            poll_timeout_seconds=_float_env("ILINK_POLL_TIMEOUT_SECONDS", 35.0),
            request_timeout_seconds=_float_env("ILINK_REQUEST_TIMEOUT_SECONDS", 30.0),
            controller_command=tuple(_parse_csv_env("CTB_CONTROLLER_COMMAND", required=False)),
        )

    def __repr__(self) -> str:
        return (
            "OpeniLinkRuntimeConfig("
            "bridge=%r, base_url=%r, bot_token='***', owner_user_ids=%r, "
            "poll_timeout_seconds=%r, request_timeout_seconds=%r, controller_command=%r)"
            % (
                self.bridge,
                self.base_url,
                self.owner_user_ids,
                self.poll_timeout_seconds,
                self.request_timeout_seconds,
                self.controller_command,
            )
        )


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ValueError("missing required environment variable: %s" % name)
    return value.strip()


def _parse_csv_env(name: str, required: bool = True) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None or not value.strip():
        if required:
            raise ValueError("missing required environment variable: %s" % name)
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        raise ValueError("invalid float environment variable: %s" % name) from None
```

- [ ] **Step 4: Add `.env.example`**

Create `.env.example`:

```text
CTB_PROJECT_ROOT=/Users/clngs/Documents/CLngs_Vault/CodexThreadBridge
ILINK_BASE_URL=https://ilink.example.invalid/bot
ILINK_BOT_TOKEN=replace-with-local-token
ILINK_OWNER_USER_IDS=owner-wechat-user-id
ILINK_POLL_TIMEOUT_SECONDS=35
ILINK_REQUEST_TIMEOUT_SECONDS=30
CTB_CONTROLLER_COMMAND=python3,/Users/clngs/Documents/Codex/tools/cross-thread-controller/server.py
```

- [ ] **Step 5: Run config tests and full suite**

Run:

```bash
python3 -m pytest tests/test_ilink_config.py -q
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add .env.example src/codex_thread_bridge/config.py tests/test_ilink_config.py
git commit -m "feat: add openilink runtime config"
```

## Task 2: iLink HTTP Client

**Files:**
- Create: `src/codex_thread_bridge/adapters/ilink_client.py`
- Create: `tests/test_ilink_client.py`

- [ ] **Step 1: Write failing HTTP client tests**

Create `tests/test_ilink_client.py`:

```python
from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

from codex_thread_bridge.adapters.ilink_client import (
    IlinkClientError,
    IlinkHttpClient,
)


class RecordingTransport:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict, dict[str, str], float]] = []

    def post_json(self, url: str, body: dict, headers: dict[str, str], timeout: float) -> dict:
        self.requests.append((url, body, headers, timeout))
        return self.responses.pop(0)


def test_get_updates_posts_cursor_and_returns_response() -> None:
    transport = RecordingTransport([
        {"ret": 0, "msgs": [{"message_id": 1}], "get_updates_buf": "cursor-2"}
    ])
    client = IlinkHttpClient("https://ilink.example.test/bot", "secret", transport=transport)

    response = client.get_updates("cursor-1", timeout_seconds=12.0)

    assert response == {"ret": 0, "msgs": [{"message_id": 1}], "get_updates_buf": "cursor-2"}
    url, body, headers, timeout = transport.requests[0]
    assert url == "https://ilink.example.test/bot/getupdates"
    assert body == {"get_updates_buf": "cursor-1"}
    assert headers["Authorization"] == "Bearer secret"
    assert headers["AuthorizationType"] == "ilink_bot_token"
    assert timeout == 12.0


def test_send_text_uses_context_token_and_text_item() -> None:
    transport = RecordingTransport([{"ret": 0}])
    client = IlinkHttpClient("https://ilink.example.test/bot", "secret", transport=transport)
    client.remember_context("owner-chat", to_user_id="owner-1", context_token="ctx-1")

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
    client = IlinkHttpClient("https://ilink.example.test/bot", "secret", transport=RecordingTransport([]))

    with pytest.raises(IlinkClientError, match="conversation context"):
        client.send_text(conversation_id="owner-chat", text="hello")


def test_non_zero_ret_raises_sanitized_error() -> None:
    client = IlinkHttpClient(
        "https://ilink.example.test/bot",
        "redacted-token-sample",
        transport=RecordingTransport([{"ret": -14, "errmsg": "session timeout"}]),
    )

    with pytest.raises(IlinkClientError) as excinfo:
        client.get_updates("", timeout_seconds=1.0)

    assert "session timeout" in str(excinfo.value)
    assert "redacted-token-sample" not in str(excinfo.value)
```

- [ ] **Step 2: Run the client tests and verify failure**

Run:

```bash
python3 -m pytest tests/test_ilink_client.py -q
```

Expected: failure because `codex_thread_bridge.adapters.ilink_client` does not exist.

- [ ] **Step 3: Implement `ilink_client.py`**

Create `src/codex_thread_bridge/adapters/ilink_client.py`:

```python
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, Protocol


class IlinkClientError(RuntimeError):
    pass


class JsonTransport(Protocol):
    def post_json(self, url: str, body: dict, headers: dict[str, str], timeout: float) -> dict:
        ...


class UrllibJsonTransport:
    def post_json(self, url: str, body: dict, headers: dict[str, str], timeout: float) -> dict:
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
        except urllib.error.URLError as exc:
            raise IlinkClientError("iLink HTTP request failed: %s" % exc.reason) from exc
        try:
            result = json.loads(payload)
        except ValueError as exc:
            raise IlinkClientError("iLink response was not JSON") from exc
        if not isinstance(result, dict):
            raise IlinkClientError("iLink response was not an object")
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

    def get_updates(self, cursor: str, timeout_seconds: Optional[float] = None) -> dict:
        response = self._post(
            "getupdates",
            {"get_updates_buf": cursor},
            timeout_seconds=timeout_seconds,
        )
        self._raise_for_ret(response, "getupdates")
        return response

    def remember_context(self, conversation_id: str, *, to_user_id: str, context_token: str) -> None:
        if not conversation_id or not to_user_id:
            raise IlinkClientError("conversation context identifiers must not be empty")
        self._contexts[conversation_id] = ConversationContext(
            to_user_id=to_user_id,
            context_token=context_token,
        )

    def send_text(self, *, conversation_id: str, text: str) -> None:
        context = self._contexts.get(conversation_id)
        if context is None:
            raise IlinkClientError("missing conversation context for %s" % conversation_id)
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

    def _post(self, endpoint: str, body: dict, timeout_seconds: Optional[float] = None) -> dict:
        return self._transport.post_json(
            "%s/%s" % (self.base_url, endpoint),
            body,
            self._headers(),
            self.default_timeout_seconds if timeout_seconds is None else float(timeout_seconds),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": "Bearer %s" % self._bot_token,
        }

    def _raise_for_ret(self, response: dict, operation: str) -> None:
        ret = response.get("ret", 0)
        if ret != 0:
            message = response.get("errmsg") or response.get("errcode") or ret
            raise IlinkClientError("iLink %s failed: %s" % (operation, message))
```

- [ ] **Step 4: Run client tests and full suite**

Run:

```bash
python3 -m pytest tests/test_ilink_client.py -q
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/codex_thread_bridge/adapters/ilink_client.py tests/test_ilink_client.py
git commit -m "feat: add ilink http client"
```

## Task 3: iLink Event Mapping

**Files:**
- Create: `src/codex_thread_bridge/adapters/ilink_events.py`
- Create: `tests/fixtures/ilink_getupdates_text.json`
- Create: `tests/fixtures/ilink_getupdates_group_text.json`
- Create: `tests/test_ilink_events.py`

- [ ] **Step 1: Add fixtures**

Create `tests/fixtures/ilink_getupdates_text.json`:

```json
{
  "ret": 0,
  "get_updates_buf": "cursor-2",
  "msgs": [
    {
      "seq": 10,
      "message_id": 12345,
      "from_user_id": "owner-1",
      "to_user_id": "bot-1",
      "session_id": "session-owner-1",
      "message_type": 1,
      "message_state": 2,
      "context_token": "ctx-owner-1",
      "item_list": [
        {
          "type": 1,
          "text_item": {
            "text": "/list"
          }
        }
      ]
    }
  ]
}
```

Create `tests/fixtures/ilink_getupdates_group_text.json`:

```json
{
  "ret": 0,
  "get_updates_buf": "cursor-3",
  "msgs": [
    {
      "seq": 11,
      "message_id": 12346,
      "from_user_id": "member-1",
      "to_user_id": "group-1",
      "session_id": "session-group-1",
      "message_type": 1,
      "message_state": 2,
      "context_token": "ctx-group-1",
      "conversation_type": "group",
      "item_list": [
        {
          "type": 1,
          "text_item": {
            "text": "@Bot status?"
          }
        }
      ]
    }
  ]
}
```

- [ ] **Step 2: Write failing event mapping tests**

Create `tests/test_ilink_events.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_thread_bridge.adapters.ilink_events import IlinkEventError, map_update_batch


def _fixture(name: str) -> dict:
    path = Path(__file__).parent / "fixtures" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_private_text_update_maps_to_openilink_payload_and_context() -> None:
    events = map_update_batch(_fixture("ilink_getupdates_text.json"))

    assert len(events) == 1
    event = events[0]
    assert event.payload == {
        "message_id": "12345",
        "conversation_id": "owner-1",
        "conversation_type": "private",
        "sender_id": "owner-1",
        "thread_key": "owner-1",
        "text": "/list",
        "mentions": [],
        "attachments": [],
    }
    assert event.context.conversation_id == "owner-1"
    assert event.context.to_user_id == "owner-1"
    assert event.context.context_token == "ctx-owner-1"


def test_group_update_maps_as_group_but_runtime_can_ignore_it() -> None:
    events = map_update_batch(_fixture("ilink_getupdates_group_text.json"))

    assert events[0].payload["conversation_type"] == "group"
    assert events[0].payload["conversation_id"] == "group-1"
    assert events[0].payload["sender_id"] == "member-1"
    assert events[0].payload["text"] == "@Bot status?"


def test_non_finish_messages_are_skipped() -> None:
    payload = _fixture("ilink_getupdates_text.json")
    payload["msgs"][0]["message_state"] = 1

    assert map_update_batch(payload) == ()


def test_media_items_become_opaque_attachment_descriptors() -> None:
    payload = _fixture("ilink_getupdates_text.json")
    payload["msgs"][0]["item_list"] = [
        {"type": 2, "image_item": {"aes_key": "key", "cdn": "ref"}}
    ]

    events = map_update_batch(payload)

    assert events[0].payload["text"] == ""
    assert events[0].payload["attachments"] == [
        {
            "message_id": "12345",
            "mime_type": "application/octet-stream",
            "original_name": "attachment",
            "ilink_item": {"type": 2, "image_item": {"aes_key": "key", "cdn": "ref"}},
        }
    ]


def test_missing_required_message_field_raises_clear_error() -> None:
    payload = _fixture("ilink_getupdates_text.json")
    del payload["msgs"][0]["from_user_id"]

    with pytest.raises(IlinkEventError, match="from_user_id"):
        map_update_batch(payload)
```

- [ ] **Step 3: Run mapping tests and verify failure**

Run:

```bash
python3 -m pytest tests/test_ilink_events.py -q
```

Expected: failure because `codex_thread_bridge.adapters.ilink_events` does not exist.

- [ ] **Step 4: Implement event mapping**

Create `src/codex_thread_bridge/adapters/ilink_events.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


class IlinkEventError(ValueError):
    pass


@dataclass(frozen=True)
class IlinkConversationContext:
    conversation_id: str
    to_user_id: str
    context_token: str


@dataclass(frozen=True)
class MappedIlinkEvent:
    payload: dict
    context: IlinkConversationContext


def map_update_batch(batch: dict) -> tuple[MappedIlinkEvent, ...]:
    if not isinstance(batch, dict):
        raise IlinkEventError("update batch must be an object")
    msgs = batch.get("msgs", ())
    if msgs is None:
        return ()
    if not isinstance(msgs, list):
        raise IlinkEventError("msgs must be a list")
    return tuple(event for event in (_map_message(msg) for msg in msgs) if event is not None)


def _map_message(msg: Any) -> Optional[MappedIlinkEvent]:
    if not isinstance(msg, dict):
        raise IlinkEventError("message must be an object")
    if msg.get("message_state", 2) != 2:
        return None
    message_id = str(_required(msg, "message_id"))
    sender_id = _required_string(msg, "from_user_id")
    context_token = str(msg.get("context_token") or "")
    conversation_type = _conversation_type(msg)
    conversation_id = _conversation_id(msg, conversation_type, sender_id)
    text, attachments = _items(msg.get("item_list", ()), message_id)
    return MappedIlinkEvent(
        payload={
            "message_id": message_id,
            "conversation_id": conversation_id,
            "conversation_type": conversation_type,
            "sender_id": sender_id,
            "thread_key": conversation_id,
            "text": text,
            "mentions": [],
            "attachments": attachments,
        },
        context=IlinkConversationContext(
            conversation_id=conversation_id,
            to_user_id=sender_id if conversation_type == "private" else conversation_id,
            context_token=context_token,
        ),
    )


def _conversation_type(msg: dict) -> str:
    value = msg.get("conversation_type")
    if value in ("private", "group"):
        return str(value)
    to_user_id = msg.get("to_user_id")
    if isinstance(to_user_id, str) and to_user_id.startswith("group"):
        return "group"
    return "private"


def _conversation_id(msg: dict, conversation_type: str, sender_id: str) -> str:
    if conversation_type == "private":
        return sender_id
    return _required_string(msg, "to_user_id")


def _items(value: Any, message_id: str) -> tuple[str, list[dict]]:
    if value is None:
        return "", []
    if not isinstance(value, list):
        raise IlinkEventError("item_list must be a list")
    texts: list[str] = []
    attachments: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            raise IlinkEventError("item_list entries must be objects")
        if item.get("type") == 1:
            text_item = item.get("text_item")
            if not isinstance(text_item, dict):
                raise IlinkEventError("text_item must be an object")
            text = text_item.get("text", "")
            if not isinstance(text, str):
                raise IlinkEventError("text_item.text must be a string")
            texts.append(text)
        else:
            attachments.append(
                {
                    "message_id": message_id,
                    "mime_type": "application/octet-stream",
                    "original_name": "attachment",
                    "ilink_item": item,
                }
            )
    return "\n".join(texts), attachments


def _required(msg: dict, field: str) -> Any:
    if field not in msg:
        raise IlinkEventError("missing required message field: %s" % field)
    return msg[field]


def _required_string(msg: dict, field: str) -> str:
    value = _required(msg, field)
    if not isinstance(value, str) or not value.strip():
        raise IlinkEventError("invalid message field: %s" % field)
    return value
```

- [ ] **Step 5: Run mapping tests and full suite**

Run:

```bash
python3 -m pytest tests/test_ilink_events.py -q
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/codex_thread_bridge/adapters/ilink_events.py tests/fixtures/ilink_getupdates_text.json tests/fixtures/ilink_getupdates_group_text.json tests/test_ilink_events.py
git commit -m "feat: map ilink updates to gateway events"
```

## Task 4: Runtime State Store

**Files:**
- Modify: `src/codex_thread_bridge/stores.py`
- Create: `tests/test_stores_runtime_state.py`

- [ ] **Step 1: Write failing store tests**

Create `tests/test_stores_runtime_state.py`:

```python
from __future__ import annotations

from codex_thread_bridge.stores import BridgeStore


def test_runtime_state_round_trips_string_values(tmp_path) -> None:
    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()

    assert store.get_runtime_state("ilink.cursor") is None
    store.set_runtime_state("ilink.cursor", "cursor-1")

    assert store.get_runtime_state("ilink.cursor") == "cursor-1"


def test_runtime_event_records_sanitized_payload(tmp_path) -> None:
    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()

    event_id = store.record_event("delivery_failed", {"conversation_id": "owner-1", "reason": "timeout"})
    events = store.list_events("delivery_failed")

    assert event_id == 1
    assert events == [
        {
            "id": 1,
            "event_type": "delivery_failed",
            "payload_json": "{\"conversation_id\": \"owner-1\", \"reason\": \"timeout\"}",
        }
    ]
```

- [ ] **Step 2: Run store tests and verify failure**

Run:

```bash
python3 -m pytest tests/test_stores_runtime_state.py -q
```

Expected: failure because runtime state helpers and event listing do not exist.

- [ ] **Step 3: Extend SQLite schema and methods**

Modify `BridgeStore.initialize()` to add:

```sql
CREATE TABLE IF NOT EXISTS runtime_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
```

Add methods to `BridgeStore`:

```python
def set_runtime_state(self, key: str, value: str) -> None:
    with self._connect() as connection:
        connection.execute(
            """
            INSERT INTO runtime_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, value, self._now()),
        )


def get_runtime_state(self, key: str) -> Optional[str]:
    with self._connect() as connection:
        row = connection.execute(
            "SELECT value FROM runtime_state WHERE key = ?",
            (key,),
        ).fetchone()
    if row is None:
        return None
    return str(row["value"])


def record_event(self, event_type: str, payload: dict) -> int:
    with self._connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO events (event_type, payload_json, created_at)
            VALUES (?, ?, ?)
            """,
            (event_type, json.dumps(payload, ensure_ascii=True), self._now()),
        )
    return int(cursor.lastrowid)


def list_events(self, event_type: Optional[str] = None) -> List[Dict[str, object]]:
    with self._connect() as connection:
        if event_type is None:
            rows = connection.execute(
                "SELECT id, event_type, payload_json FROM events ORDER BY id ASC"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id, event_type, payload_json FROM events WHERE event_type = ? ORDER BY id ASC",
                (event_type,),
            ).fetchall()
    return [self._row_to_dict(row) for row in rows]
```

- [ ] **Step 4: Run store tests and full suite**

Run:

```bash
python3 -m pytest tests/test_stores_runtime_state.py -q
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/codex_thread_bridge/stores.py tests/test_stores_runtime_state.py
git commit -m "feat: persist runtime channel state"
```

## Task 5: Runtime Batch Processor

**Files:**
- Create: `src/codex_thread_bridge/adapters/openilink_runtime.py`
- Create: `tests/test_openilink_runtime.py`

- [ ] **Step 1: Write failing runtime tests with fake client and gateway**

Create `tests/test_openilink_runtime.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from codex_thread_bridge.adapters.openilink_runtime import OpeniLinkRuntime
from codex_thread_bridge.models import OutgoingMessage
from codex_thread_bridge.stores import BridgeStore


@dataclass
class FakeGateway:
    replies: list[OutgoingMessage]
    seen_texts: list[str]

    def handle(self, msg):
        self.seen_texts.append(msg.text)
        return self.replies.pop(0)


class FakeIlinkClient:
    def __init__(self, batch: dict) -> None:
        self.batch = batch
        self.contexts: list[tuple[str, str, str]] = []
        self.sent: list[tuple[str, str]] = []

    def get_updates(self, cursor: str, timeout_seconds: Optional[float] = None) -> dict:
        assert cursor == ""
        return self.batch

    def remember_context(self, conversation_id: str, *, to_user_id: str, context_token: str) -> None:
        self.contexts.append((conversation_id, to_user_id, context_token))

    def send_text(self, *, conversation_id: str, text: str) -> None:
        self.sent.append((conversation_id, text))


def test_process_one_batch_handles_private_message_sends_reply_and_advances_cursor(tmp_path) -> None:
    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()
    client = FakeIlinkClient(
        {
            "ret": 0,
            "get_updates_buf": "cursor-2",
            "msgs": [
                {
                    "message_id": 12345,
                    "from_user_id": "owner-1",
                    "to_user_id": "bot-1",
                    "message_state": 2,
                    "context_token": "ctx-owner-1",
                    "item_list": [{"type": 1, "text_item": {"text": "/help"}}],
                }
            ],
        }
    )
    gateway = FakeGateway([OutgoingMessage("owner-1", "help text")], [])
    runtime = OpeniLinkRuntime(client=client, gateway=gateway, store=store, owner_user_ids={"owner-1"})

    result = runtime.process_one_batch()

    assert result.messages_seen == 1
    assert result.replies_sent == 1
    assert gateway.seen_texts == ["/help"]
    assert client.contexts == [("owner-1", "owner-1", "ctx-owner-1")]
    assert client.sent == [("owner-1", "help text")]
    assert store.get_runtime_state("ilink.cursor") == "cursor-2"


def test_empty_gateway_reply_does_not_send_text_but_advances_cursor(tmp_path) -> None:
    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()
    client = FakeIlinkClient(
        {
            "ret": 0,
            "get_updates_buf": "cursor-2",
            "msgs": [
                {
                    "message_id": 12345,
                    "from_user_id": "owner-1",
                    "to_user_id": "bot-1",
                    "message_state": 2,
                    "context_token": "ctx-owner-1",
                    "item_list": [{"type": 1, "text_item": {"text": ""}}],
                }
            ],
        }
    )
    gateway = FakeGateway([OutgoingMessage("owner-1", "")], [])
    runtime = OpeniLinkRuntime(client=client, gateway=gateway, store=store, owner_user_ids={"owner-1"})

    result = runtime.process_one_batch()

    assert result.replies_sent == 0
    assert client.sent == []
    assert store.get_runtime_state("ilink.cursor") == "cursor-2"


def test_send_failure_records_event_and_does_not_advance_cursor(tmp_path) -> None:
    class FailingSendClient(FakeIlinkClient):
        def send_text(self, *, conversation_id: str, text: str) -> None:
            raise RuntimeError("network timeout redacted-token-sample")

    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()
    client = FailingSendClient(
        {
            "ret": 0,
            "get_updates_buf": "cursor-2",
            "msgs": [
                {
                    "message_id": 12345,
                    "from_user_id": "owner-1",
                    "to_user_id": "bot-1",
                    "message_state": 2,
                    "context_token": "ctx-owner-1",
                    "item_list": [{"type": 1, "text_item": {"text": "/help"}}],
                }
            ],
        }
    )
    gateway = FakeGateway([OutgoingMessage("owner-1", "help text")], [])
    runtime = OpeniLinkRuntime(
        client=client,
        gateway=gateway,
        store=store,
        owner_user_ids={"owner-1"},
        redacted_values={"redacted-token-sample"},
    )

    result = runtime.process_one_batch()

    assert result.replies_sent == 0
    assert store.get_runtime_state("ilink.cursor") is None
    assert "redacted-token-sample" not in str(store.list_events("delivery_failed"))


def test_group_message_is_ignored_by_v03_runtime_without_calling_gateway(tmp_path) -> None:
    store = BridgeStore(tmp_path / "bridge.sqlite3")
    store.initialize()
    client = FakeIlinkClient(
        {
            "ret": 0,
            "get_updates_buf": "cursor-2",
            "msgs": [
                {
                    "message_id": 12345,
                    "from_user_id": "member-1",
                    "to_user_id": "group-1",
                    "conversation_type": "group",
                    "message_state": 2,
                    "context_token": "ctx-group-1",
                    "item_list": [{"type": 1, "text_item": {"text": "@Bot hello"}}],
                }
            ],
        }
    )
    gateway = FakeGateway([], [])
    runtime = OpeniLinkRuntime(client=client, gateway=gateway, store=store, owner_user_ids={"owner-1"})

    result = runtime.process_one_batch()

    assert result.messages_seen == 1
    assert result.replies_sent == 0
    assert gateway.seen_texts == []
    assert client.sent == []
    assert store.get_runtime_state("ilink.cursor") == "cursor-2"
```

- [ ] **Step 2: Run runtime tests and verify failure**

Run:

```bash
python3 -m pytest tests/test_openilink_runtime.py -q
```

Expected: failure because `OpeniLinkRuntime` does not exist.

- [ ] **Step 3: Implement runtime batch processor**

Create `src/codex_thread_bridge/adapters/openilink_runtime.py` with:

```python
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from codex_thread_bridge.adapters.ilink_client import IlinkHttpClient
from codex_thread_bridge.adapters.ilink_events import map_update_batch
from codex_thread_bridge.adapters.openilink import normalize_openilink_event
from codex_thread_bridge.config import OpeniLinkRuntimeConfig
from codex_thread_bridge.controller_client import McpControllerClient
from codex_thread_bridge.gateway import Gateway
from codex_thread_bridge.stores import BridgeStore


@dataclass(frozen=True)
class RuntimeBatchResult:
    messages_seen: int
    replies_sent: int
    cursor: str


class OpeniLinkRuntime:
    def __init__(
        self,
        *,
        client,
        gateway,
        store: BridgeStore,
        owner_user_ids: Iterable[str],
        redacted_values: Iterable[str] = (),
    ) -> None:
        self.client = client
        self.gateway = gateway
        self.store = store
        self.owner_user_ids = set(owner_user_ids)
        self.redacted_values = tuple(value for value in redacted_values if value)

    def process_one_batch(self, timeout_seconds: Optional[float] = None) -> RuntimeBatchResult:
        cursor = self.store.get_runtime_state("ilink.cursor") or ""
        batch = self.client.get_updates(cursor, timeout_seconds=timeout_seconds)
        events = map_update_batch(batch)
        replies_sent = 0
        delivery_failed = False
        for event in events:
            self.client.remember_context(
                event.context.conversation_id,
                to_user_id=event.context.to_user_id,
                context_token=event.context.context_token,
            )
            if event.payload.get("conversation_type") != "private":
                self.store.record_event(
                    "ignored_non_private_message",
                    {"conversation_id": event.context.conversation_id},
                )
                continue
            msg = normalize_openilink_event(event.payload, self.owner_user_ids)
            reply = self.gateway.handle(msg)
            if reply.text:
                try:
                    self.client.send_text(conversation_id=reply.conversation_id, text=reply.text)
                    replies_sent += 1
                except Exception as exc:
                    delivery_failed = True
                    self.store.record_event(
                        "delivery_failed",
                        {
                            "conversation_id": reply.conversation_id,
                            "reason": _sanitize_error(exc, self.redacted_values),
                        },
                    )
                    break
        new_cursor = str(batch.get("get_updates_buf") or cursor)
        if not delivery_failed:
            self.store.set_runtime_state("ilink.cursor", new_cursor)
        return RuntimeBatchResult(
            messages_seen=len(events),
            replies_sent=replies_sent,
            cursor=new_cursor,
        )

    def run_forever(self, poll_timeout_seconds: float, idle_sleep_seconds: float = 1.0) -> None:
        while True:
            result = self.process_one_batch(timeout_seconds=poll_timeout_seconds)
            if result.messages_seen == 0:
                time.sleep(idle_sleep_seconds)


def _sanitize_error(exc: Exception, redacted_values: Iterable[str]) -> str:
    text = str(exc)
    for value in redacted_values:
        text = text.replace(value, "[redacted]")
    return text[:300]
```

- [ ] **Step 4: Add CLI entrypoint in the same module**

Append this code to `openilink_runtime.py`:

```python
def build_runtime_from_env() -> OpeniLinkRuntime:
    config = OpeniLinkRuntimeConfig.from_env()
    store = BridgeStore(config.bridge.sqlite_path)
    store.initialize()
    if not config.controller_command:
        raise ValueError("CTB_CONTROLLER_COMMAND must be set for real runtime")
    controller = McpControllerClient(command=config.controller_command)
    gateway = Gateway(config.bridge, store, controller)
    client = IlinkHttpClient(
        config.base_url,
        config.bot_token,
        default_timeout_seconds=config.request_timeout_seconds,
    )
    return OpeniLinkRuntime(
        client=client,
        gateway=gateway,
        store=store,
        owner_user_ids=config.owner_user_ids,
        redacted_values={config.bot_token},
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run CodexThreadBridge OpeniLink runtime")
    parser.add_argument("--once", action="store_true", help="process one update batch then exit")
    args = parser.parse_args(argv)
    runtime = build_runtime_from_env()
    config = OpeniLinkRuntimeConfig.from_env()
    if args.once:
        runtime.process_one_batch(timeout_seconds=config.poll_timeout_seconds)
        return 0
    runtime.run_forever(poll_timeout_seconds=config.poll_timeout_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run runtime tests and full suite**

Run:

```bash
python3 -m pytest tests/test_openilink_runtime.py -q
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/codex_thread_bridge/adapters/openilink_runtime.py tests/test_openilink_runtime.py
git commit -m "feat: add openilink runtime batch processor"
```

## Task 6: End-to-End Fake iLink Smoke Test

**Files:**
- Modify: `tests/test_openilink_runtime.py`

- [ ] **Step 1: Add a fake-server style smoke test**

Append this test to `tests/test_openilink_runtime.py`:

```python
def test_runtime_smoke_add_use_and_dispatch_with_fake_controller(tmp_path) -> None:
    from codex_thread_bridge.controller_client import ControllerRunResult
    from codex_thread_bridge.config import BridgeConfig
    from codex_thread_bridge.gateway import Gateway
    from codex_thread_bridge.models import ExecutionPolicy

    config = BridgeConfig.local_dev(tmp_path, {"owner-1"})
    store = BridgeStore(config.sqlite_path)
    store.initialize()

    class RuntimeSmokeController:
        def __init__(self) -> None:
            self.starts: list[dict[str, object]] = []

        def status(self, session_id: str) -> dict:
            return {
                "session_id": session_id,
                "locked": False,
                "dirty": False,
                "reconcile_required": False,
                "session_head": "head-1",
                "cwd": str(tmp_path / "work"),
            }

        def start_or_send(
            self,
            *,
            session_id: Optional[str],
            cwd: str,
            message: str,
            owner: str,
            policy: ExecutionPolicy,
            idempotency_key: str,
            expected_session_head: Optional[str],
        ) -> ControllerRunResult:
            self.starts.append(
                {
                    "session_id": session_id,
                    "cwd": cwd,
                    "message": message,
                    "owner": owner,
                    "policy": policy,
                    "idempotency_key": idempotency_key,
                    "expected_session_head": expected_session_head,
                }
            )
            return ControllerRunResult(
                run_id="run-1",
                session_id=session_id or "created-session",
                session_head="head-2",
                status="completed",
                text="done",
                approval_summary=None,
            )

    controller = RuntimeSmokeController()
    gateway = Gateway(config, store, controller)
    batches = [
        {
            "ret": 0,
            "get_updates_buf": "cursor-1",
            "msgs": [
                {
                    "message_id": 1,
                    "from_user_id": "owner-1",
                    "to_user_id": "bot-1",
                    "message_state": 2,
                    "context_token": "ctx-1",
                    "item_list": [{"type": 1, "text_item": {"text": "/add code 019-code"}}],
                }
            ],
        },
        {
            "ret": 0,
            "get_updates_buf": "cursor-2",
            "msgs": [
                {
                    "message_id": 2,
                    "from_user_id": "owner-1",
                    "to_user_id": "bot-1",
                    "message_state": 2,
                    "context_token": "ctx-2",
                    "item_list": [{"type": 1, "text_item": {"text": "/use code"}}],
                }
            ],
        },
        {
            "ret": 0,
            "get_updates_buf": "cursor-3",
            "msgs": [
                {
                    "message_id": 3,
                    "from_user_id": "owner-1",
                    "to_user_id": "bot-1",
                    "message_state": 2,
                    "context_token": "ctx-3",
                    "item_list": [{"type": 1, "text_item": {"text": "please continue"}}],
                }
            ],
        },
    ]

    class BatchClient(FakeIlinkClient):
        def get_updates(self, cursor: str, timeout_seconds: Optional[float] = None) -> dict:
            return batches.pop(0)

    client = BatchClient({})
    runtime = OpeniLinkRuntime(client=client, gateway=gateway, store=store, owner_user_ids={"owner-1"})

    runtime.process_one_batch()
    runtime.process_one_batch()
    runtime.process_one_batch()

    assert client.sent == [
        ("owner-1", "Added alias code -> 019-code"),
        ("owner-1", "Using alias code"),
        ("owner-1", "done"),
    ]
    assert controller.starts[0]["message"] == "please continue"
    assert store.get_runtime_state("ilink.cursor") == "cursor-3"
```

- [ ] **Step 2: Run the smoke test**

Run:

```bash
python3 -m pytest tests/test_openilink_runtime.py::test_runtime_smoke_add_use_and_dispatch_with_fake_controller -q
```

Expected: pass.

- [ ] **Step 3: Run full suite**

Run:

```bash
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_openilink_runtime.py
git commit -m "test: cover openilink runtime dispatch smoke"
```

## Task 7: Operator Runbook And README Update

**Files:**
- Create: `docs/03_v0.3_OpeniLink运行说明.md`
- Modify: `README.md`

- [ ] **Step 1: Write the runbook**

Create `docs/03_v0.3_OpeniLink运行说明.md`:

```markdown
# CodexThreadBridge v0.3 OpeniLink 运行说明

v0.3 增加真实微信私聊文本 runtime。Gateway Core 仍然负责 alias、权限、Codex session dispatch 和安全边界；OpeniLink runtime 只负责 iLink/OpenClaw-WeChat 协议收发。

## 本地配置

复制 `.env.example` 为 `.env`，填入本机值。`.env` 已被 `.gitignore` 排除，不能提交。

必填项：

```text
CTB_PROJECT_ROOT=/Users/clngs/Documents/CLngs_Vault/CodexThreadBridge
ILINK_BASE_URL=<本机或通道提供的 iLink base URL>
ILINK_BOT_TOKEN=<本地 token>
ILINK_OWNER_USER_IDS=<你的微信 sender id，多个用英文逗号分隔>
CTB_CONTROLLER_COMMAND=<cross-thread-controller MCP 启动命令，逗号分隔>
```

## 私聊 smoke

先处理一批消息后退出：

```bash
set -a
source .env
set +a
PYTHONPATH=src python3 -m codex_thread_bridge.adapters.openilink_runtime --once
```

常驻运行：

```bash
set -a
source .env
set +a
PYTHONPATH=src python3 -m codex_thread_bridge.adapters.openilink_runtime
```

微信私聊测试顺序：

```text
/help
/list
/add code <session_id>
/use code
请只回复 bridge real runtime ok
```

## 安全边界

- v0.3 只启用私聊文本链路。
- 非 owner sender id 会被 Gateway 拒绝。
- 群聊 runtime 不在 v0.3 启用。
- 文件上传不在 v0.3 启用。
- `/list`、`/status`、`/refresh` 不创建 Codex 模型 turn。
- runtime 轮询微信通道不等于 Codex 心跳，不消耗 Codex 模型额度。
- Codex 工具审批仍回到 Codex App 内完成。
```

- [ ] **Step 2: Update README current status**

Add a v0.3 paragraph under `## Current Status` in `README.md`:

```markdown
v0.3 adds the first real iLink/OpenClaw-WeChat private-chat runtime. It keeps
the v0.2 Gateway Core policy boundary and wires real text updates through an
iLink-compatible HTTP client. Media upload, group runtime activation, Windows
packaging, and launchd autostart remain outside v0.3.
```

- [ ] **Step 3: Run documentation grep and tests**

Run:

```bash
grep -R -n "ILINK_BOT_TOKEN=.*sk-" README.md docs .env.example src tests
python3 -m pytest -q
```

Expected: grep returns no matches; tests pass.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/03_v0.3_OpeniLink运行说明.md .env.example
git commit -m "docs: add openilink runtime runbook"
```

## Task 8: Final Verification And Review Prep

**Files:**
- No source edits expected unless verification finds a concrete defect.

- [ ] **Step 1: Run placeholder and secret scans**

Run:

```bash
grep -R "T[B]D\\|T[O]DO\\|FIX[M]E" -n README.md docs/03_v0.3_OpeniLink运行说明.md docs/superpowers/specs src tests .env.example
```

Expected: no placeholder markers; runtime token placeholders in docs and `.env.example` must remain fake examples only. Sanitization token examples may appear only in tests.

- [ ] **Step 2: Run full tests**

Run:

```bash
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Run local simulator regression**

Run:

```bash
PYTHONPATH=src python3 -m codex_thread_bridge.adapters.local --project-root /private/tmp/ctb_v03_smoke
```

Input:

```text
/add code 019-code
/use code
请只回复 bridge smoke ok
```

Expected output includes:

```text
Added alias code -> 019-code
Using alias code
LOCAL: 请只回复 bridge smoke ok
```

- [ ] **Step 4: Summarize final state**

Collect:

```bash
git log --oneline --max-count=8
git status --short --branch
```

Expected: branch is clean except ignored local files, and recent commits correspond to Tasks 1-7.

- [ ] **Step 5: Request code review**

Use `superpowers:requesting-code-review` before claiming the implementation is complete. The review should focus on:

- token leakage paths
- cursor advancement correctness
- idempotency and duplicate delivery risk
- non-owner rejection path
- no Codex heartbeat
- no group QA runtime activation in v0.3
- no accidental media upload implementation

## Execution Recommendation

Use Subagent-Driven execution for implementation. Good split:

- Worker 1: config + store state
- Worker 2: iLink HTTP client + event mapper
- Worker 3: runtime processor + fake smoke
- Main agent: docs, integration review, final verification

The write sets are mostly disjoint, but `openilink_runtime.py` depends on Worker 1 and Worker 2 outputs. If running in parallel, keep Worker 3 blocked until those two are complete, or assign Worker 3 to prepare only tests first.
