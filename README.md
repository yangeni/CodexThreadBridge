# CodexThreadBridge

CodexThreadBridge is a local gateway project for connecting mobile chat
surfaces, such as Feishu, QQ, or WeChat, to long-lived Codex history threads.

The first version is intentionally small: it binds an external chat context to
an existing Codex session, sends text messages through the
`cross-thread-controller` MCP server, returns the completed response, and
supports manual refresh from local Codex history. It does not poll the model,
does not create heartbeat turns, and does not try to control the Codex App UI.

## Current Status

This folder currently contains the design scaffold only. Business code is not
implemented yet.

## Project Layout

```text
CodexThreadBridge/
├── README.md
├── docs/
│   ├── 00_设计草稿.md
│   └── 01_MVP实施方案.md
└── data/
    └── attachments/
        └── .gitignore
```

## First-Version Scope

- Runtime: local Python service on this Mac.
- Controller integration: MCP stdio calls to `cross-thread-controller`.
- First adapters: local simulation adapter first, Feishu adapter second.
- Binding model: explicit `/bind <session_id>` to an existing Codex thread.
- Default mode: hybrid mode. Before binding, the gateway handles routing and
  binding. After binding, ordinary chat messages go directly to the target
  Codex session.
- Result mode: return the completed response as one message.
- Image mode: save images under `data/attachments/` and forward the local path
  in the text message. True multimodal attachment passthrough is out of scope
  for v1.

## Hard Boundaries

- No model heartbeat. Background sync may read local state or Codex JSONL files,
  but must not send turns such as "are you still there?" or "report status".
- No automatic realtime sync in v1. Mobile-initiated runs return results; Codex
  App manual changes are pulled with `/refresh`.
- No unbounded execution authority. Default runs are read-only. Higher-risk
  actions require explicit confirmation and structured authorization.
- No direct dependency on `cross-thread-controller` internals. The gateway should
  treat the controller as an MCP server boundary.

## Planned Commands

```text
/bind <session_id>          Bind current chat context to a Codex session.
/unbind                     Remove the current binding.
/status                     Show binding and controller state.
/refresh                    Pull new local Codex history without model calls.
/send <session_id> <text>   Send one temporary message to a specific session.
/help                       Show supported commands.
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

