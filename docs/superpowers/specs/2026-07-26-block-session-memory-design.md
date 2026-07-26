# 块式会话记忆(Block Session Memory)设计

状态:设计定稿;P1-P4 核心已实现(见文末实现状态),默认 kill-switch 关闭
范围:**私聊**(ConversationTurn);群聊完全短路,后议
日期:2026-07-26

## 一、目标与动机

当前私聊上下文按"最近 N 轮 + token"裁剪 raw window,窗口外压成**一条**滚动大摘要,更早历史靠 memory_query/sql_analysis 按需检索。问题:单条滚动摘要把不同时间段的话题糊在一起,召回不精准。

新架构把会话按**连续时间段**切成「块(block)」,让块成为长期记忆与召回的单元:

- 相邻消息间隔 < `BLOCK_GAP_SECONDS` 的连续消息为一个块;超阈值封口开新块。
- 触发回复时:召回**上一个块** + **RAG 相关历史块** + **当前块最近消息**。

## 二、两个正交系统

| | 系统 A:块内滚动压缩 | 系统 B:历史块记忆 |
|---|---|---|
| 作用域 | 当前 open 块内 | 已封口的历史块 |
| 目的 | 当前块原文超 token 时压缩早期部分,控制 prompt 体量 | 长期记忆单元 + 跨块召回 |
| 触发 | 块内 token 超限,随对话滚动更新 | 块封口时固化,一块一条 |
| 存储 | 复用 `rolling_session_summaries`(新增 `block_id` 列,游标 clamp 到块内) | 独立表 `conversation_block_episodes` |
| 衔接 | — | 封口时系统 A 的当前块压缩产物成为一条 episode 的初稿(条件固化或 LLM 精炼) |

关键约束:episode **绝不**写进 `rolling_session_summaries`——`get_best_session_summary` 只按 session_id 取最新 active,混入会被当"当前会话摘要"注入,污染系统 A。物理分表实现天然隔离。

## 三、数据模型(canonical)

### 3.1 `conversation_blocks` / `ConversationBlock`

```
id            INTEGER PK
session_id    VARCHAR  index
user_id       VARCHAR  index
chat_type     VARCHAR  default "private"
block_seq     INTEGER  会话内单调递增(1,2,3…)
status        VARCHAR  "open" | "closed" | "cleared"
open_key      VARCHAR  nullable UNIQUE  -- status==open 时=session_id,否则 NULL;
                                        -- SQLite 多 NULL 互异 => 每 session 至多一个 open 块
first_turn_id INTEGER  index  -- 块内首个 ConversationTurn.id
last_turn_id  INTEGER  index  -- 块内最后一个 turn.id(open 块随追加推进)
started_at    DATETIME        -- first turn 的 created_at
last_turn_at  DATETIME index  -- 块内最新 turn 的 created_at(gap 判定 + path② 时距 + idle sweep 唯一基准)
closed_at     DATETIME nullable -- 封口墙钟(仅生命周期用,不用于时距)
turn_count    INTEGER  -- eligible turn 累加(按 is_context_eligible_turn 口径)
token_estimate INTEGER -- estimate_tokens 累加,系统 A 压缩预算输入
closed_reason VARCHAR  -- "gap"|"idle_timeout"|"size"|"history_clear"|"manual"|"startup_recovery"
rolling_summary_id INTEGER nullable -- 系统 A 当前块滚动摘要指针
episode_id    INTEGER nullable -- 系统 B episode 外键
meta_json     TEXT
created_at / updated_at DATETIME
```

索引:`UNIQUE(open_key)`;`Index(session_id, status, id)`;`Index(session_id, block_seq)`;`Index(status, last_turn_at)`(idle sweep)。

### 3.2 `conversation_block_episodes` / `ConversationBlockEpisode`(唯一进语义索引的长期单元)

```
id            INTEGER PK
block_id      INTEGER index UNIQUE  -- 一块至多一条 episode
block_seq     INTEGER index
session_id / user_id VARCHAR index
chat_type     VARCHAR
status        VARCHAR  "active" | "archived"
summary_kind  VARCHAR  "deterministic_fallback" | "llm_episode"
llm_status    VARCHAR  "" | "pending" | "running" | "done" | "failed"  -- episode 是否已升级为 LLM 版本的权威字段
summary_text  TEXT     -- 注入模型的正文(path② 摘要形态与 RAG 召回读此)
summary_json  TEXT     -- 与 rolling summary 同 JSON 契约
covered_first_turn_id / covered_last_turn_id INTEGER
source_turn_ids_json / source_turn_count
seed_summary_id INTEGER nullable  -- 初稿来源的 rolling_session_summaries.id
quality_score / issues_json / model / prompt_sha256
stable_hash   VARCHAR index
source_revision VARCHAR  -- block_episode_source_revision(episode) 统一口径
created_at / sealed_at / refined_at / updated_at
meta_json     TEXT  -- {block_seq, opened_at, closed_at, first/last_turn_id, keywords, participants}
```

