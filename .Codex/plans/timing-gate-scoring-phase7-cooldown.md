# TimingGate 评分体系阶段七 Ambient Cooldown 软化计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `trigger_reason="ambient"` 的 cooldown 从 hard wait 降级为 scoring min_interval 信号：纯 ambient / 纯确认在 bot 刚回复后应确定性 `no_reply`，不浪费模型调用，也不进入无意义 wait。

**架构：** 保留 `_should_cooldown()` 作为 min_interval 检测。`process_message()` 命中 cooldown 且 `tr == "ambient"` 时先调用 `_score_timing()`；如果得到 `rule_shortcut`，用 `_apply_scoring_shortcut()` 输出最终动作。非 ambient legacy 入口和 timer path 先保持原 hard wait，后续单独阶段再处理。

**技术栈：** Python、pytest、现有 `GroupRuntime._score_timing()` / `_apply_scoring_shortcut()`。

---

### 任务 1：Ambient cooldown scoring 红灯

**文件：**
- 修改：`tests/test_timing_runtime.py`
- 测试：`tests/test_timing_runtime.py::TestGroupRuntime::test_ambient_trigger_does_not_bypass_cooldown`

- [x] **步骤 1：更新失败测试**

将现有 `trigger_reason="ambient"` cooldown 测试改为断言：
- `_call_gate` 不被调用；
- `action == "no_reply"`；
- `reason` 以 `cooldown scoring shortcut:` 开头；
- `timing_scoring.stage == "rule_shortcut"`；
- `timing_scoring.action == "no_reply"`。

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_runtime.py::TestGroupRuntime::test_ambient_trigger_does_not_bypass_cooldown -q -p no:cacheprovider
```

预期：FAIL，失败原因是当前 ambient cooldown 仍 hard wait。

### 任务 2：实现 ambient cooldown scoring shortcut

**文件：**
- 修改：`core/group_runtime/runtime.py`
- 测试：`tests/test_timing_runtime.py`

- [x] **步骤 1：在 cooldown 分支中加入 ambient scoring**

在 `process_message()` 的 `_should_cooldown(state, tr)` 分支中，如果 `tr == "ambient"`：
1. 取 `snapshot = state.take_snapshot()`。
2. 调用 `_score_timing(state, tr, pending=snapshot)`。
3. 若 `stage == "rule_shortcut"`，调用 `_apply_scoring_shortcut(..., reason_prefix="cooldown scoring shortcut")`。

- [x] **步骤 2：保留兼容兜底**

如果 scoring 失败或不是 `rule_shortcut`，继续沿用旧 hard wait；`trigger_reason=""` 和 timer path 不在本阶段改。

- [x] **步骤 3：运行 cooldown 相关测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_runtime.py::TestGroupRuntime::test_cooldown_blocks_ambient_after_bot_reply tests/test_timing_runtime.py::TestGroupRuntime::test_ambient_trigger_does_not_bypass_cooldown tests/test_timing_runtime.py::TestGroupRuntime::test_timer_respects_cooldown -q -p no:cacheprovider
```

预期：全部 PASS。

### 任务 3：阶段七回归、审查与提交

**文件：**
- 修改：`.Codex/plans/timing-gate-scoring-phase7-cooldown.md`
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
git diff --check -- .Codex/plans/timing-gate-scoring-phase7-cooldown.md core/group_runtime/runtime.py tests/test_timing_runtime.py
```

预期：无输出，退出码 0。

- [x] **步骤 4：Commit**

只暂存本阶段文件：

```bash
git add .Codex/plans/timing-gate-scoring-phase7-cooldown.md core/group_runtime/runtime.py tests/test_timing_runtime.py
git commit -m "refactor(时机门控): 软化群聊环境冷却"
```
