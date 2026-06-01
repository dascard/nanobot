# 摘要重生成与近期摘要可见性修复设计

## 背景

WebUI 线上长期摘要页没有重新生成按钮，近期摘要页只显示一个私聊
session。实际检查发现：

- 长期摘要按钮只在 `mode === "recent"` 时渲染。
- 近期摘要列表只读取 `rolling_session_summaries`，没有摘要但有
  `conversation_turns` 的群聊不会出现。
- 群聊滚动摘要的不同发言人门槛依赖 `meta_json.sender_name`，旧数据缺失该字段，
  但正文里有 `[用户名]` 标记，导致被误判为 `not_enough_distinct_senders`。
- `session_summary_jobs` 已产生 pending 任务，但服务启动调度器没有启动
  session summary worker。

## 目标

修复摘要管理链路，使管理员能在 WebUI 上看到可生成近期摘要的群聊，能手动生成
或重新生成近期摘要，能重新生成长期摘要，并让 LLM 摘要任务自动被后台 worker 消费。

## 方案

采用最小边界修复：

1. 后端 session browser 把 `conversation_turns` 纳入 session 汇总来源。
   `kind=recent` 表示“已有近期摘要或具备近期对话 turns”，而不是只看
   `rolling_session_summaries`。
2. 群聊 sender 识别新增正文兜底解析，从 `[用户名]xxx` 提取 sender key，
   兼容旧 `conversation_turns`。
3. 管理端新增长期摘要重生成接口：
   `POST /api/v1/admin/session-memory/{session_id}/digests/run`。
   未传日期时默认重生成该 session 最新 digest 日期；传日期时按该日期重生成。
4. `generate_daily_digest_for_date` 新增可选 `session_id` 过滤，避免按 user_id
   误重生成同一用户下的其他 session。
5. WebUI 长期摘要页显示“重新生成长期摘要”按钮；近期摘要页对没有摘要的 session
   显示“生成近期摘要”，对已有摘要的 session 保留“重新生成 LLM 摘要”。
6. 服务启动调度器启动 session summary worker 轮询，消费 pending LLM 摘要任务。

## 非目标

- 不重构摘要数据模型。
- 不改变长期摘要三层结构。
- 不把历史 `conversation_turns` 做批量迁移；旧数据通过运行时兜底解析兼容。
- 不在本次修复中处理知识库 RAG、贴纸 RAG 或其他未提交改动。

## 测试

- 后端 API 测试覆盖：conversation-only 群聊进入近期摘要列表；长期摘要重生成
  endpoint 传递 session/date/force。
- 单元测试覆盖：群聊 sender 兜底从 `[用户名]` 提取不同发言人。
- daily digest 测试覆盖：`session_id` 过滤只重生成指定 session。
- 调度器测试覆盖：生产模式启动 session summary worker handle。
- WebUI 静态测试覆盖：近期/长期两个重生成按钮和对应 API 调用存在。
- 最终运行相关 pytest、`npm run build`、必要时完整 pytest。
