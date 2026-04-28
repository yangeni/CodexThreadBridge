# CodexThreadBridge MVP 实施方案

版本：v0.1 草稿  
日期：2026-04-28

## 1. MVP 范围

第一版只实现最小可用线程桥：

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

## 2. 工程骨架

建议后续代码结构：

```text
CodexThreadBridge/
├── README.md
├── docs/
├── data/
│   ├── bridge.sqlite3
│   └── attachments/
├── src/
│   └── codex_thread_bridge/
│       ├── __init__.py
│       ├── gateway.py
│       ├── mcp_client.py
│       ├── stores.py
│       ├── refresh.py
│       ├── security.py
│       └── adapters/
│           ├── local.py
│           └── feishu.py
└── tests/
```

本次落地只创建文档和附件目录，不写业务代码。

## 3. 实施步骤

### 阶段 1：状态与绑定

实现 `BindingStore`：

- 使用 SQLite。
- 支持 upsert binding。
- 支持按 `platform + chat_id + thread_key` 查询绑定。
- 支持更新 `last_seen_session_head`。
- 支持解除绑定。

实现 `AttachmentStore`：

- 保存附件元数据。
- 将图片文件写入 `data/attachments/YYYY/MM/DD/`。
- 文件名使用 `timestamp + message_id + hash`，避免重名。

### 阶段 2：MCP Client

实现 stdio JSON-RPC MCP client，不直接 import controller 内部类。

最低方法：

```text
status(session_id)
start_run(session_id, cwd, message, owner, idempotency_key)
send_followup(session_id, lock_token, expected_session_head, message)
wait_result(run_id, after_seq)
read_result(run_id)
ack_close_release(run_id, session_id, lock_token)
```

默认参数：

```text
intent=status_probe
transport=app_server
plan_capability=protocol
sandbox=read-only
approval_policy=never
lease_seconds=300
```

`delegated_execution` 不在 v1 默认路径中启用。

### 阶段 3：Gateway 命令处理

实现统一消息对象：

```text
IncomingMessage
- platform
- chat_id
- thread_key
- message_id
- sender_id
- text
- images
- created_at
```

命令处理：

- `/bind <session_id>`：读取真实 session head，保存绑定。
- `/unbind`：删除当前聊天绑定。
- `/status`：返回绑定信息和 controller session 状态。
- `/refresh`：读取本地 Codex JSONL 的新增内容，不调用模型。
- `/send <session_id> <message>`：临时发送，不改变当前绑定。
- `/help`：返回命令说明。

普通消息：

- 有绑定：发送到绑定 session。
- 无绑定：提示先 `/bind <session_id>`。

### 阶段 4：Local Adapter

实现本地模拟入口，用来在不接飞书的情况下测试完整链路。

建议形式：

```text
python -m codex_thread_bridge.adapters.local --platform local --chat-id test
```

交互行为：

- 从 stdin 读消息。
- 把消息转成 `IncomingMessage`。
- 打印 Gateway 返回内容。

### 阶段 5：Feishu Adapter

飞书 adapter 后续接入：

- 接收飞书事件。
- 把私聊、群消息、群 thread 统一映射为 `platform=feishu`。
- `chat_id` 使用飞书会话 ID。
- `thread_key` 优先使用飞书 thread/topic/root message；没有时使用 `chat_id`。
- 结果以整段文本回发。

第一版不在飞书消息里暴露内部 traceback。

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

- `/bind 019...` 可以绑定已有 Codex session。
- 已绑定后普通文本会进入目标 session。
- 返回结果后 run 已 `delivery_ack`、`close`，session 已 `release`。
- `/status` 不产生 Codex turn。
- `/refresh` 不产生 Codex turn。
- `/unbind` 后普通消息不再发送到 Codex。

安全：

- 非白名单 sender 被拒绝。
- active lock 不被抢占。
- `expected_session_head` 不一致时停止。
- force recover 不可由普通消息隐式触发。

图片：

- 图片保存到 `data/attachments/`。
- 消息文本包含绝对本地路径。
- 图片保存失败时不发送半成品消息。

飞书：

- 私聊可绑定。
- 群 thread 可绑定。
- 返回结果不泄露 token、traceback 或敏感环境变量。

## 7. 验收标准

MVP 通过条件：

- 本地模拟 adapter 能完成绑定、发送、返回、释放锁的闭环。
- 至少一个已有 Codex session 可被成功接入。
- `/refresh` 可读取 App 侧手动新增内容，且不消耗模型额度。
- 图片路径转发链路可用。
- 默认安全策略阻止非白名单用户。

