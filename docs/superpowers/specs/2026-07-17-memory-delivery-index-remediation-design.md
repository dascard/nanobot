# 记忆、投递与语义索引后继整改设计

## 状态

- 日期：2026-07-17
- 状态：已批准，代码验证完成，待版本化提交与部署前门禁
- 基线提交：`0f0b6293371aa539aad718793f2713937f428eaf`
- 目标：生成一个可安全部署的后继版本，替代当前不能直接上线的目标提交
- 实施计划：`.Codex/plans/memory-delivery-index-remediation.md`

## 背景

目标提交已经完成配置默认值、MemoryDigest 质量门禁、Session Summary 异步生成和部分语义索引入队，但生产预检与隔离测试证明它仍不能直接部署：

- QQbot 的 `/nanobot/push` 实际要求 `NANOBOT_PUSH_TOKEN` Bearer，目标代码没有发送该请求头；
- Docker 镜像使用 Python 3.10，代码和测试已经调用 Python 3.11 才提供的异步 API；
- Session Summary 会静默截断超长 turn 和旧摘要，不能证明 previous summary 的语义得到继承；
- Session Summary 保存的是调用结束后重新解析的路由模型，不是实际响应模型；
- 延迟旧摘要任务可以覆盖较新的摘要，使 coverage 倒退；
- Semantic Index 可重试失败会落为不可领取的 `failed`，真实 worker 还绕过了重试函数；
- Semantic Index 没有 lease token，超时 worker 恢复后仍能覆盖新 owner 的结果；
- MemoryDigest 多张 Recall Card 会重复使用 `card:0`；
- Session Summary adapter 读取旧字段名，canonical 请求和 resolved 字段没有进入索引；
- 业务源归档不会使旧索引失效，现有 backfill 也无法发现部分缺失和 stale 索引。

隔离测试基线为 4673 项，结果为 4641 passed、26 failed、6 skipped。修正 canonical Prompt Runtime 挂载后的定向回归仍有 22 项失败。因此，本设计不以「容器能启动」作为完成标准，而以数据库状态机、HTTP 鉴权、容器版本和端到端索引证据作为验收依据。

## 已确认决策

1. 采用「契约化摘要 + 逻辑源级原子 reconcile」方案。
2. 使用一个后继分支分阶段实现，但生产只部署包含全部修复的最终 HEAD。
3. Nanobot → QQbot 正式发送 `NANOBOT_PUSH_TOKEN` Bearer。
4. 暂不新增明文 HTTP 限制或 `allow_insecure_http` 风险确认开关；当前内网 HTTP 风险只记录，不在本次修改。
5. Python 运行时统一升级到 3.11，不在业务代码里为 Python 3.10 增加兼容分支。
6. Session Summary 使用纯契约层修复输入完整性、继承门禁和模型追踪，不新增 summary head 表。
7. Semantic Index 扩展现有 job/item 表，增加 lease fencing 和 source revision，不新建第二套索引命名空间。
8. 历史 Persona、Group Memory、Recall Card 和全量语义索引仍先做 dry-run；本设计的部署不得自动归档、重建或全量重索引。

## 目标

1. 四个核心服务运行同一个后继提交和同一镜像，并使用 Python 3.11。
2. outbound worker 从唯一有效配置源读取专用 push Token，并在真实请求中发送 Bearer。
3. Session Summary 的每个输入字符都有可复查 coverage，不再静默截断。
4. 每轮摘要明确处置 previous summary 的未完成事项、决定、请求和产物。
5. 保存实际 LLM 响应模型和 request log ID，不再调用结束后重新读取路由。
6. 旧摘要任务不能覆盖更高 coverage 的 active summary。
7. Semantic Index 失败可以自动重试，也可以通过正式 Admin 接口显式重试。
8. Semantic worker 的所有结算都受 owner、lease token 和 lease 到期时间约束。
9. 同一逻辑源的 replace/delete、FTS 更新和 job 终态在一个事务内提交。
10. Recall Card、Session Summary chunk 和业务源 revision 使用稳定身份。
11. backfill 能分页发现 missing、stale 和 orphan，而不是只判断索引总数是否为 0。
12. 实现提供 dry-run 和可审计维护入口，不通过直接 SQL 伪造成功状态。

## 非目标与固定边界