索引:`UNIQUE(block_id)`;`Index(session_id, status)`;`Index(user_id, session_id)`。

不变式:
- INV1 open 块永远没有 active episode(episode 只在封口事务内创建)。
- INV2 closed 块封口提交那一刻即有 active(fallback)episode(同步写)。
- INV3 一个 block_id 至多一条 episode(UNIQUE),重复封口 no-op。

### 3.3 `block_episode_jobs` / `BlockEpisodeJob`

字段/语义逐一对齐 `SessionSummaryJob`(status pending/running/done/failed/obsolete、locked_by/lease_token/lease_expires_at/generation/attempt_count、covered_from/until、fallback_summary_id→seed episode、result_episode_id、stable_hash、max_retry、next_retry_at)。独立表,复刻 fencing 租约 + finalize permit;不复用 session_summary_jobs 避免语义混。

### 3.4 `rolling_session_summaries` 新增列

- `block_id INTEGER index nullable` — 系统 A 当前块摘要归属;群聊/旧行为为 NULL。

### 3.5 迁移(`core/schema_migrations.py`)

新迁移版本 `20260726_block_session_memory`:
- 建三张新表(`_add_missing_columns` 只加列,不建表;建表走 `Base.metadata.create_all` 的新 model 注册即可,迁移里只需处理 `rolling_session_summaries.block_id` 加列)。
- **严格"加列先于读列":** 先部署含新表/新列的迁移,再部署读它们的代码。
- 迁移测试覆盖"全新库"与"从当前 master 升级"两条路径。

## 四、机制 1:块边界与 open 块状态维护

### 4.1 写路径分块(私聊,两条 choke point)

`assign_turns_to_block(db, *, session_id, user_id, chat_type, new_turn_ids, first_user_created_at) -> ConversationBlock` 必须在**两条**私聊写路径都调用:
- `api/chat_persistence.py::persist_chat_turn` 的 `operation()`
- `api/chat_persistence.py::persist_claimed_chat_turn` 的 `operation()`(评审抓出的漏点)

步骤(在 `add_conversation_turn` 之后、`commit()` 之前):
1. `db.flush()` 取 user/assistant 两条 turn 的 id(同一 exchange 视为原子,必进同一块)。
2. `operation()` 内(每次重试重读)查 open 块:`WHERE session_id=? AND open_key IS NOT NULL`。
3. 无 open 块 → INSERT(`block_seq = COALESCE(MAX,0)+1`,open_key=session_id)。
4. 有 open 块 → `gap = max(0, (new_user_turn.created_at - open.last_turn_at).total_seconds())`:
   - `gap >= BLOCK_GAP_SECONDS` 或 `turn_count+N > BLOCK_MAX_TURNS` 或 `token_estimate+Δ > BLOCK_MAX_TOKENS` → `close_block(reason)` 后 INSERT 新块。
   - 否则 → `UPDATE ... SET last_turn_id=?, last_turn_at=?, turn_count+=N, token_estimate+=Δ WHERE id=? AND last_turn_id=<期望值>`(compare-and-set);rowcount==0 → 抛锁风格异常让 `run_sqlite_locked_retry` 重读重试。

`gap = max(0, delta)` 处理时钟回拨/同秒。

### 4.2 并发(复用现有已验证机制)

- SQLite 单写:`run_sqlite_locked_retry` 指数退避;重试重读 open 块。
- 双开保护:`open_key` UNIQUE 冲突 → 失败者重试改走追加。
- 丢失更新:`last_turn_id` compare-and-set(等价 rolling_summary head-change 检测)。

### 4.3 封口:惰性为主 + idle sweep 兜底

- 惰性(热路径):下条消息 gap 超阈值时 `close_block`。
- idle sweep(冷尾兜底):复用 `app/session_memory/jobs.py` worker 模式,周期扫 `status='open' AND last_turn_at < now-BLOCK_IDLE_SEAL_SECONDS`,`close_block(reason="idle_timeout")`。
- 竞态:`close_block` = `UPDATE ... SET status='closed', open_key=NULL WHERE id=? AND status='open'`;rowcount==1 为赢家负责 enqueue episode,==0 no-op;episode 入队靠 stable_hash 幂等。

### 4.4 崩溃/中断恢复与 drift 对账

