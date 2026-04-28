# CodexThreadBridge v0.2 Mobile Agent Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a WeChat-first mobile Agent console that can dispatch owner private-chat messages to existing Codex sessions, provide isolated group QA, surface Codex approval summaries, and safely deliver generated artifacts.

**Architecture:** Implement a platform-neutral Python Gateway Core with SQLite-backed state, a cross-thread-controller client boundary, a local simulator, and an OpeniLink-compatible WeChat channel adapter. Keep WeChat protocol details outside Gateway Core; Gateway Core owns alias routing, policy decisions, group authorization, controller calls, and file-sending safety.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`, `dataclasses`, `argparse`, `json`, `subprocess` for MCP stdio, `pytest` for tests, OpeniLink Hub WebSocket for inbound WeChat messages, and an OpeniLink SDK/REST wrapper for outbound text/file sends.

---

## Scope Check

This plan implements one vertical v0.2 product slice: Gateway Core, local simulator, controller boundary, WeChat channel contract, owner private-chat dispatch, group QA isolation, and artifact delivery safety. Feishu UI, Windows packaging, and mobile approval-confirmation proxy are deliberately excluded from this plan and remain future adapters.

The current project is not a git repository. Do not initialize git unless the user asks. Each task still includes a checkpoint step; if a git repository exists at execution time, use the commit command shown there.

## Source References

- Spec: `docs/superpowers/specs/2026-04-28-mobile-agent-console-design.md`
- Existing scaffold: `README.md`, `docs/00_设计草稿.md`, `docs/01_MVP实施方案.md`
- Controller project: `/Users/clngs/Documents/Codex/tools/cross-thread-controller/`
- OpeniLink references checked during planning:
  - `https://openilink.com/`
  - `https://github.com/openilink/openilink-hub`
  - `https://openilink.com/docs`

## File Structure

Create this package structure:

```text
CodexThreadBridge/
├── pyproject.toml
├── src/
│   └── codex_thread_bridge/
│       ├── __init__.py
│       ├── config.py
│       ├── models.py
│       ├── stores.py
│       ├── commands.py
│       ├── policy.py
│       ├── gateway.py
│       ├── controller_client.py
│       ├── refresh.py
│       ├── artifacts.py
│       └── adapters/
│           ├── __init__.py
│           ├── local.py
│           ├── wechat_channel.py
│           └── openilink.py
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   └── openilink_text_message.json
    ├── test_models.py
    ├── test_stores.py
    ├── test_commands.py
    ├── test_policy.py
    ├── test_gateway_private.py
    ├── test_gateway_group.py
    ├── test_artifacts.py
    ├── test_refresh.py
    └── test_openilink_adapter.py
```

Responsibilities:

- `models.py`: stable dataclasses/enums shared across the Gateway.
- `config.py`: load paths, owner IDs, allowed roots, and controller defaults.
- `stores.py`: SQLite schema and repository methods.
- `commands.py`: parse private and group command text.
- `policy.py`: owner/group/artifact safety decisions.
- `controller_client.py`: cross-thread-controller boundary plus fake client for tests.
- `gateway.py`: route `IncomingMessage` into commands, Codex dispatch, group QA, and replies.
- `refresh.py`: read local Codex JSONL history for `/refresh`.
- `artifacts.py`: detect and gate outbound files.
- `adapters/local.py`: command-line simulator for private/group events.
- `adapters/wechat_channel.py`: minimal WeChat channel protocol interface.
- `adapters/openilink.py`: OpeniLink Hub v1 event normalization and reply/file send wrapper.

## Task 1: Project Scaffold And Test Harness

**Files:**
- Create: `pyproject.toml`
- Create: `src/codex_thread_bridge/__init__.py`
- Create: `src/codex_thread_bridge/config.py`
- Create: `tests/conftest.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the scaffold files**

Create `pyproject.toml`:

```toml
[project]
name = "codex-thread-bridge"
version = "0.2.0"
description = "Local gateway for routing mobile chat messages to Codex sessions"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

Create `src/codex_thread_bridge/__init__.py`:

```python
__all__ = ["__version__"]
__version__ = "0.2.0"
```

Create `src/codex_thread_bridge/config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class BridgeConfig:
    project_root: Path
    data_dir: Path
    sqlite_path: Path
    attachments_dir: Path
    artifact_roots: tuple[Path, ...]
    owner_user_ids: frozenset[str]
    group_qa_cwd: Path
    default_group_model: str | None = None
    default_group_effort: str | None = None
    max_artifact_bytes: int = 25 * 1024 * 1024
    sensitive_path_markers: tuple[str, ...] = field(
        default=(".ssh", ".codex", ".env", "keychain", "Library/Application Support")
    )

    @classmethod
    def local_dev(cls, project_root: Path, owner_user_ids: set[str]) -> "BridgeConfig":
        data_dir = project_root / "data"
        attachments_dir = data_dir / "attachments"
        return cls(
            project_root=project_root,
            data_dir=data_dir,
            sqlite_path=data_dir / "bridge.sqlite3",
            attachments_dir=attachments_dir,
            artifact_roots=(project_root / "exports",),
            owner_user_ids=frozenset(owner_user_ids),
            group_qa_cwd=project_root,
        )
```

Create `tests/conftest.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from codex_thread_bridge.config import BridgeConfig


@pytest.fixture
def bridge_config(tmp_path: Path) -> BridgeConfig:
    return BridgeConfig.local_dev(tmp_path, {"owner-1"})
```

- [ ] **Step 2: Run the empty harness**

Run:

```bash
python -m pytest -q
```

Expected: `no tests ran` or a pass once Task 2 tests are added. If `pytest` is missing, run only after installing the dev extra in the implementation environment.

- [ ] **Step 3: Checkpoint**

Run:

```bash
find . -maxdepth 3 -type f | sort
```

Expected: scaffold files exist under `src/`, `tests/`, and `pyproject.toml`. If the project has git initialized, commit with:

```bash
git add pyproject.toml src/codex_thread_bridge/__init__.py src/codex_thread_bridge/config.py tests/conftest.py
git commit -m "chore: add Python project scaffold"
```

## Task 2: Core Models

