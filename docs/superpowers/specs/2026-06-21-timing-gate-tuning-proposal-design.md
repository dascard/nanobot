# TimingGate 可审核调参提案设计

> 2026-06-21 · 在只读调参分析和 TimingSignal 不可变 artifact 之后，生成供人工审核的 TimingGate 参数调整提案。第一版只输出证据、候选方向、模拟结果和阻断原因，不自动应用参数、不更新 baseline、不改变 gate。

## 背景

TimingGate 混合决策主线已经落地。当前系统具备 shared timing scoring、群聊 / 私聊统一公式、模型失败规则兜底、`rules_only` / `shadow` 策略、真实日志 TimingSignal audit、`timing_gate` eval baseline diff / PR gate、周期运行 manifest、跨 artifact 趋势、只读调参分析，以及 latest / dated / run-scoped 三类 TimingSignal audit artifact。

这些能力已经能回答「最近哪些信号或评测可能需要复核」，但还不能安全回答「应该把哪个参数改成多少」。现有 `evals.tuning_analysis` 主要做趋势归因和复核建议；TimingSignal audit 的 `true_positive` / `false_positive` 只说明某个信号是否误判，不等价于最终 `timing_action` 真值。当前仓库内 `evals/reports/timing_signal_audit_latest.json` 也可能是 `source.mode=skipped`、`total_samples=0`，并且本地可能缺少 `artifact_trends_latest.json` 和 `tuning_analysis_latest.json`。

因此下一阶段不直接调参，而是先新增一个「可审核调参提案」报告：读取已有 artifact、显式候选参数和 eval case，生成 readiness、证据引用、what-if 模拟和验证建议。证据不足时报告仍生成，但必须阻断人工调参讨论。

## 目标

新增只读 proposal 报告，默认输出：

```text
evals/reports/timing_tuning_proposal_latest.json
```

目标包括：

1. 汇总输入 artifact 的版本、路径、run id、git sha 和 TimingSignal audit mode。
2. 判断是否具备进入人工调参讨论的最低证据。
3. 明确列出阻断原因，例如 audit skipped、零样本、缺少最终动作 truth 或缺少不可变 artifact。
4. 读取显式候选参数集，输出候选组的证据、预期影响、风险和非目标。
5. 基于现有 TimingGate eval case 或人工 truth 样本做只读 what-if 模拟，展示翻转样本和聚合指标。
6. 输出人工审核需要运行的验证命令和观察项。
7. 固定声明 `apply_policy=manual_only`，并列出禁止动作。

## 非目标

- 不自动修改 `core/timing_score.py` 的 live 常量。
- 不新增线上动态配置入口。
- 不更新 `evals/baselines/timing_gate.json` 或其他 baseline。
- 不改变 `scripts/run_timing_gate_gate.sh`、统一 PR gate、周期 gate 或 workflow 退出码。
- 不把 proposal 接入阻断性 CI gate。
- 不把 `runtime_action`、`scoring_action` 或信号真假阳 label 当作最终动作 truth。
- 不读取生产 DB；真实日志采样仍通过既有 TimingSignal audit / EvalCandidate 流程完成。
- 不调用模型，不运行 RAG benchmark，不写 DB。
- 不提供 `--apply`、`--update-baseline`、`--write-config`、`--promote` 等命令。

## 方案选择

### 方案 A：只做文档化人工流程

优点是没有实现风险，也不会影响任何 gate。缺点是后续每次调参都要人工拼 artifact、case 和参数差异，无法稳定复核同一批证据，也不利于子 agent 并行实现。

### 方案 B：直接补 TimingSignal truth，再做 proposal

优点是证据更完整。缺点是会先改 audit / labeling 链路，范围更大；在 proposal schema 未稳定前，多个 worker 容易各自定义字段，后续集成成本高。

### 方案 C：只读 proposal schema + readiness blocking + 离线模拟

这是推荐方案。先固定报告合同、输入路径、阻断条件、候选参数格式、模拟输出和后续 worker 文件 owner。当前 artifact 不足时，报告 `readiness.ready=false`，不能伪造生产证据；后续再逐步补 final truth、证据加厚、Admin API 和 WebUI 展示。

## 输入

默认输入建议：

- `evals/reports/periodic_manifest_latest.json`
- `evals/reports/artifact_trends_latest.json`
- `evals/reports/tuning_analysis_latest.json`
- `evals/reports/timing_signal_audit_latest.json`
- `evals/cases/timing_gate/`
- `evals/baselines/timing_gate.json`

