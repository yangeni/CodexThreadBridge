# CodexThreadBridge v0.2 Mobile Agent Console Design

Date: 2026-04-28
Status: Draft for user review
Target project: `/Users/clngs/Documents/CLngs_Vault/CodexThreadBridge`

## Summary

CodexThreadBridge v0.2 upgrades the original "chat bridge" idea into a
WeChat-first mobile Agent console. The first real channel is WeChat, using an
existing iLink/OpeniLink-style channel rather than implementing the WeChat
protocol from scratch. Feishu is reserved as a later structured dispatch
surface.

The core design remains platform-neutral:

```text
WeChat private chat / WeChat group chat
-> existing iLink/OpeniLink channel
-> WeChat Adapter
-> Gateway Core
-> Thread Registry / Context State / Policy Engine
-> cross-thread-controller MCP
-> Codex history session
```

The first version should be useful on the user's current Mac, while keeping the
core portable enough to add Windows support through platform adapters later.

## WeChat Channel Contract

v0.2 reuses an existing iLink/OpeniLink-style channel, but the Gateway must only
depend on a small adapter contract. The WeChat channel layer must provide:

- incoming private and group message events
- stable sender, group, conversation, and message identifiers
- text content, mention markers, and attachment descriptors
- attachment download into Gateway-owned local storage
- text replies to private chat or group chat
- image/file upload to owner private chat after Gateway approval
- optional typing/status notification

The channel layer must not decide Codex thread routing, alias selection,
approval policy, group authorization, or file-sending safety. Those decisions
belong to Gateway Core.

## Product Boundaries

WeChat private chat is the owner control surface. The owner can add global
thread aliases, switch the current thread, send ordinary messages to the active
Codex session, check status, refresh local history, receive completed replies,
receive Codex approval summaries, and request safe artifact delivery.

WeChat group chat is a low-permission QA surface. Groups are disabled by
default. A group must be approved from owner private chat before it gets its own
isolated `group-qa` Codex session. Group chat cannot dispatch real work
threads, switch aliases, approve actions, reset itself, or send local files.

Feishu is not implemented in v0.2. It should later reuse the same Gateway Core
and add stronger structured interaction: cards, buttons, status panels, thread
lists, approval-summary display, and document/file integrations.

## Thread Model

Thread aliases are global. An alias such as `code`, `paper`, or `dr` points to
the same Codex session from every future platform.

```text
code  -> 019xxx
paper -> 019yyy
dr    -> 019zzz
```

The active alias is local to each conversation context. Switching to `code` in
WeChat private chat does not change the active alias in a future Feishu thread.

```text
WeChat owner private chat -> active_alias = code
Future Feishu thread      -> active_alias = paper
Local simulator           -> active_alias = dr
```

The first WeChat private-chat command set is:

```text
/add <alias> <session_id>      Add a global thread alias.
/use <alias>                   Switch current private-chat context.
/list                          Show global aliases.
/remove <alias>                Remove an alias; requires confirmation.
/status [alias]                Show status without a model turn.
/refresh [alias]               Read local history without a model turn.
/send <alias> <message>        Send once without switching active alias.
/artifacts                     Show detected files from the latest result.
/sendfile <id|all>             Send allowed artifacts to owner private chat.
/help                          Show available commands.
```

`/bind <session_id>` remains as a compatibility shortcut for:

```text
/add default <session_id>
/use default
```

Group chat only supports normal QA and a narrow status command:

```text
@Bot <question>
@Bot /qa status
```

Group management is private-chat only:

```text
/group pending
/group approve <group_ref> [group_alias]
/group list
/group status <group_alias>
/group reset <group_alias>
/group disable <group_alias>
```

New groups are not auto-created. The first group mention records a pending
group and replies that the owner must approve it in private chat.