- open 块状态全在 DB,进程重启直接重读。
- turns + 块变更 + episode enqueue 同一事务,全成或全败。
- open 块永等不到下条消息 → idle sweep 封口。
- drift:读写时若 `MAX(ConversationTurn.id) > open.last_turn_id`(历史/群/旧代码写入未分块 turn)→ `reconcile_session_blocks` 从 last_turn_id 后按 gap 补分块(懒执行 + 一次性 backfill 脚本)。

### 4.5 history_clear 级联

`mark_clear` 现有事务内追加:把 `last_turn_at<=now` 的块置 `cleared`+open_key=NULL,对其 episode enqueue 语义删除;召回统一用 `after_clear_at` 栅栏。

## 五、机制 2:召回打分与拼装

统一入口 `build_block_memory_context(db, session_id, user_id, current_user_input, max_per_msg, max_total, read_only) -> (header_text, history_messages, debug)`,仅替换 `build_session_memory` 的**私聊分支**;群聊仍走 `build_session_memory(is_group=True)`。

拼装顺序(阅读序):`① RAG 相关历史块 episode(system,旧→次新)→ ② 上一块(近给原文/久给摘要,user/assistant role)→ ③ 当前块(系统A滚动摘要 header + raw 最近消息 role)→ ④ 当前用户输入`。

### 5.1 RAG 召回(复用 MemoryRagService,但需改三处)

`MemoryRagService.query(source="block", session_id, user_id, limit=BLOCK_RAG_TOP_K)`。**必须改**:
- `_source_types`:加 `"block"` → `{"block_episode"}`。
- `_group_by_parent`(memory_rag.py:606):加 `block_episode` 分支(独立 source 标签 + block_id 提取);现状把非 memory_digest 硬编码成 session_summary。
- scoring source_prior:`block_episode` fallback 0.35 / llm 0.7。
- recency 用 `source_updated_at=closed_at`,半衰期沿用 60d(recency 仅占 final 5%,近端交给上一块恒召回)。
- 索引适配器 `chunks_from_block_episode(episode)`(对齐 `chunks_from_session_summary`),仅 `llm_status in {done}` 或 fallback active 的 episode 产 chunk。

### 5.2 上一块恒召回 + 去重

- `get_previous_closed_block(db, session_id)` order by block_seq desc,不走 RAG、不过 gate、恒注入。
- 去重:RAG 结果按 block_id 排除 `{上一块, 当前 open 块}`。
- 原文 vs 摘要判据(**用 `last_turn_at` 不是 `closed_at`**):`now - last_turn_at <= PREV_BLOCK_RAW_MAX_AGE`(6h)且 raw token `<= PREV_BLOCK_RAW_TOKEN_BUDGET`(1500)→ 原文;否则 → `get_active_episode_for_block(block_id).summary_text`;episode 未 ready 且块陈旧 → 块尾 N 条 raw 兜底。

### 5.3 token 预算(与 raw_window_limits 对齐)

四路软上限:当前块 raw `CUR_BLOCK_TOKENS`、当前块滚动摘要 600、上一块 `PREV_BLOCK_TOKENS`、RAG 历史块 `RAG_BLOCKS_TOKENS`;当前块 raw 同受 `MAX_TURNS` 与 token 双约束(取先到)。瀑布裁剪序:RAG 历史块 < 上一块(原文→摘要→丢弃)< 当前块历史 < 当前块最新/输入。硬保底:当前输入 + 当前块滚动摘要 + 当前块最新 2 条。

### 5.4 降级 / 冷启动 / 超短输入

- reranker 失败:沿用现有 `allow_degraded`(fallback_reason=reranker_error);上一块+当前块+输入不依赖 reranker。
- 冷启动:无 episode → RAG 空 → 退化为上一块+当前块+输入;全新单 open 块 → 仅当前块+输入。
- 超短输入(< `BLOCK_RAG_MIN_QUERY_CHARS` CJK 4 字)→ 短路 RAG,仅靠上一块恒召回。

## 六、机制 3:系统 A↔B 交棒

### 6.1 系统 A 收窄到 open 块

- `rolling_session_summaries` 增 `block_id`;`get_best_session_summary(db, session_id, *, block_id=None, after_clear_at)` 增 block_id 过滤(None=群聊/旧行为)。
- raw window 起点 clamp:`after_turn_id = max(last_covered_id, open_block.first_turn_id-1)`,raw 与 pending 永不跨块。

### 6.2 封口交棒(事务内顺序)

