---
name: AI 日报质量模式候选卡片
version: 2
kind: tool
tool_name: ai_daily
description: ai_daily quality 模式 LLM 的 user prompt。
---
## 候选新闻卡片（{{ card_count }} 条）

{{ candidate_cards }}

## 要求
生成 1-6 条 highlights、1-3 条 details、0-2 条 watchlist；条目数不得超过候选卡片能独立支撑的事件数，候选不足时必须少写。
每条 highlight 100-150 字，必须写清楚：什么事、为什么重要、对谁有影响。要像新闻导语一样有信息量，不能只写标题。
每条 detail 必须有 known（已知信息 2-3 点）、unknown（缺失信息 0-2 点）、impact（一句话影响）。
details 的 source_labels 使用卡片中的“来源名（组）”格式。
source_ids 只能填写“来源 #数字”中的整数，例如来源 #1 必须写 [1]；importance 只能填写 1-5 的整数。
只输出 JSON，第一个字符必须是 {，最后一个必须是 }。
