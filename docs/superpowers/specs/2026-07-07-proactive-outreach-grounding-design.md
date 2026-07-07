# 主动情感外呼 Grounding 增强设计

## 目标

让主动情感外呼喂给 Judge 和 Generator 的上下文从「原始 JSON 堆」变成「可直接使用的信息」。本次只增强 `core/proactive_outreach.py` 和测试，不修改 `vendor/`，不 monkeypatch KT，不新增 shadow、语义闸或禁止话术清单。

## C1：时间锚点

`build_outreach_grounding()` 增加 `now` 参数，默认仍使用当前时间，但测试可以传入固定时间。返回值新增：

- `now`：包含 ISO 时间、星期几、小时和时段描述。
- `hours_since_last_user_message`：距最近用户消息的小时数，找不到时为 `None`。
- `last_user_message`：最近一条用户消息的内容、时间和截断标记。
- `days_since_last_outreach`：距最近主动外呼记录的天数，找不到时为 `None`。

时段描述使用本地固定分段：深夜、清晨、上午、午后、傍晚、夜晚。

## C2：近期可跟进话题

新增 `extract_recent_threads(recent_messages, *, llm_call=None) -> list[str]`。函数从最近对话里提炼 1 到 3 个未完话题、近期事件或可自然跟进点：

- `recent_messages` 为空时直接返回 `[]`。
- 默认 `llm_call` 使用 `call_model_route(route_key="timing_proactive", ...)`。
- prompt 要求输出 JSON 数组。
- 解析失败、LLM 报错或结果为空时返回 `[]`，不阻断外呼链路。
- 返回项做字符串化、去空、截断和最多 3 条限制。

`build_outreach_grounding()` 默认调用该函数，并把结果放入 `recent_threads`。

## C3：Prompt 与 next_check

Judge prompt 改为优先使用 `recent_threads` 和时间锚点。它允许反向判断：刚聊过、深夜、没有真正话题时倾向不发。这里是决策提示，不是安全闸，也不是禁止话术清单。

Judge 输出字段改为推荐 `next_check_in_hours`。`judge_outreach()` 内部优先解析相对小时数并转换成绝对 `next_check_at`，再走现有 `_clamp_next_check_at()`；如果旧模型仍输出 `next_check_at`，继续兼容。

Generator prompt 改为要求从 `recent_threads` 或 persona 中挑一个具体锚点，并避免重复 `last_outreach.message` 的话题或措辞。它保持正面示范风格，继续允许 bot 表达自身状态。

## C4：画像维度 TODO

`personas` 当前主要承载稳定画像。未来可以在 persona 生成 pipeline 增加 `recent_event` / `open_thread` 这类时效性 fact 类型，减少实时 `recent_threads` 提炼的依赖。本轮不实现 persona pipeline 改造。

## 验收

- C1：固定 `now` 下，时间锚点字段计算正确。
- C2：话题提炼正常、空消息、LLM 失败三条路径都有测试。
- C3：`next_check_in_hours` 转 ISO 并受钳制；prompt 包含 recent_threads、时间锚点和避免重复上次外呼的指引。
- 全量 `python -m pytest tests/ -v` 0 failures。