**Files:**
- Create: `src/codex_thread_bridge/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write model tests**

Create `tests/test_models.py`:

```python
from __future__ import annotations

from codex_thread_bridge.models import (
    AttachmentRef,
    ConversationType,
    ExecutionPolicy,
    IncomingMessage,
    SenderRole,
)


def test_incoming_message_context_key_includes_platform_and_thread() -> None:
    msg = IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.PRIVATE,
        conversation_id="private-owner",
        thread_key="private-owner",
        sender_id="owner-1",
        sender_role=SenderRole.OWNER,
        text="/list",
        attachments=(),
        raw_ref="m-1",
    )

    assert msg.context_key == ("wechat", "private", "private-owner", "private-owner")


def test_execution_policy_defaults_keep_codex_approval_available() -> None:
    policy = ExecutionPolicy.work_default(default_cwd="/tmp/work")

    assert policy.sandbox == "workspace-write"
    assert policy.approval_policy == "on-request"
    assert policy.writable_roots == ("/tmp/work",)


def test_attachment_ref_records_direction() -> None:
    ref = AttachmentRef(
        source_message_id="m-1",
        local_path="/tmp/a.png",
        mime_type="image/png",
        original_name="a.png",
        direction="inbound",
    )

    assert ref.direction == "inbound"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_models.py -q
```

Expected: import failure for `codex_thread_bridge.models`.

- [ ] **Step 3: Implement models**

Create `src/codex_thread_bridge/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class ConversationType(str, Enum):
    PRIVATE = "private"
    GROUP = "group"
    FEISHU_THREAD = "future_feishu_thread"


class SenderRole(str, Enum):
    OWNER = "owner"
    MEMBER = "member"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AttachmentRef:
    source_message_id: str
    local_path: str
    mime_type: str
    original_name: str
    direction: Literal["inbound", "outbound"]


@dataclass(frozen=True)
class IncomingMessage:
    platform: str
    conversation_type: ConversationType
    conversation_id: str
    thread_key: str
    sender_id: str
    sender_role: SenderRole
    text: str
    attachments: tuple[AttachmentRef, ...]
    raw_ref: str

    @property
    def context_key(self) -> tuple[str, str, str, str]:
        return (
            self.platform,
            self.conversation_type.value,
            self.conversation_id,
            self.thread_key,
        )


@dataclass(frozen=True)
class OutgoingMessage:
    conversation_id: str
    text: str
    attachments: tuple[AttachmentRef, ...] = ()


@dataclass(frozen=True)
class ExecutionPolicy:
    sandbox: str
    approval_policy: str
    writable_roots: tuple[str, ...]
    model: str | None = None
    effort: str | None = None

    @classmethod
    def work_default(cls, default_cwd: str) -> "ExecutionPolicy":
        return cls(
            sandbox="workspace-write",
            approval_policy="on-request",
            writable_roots=(default_cwd,),
        )

    @classmethod
    def group_qa(cls) -> "ExecutionPolicy":
        return cls(sandbox="read-only", approval_policy="never", writable_roots=())


@dataclass(frozen=True)
class ThreadAlias:
    alias: str
    session_id: str
    label: str
    default_cwd: str
    policy: ExecutionPolicy
```

- [ ] **Step 4: Run tests**

Run:

```bash
python -m pytest tests/test_models.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Checkpoint**

If git is initialized:

```bash
git add src/codex_thread_bridge/models.py tests/test_models.py
git commit -m "feat: add bridge domain models"
```

## Task 3: SQLite Store

**Files:**
- Create: `src/codex_thread_bridge/stores.py`
- Create: `tests/test_stores.py`

- [ ] **Step 1: Write store tests**

Create `tests/test_stores.py`:

```python
from __future__ import annotations

from codex_thread_bridge.models import ExecutionPolicy
from codex_thread_bridge.stores import BridgeStore


def test_alias_context_and_group_lifecycle(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()

    store.upsert_alias(
        alias="code",
        session_id="019-code",
        label="Code",
        default_cwd="/tmp/project",
        policy=ExecutionPolicy.work_default("/tmp/project"),
        created_by="owner-1",
    )
    alias = store.get_alias("code")
    assert alias is not None
    assert alias.session_id == "019-code"
    assert alias.policy.approval_policy == "on-request"

    store.set_active_alias(("wechat", "private", "owner-chat", "owner-chat"), "code", "owner-1")
    assert store.get_active_alias(("wechat", "private", "owner-chat", "owner-chat")) == "code"

    store.record_pending_group(group_id="group-1", group_alias="friends", created_by="owner-1")
    group = store.get_group_by_alias("friends")
    assert group["status"] == "pending"
    assert group["qa_session_id"] is None

    store.activate_group("friends", "019-group-qa")
    group = store.get_group_by_id("group-1")
    assert group["status"] == "active"
    assert group["qa_session_id"] == "019-group-qa"


def test_artifact_statuses_are_persisted(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()

    artifact_id = store.record_artifact(
        run_id="run-1",
        alias="code",
        session_id="019-code",
        local_path="/tmp/project/exports/report.md",
        mime_type="text/markdown",
        size_bytes=10,
        status="allowed",
        reason="created during current run",
    )

    artifacts = store.list_artifacts("code")
    assert artifacts[0]["id"] == artifact_id
    assert artifacts[0]["status"] == "allowed"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_stores.py -q
```

Expected: import failure for `codex_thread_bridge.stores`.

- [ ] **Step 3: Implement store**

Create `src/codex_thread_bridge/stores.py` with a `BridgeStore` that creates these tables: `thread_aliases`, `contexts`, `wechat_groups`, `attachments`, `artifacts`, `events`.

The public methods required by later tasks are:

