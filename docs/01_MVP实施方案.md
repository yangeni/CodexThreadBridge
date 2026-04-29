# CodexThreadBridge MVP 实施方案

版本：v0.2 当前实现说明，保留 v0.1 历史
日期：2026-04-28

## 1. MVP 范围

v0.2 的当前范围已经从 v0.1 的 Feishu-first 设计骨架，收敛为 WeChat-first mobile Agent console：

- 本机 Python Gateway Core。
- SQLite-backed alias、active context、group、artifact state。
- 本地模拟 adapter。
- OpeniLink-compatible WeChat channel adapter boundary。
- owner 私聊通过 alias dispatch 到已有 Codex session。
- 已批准微信群使用隔离 read-only QA session。
- artifact 只在 owner 私聊中经过安全检查后发送。
- `/status`、`/refresh`、`/list`、`/group list`、`/group status`、`@Bot /qa status` 不创建模型 turn。

v0.1 历史目标是最小可用线程桥：

- 本机 Python Gateway。
- 本地模拟 adapter。
- 飞书 adapter 的接口预留和后续接入方案。
- 显式 `/bind <session_id>` 绑定已有 Codex 线程。
- 普通文本消息进入目标 Codex session。
- 完成后整段回传。
- 手动 `/refresh` 拉取本地新增内容。
- 图片保存到本地并转发路径。

明确不做：

- 不创建新 Codex thread。
- 不做自动实时同步。
- 不做模型心跳。
- 不做真正多模态附件透传。
- 不接微信个人号逆向方案。

v0.2 明确不交付：

- Feishu UI/adapter 完整接入。
- Windows packaging。
- 移动端 approval-confirmation proxy。
- 微信个人号逆向 hook。
- 自动实时同步或模型心跳。

## 2. 工程骨架

当前 v0.2 代码结构：

```text
CodexThreadBridge/
├── README.md
├── setup.py
├── setup.cfg
├── pytest.ini
├── docs/
├── data/
│   ├── bridge.sqlite3
│   └── attachments/
├── src/
│   └── codex_thread_bridge/
│       ├── __init__.py
│       ├── artifacts.py
│       ├── commands.py
│       ├── config.py
│       ├── controller_client.py
│       ├── gateway.py
│       ├── models.py
│       ├── policy.py
│       ├── stores.py
│       ├── refresh.py
│       └── adapters/
│           ├── local.py
│           ├── openilink.py
│           └── wechat_channel.py
└── tests/
```

v0.2 不是单纯文档骨架，已经包含 Gateway Core、policy、store、artifact detector、local simulator、OpeniLink event normalization、WeChat channel port，以及 pytest 覆盖。Feishu 和 Windows 只保留为后续目标。

## 3. 实施步骤

### 阶段 1：状态与 alias

v0.2 使用 alias 模型替代 v0.1 的单一 `/bind` 叙述。owner 私聊命令：

```text
/add <alias> <session_id>
/use <alias>
/list
/status [alias]
```

`/add` 会读取 controller status，从 status 中提取 `cwd`、`workspace_root`、`project_root` 或 `default_cwd`，并保存 alias 的默认 workspace 与执行策略。普通私聊消息只发送到当前 active alias。

状态使用 SQLite 保存：

```text
aliases
active_contexts
groups
artifact_runs
artifacts
```

### 阶段 2：Controller Client Boundary

v0.2 继续把 `cross-thread-controller` 视为边界，不直接 import controller 内部类。Gateway 需要的最小方法是：

```text
status(session_id)
start_or_send(session_id, cwd, message, owner, policy, idempotency_key, expected_session_head)
```

私聊 work alias 使用：

```text
sandbox=workspace-write
approval_policy=on-request
writable_roots=(alias.default_cwd,)
```

群聊 QA session 使用：

```text
sandbox=read-only
approval_policy=never
writable_roots=()
```

### 阶段 3：Gateway 命令处理

当前统一消息对象：

```text
IncomingMessage
- platform
- conversation_type
- conversation_id
- thread_key
- sender_id
- sender_role
- text
- attachments
- raw_ref
```

私聊命令：

- `/add <alias> <session_id>`：添加 alias。
- `/bind <session_id>`：兼容解析入口，非 v0.2 主路径。
- `/use <alias>`：设置 active alias。
- `/list`：列出 alias，不调用模型。
- `/status [alias]`：读取 controller status，不调用模型。
- `/refresh [alias]`：只读本地历史设计边界，不允许创建模型 turn。
- `/artifacts [alias]`：列出最近一次 run 的 artifact 检测结果。
- `/sendfile <artifact_id|all>`：只发送 allowed artifact。
- `/group approve/list/status/reset/disable`：owner 私聊管理群 QA。

普通消息：

- owner 私聊有 active alias：发送到 alias 对应 session。
- owner 私聊无 active alias：提示先 `/use <alias>`。
- 群聊未 `@Bot`：忽略。
- 群聊 `@Bot` 但未批准：记录 pending 并提示 owner 私聊批准。
- 已批准群 `@Bot` 普通问题：发送到该群隔离 QA session。
- 群聊 work/file/admin 命令：拒绝。

