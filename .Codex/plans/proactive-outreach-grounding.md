# 主动情感外呼 Grounding 增强实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:test-driven-development 执行本计划。步骤使用复选框（`- [ ]`）语法跟踪进度。本轮用户明确要求新任务不 commit，完成后停下交人工 review。

**目标：** 把主动情感外呼的 grounding 从原始 JSON 堆增强为包含时间锚点和近期话题提炼的可用信息。

**架构：** 在 `core/proactive_outreach.py` 内保持单模块实现，新增时间描述 helper、`extract_recent_threads()` 和 `next_check_in_hours` 解析 helper。`build_outreach_grounding()` 负责生成时间锚点和 recent_threads；Judge/Generator prompt 只增强正面决策指引，不加入安全闸。

**技术栈：** Python、SQLAlchemy ORM、pytest、现有 `call_model_route` 和 `strip_think_blocks`。

---

## 文件结构

- 修改：`core/proactive_outreach.py`
  - `build_outreach_grounding()` 增加 `now` 和 `thread_extractor` 参数。
  - 新增 `_time_period_label()`、`_weekday_label()`、`_last_user_message()` 等 helper。
  - 新增 `extract_recent_threads()`。
  - `judge_outreach()` 支持 `next_check_in_hours`。
  - 增强 `OUTREACH_JUDGE_PROMPT` 和 `OUTREACH_GENERATOR_PROMPT`。
- 修改：`tests/test_proactive_outreach.py`
  - 增加 C1/C2/C3 单测。
- 创建：`docs/superpowers/specs/2026-07-07-proactive-outreach-grounding-design.md`
- 创建：`.Codex/plans/proactive-outreach-grounding.md`

## 任务 1：C1 时间锚点

- [ ] **步骤 1：编写失败测试**

添加 `test_build_outreach_grounding_includes_precomputed_time_anchors`，构造固定 `now`、用户最近消息和上次主动外呼，断言 `now.period`、`weekday`、`hours_since_last_user_message`、`last_user_message`、`days_since_last_outreach`。

- [ ] **步骤 2：运行红灯测试**

```bash
python -m pytest tests/test_proactive_outreach.py::test_build_outreach_grounding_includes_precomputed_time_anchors -v
```

预期：`build_outreach_grounding()` 不接受 `now` 或缺少字段。

- [ ] **步骤 3：最小实现**

给 `build_outreach_grounding()` 增加 `now` 参数，查询最近用户消息和最近外呼记录，填充时间锚点字段。

- [ ] **步骤 4：运行绿灯测试**

运行同步骤 2，预期 PASS。

## 任务 2：C2 recent_threads 提炼

- [ ] **步骤 1：编写失败测试**

添加三个测试：

- `test_extract_recent_threads_uses_injected_llm_call`
- `test_extract_recent_threads_returns_empty_when_llm_fails`
- `test_extract_recent_threads_returns_empty_for_no_messages`

- [ ] **步骤 2：运行红灯测试**

```bash
python -m pytest tests/test_proactive_outreach.py -k "extract_recent_threads" -v
```

预期：函数不存在。

- [ ] **步骤 3：最小实现**

实现 `extract_recent_threads()`，支持注入 `llm_call`，默认使用 `call_model_route`，解析 JSON 数组并降级为空数组。

- [ ] **步骤 4：运行绿灯测试**

运行同步骤 2，预期 PASS。

## 任务 3：C3 Prompt 与相对 next_check

- [ ] **步骤 1：编写失败测试**

扩展 Judge 测试：mock 模型输出 `next_check_in_hours`，断言对外 `next_check_at` 为 `now + hours` 且受上下界钳制。扩展 Generator 测试，断言 prompt 提到 `recent_threads` 和避免重复上次主动消息。

- [ ] **步骤 2：运行红灯测试**

```bash
python -m pytest tests/test_proactive_outreach.py -k "judge_outreach or generate_outreach_message" -v
```

预期：当前 prompt 和解析逻辑不满足新断言。

- [ ] **步骤 3：最小实现**

更新两个 prompt。新增 `_parse_next_check_candidate()`，优先解析 `next_check_in_hours`，失败时兼容旧 `next_check_at`。

- [ ] **步骤 4：运行绿灯测试**

运行同步骤 2，预期 PASS。

## 任务 4：集成验证

- [ ] **步骤 1：定向测试**

```bash
python -m pytest tests/test_proactive_outreach.py -v
```

- [ ] **步骤 2：全量测试**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -m pytest tests/ -v
```

- [ ] **步骤 3：收尾检查**

```bash
git diff --name-only -- vendor
git -C vendor/KohakuTerrarium status --short
git status --short
```
