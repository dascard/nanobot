# TimingGate 模型层开关实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 或在当前会话中逐步骤执行。步骤使用复选框（`- [x]`）语法来跟踪进度。

**目标：** 增加 session / platform 级 TimingGate 模型层开关，支持 `enabled`、`rules_only`、`shadow` 三种模式；默认向后兼容。

**架构：** 新增 `core/timing_model_policy.py` 作为纯解析层，从 `SettingsService` 读取默认、platform map、session map。`GroupRuntime` 在准备调用 TimingGate 模型前解析 policy：`enabled` 保持原行为；`rules_only` 跳过模型并使用 shared scoring 规则兜底；`shadow` 仍调用模型但最终采用规则侧决策，同时把模型 shadow 结果写入响应调试字段。

**技术栈：** Python 3.13、pytest、现有 `core.settings_service`、`core.timing_score`、`GroupRuntime`。

---

### 任务 1：新增 policy resolver 纯函数

**文件：**
- 创建：`core/timing_model_policy.py`
- 修改：`core/config_registry.py`
- 测试：`tests/test_timing_model_policy.py`

- [x] **步骤 1：编写失败的测试**

新增测试覆盖：

```python
def test_timing_model_policy_defaults_to_enabled():
    assert resolve_timing_model_policy(session_id="group_1", platform="qq").mode == "enabled"

def test_timing_model_policy_session_overrides_platform(monkeypatch):
    monkeypatch.setattr("core.timing_model_policy.settings.get_str", fake_get_str)
    policy = resolve_timing_model_policy(session_id="group_1", platform="web")
    assert policy.mode == "rules_only"
    assert policy.source == "session:group_1"
```

- [x] **步骤 2：运行测试验证失败**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_model_policy.py -q -p no:cacheprovider
```

预期：FAIL，模块不存在。

- [x] **步骤 3：编写最少实现代码**

新增 dataclass：

```python
@dataclass(frozen=True)
class TimingModelPolicy:
    mode: str
    source: str
```

支持配置键：

- `timing_gate.model_policy.default`，默认 `enabled`
- `timing_gate.model_policy.platforms`，默认 `{}`
- `timing_gate.model_policy.sessions`，默认 `{}`

解析优先级：session > platform > default。合法 mode：`enabled`、`rules_only`、`shadow`，并兼容 `disabled/rule_only/rules/no_model` 映射为 `rules_only`。

- [x] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令，预期 PASS。

---

### 任务 2：`rules_only` 跳过 TimingGate 模型

**文件：**
- 修改：`core/group_runtime/runtime.py`
- 测试：`tests/test_timing_runtime.py`
- 修改：`app/group_ingress/service.py`
- 测试：`tests/test_api.py`

- [x] **步骤 1：编写失败的测试**

新增测试：

```python
@pytest.mark.asyncio
async def test_timing_model_policy_rules_only_skips_gate_for_fuzzy_linger(monkeypatch):
    runtime = GroupRuntime()
    runtime.timing_model_policy_resolver = lambda session_id, platform: TimingModelPolicy("rules_only", f"session:{session_id}")

    async def fail_gate(*_args, **_kwargs):
        raise AssertionError("rules_only 不应调用 TimingGate 模型")

    monkeypatch.setattr(runtime, "_call_gate", fail_gate)
    state = runtime._states.setdefault("group_1", GateState(group_id="group_1"))
    state.activate_linger("u1", "at_bot")
    state.note_bot_replied()

    result = await runtime.process_message(
        "group_1",
        {"sender_id": "u1", "sender_name": "A", "message": "继续看看", "message_id": "m1"},
        trigger_reason="recent_bot_followup",
        talk_value=1.0,
    )

    assert result["timing_model_policy"]["mode"] == "rules_only"
    assert result["timing_scoring"]["stage"] == "rule_fallback"
    assert result["timing_scoring"]["model_used"] is False