```python
class BridgeStore:
    def __init__(self, sqlite_path: Path): ...
    def initialize(self) -> None: ...
    def upsert_alias(self, alias, session_id, label, default_cwd, policy, created_by) -> None: ...
    def get_alias(self, alias: str) -> ThreadAlias | None: ...
    def list_aliases(self) -> list[ThreadAlias]: ...
    def remove_alias(self, alias: str) -> bool: ...
    def set_active_alias(self, context_key, alias: str, owner_user_id: str) -> None: ...
    def get_active_alias(self, context_key) -> str | None: ...
    def record_pending_group(self, group_id: str, group_alias: str, created_by: str) -> None: ...
    def activate_group(self, group_alias: str, qa_session_id: str) -> None: ...
    def disable_group(self, group_alias: str) -> None: ...
    def get_group_by_alias(self, group_alias: str) -> dict | None: ...
    def get_group_by_id(self, group_id: str) -> dict | None: ...
    def list_groups(self) -> list[dict]: ...
    def get_refresh_offset(self, alias: str) -> int: ...
    def set_refresh_offset(self, alias: str, line_number: int) -> None: ...
    def record_artifact(self, run_id, alias, session_id, local_path, mime_type, size_bytes, status, reason) -> int: ...
    def list_artifacts(self, alias: str | None = None) -> list[dict]: ...
```

Use JSON strings for `writable_roots` and store timestamps as Unix seconds.

- [ ] **Step 4: Run store tests**

Run:

```bash
python -m pytest tests/test_stores.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Checkpoint**

If git is initialized:

```bash
git add src/codex_thread_bridge/stores.py tests/test_stores.py
git commit -m "feat: add SQLite bridge store"
```

## Task 4: Command Parser And Policy Engine

**Files:**
- Create: `src/codex_thread_bridge/commands.py`
- Create: `src/codex_thread_bridge/policy.py`
- Create: `tests/test_commands.py`
- Create: `tests/test_policy.py`

- [ ] **Step 1: Write parser tests**

Create `tests/test_commands.py`:

```python
from __future__ import annotations

from codex_thread_bridge.commands import CommandKind, parse_command
from codex_thread_bridge.models import ConversationType


def test_parse_private_add_and_send_commands() -> None:
    add = parse_command("/add code 019-code", ConversationType.PRIVATE)
    assert add.kind == CommandKind.ADD_ALIAS
    assert add.args == ("code", "019-code")

    send = parse_command("/send paper continue the plan", ConversationType.PRIVATE)
    assert send.kind == CommandKind.SEND_ONCE
    assert send.args == ("paper", "continue the plan")


def test_plain_private_message_is_not_a_command() -> None:
    parsed = parse_command("continue from the last step", ConversationType.PRIVATE)
    assert parsed.kind == CommandKind.PLAIN_TEXT
    assert parsed.args == ("continue from the last step",)


def test_group_rejects_work_commands() -> None:
    parsed = parse_command("@Bot /use code", ConversationType.GROUP)
    assert parsed.kind == CommandKind.GROUP_FORBIDDEN_COMMAND
```

Create `tests/test_policy.py`:

```python
from __future__ import annotations

from codex_thread_bridge.models import ConversationType, IncomingMessage, SenderRole
from codex_thread_bridge.policy import PolicyEngine


def test_owner_private_message_is_allowed(bridge_config) -> None:
    policy = PolicyEngine(bridge_config)
    msg = IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.PRIVATE,
        conversation_id="owner-chat",
        thread_key="owner-chat",
        sender_id="owner-1",
        sender_role=SenderRole.OWNER,
        text="/list",
        attachments=(),
        raw_ref="m-1",
    )
    assert policy.can_use_private_console(msg).allowed is True


def test_group_cannot_dispatch_work_alias(bridge_config) -> None:
    policy = PolicyEngine(bridge_config)
    msg = IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.GROUP,
        conversation_id="group-1",
        thread_key="group-1",
        sender_id="owner-1",
        sender_role=SenderRole.OWNER,
        text="@Bot /send code run tests",
        attachments=(),
        raw_ref="m-2",
    )
    assert policy.can_group_dispatch_work(msg).allowed is False
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_commands.py tests/test_policy.py -q
```

Expected: import failures for `commands` and `policy`.

- [ ] **Step 3: Implement command parser and policy engine**

Create `commands.py` with `CommandKind`, `ParsedCommand`, and `parse_command(text, conversation_type)`.

Required command kinds:

```python
ADD_ALIAS
USE_ALIAS
LIST_ALIASES
REMOVE_ALIAS
STATUS
REFRESH
SEND_ONCE
ARTIFACTS
SEND_FILE
HELP
BIND_COMPAT
GROUP_PENDING
GROUP_APPROVE
GROUP_LIST
GROUP_STATUS
GROUP_RESET
GROUP_DISABLE
GROUP_QA_STATUS
GROUP_FORBIDDEN_COMMAND
PLAIN_TEXT
```

Create `policy.py` with:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""


class PolicyEngine:
    def __init__(self, config):
        self.config = config

    def can_use_private_console(self, msg):
        if msg.conversation_type.value != "private":
            return PolicyDecision(False, "private console only")
        if msg.sender_id not in self.config.owner_user_ids:
            return PolicyDecision(False, "sender is not owner")
        return PolicyDecision(True)

    def can_group_dispatch_work(self, msg):
        return PolicyDecision(False, "group chat cannot dispatch work aliases")
```

- [ ] **Step 4: Run parser and policy tests**

Run:

```bash
python -m pytest tests/test_commands.py tests/test_policy.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Checkpoint**

If git is initialized:

```bash
git add src/codex_thread_bridge/commands.py src/codex_thread_bridge/policy.py tests/test_commands.py tests/test_policy.py
git commit -m "feat: add command parser and policy engine"
```

## Task 5: Controller Client Boundary

**Files:**
- Create: `src/codex_thread_bridge/controller_client.py`
- Create: `tests/test_gateway_private.py`

- [ ] **Step 1: Write fake-controller contract tests**

Create `tests/test_gateway_private.py` with the initial controller fake:

```python
from __future__ import annotations

from dataclasses import dataclass, field

from codex_thread_bridge.controller_client import ControllerRunResult


@dataclass
class FakeControllerClient:
    starts: list[dict] = field(default_factory=list)
    status_by_session: dict[str, dict] = field(default_factory=dict)

    def status(self, session_id: str) -> dict:
        return self.status_by_session.get(
            session_id,
            {"session_id": session_id, "locked": False, "dirty": False, "reconcile_required": False, "session_head": "head-1"},
        )

    def start_or_send(self, **kwargs) -> ControllerRunResult:
        self.starts.append(kwargs)
        return ControllerRunResult(
            run_id="run-1",
            session_id=kwargs["session_id"],
            session_head="head-2",
            status="completed",
            text="done",
            approval_summary=None,
        )