1. `seed = get_best_block_rolling_summary(block_id)`(系统 A 该块最佳,llm 优先)。
2. **条件固化**:seed 是覆盖整块的 llm_episode(`covered_until == block.last_turn_id`)→ 直接建 `llm_episode` episode,免 LLM;否则建 `deterministic_fallback` episode(active)+ enqueue `BlockEpisodeJob` 覆盖整块。
3. `archive_active_summaries`(旧块 rolling summary)+ obsolete 该块 pending/running 的 SessionSummaryJob(防延迟完成往已封口块写 active)。
4. 语义索引:封口即对 fallback episode enqueue index(prior 0.35);LLM 完成后 enqueue replace(prior 0.7,delete_source_ids 覆盖旧)。

### 6.3 隐私/注入防护

episode 精炼复用 `audit_rolling_summary` + `sanitize_prompt_text`,防止历史工具调用/注入内容固化进长期召回单元。

## 七、常量默认值(`app/session_memory/config.py`)

```
BLOCK_GAP_SECONDS = 1800            # 30min,可配 1800-3600;与 CONTEXT_GAP_HINT_MIN=20min 正交
BLOCK_IDLE_SEAL_SECONDS = 7200      # 2h,须 >= BLOCK_GAP
BLOCK_MAX_TURNS = 200
BLOCK_MAX_TOKENS = 16000
BLOCK_RAG_TOP_K = 3
BLOCK_RAG_MIN_QUERY_CHARS = 4       # CJK
PREV_BLOCK_RAW_MAX_AGE_SECONDS = 21600  # 6h
PREV_BLOCK_RAW_TOKEN_BUDGET = 1500
PREV_BLOCK_SUMMARY_TOKEN_CAP = 500
CUR_BLOCK_TOKENS = 2400
RAG_BLOCKS_TOKEN_BUDGET = 1200
EPISODE_FALLBACK_SOURCE_PRIOR = 0.35
EPISODE_LLM_SOURCE_PRIOR = 0.70
BLOCK_SESSION_MEMORY_ENABLED = False  # kill-switch,默认关闭,fail-safe 回退现有 build_session_memory
```

## 八、横切关注点

1. **kill-switch**:`BLOCK_SESSION_MEMORY_ENABLED`(settings 可覆盖);关闭时私聊走现有 `build_session_memory`;块分配失败降级为"照常写 turn、跳过建块"(不阻塞回复)。
2. **backfill**:一次性幂等脚本(分批限速 + 与在线写路径封口并发协调),参照 `scripts/migrate-sandbox-project-map.py` 风格。
3. **可观测**:blocks_opened/closed、封口来源(write vs sweep)、episode 生成延迟/失败率、sweep 积压、召回四路命中率与 token 占用。
4. **半封口自愈**:检测"块 closed 但 episode job 未 enqueue""fallback 写成功但 LLM 永久失败""idle sweep 长期不运行冷尾堆积"。
5. **群聊零影响**:块逻辑只挂私聊,回归测试保证群路径不变。
6. **digest 悬挂引用**:episode 被 history_clear 删除/replace 升级后,引用它的 memory_digest 的失效/重建策略。

## 九、落地阶段(每阶段独立可测、可回退)

- **P1** 数据模型(3 表 + `rolling_session_summaries.block_id` 列)+ 迁移 + 两条写路径分块 + kill-switch。此阶段不改召回,纯建块,验证边界与并发。
- **P2** 系统 A 收窄到 open 块(block_id + 游标 clamp + 封口归档)。
- **P3** 封口交棒:fallback episode 同步写 + BlockEpisodeJob LLM 升级 + `block_episode` 语义索引全链路。
- **P4** 召回层:`build_block_memory_context` 替换私聊分支 + MemoryRagService 的 block 分支 + 四路拼装。
- **P5** backfill 脚本 + 可观测埋点 + 灰度调参。

## 九·五、实现状态(2026-07-26)

**已实现**(默认关闭,`BLOCK_SESSION_MEMORY_ENABLED=False`):

- P1 数据模型:`conversation_blocks` + `conversation_block_episodes` 表、
  `rolling_session_summaries.block_id` 列、迁移
  `20260726_block_session_memory_schema`;两条私聊写路径
  (`persist_chat_turn`/`persist_claimed_chat_turn`)经 `_assign_block_safely`
  (SAVEPOINT 内 best-effort,失败降级不阻塞回复)调用
  `app/session_memory/blocks.py::assign_turns_to_block`;gap/尺寸封口、
  时钟回拨防护、open_key 唯一 open 块。测试 `tests/test_session_blocks.py`。
- P2 系统 A 收窄:`get_best_session_summary`/`save_new_active_summary`/
  `archive_active_summaries_for_session`/`maybe_rollup_session_summary` 增
  `block_id=None` 参数(None=旧行为);LLM 升级(`save_llm_session_summary`)
  继承 fallback 的 block_id 并同块归档。测试 `tests/test_block_rolling_summary.py`。
