# Session 摘要质量修复设计

## 背景

当前近期摘要采用同步 `deterministic_fallback` 加异步 `llm_episode` 的双路径。同步兜底摘要会把最近 `ConversationTurn` 直接格式化为带 turn_id、时间、role 的文本，因此管理页看起来混入大量原始对话。长期摘要 `memory_digests` 当前也是确定性构建，`important_details` 和 recall card 会使用原始消息片段，质量更接近摘录而非总结。

## 目标

1. 近期兜底摘要不再把 turn_id、时间、role 等原始行格式直接展示为摘要正文。
2. 管理页明确区分“代码兜底摘要”和“LLM 摘要”，避免误判质量。
3. 管理页提供手动触发 LLM 摘要生成/重新生成和失败 job 重试入口。
4. LLM 摘要提示词强调不要逐字复述原始对话，输出归纳式摘要。
5. 长期摘要后续生成时减少“代表消息”式原文暴露，改为更像结构化概括。

## 非目标

本次不重建长期摘要历史数据，不引入新的摘要表，也不把长期摘要生成整体改成 LLM 管线。已有低质量历史摘要需要后续单独做回填或重生成。

## 设计

### 近期摘要

`app/session_memory/summarizer.py` 继续保留确定性兜底，但把渲染用的 turn 行替换为干净片段：

- 去掉 `[turn_id]`、时间、role 标签。
- 去掉用户名前缀中的结构化标记。
- summary 文案明确这是“代码兜底摘要，等待 LLM 摘要提升”。
- `summary_json` 中保留 `evidence_turn_ids` 供审计，不在正文展示。

### LLM 摘要

`app/session_memory/llm_summarizer.py` 的系统提示词和用户提示词补充要求：

- 不逐字复述原始对话。
- 不输出 turn_id、时间戳、role 标签。
- 用主题、结论、待跟进、用户意图做归纳。

### 手动重生成

复用现有 `session_summary_jobs`：

- `POST /admin/session-memory/{session_id}/rolling-summary/enqueue-llm` 支持 `force` 和 `summary_id`。
- 没有 active fallback 时，可以基于 active LLM 摘要的 source turns 强制创建新 job。
- force 只绕过历史 `done` job；相同范围已有 pending/running 时仍返回已有 job，避免重复并发。

### Web 展示

`SessionSummaryBrowser` 增加：

- 摘要类型说明：代码兜底 / LLM 摘要。
- “生成/重新生成 LLM 摘要”按钮。
- job 列表和失败 job 重试按钮。
- 操作失败用页面内错误展示，不用裸 alert JSON。

## 测试

1. 确定性兜底摘要不包含 turn_id、时间戳、role 标签。
2. active LLM 摘要也可以 force enqueue 新 job。
3. force enqueue 不被 done job 阻断，但不重复 pending/running job。
4. Web 源码包含重生成和重试入口文案。