- 不修改 QQbot `/nanobot/push` 的服务端鉴权实现；QQbot 已经按专用 Token 验证 Bearer。
- 不解决 Bearer 经内网明文 HTTP 传输的问题，不新增 HTTPS、mTLS 或 IP allowlist。
- 不把 `NANOBOT_ADMIN_TOKEN` 配置到 QQbot，也不用 Admin Token 代替 API Token 或 push Token。
- 不开启 `persona.injection_enabled`、`persona.auto_update_enabled` 或 `group_memory.injection_enabled`。
- 不新增「每个普通回复自动注入长期记忆」路径。
- 不删除 `chat_logs`，也不物理删除 Persona、Group Memory 或 MemoryDigest。
- 不在本次代码实现中执行真实 AI 日报投递、真实主动外呼或生产数据清洗。
- 不把显式 `memory_query` 成功描述成所有回复已经自动消费长期记忆。
- 不直接覆盖生产 Prompt Runtime；Runtime 升级继续使用正式 audit/plan/apply 流程。
- 不顺带修复 Qwen classifier、Web Search 或研究型主动外呼的独立运行问题。

## 方案比较

### 方案 A：最小补丁

只升级 Python、发送 Bearer、修正明显字段名、重试状态和 `card:0`。优点是改动少；缺点是旧 worker fencing、摘要语义继承、乱序覆盖、旧索引失效和部分回填仍无法闭环，不满足生产恢复目标。

### 方案 B：契约化摘要 + 原子索引 reconcile

在现有数据模型上增加最少必要字段；摘要用纯函数契约层保证输入、继承和结果追踪；索引用逻辑源 reconcile 保证 replace/delete 原子性。部署需要一次 additive migration，但不需要新索引命名空间或业务数据重建。该方案能够覆盖全部已证明阻断，作为本次采用方案。

### 方案 C：generation/head 表 + 新索引命名空间

为每个 session/source 新建 head 表，并在新索引命名空间完成全量构建后原子切换。该方案一致性最强，但需要更复杂的双写、回填、切换和回滚协议，超出当前修复范围。

## 总体架构

```text
QQbot /nanobot/push
  ^
  | Authorization: Bearer <专用 push token>
  |
outbound worker (Python 3.11)

conversation turns + previous active summary
  -> Session Summary 纯契约层
  -> 分片 / 请求预算 / inheritance gate
  -> LLM 调用结果（content + actual model + request_log_id）
  -> coverage CAS
  -> active summary + semantic replace job（同一事务）

MemoryDigest / Session Summary / 其他 recallable source
  -> 逻辑 source identity + source revision
  -> SemanticIndexJob claim（owner + lease token + expiry）
  -> 事务外 embedding
  -> fenced reconcile（delete stale + upsert expected + FTS + job terminal）
  -> 单次 commit

Admin dry-run
  -> keyset cursor 扫描
  -> current / missing / stale / orphan 报告
  -> 经明确维护动作入队 replace/delete
```

## Python 运行时与投递鉴权

### Python 3.11

Dockerfile 的 Python 基础镜像从 `python:3.10-slim-bullseye` 升级为同发行版的 Python 3.11 镜像，避免同时引入 Debian 发行版变化。CI 中明确固定 Python 版本的 workflow 同步调整到 3.11。

镜像验证必须证明：

- `python --version` 为 3.11.x；
- `asyncio.Runner` 和 `Task.cancelling()` 可用；
- server、outbound、session-summary、semantic-index 四个服务来自同一新镜像；
- 镜像版本报告包含后继提交，`dirty=false`。

### 专用 push Token

统一使用配置键 `NANOBOT_PUSH_TOKEN`。它只表示 Nanobot → QQbot `/nanobot/push` 的 Bearer，不与下列 Token 复用：

- `NANOBOT_API_TOKEN`：QQbot → Nanobot API；
- `NANOBOT_ADMIN_TOKEN`：Nanobot Admin API；
- `QQBOT_ADMIN_API_TOKEN`：QQbot 管理面。

`OutboundWorkerConfig` 增加脱敏字段 `push_token`，`from_env()` 失败闭合：空值或控制字符导致 worker 启动失败，异常只包含配置键名，不包含 Token 原文。`deliver_qq_push_with_session()` 接收显式 token，并发送：

```http
Authorization: Bearer <NANOBOT_PUSH_TOKEN>
```

transport、异常、outcome、日志和持久化记录均不得保存 header 或 Token。测试 fake session 只断言 header 是否正确，不打印值。

Compose 继续使用 worker 最小环境 allowlist，只给 outbound worker 增加 `NANOBOT_PUSH_TOKEN`。server、session-summary worker 和 semantic-index worker 不需要该变量。`.env.example` 增加空占位，生产 `.env` 仍由部署阶段安全配置，不进入提交。