`/group approve` creates the group's isolated QA session immediately. The
Gateway calls `cross-thread-controller` with `session_id=null`, the configured
`group_qa_cwd`, `sandbox=read-only`, `approval_policy=never`, a configured
low-cost model/effort if available, and owner
`ctb-group-qa:<group_alias>`. The seed message establishes that the thread is a
read-only group QA context and asks for a minimal readiness reply. The returned
session id is written to `wechat_groups.qa_session_id`. If session creation
fails, the group remains `pending` and no group messages call Codex.

## Message Flow

Private-chat work dispatch uses `cross-thread-controller` instead of direct
Codex App UI manipulation or direct session-file writes.

```text
receive WeChat private message
-> verify sender is owner
-> resolve active alias
-> resolve session_id
-> controller status check
-> start or send to target session
-> wait_any
-> read_result
-> delivery_ack
-> close
-> release
-> reply to WeChat
```

The Gateway does not steal active locks. It stops and reports if the target
session is locked by another owner, dirty, reconcile-required, or has a mismatched
session head. `/status`, `/refresh`, and `/list` are strictly read-only and must
not create Codex model turns.

Private-chat work dispatch must use the target alias execution policy, not the
group QA policy. A work alias stores its intended `sandbox`, `approval_policy`,
`writable_roots`, `model`, and `effort`. The v0.2 default for a work alias is
`sandbox=workspace-write` and `approval_policy=on-request` with writable roots
limited to the alias workspace. This keeps Codex's normal tool review available.
`read-only + never` is reserved for status/refresh paths and group QA.

Replies are returned as completed messages in v0.2. If a platform limit is hit,
the Gateway may split the reply into simple chunks. Token streaming is not a
v0.2 requirement.

## Group QA Flow

Each approved WeChat group gets one independent `group-qa` Codex session.

Group QA is intentionally isolated:

```text
sandbox=read-only
approval_policy=never
no work-thread alias dispatch
no Gateway management commands
no force recover
no local-file delivery
```

This means group QA can have local continuity for that group, but it cannot
touch the user's real work aliases or send local artifacts to a group.

## Approval Boundary

Codex internal tool review remains Codex's responsibility. If a work thread
wants to write files, run commands, use network access, install dependencies, or
perform other Codex-governed tool actions, Gateway does not duplicate that
approval system.

When the controller exposes enough information to identify an approval wait,
Gateway forwards a short summary to WeChat private chat:

```text
code is waiting for Codex approval.
Action: ...
Reason: ...
Please return to Codex App to confirm.
```

Gateway does not confirm Codex approvals from WeChat in v0.2. Final approval
stays in Codex App.

Gateway does require explicit owner confirmation for bridge-layer risk:

- approving or disabling a WeChat group
- changing whitelist or platform credentials
- force recovering a lock
- resetting a group QA session
- deleting an alias
- sending local files that were not clearly produced by the latest task
- sending any local file to an external destination outside owner private chat

Group chat can never approve real actions.

## Files, Images, And Artifacts

Inbound WeChat images and files are saved under `data/attachments/` and recorded
in SQLite. v0.2 forwards local paths to Codex as text. True multimodal attachment
passthrough is out of scope.

Codex results are text-first. If Codex creates or references local files, Gateway
can detect candidate artifact paths in the result and offer safe delivery to
owner private chat.

Safe artifact delivery requires:

- the file exists
- the path is inside an allowed directory
- the file size is below the configured limit
- the path and type do not match sensitive-file rules
- the destination is owner private chat
- confirmation is obtained when the file was not clearly produced by the latest task

The default allowed artifact roots are the active alias workspace `exports/`
directory and configured `artifact_roots`. A detected file is sendable only when
it was created or modified after the current run started and lives under those
roots. Other detected paths are recorded as blocked candidates with a reason and
cannot be sent by `/sendfile` in v0.2.

Group chat cannot receive local artifacts in v0.2.

## Data Model

The SQLite store should separate global aliases, conversation-local context,
group authorization, attachments, artifacts, and events.

