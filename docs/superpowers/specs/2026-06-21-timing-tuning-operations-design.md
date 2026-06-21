# TimingGate 调参提案运营链路设计

> 2026-06-21 · 基于已完成的 TimingGate 可审核调参提案第一版只读链路，补齐真实 run-scoped audit、最终动作 truth、候选参数治理和 record-only 人工审核闭环。本文只定义下一阶段实现边界，不自动应用参数、不更新 baseline、不改变 PR gate 或周期 gate。

## 背景

TimingGate 混合决策主线已经完成：群聊与私聊共用 scoring 公式，模型失败会进入规则兜底，TimingGate eval baseline 与 PR gate 已稳定运行。围绕真实样本运营，系统已经具备 TimingSignal 周期审计、run-scoped artifact、periodic manifest、artifact trends、只读调参分析，以及 `evals.timing_tuning_proposal` 第一版只读 proposal report。

第一版 proposal 的价值是固定了报告形状和安全边界，但它仍偏向「本地可复核报告」而非「真实运营输入」。当前关键缺口有 4 个：

- TimingSignal audit 可以写 latest、dated 和 run-scoped 三类 artifact，但 proposal 仍允许显式读取 mutable latest，且 dated 路径仍可能被当成可审核证据。
- TimingSignal audit 样本只包含 runtime / scoring action、信号、预览文本和标注 label，不自动包含人工确认后的最终 `timing_action` truth。
- 候选参数文件虽然已有 `candidate_version=1`，但缺少默认路径、重复 ID、空 diff、证据引用和人工说明的治理规则。
- Admin / WebUI 只能只读展示 proposal report，缺少 record-only 的人工审核记录，无法表达「已看过、需要补数据、拒绝、允许进入人工实验」这些运营状态。

下一阶段目标是把 proposal 从「只读报告样板」推进为「真实运营前置材料」。它仍然不能改变线上参数，也不能让任何审核动作具备自动 apply 含义。

## 目标

1. proposal 输入必须绑定目标 run，优先读取 `evals/reports/runs/<run_id>/manifest.json` 和同 run 的 `timing_signal_audit.json`。
2. `final_timing_action` 成为人工最终动作 truth 的 canonical 字段，取值只允许 `continue`、`wait`、`no_reply`。
3. TimingSignal audit 的 label sidecar 支持把 `final_timing_action` 合并进 `samples[]`，并保留 `label`、`note`、`annotator` 等审核上下文。
4. proposal readiness 可以区分缺 truth、truth 非法、audit run 不一致、显式 latest 输入、缺 replay input 和候选参数合同错误。
5. 候选参数文件形成稳定治理规则：默认路径、唯一 ID、非空 `param_diff`、受控参数名、证据引用透传、`expected_effect` 透传。
6. Admin API 增加 record-only 审核记录：读取 proposal 绑定的最新审核状态，写入人工审核记录，不应用参数。
7. WebUI 在「调参提案」tab 展示审核状态，并提供记录型审核入口；页面不得出现「应用参数」「更新 baseline」等生产动作。
8. 计划支持多个子 agent 并行实现互不冲突的模块，主线程负责接口收口、冲突审查、全量验证和提交。

## 非目标

- 不修改 `core/timing_score.py` 默认参数。
- 不新增线上动态调参配置。
- 不新增自动 apply、promote、write config 或 baseline update 入口。
- 不更新 `evals/baselines/timing_gate.json`。
- 不改变 `scripts/run_timing_gate_gate.sh`、统一 PR gate、周期 gate 或 workflow 退出码。
- 不把 `EvalCandidate.expected_json` 复用于 TimingSignal 最终动作 truth。
- 不恢复或扩散废弃的 `expected_action` 字段；仅作为旧 artifact 兼容读取。
- 不重复实现 EvalCandidate 运营趋势报表。
- 不把 `approved_for_manual_experiment` 解释为生产参数已应用。

## 方案选择

### 方案 A：直接把 proposal ready 接入参数应用

该方案能缩短从证据到配置变更的路径，但风险过高。当前 truth、候选文件和真实 replay 输入都没有完全固化，自动 apply 会绕过人工判断，也会让 baseline 和 gate 的含义混在一起。

### 方案 B：复用 EvalCandidate 期望合同承载 final action truth

该方案能借用现有 `import-labels`、`label_candidate()` 和 promote 流程，但会把通用 eval case 的 `expected_json` 和 TimingSignal audit 的运营 truth 混成一套语义。`expected_action` 在通用 expected contract 中已经被拒绝，继续扩散会制造长期兼容债。