本次不验证 URL scheme，不阻止当前内网 HTTP，也不新增 `NANOBOT_QQ_PUSH_ALLOW_INSECURE_HTTP`。`NANOBOT_QQ_PUSH_CONFIG_REVISION` 继续表示 endpoint 配置版本；Token 轮换时部署流程必须同步递增 revision，但 revision 不包含 Token 指纹。

### 凭据隔离后的 legacy compatibility drain

Compose 必须继续让 `nanobot-server` 的 `NANOBOT_PUSH_TOKEN` 为空，不能为了兼容
`legacy_direct` 把凭据放回 server。`daily_digest.run_scheduled_tasks()` 和
`proactive_outreach_scheduler()` 只负责发现、生成和持久化 occurrence；两条主循环不得构造
`OutboundWorkerConfig`、创建 QQ HTTP session 或主动执行 legacy drain。scheduled producer 仅在
调用方显式注入 `legacy_transport` 时保留同步兼容行为；proactive producer 仅在显式注入
`publisher` 时保留旧三态兼容行为。没有显式注入时，即使 control 仍为 `legacy_direct`，producer
也只提交不可变 outbox，并返回 `queued`。

持有 push Token 的 outbound worker 在每个轮询周期依次执行三个独立、有界的 lane：普通
outbox、scheduled legacy 和 proactive legacy。三个 lane 复用同一份
`OutboundWorkerConfig` 和同一个 transport；每个 lane 最多处理 `batch_size` 条，单个 lane
异常只记录安全错误类型并继续后续 lane，因此持续积压的普通 outbox 或其中一种 legacy 来源
都不能永久饿死其他来源。停止信号在每次新 claim 前检查；已经开始的 HTTP 请求必须完成持久化
结算，停止后不得领取下一条。

普通 `deliver_outbound_once()` 继续拒绝 `legacy_direct`。worker 只能通过两条 source-specific
drain 调用 `deliver_legacy_outbound_once()`，不能增加绕过 source/cutover fencing 的通用 claim。
跨进程兼容 drain 先只读快照 control 当前仍有效的 writer owner、token、protocol、version
和到期时间。真正 claim 必须在同一个数据库事务和 source control 写锁内重新校验：source
type 一致、owner/token/protocol/version 未变化且 lease 仍未过期；任一事实变化都返回无
claim，不能发起 HTTP。

如果存在到期 legacy leaf、但原 writer lease 已过期，worker 使用进程内随机、source-scoped
的 takeover identity 走现有 `acquire_or_renew_delivery_writer()`；只有成功取得 source 写锁和
writer lease 后，才能按现有 CAS 把 queued/blocked run 重绑到新 writer，再领取 outbox。活动
writer 存在时不得抢占；普通 outbox claim 仍不得领取 legacy。writer token 不是 push 凭据，
但同样禁止进入日志、异常正文、API、meta、repr 或统计结果。

该整改不修改数据库 schema、cutover 状态机、投递幂等键、deadline、attempt 审计或来源状态
投影。明文 HTTP 限制仍按已确认边界留待独立安全改造。

## Session Summary 契约

### 模块边界

新增 `app/session_memory/llm_contract.py`，只负责纯函数和不可变数据结构：

- turn 净化与完整分片；
- coverage manifest；
- 完整 messages 预算；
- canonical previous state；
- inheritance obligation 与门禁；
- `SessionSummaryLLMResult`。

`llm_summarizer.py` 负责业务编排、LLM 调用、审计和 finalize；`jobs.py` 负责 job owner、续租、obsolete 和 finalize permit；worker 负责生成唯一 owner 并在各批次之间续租。纯契约层不得访问数据库或外部服务。

### Turn 分片与 coverage manifest

每条 turn 先完整执行非截断净化，再按换行优先、字符边界兜底分片。不得先按 12000 字符切尾，也不得对最终 Prompt 做 `[:N]`。

每个 fragment 包含：

```text
turn_id
role
fragment_index
fragment_count
content
sanitized_sha256
fragment_sha256
```

manifest 保存有序 turn ID、完整净化后 hash、片数和每片 hash。LLM 成功批次逐步累计已覆盖 fragment hash；finalize 前重新读取来源 turn 并重算 manifest。来源内容、顺序、hash 或完成集合不一致时任务失败，不创建 active summary。

### 完整请求预算

请求预算计算覆盖 system prompt、wrapper、header、previous state、turn fragments 和安全余量。固定常量：

```text
SESSION_SUMMARY_LLM_MAX_REQUEST_CHARS = 12000
SESSION_SUMMARY_LLM_MAX_STATE_CHARS = 4000
SESSION_SUMMARY_LLM_REQUEST_SAFETY_CHARS = 512
ROLLING_SUMMARY_MAX_CHARS = 1800
```