```text
thread_aliases
- alias
- session_id
- label
- default_cwd
- sandbox
- approval_policy
- writable_roots
- model
- effort
- created_by
- created_at
- updated_at
```

```text
contexts
- platform
- conversation_type
- conversation_id
- thread_key
- active_alias
- owner_user_id
- created_at
- updated_at
```

```text
wechat_groups
- group_id
- group_alias
- status
- qa_session_id
- created_by
- approved_at
- disabled_at
- created_at
- updated_at
```

```text
attachments
- id
- source_platform
- source_message_id
- conversation_id
- local_path
- mime_type
- original_name
- direction
- created_at
```

```text
artifacts
- id
- run_id
- alias
- session_id
- local_path
- mime_type
- size_bytes
- status
- reason
- created_at
```

```text
events
- id
- event_type
- platform
- conversation_id
- alias
- session_id
- run_id
- summary
- status
- created_at
```

## Cross-Platform Packaging Boundary

v0.2 targets macOS first because the current Codex App, session files,
controller, and user workspace are on this Mac. The design should still avoid
hard-coding macOS-specific paths or service mechanisms into the Gateway Core.

Recommended packaging boundary:

```text
core/             platform-neutral Gateway logic
adapters/wechat/  channel integration, ideally cross-platform
codex_backend/    Codex path/controller adapter by OS
service/          macOS launchd or Windows service adapter
paths/            OS-specific path discovery and config
```

A macOS package should not be expected to run unchanged on Windows. Windows
support should be added as a platform adapter that verifies:

- Windows Codex App/session/config paths
- cross-thread-controller support on Windows
- background service strategy
- file path and allowed-directory rules
- WeChat channel SDK behavior on Windows

This should be an adaptation, not a rewrite, if the core boundaries are kept.

## Error Handling

Phone-facing errors should be short and actionable:

```text
No active thread. Use /use <alias> first.
code is locked by another owner. Try later.
code changed in Codex App. Run /refresh code first.
code is waiting for Codex approval. Summary sent; confirm in Codex App.
This group is not enabled. Ask the owner to approve it in private chat.
File blocked: path is outside allowed directories.
```

Do not expose platform tokens, internal tracebacks, environment variables, or
unnecessary local sensitive paths in chat replies.

## Test Plan

Local simulator:

- `/add`, `/use`, `/list`, `/status`, `/refresh`, `/artifacts`, and `/sendfile`
  work against simulated private messages.
- Ordinary private messages enter the active Codex session.
- Completed runs are delivery-acked, closed, and released.
- `/status`, `/refresh`, and `/list` do not create Codex model turns.

WeChat private chat:

- Owner can switch aliases and dispatch to an existing Codex session.
- Completed Codex replies return to private chat.
- Approval waits produce summaries but require Codex App confirmation.
- Allowed artifacts can be sent to owner private chat after safety checks.

WeChat group chat:

- Unapproved groups do not call Codex.
- Approved groups use independent `group-qa` sessions.
- Group chat cannot dispatch work aliases, approve actions, reset QA, or receive
  local files.
- Multiple group QA sessions remain isolated.

Safety:

- Non-whitelisted users are rejected.
- Active locks are not stolen.
- Dirty, reconcile-required, or head-mismatch sessions are not written.
- Sensitive artifacts are blocked.
- Gateway-layer high-risk commands require private-chat owner confirmation.

## Acceptance Criteria

v0.2 is successful when:

- The owner can use WeChat private chat to switch to a global alias and continue
  an existing Codex thread.
- The owner receives completed replies and approval summaries on WeChat.
- Status and refresh checks do not consume model turns.
- At least one approved WeChat group can use an isolated QA thread without
  access to real work aliases.
- Codex-generated local files can be safely offered and delivered to owner
  private chat.
- The implementation remains structured so Feishu and Windows adapters can be
  added later without replacing the Gateway Core.
