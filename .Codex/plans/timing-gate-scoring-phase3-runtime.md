# TimingGate 评分体系阶段三 Runtime 接管计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将普通 ambient 消息的确定性 `rule_shortcut` 接入 `GroupRuntime` 主链路，在规则明确 no_reply / wait / continue 时跳过 TimingGate 模型调用。

**架构：** 继续复用阶段一的 `core.timing_score.decide_timing()` 和阶段二的 `GroupRuntime._score_timing()` / `_apply_scoring_shortcut()`。本阶段只在普通路径通过 talk_value、rate limit 等既有门控后、调用 `_call_gate()` 前增加一次评分短路；冲突或模糊带仍调用模型，direct trigger 既有分支不改。

**技术栈：** Python、pytest、现有 `GroupRuntime` 状态机和 `TimingDecision` dataclass。

---

### 任务 1：普通 ambient 规则短路红灯

**文件：**
- 修改：`tests/test_timing_runtime.py`
- 测试：`tests/test_timing_runtime.py::TestGroupRuntime::test_ambient_ack_rule_shortcut_skips_gate`

- [x] **步骤 1：编写失败的测试**

在 `TestGroupRuntime` 中新增测试：一条 `trigger_reason="ambient"`、`talk_value=1.0` 的纯确认消息应由 scoring 直接 `no_reply`，且不调用 `_call_gate()`。

```python
@pytest.mark.asyncio
async def test_ambient_ack_rule_shortcut_skips_gate(self, monkeypatch):
    runtime = GroupRuntime()

    async def fail_gate(*_args, **_kwargs):
        raise AssertionError("deterministic ambient scoring should skip TimingGate")

    monkeypatch.setattr(runtime, "_call_gate", fail_gate)

    result = await runtime.process_message("g1", {
        "sender_id": "u1",
        "sender_name": "A",
        "message": "嗯",
        "message_id": "m1",
    }, trigger_reason="ambient", talk_value=1.0)

    assert result["action"] == "no_reply"
    assert result["reason"].startswith("ambient scoring shortcut:")
    scoring = result["timing_scoring"]
    assert scoring["stage"] == "rule_shortcut"
    assert scoring["model_used"] is False
    assert scoring["signals"]["sub_signals"]["s_ack"] == 0.85
```

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_runtime.py::TestGroupRuntime::test_ambient_ack_rule_shortcut_skips_gate -q -p no:cacheprovider
```

预期：FAIL，失败原因是当前普通 ambient 通过旧路径调用 `_call_gate()`。

### 任务 2：实现 Runtime 普通路径评分短路

**文件：**
- 修改：`core/group_runtime/runtime.py`
- 测试：`tests/test_timing_runtime.py`

- [x] **步骤 1：在 `_call_gate()` 前接入 scoring shortcut**

在 `process_message()` 的非 force 分支中，完成 talk_value gate、rate limit 之后，调用 `_score_timing(state, tr, pending=snapshot)`。

仅当以下条件同时满足时应用 `_apply_scoring_shortcut()`：

```python
tr == "ambient"
scoring_decision.stage == "rule_shortcut"
```

其他 stage 继续调用 `_call_gate()`，保持模糊带 / 冲突由模型裁量。

- [x] **步骤 2：保留既有兼容行为**

不要改变：
- `trigger_reason=""` 的历史测试路径；
- direct trigger 的 force scoring 分支；
- directed_to_other hard rule 的返回字段；
- cooldown wait 和 timer path；
- `_call_gate()` / `TimingGate.judge()` 输出契约。

- [x] **步骤 3：运行新增测试验证通过**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_runtime.py::TestGroupRuntime::test_ambient_ack_rule_shortcut_skips_gate -q -p no:cacheprovider
```

预期：PASS。

### 任务 3：阶段三回归、审查与提交

**文件：**
- 修改：`.Codex/plans/timing-gate-scoring-phase3-runtime.md`
- 修改：`core/group_runtime/runtime.py`
- 修改：`tests/test_timing_runtime.py`

- [x] **步骤 1：运行 timing runtime 回归**

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
git diff --check -- .Codex/plans/timing-gate-scoring-phase3-runtime.md core/group_runtime/runtime.py tests/test_timing_runtime.py
```

预期：无输出，退出码 0。

- [x] **步骤 4：Commit**

只暂存本阶段文件：

```bash
git add .Codex/plans/timing-gate-scoring-phase3-runtime.md core/group_runtime/runtime.py tests/test_timing_runtime.py
git commit -m "feat(时机门控): 接管普通规则短路"
```