CLI 应允许显式指定 run-scoped 或 dated artifact：

```bash
python -B -m evals.timing_tuning_proposal \
  --manifest evals/reports/periodic_manifest_latest.json \
  --trends evals/reports/artifact_trends_latest.json \
  --analysis evals/reports/tuning_analysis_latest.json \
  --timing-audit evals/reports/runs/<run_id>/timing_signal_audit.json \
  --cases evals/cases/timing_gate \
  --baseline evals/baselines/timing_gate.json \
  --params tmp/timing_gate/param_candidates.json \
  --out evals/reports/timing_tuning_proposal_latest.json
```

输入规则：

- `latest` 只作为本地默认入口；可审核证据优先引用 `evals/reports/runs/<run_id>/...`，dated 路径次之。
- `--timing-audit` 缺省时可以从 manifest 的 TimingSignal step 中解析 run-scoped 报告，不能只依赖 mutable latest 当作历史证据。
- `--params` 必须显式传入。没有候选参数时，报告只能输出 readiness 和补数据建议。
- `--baseline` 只读，用于记录当前 gate 参照和模拟风险，不允许写回。
- 缺少任一核心输入时，不抛出不可读错误；报告应以 `ready=false` 输出稳定 blocking reason，除非 JSON 无法解析或版本不支持。

## 候选参数格式

候选参数文件是人工准备的只读输入，不代表线上配置。

```json
{
  "candidate_version": 1,
  "source": {
    "author": "manual",
    "reason": "review_s_ack_false_positive"
  },
  "candidates": [
    {
      "id": "ack_threshold_soften_v1",
      "description": "降低 s_ack 对非纯确认样本的抑制强度",
      "scope": "timing_score",
      "param_diff": {
        "s_ack": 0.75
      },
      "expected_effect": "减少短确认误杀后续请求",
      "risk_level": "medium"
    }
  ]
}
```

第一版只允许引用已存在的稳定参数或信号名：

- `BASE_SCORE`
- `DIRECT_WEIGHT`
- `SUPPRESS_WEIGHT`
- `DECISION_MARGIN`
- `CONFLICT_THRESHOLD`
- `MODEL_WEIGHT_SCALE`
- `BOT_SOFT_REJECT_GAMMA`
- `s_ack`
- `s_transport`
- `s_other`
- `s_bot`
- `w_marker`
- `w_file`
- `w_incomplete`

不允许候选文件新增线上配置项、改函数签名、改策略模式或改 baseline 阈值。

## 输出

报告结构：

```json
{
  "proposal_version": 1,
  "generated_at": "2026-06-21T12:00:00+08:00",
  "source": {
    "git_sha": "abc1234",
    "manifest_path": "evals/reports/periodic_manifest_latest.json",
    "trends_path": "evals/reports/artifact_trends_latest.json",
    "analysis_path": "evals/reports/tuning_analysis_latest.json",
    "timing_audit_path": "evals/reports/runs/<run_id>/timing_signal_audit.json",
    "baseline_path": "evals/baselines/timing_gate.json",
    "run_id": "20260621_120000_local",
    "timing_audit_mode": "sampled"
  },
  "readiness": {
    "ready": false,
    "blocking_reasons": [
      {
        "code": "missing_action_truth",
        "message": "TimingSignal audit label 不是最终 timing_action 真值"
      }
    ]
  },
  "candidate_sets": [],
  "parameters": [],
  "simulation": {
    "case_count": 0,
    "candidate_count": 0,
    "flip_count": 0,
    "flips": [],
    "aggregates": []
  },
  "validation_plan": [],
  "apply_policy": "manual_only",
  "blocked_actions": [
    "auto_apply",
    "baseline_update",
    "gate_change"
  ]
}
```

### Readiness

`readiness.ready` 表示是否具备进入人工调参讨论的最低证据。即使 `ready=false`，报告仍应输出 source、blocking reasons、补数据建议和可运行验证命令。

稳定 blocking code：

- `manifest_missing`
- `trends_missing`
- `analysis_missing`
- `timing_audit_missing`
- `timing_audit_skipped`
- `timing_zero_samples`
- `missing_immutable_artifact`
- `missing_action_truth`
- `missing_param_candidates`
- `unsupported_candidate_version`
- `unsupported_proposal_input`
- `baseline_missing`

