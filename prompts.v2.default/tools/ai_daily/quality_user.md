---
name: AI 日报质量模式候选卡片
version: 1
kind: tool
tool_name: ai_daily
description: ai_daily quality 模式 LLM 的 user prompt。
---
## 候选新闻卡片（{{ card_count }} 条）

{{ candidate_cards }}

## 要求
生成 3-6 条 highlights、2-3 条 details、1-2 条 watchlist。
每条 highlight 100-150 字，必须写清楚：什么事、为什么重要、对谁有影响。要像新闻导语一样有信息量，不能只写标题。
每条 detail 必须有 known（已知信息 2-3 点）、unknown（缺失信息 0-2 点）、impact（一句话影响）。
details 的 source_labels 使用卡片中的“来源名（组）”格式。
只输出 JSON，第一个字符必须是 {，最后一个必须是 }。
