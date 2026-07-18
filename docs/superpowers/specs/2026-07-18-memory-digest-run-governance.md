# MemoryDigest 运行治理设计

## 背景

当前普通与 Admin 两个 `digests/run` 路由调用同步生成器。默认 LLM 摘要器只提供异步入口，因此真实请求会被折叠为 `created_sessions=0`；无输入、已存在、质量门禁失败、模型失败和并发占用也都无法区分，失败没有可重试的运行记录。

## 目标

- 两个 HTTP 路由改为异步，并直接等待正式异步生成链路。
- 返回 `created`、`skipped`、`no_input`、`failed`、`in_progress` 的结构化计数和逐会话结果。
- 为每个 `(session_id, digest_date)` 保存唯一作业、来源快照、尝试次数、失败分类和安全错误摘要。
- 使用短租约原子 claim；不得在 LLM 网络调用期间持有数据库事务。
- 每个 LLM 批次调用前后使用独立短会话续租，并以 lease token 和来源 revision 做 fencing。
- 成功写入 L0/L1/L2、归档旧 revision、创建 semantic index job 和完成作业必须位于同一短事务。
- LLM、JSON 或审计失败不得写 active fallback 或空层级；失败作业可由同一正式入口显式重试。
- 响应只公开固定运行字段；不得透传 Prompt、正文、上游错误或内部诊断。

## 非目标

- 不增加长期记忆自动注入路径。
- 不修改现有摘要 Prompt 或模型路由。
- 不在本变更中增加独立 MemoryDigest worker。
- 不迁移、删除或重建历史 MemoryDigest 数据。

## 数据模型

新增 `memory_digest_jobs`，核心字段如下：

- 唯一键：`(session_id, digest_date)`；
- 来源快照：`user_id`、`source_start_log_id`、`source_end_log_id`、`source_log_count`、`source_revision`；
- 状态：`pending | running | done | skipped | failed`；
- 租约：`locked_by`、`lease_token`、`lease_expires_at`；
- 重试：`attempt_count`、`retry_count`、`max_retry`、`next_retry_at`；
- 结果：`result_digest_count`、`error_type`、`error_summary`、`finished_at`；
- 审计时间：`created_at`、`updated_at`。

终态 `done` 表示摘要和索引任务已原子落库；`skipped` 表示来源经过正式过滤后没有可摘要内容；`failed` 表示模型、输出合同、证据审计、来源快照变化或写入失败。`no_input` 是请求级结果：指定日期/过滤条件下没有任何 ChatLog 时不虚构 session 作业。

## 执行流程

1. 在短只读会话中按日期和过滤条件加载 ChatLog，按 canonical session 分组，并生成稳定来源 revision。
2. 在新的短事务中 upsert 作业并原子 claim。未过期的 `running` 返回 `in_progress`；已有 `done` 且非 force 返回 `skipped`；`failed` 只有显式 retry/force 才重新 claim。
3. 关闭数据库会话，在事务外等待 LLM；每批前后以新的短会话续租，续租失败立即放弃当前执行结果。
4. 打开新的短事务，重新核对来源 revision 和租约 fencing token。
5. 成功时写摘要层级、semantic index job，并 CAS 完成作业；失败时只 CAS 写入失败分类和安全摘要。

租约只防止同一逻辑来源并发生成；最终写入还必须校验 token、状态和来源 revision，避免过期执行者覆盖新结果。

## API 契约

两个运行端点保留原有顶层字段，并新增：

```json
{
  "status": "ok|partial|failed|no_input",
  "created_sessions": 1,
  "counts": {
    "created": 1,
    "skipped": 0,
    "no_input": 0,
    "failed": 0,
    "in_progress": 0
  },
  "results": [
    {
      "session_id": "...",
      "status": "created",
      "job_id": 1,
      "retryable": false,
      "error_type": ""
    }
  ]
}
```

响应不得包含 Prompt、模型原始响应、聊天正文或错误正文。普通 API 仍受 Bearer 鉴权，并且必须提供非空 `user_id`；普通 Token 不得使用 `force` 或 `retry_failed`。Admin API 仍受 Admin Token 鉴权，允许显式 force/retry；按 session 重建且请求未提供 `user_id` 时，不得从旧摘要推断首发送者作为来源过滤条件。

## 失败与重试

- 模型连接/超时：`model_error`，可重试；
- JSON/输出合同：`output_invalid`，可重试；
- 证据或质量门禁：`quality_rejected`，可重试但不自动 fallback；
- 来源在 LLM 期间变化：`source_changed`，可重试；
- 数据库或索引入队失败：事务回滚后记录 `write_failed`；
- 租约丢失：当前执行只返回 `in_progress`/`lease_lost`，不得写结果。

`retry_failed=true` 只能重试已存在的 failed job；不得为历史 active 摘要伪造失败 job。`force=true` 属于 Admin 人工治理能力，可越过自动重试预算，但仍必须持有有效租约。新尝试开始和失败结算时清空当前结果引用，旧 active 摘要实体继续保留，避免 failed job 混用上一次成功结果字段。

错误摘要只保存固定错误码和经过长度限制、去秘密处理的说明。

## 验证

- 路由测试证明默认异步 summarizer 被等待，且不再固定返回 0。
- 失败测试证明 job 留存、无 L0/L1/L2、可显式重试。
- 并发测试证明同一 `(session,date)` 只有一个调用进入 summarizer。
- 事务测试证明 semantic enqueue 失败时摘要和 job 成功状态均不落库。
- 来源变化测试证明旧 LLM 结果不能写入。
- 迁移测试证明表、唯一约束和索引幂等创建。
- 多发送者群聊测试证明来源复查不使用首发送者过滤。
- 文件型 SQLite 双连接测试证明同一来源只有一个 lease owner。
- lease 过期、heartbeat、旧 token fencing 和 force 越过预算均有回归测试。
- 多 session 的 claim/settlement 异常返回 partial，且不阻断其他 session。
- 路由测试证明普通/Admin 权限边界、日期校验和响应字段白名单。
