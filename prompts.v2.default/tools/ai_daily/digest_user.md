---
name: AI 日报摘要证据卡片
version: 1
kind: tool
tool_name: ai_daily
description: ai_daily 结构化简报 LLM 的 user prompt。
---
## 证据卡片（{{ card_count }} 条）

{{ evidence_cards }}

## 要求
{{ mode_hint }}

只输出 JSON，不要 Markdown，不要代码块标记。