`ROLLING_SUMMARY_MAX_CHARS` 只限制给聊天主链路渲染的摘要文本，不限制内部 canonical state。构建 messages 后必须重新计算总长度；超限时将最后一个完整 fragment 移到下一批，不能截断 fragment。若固定 system + previous state 已经超过请求预算，返回可诊断失败，不伪造 coverage。

### Canonical state 与 previous 继承

下一批从 `summary_json` 读取 canonical state；旧记录无法解析时才回退到 `summary_text`。canonical state 只包含：

```text
summary
open_threads
decisions
important_user_requests
resolved_items
artifacts
participants
keywords
```

`quality` 和审计 meta 不计入 state。canonical JSON 超过 4000 字符时返回 `summary_state_budget_exceeded`，要求模型合并同类事项后重试；不得静默切尾。

对 previous state 中的 `open_threads`、`decisions`、`important_user_requests` 和 `artifacts` 生成稳定 obligation ID：

```text
sha256(field + normalized_text + duplicate_ordinal)[:16]
```

模型输出增加只用于审计的 `inheritance` 数组：

```json
{
  "source_id": "a12b34c56d78e90f",
  "disposition": "carried",
  "target_field": "open_threads",
  "target_index": 0
}
```

允许 `carried`、`updated`、`resolved`。每个 obligation 必须恰好出现一次；未知、重复、空 target 或越界 target 均拒绝。`resolved` 只能指向 `resolved_items`。每个批次都执行门禁，不能只审计最终批次。legacy 自由文本摘要生成一个 `legacy_summary` obligation，要求映射到新 `summary`。

最终保存前从业务 `summary_json` 中移除 `inheritance`，只在 meta 中保存 contract version、gate 统计、state hash 和批次 trace，保持现有消费接口兼容。

### 实际模型与 request log

`NewAPIClient.chat_completion()` 成功响应继续保留 `_nanobot_model_id`，并附加内部 `_nanobot_requested_model` 与 `_nanobot_request_log_id`。Session Summary 将默认调用包装为：

```python
@dataclass(frozen=True)
class SessionSummaryLLMResult:
    content: object
    model: str
    requested_model: str
    request_log_id: int | None
```

实际模型优先使用响应的 `model`，其次使用本次请求实际选中的 `_nanobot_model_id`，最后为 `unknown`。禁止调用结束后重新解析当前路由。多批次时，现有列保存最后一批实际模型和 request log ID，全部批次的脱敏 trace 写入 meta。

自定义 summarizer 继续兼容 `str` 和 payload `dict` 返回值，包装为 `model=custom_summarizer`；同步和异步调用接口不变。

### Coverage CAS 与 obsolete

`SessionSummaryJob.status` 是普通字符串，本次增加终态 `obsolete`，不需要 DDL。finalize 前取得 `FinalizePermit`：

```text
promote | obsolete | lost_lease
```

权威规则：

1. 没有 active summary：`promote`。
2. active coverage 小于 proposed coverage：`promote`。
3. coverage 相等且 active row 是 job 选定的 fallback：允许 LLM 替换 fallback。
4. coverage 相等但 active 是其他 summary：`obsolete`。
5. active coverage 大于 proposed coverage：`obsolete`。
6. job 不再是当前 owner 的 running job：`lost_lease`。

`obsolete` 只更新 job 状态与 meta，不归档 active summary、不创建新 summary、不创建 semantic job。`promote` 路径维持「归档旧摘要 + 创建新摘要 + 入队 semantic replace job + summary job done」单事务。Admin retry 只接受 `failed`；`obsolete` 和其他状态返回 409。

每个 LLM 批次之后通过短事务续租。worker 默认 owner 使用 `hostname:pid:uuid`，不能让多个实例共享固定 owner。

## Semantic Index v2

### Schema migration

新增迁移 `20260717_semantic_index_reconcile_v2`。

`semantic_index_jobs` 新增：

| 字段 | 默认值 | 语义 |
|---|---|---|
| `lease_token` | `''` | 每次 claim 生成的新随机 token |
| `lease_expires_at` | `NULL` | 当前 lease 的权威到期时间 |
| `attempt_count` | `0` | 每次成功 claim 单调增加 |
| `manual_retry_count` | `0` | Admin 显式 retry 次数 |
| `source_revision` | `''` | 入队时的逻辑源 revision |
| `meta_json` | `'{}'` | delete source、adapter manifest 等任务参数 |

`semantic_index_items` 新增 `source_revision TEXT NOT NULL DEFAULT ''`。

