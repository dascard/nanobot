# GroupRuntime 状态与评分拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍未完成。当前实测
`core/group_runtime/runtime.py` 为 1385 行，已明显超过文档中记录的 829 行旧状态。
该文件同时承载单群状态模型、pending payload 构造、TimingGate scoring 桥接、主状态机、
timer 状态机、模型调用和 Admin 快照，维护成本已经偏高。

本次目标不是改 TimingGate 行为，而是把稳定、可测试、低耦合的状态模型与评分辅助逻辑
拆出，让 `runtime.py` 回到 800 行以下，同时保持旧导入路径和主流程行为不变。

## 当前职责分区

- `runtime.py:36-410`：常量、`GroupPendingMessage`、pending payload helper、
  `GroupChatState` 和 wait / linger / cooldown 状态方法。
- `runtime.py:429-728`：`GroupRuntime` 的 scoring 私有方法，包括 `_score_timing()`、
  `_apply_scoring_shortcut()`、policy 包装、cooldown scoring 和 recent follow-up 判断。
- `runtime.py:729-1164`：`process_message()` 与 `handle_timer_fired()` 主状态机。
- `runtime.py:1166-1373`：模型结果应用、Admin 快照、TimingGate 调用和
  `_build_timing_context()`。
- `runtime.py:1376-1385`：全局单例。

只拆状态模型约减少 375 行，`runtime.py` 仍会超过 1000 行，无法完成当前待办项。
因此本次拆分需要同时迁出状态模型和 scoring mixin，但保留主状态机在原文件。

## 方案选择

推荐方案：拆出 `constants.py`、`state.py`、`scoring.py`，`runtime.py` 保留 facade 和主流程。

- `core/group_runtime/constants.py`
  - 持有 `MIN_INTERVAL`、`MAX_PENDING`、`MAX_WAIT_SEC`、`MAX_RETRIES`、`MAX_AGE_SEC`、
    `IDLE_CLEANUP_SEC`、`BOT_REPLY_COOLDOWN_SEC`、`TIMING_WAIT_DELAY_MIN`、
    `TIMING_WAIT_DELAY_MAX`、`LINGER_*`、`BOT_FOLLOWUP_WINDOW_SEC`、
    `_DIRECT_TRIGGERS`、`_COOLDOWN_BYPASS_TRIGGERS`、
    `_DIRECTED_SUPPRESS_BYPASS_TRIGGERS`。
- `core/group_runtime/state.py`
  - 持有 `_clip_timing_wait_delay()`、`_model_confidence_from_gate_result()`、
    `GroupPendingMessage`、`GroupChatState`、`_pending_payload()`、
    `_pending_text_for_scoring()`、`_has_file_segments()`、
    `_has_directed_to_other_signal()`、`_has_other_recipient_signal()`、
    `should_suppress_directed_to_other()`。
- `core/group_runtime/scoring.py`
  - 提供 `GroupRuntimeScoringMixin`。
  - 迁移 `_active_linger_score()`、`_should_cooldown()`、`_activity_factor()`、
    `_talk_threshold()`、`_should_gate_by_frequency()`、`_shadow_scoring()`、
    `_timing_scoring_payload()`、`_score_timing()`、`_apply_scoring_shortcut()`、
    `_policy_payload()`、`_resolve_timing_model_policy()`、
    `_apply_policy_scoring_decision()`、`_cooldown_scoring_shortcut()`、
    `_attach_shadow_scoring()`、`_looks_like_recent_bot_followup()`。
- `core/group_runtime/runtime.py`
  - 改为 `class GroupRuntime(GroupRuntimeScoringMixin)`。
  - 继续保留 `process_message()`、`handle_timer_fired()`、`_apply_gate_result()`、
    `note_bot_replied()`、`cleanup_idle()`、`snapshot_states()`、`_call_gate()`、
    `_build_timing_context()` 和 `get_group_runtime()`。
  - 从新模块导入并重导出旧符号。

放弃方案：

- 只拆 `state.py`：风险最低，但不能让 `runtime.py` 低于 800 行，无法完成当前待办项。
- 抽 `_call_gate()` / `_build_timing_context()`：减少行数太少，且 `_build_timing_context()`
  有 deprecated wrapper 和直接测试依赖。
- 抽 `process_message()` / `handle_timer_fired()`：行数收益最大，但这两个函数承载锁、
  generation、snapshot、policy 和模型返回落盘的核心编排，第一刀风险过高。

## 兼容边界

必须保持以下旧路径可用：

- `core.timing_runtime.PendingMessage is core.group_runtime.runtime.GroupPendingMessage`。
- `core.timing_runtime.GateState is core.group_runtime.runtime.GroupChatState`。
- `core.timing_runtime.GroupRuntime`、`get_group_runtime()`、`_pending_payload()`、
  `MIN_INTERVAL`、`MAX_PENDING`、`MAX_WAIT_SEC`、`MAX_RETRIES`、`MAX_AGE_SEC`、
  `IDLE_CLEANUP_SEC`、`BOT_REPLY_COOLDOWN_SEC`、`_DIRECT_TRIGGERS` 继续可导入。
- `core.group_runtime.runtime.GroupPendingMessage`、
  `core.group_runtime.runtime.should_suppress_directed_to_other` 继续可直接导入。
- `GroupRuntime._build_timing_context()` 继续作为 class static method 可调用。
- `GroupRuntime` 实例继续暴露 `_score_timing()`、`_cooldown_scoring_shortcut()` 等私有方法，
  以兼容现有测试和 monkeypatch 风格。

本次不新增 `core/group_runtime/__init__.py` 的批量 re-export，避免扩展新的 public path。

## 测试策略

先补红灯测试，再迁移代码：

- 新增 `tests/test_group_runtime_split_compat.py`。
- 锁定 `core.timing_runtime` 和 `core.group_runtime.runtime` 的旧导入兼容。
- 直接覆盖 `_pending_payload()` 的方向格式、引用格式和 `source_message_ids` 顺序。
- 覆盖 `GroupPendingMessage.__post_init__()` 的 at-bot 优先级。
- 覆盖 `_clip_timing_wait_delay()` 和 `_model_confidence_from_gate_result()` 边界。
- 通过 monkeypatch 捕获 `_score_timing()` 传给 `decide_timing()` 的关键入参：
  `is_other_bot`、`has_other_recipient`、`has_files`、direct flags 和 `force_direct_score`。

迁移后验证顺序：

1. 新增兼容测试。
2. `tests/test_timing_runtime.py` 与 `tests/test_timing_score.py`。
3. API / group response 相邻回归。
4. `python -m compileall core/group_runtime -q` 与 `git diff --check`。
5. 全量 `python -m pytest tests/ -v`。

## 风险与约束

- 不改变 `GroupRuntime.process_message()` 和 `GroupRuntime.handle_timer_fired()` 的签名或主流程。
- 不改变 `TimingGate` prompt context 文本语义。
- 不改变 wait anti-loop、cooldown、linger、rules_only / shadow policy 的响应字段。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。
- 不改 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 prompt runtime 输入。
- 迁移后必须用 `wc -l` 复核 `runtime.py` 行数，并同步 `docs/todo.md` 与
  `docs/plan_walkthrough.md`。
