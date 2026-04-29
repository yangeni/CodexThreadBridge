# CodexThreadBridge v0.3 OpeniLink Runtime Design

Date: 2026-04-29
Status: Draft for user review
Target project: `/Users/clngs/Documents/CLngs_Vault/CodexThreadBridge`

## Summary

v0.3 turns the v0.2 WeChat adapter boundary into a real local runtime. The
chosen channel direction is the iLink/OpenClaw-WeChat protocol family, using
`Tencent/openclaw-weixin` as the primary protocol reference. Projects such as
`wechat-claude-code` are useful references for user experience and edge cases,
but they are not the dependency base for CodexThreadBridge.

The reason is architectural: CodexThreadBridge already owns thread aliases,
private/group authorization, Codex session dispatch, controller cleanup, refresh,
and artifact safety. v0.3 only needs the missing transport layer:

```text
WeChat / ClawBot
-> iLink-compatible HTTP long-poll and send APIs
-> OpeniLinkRuntime
-> existing OpeniLinkChannelAdapter
-> existing Gateway Core
-> cross-thread-controller MCP
-> existing Codex session
```

## Decision

Use an iLink-compatible runtime as the first real WeChat integration.

Do not wrap `wechat-claude-code` as the runtime. That project is already a
Claude Code application with its own channel logic, command assumptions, and
agent lifecycle. Reusing it directly would force CodexThreadBridge to remove or
override Claude-specific behavior. It should remain a reference for interaction
details only.

Do not require Feishu or a web UI in v0.3. Those remain later adapters.

## v0.3 Scope

v0.3 should deliver:

- local long-running Python runtime for WeChat private chat
- iLink-compatible HTTP client wrapper for `getupdates`, `sendmessage`, optional
  `getconfig`, and optional `sendtyping`
- runtime config loaded from local env/config files
- safe token handling: secrets live in local files or environment variables,
  never in chat, docs, tests, or git
- owner whitelist based on WeChat sender identifiers observed from real events
- conversion from iLink messages to the existing `IncomingMessage`
- outbound text reply through the existing `WeChatChannelPort.send_text`
- private-chat commands and ordinary alias dispatch using existing Gateway Core
- local smoke runner that can run without changing the Gateway Core
- tests using recorded or synthetic iLink payloads
- operator runbook for login, config, smoke test, and shutdown

v0.3 should not deliver:

- true media upload and `/sendfile` real delivery
- inbound image/file decryption and attachment download beyond metadata capture
- group QA activation in the first runtime pass
- approval confirmation from WeChat
- Windows packaging
- launchd autostart
- automatic realtime sync or Codex heartbeat

## Runtime Components

`OpeniLinkRuntime` is the process boundary. It loads config, creates the store,
controller client, Gateway, and channel adapter, then loops over real WeChat
events.

`IlinkHttpClient` is the transport wrapper. It hides HTTP details and exposes
small methods:

```text
get_updates(cursor) -> IlinkUpdateBatch
send_message(conversation, text) -> None
get_config(conversation) -> optional typing ticket
send_typing(conversation, enabled) -> optional
```

`IlinkEventMapper` converts protocol payloads into the normalized shape already
accepted by `normalize_openilink_event`. It must preserve:

- raw message id
- sender id
- target conversation id
- context token needed for replies
- private versus group shape when the protocol exposes it
- text item content
- media item descriptors as opaque attachment metadata

`RuntimeConfig` extends `BridgeConfig` with channel settings:

```text
ILINK_BASE_URL
ILINK_BOT_TOKEN
ILINK_OWNER_USER_IDS
ILINK_POLL_TIMEOUT_SECONDS
ILINK_REQUEST_TIMEOUT_SECONDS
CTB_PROJECT_ROOT
CTB_CONTROLLER_COMMAND
```

The implementation may use `.env` or shell environment variables, but `.env`
must be ignored by git. A committed `.env.example` may document variable names
with placeholder values only.

