# 私聊可恢复完成与持久投递设计

- 日期：2026-07-11
- 状态：已实现
- 审查范围：`f2e94ec`

## 背景

私聊 `/chat` 已有持久 claim、续租和完成结果重放，但成功路径先提交 ChatLog/ConversationTurn，
再用另一张 Session 完成 claim。业务提交后若 complete 失败或 owner 失权，新 owner 会 takeover 并
重新进入 pre-bridge、Bridge 和工具。流式客户端断连时，finalizer 完成 claim 后才执行 QQ push；
push 失败只记录日志，completed replay 不再触发 server push。

本设计只修改 Nanobot Server。QQbot 不接受接收端幂等键，因此投递只能提供持久、可重试的
at-least-once 语义；网络结果不确定时可能重复，但不得再静默丢失。

## 目标

1. 私聊持久化成功而 claim complete 失败后，新 owner 从恢复记录完成请求，不重跑 Bridge/工具。
2. takeover 必须验证当前请求与首次请求的业务输入一致；不一致或恢复数据损坏时失败关闭。
3. owner 失权后旧执行者不得进入 Bridge、持久化回复或返回成功。
4. 流式断连的 QQ 补投递写入持久 outbox，进程重启后仍可继续处理。
5. claim `completed` 只表示业务计算完成，outbox 独立表达投递状态。
6. 空 `message_id` 保持现有 BYPASS 行为，正常 HTTP/SSE 响应契约保持兼容。

## 非目标与保证边界

- 不修改 `/home/dascard/bot/QQbot` 或 `/nanobot/push` 请求协议。
- 不承诺 exactly-once；网络超时可能发生“远端已发送、本地未收到确认”，自动重试可能重复。
- 不为 Bridge 内所有外部工具新增步骤级 idempotency key。
- 不把投递状态塞入 `CompletedInboundResponse` 或 `inbound_message_claims.response_json`。
- 不修改 Prompt Runtime 输入、历史注入或工具输出契约。

## 私聊请求日志与输入指纹

非空 `message_id` 的首次 owner 在进入用户注册、private buffer、图片预缓存或 Bridge 前，写入唯一
private request journal：复用 `ChatLog`，`role=user`、canonical `session_id/message_id`，
`meta_json.kind=private_inbound_request`，并保存版本化业务输入 SHA-256。

业务输入包括 claim identity、user_id、query、规范化 files、sender_name、session_name、
source_message_ids 以及经过 allowlist 的业务 client meta；排除 HTTP request_id、trace、重试次数等
传输噪声。首次 journal 写入和重复加载都要求同一 identity 只有一行。

`attempt_count > 1` 时先加载 journal 并验证指纹：

- 指纹一致且没有 completion marker：允许继续执行。
- 指纹一致且有合法 completion marker：直接恢复。
- 缺失、损坏、多候选或指纹不一致：技术失败，Bridge 调用次数为 0。

## Recoverable completion

completion marker 存在 request journal 的独立 `inbound_claim_recovery` 字段中，包含 schema version、
claim key hash、request hash 和严格编码的 `CompletedInboundResponse`。对于 `respond`，assistant
`ChatLog` 同时设置 canonical `message_id`，恢复时必须唯一且 content 与 completion.reply 一致。

成功事务按以下顺序执行：

1. 更新 request journal 的业务 meta、processed 状态和 completion marker。
2. 写 assistant ChatLog 与 user/assistant ConversationTurn。
3. `flush()` 后计算 pending 数量并构造最终 completion。
4. 在同一次 commit 中保存 completion marker。

如果 marker 或任一业务行写入失败，整个成功事务回滚，只保留先前的 request journal。claim complete
仍使用现有 fresh Session 短事务；它失败时，下一 owner 从 marker 恢复并重新 complete。

blocked、silent、wait、no_reply 等早返回也把 completion marker 写回 request journal，避免重复写
档案或重复 private buffer 副作用。

## Owner fencing

私聊 runner 增加与群聊一致的保护：

- pre-bridge/Bridge 前执行 `checkpoint()`。
- Bridge await 与 `wait_unusable()` 竞速；owner 失权时取消业务 task。
- Bridge 返回后、成功事务前再次 `checkpoint()`。
- 所有取消和 checkpoint 异常都先保留主异常，再 best-effort fail claim。

stream body 的 pause/resume 语义保留；恢复分支在创建 Bridge task 之前完成。

## Delivery outbox

新增 `chat_delivery_outbox` 表，核心字段：

- canonical claim identity 与唯一 `delivery_key`；
- target_type、target_id、严格 JSON envelope；
- status：`pending/sending/ambiguous/delivered/failed`；
- owner_token、lease_expires_at、attempt_count、next_attempt_at；
- last_error、created_at、updated_at、delivered_at。