def test_controller_result_model() -> None:
    result = ControllerRunResult(
        run_id="run-1",
        session_id="019-code",
        session_head="head-2",
        status="completed",
        text="done",
        approval_summary=None,
    )
    assert result.text == "done"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_gateway_private.py -q
```

Expected: import failure for `controller_client`.

- [ ] **Step 3: Implement controller client types**

Create `src/codex_thread_bridge/controller_client.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from codex_thread_bridge.models import ExecutionPolicy


@dataclass(frozen=True)
class ControllerRunResult:
    run_id: str
    session_id: str
    session_head: str
    status: str
    text: str
    approval_summary: str | None


class ControllerClient(Protocol):
    def status(self, session_id: str) -> dict: ...

    def start_or_send(
        self,
        *,
        session_id: str | None,
        cwd: str,
        message: str,
        owner: str,
        policy: ExecutionPolicy,
        idempotency_key: str,
        expected_session_head: str | None,
    ) -> ControllerRunResult: ...
```

Leave stdio JSON-RPC implementation for Task 11; keep Gateway testable against this protocol first.

- [ ] **Step 4: Run controller contract tests**

Run:

```bash
python -m pytest tests/test_gateway_private.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Checkpoint**

If git is initialized:

```bash
git add src/codex_thread_bridge/controller_client.py tests/test_gateway_private.py
git commit -m "feat: add controller client boundary"
```

## Task 6: Gateway Private Console Flow

**Files:**
- Create: `src/codex_thread_bridge/gateway.py`
- Modify: `tests/test_gateway_private.py`

- [ ] **Step 1: Add private dispatch tests**

Append to `tests/test_gateway_private.py`:

```python
from codex_thread_bridge.gateway import Gateway
from codex_thread_bridge.models import ConversationType, ExecutionPolicy, IncomingMessage, SenderRole
from codex_thread_bridge.stores import BridgeStore


def private_msg(text: str) -> IncomingMessage:
    return IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.PRIVATE,
        conversation_id="owner-chat",
        thread_key="owner-chat",
        sender_id="owner-1",
        sender_role=SenderRole.OWNER,
        text=text,
        attachments=(),
        raw_ref="m-private",
    )


def test_private_add_use_and_plain_dispatch(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()
    controller = FakeControllerClient()
    gateway = Gateway(bridge_config, store, controller)

    add_reply = gateway.handle(private_msg("/add code 019-code"))
    assert "code" in add_reply.text

    use_reply = gateway.handle(private_msg("/use code"))
    assert "code" in use_reply.text

    work_reply = gateway.handle(private_msg("continue implementation"))
    assert work_reply.text == "done"
    assert controller.starts[0]["session_id"] == "019-code"
    assert controller.starts[0]["policy"].approval_policy == "on-request"


def test_private_plain_message_without_active_alias_is_rejected(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()
    gateway = Gateway(bridge_config, store, FakeControllerClient())

    reply = gateway.handle(private_msg("hello"))

    assert "/use" in reply.text
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_gateway_private.py -q
```

Expected: import failure for `gateway`.

- [ ] **Step 3: Implement Gateway private commands**

Create `src/codex_thread_bridge/gateway.py` with:

```python
from __future__ import annotations

from codex_thread_bridge.commands import CommandKind, parse_command
from codex_thread_bridge.models import ExecutionPolicy, IncomingMessage, OutgoingMessage, ThreadAlias
from codex_thread_bridge.policy import PolicyEngine


class Gateway:
    def __init__(self, config, store, controller):
        self.config = config
        self.store = store
        self.controller = controller
        self.policy = PolicyEngine(config)

    def handle(self, msg: IncomingMessage) -> OutgoingMessage:
        if msg.conversation_type.value == "group":
            return self._handle_group(msg)
        decision = self.policy.can_use_private_console(msg)
        if not decision.allowed:
            return OutgoingMessage(msg.conversation_id, f"Rejected: {decision.reason}")
        command = parse_command(msg.text, msg.conversation_type)
        if command.kind == CommandKind.ADD_ALIAS:
            alias, session_id = command.args
            cwd = str(self.config.project_root)
            self.store.upsert_alias(
                alias=alias,
                session_id=session_id,
                label=alias,
                default_cwd=cwd,
                policy=ExecutionPolicy.work_default(cwd),
                created_by=msg.sender_id,
            )
            return OutgoingMessage(msg.conversation_id, f"Added alias: {alias}")
        if command.kind == CommandKind.USE_ALIAS:
            alias = command.args[0]
            if self.store.get_alias(alias) is None:
                return OutgoingMessage(msg.conversation_id, f"Unknown alias: {alias}")
            self.store.set_active_alias(msg.context_key, alias, msg.sender_id)
            return OutgoingMessage(msg.conversation_id, f"Current thread: {alias}")
        if command.kind == CommandKind.LIST_ALIASES:
            aliases = self.store.list_aliases()
            text = "\n".join(f"{item.alias} -> {item.session_id}" for item in aliases) or "No aliases."
            return OutgoingMessage(msg.conversation_id, text)
        if command.kind == CommandKind.PLAIN_TEXT:
            return self._dispatch_to_active_alias(msg, command.args[0])
        return OutgoingMessage(msg.conversation_id, "Command recognized but not available in this task yet.")

    def _dispatch_to_active_alias(self, msg: IncomingMessage, text: str) -> OutgoingMessage:
        alias_name = self.store.get_active_alias(msg.context_key)
        if not alias_name:
            return OutgoingMessage(msg.conversation_id, "No active thread. Use /use <alias> first.")
        alias = self.store.get_alias(alias_name)
        if alias is None:
            return OutgoingMessage(msg.conversation_id, f"Active alias no longer exists: {alias_name}")
        status = self.controller.status(alias.session_id)
        if status.get("locked") or status.get("dirty") or status.get("reconcile_required"):
            return OutgoingMessage(msg.conversation_id, f"{alias.alias} is not ready. Run /status {alias.alias}.")
        result = self.controller.start_or_send(
            session_id=alias.session_id,
            cwd=alias.default_cwd,
            message=text,
            owner=f"ctb-private:{msg.sender_id}",
            policy=alias.policy,
            idempotency_key=f"{msg.raw_ref}:{alias.alias}",
            expected_session_head=status.get("session_head"),
        )
        if result.approval_summary:
            return OutgoingMessage(msg.conversation_id, result.approval_summary)
        return OutgoingMessage(msg.conversation_id, result.text)

    def _handle_group(self, msg: IncomingMessage) -> OutgoingMessage:
        return OutgoingMessage(msg.conversation_id, "Group handling is added in a later task.")
```

