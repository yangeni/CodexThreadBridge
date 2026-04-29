# CodexThreadBridge

CodexThreadBridge is a local gateway project for connecting mobile chat
surfaces to long-lived Codex history threads.

The v0.4 slice is a WeChat-first mobile Agent console with a real private-chat
text runtime and an OpenClaw-free iLink QR login bootstrapper. It keeps platform
protocol details behind channel adapters, routes normalized chat events through
Gateway Core, and uses the `cross-thread-controller` boundary to continue
existing Codex sessions.

## Current Status

v0.4 is implemented as a local Python package with a simulator, SQLite-backed
state, Gateway Core policy checks, a controller client boundary, an
OpeniLink-compatible WeChat adapter boundary, a real iLink private-chat text
runtime, and a standalone QR login helper that does not require installing or
running OpenClaw.

v0.4 keeps the Gateway Core policy boundary and wires real text updates through
an iLink-compatible HTTP client. Media upload, group runtime activation, Windows
packaging, and launchd autostart remain outside v0.4.

The current runtime behavior is:

- WeChat is the first mobile console target.
- v0.3 can long-poll iLink/OpenClaw-WeChat-style private text updates and send
  text replies through `sendmessage`.
- v0.4 can obtain iLink credentials directly with a QR login helper, save them
  to ignored local JSON, and let the runtime load that credentials file.
- Owner private chat can dispatch work to existing Codex aliases with
  `/add <alias> <session_id>` and `/use <alias>`.
- Gateway Core still has the approved group QA path with read-only policy and
  `approval_policy=never`, but the v0.3 real OpeniLink runtime does not
  activate group chat.
- Status/refresh/list do not create model turns; alias listing/status commands
  are read-only Gateway/controller operations.
- Artifacts are only eligible for owner-private delivery after local path, root,
  freshness, size, and sensitive-path checks; `/sendfile` currently returns
  owner-private would-send results until real channel upload wiring is added.
- Feishu and Windows remain adapter targets after v0.3.

## Project Layout

```text
CodexThreadBridge/
├── README.md
├── .env.example
├── setup.py
├── setup.cfg
├── pytest.ini
├── docs/
│   ├── 00_设计草稿.md
│   ├── 01_MVP实施方案.md
│   ├── 02_v0.2运行说明.md
│   ├── 03_v0.3_OpeniLink运行说明.md
│   └── 04_v0.4_iLink独立登录.md
├── src/
│   └── codex_thread_bridge/
│       ├── config.py
│       ├── gateway.py
│       ├── controller_client.py
│       ├── stores.py
│       ├── policy.py
│       ├── artifacts.py
│       └── adapters/
│           ├── local.py
│           ├── ilink_auth.py
│           ├── ilink_client.py
│           ├── ilink_events.py
│           ├── wechat_channel.py
│           ├── openilink.py
│           └── openilink_runtime.py
├── tests/
└── data/
    └── attachments/
        └── .gitignore
```

## v0.4 Scope

- Runtime: local Python service on this Mac.
- Controller integration: MCP stdio calls to `cross-thread-controller`.
- Adapters: local simulator, OpeniLink-compatible WeChat boundary, and real
  private-chat text runtime.
- Login: standalone iLink QR login helper saves local credentials under
  `data/local/`, with no OpenClaw runtime dependency.
- Binding model: owner private chat maps short aliases to existing Codex
  session IDs.
- Default mode: after `/use <alias>`, ordinary owner private-chat messages go
  directly to the target Codex session through the controller boundary.
- Group mode: Gateway Core has isolated read-only QA capability, but the
  current v0.3 OpeniLink runtime does not expose it.
- Result mode: return the completed response as one message.
- Artifact mode: detected local files are listed after safety checks. `/sendfile`
  is owner-private only and currently returns would-send results; real channel
  upload wiring remains a later integration step.

## Hard Boundaries

- No model heartbeat. Background sync may read local state or Codex JSONL files,
  but must not send turns such as "are you still there?" or "report status".
- No automatic realtime sync. Mobile-initiated runs return results; Codex App
  manual changes are pulled with `/refresh`.
- No group work dispatch. Groups cannot dispatch work aliases, approve actions,
  reset themselves, or receive local files.
- No group QA runtime in v0.3 OpeniLink. Group messages are ignored, and
  private `/group ...` commands are blocked by the runtime.
- No unbounded execution authority. Private work aliases use the existing Codex
  session workspace with `approval_policy=on-request`; Gateway Core group QA
  remains read-only with approvals disabled and is not exposed by v0.3
  OpeniLink runtime.
- No OpenClaw runtime dependency. Open-source iLink/OpenClaw-WeChat code may be
  used as protocol reference, but CodexThreadBridge does not install or launch
  OpenClaw.
- No direct dependency on `cross-thread-controller` internals. The gateway should
  treat the controller as an MCP server boundary.

## Current v0.4 Login Commands

```text
PYTHONPATH=src python3 -m codex_thread_bridge.adapters.ilink_auth login
PYTHONPATH=src python3 -m codex_thread_bridge.adapters.ilink_auth probe-updates
PYTHONPATH=src python3 -m codex_thread_bridge.adapters.ilink_auth show-env --owner-user-id <wechat-owner-id>
```

The `login` command prints a WeChat QR URL, waits for confirmation, and saves
credentials to `data/local/ilink_credentials.json` by default. That path is
ignored by git. The helper never prints the bot token. `probe-updates` reads one
iLink update batch and prints sender ids for owner discovery; it does not reply
to WeChat and does not touch Codex.

## Current v0.3 OpeniLink Runtime Commands

```text
/add <alias> <session_id>       Add an owner private-chat alias.
/bind <session_id>              Bind the default private-chat alias and select it.
/use <alias>                    Set the active alias for ordinary messages.
/list                           List aliases without a model turn.
/remove <alias>                 Remove an owner private-chat alias. `/rm` is also accepted.
/status [alias]                 Read controller status without a model turn.
/refresh [alias]                Read local history without a model turn.
/send <alias> <message>         Send one message to an alias without changing active context.
/artifacts [alias]              List latest detected artifacts.
/sendfile <artifact_id|all>     Return owner-private would-send results for allowed artifacts.
/help                           Show Gateway command help.
```

Gateway Core retains future-only `/group approve|list|status|reset|disable` and group-side QA handling for adapter work; those commands are not exposed by the current v0.3 OpeniLink runtime.

## Source Dependency

The bridge depends on the existing controller project:

```text
/Users/clngs/Documents/Codex/tools/cross-thread-controller/
```

The controller MCP state lives at:

```text
/Users/clngs/.codex/cross-thread-controller/state.sqlite3
```
