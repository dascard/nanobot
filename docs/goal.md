# Nanobot 长期摘要系统完整修改计划与示例 Prompt

## 一、核心结论

`memory_digests` 应按 `digest_source` 生成三级摘要。

默认规则：

```text
一个 digest_source
    → 生成 1 条 level 0 detailed_digest
    → 生成 1 条 level 1 preview_digest
    → 生成 N 条 level 2 recall_card
```

也就是说：

| 层级    | 数量 | 粒度                  | 主要用途             |
| ------- | ---: | --------------------- | -------------------- |
| level 0 | 1 条 | source 级详细摘要     | 人类审计、上下文回放 |
| level 1 | 1 条 | source 级短预览       | WebUI 列表快速浏览   |
| level 2 | 多条 | source 内原子记忆卡片 | RAG 精准召回         |

推荐的默认 `digest_source` 边界是：

```text
date + session_id
```

例如：

```text
2026-06-01 + session_A
    → 1 个 digest_source
    → 1 条 level 0
    → 1 条 level 1
    → 3-12 条 level 2 recall cards
```

不要每条消息都生成摘要，也不要默认把一整天所有 session 混成一个 source。

如果 session 很短，可以合并到 date 级摘要。

如果 session 很长，可以按 topic chunk 或 time window 再切成多个 source。

---

## 二、摘要链路职责划分

### 1. ChatLog

`ChatLog` 是原始消息档案库。

职责：

- 保存 ambient / user / assistant / tool 等完整原始记录。
- 适合作为长期摘要的数据来源。
- 适合用于 daily digest、历史审计、长期记忆沉淀。

长期摘要 `memory_digests` 应主要从 `ChatLog` 生成。

---

### 2. ConversationTurn

`ConversationTurn` 是工作上下文。

职责：

- 服务近期上下文。
- 服务 rolling summary。
- 用于对话压缩。

它不应替代 `ChatLog` 成为长期摘要的数据来源。

---

### 3. rolling_session_summaries

`rolling_session_summaries` 是近期摘要层。

职责：

- 压缩最近一段会话上下文。
- 由 `maybe_rollup_session_summary()` 触发。
- LLM 版本依赖 `session_summary_worker.py` 消费 pending jobs。
- 不是 daily digest 主链路。

不要把 rolling summary 和长期 daily digest 混为一谈。

---

### 4. memory_digests

`memory_digests` 是长期摘要层。

职责：

- 从 `ChatLog` 按日期、session、topic source 生成长期摘要。
- level 0 / level 1 给人看。
- level 2 给 RAG 精准召回。
- 是长期记忆沉淀和后续问答效果的关键来源。

---

## 三、三级摘要粒度定义

### level 0：详细摘要层

`level=0` 是完整详细摘要。

粒度：

```text
source 级
```

数量：

```text
每个 digest_source 1 条
```

推荐长度：

```text
500-1500 字
```

如果 source 内容很少，可以是 100-300 字。

如果 source 内容很多，不建议无限增长，最好压缩在 1500-2500 字以内。

用途：

- 人类查看。
- 审计。
- 回放当天或该 session 的主要上下文。
- 后续必要时作为 level 2 的回溯来源。

内容应包括：

- 用户主要做了什么。
- 讨论了什么问题。
- 尝试了什么方案。
- 最终形成了什么结论。
- 有哪些重要决策。
- 有哪些未完成事项。
- 和项目长期状态有关的上下文。

不应包括：

- 原始聊天流水账。
- 大段 URL。
- 工具调用参数。
- 报错堆栈全文。
- 无意义寒暄。
- LLM 编造的新事实。

---

### level 1：预览摘要层

`level=1` 是短预览摘要。

粒度：

```text
source 级
```

数量：

```text
每个 digest_source 1 条
```

推荐长度：

```text
100-300 字
```

内容很少时可以是 30-100 字。

用途：

- WebUI 列表展示。
- 快速判断该 digest_source 主要讲什么。
- 辅助人工浏览和筛选。

内容应包括：

- source 的 1-3 个主要主题。
- 最关键的结论。
- 最重要的修改方向或待办。