- [ ] **Step 4: Run private gateway tests**

Run:

```bash
python -m pytest tests/test_gateway_private.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Checkpoint**

If git is initialized:

```bash
git add src/codex_thread_bridge/gateway.py tests/test_gateway_private.py
git commit -m "feat: route private messages to active Codex aliases"
```

## Task 7: Group QA Authorization And Isolation

**Files:**
- Modify: `src/codex_thread_bridge/gateway.py`
- Create: `tests/test_gateway_group.py`

- [ ] **Step 1: Write group tests**

Create `tests/test_gateway_group.py`:

```python
from __future__ import annotations

from tests.test_gateway_private import FakeControllerClient

from codex_thread_bridge.gateway import Gateway
from codex_thread_bridge.models import ConversationType, IncomingMessage, SenderRole
from codex_thread_bridge.stores import BridgeStore


def group_msg(text: str, sender: str = "member-1") -> IncomingMessage:
    return IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.GROUP,
        conversation_id="group-1",
        thread_key="group-1",
        sender_id=sender,
        sender_role=SenderRole.MEMBER,
        text=text,
        attachments=(),
        raw_ref="m-group",
    )


def owner_private(text: str) -> IncomingMessage:
    return IncomingMessage(
        platform="wechat",
        conversation_type=ConversationType.PRIVATE,
        conversation_id="owner-chat",
        thread_key="owner-chat",
        sender_id="owner-1",
        sender_role=SenderRole.OWNER,
        text=text,
        attachments=(),
        raw_ref="m-owner",
    )