### 方案 C：run-scoped audit + sidecar truth + record-only review

这是推荐方案。TimingSignal audit 继续作为真实样本证据载体，JSONL sidecar 只按 `log_id + signal_name` 合并人工 truth。proposal 严格绑定 run-scoped artifact，只读生成 readiness、候选、模拟和审核状态。Admin / WebUI 只写审核记录，不写 live 参数。

该方案的优点是边界清晰：raw evidence 属于 run-scoped audit，人工 truth 属于 sidecar，候选参数属于 proposal input，审核状态属于 Admin audit 或 JSON sidecar。任何一步证据不足都只会让 proposal `ready=false`，不会影响线上行为。

## 数据与 Artifact 合同

### Run-scoped 输入

proposal 应以目标 run 为锚点。推荐 CLI 形态：

```bash
python -B -m evals.timing_tuning_proposal \
  --run-id 20260621_120000_local \
  --manifest evals/reports/runs/20260621_120000_local/manifest.json \
  --trends evals/reports/artifact_trends_latest.json \
  --analysis evals/reports/tuning_analysis_latest.json \
  --params tmp/timing_gate/param_candidates.json \
  --out evals/reports/timing_tuning_proposal_latest.json
```

读取规则：

- `--run-id` 传入时，`manifest.run_id` 必须与其一致。
- manifest 中 TimingSignal step 只接受 `evals/reports/runs/<run_id>/timing_signal_audit.json`。
- 显式 `--timing-audit` 不得指向 `timing_signal_audit_latest.json`。
- dated audit 仅作为人工排查线索，不让 readiness 进入 ready。
- raw audit 必须满足 `source.mode != "skipped"`、`source.reason != "db_not_found"`、`total_samples > 0`、`samples` 非空。
- proposal `source.timing_audit_path` 必须记录 run-scoped 路径，`source.run_id` 来自 manifest，`source.timing_audit_run_id` 来自 audit `source.run_id`。
- 如果 manifest run 和 audit source run 同时存在且不一致，新增阻断原因 `audit_run_mismatch`。

### TimingSignal audit source

TimingSignal audit 生产端应在 `source` 中加入 run 上下文：

```json
{
  "source": {
    "db": "data/nanobot.db",
    "run_id": "20260621_120000_local",
    "after_id": 0,
    "limit": 200,
    "signals": ["s_ack", "s_transport", "w_marker"]
  }
}
```

周期 wrapper 通过 `PERIODIC_RUN_ID` 传入 run id。缺 DB 时仍写 skipped 报告并退出 0，但 proposal 必须把该报告阻断为 `timing_audit_skipped` 和 `timing_zero_samples`。

### Final action truth sidecar

人工标注 sidecar 采用 JSONL。每行绑定一个 TimingSignal sample：

```json
{"log_id":101,"signal_name":"s_ack","final_timing_action":"continue","label":"false_positive","note":"后半句继续提出请求","annotator":"human-a"}
```

字段规则：

- `log_id`：必填，与 audit sample 的 `log_id` 完全一致。
- `signal_name`：必填，与 audit sample 的 `signal_name` 完全一致。
- `final_timing_action`：推荐必填；只允许 `continue`、`wait`、`no_reply`。
- `label`：可选，继续用于 TimingSignal 假阳 / 真阳聚合。
- `note`：可选，用于人工复核说明。
- `annotator`：可选，用于记录标注人或标注批次。

`merge_timing_signal_labels()` 已经支持任意字段透传，因此实现重点不是新建合并器，而是补 schema 校验和测试：合法 truth 能进入 `samples[]`，非法 truth 触发 proposal 阻断 `invalid_action_truth`。

### Replay input

真实 audit sample 当前只有 `text_preview`，不足以可靠重放 `decide_timing()`。如果 proposal 要把真实样本纳入 what-if simulation，audit sample 需要可选 `timing_input`：

```json
{
  "timing_input": {
    "text": "好的，我还想问下明天的安排",
    "is_group": true,
    "is_private": false,
    "is_at_bot": true,
    "is_reply_to_bot": false,
    "bot_name_mentioned": false,
    "direct_call": false,
    "is_directed_to_other": false,
    "has_other_recipient": false,
    "is_other_bot": false,
    "has_files": false,
    "linger_score": 0.0,
    "force_direct_score": null,
    "min_interval_active": false,
    "min_interval_remaining": 0.0,
    "model_hint": null
  }
}
```