不应包括：

- 多段详细推理。
- 过细代码路径。
- 工具调用细节。
- 与主题无关的背景。

---

### level 2：召回卡片层

`level=2` 是 RAG 主召回层。

粒度：

```text
source 内原子记忆卡片
```

数量：

```text
每个 digest_source 多条
```

推荐数量：

```text
默认 3-12 条
内容很少时 1-3 条
内容很多时最多不超过 20 条
```

推荐长度：

```text
每条 30-120 字
```

用途：

- RAG 精准召回。
- 长期记忆检索。
- 后续回答用户问题时优先命中。

每条 recall card 只能表达一个稳定信息：

- 一个项目事实。
- 一个用户决策。
- 一个设计原则。
- 一个长期偏好。
- 一个待办事项。
- 一个模块职责。
- 一个链路结论。

好的 recall card 示例：

```text
nanobot 的长期摘要 memory_digests 应主要从 ChatLog 按日期和 session_id 聚合生成，不应依赖 rolling_session_summaries 作为 daily digest 主链路。
```

```text
memory_digests 的 level=2 应作为 RAG 主召回层，内容需要拆成原子化 recall cards，每条只表达一个稳定事实、决策、偏好或待办。
```

不好的 recall card 示例：

```text
今天讨论了很多摘要相关问题。
```

```text
用户希望系统更好。
```

```text
需要优化代码。
```

原因是这些句子太泛，缺少可检索关键词，也不能单独支撑回答。

---

## 四、digest_source 设计

### 1. 默认 source 边界

推荐默认：

```text
source = date + session_id
```

例如：

```text
date: 2026-06-01
session_id: nanobot-memory-refactor-001
```

生成：

```text
source_id = hash(date + session_id + source_range + digest_version)
```

入库结构：

```text
source_id = xxx
level = 0
content = detailed_digest

source_id = xxx
level = 1
content = preview_digest

source_id = xxx
level = 2
content = recall_card_1

source_id = xxx
level = 2
content = recall_card_2

source_id = xxx
level = 2
content = recall_card_3
```

---

### 2. 短 session 合并规则

如果一个 session 内容很短，比如只有几条无明显主题的消息，可以合并到 date 级 source。

建议规则：

```text
如果 session message_count < MIN_SESSION_MESSAGES
并且没有明确 topic
则合并到 date_misc source
```

例如：

```text
2026-06-01 + misc
```

---

### 3. 长 session 切分规则

如果一个 session 很长，包含多个明显主题，不建议只生成一个超长 source。

可以切成：

```text
date + session_id + topic_index
```

或：

```text
date + session_id + time_window
```

例如：

```text
2026-06-01 + session_A + topic_01_memory_digest
2026-06-01 + session_A + topic_02_prompt_v2
2026-06-01 + session_A + topic_03_webui_debug
```

切分目标不是越碎越好，而是保证：

- level 0 能完整覆盖一个主题。
- level 2 cards 不互相污染。
- RAG 召回能命中准确主题。

---

## 五、长期摘要生成链路

目标链路：

```text
ChatLog 原始消息
    ↓
按 date / session_id / topic 聚合
    ↓
生成 digest_source
    ↓
MemoryDigestBuilder 清洗与规则兜底
    ↓
Prompt V2 渲染 system/user prompt
    ↓
LLM 生成结构化 JSON
    ↓
代码审计 JSON、字段、质量、污染
    ↓
审计通过：写 generator=llm
审计失败：写 generator=deterministic_fallback
    ↓
写入 memory_digests level 0 / level 1 / level 2
```

---

## 六、Prompt 迁移要求

当前如果存在类似：

```text
app/memory_digest/llm_builder.py
LLM_MEMORY_DIGEST_SYSTEM_PROMPT
build_llm_digest_messages()
```

这只能作为临时实现，不应作为最终形态。

需要迁移到 Prompt V2 模板体系。

推荐新增：

```text
prompts.v2.default/tasks/memory_digest_system.md
prompts.v2.default/tasks/memory_digest_user.md
```

运行时通过项目已有模板渲染函数加载，例如：