```

- [x] **步骤 2：运行测试验证失败**

预期：FAIL，当前没有 policy resolver，且会调用 `_call_gate()`。

- [x] **步骤 3：编写最少实现代码**

在 `GroupRuntime.__init__()` 设置：

```python
self.timing_model_policy_resolver = resolve_timing_model_policy
```

在准备调用 `_call_gate()` 前，如果 policy 为 `rules_only`：

```python
decision = self._score_timing(...)
return self._apply_policy_scoring_decision(..., reason_prefix="timing model rules_only")
```

响应附带：

```python
"timing_model_policy": {"mode": "...", "source": "..."}
```

- [x] **步骤 4：运行测试验证通过**

运行新增单测，预期 PASS。

---

### 任务 3：`shadow` 调模型但最终采用规则侧决策

**文件：**
- 修改：`core/group_runtime/runtime.py`
- 测试：`tests/test_timing_runtime.py`

- [x] **步骤 1：编写失败的测试**

新增测试：

```python
@pytest.mark.asyncio
async def test_timing_model_policy_shadow_calls_gate_but_uses_rule_decision(monkeypatch):
    runtime = GroupRuntime()
    runtime.timing_model_policy_resolver = lambda session_id, platform: TimingModelPolicy("shadow", f"platform:{platform}")
    calls = []

    async def fake_gate(*_args, **_kwargs):
        calls.append(1)
        return {"action": "no_reply", "reason": "model shadow says no"}

    monkeypatch.setattr(runtime, "_call_gate", fake_gate)
    state = runtime._states.setdefault("group_1", GateState(group_id="group_1"))
    state.activate_linger("u1", "at_bot")
    state.note_bot_replied()

    result = await runtime.process_message(
        "group_1",
        {"sender_id": "u1", "sender_name": "A", "message": "继续看看", "message_id": "m1"},
        trigger_reason="recent_bot_followup",
        talk_value=1.0,
        platform="web",
    )

    assert calls == [1]
    assert result["timing_model_policy"]["mode"] == "shadow"
    assert result["timing_scoring"]["model_used"] is False
    assert result["timing_model_shadow_scoring"]["model_action"] == "no_reply"
```

- [x] **步骤 2：运行测试验证失败**

预期：FAIL，当前模型结果会直接影响最终 action，且没有 shadow 字段。

- [x] **步骤 3：编写最少实现代码**

给 `process_message()` 增加可选 `platform: str = "qq"` 参数并写入 state。`enabled` 继续 `_apply_gate_result()`；`shadow` 在 `_call_gate()` 后计算 `model_shadow_scoring`，最终调用规则侧 decision 应用状态。

同阶段补齐 `handle_timer_fired()`：timer 到期前同样解析 policy，`rules_only` 不调用 `_call_gate()`，`shadow` 只记录模型 shadow scoring。`/group/message` 从 `client_meta.platform` 读取平台，默认 `qq`，并传入 `runtime.process_message(platform=...)`。

- [x] **步骤 4：运行测试验证通过**

运行新增单测，预期 PASS。

---

### 任务 4：验证与提交

**文件：**
- 创建：`core/timing_model_policy.py`
- 修改：`core/config_registry.py`
- 修改：`core/group_runtime/runtime.py`
- 修改：`app/group_ingress/service.py`
- 修改：`tests/test_timing_model_policy.py`
- 修改：`tests/test_timing_runtime.py`
- 修改：`tests/test_api.py`
- 创建：`.Codex/plans/timing-gate-scoring-phase10-model-policy.md`

- [x] **步骤 1：运行定向测试**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_model_policy.py tests/test_timing_runtime.py::TestGroupRuntime -q -p no:cacheprovider
```

- [x] **步骤 2：运行 timing 回归**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_score.py tests/test_timing_runtime.py tests/test_timing_gate.py tests/test_timing_gate_prompt_policy.py tests/test_private_timing.py tests/test_timing_model_policy.py -q -p no:cacheprovider
```

- [x] **步骤 3：运行全量测试**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

- [x] **步骤 4：检查 diff 并提交**

```bash
git diff --check -- core/timing_model_policy.py core/config_registry.py core/group_runtime/runtime.py tests/test_timing_model_policy.py tests/test_timing_runtime.py .Codex/plans/timing-gate-scoring-phase10-model-policy.md
git add core/timing_model_policy.py core/config_registry.py core/group_runtime/runtime.py tests/test_timing_model_policy.py tests/test_timing_runtime.py
blob=$(git hash-object -w .Codex/plans/timing-gate-scoring-phase10-model-policy.md)
git update-index --add --cacheinfo 100644,$blob,.Codex/plans/timing-gate-scoring-phase10-model-policy.md
git diff --cached --check -- core/timing_model_policy.py core/config_registry.py core/group_runtime/runtime.py tests/test_timing_model_policy.py tests/test_timing_runtime.py .Codex/plans/timing-gate-scoring-phase10-model-policy.md
git commit -m "feat(时机门控): 添加模型层策略开关"
```
