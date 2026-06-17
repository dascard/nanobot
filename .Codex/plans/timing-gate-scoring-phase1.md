# TimingGate 评分体系阶段一实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 按 `docs/superpowers/specs/2026-06-16-timing-gate-scoring-design.md` 先落地可单测的 TimingGate scoring 纯函数，不替换现有 runtime 主链路。

**架构：** 新增 `core/timing_score.py`，提供信号提取、规则参与度、门控短路、模型 blend、就绪度判定和解释字段。现有 `TimingGate.judge` 与 `GroupRuntime` 暂不切换到新 action，只通过单元测试和 eval 证明规则层行为。

**技术栈：** Python dataclass、pytest、现有 `GroupPendingMessage` 风格字段兼容。

---

### 任务 1：纯函数测试红灯

**文件：**
- 创建：`tests/test_timing_score.py`
- 创建：`core/timing_score.py`

- [x] **步骤 1：编写失败的测试**

在 `tests/test_timing_score.py` 覆盖这些行为：

```python
from core.timing_score import decide_timing, extract_signals, TimingModelHint


def test_at_bot_request_rule_shortcuts_continue_without_model():
    decision = decide_timing(
        text="@bot 帮我查一下 X",
        is_group=True,
        is_at_bot=True,
        model_hint=None,
    )
    assert decision.action == "continue"
    assert decision.stage == "rule_shortcut"
    assert decision.model_used is False


def test_ambient_ack_rule_shortcuts_no_reply_without_model():
    decision = decide_timing(text="嗯", is_group=True, model_hint=None)
    assert decision.action == "no_reply"
    assert decision.model_used is False


def test_at_bot_with_image_waits_without_transport_suppression():
    decision = decide_timing(
        text="@bot",
        is_group=True,
        is_at_bot=True,
        has_files=True,
        model_hint=None,
    )
    assert decision.action == "wait"
    assert decision.delay_seconds == 5
    assert decision.signals.sub_signals["s_transport"] == 0.0
    assert decision.signals.wait_signal >= 0.4


def test_directed_to_other_with_linger_escalates_to_model():
    decision = decide_timing(
        text="张三你看看这个",
        is_group=True,
        is_directed_to_other=True,
        linger_score=0.55,
        model_hint=TimingModelHint(action="continue", confidence=0.8, raw="{}", reason="仍在上一轮对话中"),
    )
    assert decision.stage == "model_assisted_conflict"
    assert decision.model_used is True
    assert decision.action == "continue"


def test_non_pure_ack_is_not_suppressed_as_ack():
    signals = extract_signals(text="好的，帮我查下 X", is_group=True)
    assert signals.sub_signals["s_ack"] == 0.0
```

- [x] **步骤 2：运行测试验证失败**

运行：`python -B -m pytest tests/test_timing_score.py -q`

预期：FAIL，失败原因是 `core.timing_score` 或函数不存在。

### 任务 2：实现 scoring 纯函数

**文件：**
- 修改：`core/timing_score.py`
- 测试：`tests/test_timing_score.py`

- [x] **步骤 1：新增数据结构**

实现：

```python
@dataclass(frozen=True)
class TimingSignals:
    explicit_direct_score: float
    linger_score: float
    direct_score: float
    wait_signal: float
    suppress_score: float
    sub_signals: dict[str, Any]


@dataclass(frozen=True)
class TimingModelHint:
    action: str
    confidence: float
    raw: str = ""
    reason: str = ""


@dataclass(frozen=True)
class TimingDecision:
    action: str
    stage: str
    participation_score: float
    final_score: float
    theta: float
    low_threshold: float
    high_threshold: float
    delay_seconds: int | None
    model_used: bool
    model_action: str
    model_confidence: float
    model_weight: float
    signals: TimingSignals
    reason: str
```

- [x] **步骤 2：实现信号提取**

实现 `extract_signals(...)`：

```python
signals = extract_signals(
    text,
    is_group=True,
    is_private=False,
    is_at_bot=False,
    is_reply_to_bot=False,
    bot_name_mentioned=False,
    direct_call=False,
    is_directed_to_other=False,
    is_other_bot=False,
    has_files=False,
    linger_score=0.0,
)
```

规则按设计文档：
- `d0`: reply_to_bot=1.0、at_bot=0.95、bot_name=0.75、direct_call=0.65、private=0.75、ambient=0
- `d = max(d0, linger_score)`
- `s_ack` 只匹配纯确认；含请求词、问号、URL、代码、文件时为 0
- `s_transport`: secret/token/blob=0.95、纯 URL=0.75、纯代码块=0.65、长文本 dump 无问句=0.55；图片/文件不进入 suppress
- `s_other=0.75`
- `s_bot=0.70`
- `w_marker=1.0`、`w_file=0.45`、`w_incomplete=0.35`

- [x] **步骤 3：实现规则与模型决策**

实现：
- `compute_rule_score(signals) -> float`
- `select_theta(signals, is_private=False) -> float`
- `compute_model_prior(action) -> float`
- `compute_model_weight(confidence) -> float`
- `decide_timing(...) -> TimingDecision`

规则：
- `E_rule = clip(0.10 + 0.85*d - 0.85*s)`
- `theta`: private=0.40、d0>=0.9 为 0.30、d0>=0.65 为 0.42、否则 0.62
- `margin=0.18`
- `kappa=min(d,s) >= 0.35` 时进入 `model_assisted_conflict`
- 模型正常时 `E_final=(1-lambda)*E_rule + lambda*M_e(action)`
- 模型缺失或失败时 `rule_fallback`
- 参与后按 Stage 4 输出 `wait` 或 `continue/no_reply`

- [x] **步骤 4：运行测试验证通过**

运行：`python -B -m pytest tests/test_timing_score.py -q`

预期：PASS。

### 任务 3：回归与验收

**文件：**
- 测试：`tests/test_timing_score.py`
- 相关回归：`tests/test_timing_gate.py`、`tests/test_timing_runtime.py`、`tests/test_timing_gate_prompt_policy.py`

- [x] **步骤 1：运行 timing 相关回归**

运行：

```bash
python -B -m pytest tests/test_timing_score.py tests/test_timing_gate.py tests/test_timing_runtime.py tests/test_timing_gate_prompt_policy.py -q
```

预期：全部 PASS。

- [x] **步骤 2：运行 eval suite**

运行：

```bash
python -m evals.run --suite timing_gate
```

预期：命令退出码 0，报告中无失败用例。

- [x] **步骤 3：整理阶段一结论**

结论必须说明：
- 新 scoring 已有纯函数和测试覆盖。
- 现有 runtime 尚未替换，新旧 shadow/主链路切换留到下一阶段。
- 下一阶段要接入 `GroupRuntime._call_gate()` 和 timing meta 日志。