```text
render_tool_execution_template()
```

或项目已有同类函数。

要求：

1. 正常情况下从 Prompt V2 加载。
2. Python 文件中不保留大段 prompt。
3. Python 里只保留极短 fallback prompt。
4. 模板缺失时记录 warning。
5. 模板缺失不应导致任务崩溃。
6. prompt 版本、模板名应能记录到 digest metadata 中。

---

## 七、LLM 输出结构

LLM 应输出严格 JSON，不要 Markdown，不要解释性文字。

推荐结构：

```json
{
  "preview": "level 1 preview digest",
  "long_summary": "level 0 detailed digest",
  "recall_cards": [
    "level 2 recall card 1",
    "level 2 recall card 2",
    "level 2 recall card 3"
  ],
  "quality": {
    "score": 0.0,
    "reason": "quality reason"
  }
}
```

字段含义：

| 字段           | 对应层级 | 说明              |
| -------------- | -------- | ----------------- |
| `long_summary` | level 0  | source 级详细摘要 |
| `preview`      | level 1  | source 级短预览   |
| `recall_cards` | level 2  | 多条原子召回卡片  |
| `quality`      | metadata | 质量分和原因      |

---

## 八、LLM 输出审计

LLM 输出不能直接写库，必须审计。

审计项：

1. 是否为合法 JSON。
2. 是否包含必填字段：
   - `preview`
   - `long_summary`
   - `recall_cards`
   - `quality`

3. `preview` 是否为空或过短。
4. `long_summary` 是否为空。
5. `recall_cards` 是否为数组。
6. recall cards 数量是否合理。
7. recall card 是否过长。
8. recall card 是否过泛。
9. recall card 是否包含污染信息：
   - URL 堆砌；
   - 文件路径堆砌；
   - 日志路径；
   - 报错堆栈；
   - tool call 参数；
   - 原始 JSON 参数；
   - 无意义模板话术。

10. `quality.score` 是否达到阈值。
11. 是否出现 digest_source 中没有依据的新事实。

审计通过：

```text
generator = llm
fallback_reason = null
```

审计失败：

```text
generator = deterministic_fallback
fallback_reason = 具体失败原因
```

---

## 九、deterministic fallback 要求

不要删除规则摘要。

fallback 至少承担三种职责：

1. LLM 关闭时可用。
2. LLM 调用失败时兜底。
3. LLM 输出质量不合格时兜底。

建议配置：

```text
MEMORY_DIGEST_LLM_ENABLED=true
MEMORY_DIGEST_LLM_MIN_QUALITY=0.75
MEMORY_DIGEST_MAX_RECALL_CARDS=12
MEMORY_DIGEST_MAX_LONG_SUMMARY_CHARS=2500
MEMORY_DIGEST_PROMPT_TEMPLATE_ENABLED=true
```

如果项目已有统一配置命名风格，应沿用项目风格。

---

## 十、入库字段建议

`memory_digests` 建议至少能记录：

```text
id
date
session_id
source_id
source_type
source_range
level
summary_type
content
generator
quality_score
prompt_template
prompt_version
fallback_reason
created_at
updated_at
```

其中：

```text
level=0, summary_type=detailed_digest
level=1, summary_type=preview_digest
level=2, summary_type=recall_card
```

---

## 十一、WebUI 展示建议

WebUI 中建议展示：

1. 日期。
2. session_id。
3. source_id。
4. level。
5. summary_type。
6. generator。
7. quality_score。
8. prompt_template。
9. prompt_version。
10. fallback_reason。
11. recall card 数量。
12. 原始 source 的 message_count。

这样可以快速判断：

- daily digest 是否跑了；
- LLM 是否开启；
- prompt 是否加载成功；
- 是否发生 fallback；
- 失败原因是什么；
- level 2 是否真的适合 RAG 召回。

---

## 十二、RAG 召回策略

RAG 应优先召回：

```text
memory_digests level=2
```

原因：

- level 2 是原子化记忆。
- 噪声少。
- 关键词密度高。
- 可独立用于回答。

推荐召回流程：