def test_unapproved_group_records_pending_without_codex_call(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()
    controller = FakeControllerClient()
    gateway = Gateway(bridge_config, store, controller)

    reply = gateway.handle(group_msg("@Bot hello"))

    assert "not enabled" in reply.text
    assert controller.starts == []


def test_owner_approves_group_and_group_uses_read_only_qa_session(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()
    controller = FakeControllerClient()
    gateway = Gateway(bridge_config, store, controller)

    gateway.handle(group_msg("@Bot hello"))
    approve_reply = gateway.handle(owner_private("/group approve group-1 friends"))
    assert "friends" in approve_reply.text

    qa_reply = gateway.handle(group_msg("@Bot what is this project?"))

    assert qa_reply.text == "done"
    assert controller.starts[-1]["policy"].sandbox == "read-only"
    assert controller.starts[-1]["policy"].approval_policy == "never"


def test_group_work_command_is_forbidden_even_after_approval(bridge_config) -> None:
    store = BridgeStore(bridge_config.sqlite_path)
    store.initialize()
    gateway = Gateway(bridge_config, store, FakeControllerClient())

    gateway.handle(group_msg("@Bot hello"))
    gateway.handle(owner_private("/group approve group-1 friends"))
    reply = gateway.handle(group_msg("@Bot /send code run tests"))

    assert "cannot dispatch" in reply.text
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_gateway_group.py -q
```

Expected: group tests fail because `_handle_group` and `/group approve` are not implemented.

- [ ] **Step 3: Implement group management and QA routing**

Modify `Gateway.handle()` to recognize `GROUP_APPROVE`, `GROUP_LIST`, `GROUP_STATUS`, `GROUP_RESET`, and `GROUP_DISABLE` in private chat.

Implement `_handle_group()`:

```python
def _handle_group(self, msg: IncomingMessage) -> OutgoingMessage:
    command = parse_command(msg.text, msg.conversation_type)
    if command.kind == CommandKind.GROUP_FORBIDDEN_COMMAND:
        return OutgoingMessage(msg.conversation_id, "Group chat cannot dispatch work aliases.")
    group = self.store.get_group_by_id(msg.conversation_id)
    if group is None or group["status"] != "active":
        self.store.record_pending_group(msg.conversation_id, msg.conversation_id, "system")
        return OutgoingMessage(msg.conversation_id, "This group is not enabled. Ask the owner to approve it in private chat.")
    if command.kind == CommandKind.GROUP_QA_STATUS:
        return OutgoingMessage(msg.conversation_id, f"QA enabled: {group['group_alias']}")
    qa_session_id = group["qa_session_id"]
    result = self.controller.start_or_send(
        session_id=qa_session_id,
        cwd=str(self.config.group_qa_cwd),
        message=msg.text.replace("@Bot", "", 1).strip(),
        owner=f"ctb-group-qa:{group['group_alias']}",
        policy=ExecutionPolicy.group_qa(),
        idempotency_key=f"{msg.raw_ref}:{group['group_alias']}",
        expected_session_head=None,
    )
    return OutgoingMessage(msg.conversation_id, result.text)
```

For `/group approve group-1 friends`, call controller with `session_id=None` and `ExecutionPolicy.group_qa()`. Store the returned session id via `activate_group("friends", result.session_id)`. Group runtime lookup must use `get_group_by_id("group-1")`; owner management commands may use `get_group_by_alias("friends")`.

- [ ] **Step 4: Run group tests**

Run:

```bash
python -m pytest tests/test_gateway_group.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Checkpoint**

If git is initialized:

```bash
git add src/codex_thread_bridge/gateway.py tests/test_gateway_group.py
git commit -m "feat: add isolated WeChat group QA flow"
```

## Task 8: Artifact Detection And Safe Delivery

**Files:**
- Create: `src/codex_thread_bridge/artifacts.py`
- Create: `tests/test_artifacts.py`
- Modify: `src/codex_thread_bridge/gateway.py`

- [ ] **Step 1: Write artifact tests**

Create `tests/test_artifacts.py`:

```python
from __future__ import annotations

import os
import time
from pathlib import Path

from codex_thread_bridge.artifacts import ArtifactService


def test_detects_allowed_file_created_after_run_start(bridge_config, tmp_path: Path) -> None:
    exports = bridge_config.project_root / "exports"
    exports.mkdir()
    report = exports / "report.md"
    run_started_at = time.time()
    time.sleep(0.01)
    report.write_text("hello", encoding="utf-8")

    service = ArtifactService(bridge_config)
    candidates = service.detect("created /tmp path\n" + str(report), run_started_at)

    assert candidates[0].status == "allowed"
    assert candidates[0].path == report


def test_blocks_sensitive_or_old_paths(bridge_config, tmp_path: Path) -> None:
    secret = tmp_path / ".ssh" / "id_rsa"
    secret.parent.mkdir()
    secret.write_text("secret", encoding="utf-8")
    old_time = time.time() + 10

    service = ArtifactService(bridge_config)
    candidates = service.detect(str(secret), old_time)

    assert candidates[0].status == "blocked"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_artifacts.py -q
```

Expected: import failure for `artifacts`.

- [ ] **Step 3: Implement artifact service**

Create `src/codex_thread_bridge/artifacts.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


PATH_RE = re.compile(r"(/[^\\s`'\"]+)")


@dataclass(frozen=True)
class ArtifactCandidate:
    path: Path
    status: str
    reason: str
    mime_type: str
    size_bytes: int


class ArtifactService:
    def __init__(self, config):
        self.config = config

    def detect(self, text: str, run_started_at: float) -> list[ArtifactCandidate]:
        candidates: list[ArtifactCandidate] = []
        for match in PATH_RE.findall(text):
            path = Path(match).expanduser()
            if not path.exists() or not path.is_file():
                continue
            candidates.append(self._classify(path, run_started_at))
        return candidates

    def _classify(self, path: Path, run_started_at: float) -> ArtifactCandidate:
        resolved = path.resolve()
        size = resolved.stat().st_size
        reason = "created during current run"
        allowed_roots = tuple(root.resolve() for root in self.config.artifact_roots)
        if any(marker in str(resolved) for marker in self.config.sensitive_path_markers):
            return ArtifactCandidate(resolved, "blocked", "sensitive path marker", "application/octet-stream", size)
        if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
            return ArtifactCandidate(resolved, "blocked", "outside artifact roots", "application/octet-stream", size)
        if resolved.stat().st_mtime < run_started_at:
            return ArtifactCandidate(resolved, "blocked", "file predates current run", "application/octet-stream", size)
        if size > self.config.max_artifact_bytes:
            return ArtifactCandidate(resolved, "blocked", "file exceeds size limit", "application/octet-stream", size)
        return ArtifactCandidate(resolved, "allowed", reason, "application/octet-stream", size)
```

Wire `Gateway` private dispatch to call `ArtifactService.detect(result.text, run_started_at)` after controller returns. Record each candidate in `BridgeStore.record_artifact()`. `/artifacts` lists recent candidates. `/sendfile` returns text saying which allowed file would be sent; the actual OpeniLink file upload is wired in Task 12.

- [ ] **Step 4: Run artifact tests**

Run:

```bash
python -m pytest tests/test_artifacts.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Checkpoint**

If git is initialized:

```bash
git add src/codex_thread_bridge/artifacts.py tests/test_artifacts.py src/codex_thread_bridge/gateway.py
git commit -m "feat: detect and gate local artifacts"
```

## Task 9: Refresh From Local Codex JSONL

**Files:**
- Create: `src/codex_thread_bridge/refresh.py`
- Create: `tests/test_refresh.py`

- [ ] **Step 1: Write refresh tests**

Create `tests/test_refresh.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from codex_thread_bridge.refresh import read_new_items


def test_read_new_items_from_jsonl_after_line_offset(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    rows = [
        {"type": "response_item", "item": {"role": "user", "content": [{"text": "hi"}]}},
        {"type": "response_item", "item": {"role": "assistant", "content": [{"text": "hello"}]}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = read_new_items(path, last_seen_line=1)

    assert result.next_line == 2
    assert result.summary == "assistant: hello"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_refresh.py -q
```

Expected: import failure for `refresh`.

- [ ] **Step 3: Implement refresh reader**

Create `src/codex_thread_bridge/refresh.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RefreshResult:
    next_line: int
    summary: str


def read_new_items(path: Path, last_seen_line: int) -> RefreshResult:
    lines = path.read_text(encoding="utf-8").splitlines()
    summaries: list[str] = []
    for line in lines[last_seen_line:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = payload.get("item") or {}
        role = item.get("role")
        content = item.get("content") or []
        text_parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        text = " ".join(part for part in text_parts if part).strip()
        if role in {"user", "assistant"} and text:
            summaries.append(f"{role}: {text}")
    return RefreshResult(next_line=len(lines), summary="\n".join(summaries) or "No new messages.")
```

Add a `refresh_offsets` table in `BridgeStore` with `alias TEXT PRIMARY KEY` and `last_seen_line INTEGER NOT NULL`. Gateway `/refresh [alias]` must read the alias offset with `get_refresh_offset(alias)`, call `read_new_items()`, return the summary, and persist `set_refresh_offset(alias, result.next_line)` only after a successful read.

- [ ] **Step 4: Run refresh tests**

Run:

```bash
python -m pytest tests/test_refresh.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Checkpoint**

If git is initialized:

```bash
git add src/codex_thread_bridge/refresh.py tests/test_refresh.py
git commit -m "feat: read Codex history without model turns"
```

## Task 10: Local Simulator Adapter

**Files:**
- Create: `src/codex_thread_bridge/adapters/__init__.py`
- Create: `src/codex_thread_bridge/adapters/local.py`

- [ ] **Step 1: Implement local simulator CLI**

Create `src/codex_thread_bridge/adapters/local.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path

from codex_thread_bridge.config import BridgeConfig
from codex_thread_bridge.gateway import Gateway
from codex_thread_bridge.models import ConversationType, IncomingMessage, SenderRole
from codex_thread_bridge.stores import BridgeStore


def build_message(text: str, conversation_type: str, sender_id: str) -> IncomingMessage:
    ctype = ConversationType(conversation_type)
    conversation_id = "local-owner" if ctype is ConversationType.PRIVATE else "local-group"
    return IncomingMessage(
        platform="local",
        conversation_type=ctype,
        conversation_id=conversation_id,
        thread_key=conversation_id,
        sender_id=sender_id,
        sender_role=SenderRole.OWNER if sender_id == "owner-1" else SenderRole.MEMBER,
        text=text,
        attachments=(),
        raw_ref=f"local:{hash(text)}",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--sender-id", default="owner-1")
    parser.add_argument("--conversation-type", choices=["private", "group"], default="private")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    config = BridgeConfig.local_dev(project_root, {args.sender_id})
    store = BridgeStore(config.sqlite_path)
    store.initialize()

    from codex_thread_bridge.controller_client import ControllerRunResult

    class EchoController:
        def status(self, session_id: str) -> dict:
            return {"session_id": session_id, "locked": False, "dirty": False, "reconcile_required": False, "session_head": "local-head"}

        def start_or_send(self, **kwargs) -> ControllerRunResult:
            return ControllerRunResult("local-run", kwargs["session_id"] or "local-new-session", "local-head-2", "completed", "LOCAL: " + kwargs["message"], None)

    gateway = Gateway(config, store, EchoController())
    print("CodexThreadBridge local simulator. Ctrl-D to exit.")
    while True:
        try:
            text = input("> ")
        except EOFError:
            break
        reply = gateway.handle(build_message(text, args.conversation_type, args.sender_id))
        print(reply.text)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run simulator smoke**

Run:

```bash
python -m codex_thread_bridge.adapters.local --project-root /Users/clngs/Documents/CLngs_Vault/CodexThreadBridge
```

Manual input:

```text
/add code 019-code
/use code
hello
```

Expected output includes `Added alias: code`, `Current thread: code`, and `LOCAL: hello`.

- [ ] **Step 3: Run full tests**

Run:

```bash
python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Checkpoint**

If git is initialized:

```bash
git add src/codex_thread_bridge/adapters/__init__.py src/codex_thread_bridge/adapters/local.py
git commit -m "feat: add local simulator adapter"
```

## Task 11: MCP Stdio Controller Client

**Files:**
- Modify: `src/codex_thread_bridge/controller_client.py`
- Create: `tests/test_controller_client.py`

- [ ] **Step 1: Write serialization tests**

Create `tests/test_controller_client.py`:

```python
from __future__ import annotations

from codex_thread_bridge.controller_client import build_start_payload
from codex_thread_bridge.models import ExecutionPolicy


def test_build_start_payload_uses_alias_policy() -> None:
    payload = build_start_payload(
        session_id="019-code",
        cwd="/tmp/project",
        message="hello",
        owner="ctb-private:owner-1",
        policy=ExecutionPolicy.work_default("/tmp/project"),
        idempotency_key="m-1:code",
        expected_session_head="head-1",
    )

    assert payload["session_id"] == "019-code"
    assert payload["sandbox"] == "workspace-write"
    assert payload["approval_policy"] == "on-request"
    assert payload["writable_roots"] == ["/tmp/project"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_controller_client.py -q
```

Expected: import failure for `build_start_payload`.

- [ ] **Step 3: Implement payload builder and client skeleton**

Add to `controller_client.py`:

```python
def build_start_payload(*, session_id, cwd, message, owner, policy, idempotency_key, expected_session_head):
    return {
        "session_id": session_id,
        "cwd": cwd,
        "message": message,
        "owner": owner,
        "intent": "plan_review",
        "transport": "app_server",
        "plan_capability": "protocol",
        "sandbox": policy.sandbox,
        "approval_policy": policy.approval_policy,
        "writable_roots": list(policy.writable_roots),
        "model": policy.model,
        "effort": policy.effort,
        "idempotency_key": idempotency_key,
        "expected_session_head": expected_session_head,
    }
```

Then implement `McpControllerClient` with methods `status()` and `start_or_send()` using JSON-RPC stdio against the configured MCP command. Keep method parsing limited to the controller tools used in the spec:

```text
cross_thread_status
cross_thread_start
cross_thread_wait_any
cross_thread_read_result
cross_thread_delivery_ack
cross_thread_close
cross_thread_release
```

- [ ] **Step 4: Run controller client tests**

Run:

```bash
python -m pytest tests/test_controller_client.py -q
```

Expected: all serialization tests pass. Real MCP smoke is a manual verification step because it depends on the local controller process.

- [ ] **Step 5: Manual MCP smoke**

Use the known low-risk test session only after confirming it is not active in another workflow:

```bash
python -m codex_thread_bridge.adapters.local --project-root /Users/clngs/Documents/CLngs_Vault/CodexThreadBridge
```

Then configure the local adapter to use `McpControllerClient` and run:

```text
/add smoke 019dbdc6-4522-7a22-b54f-bf454f0de6d1
/use smoke
请只回复：bridge smoke ok
```

Expected: reply comes back, and controller state is released after delivery.

- [ ] **Step 6: Checkpoint**

If git is initialized:

```bash
git add src/codex_thread_bridge/controller_client.py tests/test_controller_client.py
git commit -m "feat: add cross-thread-controller MCP client"
```

## Task 12: OpeniLink Channel Adapter Boundary

**Files:**
- Create: `src/codex_thread_bridge/adapters/wechat_channel.py`
- Create: `src/codex_thread_bridge/adapters/openilink.py`
- Create: `tests/fixtures/openilink_text_message.json`
- Create: `tests/test_openilink_adapter.py`

- [ ] **Step 1: Write OpeniLink fixture**

Create `tests/fixtures/openilink_text_message.json`:

```json
{
  "message_id": "wx-m-1",
  "conversation_id": "owner-chat",
  "conversation_type": "private",
  "sender_id": "owner-1",
  "text": "/list",
  "mentions": [],
  "attachments": []
}
```

Create `tests/test_openilink_adapter.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from codex_thread_bridge.adapters.openilink import normalize_openilink_event
from codex_thread_bridge.models import ConversationType, SenderRole


def test_normalize_private_text_event() -> None:
    payload = json.loads(Path("tests/fixtures/openilink_text_message.json").read_text(encoding="utf-8"))

    msg = normalize_openilink_event(payload, owner_user_ids={"owner-1"})

    assert msg.platform == "wechat"
    assert msg.conversation_type == ConversationType.PRIVATE
    assert msg.sender_role == SenderRole.OWNER
    assert msg.text == "/list"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_openilink_adapter.py -q
```

Expected: import failure for `adapters.openilink`.

- [ ] **Step 3: Implement channel protocol and normalizer**

Create `adapters/wechat_channel.py`:

```python
from __future__ import annotations

from typing import Protocol

from codex_thread_bridge.models import AttachmentRef, IncomingMessage, OutgoingMessage


class WeChatChannelPort(Protocol):
    def iter_messages(self): ...
    def download_attachment(self, descriptor: dict) -> AttachmentRef: ...
    def send_text(self, conversation_id: str, text: str) -> None: ...
    def send_file(self, conversation_id: str, path: str, mime_type: str) -> None: ...
    def send_typing(self, conversation_id: str, enabled: bool) -> None: ...
```

Create `adapters/openilink.py`:

```python
from __future__ import annotations

from codex_thread_bridge.models import ConversationType, IncomingMessage, SenderRole


def normalize_openilink_event(payload: dict, owner_user_ids: set[str]) -> IncomingMessage:
    conversation_type = ConversationType(payload["conversation_type"])
    sender_id = payload["sender_id"]
    return IncomingMessage(
        platform="wechat",
        conversation_type=conversation_type,
        conversation_id=payload["conversation_id"],
        thread_key=payload.get("thread_key") or payload["conversation_id"],
        sender_id=sender_id,
        sender_role=SenderRole.OWNER if sender_id in owner_user_ids else SenderRole.MEMBER,
        text=payload.get("text", ""),
        attachments=(),
        raw_ref=payload["message_id"],
    )
```

Add this runtime adapter class in `adapters/openilink.py` so the real SDK/REST
client remains injectable and all OpeniLink-specific message shape logic stays
in one file:

```python
class OpeniLinkChannelAdapter:
    def __init__(self, *, client, owner_user_ids: set[str]):
        self.client = client
        self.owner_user_ids = owner_user_ids

    def iter_messages(self):
        for payload in self.client.iter_events():
            yield normalize_openilink_event(payload, self.owner_user_ids)

    def send_text(self, conversation_id: str, text: str) -> None:
        self.client.send_text(conversation_id=conversation_id, text=text)

    def send_file(self, conversation_id: str, path: str, mime_type: str) -> None:
        self.client.send_file(conversation_id=conversation_id, path=path, mime_type=mime_type)

    def send_typing(self, conversation_id: str, enabled: bool) -> None:
        if hasattr(self.client, "send_typing"):
            self.client.send_typing(conversation_id=conversation_id, enabled=enabled)
```

- [ ] **Step 4: Run adapter tests**

Run:

```bash
python -m pytest tests/test_openilink_adapter.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Checkpoint**

If git is initialized:

```bash
git add src/codex_thread_bridge/adapters/wechat_channel.py src/codex_thread_bridge/adapters/openilink.py tests/fixtures/openilink_text_message.json tests/test_openilink_adapter.py
git commit -m "feat: add OpeniLink channel adapter boundary"
```

## Task 13: Runtime Documentation And Verification

**Files:**
- Modify: `README.md`
- Create: `docs/02_v0.2运行说明.md`
- Modify: `docs/01_MVP实施方案.md`

- [ ] **Step 1: Update README with v0.2 status**

Add a v0.2 section to `README.md` covering:

```text
- WeChat-first mobile Agent console
- Owner private chat dispatches to existing Codex aliases
- Approved WeChat groups use isolated read-only QA sessions
- Status/refresh/list do not create model turns
- Artifacts are delivered only to owner private chat after safety checks
- Feishu and Windows are adapter targets, not v0.2 deliverables
```

- [ ] **Step 2: Write runbook**

Create `docs/02_v0.2运行说明.md` with these sections:

```markdown
# CodexThreadBridge v0.2 运行说明

## 本地模拟器

Run:

```bash
python -m codex_thread_bridge.adapters.local --project-root /Users/clngs/Documents/CLngs_Vault/CodexThreadBridge
```

Smoke commands:

```text
/add code <session_id>
/use code
请只回复 bridge smoke ok
```

## 微信入口

Use OpeniLink Hub WebSocket as the inbound message channel. Gateway receives
normalized events, routes them through Gateway Core, and sends replies through
the channel port.

## 安全边界

Private chat can dispatch work aliases. Group chat cannot dispatch work aliases,
approve actions, reset itself, or receive local files.
```
```

- [ ] **Step 3: Run full verification**

Run:

```bash
python -m pytest -q
```

Expected: all tests pass.

Run local simulator smoke:

```bash
python -m codex_thread_bridge.adapters.local --project-root /Users/clngs/Documents/CLngs_Vault/CodexThreadBridge
```

Expected manual behavior:

```text
/add code 019-code       -> Added alias: code
/use code                -> Current thread: code
hello                    -> LOCAL: hello
```

- [ ] **Step 4: Checkpoint**

If git is initialized:

```bash
git add README.md docs/01_MVP实施方案.md docs/02_v0.2运行说明.md
git commit -m "docs: document v0.2 mobile agent console"
```

## Final Acceptance Checklist

- [ ] `python -m pytest -q` passes.
- [ ] Local simulator can add an alias, switch to it, and dispatch a plain message.
- [ ] Private dispatch uses `workspace-write + on-request` by default for work aliases.
- [ ] Group QA uses `read-only + never` and cannot dispatch work aliases.
- [ ] `/status`, `/refresh`, and `/list` do not call controller start/send methods.
- [ ] Artifact candidates outside `exports/` or configured roots are blocked.
- [ ] OpeniLink adapter tests normalize private text messages into `IncomingMessage`.
- [ ] README and runbook describe current boundaries without promising Feishu or Windows support in v0.2.

## Execution Choice

Plan complete. Two execution options:

1. Subagent-Driven (recommended): dispatch a fresh subagent per task, review between tasks, fast iteration.
2. Inline Execution: execute tasks in this session using executing-plans, with checkpoints for review.