- P3 封口交棒(核心):`app/session_memory/block_episodes.py::seal_block_to_episode`
  ——封口即产出 active episode(seed 为 LLM 摘要则条件固化为 `llm_episode`,
  否则 `deterministic_fallback`),归档该块滚动摘要,幂等(UNIQUE(block_id))。
  测试 `tests/test_block_episodes.py`。
- P4 召回(核心):`build_session_memory` 私聊分支按 open 块收窄系统 A、
  raw window clamp 到 `open_block.first_turn_id-1`,上一块 episode 摘要经
  `<previous_block_summary>` header 恒注入(标签已加入 `sanitize_prompt_text`
  转义表;canonical + 运行时 chat/main.md 已同步说明)。无块存量会话自动降级
  旧行为。测试 `tests/test_block_context.py`。

**实现偏差**:P4 未新建 `build_block_memory_context` 独立入口,而是直接在
`build_session_memory` 私聊分支内门控实现——复用已验证的 raw window/rollup/
history_clear 机制,改动面更小;上一块注入为摘要形态(episode summary_text),
未实现"6h 内给原文"分支。

- P5 灰度与回填:
  - 开关接 settings 托管配置:`block_memory.enabled`(bool,默认取
    `config.BLOCK_SESSION_MEMORY_ENABLED`)+ `block_memory.session_allowlist`
    (逗号分隔 session_id,非空时仅名单内会话启用)——统一入口
    `app/session_memory/blocks.py::is_block_memory_enabled(session_id)`,
    写路径/召回路径共用,运行时可改无需重启。
  - 存量回填脚本 `scripts/backfill_session_blocks.py`:只处理私聊会话
    (canonical 身份解析判定),幂等(已有块只补最早块之前的前缀,编号排
    在现有 seq 之前),按在线相同 gap/尺寸规则切块,历史块 closed
    (reason=backfill)+ 同步产出 deterministic episode,尾段仍热时保留
    open;与在线写路径冲突(open_key 唯一冲突)时该会话让位跳过。CLI 支持
    `--dry-run` / `--session` / `--limit-sessions` / `--database-url`,启动前
    校验迁移已应用(fail-closed)。测试 `tests/test_backfill_session_blocks.py`。
  - 可观测:块开/封、episode 产出的结构化日志(`[Block]`/`[BlockEpisode]`,
    含 reason/seq/turns/tokens/kind),召回侧 `block_memory_*` debug 字段。

**未实现(后续增强,不影响开关内已实现闭环)**:

- P3 余量:`BlockEpisodeJob` LLM 二次精炼、episode 进语义索引
  (`block_episode` source_type 全链路:`_source_types`/`_group_by_parent`/
  source_prior/`chunks_from_block_episode`)。当前 episode 质量 = 封口时系统 A
  最佳摘要(若该块已滚动出 LLM 摘要则为 LLM 质量)。
- P4 余量:RAG 相关历史块召回(①路)、上一块原文形态、四路 token 瀑布裁剪。
- P5 余量:idle sweep(冷尾块靠下条消息惰性封口;永久静默的 open 块暂不会
  封口,不影响正确性只延迟 episode 产出)、history_clear 级联清块(召回侧已
  有 after_clear_at 栅栏防泄漏)、指标面板化(当前为结构化日志)。

**上线步骤(服务器)**:① 部署新版本并启动(迁移自动应用,开关默认关)→
② `python scripts/backfill_session_blocks.py --dry-run` 预览后去掉 dry-run 执行
→ ③ 管理端 settings 设 `block_memory.enabled=true` +
`block_memory.session_allowlist=<测试会话>` 灰度 → ④ 观察 `[Block]` 日志与
召回 debug 后清空白名单全量。

## 十、测试矩阵(关键点)

- 块边界:gap 恰好等于阈值、跨天、乱序 created_at、时钟回拨、同秒。
- 双轨封口并发:write 与 sweep 同时 close_block 的 CAS。
- 双写路径:persist_chat_turn 与 persist_claimed_chat_turn 均正确分块。
- episode 幂等:重复 enqueue、fallback→llm replace。
- 召回:episode 缺失/降级 fallback、超短输入短路、上一块原文/摘要切换、四路 token 裁剪。
- history_clear 级联删除后无残留召回。
- 系统 A→B 交棒:seed 已 llm 时直接固化。
- 群聊路径完全不受影响回归。
- kill-switch 开关:关闭时私聊走旧路径。
- 迁移:全新库 + 从 master 升级两路径。