```text
用户问题
    ↓
向量 / BM25 / 混合检索 level 2 recall cards
    ↓
命中相关 source_id
    ↓
必要时回溯同 source_id 的 level 0
    ↓
组织回答
```

不要默认把 level 0 长摘要直接作为主召回层，否则容易引入长文本噪声。

---

## 十三、测试要求

### 1. 单元测试

至少覆盖：

1. Prompt V2 模板能正常加载。
2. 模板缺失时 fallback prompt 生效。
3. LLM 返回合法 JSON 时写入 `generator=llm`。
4. LLM 返回非法 JSON 时 fallback。
5. LLM 返回缺字段 JSON 时 fallback。
6. LLM 返回低质量摘要时 fallback。
7. recall cards 中 URL 污染能被拦截。
8. recall cards 中日志路径能被拦截。
9. 空 source 不生成错误摘要。
10. 同一 source 只生成一条 level 0 和一条 level 1。

---

### 2. 集成测试

准备一天真实或模拟 `ChatLog` 数据，执行：

```text
generate_daily_digest_for_date()
```

检查：

1. 是否生成 `memory_digests`。
2. 每个 source 是否只有一条 level 0。
3. 每个 source 是否只有一条 level 1。
4. 每个 source 是否有多条 level 2。
5. level 2 是否适合作为 RAG recall cards。
6. generator 是否正确。
7. fallback reason 是否可追踪。
8. WebUI 是否能展示结果。

---

### 3. 回归测试

确认不要破坏：

1. ChatLog 写入。
2. ConversationTurn 近期上下文。
3. rolling_session_summaries。
4. session_summary_jobs。
5. session_summary_worker.py。
6. RAG 对 `memory_digests level=2` 的读取。

---

## 十四、验收标准

修改完成后应满足：

1. daily digest 从 ChatLog 生成长期摘要。
2. 每个 digest_source 默认生成 1 条 level 0、1 条 level 1、多条 level 2。
3. level 0 能让人理解 source 内发生了什么。
4. level 1 能让 WebUI 快速展示主题。
5. level 2 能作为 RAG 主召回卡片。
6. memory digest prompt 已迁移到 Prompt V2。
7. Python 中不再保留大段 memory digest prompt。
8. LLM 输出经过 JSON 和质量审计。
9. LLM 失败或输出不合格时 deterministic fallback 生效。
10. generator、quality、prompt_template、fallback_reason 可见。
11. 不破坏 rolling summary 链路。
12. 后续调 prompt 不需要改业务代码。

---

# 示例 Prompt

下面是建议放入 Prompt V2 的两个模板。

---

## memory_digest_system.md

```text
You are a memory digest generator for a personal long-term memory system.

Your task is to convert a cleaned conversation source into a three-level memory digest.

The system has three digest levels:

1. level 0: detailed_digest
   - One per digest_source.
   - A detailed source-level summary.
   - Used for human review, audit, and context replay.
   - It should explain what the user worked on, what problems were discussed, what decisions were made, what solutions were considered, and what follow-up items remain.
   - Recommended length: 500-1500 Chinese characters. If the source is small, shorter is allowed.

2. level 1: preview_digest
   - One per digest_source.
   - A short source-level preview for WebUI lists.
   - It should summarize the top 1-3 themes and the most important conclusions.
   - Recommended length: 100-300 Chinese characters.

3. level 2: recall_card
   - Multiple per digest_source.
   - Atomic memory cards for RAG retrieval.
   - Each card must express exactly one stable fact, decision, preference, design rule, module responsibility, or follow-up task.
   - Each card should be independently understandable.
   - Each card should include concrete searchable keywords.
   - Recommended length: 30-120 Chinese characters per card.
   - Recommended count: 3-12 cards. If the source has little useful content, output 1-3 cards. Do not output more than 20 cards.

Important rules:

- Output strict JSON only.
- Do not wrap JSON in Markdown.
- Do not include explanations outside JSON.
- Do not invent facts not supported by the source.
- Do not include raw URLs unless the URL itself is the stable memory.
- Do not include log paths, stack traces, tool call arguments, raw JSON parameters, or temporary noise in recall cards.
- Do not create vague recall cards such as "the user discussed many things" or "the system needs optimization".
- If the source contains little useful long-term information, generate fewer recall cards.
- The preview must be consistent with the detailed summary.
- The recall cards must be grounded in the source and consistent with the detailed summary.
- Prefer Chinese output unless the source is mostly English technical content.
- Keep project names, table names, function names, file names, and configuration names accurate.

Return JSON in this exact shape:

{
  "preview": "string, level 1 preview digest",
  "long_summary": "string, level 0 detailed digest",
  "recall_cards": [
    "string, level 2 atomic recall card"
  ],
  "quality": {
    "score": 0.0,
    "reason": "string"
  }
}

Quality score guidance:

- 0.90-1.00: complete, specific, well-grounded, good recall cards.
- 0.75-0.89: usable, mostly specific, minor omissions.
- 0.50-0.74: weak, too generic, incomplete, or some recall cards are poor.
- below 0.50: not reliable.

If the source is insufficient, still return valid JSON, but use a lower quality score and fewer recall cards.
```

