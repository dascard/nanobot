# TimingGate scoring 可观测性收尾设计

> 2026-06-20 · 补齐 Admin WebUI 中 TimingGate scoring 详情字段，让真实样本复盘和阈值调参可以直接看到完整子信号。

## 背景

TimingGate 混合决策已经把核心评分字段写入 `timing_gate.scoring`。后端 `GroupRuntime` 使用 `asdict(decision)` 持久化 scoring，Admin events API 也会原样返回 `timing.scoring`，因此接口侧已经包含 `conflict_score`、`soft_reject_cap`、`delay_seconds` 和 `signals.sub_signals` 中的 `s_transport_tier`、`w_marker`、`w_file`、`w_incomplete`。

当前缺口在 WebUI 详情页：已有 `E_rule`、`E_final`、`theta`、`band`、`d0`、`linger`、`d`、`w`、`s`、`s_ack`、`s_transport`、`s_other`、`s_bot`、模型参与和模型权重；但设计文档要求的冲突分、软拒绝上限、transport tier、wait 子信号和 scoring delay 没有直接展示。用户需要打开 raw JSON 才能复盘这些字段，不利于真实样本标注和调参。

## 目标

- 在 `TimingEventDetail` 的规则评分卡片中展示 `conflict_score`、`soft_reject_cap` 和 `scoring.delay_seconds`。
- 在信号分解中展示 `s_transport_tier`、`w_marker`、`w_file`、`w_incomplete`。
- 保持所有字段只读展示，不改变 TimingGate 决策公式、Admin API、ChatLog schema 或真实样本抽样逻辑。
- 增强 `tests/test_webui_observability.py` 静态守卫，防止后续前端重构再次丢字段。

## 非目标

- 不新增后端字段。当前 API 已返回完整 `scoring`。
- 不调整 `core/timing_score.py` 的分数、阈值或 stage 语义。
- 不改 `core/eval_sampling/timing_signal_audit.py` 的采样信号集合。
- 不启动真实样本运营动作；该阶段在本收尾之后单独设计。

## 方案对比

### 方案 A：只补 WebUI 展示和静态测试

优点是范围最小，不改数据契约；实现只涉及 `webui/src/App.jsx` 和 `tests/test_webui_observability.py`。缺点是不能验证真实 API 返回样例中的字段形态，但现有 Admin events 已经透传 `scoring`，后端合同风险低。

### 方案 B：同时补 Admin API fixture 测试

优点是接口层证据更完整；缺点是本阶段会扩大到测试数据构造和 API fixture，和实际缺口不完全匹配。由于 `_timing_event_dict()` 已经原样返回 `timing.get("scoring") or {}`，新增接口测试收益有限。

### 方案 C：拆出独立 React 子组件

优点是长期可维护性更好；缺点是 `App.jsx` 当前仍保留该页面实现，拆分会引入无关重构。此阶段只补字段，不重构页面结构。

推荐方案 A：它直接解决可观测性缺口，变更面最小，适合阶段性提交。

## 设计细节

### WebUI

在 `TimingEventDetail` 中继续使用现有 `scoreValue()` 格式化数字。规则评分网格从 4 个字段扩展为 7 个字段：

- `E_rule` → `scoring.participation_score`
- `E_final` → `scoring.final_score`
- `theta` → `scoring.theta`
- `band` → `low_threshold / high_threshold`
- `conflict` → `scoring.conflict_score`
- `soft_cap` → `scoring.soft_reject_cap`
- `delay` → `scoring.delay_seconds`

信号分解继续读取 `signals.sub_signals`，补充：

- `s_transport_tier` → `subSignals.s_transport_tier || '-'`
- `w_marker` → `subSignals.w_marker`
- `w_file` → `subSignals.w_file`
- `w_incomplete` → `subSignals.w_incomplete`

### 测试

`tests/test_webui_observability.py::test_timing_gate_detail_exposes_scoring_breakdown` 增加上述字段的源码断言。按 TDD 流程先让测试断言失败，再补 WebUI，最后运行 WebUI 静态守卫和相邻 TimingGate 测试。

## 验收

- 红灯：新增静态断言后，`tests/test_webui_observability.py::test_timing_gate_detail_exposes_scoring_breakdown` 因缺少至少一个字段失败。
- 绿灯：补 WebUI 展示后，同一测试通过。
- 回归：`tests/test_webui_observability.py tests/test_admin_api.py::TestPersonaAdmin::test_timing_gate_events_returns_scoring tests/test_timing_score.py::test_decision_exposes_conflict_and_soft_reject_debug_fields` 通过。
- 提交前全量：`python -B -m pytest tests/ -q -p no:cacheprovider` 通过。
