# TimingGate 评分体系阶段五 Eval Scoring 计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 timing eval 从只 echo `input.action` 的静态合同，升级为可在 action 缺失时执行 `decide_timing()`，并可校验 `expected.scoring` 解释字段。

**架构：** `evals.runners.timing_gate_runner.run_timing_gate_case()` 保留旧 `input.action` 兼容路径；当 action 缺失时，从结构化 input 调用 `core.timing_score.decide_timing()`，把 `TimingDecision` 序列化到 `output.raw["scoring"]`。`evals.scorers.score_case()` 增加可选的 `expected.scoring` 递归字典校验，不影响未声明 scoring 的 suite。

**技术栈：** Python dataclass/asdict、pytest、现有 `EvalCase` / `EvalOutput` / `score_case`。

---

### 任务 1：Runner 执行 scoring 红灯

**文件：**
- 修改：`tests/test_timing_gate_prompt_policy.py`
- 测试：`tests/test_timing_gate_prompt_policy.py::test_timing_gate_eval_runner_uses_rule_scoring_when_action_missing`

- [x] **步骤 1：编写失败的测试**

新增测试：当 `input.action` 缺失但提供结构化 flags 时，runner 应调用 scoring 并生成 `timing_action` 与 `raw.scoring`。

```python
def test_timing_gate_eval_runner_uses_rule_scoring_when_action_missing():
    from evals.schema import EvalCase
    from evals.runners.timing_gate_runner import run_timing_gate_case

    case = EvalCase(
        id="timing_gate_runner_scoring",
        suite="timing_gate",
        input={
            "text": "@nanobot 这个报错怎么修",
            "is_group": True,
            "is_at_bot": True,
            "trigger_reason": "at_bot",
        },
    )

    output = run_timing_gate_case(case)

    assert output.timing_action == "continue"
    assert output.should_reply is True
    scoring = output.raw["scoring"]
    assert scoring["stage"] == "rule_shortcut"
    assert scoring["model_used"] is False
    assert scoring["signals"]["explicit_direct_score"] == 0.95
```

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_gate_prompt_policy.py::test_timing_gate_eval_runner_uses_rule_scoring_when_action_missing -q -p no:cacheprovider
```

预期：FAIL，失败原因是 runner 当前只读取 `input.action`。

### 任务 2：Scorer 校验 scoring 红灯

**文件：**
- 修改：`tests/test_timing_gate_prompt_policy.py`
- 测试：`tests/test_timing_gate_prompt_policy.py::test_timing_gate_scorer_rejects_scoring_stage_model_used_signal_mismatch`

- [x] **步骤 1：编写失败的测试**

新增测试：`expected.scoring` 指定 stage、model_used 和嵌套 signal 时，scorer 应发现 output.raw.scoring 中的 mismatch。

```python
def test_timing_gate_scorer_rejects_scoring_stage_model_used_signal_mismatch():
    from evals.schema import EvalCase, EvalOutput
    from evals.scorers import score_case

    case = EvalCase(
        id="timing_gate_scorer_scoring",
        suite="timing_gate",
        expected={
            "scoring": {
                "stage": "rule_shortcut",
                "model_used": False,
                "signals": {"sub_signals": {"s_transport_tier": "url"}},
            },
        },
    )
    output = EvalOutput(
        case_id=case.id,
        suite=case.suite,
        raw={
            "scoring": {
                "stage": "model_assisted",
                "model_used": True,
                "signals": {"sub_signals": {"s_transport_tier": "blob"}},
            }
        },
    )

    result = score_case(case, output)

    assert result["passed"] is False
    assert any("scoring.stage mismatch" in err for err in result["errors"])
    assert any("scoring.model_used mismatch" in err for err in result["errors"])
    assert any("scoring.signals.sub_signals.s_transport_tier mismatch" in err for err in result["errors"])
```

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_gate_prompt_policy.py::test_timing_gate_scorer_rejects_scoring_stage_model_used_signal_mismatch -q -p no:cacheprovider
```

预期：FAIL，失败原因是 scorer 当前忽略 `expected.scoring`。

### 任务 3：实现 runner / scorer scoring 支持

**文件：**
- 修改：`evals/runners/timing_gate_runner.py`
- 修改：`evals/scorers.py`
- 测试：`tests/test_timing_gate_prompt_policy.py`

- [x] **步骤 1：runner 结构化 scoring 路径**

当 `input.action` 为空时，读取以下字段并调用 `decide_timing()`：
- `text`
- `is_group`
- `is_private`
- `is_at_bot`
- `is_reply_to_bot`
- `bot_name_mentioned`
- `direct_call`
- `is_directed_to_other`
- `is_other_bot`
- `has_files`
- `linger_score`
- `force_direct_score`
- `min_interval_active`
- `min_interval_remaining`
- 可选 `model_hint`

输出：
- `out.timing_action = decision.action`
- `out.should_reply = decision.action == "continue"`
- `out.raw["scoring"] = asdict(decision)`

- [x] **步骤 2：scorer 递归校验 expected.scoring**

新增私有 helper 递归比较 dict：

```python
_compare_expected_dict(errors, "scoring", expected_scoring, actual_scoring)
```

只校验 expected 中声明的字段；缺失字段或值不等都报错，错误路径使用点号。

- [x] **步骤 3：运行两个新增测试验证通过**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_gate_prompt_policy.py::test_timing_gate_eval_runner_uses_rule_scoring_when_action_missing tests/test_timing_gate_prompt_policy.py::test_timing_gate_scorer_rejects_scoring_stage_model_used_signal_mismatch -q -p no:cacheprovider
```

预期：全部 PASS。

### 任务 4：阶段五回归、审查与提交

**文件：**
- 修改：`.Codex/plans/timing-gate-scoring-phase5-eval.md`
- 修改：`evals/runners/timing_gate_runner.py`
- 修改：`evals/scorers.py`
- 修改：`tests/test_timing_gate_prompt_policy.py`

- [x] **步骤 1：运行 timing eval 测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_gate_prompt_policy.py -q -p no:cacheprovider
```

预期：全部 PASS。

- [x] **步骤 2：运行 timing 回归**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_score.py tests/test_timing_runtime.py tests/test_timing_gate.py tests/test_timing_gate_prompt_policy.py -q -p no:cacheprovider
```

预期：全部 PASS。

- [x] **步骤 3：运行全量测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

预期：0 failures。

- [x] **步骤 4：检查 diff**

运行：

```bash
git diff --check -- .Codex/plans/timing-gate-scoring-phase5-eval.md evals/runners/timing_gate_runner.py evals/scorers.py tests/test_timing_gate_prompt_policy.py
```

预期：无输出，退出码 0。

- [x] **步骤 5：Commit**

只暂存本阶段文件：

```bash
git add .Codex/plans/timing-gate-scoring-phase5-eval.md evals/runners/timing_gate_runner.py evals/scorers.py tests/test_timing_gate_prompt_policy.py
git commit -m "test(时机门控): 让评测覆盖规则评分"
```