---

## memory_digest_user.md

```text
Generate a three-level memory digest for the following digest_source.

Metadata:

date: {{ date }}
session_id: {{ session_id }}
source_id: {{ source_id }}
source_type: {{ source_type }}
source_range: {{ source_range }}
message_count: {{ message_count }}

Generation requirements:

- Generate exactly one "long_summary" for level 0.
- Generate exactly one "preview" for level 1.
- Generate multiple "recall_cards" for level 2.
- The recall cards should be atomic, concrete, independently understandable, and useful for future RAG retrieval.
- Do not produce one recall card per message.
- Do not include temporary noise, raw tool outputs, stack traces, or irrelevant URLs.
- Do not invent facts.
- Use fewer recall cards if the source has little durable information.
- Prefer preserving concrete identifiers such as project names, table names, module names, function names, and configuration names.

Cleaned digest_source:

{{ digest_source }}

Optional existing hints or previous digest context:

{{ existing_digest_hint }}

Now output strict JSON only.
```

---

# 示例输入

```text
date: 2026-06-01
session_id: nanobot-memory-refactor-001
source_id: 20260601_nanobot_memory_refactor_001
source_type: date_session
source_range: 2026-06-01 session nanobot-memory-refactor-001
message_count: 18

用户要求先讲清楚 nanobot 的摘要生成链路，并质疑为什么 memory digest 的 prompt 被硬编码到 Python 文件中。

讨论中明确：
1. ChatLog 是原始消息档案库，保存 ambient / user / assistant / tool 等完整记录。
2. ConversationTurn 是工作上下文，用于近期上下文和 rolling summary。
3. rolling_session_summaries 是近期摘要，不是 daily digest 主链路。
4. rolling summary 由 maybe_rollup_session_summary 触发，LLM 摘要依赖 session_summary_worker 消费 pending jobs。
5. memory_digests 是长期摘要，主要由 generate_daily_digest_for_date 从 ChatLog 按日期生成。
6. memory_digests 的 level 2 recall cards 是 RAG 长期记忆召回的主来源。
7. 当前把 memory digest prompt 硬编码在 app/memory_digest/llm_builder.py 只是临时打通链路的短路径，不是最终设计。
8. 正确做法是迁移到 Prompt V2 模板体系，例如 memory_digest_system.md 和 memory_digest_user.md。
9. Python 代码里只应保留极短 fallback prompt，避免模板缺失时任务崩溃。
10. LLM 输出不能直接写库，需要经过 JSON 解析、字段完整性、质量分、URL 污染、空泛话术等审计。
11. 审计通过写 generator=llm，失败写 generator=deterministic_fallback。
12. 用户进一步要求先明确 memory_digests 三级分层的粒度大小。
13. 最终确认：
    - level 0 是详细摘要层，负责看懂当天发生了什么；
    - level 1 是预览摘要层，负责 WebUI 快速浏览；
    - level 2 是召回卡片层，负责 RAG 精准召回。
14. 同一个 digest_source 只生成一条 level 0 和一条 level 1，但可以生成多条 level 2 recall cards。
```