第一版 readiness 默认规则：

- TimingSignal audit 为 skipped 或零样本时阻断。
- 没有 run-scoped 或 dated audit 引用时阻断。
- 没有最终动作 truth 时阻断。
- 没有候选参数时阻断。
- baseline 缺失时阻断。
- `evals.tuning_analysis` 已经 `ready=false` 时，proposal 不得升级为 `ready=true`。

### Candidate Sets

`candidate_sets[]` 记录每个候选的人工说明和证据引用：

```json
{
  "id": "ack_threshold_soften_v1",
  "area": "timing_signal",
  "risk_level": "medium",
  "rationale": "s_ack 假阳率偏高，且存在短确认后追加请求的误杀样本",
  "param_diff": {
    "s_ack": 0.75
  },
  "evidence_refs": [
    {
      "type": "timing_signal_audit_sample",
      "path": "evals/reports/runs/<run_id>/timing_signal_audit.json",
      "log_id": 123
    }
  ],
  "non_goals": [
    "不自动修改 live 参数",
    "不更新 baseline"
  ]
}
```

### Simulation

`simulation` 是离线 what-if 结果。第一版优先覆盖 timing_gate eval case；有人工 final truth 样本时再纳入真实样本。

样本翻转结构：

```json
{
  "candidate_id": "ack_threshold_soften_v1",
  "case_id": "timing_gate/ambient_ack_request_001",
  "source_ref": "evals/cases/timing_gate/ambient_ack_request_001.json",
  "expected_action": "continue",
  "before": {
    "action": "no_reply",
    "stage": "rule_shortcut",
    "participation_score": 0.18,
    "final_score": 0.18,
    "theta": 0.30,
    "conflict_score": 0.10
  },
  "after": {
    "action": "wait",
    "stage": "model_assisted_conflict",
    "participation_score": 0.28,
    "final_score": 0.28,
    "theta": 0.30,
    "conflict_score": 0.20
  },
  "signals": {
    "sub_signals": {
      "s_ack": 0.75
    }
  },
  "risk_tag": "expected_improved"
}
```

聚合维度：

- `candidate_id`
- `signal`
- `stage`
- `expected_action`
- `before_action`
- `after_action`
- `trigger_reason`
- `model_used`

模拟结果不能被解释为生产效果证明；它只说明「如果用候选参数重放当前样本，会发生哪些动作翻转」。

### Validation Plan

`validation_plan[]` 给出人工审核前必须运行的命令：

```json
{
  "name": "timing_gate_baseline_gate",
  "command": "bash scripts/run_timing_gate_gate.sh",
  "purpose": "确认现有 baseline gate 未被 proposal 生成过程改变"
}
```

第一版默认包含：

- `python -B -m pytest tests/test_timing_tuning_proposal.py -q -p no:cacheprovider`
- `python -B -m pytest tests/test_timing_score.py tests/test_timing_gate.py tests/test_timing_runtime.py -q -p no:cacheprovider`
- `python -B -m pytest tests/test_eval_artifact_trends.py tests/test_periodic_tuning_analysis.py tests/test_timing_signal_audit.py tests/test_eval_baseline.py -q -p no:cacheprovider`
- `bash scripts/run_timing_gate_gate.sh`

## 模块边界

### Worker A：Proposal schema 与 CLI

文件 owner：

- 新建 `evals/timing_tuning_proposal.py`
- 新建 `tests/test_timing_tuning_proposal.py`

职责：

- 定义 proposal report builder。
- 读取输入 artifact。
- 生成 readiness blocking。
- 解析候选参数文件。
- 输出 `timing_tuning_proposal_latest.json`。
- 拒绝 `--apply`、`--update-baseline`、`--write-config` 等模式。

### Worker B：TimingSignal 证据加厚

文件 owner：

- `core/eval_sampling/timing_signal_audit.py`
- `evals/timing_signal_audit.py`
- `tests/test_timing_signal_audit.py`

职责：

- 只添加 proposal 需要的可选字段，例如 `scoring_stage`、`threshold_band`、`signal_context`。
- 保持旧 report 字段兼容。
- 不把信号 label 升级成 final truth。

### Worker C：What-if 模拟

文件 owner：

- 新建 `evals/timing_score_simulation.py`
- 新建 `tests/test_timing_score_simulation.py`

职责：

- 基于 eval case 和候选参数做离线模拟。
- 输出 before / after action、stage、score 和 flip aggregate。
- 不接入 `GroupRuntime`，不改变 live `decide_timing()` 默认行为。