迁移增加 claim、lease、source revision 组合索引，并保证迁移建表与 ORM `create_all` 的字段和索引一致。迁移重复执行必须幂等。

上线迁移前必须停止旧 semantic worker。迁移将：

- 旧 `running` 安全重排为 `pending`，清除旧锁并记录原因；
- 将 `failed + finished_at IS NULL + next_retry_at IS NOT NULL` 的旧不可达重试恢复为 `pending`；
- 将旧 item 的空 `source_revision` 保留为 stale 标记，等待正式 backfill；
- 允许旧 pending job 在首次 claim 时由兼容 resolver 补出 revision。

迁移不归档业务数据、不清空索引、不直接创建成功记录。

### Job 状态机与 fencing

状态集合：

```text
pending | running | done | done_with_warning | failed | superseded
```

状态迁移：

| 起点 | 条件 | 终点 |
|---|---|---|
| 新建 | 参数合法 | `pending` |
| `pending` | 到期且 claim CAS 成功 | `running`，生成 token 与 expiry，`attempt_count += 1` |
| `running` | 相同 token 且未过期 | heartbeat 延长 expiry |
| `running` | reconcile 成功 | `done` |
| `running` | lexical 成功但 embedding 失败 | `done_with_warning` |
| `running` | source revision 已变化 | `superseded` |
| `running` | 暂时错误且有预算 | `pending`，增加 `retry_count` 和退避时间 |
| `running` | 永久错误或预算耗尽 | `failed` |
| `running` | lease 过期 | 按一次失败 CAS 到 `pending` 或 `failed` |
| `failed` 或过期 `running` | Admin CAS retry | `pending`，增加 `manual_retry_count` |

`max_retry=3` 表示首次尝试外最多 3 次自动重试。人工 retry 不清零自动重试历史，只授予一次显式尝试。

heartbeat、recover、finish、fail 和最终索引写入均必须匹配：

```text
job_id + status=running + lease_token + lease_expires_at > now
```

旧 token 过期或被新 owner 替换后，不得修改 index item、FTS 或 job 终态。外部 embedding 在事务外执行；返回后重新验证 lease 和 source revision。

### 稳定 Recall Card 身份

模型返回的 `card_id` 只作为本次响应内局部标签，不作为持久身份。完成批次合并后由运行时生成 canonical ID：

```text
normalized_text = NFKC(text) + 合并连续空白
evidence_ids = 排序去重后的正整数
identity = digest_source_id + canonical_type + normalized_text + evidence_ids
card_id = "rc_" + sha256(canonical_json(identity))[:24]
source_sub_id = "card:" + card_id
```

关键词、重要度和数组顺序不参与身份；正文、类型或证据变化会形成新 ID。同一 canonical ID 必须去重。

MemoryDigest semantic job 从「每个物理 L0/L1/L2 行一个 job」收敛为「每个逻辑 digest source 一个 job」。loader 聚合同一 active source 的 L0、L1 和全部 Recall Card，再生成完整 expected chunks。

### Session Summary 稳定身份

Session Summary 的 canonical 字段固定为：

```text
summary
open_threads
decisions
important_user_requests
resolved_items
artifacts
participants
keywords
quality
```

adapter 仅在 canonical 键缺失时兼容旧 `requests`、`resolved`；两套字段同时存在时以 canonical 为准，不生成重复 chunk。

身份合同：

| 项目 | 值 |
|---|---|
| `source_id` | 稳定 `session_id` |
| `document_id` | 当前 `RollingSessionSummary.id` |
| `source_revision` | coverage、summary kind rank、row ID 和 stable hash 的确定性组合 |
| summary chunk | `section:summary` |
| 列表 chunk | `section:<field>:<normalized_content_hash>` |

`participants` 和 `keywords` 写 metadata 并用于 lexical/embedding enrichment，不单独制造低信息 chunk；`quality` 用作 gate 和 metadata。

Memory RAG 按稳定 `source_id` 聚合，但 API 的 `summary_id` 从 `document_id` 读取。旧 item 缺少 `document_id` 且旧 `source_id` 为数字时才回退旧行为。

### 原子 reconcile

新增唯一写入口：

```python
reconcile_semantic_source(
    db,
    *,
    source_type,
    source_id,
    source_revision,
    index_version,
    expected_chunks,
    delete_source_ids,
    lease_fence,
)
```

函数只 flush，不自行 commit。replace 在同一事务内：