---

# 示例输出

```json
{
  "preview": "本次讨论梳理了 nanobot 的长期摘要生成链路，明确 ChatLog、ConversationTurn、rolling_session_summaries 和 memory_digests 的职责边界，并确认 memory digest prompt 不应硬编码在 Python 中，而应迁移到 Prompt V2 模板体系。同时定义了 memory_digests 的三级粒度：level 0 用于详细审计，level 1 用于 WebUI 预览，level 2 用于 RAG 精准召回。",
  "long_summary": "本次讨论围绕 nanobot 的长期记忆摘要系统展开。用户首先要求讲清楚当前摘要生成链路，并指出 memory digest 的 prompt 被硬编码到 Python 文件中是不合理的。链路分析后明确：ChatLog 是完整原始消息档案库，保存 ambient、user、assistant、tool 等完整记录，适合作为长期摘要的数据来源；ConversationTurn 是工作上下文，主要服务近期上下文和 rolling summary；rolling_session_summaries 属于近期摘要，由 maybe_rollup_session_summary 触发，并且 LLM 版本依赖 session_summary_worker 消费 pending jobs；memory_digests 才是 daily digest 和长期记忆沉淀的主链路，应由 generate_daily_digest_for_date 从 ChatLog 按日期和 session 聚合生成。\n\n讨论进一步确认，memory_digests 的 level 2 recall cards 是 RAG 长期召回的主来源，因此长期摘要质量会直接影响后续问答效果。当前把 memory digest prompt 写在 app/memory_digest/llm_builder.py 中，只能视为临时打通链路的短路径，不符合项目已有 Prompt V2 / WebUI 模板体系。正确方向是新增 memory_digest_system.md 和 memory_digest_user.md 等模板，运行时通过现有模板渲染函数加载，代码中只保留极短 fallback，避免模板缺失时任务崩溃。\n\n用户随后要求先明确三级分层的粒度大小。最终定义为：level 0 是详细摘要层，面向人类审计和上下文回放，通常为 500-1500 字；level 1 是预览摘要层，面向 WebUI 列表和快速判断主题，通常为 100-300 字；level 2 是原子化召回卡片层，面向 RAG 精准召回，每条 30-120 字，默认每个 digest_source 生成 3-12 条。三层应同源生成、粒度递减；同一个 digest_source 只生成一条 level 0 和一条 level 1，但可以生成多条 level 2 recall cards；level 2 不得编造 digest_source 中没有依据的新事实。",
  "recall_cards": [
    "nanobot 的长期摘要 memory_digests 应主要从 ChatLog 按日期和 session_id 聚合生成，不应依赖 rolling_session_summaries 作为 daily digest 主链路。",
    "ChatLog 在 nanobot 中承担原始消息档案库职责，保存 ambient、user、assistant、tool 等完整记录，适合作为长期摘要的数据来源。",
    "ConversationTurn 在 nanobot 中主要承担工作上下文职责，用于近期上下文和 rolling summary，不应替代 ChatLog 成为长期记忆摘要来源。",
    "rolling_session_summaries 是近期摘要层，由 maybe_rollup_session_summary 触发，LLM 摘要依赖 session_summary_worker 消费 pending jobs。",
    "memory_digests 的 level 0 应是详细摘要层，面向人类审计和上下文回放，用于说明 source 内主要任务、问题、方案、决策和未完成事项。",
    "memory_digests 的 level 1 应是预览摘要层，面向 WebUI 列表快速浏览，用 100-300 字概括 source 的 1-3 个核心主题。",
    "memory_digests 的 level 2 应是 RAG 主召回层，由多条原子 recall cards 组成，每条只表达一个稳定事实、决策、偏好或待办。",
    "同一个 digest_source 应只生成一条 level 0 detailed_digest 和一条 level 1 preview_digest，但可以生成多条 level 2 recall_card。",
    "memory digest 的 LLM prompt 不应长期硬编码在 app/memory_digest/llm_builder.py 中，应迁移到 Prompt V2 模板体系以支持 WebUI 编辑、版本管理和热更新。",
    "memory digest 的 Python 代码中只应保留极短 fallback prompt，用于模板缺失或渲染失败时避免任务崩溃。",
    "memory digest 的 LLM 输出不能直接写库，必须经过 JSON 解析、字段完整性、质量分、URL 污染和空泛话术审计。",
    "memory digest 审计通过时应写入 generator=llm，审计失败或 LLM 调用失败时应写入 generator=deterministic_fallback。"
  ],
  "quality": {
    "score": 0.92,
    "reason": "摘要覆盖了摘要链路、表职责、三级粒度、Prompt V2 迁移、LLM 审计和 fallback 规则；recall cards 具体、可独立召回，未包含 URL、日志路径、工具参数或无依据事实。"
  }
}
```