## Message Flow

Private-chat inbound flow:

```text
runtime starts
-> load config and restore cursor
-> long-poll getupdates
-> map every new text message
-> OpeniLinkChannelAdapter.iter_messages yields IncomingMessage
-> Gateway handles commands or dispatch
-> runtime sends Gateway reply with sendmessage
-> cursor is persisted only after the batch is safely handled
```

Outbound text flow:

```text
Gateway reply
-> channel.send_text(conversation_id, text)
-> IlinkHttpClient.send_message(to_user_id, context_token, text)
```

The runtime may poll the WeChat channel while idle. This is not a Codex heartbeat
and does not spend Codex model quota because it does not send a turn to Codex.
No background task may send "are you there" or status prompts into Codex.

## Conversation Identity

The Gateway context key must stay stable across restarts. For private chat,
the preferred context identity is:

```text
platform = wechat
conversation_type = private
conversation_id = remote user id or iLink session id
thread_key = conversation_id
```

If the protocol provides a context token only for replies, the token should be
stored as channel metadata, not used as the Gateway thread key. Context tokens
may rotate; owner and conversation identifiers should remain the stable routing
base.

The first successful owner message should be logged in a local operator-friendly
way so the owner can copy the sender id into `ILINK_OWNER_USER_IDS` if it was not
known before.

## Security Boundaries

v0.3 keeps the v0.2 policy model:

- owner private chat can use aliases and dispatch existing Codex sessions
- non-owner private messages are rejected before Gateway dispatch
- group messages are ignored or return a not-enabled response in the first
  runtime pass
- no token is printed in normal logs
- exception traces are kept local and sanitized before chat replies
- local file paths are not sent to non-owner destinations
- `/status`, `/list`, and `/refresh` remain read-only
- Codex tool approvals remain in Codex App

The first runtime should default to private chat only. Group QA can be enabled
after private chat is stable because it adds a second authorization surface and
separate session creation behavior.

## Error Handling

Network errors should back off and keep the runtime alive unless the config is
invalid. Authentication errors should stop the runtime with a clear local error
that asks the user to refresh login or token.

Malformed incoming payloads should be recorded locally and skipped without
calling Codex. A malformed payload must not poison the cursor if doing so would
drop later valid messages from the same batch.

Controller errors should reuse the existing Gateway-visible safe messages. If a
dispatch fails after acquiring a controller lock, the existing controller cleanup
sequence must still run.

If outbound reply sending becomes ambiguous after Codex completed the task, the
runtime should record a local `delivery_unknown` event with message id,
conversation id, and sanitized reason. It should prefer avoiding duplicate
WeChat replies over automatic resend. Explicit terminal channel/controller
errors should stop the runtime with a local error instead of retrying forever.

## Test Strategy

Unit tests should cover:

- iLink text payload mapping into normalized events
- owner sender id recognition
- context token preservation for outbound replies
- non-owner rejection before dispatch
- malformed payload handling
- sendmessage request body shape for text replies
- cursor persistence after successful batches
- cursor non-advance on unsafe partial failure
- config loading without leaking secrets in repr or error messages

Integration smoke should cover:

```text
start runtime with fake iLink server
fake server returns one owner text message
Gateway handles it with EchoController or fake controller
runtime sends one text reply
cursor is persisted
```

Real-channel smoke should be manual:

```text
start runtime
send /help from owner WeChat private chat
receive command list
send /list
confirm no Codex model turn is created
send ordinary text after /use <alias>
confirm Codex reply returns to WeChat
```

## References

- `Tencent/openclaw-weixin`: primary protocol reference for iLink-compatible
  WeChat message polling, reply, typing, and media shape.
- `wechat-claude-code` style projects: secondary reference for user-facing
  interaction expectations only.
- Existing v0.2 spec:
  `docs/superpowers/specs/2026-04-28-mobile-agent-console-design.md`