1. 验证 lease fence 与当前 source revision。
2. 加载当前逻辑 source 及 `delete_source_ids` 的 active index rows。
3. 删除对应 FTS row，并将旧 item 软删除为 `status=deleted`，同时写入 `deleted_at`。
4. upsert/reactivate 完整 expected chunks，设置 `status=active` 并清空 `deleted_at`。
5. 重建 expected chunks 的 FTS。
6. CAS 写入 job 终态。
7. 单次 commit。

任一步失败都回滚，旧索引完整保留。delete 使用相同入口但 expected 为空，并清理该逻辑源的全部 active index version，因为当前 retriever 不按 version 过滤。source 存在但 adapter 产出空集合时也按 delete 处理，不能静默 no-op。

业务源变化与 job 入队必须同事务：

- Session Summary replacement 入队同一 session source 的 replace；
- MemoryDigest force rebuild 将旧 logical source 放入 `delete_source_ids`，并为新 source 入队 replace；
- 纯 archive/history clear 入队 delete；
- vNext producer 使用 `replace`/`delete`，旧 `upsert` 只作为迁移兼容别名。

### Cursor backfill

backfill 只做逻辑源级扫描和入队，不直接绕过 worker 写索引。顺序固定为：

```text
memory_digest -> session_summary -> group_memory -> sticker -> knowledge -> orphan_sweep
```

Persona 不进入本次 source 枚举。

cursor 使用 base64url 编码的 canonical JSON，至少包含版本、source type、after anchor、high-water mark、目标 index version 和 adapter manifest。首次扫描捕获 high-water，保证一轮扫描有限且可复查；source、version 或 manifest 与后续请求不一致时返回 422。

每个逻辑源计算完整 expected map：

```text
expected[(source_sub_id, target_index_version)] = source_hash
actual = 该逻辑源全部 active item
```

分类：

- `current`：expected 与 actual 的 key/hash 完全一致，或二者均为空；
- `missing`：expected 非空，但没有 active item；
- `stale`：version、sub-ID、hash、revision 或逻辑身份任一不一致；
- `orphan`：业务源已不可索引，但 index item 仍 active。

embedding 的 pending/failed 单独报告 `embedding_incomplete`，不把 lexical 正常内容误判为 stale。

新增正式入口：

```text
POST /api/v1/admin/rag/index-backfill/preview
POST /api/v1/admin/rag/index-backfill/enqueue
```

响应包含 `scanned/current/missing/stale/orphan/enqueued/next_cursor/done/reasons`，不返回召回正文。preview 只读；enqueue 只创建正式 replace/delete job，不直接修改 item 成功状态。

### Admin retry

新增：

```text
POST /api/v1/admin/rag/index-jobs/{job_id}/retry
```

请求要求 `expected_status`、`expected_updated_at` 和非空 `reason`。仅允许 `failed`，或 lease 已过期的 `running`。成功通过单条 CAS 更新为 `pending`，增加 `manual_retry_count`，清理 lease/lock/finished_at，保留 retry 历史和 source revision。状态变化与 `AdminAuditLog` 同事务提交。

错误合同：不存在为 404；状态、版本或有效 lease 冲突为 409；请求参数非法为 422；未知事务失败为固定 500。并发重复操作只能一条成功，另一条必须 409。响应、审计和日志不得暴露 lease token。

## Prompt Runtime 影响

Session Summary 当前使用代码内专用结构化 Prompt，canonical Prompt Runtime 中没有对应 `tasks/session_summary_*` 模板。本次不把它迁入 Runtime，避免在修复状态机时同时引入新的模板加载路径。

实现必须执行以下审计：

1. 检查 `prompts.v2.default/chat/*`、`tasks/*`、`tools/memory_query/usage.md`、变量注册和模板注册表。
2. 确认新增 inheritance 只属于 Session Summary 内部调用，不改变聊天主链路 runtime 变量或工具输出合同。
3. Recall Card canonical ID 由运行时覆盖；现有 MemoryDigest Prompt 已明确 `card_id` 仅在当前 digest 内唯一，因此不需要修改模板。
4. 若实现过程中实际改变模板变量、标记或工具返回结构，必须同批更新 canonical 默认模板，并通过正式 Runtime migration plan/apply 处理生产 override；不得直接复制覆盖 Runtime。

## 错误处理与可观测性

### 最终审查补充门禁

事务释放、worker 错误持久化与历史清除并发采用失败闭合策略：

- ORM Session 对原始 `TextClause` 采用只读 allowlist：仅允许无分号、剥离连续前导行/块注释后
  首 token 精确为 `SELECT` 的单条文本作为可证明只读。CTE、PRAGMA、EXPLAIN、多语句、未知
  方言和任何非 SELECT 文本都标记当前根事务或 savepoint 为“可能写入”，避免 clean release
  把已执行写入回滚；同时保留项目中 `text("SELECT 1")` 的 await 前事务释放合同。