同一 claim identity 只能存在一条 delivery。断连 finalizer 先持久化业务 marker，但暂不 complete
claim；delivery task 随后幂等登记 outbox、complete claim，再尝试推送。这样 outbox 登记失败时
claim 仍可 fail/takeover，不会形成“claim 已完成但没有投递记录”的不可恢复状态。push 返回
`False`、`None` 或抛异常时也都有持久记录。

正常 SSE 不创建 outbox：业务持久化后先 yield 最终 `done`，只有 yield 恢复才 complete claim 并
安排 evolution。若 complete 在 `done` 已交付后失败，当前 owner fail，`finally` 再按断连流程登记
outbox；takeover 仍从业务 marker 恢复，不重进 Bridge。

最终 done 事件的完成标志在 `yield` 恢复后设置：如果连接在发送 done 时断开，`finally` 仍登记
outbox。断连发生在 finalizer 等待期间时，delivery task等待同一个 finalizer，再登记并投递。

## Outbox 状态机

```text
pending --claim lease--> sending
sending --True--> delivered
sending --False--> failed
sending --None/exception--> ambiguous --backoff到期--> sending
stale sending --lease回收--> ambiguous
```

网络不确定态使用同一 delivery key 自动重试，退避为 30 秒起步、指数增长、上限 30 分钟，不设置
自动放弃次数。`False` 表示明确拒绝或不可渲染，进入 failed，保留错误供运维处理但不盲目重试。

worker 使用 owner token 和租约条件更新，多个进程只能由一个 owner 发送同一行。常驻 worker 随
现有 scheduler 启动；测试模式不启动。即时断连任务和 worker 复用同一 claim/deliver API。

单次 publisher 尝试上限为 30 秒，默认 lease 为 60 秒，并强制 `attempt_timeout < lease`，避免请求
仍在发送时被其他 worker 当作过期任务回收。每次领取使用新的随机 fencing token；停止信号在
下一次领取前检查，当前尝试最多等待 30 秒完成结算。scheduler 为该线程保留 35 秒有界停止窗口。

常驻 worker 使用自己事件循环内创建并关闭的 `aiohttp.ClientSession`，不会与 ASGI 主循环共享
`core.daily_digest` 的模块级 session。它仍复用同一 envelope renderer、QQ push URL 和请求协议，
没有修改 QQbot 端代码或 payload。

## 数据库迁移

新增迁移 `20260711_chat_delivery_outbox`，启动自动创建表、唯一 identity 索引和
`status/next_attempt_at/lease_expires_at` 扫描索引。迁移前继续使用 SQLite 在线快照；无需手工 SQL。

## 测试矩阵

- 非流式和流式真实 SQLite：persist 成功、complete 失败、第二 owner恢复，Bridge/工具总调用 1。
- 连续 complete 失败到第三 owner仍只有一份业务数据。
- 请求指纹不一致、marker 缺失/损坏/多候选全部在 Bridge 前失败。
- Bridge 前后 owner 失权都不持久化回复。
- ChatLog、ConversationTurn、marker 同事务回滚。
- 断连 push 的 True/False/None/异常分别得到 delivered/failed/ambiguous。
- completed replay 不创建第二 outbox、不重跑 Bridge。
- 两个 worker 并发只有一个取得 delivery；stale sending 可恢复。
- worker 重启后继续处理 pending/ambiguous；backoff 和 fencing 生效。
- 在最终 done yield 时断连仍登记 outbox；正常消费 done 不登记。
- 空 message_id 继续 BYPASS，无 journal、marker 或 outbox。

## 验收条件

1. 私聊 persisted-but-uncompleted 的 takeover 不再调用 Bridge 或工具。
2. 旧 owner 失权后无法持久化或返回成功。
3. 断连补推失败不再只存在日志，数据库中始终有可查询、可重试状态。
4. 正常 HTTP/SSE、completed replay 和空 message_id 行为保持兼容。
5. 自动迁移、定向测试、全量测试和 WebUI 构建均为 0 failures。

## 实现映射

- `api/chat_recovery.py`：业务输入指纹、request journal 与 completion marker 严格 codec。
- `api/chat_persistence.py`：journal、assistant ChatLog、ConversationTurn 和 marker 原子提交。
- `api/chat_route_runner.py` / `api/chat_streaming_result.py`：owner guard、断连 finalizer 与 outbox 接入。
- `core/chat_delivery_outbox.py`：原子入队、fenced claim、过期租约恢复和状态结算。
- `core/chat_delivery_service.py`：短事务领取、超时 publisher 和 fresh-session settlement。
- `workers/chat_delivery_worker.py` / `bootstrap/schedulers.py`：重启恢复、轮询和有界生命周期。
- `core/chat_delivery_outbox_schema.py` / `core/schema_migrations.py`：严格 schema 与自动迁移。
