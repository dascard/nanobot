# Timer 与 Legacy Cooldown 软化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 或在当前会话中逐步骤执行。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `trigger_reason=""` 的 legacy cooldown 和 timer fired cooldown 从 hard wait 软化为 shared scoring shortcut；规则可确定时直接输出 scoring 结果，只有 scoring 不可用或非短路时保留兼容 wait。

**架构：** 复用 `GroupRuntime._score_timing()` 和 `_apply_scoring_shortcut()`。`process_message()` 的 cooldown 分支将 legacy 空 trigger 与 ambient 一样尝试 scoring shortcut；`handle_timer_fired()` 的 cooldown 分支在返回 hard wait 前先对当前 pending snapshot 执行 scoring。

**技术栈：** Python 3.13、pytest、现有 `core.timing_score` 和 `GroupRuntime` 状态机。

---

### 任务 1：legacy 空 trigger cooldown 走 scoring shortcut

**文件：**
- 修改：`tests/test_timing_runtime.py`
- 修改：`core/group_runtime/runtime.py`

- [ ] **步骤 1：编写失败的测试**

修改 `test_cooldown_blocks_ambient_after_bot_reply`，将旧 hard wait 期望改为 scoring shortcut：

```python
assert r["action"] == "no_reply"
assert r["reason"].startswith("cooldown scoring shortcut:")
assert r["timing_scoring"]["stage"] == "rule_shortcut"
assert r["timing_scoring"]["action"] == "no_reply"
assert len(rt_calls) == 0
```

- [ ] **步骤 2：运行测试验证失败**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_runtime.py::TestProcessMessageCooldown::test_cooldown_blocks_ambient_after_bot_reply -q -p no:cacheprovider
```

预期：FAIL，当前实现返回 `wait` 且 reason 包含「冷却」。

- [ ] **步骤 3：编写最少实现代码**

在 `process_message()` 的 cooldown 分支中，把 `if tr == "ambient":` 扩展为 `if tr in {"", "ambient"}:`，保持现有 ambient 行为不变。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令，预期 PASS。

---

### 任务 2：timer cooldown 走 scoring shortcut

**文件：**
- 修改：`tests/test_timing_runtime.py`
- 修改：`core/group_runtime/runtime.py`

- [ ] **步骤 1：编写失败的测试**

修改 `test_timer_respects_cooldown`，将旧 hard wait 期望改为 timer scoring shortcut：

```python
assert r["action"] == "no_reply"
assert r["reason"].startswith("timer cooldown scoring shortcut:")
assert r["timing_scoring"]["stage"] == "rule_shortcut"
assert r["timing_scoring"]["action"] == "no_reply"
assert len(rt_calls) == 0
```

- [ ] **步骤 2：运行测试验证失败**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_runtime.py::TestProcessMessageCooldown::test_timer_respects_cooldown -q -p no:cacheprovider
```

预期：FAIL，当前实现返回 `wait` 且 reason 包含「冷却」。

- [ ] **步骤 3：编写最少实现代码**

在 `handle_timer_fired()` 的 `_should_cooldown()` 分支中：

```python
snapshot = state.take_snapshot()
scoring_decision = self._score_timing(
    state,
    trigger_reason or state.last_trigger_reason or "timer",
    pending=snapshot,
)
if scoring_decision.stage == "rule_shortcut":
    return self._apply_scoring_shortcut(
        state,
        scoring_decision,
        pending=snapshot,
        reason_prefix="timer cooldown scoring shortcut",
    )
```

若 scoring 抛异常或不是 `rule_shortcut`，保留现有 hard wait fallback。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令，预期 PASS。

---

### 任务 3：验证与提交

**文件：**
- 修改：`core/group_runtime/runtime.py`
- 修改：`tests/test_timing_runtime.py`
- 创建：`.Codex/plans/timing-gate-scoring-phase9-cooldown.md`

- [ ] **步骤 1：运行 cooldown 定向测试**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_runtime.py::TestProcessMessageCooldown -q -p no:cacheprovider
```

- [ ] **步骤 2：运行 timing 回归**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_score.py tests/test_timing_runtime.py tests/test_timing_gate.py tests/test_timing_gate_prompt_policy.py tests/test_private_timing.py -q -p no:cacheprovider
```

- [ ] **步骤 3：运行全量测试**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

- [ ] **步骤 4：检查 diff**

```bash
git diff --check -- core/group_runtime/runtime.py tests/test_timing_runtime.py .Codex/plans/timing-gate-scoring-phase9-cooldown.md
```

- [ ] **步骤 5：按文件暂存并提交**

```bash
git add core/group_runtime/runtime.py tests/test_timing_runtime.py
blob=$(git hash-object -w .Codex/plans/timing-gate-scoring-phase9-cooldown.md)
git update-index --add --cacheinfo 100644,$blob,.Codex/plans/timing-gate-scoring-phase9-cooldown.md
git diff --cached --check -- core/group_runtime/runtime.py tests/test_timing_runtime.py .Codex/plans/timing-gate-scoring-phase9-cooldown.md
git commit -m "refactor(时机门控): 软化计时冷却路径"
```