---

# 代码层写库示例

LLM JSON 审计通过后，应拆成多条 `memory_digests` 记录。

```json
[
  {
    "date": "2026-06-01",
    "session_id": "nanobot-memory-refactor-001",
    "source_id": "20260601_nanobot_memory_refactor_001",
    "level": 0,
    "summary_type": "detailed_digest",
    "content": "<long_summary>",
    "generator": "llm",
    "quality_score": 0.92,
    "prompt_template": "tasks/memory_digest_system.md + tasks/memory_digest_user.md",
    "fallback_reason": null
  },
  {
    "date": "2026-06-01",
    "session_id": "nanobot-memory-refactor-001",
    "source_id": "20260601_nanobot_memory_refactor_001",
    "level": 1,
    "summary_type": "preview_digest",
    "content": "<preview>",
    "generator": "llm",
    "quality_score": 0.92,
    "prompt_template": "tasks/memory_digest_system.md + tasks/memory_digest_user.md",
    "fallback_reason": null
  },
  {
    "date": "2026-06-01",
    "session_id": "nanobot-memory-refactor-001",
    "source_id": "20260601_nanobot_memory_refactor_001",
    "level": 2,
    "summary_type": "recall_card",
    "content": "nanobot 的长期摘要 memory_digests 应主要从 ChatLog 按日期和 session_id 聚合生成，不应依赖 rolling_session_summaries 作为 daily digest 主链路。",
    "generator": "llm",
    "quality_score": 0.92,
    "prompt_template": "tasks/memory_digest_system.md + tasks/memory_digest_user.md",
    "fallback_reason": null
  }
]
```

---

# fallback 示例

如果 LLM 输出非法 JSON，或者缺少 `recall_cards` 字段，应写入 fallback。

```json
{
  "date": "2026-06-01",
  "session_id": "nanobot-memory-refactor-001",
  "source_id": "20260601_nanobot_memory_refactor_001",
  "level": 1,
  "summary_type": "preview_digest",
  "content": "用户讨论了 nanobot 摘要系统的链路、长期摘要生成、Prompt V2 迁移、LLM 输出审计和 memory_digests 三级分层粒度。",
  "generator": "deterministic_fallback",
  "quality_score": 0.62,
  "prompt_template": "tasks/memory_digest_system.md + tasks/memory_digest_user.md",
  "fallback_reason": "LLM output failed JSON validation: missing recall_cards field"
}
```

---

# 给 Coding Agent 的执行顺序

建议按以下顺序实施：

1. 阅读现有 Prompt V2 模板加载方式。
2. 新增 `memory_digest_system.md` 和 `memory_digest_user.md`。
3. 重构 `app/memory_digest/llm_builder.py`，移除大段硬编码 prompt。
4. 接入 Prompt V2 渲染和短 fallback prompt。
5. 明确 `digest_source = date + session_id` 的默认边界。
6. 实现同一 source 生成 1 条 level 0、1 条 level 1、多条 level 2。
7. 增加 LLM JSON 审计。
8. 增加质量分、fallback_reason、prompt_template、source_id 记录。
9. 更新 WebUI 展示字段。
10. 更新 RAG 召回逻辑，优先召回 level 2。
11. 补充单元测试和集成测试。
12. 用真实一天 ChatLog 跑一次，人工检查 level 2 recall cards 是否真的可召回。13.测试完成后直接提交