如果后续需要 `TimingScoreConfig` 或 `decide_timing_with_config()`，必须由主线程指定单一 owner，并确保默认 `decide_timing()` 结果完全不变。

### Worker D：Admin 只读 API

文件 owner：

- `api/admin_routes.py`
- 新建 `tests/test_timing_tuning_proposal_admin.py`

职责：

- 暴露只读报告端点，例如 `GET /api/v1/admin/evals/timing-tuning/proposal`。
- 只读取报告 JSON，不计算、不写 DB。
- 缺报告时返回可解释状态，不触发生成。

### Worker E：WebUI 与文档

文件 owner：

- `webui/src/features/evals/EvalsPage.jsx`
- WebUI 静态测试文件
- `docs/evals.md`
- `docs/plan_walkthrough.md`
- 必要时同步 `docs/todo.md`

职责：

- 在评测页面展示 proposal readiness、candidate sets、simulation flips 和 blocked actions。
- 文档说明生成流程、人工审核边界和禁止自动 apply。
- WebUI 不提供“应用参数”按钮。

## 不可并行编辑的文件

以下文件只能由主线程或单一 owner 修改：

- `core/timing_score.py`
- `core/group_runtime/runtime.py`
- `core/private_timing.py`
- `api/routes.py`
- `api/admin_routes.py`
- `evals/tuning_analysis.py`
- `scripts/run_eval_periodic.sh`
- `scripts/run_timing_gate_gate.sh`
- `evals/baselines/timing_gate.json`
- `tests/conftest.py`
- `docs/todo.md`
- `docs/plan_walkthrough.md`

## 验收计划

设计文档阶段：

1. 检查文档没有占位符、自动应用口径或 baseline 更新口径。
2. 运行：

```bash
git diff --check -- docs/superpowers/specs/2026-06-21-timing-gate-tuning-proposal-design.md
```

实现阶段按 TDD 拆分：

1. `tests/test_timing_tuning_proposal.py::test_proposal_blocks_missing_or_skipped_inputs`：缺 trends、audit skipped、零样本、缺 final truth 时 `ready=false`。
2. `tests/test_timing_tuning_proposal.py::test_current_config_simulation_is_identity`：当前参数重放 eval case 时不产生翻转，且 baseline 只读。
3. `tests/test_timing_tuning_proposal.py::test_candidate_config_reports_flips_with_score_breakdown`：候选参数导致翻转时输出 before / after score、stage、signals 和 risk tag。
4. `tests/test_timing_tuning_proposal.py::test_cli_has_no_apply_or_baseline_write_modes`：CLI 不接受 apply / update-baseline / write-config。
5. `tests/test_timing_signal_audit.py`：新增证据字段保持旧 report 兼容，缺 final truth 不得伪装为 ready。
6. `tests/test_timing_score_simulation.py`：what-if 模拟只使用显式候选参数，不改变 live 默认行为。
7. Admin / WebUI 阶段分别跑专用 API 测试、WebUI 静态测试和 `npm --prefix webui run build`。

最终收敛验证：

```bash
python -B -m pytest \
  tests/test_timing_score.py \
  tests/test_timing_gate.py \
  tests/test_timing_runtime.py \
  tests/test_eval_artifact_trends.py \
  tests/test_periodic_tuning_analysis.py \
  tests/test_timing_signal_audit.py \
  tests/test_eval_baseline.py \
  -q -p no:cacheprovider

bash scripts/run_timing_gate_gate.sh

python -m pytest tests/ -v
```

## 风险与约束

- 当前仓库内 artifact 可能不足以生成真实调参提案。实现必须把这种状态表达为 `ready=false`，不能输出含糊的候选参数结论。
- 参数之间存在耦合。`s_ack`、`s_transport`、`m`、`κ`、模型权重和 soft reject cap 不能只看 aggregate pass rate，必须展示样本级翻转。
- 群聊和私聊共享 scoring 公式。任何后续 live 参数变更都必须同时验证群聊和私聊路径；proposal 第一版只提出证据，不改 live 行为。
- `latest` 文件会被覆盖。人工审核时必须优先引用 run-scoped artifact。
- Admin / WebUI 只能展示只读报告，不提供应用参数入口。
- 多 agent 并行实现时，必须严格按文件 owner 分工；主线程负责合并、验证和最终提交。