- semantic worker 不持久化普通异常的 `str(exc)`。普通 `ValueError` 只保存
  `semantic_index_permanent_error:ValueError`，其他普通异常只保存固定前缀与异常类型；若未来
  需要保留业务错误码，必须使用专用异常类型和显式枚举，不能把任意字符串当作机器码。
- live rolling summary 写路径在单个 savepoint 中先取得 SQLite 写序列化点，再重新读取
  `User.history_clear_at`、确认全部 pending `ConversationTurn` 仍存在且属于预期 scope，并重新
  执行 active summary coverage CAS。任一 fence 变化都回滚 savepoint，不创建摘要、摘要任务或
  semantic job；历史清除变化返回稳定原因 `history_clear_changed`。
- rolling summary 先取得写序列化点时，后续 `mark-clear` 必须等待并在摘要提交后归档它；
  `mark-clear` 先提交时，旧 turns 的 rollup 必须被 fence 拒绝。禁止靠读取时过滤或事后补偿
  代替该串行化边界。
- `enqueue_index_job(commit=False)` 属于既有业务事务的一部分，不得在内部运行 DDL 或通过
  `Engine.begin()` 初始化 schema；migration、启动流程和 backfill 等独立维护入口必须在开启
  业务 unit-of-work 前显式准备 semantic schema，避免 StaticPool/SQLite 提交同一底层连接并
  破坏 savepoint 或原子写入。

- 配置错误只记录键名和错误类型，不记录 Token、header、URL 凭据或请求正文。
- Session Summary 失败使用稳定错误枚举：input manifest mismatch、request budget exceeded、state budget exceeded、inheritance gate failed、lost lease、LLM/JSON failure。
- `obsolete` 是正常竞争终态，不计为模型失败，也不允许 retry。
- Semantic worker 区分 retryable、permanent、superseded、lease_lost 和 embedding warning。
- job meta 只保存 ID、hash、计数、model、request log ID 和状态，不保存会话正文或召回正文。
- Admin backfill/retry 都写审计；只读 preview 不改状态。
- 所有时间字段在 API 中明确 UTC，最终部署报告同时换算 Asia/Shanghai。

## TDD 与验证策略

### 第一组：运行时与 push

- Dockerfile 和 CI 明确 Python 3.11。
- outbound worker 缺少 push Token 时失败闭合。
- transport 发送正确 Bearer，且重定向仍禁用。
- 日志、异常、outcome 和 repr 不包含 Token。
- Compose 只向 outbound worker 传入 push Token。
- 正确 Token 请求成功，错误/缺失 Token 返回 401；测试不得打印 Token。

### 第二组：Session Summary

- 单条超长 turn 完整分片，无字符丢失。
- 请求预算计入 system、previous、wrapper 和 headers。
- fragment manifest 未完整覆盖时不能 finalize。
- previous 优先读取结构化 JSON，不读取截断文本。
- 缺失、重复或未知 inheritance obligation 会拒绝摘要。
- obligation 可以合法转入 `resolved_items`。
- canonical state 超预算时失败，不静默截断。
- 延迟旧 job 变为 `obsolete`，不能覆盖更新摘要。
- 丢失 owner/lease 的 worker 不能 finalize。
- 保存实际响应模型和 request log ID，路由事后变化不影响记录。
- semantic enqueue flush 失败时，summary、job done 和 semantic job 全部回滚。

### 第三组：Semantic Index

- retryable 失败回到 `pending`，到期后可再次 claim。
- lease 过期被 B 重领后，A 不能写 item、FTS 或终态。
- heartbeat 只能由匹配 token 延期。
- recover 与 finish 竞争不能把 done 重开。
- FTS 失败会回滚旧索引删除、replace 和 job done。
- replace 删除消失 sub-ID 和旧 index version。
- source revision 变化后延迟 job 进入 `superseded`。
- 多张 Recall Card 产生不同、稳定的 sub-ID，重排不改变 ID。
- canonical Session Summary request/resolved 字段进入索引且不与 legacy 别名重复。
- 两个 summary revision 共用逻辑 source，但 document ID 不同。

### 第四组：backfill 与 Admin