没有 `timing_input` 时，proposal 不能用 `text_preview` 静默模拟真实样本。行为选择为：eval case simulation 仍可运行；真实样本 simulation 记录 `skipped_audit_sample_count`，并在 readiness 中给出 `missing_replay_input`。

## 候选参数治理

默认候选参数路径：

```text
tmp/timing_gate/param_candidates.json
```

候选文件 schema 继续使用 `candidate_version=1`：

```json
{
  "candidate_version": 1,
  "source": {
    "author": "manual",
    "reason": "review_s_ack_false_positive",
    "run_id": "20260621_120000_local"
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
      "risk_level": "medium",
      "evidence_refs": [
        {
          "type": "timing_signal_audit_sample",
          "path": "evals/reports/runs/20260621_120000_local/timing_signal_audit.json",
          "log_id": 101,
          "signal_name": "s_ack"
        }
      ]
    }
  ]
}
```

治理规则：

- `candidate_version` 必须为 `1`。
- `candidates[]` 必须非空。
- `id` 必须非空且唯一。
- `param_diff` 必须是非空对象。
- 参数名必须属于 `ALLOWED_PARAM_NAMES`。
- `evidence_refs` 透传到 `candidate_sets[]`，不再固定为空数组。
- `expected_effect` 透传到 `candidate_sets[]`，仅供人工阅读，不参与 readiness。
- unsupported param、重复 ID、空 diff、缺 ID 都使用稳定 blocking reason 表达，不抛出不可控异常。

## Proposal 输出调整

在保持 `proposal_version=1` 兼容的前提下，新增或加厚字段：

```json
{
  "source": {
    "run_id": "20260621_120000_local",
    "timing_audit_run_id": "20260621_120000_local",
    "timing_audit_path": "evals/reports/runs/20260621_120000_local/timing_signal_audit.json",
    "params_path": "tmp/timing_gate/param_candidates.json"
  },
  "readiness": {
    "ready": false,
    "blocking_reasons": [
      {"code": "missing_replay_input", "message": "真实 audit 样本缺少 timing_input，不能纳入参数模拟"}
    ]
  },
  "candidate_sets": [
    {
      "id": "ack_threshold_soften_v1",
      "area": "timing_score",
      "risk_level": "medium",
      "rationale": "降低 s_ack",
      "expected_effect": "减少短确认误杀后续请求",
      "param_diff": {"s_ack": 0.75},
      "evidence_refs": []
    }
  ],
  "simulation": {
    "sources": {
      "eval_case_count": 20,
      "audit_sample_count": 0,
      "skipped_audit_sample_count": 12
    }
  },
  "apply_policy": "manual_only",
  "blocked_actions": ["auto_apply", "baseline_update", "gate_change"]
}
```

新增 blocking code：

- `explicit_latest_audit`
- `audit_not_run_scoped`
- `audit_run_mismatch`
- `invalid_action_truth`
- `missing_replay_input`
- `duplicate_candidate_id`
- `empty_candidate_param_diff`
- `missing_candidate_id`

旧兼容：

- `expected_action` 和 `timing_action_truth` 可以继续作为历史 artifact truth 兼容读取。
- 新写文档、测试和 sidecar 均使用 `final_timing_action`。
- `apply_policy` 固定为 `manual_only`。
- `blocked_actions` 固定包含 `auto_apply`、`baseline_update`、`gate_change`。

## Record-only 审核接口

Admin API 建议新增：

- `GET /api/v1/admin/evals/timing-tuning/proposal/review`
- `POST /api/v1/admin/evals/timing-tuning/proposal/reviews`

审核记录绑定字段：

- `proposal_sha256`
- `report_path`
- `proposal_version`
- `generated_at`
- `run_id`
- `reviewer`
- `decision`
- `reason_code`
- `note`

允许 decision：

- `needs_data`
- `rejected`
- `approved_for_manual_experiment`
- `reviewed_no_change`

语义：

- `needs_data`：当前证据不足，需要补 audit、truth、replay input 或候选参数。
- `rejected`：当前候选方向不采纳。
- `approved_for_manual_experiment`：允许进入人工实验或单独 PR，不代表已应用生产参数。
- `reviewed_no_change`：人工确认当前不改参数。

存储策略优先复用 `AdminAuditLog`，action 使用 `review_timing_tuning_proposal`，`target_type` 使用 `timing_tuning_proposal`，`target_id` 使用 `proposal_sha256`。如果测试环境不便于查询最近记录，可在 API 层封装只读 helper，避免新增 DB schema。

