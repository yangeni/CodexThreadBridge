# CodexThreadBridge

CodexThreadBridge is a local gateway project for connecting mobile chat
surfaces to long-lived Codex history threads.

The v0.3 slice is a WeChat-first mobile Agent console with a real private-chat
text runtime. It keeps platform protocol details behind channel adapters, routes
normalized chat events through Gateway Core, and uses the
`cross-thread-controller` boundary to continue existing Codex sessions.

## Current Status

v0.3 is implemented as a local Python package with a simulator, SQLite-backed
state, Gateway Core policy checks, a controller client boundary, an
OpeniLink-compatible WeChat adapter boundary, and a real iLink/OpenClaw-WeChat
private-chat text runtime.

v0.3 adds the first real iLink/OpenClaw-WeChat private-chat runtime. It keeps
the v0.2 Gateway Core policy boundary and wires real text updates through an
iLink-compatible HTTP client. Media upload, group runtime activation, Windows
packaging, and launchd autostart remain outside v0.3.

The current runtime behavior is:

- WeChat is the first mobile console target.
- v0.3 can long-poll iLink/OpenClaw-WeChat-style private text updates and send
  text replies through `sendmessage`.
- Owner private chat can dispatch work to existing Codex aliases with
  `/add <alias> <session_id>` and `/use <alias>`.
- Gateway Core still has the approved group QA path with read-only policy and
  `approval_policy=never`, but the v0.3 real OpeniLink runtime does not
  activate group chat.
- Status/refresh/list do not create model turns; alias listing/status commands
  are read-only Gateway/controller operations.
- Artifacts are only eligible for owner-private delivery after local path, root,
  freshness, size, and sensitive-path checks; v0.2 currently returns
  would-send results until real channel upload wiring is added.
- Feishu and Windows remain adapter targets, not v0.2 deliverables.

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
│   └── 03_v0.3_OpeniLink运行说明.md
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

## v0.2 Scope

- Runtime: local Python service on this Mac.
- Controller integration: MCP stdio calls to `cross-thread-controller`.
- First adapters: local simulator and OpeniLink-compatible WeChat boundary.
- Binding model: owner private chat maps short aliases to existing Codex
  session IDs.
- Default mode: after `/use <alias>`, ordinary owner private-chat messages go
  directly to the target Codex session through the controller boundary.
- Group mode: owner-approved WeChat groups get isolated read-only QA sessions,
  not write-capable work aliases.
- Result mode: return the completed response as one message.
- Artifact mode: detected local files are listed after safety checks. `/sendfile`
  is owner-private only and currently returns would-send results; real channel
  upload wiring remains a later integration step.

## Hard Boundaries

- No model heartbeat. Background sync may read local state or Codex JSONL files,
  but must not send turns such as "are you still there?" or "report status".
- No automatic realtime sync in v0.2. Mobile-initiated runs return results; Codex
  App manual changes are pulled with `/refresh`.
- No group work dispatch. Groups cannot dispatch work aliases, approve actions,
  reset themselves, or receive local files.
- No unbounded execution authority. Private work aliases use the existing
  Codex session workspace with `approval_policy=on-request`; group QA is
  read-only with approvals disabled.
- No direct dependency on `cross-thread-controller` internals. The gateway should
  treat the controller as an MCP server boundary.

## Current Command Shape

```text
/add <alias> <session_id>       Add an owner private-chat alias.
/use <alias>                    Set the active alias for ordinary messages.
/list                           List aliases without a model turn.
/status [alias]                 Read controller status without a model turn.
/refresh [alias]                Read local history without a model turn.
/artifacts [alias]              List latest detected artifacts.
/sendfile <artifact_id|all>     Return owner-private would-send results for allowed artifacts.
/group approve <group> [alias]  Owner-only approval for isolated group QA.
/group list                     Owner-only group listing.
/group status <group|alias>     Owner-only group status.
/group reset <group|alias>      Owner-only reset to pending.
/group disable <group|alias>    Owner-only disable.
@Bot /qa status                 Group-side QA status check.
```

## Source Dependency

The bridge depends on the existing controller project:

```text
/Users/clngs/Documents/Codex/tools/cross-thread-controller/
```

The controller MCP state lives at:

```text
/Users/clngs/.codex/cross-thread-controller/state.sqlite3
```
