# TimingGate 评分体系阶段六 Directed 信号软化计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `directed_to_other` 从 runtime hard rule 降级为 scoring 抑制信号：独自成立时仍确定性 `no_reply`，但结果来源是 `rule_shortcut`，不再写 `hard_rule=directed_to_other_no_bot_target`。

**架构：** 保留 `should_suppress_directed_to_other()` 作为“全部 pending 指向其他人且无 bot 指向”的快速检测，但命中后调用 `_score_timing()` 和 `_apply_scoring_shortcut()`。这样 directed-only 消息继续跳过模型、保留 `s_other=0.75` 可解释字段，并与 `directed_to_other + linger` 的冲突升级路径保持一致。

**技术栈：** Python、pytest、现有 `GroupRuntime._score_timing()` / `_apply_scoring_shortcut()`。

---

### 任务 1：Directed-only 不再 hard_rule 红灯

**文件：**
- 修改：`tests/test_timing_runtime.py`
- 测试：`tests/test_timing_runtime.py::TestProcessMessageDirected::test_process_message_directed_to_other_returns_no_reply`

- [x] **步骤 1：更新失败测试**

将现有 directed-only 测试改为断言：
- `action == "no_reply"`
- `hard_rule` 不在结果中
- `reason` 以 `directed_to_other scoring shortcut:` 开头
- `timing_scoring.stage == "rule_shortcut"`
- `timing_scoring.signals.sub_signals.s_other == 0.75`
- `source_message_ids` 仍包含原消息 ID

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_runtime.py::TestProcessMessageDirected::test_process_message_directed_to_other_returns_no_reply -q -p no:cacheprovider
```

预期：FAIL，失败原因是当前结果仍包含 `hard_rule=directed_to_other_no_bot_target`。

### 任务 2：Other bot thinking 场景继续跳过模型

**文件：**
- 修改：`tests/test_timing_runtime.py`
- 测试：`tests/test_timing_runtime.py::TestProcessMessageDirected::test_process_message_directed_to_other_with_other_bot_thinking_bypasses_gate`

- [x] **步骤 1：更新失败测试**

保留 `_call_gate` 抛错断言，证明 directed-only 仍不调模型。将 `hard_rule` 断言改为：

```python
assert "hard_rule" not in result
assert result["reason"].startswith("directed_to_other scoring shortcut:")
assert result["timing_scoring"]["signals"]["sub_signals"]["s_other"] == 0.75
```

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_runtime.py::TestProcessMessageDirected::test_process_message_directed_to_other_with_other_bot_thinking_bypasses_gate -q -p no:cacheprovider
```

预期：FAIL，失败原因同样是当前结果仍写 hard_rule。

### 任务 3：实现 directed_to_other scoring shortcut

**文件：**
- 修改：`core/group_runtime/runtime.py`
- 测试：`tests/test_timing_runtime.py`

- [x] **步骤 1：替换 hard rule 返回**

在 `should_suppress_directed_to_other(state.pending)` 命中后：
1. 取 `snapshot = state.take_snapshot()`。
2. 调用 `_score_timing(state, tr, pending=snapshot)`。
3. 若 `stage == "rule_shortcut"`，调用 `_apply_scoring_shortcut(..., reason_prefix="directed_to_other scoring shortcut")`。
4. 在响应上补充 `pending_count`、`trigger_reason`、`directed_to_other=True` 和 `_pending_payload(snapshot)`。

- [x] **步骤 2：保留异常兜底**

如果 scoring 计算异常，保守返回 `no_reply`，但 reason 用 `directed_to_other scoring unavailable`，不写 `hard_rule`。

- [x] **步骤 3：运行 directed runtime 测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_runtime.py::TestProcessMessageDirected -q -p no:cacheprovider
```

预期：全部 PASS。

### 任务 4：阶段六回归、审查与提交

**文件：**
- 修改：`.Codex/plans/timing-gate-scoring-phase6-directed.md`
- 修改：`core/group_runtime/runtime.py`
- 修改：`tests/test_timing_runtime.py`

- [x] **步骤 1：运行 timing 回归**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_score.py tests/test_timing_runtime.py tests/test_timing_gate.py tests/test_timing_gate_prompt_policy.py -q -p no:cacheprovider
```

预期：全部 PASS。

- [x] **步骤 2：运行全量测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

预期：0 failures。

- [x] **步骤 3：检查 diff**

运行：

```bash
git diff --check -- .Codex/plans/timing-gate-scoring-phase6-directed.md core/group_runtime/runtime.py tests/test_timing_runtime.py
```

预期：无输出，退出码 0。

- [x] **步骤 4：Commit**

只暂存本阶段文件：

```bash
git add .Codex/plans/timing-gate-scoring-phase6-directed.md core/group_runtime/runtime.py tests/test_timing_runtime.py
git commit -m "refactor(时机门控): 软化指向他人规则"
```