POST 行为只写审核记录，不修改 proposal report、不修改候选文件、不改 eval case、不改 baseline、不改 live config。

## WebUI 展示

「调参提案」tab 在现有只读 report 展示基础上增加：

- proposal hash、run id、audit path 和 params path。
- 最近审核记录状态。
- record-only 审核表单：decision、reason code、note。
- `approved_for_manual_experiment` 的解释文案必须表达「进入人工实验」，不能表达「应用参数」。

禁止 UI 文案和按钮：

- `应用参数`
- `自动应用`
- `更新 baseline`
- `写入配置`
- `Promote 参数`

WebUI 静态测试应继续断言这些词不存在。

## 子 agent 分工

实现阶段建议拆成 5 个互不重叠的 owner。所有子 agent 默认只改自己 owner 文件，跨 owner 需求交回主线程。

### Agent A：TimingSignal truth 与 run source

Owner：

- `core/eval_sampling/timing_signal_audit.py`
- `evals/timing_signal_audit.py`
- `scripts/run_timing_signal_audit_periodic.sh`
- `tests/test_timing_signal_audit.py`
- `tests/test_timing_signal_audit_periodic.py`

接口：

- 增加 `source.run_id`。
- 校验 `final_timing_action` 枚举。
- 保持 label 合并向后兼容。

### Agent B：Proposal run-scoped 输入与候选治理

Owner：

- `evals/timing_tuning_proposal.py`
- `tests/test_timing_tuning_proposal.py`

接口：

- 收紧 audit path 解析。
- 增加 `--run-id` 与默认 params 路径。
- 加厚 readiness blocking。
- 透传 `expected_effect` 和 `evidence_refs`。

### Agent C：真实样本 simulation 来源标识

Owner：

- `evals/timing_score_simulation.py`
- `tests/test_timing_score_simulation.py`
- `tests/test_timing_tuning_proposal.py`

接口：

- 只使用 `timing_input` 重放真实样本。
- `flips[]` 增加 `source_type`、`log_id`、`signal_name`。
- `simulation.sources` 统计 eval case 和 audit sample。

### Agent D：Admin record-only 审核 API

Owner：

- `api/admin_routes.py`
- `tests/test_timing_tuning_proposal_admin.py`

接口：

- 新增 review GET / POST。
- 使用 `AdminAuditLog` 存记录。
- 不写 report，不写配置，不写 baseline。

### Agent E：WebUI 审核状态

Owner：

- `webui/src/features/evals/EvalsPage.jsx`
- `tests/test_webui_admin_redesign.py`

接口：

- 展示审核状态。
- 提供 record-only 表单。
- 继续禁止生产动作按钮。

## 测试策略

定向测试：

- `python -B -m pytest tests/test_timing_signal_audit.py tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider`
- `python -B -m pytest tests/test_timing_tuning_proposal.py -q -p no:cacheprovider`
- `python -B -m pytest tests/test_timing_score_simulation.py -q -p no:cacheprovider`
- `python -B -m pytest tests/test_timing_tuning_proposal_admin.py -q -p no:cacheprovider`
- `python -B -m pytest tests/test_webui_admin_redesign.py -q -p no:cacheprovider`

集成验证：

- `bash scripts/run_timing_gate_gate.sh`
- `bash scripts/run_eval_periodic.sh`
- `npm --prefix webui run build`
- `python -m pytest tests/ -v`

关键断言：

- 显式 latest audit 被阻断。
- skipped run-scoped audit 被阻断。
- manifest run 和 audit run 不一致被阻断。
- 缺 `final_timing_action` 时 `missing_action_truth` 保持。
- 非法 `final_timing_action` 触发 `invalid_action_truth`。
- 候选重复 ID、空 diff、缺 ID、unsupported param 均被阻断。
- review POST 只写一条审核记录，不改变 proposal report 和候选状态。
- WebUI 不出现生产应用按钮。

## 推进顺序

1. 先实现 TimingSignal truth 与 run source，确保真实 audit 可以承载 proposal 需要的证据。
2. 再收紧 proposal run-scoped 输入和候选参数治理。
3. 再补真实样本 simulation 的来源标识和 `timing_input` 守卫。
4. 再接 Admin record-only 审核 API。
5. 最后接 WebUI 审核状态和文档收口。

每个阶段完成后按项目约定运行验证、显式暂存相关文件并单独提交。