### 阶段 4：Local Adapter

本地模拟入口用于在不接 OpeniLink 的情况下测试 Gateway 行为。本仓库是 src layout，安装后可运行：

```text
python3 -m codex_thread_bridge.adapters.local --project-root /Users/clngs/Documents/CLngs_Vault/CodexThreadBridge
```

未安装时，在仓库根目录运行：

```text
PYTHONPATH=src python3 -m codex_thread_bridge.adapters.local --project-root /Users/clngs/Documents/CLngs_Vault/CodexThreadBridge
```

交互行为：

- 从 stdin 读消息。
- 把消息转成 `IncomingMessage`。
- 使用内置 EchoController 返回 `LOCAL: <message>`。
- 打印 Gateway 返回内容。

smoke：

```text
/add code 019-code
/use code
请只回复 bridge smoke ok
```

### 阶段 5：OpeniLink / WeChat Adapter

v0.2 的微信入口是 OpeniLink Hub WebSocket inbound：

```text
OpeniLink Hub WebSocket inbound
-> OpeniLink adapter normalize_openilink_event
-> Gateway normalized IncomingMessage
-> Gateway Core
-> ChannelPort send_text/send_file
```

OpeniLink adapter 只做事件归一化和 channel port 包装。Gateway Core 不依赖 OpeniLink 内部实现。

### 阶段 6：Feishu / Windows 后置

飞书和 Windows 是 adapter targets，不是 v0.2 deliverables。后续接入时应复用当前 Gateway Core、policy、store、controller boundary 和 channel port 思路。

Feishu 后续接入仍应遵守：不在群聊泄露 token、traceback 或敏感本地路径；状态类命令不创建模型 turn。

## 4. `/refresh` 设计

`/refresh` 不创建 Codex run。

流程：

```text
读取 binding
-> 找到 bound_session_id
-> 读取本地 Codex session JSONL
-> 计算当前 session_head
-> 与 last_seen_session_head 比较
-> 提取新增 user/assistant 文本摘要
-> 更新 last_seen_session_head
-> 回传新增内容
```

如果无法读取 JSONL：

- 返回“无法读取本地 session 历史”。
- 不更新 `last_seen_session_head`。
- 不调用模型补救。

## 5. 错误处理

常见错误：

- 未绑定：提示 `/bind <session_id>`。
- session 不存在：提示检查 session id。
- active lock 被其他 owner 持有：提示稍后重试或手动确认 recover。
- `dirty=true` 或 `reconcile_required=true`：提示需要 reconcile。
- head 不一致：提示先 `/refresh`。
- 图片保存失败：不发送消息到 Codex，直接返回错误。
- MCP server 启动失败：提示检查 `cross-thread-controller` 配置。

错误消息要面向手机端，短、明确、可行动。

## 6. 测试清单

本地模拟：

- `/add code 019...` 可以添加已有 Codex session alias。
- `/use code` 可以设置 active alias。
- 已设置 active alias 后普通文本会进入目标 session。
- `/status` 不产生 Codex turn。
- `/refresh` 不产生 Codex turn。
- `/list` 不产生 Codex turn。

安全：

- 非白名单 sender 被拒绝。
- active lock 不被抢占。
- `expected_session_head` 不一致时停止。
- force recover 不可由普通消息隐式触发。
- 群聊不能 dispatch work aliases。
- 群聊不能 approve actions。
- 群聊不能 reset itself。
- 群聊不能 receive local files。
- 群聊 QA 使用 read-only policy 和 `approval_policy=never`。

Artifact：

- 只检测存在的本地文件。
- 文件必须在 allowed artifact roots 内。
- 文件不能命中敏感路径 marker。
- 文件不能早于 run start。
- 文件不能超过大小限制。
- 只向 owner 私聊发送 allowed artifact。

微信 / OpeniLink：

- OpeniLink event 可归一化为 `platform=wechat` 的 `IncomingMessage`。
- 私聊 sender 命中 owner whitelist 时标记为 owner。
- 群消息保持 `conversation_id` 和 `thread_key`。
- ChannelPort 支持 reply/file sends。

飞书后置：

- 私聊可映射为 normalized private message。
- 群 thread 可映射为 normalized group/thread message。
- 返回结果不泄露 token、traceback 或敏感环境变量。

## 7. 验收标准

v0.2 通过条件：

- 本地模拟 adapter 能完成 `/add`、`/use`、普通消息、返回的闭环。
- owner 私聊可以通过 alias 接入至少一个已有 Codex session。
- 已批准微信群使用隔离 read-only QA session。
- `/refresh` 可读取 App 侧手动新增内容，且不消耗模型额度。
- `/status`、`/list`、群状态命令不消耗模型额度。
- Artifact gating 能区分 allowed/blocked，并只允许 owner 私聊发送。
- 默认安全策略阻止非白名单用户。
- OpeniLink boundary 可接收 normalized inbound，并通过 ChannelPort 回发文本或文件。
- Feishu 和 Windows 不被当作 v0.2 完成交付项。