- 超过一页的数据无遗漏、无重复。
- high-water 之后新增的数据不进入当前扫描。
- cursor version/manifest 不匹配时返回 422。
- 部分 chunk 缺失判为 stale，不判 missing。
- archived source 的 active item 进入 orphan delete。
- preview 零写入；enqueue 只创建正式 job。
- Admin retry 的 CAS 并发一成功一冲突。
- 有效 running lease、done、pending、warning、superseded 均拒绝 retry。
- ORM 建表与 migration 建表的列和索引一致，迁移重复执行幂等。

### 完整验证

在不连接生产数据库、不注入生产 Token 的隔离环境中执行：

```bash
python -B -m pytest tests/ -v -p no:cacheprovider
python -m compileall api app clients core workers
git diff --check
```

同时运行目标文件 Ruff、Prompt Runtime audit、镜像构建和容器内 Python 版本探针。完整测试必须 0 failures；时区敏感用例需要修正为显式 UTC，不通过强制全局 `TZ=UTC` 掩盖生产时区问题。

## 实施切分

1. Python 3.11 与 push Bearer 正式化。
2. Session Summary 纯契约层、真实模型结果和 coverage CAS。
3. Semantic job migration、retry 与 lease fencing。
4. 稳定 adapter 身份与原子 reconcile。
5. cursor backfill、Admin retry/preview/enqueue。
6. Prompt Runtime 契约审计、全量回归和部署证据脚本。

各阶段使用独立 TDD 红灯与聚焦回归，但生产只部署包含全部阶段的最终 HEAD。

## 部署顺序与混跑限制

1. 再次确认生产仓库和配置工作区状态，创建一致性数据库与配置备份。
2. 构建最终镜像并在隔离容器内完成测试和版本探针。
3. 在任何 Compose 命令前清除交互 shell 继承的 API、Admin 和 push Token，让权威 `.env` 成为唯一插值来源。
4. 停止旧 semantic worker，避免无 lease token 的旧进程与新 schema 混跑。
5. 使用新 server 执行 additive migration，验证迁移记录和表结构。
6. 以最小影响方式重建 server、outbound、session-summary、semantic-index 四个服务。
7. QQbot 已配置相同 push Token 时不需要代码重启；若配置源发生变化，只重启必要的 QQbot 进程。
8. 验证四个容器的镜像 ID、Git commit、dirty 标记、启动时间和日志。
9. 先闭环历史 Session Summary retry、一个新摘要和增量索引。
10. 再闭环受控 MemoryDigest 和显式 memory_query。
11. 生成 Persona、Group Memory、Recall Card 和旧索引 dry-run 报告。
12. 真实日报 occurrence、生产归档/重建和全量索引重建仍按原安全边界单独确认。

旧 semantic worker 禁止在 migration 后重新启动。由于 schema 为 additive，旧 server 可以读取数据库，但旧 semantic worker 不理解 fencing；若需要整体回滚，必须停止所有新旧 worker，并使用部署前数据库备份和旧镜像恢复，不能只切回旧 worker。

## 验收标准

1. 全量测试、Ruff、compileall、Prompt audit、镜像构建和 `git diff --check` 均通过。
2. 四个核心服务使用同一最终镜像、同一 Git commit、`dirty=false`，Python 为 3.11。
3. QQbot 实际 helper 请求不再 401；正确、错误 Token 的 HTTP 行为符合合同且无秘密回显。
4. 一个历史失败摘要和一个部署后新摘要成功保存，previous lineage、实际模型和完整 coverage 可由数据库证明。
5. 对应 semantic job 被 worker 领取并完成，旧 owner 无法越权结算。
6. 多张 Recall Card 产生多个唯一 active index item，不再覆盖为 `card:0`。
7. Session Summary canonical 请求和 resolved 字段能够召回，旧 archived revision 不再可召回。
8. backfill preview 能报告 missing/stale/orphan；未授权前不执行全量 enqueue 或索引重建。
9. Admin retry 通过正式接口完成，未直接 UPDATE 任务成功状态。
10. 任何没有数据库、HTTP、日志或容器证据的项都标为「尚未证明」，不得写成已恢复。

## 回滚与生产安全

- 代码回滚点包括部署前 commit、四个镜像 ID 和服务状态。
- 数据回滚点使用 SQLite 在线备份，记录路径、时间、大小、SHA-256 和 `integrity_check`。
- 配置备份位于仓库外且权限为 0600，不输出秘密原文。
- migration 后如需回滚，先停新旧 semantic worker，再恢复数据库备份和旧镜像。
- 不通过手工修改 job、outbox 或 scheduled task 成功状态伪造闭环。
- 不物理删除历史业务数据；清洗阶段只允许经批准的归档、禁用或状态标记。
- 真实 QQ 日报和生产清洗仍属于独立副作用操作，必须以对应 dry-run 证据为前提。
