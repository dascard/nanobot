# TimingGate 评分体系阶段四 Rule Fallback 接管计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 当 TimingGate 模型失败、超时或解析失败时，`GroupRuntime` 使用评分层 `rule_fallback` 的最终动作，而不是盲从模型失败返回的 `no_reply`。

**架构：** 保持 `_call_gate()` 和 `TimingGate.judge()` 输出契约不变。`_apply_gate_result()` 继续先生成 `timing_scoring`，但当 `result.error_type` 存在且 `timing_scoring.stage == "rule_fallback"` 时，以 scoring 的 `action/delay_seconds/reason` 作为最终状态机动作；响应仍透出 `error_type/raw/fallback_action/timing_scoring` 供 admin 和日志审计。

**技术栈：** Python、pytest、现有 `GroupRuntime._shadow_scoring()` 和 `core.timing_score.decide_timing()`。

---

### 任务 1：模型失败规则兜底红灯

**文件：**
- 修改：`tests/test_timing_runtime.py`
- 测试：`tests/test_timing_runtime.py::TestGroupRuntime::test_model_failure_uses_rule_fallback_action_not_raw_no_reply`

- [x] **步骤 1：编写失败的测试**

在 `TestGroupRuntime` 中新增测试：构造处于 linger 余韵内的 `recent_bot_followup`，模型返回 `network_error/no_reply`，最终 response 应采用 scoring 的 `rule_fallback` 动作 `continue`。

```python
@pytest.mark.asyncio
async def test_model_failure_uses_rule_fallback_action_not_raw_no_reply(self, monkeypatch):
    runtime = GroupRuntime()
    state = runtime._states.setdefault("group_1", GateState(group_id="group_1"))
    state.activate_linger("u1", "at_bot")

    async def failed_gate(*_args, **_kwargs):
        return {
            "action": "no_reply",
            "error_type": "network_error",
            "reason": "timeout",
            "raw": "",
        }

    monkeypatch.setattr(runtime, "_call_gate", failed_gate)

    result = await runtime.process_message("group_1", {
        "sender_id": "u1",
        "sender_name": "A",
        "message": "继续看看呢",
        "message_id": "m1",
    }, trigger_reason="recent_bot_followup", talk_value=1.0)

    assert result["action"] == "continue"
    assert result["fallback_action"] == "continue"
    assert result["error_type"] == "network_error"
    assert result["source_message_ids"] == ["m1"]
    assert result["timing_scoring"]["stage"] == "rule_fallback"
    assert result["timing_scoring"]["action"] == "continue"
    assert result["timing_scoring"]["model_used"] is False
    assert result["reason"].startswith("rule_fallback after network_error:")
```

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_runtime.py::TestGroupRuntime::test_model_failure_uses_rule_fallback_action_not_raw_no_reply -q -p no:cacheprovider
```

预期：FAIL，失败原因为当前 `_apply_gate_result()` 仍使用 gate 失败结果里的 `action=no_reply`。

### 任务 2：实现 `_apply_gate_result()` 使用 rule_fallback

**文件：**
- 修改：`core/group_runtime/runtime.py`
- 测试：`tests/test_timing_runtime.py`

- [x] **步骤 1：计算有效动作**

在 `_apply_gate_result()` 生成 `scoring` 后，检测：

```python
result.get("error_type") and scoring.get("stage") == "rule_fallback"
```

如果 scoring action 是 `continue/wait/no_reply` 之一，则覆盖本次状态机使用的 `action`。`wait` 的 delay 优先使用 `scoring["delay_seconds"]`。

- [x] **步骤 2：保留观测字段**

响应仍保留：
- `error_type`
- `raw`
- `fallback_action`
- `timing_scoring`
- `pending_text/source_message_ids`（当 fallback action 是 `continue`）

最终 `reason` 使用 `rule_fallback after <error_type>: <scoring reason>`。

- [x] **步骤 3：运行新增测试验证通过**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_runtime.py::TestGroupRuntime::test_model_failure_uses_rule_fallback_action_not_raw_no_reply -q -p no:cacheprovider
```

预期：PASS。

### 任务 3：阶段四回归、审查与提交

**文件：**
- 修改：`.Codex/plans/timing-gate-scoring-phase4-fallback.md`
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
git diff --check -- .Codex/plans/timing-gate-scoring-phase4-fallback.md core/group_runtime/runtime.py tests/test_timing_runtime.py
```

预期：无输出，退出码 0。

- [x] **步骤 4：Commit**

只暂存本阶段文件：

```bash
git add .Codex/plans/timing-gate-scoring-phase4-fallback.md core/group_runtime/runtime.py tests/test_timing_runtime.py
git commit -m "fix(时机门控): 模型失败时使用规则兜底"
```
