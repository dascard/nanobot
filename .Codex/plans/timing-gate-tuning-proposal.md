# TimingGate 可审核调参提案实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新增只读 TimingGate 调参提案报告，把周期 artifact、TimingSignal audit、显式候选参数和 eval case 转成可人工审核的 proposal，不自动应用参数、不更新 baseline、不改变 gate。

**架构：** 先固定 `evals.timing_tuning_proposal` 的 report schema、readiness blocking、候选参数解析和 CLI，再接入独立 what-if 模拟与可选 TimingSignal 证据加厚。Admin API 与 WebUI 只在 report schema 稳定后读取并展示 JSON 报告；文档最后收口。核心实现只读 JSON artifact 和 eval case，不读取生产 DB，不调用模型，不写 live 配置。

**技术栈：** Python 标准库、pytest、JSON artifact、现有 `evals.tuning_analysis` / `evals.artifact_trends` / `evals.periodic_manifest` / `evals.timing_signal_audit` 合同、FastAPI Admin 路由、React WebUI 静态测试。

---

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-21-timing-gate-tuning-proposal-design.md`
- 前置能力：
  - `evals.artifact_trends` 已输出跨 run 趋势。
  - `evals.tuning_analysis` 已输出只读调参分析。
  - TimingSignal audit 已具备 latest、dated、run-scoped 三类 artifact。
  - `timing_gate` eval baseline gate 已稳定运行。
- 范围边界：
  - 第一版只生成 `evals/reports/timing_tuning_proposal_latest.json`。
  - `ready=false` 是合法输出，用于表达证据不足。
  - 不提供任何 `apply`、baseline 更新、配置写入或 promote 入口。

## 计划文件位置

- 本计划唯一 source of truth：`.Codex/plans/timing-gate-tuning-proposal.md`
- 不在 `.codex/plans/` 另写同名计划。当前仓库同时存在 `.Codex/plans` 与 `.codex/plans`，在 WSL / Linux 中二者是不同目录，worker 必须按本计划路径读写。

## 文件结构

- 创建：`evals/timing_tuning_proposal.py`
  - 负责 JSON 输入读取、manifest audit 路径解析、候选参数解析、readiness blocking、proposal report 构建、写文件和 CLI。
  - 不依赖数据库、不调用模型、不运行 `evals.run`、不修改任何输入 artifact。
- 创建：`tests/test_timing_tuning_proposal.py`
  - 覆盖 proposal schema、blocking code、候选参数校验、CLI 写报告和禁止自动应用入口。
- 创建：`evals/timing_score_simulation.py`
  - 负责离线 what-if 模拟。输入为 timing_gate eval case、当前参数快照和显式候选参数 diff，输出 before / after action、score、stage、signals 和翻转聚合。
  - 不接入 `GroupRuntime`，不改变 live `decide_timing()` 默认行为。
- 创建：`tests/test_timing_score_simulation.py`
  - 覆盖 identity 模拟、候选参数翻转、未知参数拒绝和 baseline 只读边界。
- 修改：`core/eval_sampling/timing_signal_audit.py`
  - 可选加厚 proposal 需要的证据字段，保持旧 report 字段兼容。
- 修改：`evals/timing_signal_audit.py`
  - 保持 CLI 兼容，仅在输入样本包含新增字段时透传到 report。
- 修改：`tests/test_timing_signal_audit.py`
  - 覆盖新增证据字段不破坏旧聚合，且 signal label 不被当作 final action truth。
- 修改：`api/admin_routes.py`
  - 增加只读 report endpoint，例如 `GET /api/v1/admin/evals/timing-tuning/proposal`。
  - 只读取报告 JSON；缺报告返回可解释状态；不触发生成、不写 DB。
- 创建：`tests/test_timing_tuning_proposal_admin.py`
  - 覆盖鉴权、缺报告、合法报告读取和禁止写入语义。
- 修改：`webui/src/features/evals/EvalsPage.jsx`
  - 在 Eval 页面增加 proposal 只读展示入口，展示 readiness、blocking reasons、candidate sets、simulation 和 blocked actions。
- 修改：`tests/test_webui_admin_redesign.py`
  - 静态断言 WebUI 有 proposal 入口、读取只读 endpoint、没有应用参数按钮。
- 修改：`docs/evals.md`
  - 记录 proposal CLI、输入 artifact、readiness blocking 和人工审核边界。
- 修改：`docs/todo.md`
  - 同步路线项 8 / 10 状态。
- 修改：`docs/plan_walkthrough.md`
  - 记录阶段提交、验证命令和下一阶段边界。

## 接口约定

### CLI

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

CLI 必须返回 0 并写出 JSON report，只要输入 JSON 可解析。缺少 artifact、audit skipped、零样本、缺候选参数或缺 final action truth 时，report 使用 `readiness.ready=false` 和稳定 blocking reason 表达。

CLI 不接受以下参数：

- `--apply`
- `--update-baseline`
- `--write-config`
- `--promote`

### 核心函数命名

Worker A 固定以下公开函数和常量名，后续 worker 按这些接口接入：

- `PROPOSAL_VERSION = 1`
- `DEFAULT_MANIFEST`
- `DEFAULT_TRENDS`
- `DEFAULT_ANALYSIS`
- `DEFAULT_TIMING_AUDIT`
- `DEFAULT_CASES_DIR`
- `DEFAULT_BASELINE`
- `DEFAULT_OUT`
- `ALLOWED_PARAM_NAMES`
- `BLOCKED_ACTIONS`
- `load_json_object(path)`
- `resolve_proposal_timing_audit_path(manifest, explicit_path)`
- `write_timing_tuning_proposal(payload, out_path)`
- `parse_candidate_sets(params)`
- `validation_plan()`
- `build_timing_tuning_proposal(...)`
- `build_parser()`
- `main(argv=None)`

### 候选参数 schema

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

允许参数名固定为：

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

### Proposal schema

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
    "params_path": "tmp/timing_gate/param_candidates.json",
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

### Blocking code

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

`analysis.readiness.ready=false` 不复用 `analysis_missing`，而是使用 `unsupported_proposal_input` 并携带 `source="tuning_analysis"` 与上游 blocking reasons。这样 `analysis_missing` 只表示文件或对象缺失，避免把“存在但证据不足”误读成输入丢失。

## 子 agent 分工与边界

### Worker A：Proposal schema 与 CLI

Owner：

- `evals/timing_tuning_proposal.py`
- `tests/test_timing_tuning_proposal.py`

职责：

- 固定 proposal schema、candidate parser 和 readiness blocking。
- 输出 `build_timing_tuning_proposal(...)`、`write_timing_tuning_proposal(...)`、`main(argv)`。
- 作为其他 worker 的接口前置，必须最先完成。

### Worker C：What-if 模拟

Owner：

- `evals/timing_score_simulation.py`
- `tests/test_timing_score_simulation.py`

职责：

- 在 Worker A schema 稳定后并行实现。
- 只接收显式候选参数和 eval case。
- 如果需要改 `core/timing_score.py`，必须暂停并交由主线程单一 owner 修改。

### Worker B：TimingSignal 证据加厚

Owner：

- `core/eval_sampling/timing_signal_audit.py`
- `evals/timing_signal_audit.py`
- `tests/test_timing_signal_audit.py`

职责：

- 在 Worker A 的 `evidence_refs` 结构稳定后并行实现。
- 只新增可选证据字段，不改旧 report 含义。
- 不把 signal label 升级成 final action truth。

### Worker D：Admin 只读 API

Owner：

- `api/admin_routes.py`
- `tests/test_timing_tuning_proposal_admin.py`

职责：

- 在 Worker A 的 report 文件格式稳定后开始。
- `api/admin_routes.py` 是大共享文件，同一时间只能一个 worker 写。
- endpoint 只读 report，不生成、不写 DB、不提供 apply。

### Worker E：WebUI 与文档

Owner：

- `webui/src/features/evals/EvalsPage.jsx`
- `tests/test_webui_admin_redesign.py`
- `docs/evals.md`
- `docs/todo.md`
- `docs/plan_walkthrough.md`
- `.Codex/plans/timing-gate-tuning-proposal.md`

职责：

- WebUI 在 Worker D API 合同稳定后开始。
- 文档最后串行收口，记录真实验证结果和提交号。

### 不可并行编辑

- `core/timing_score.py`
- `core/group_runtime/runtime.py`
- `core/private_timing.py`
- `api/routes.py`
- `api/admin_routes.py`
- `evals/tuning_analysis.py`
- `scripts/run_eval_periodic.sh`
- `scripts/run_eval_pr_gate.sh`
- `scripts/run_timing_gate_gate.sh`
- `evals/baselines/timing_gate.json`
- `tests/conftest.py`
- `docs/todo.md`
- `docs/plan_walkthrough.md`
- `.Codex/plans/timing-gate-tuning-proposal.md`

## 任务 1：建立 Proposal 核心报告骨架

**文件：**

- 创建：`tests/test_timing_tuning_proposal.py`
- 创建：`evals/timing_tuning_proposal.py`

- [ ] **步骤 1：编写 readiness 红灯测试**

在 `tests/test_timing_tuning_proposal.py` 中新增基础 helper 和 readiness 测试：

```python
import json


def _manifest(audit_path: str = "evals/reports/runs/run_1/timing_signal_audit.json") -> dict:
    return {
        "manifest_version": 1,
        "run_id": "run_1",
        "git": {"sha": "abc123", "ref": "master", "repository": ""},
        "steps": [{
            "kind": "timing_signal_audit",
            "suite": "timing_signal_audit",
            "report_paths": [audit_path],
            "summary": {"total_samples": 20, "labeled_samples": 10},
        }],
    }


def _trends() -> dict:
    return {
        "trend_version": 1,
        "source": {"run_count": 3, "deduped_run_ids": ["run_1", "run_2", "run_3"]},
        "summary": {"latest_run_id": "run_3", "previous_run_id": "run_2"},
        "series": {"runs": [], "eval_suites": {}, "rag_benchmark": [], "timing_signal_audit": []},
        "regressions": [],
    }


def _analysis(ready: bool = True) -> dict:
    return {
        "analysis_version": 1,
        "readiness": {"ready": ready, "blocking_reasons": [] if ready else [{"code": "low_label_coverage"}]},
        "signals": [],
        "recommendations": [],
    }


def _audit(*, total_samples: int = 20, source: dict | None = None, samples: list[dict] | None = None) -> dict:
    return {
        "total_samples": total_samples,
        "labeled_samples": 10 if total_samples else 0,
        "signals": {},
        "shadow": {"action_mismatch_count": 0, "action_mismatch_rate": 0.0},
        "samples": samples or [],
        "source": source or {"mode": "sampled"},
    }


def _params() -> dict:
    return {
        "candidate_version": 1,
        "source": {"author": "manual", "reason": "unit"},
        "candidates": [{
            "id": "ack_threshold_soften_v1",
            "description": "降低 s_ack",
            "scope": "timing_score",
            "param_diff": {"s_ack": 0.75},
            "expected_effect": "减少误杀",
            "risk_level": "medium",
        }],
    }


def _reason_codes(report: dict) -> set[str]:
    return {item["code"] for item in report["readiness"]["blocking_reasons"]}


def test_proposal_blocks_missing_inputs_and_does_not_crash():
    from evals.timing_tuning_proposal import build_timing_tuning_proposal

    report = build_timing_tuning_proposal(
        manifest=None,
        trends=None,
        analysis=None,
        timing_audit=None,
        baseline=None,
        params=None,
        source_paths={},
    )

    assert report["proposal_version"] == 1
    assert report["readiness"]["ready"] is False
    assert _reason_codes(report) >= {
        "manifest_missing",
        "trends_missing",
        "analysis_missing",
        "timing_audit_missing",
        "baseline_missing",
        "missing_param_candidates",
    }
    assert report["apply_policy"] == "manual_only"
    assert report["blocked_actions"] == ["auto_apply", "baseline_update", "gate_change"]


def test_proposal_blocks_skipped_zero_and_missing_action_truth():
    from evals.timing_tuning_proposal import build_timing_tuning_proposal

    skipped = build_timing_tuning_proposal(
        manifest=_manifest(),
        trends=_trends(),
        analysis=_analysis(),
        timing_audit=_audit(total_samples=0, source={"mode": "skipped", "reason": "db_not_found"}),
        baseline={"suite": "timing_gate"},
        params=_params(),
        source_paths={"timing_audit": "evals/reports/runs/run_1/timing_signal_audit.json"},
    )
    no_truth = build_timing_tuning_proposal(
        manifest=_manifest(),
        trends=_trends(),
        analysis=_analysis(),
        timing_audit=_audit(samples=[{"log_id": 1, "signal_name": "s_ack", "label": "false_positive"}]),
        baseline={"suite": "timing_gate"},
        params=_params(),
        source_paths={"timing_audit": "evals/reports/runs/run_1/timing_signal_audit.json"},
    )

    assert "timing_audit_skipped" in _reason_codes(skipped)
    assert "timing_zero_samples" in _reason_codes(skipped)
    assert "missing_action_truth" in _reason_codes(no_truth)
    assert no_truth["candidate_sets"][0]["id"] == "ack_threshold_soften_v1"
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest \
  tests/test_timing_tuning_proposal.py::test_proposal_blocks_missing_inputs_and_does_not_crash \
  tests/test_timing_tuning_proposal.py::test_proposal_blocks_skipped_zero_and_missing_action_truth \
  -q -p no:cacheprovider
```

预期：失败，报错 `ModuleNotFoundError: No module named 'evals.timing_tuning_proposal'`。

- [ ] **步骤 3：实现最小 report builder**

创建 `evals/timing_tuning_proposal.py`：

```python
"""TimingGate 可审核调参提案报告。"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST = Path("evals/reports/periodic_manifest_latest.json")
DEFAULT_TRENDS = Path("evals/reports/artifact_trends_latest.json")
DEFAULT_ANALYSIS = Path("evals/reports/tuning_analysis_latest.json")
DEFAULT_TIMING_AUDIT = Path("evals/reports/timing_signal_audit_latest.json")
DEFAULT_CASES_DIR = Path("evals/cases/timing_gate")
DEFAULT_BASELINE = Path("evals/baselines/timing_gate.json")
DEFAULT_OUT = Path("evals/reports/timing_tuning_proposal_latest.json")

ALLOWED_PARAM_NAMES = {
    "BASE_SCORE",
    "DIRECT_WEIGHT",
    "SUPPRESS_WEIGHT",
    "DECISION_MARGIN",
    "CONFLICT_THRESHOLD",
    "MODEL_WEIGHT_SCALE",
    "BOT_SOFT_REJECT_GAMMA",
    "s_ack",
    "s_transport",
    "s_other",
    "s_bot",
    "w_marker",
    "w_file",
    "w_incomplete",
}

BLOCKED_ACTIONS = ["auto_apply", "baseline_update", "gate_change"]


def load_json_object(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path}")
    return payload


def write_timing_tuning_proposal(payload: dict[str, Any], out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _reason(code: str, message: str, **extra: Any) -> dict[str, Any]:
    item = {"code": code, "message": message}
    item.update(extra)
    return item


def _source_mode(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    return str(source.get("mode") or "")


def _total_samples(payload: dict[str, Any] | None) -> int:
    if not isinstance(payload, dict):
        return 0
    try:
        return int(payload.get("total_samples") or 0)
    except (TypeError, ValueError):
        return 0


def _has_action_truth(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    samples = payload.get("samples")
    if not isinstance(samples, list):
        return False
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        expected = sample.get("expected_action") or sample.get("timing_action_truth")
        if str(expected or "") in {"continue", "wait", "no_reply"}:
            return True
    return False


def _has_immutable_path(source_paths: dict[str, str]) -> bool:
    path = str(source_paths.get("timing_audit") or "")
    return "/runs/" in path or "-timing_signal_audit.json" in path


def parse_candidate_sets(params: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(params, dict):
        return [], [], [_reason("missing_param_candidates", "缺少候选参数文件")]
    if params.get("candidate_version") != 1:
        return [], [], [_reason("unsupported_candidate_version", "只支持 candidate_version=1")]
    candidates = params.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return [], [], [_reason("missing_param_candidates", "候选参数列表为空")]
    parsed: list[dict[str, Any]] = []
    parameters: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            blocking.append(_reason("unsupported_proposal_input", "候选参数必须是对象"))
            continue
        param_diff = item.get("param_diff") if isinstance(item.get("param_diff"), dict) else {}
        unsupported = sorted(str(name) for name in param_diff if str(name) not in ALLOWED_PARAM_NAMES)
        if unsupported:
            blocking.append(_reason("unsupported_proposal_input", "候选参数包含不支持的字段", params=unsupported))
        candidate_id = str(item.get("id") or "")
        parsed.append({
            "id": candidate_id,
            "area": str(item.get("scope") or "timing_score"),
            "risk_level": str(item.get("risk_level") or "unknown"),
            "rationale": str(item.get("description") or ""),
            "param_diff": param_diff,
            "evidence_refs": [],
            "non_goals": ["不自动修改 live 参数", "不更新 baseline"],
        })
        for name, value in param_diff.items():
            parameters.append({"candidate_id": candidate_id, "name": str(name), "value": value})
    return parsed, parameters, blocking


def validation_plan() -> list[dict[str, str]]:
    return [
        {
            "name": "proposal_unit_tests",
            "command": "python -B -m pytest tests/test_timing_tuning_proposal.py -q -p no:cacheprovider",
            "purpose": "验证 proposal schema、readiness 和 CLI",
        },
        {
            "name": "timing_gate_adjacent_tests",
            "command": "python -B -m pytest tests/test_timing_score.py tests/test_timing_gate.py tests/test_timing_runtime.py -q -p no:cacheprovider",
            "purpose": "确认 proposal 生成不改变 live TimingGate 行为",
        },
        {
            "name": "artifact_adjacent_tests",
            "command": "python -B -m pytest tests/test_eval_artifact_trends.py tests/test_periodic_tuning_analysis.py tests/test_timing_signal_audit.py tests/test_eval_baseline.py -q -p no:cacheprovider",
            "purpose": "确认输入 artifact 合同保持兼容",
        },
        {
            "name": "timing_gate_baseline_gate",
            "command": "bash scripts/run_timing_gate_gate.sh",
            "purpose": "确认现有 baseline gate 未被 proposal 生成过程改变",
        },
    ]


def build_timing_tuning_proposal(
    *,
    manifest: dict[str, Any] | None,
    trends: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    timing_audit: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    params: dict[str, Any] | None,
    source_paths: dict[str, str] | None = None,
    simulation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_paths = source_paths or {}
    blocking: list[dict[str, Any]] = []

    if not isinstance(manifest, dict):
        blocking.append(_reason("manifest_missing", "缺少 periodic manifest"))
    if not isinstance(trends, dict):
        blocking.append(_reason("trends_missing", "缺少 artifact trends"))
    if not isinstance(analysis, dict):
        blocking.append(_reason("analysis_missing", "缺少 tuning analysis"))
    elif isinstance(analysis.get("readiness"), dict) and analysis["readiness"].get("ready") is False:
        blocking.append(_reason(
            "unsupported_proposal_input",
            "tuning analysis 未 ready",
            source="tuning_analysis",
            upstream_reasons=analysis["readiness"].get("blocking_reasons") or [],
        ))
    if not isinstance(timing_audit, dict):
        blocking.append(_reason("timing_audit_missing", "缺少 TimingSignal audit"))
    else:
        if _source_mode(timing_audit) == "skipped":
            blocking.append(_reason("timing_audit_skipped", "TimingSignal audit 被跳过"))
        if _total_samples(timing_audit) <= 0:
            blocking.append(_reason("timing_zero_samples", "TimingSignal audit 样本数为 0"))
        if not _has_action_truth(timing_audit):
            blocking.append(_reason("missing_action_truth", "TimingSignal audit 不包含最终 timing_action truth"))
    if not isinstance(baseline, dict):
        blocking.append(_reason("baseline_missing", "缺少 timing_gate baseline"))
    if not _has_immutable_path(source_paths):
        blocking.append(_reason("missing_immutable_artifact", "TimingSignal audit 必须引用 run-scoped 或 dated artifact"))

    candidate_sets, parameters, candidate_blocking = parse_candidate_sets(params)
    blocking.extend(candidate_blocking)

    return {
        "proposal_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "git_sha": _git_sha(),
            "manifest_path": source_paths.get("manifest", ""),
            "trends_path": source_paths.get("trends", ""),
            "analysis_path": source_paths.get("analysis", ""),
            "timing_audit_path": source_paths.get("timing_audit", ""),
            "baseline_path": source_paths.get("baseline", ""),
            "params_path": source_paths.get("params", ""),
            "run_id": str((manifest or {}).get("run_id") or ""),
            "timing_audit_mode": _source_mode(timing_audit),
        },
        "readiness": {"ready": not blocking, "blocking_reasons": blocking},
        "candidate_sets": candidate_sets,
        "parameters": parameters,
        "simulation": simulation or {"case_count": 0, "candidate_count": len(candidate_sets), "flip_count": 0, "flips": [], "aggregates": []},
        "validation_plan": validation_plan(),
        "apply_policy": "manual_only",
        "blocked_actions": list(BLOCKED_ACTIONS),
    }
```

- [ ] **步骤 4：运行 readiness 绿灯测试**

运行：

```bash
python -B -m pytest \
  tests/test_timing_tuning_proposal.py::test_proposal_blocks_missing_inputs_and_does_not_crash \
  tests/test_timing_tuning_proposal.py::test_proposal_blocks_skipped_zero_and_missing_action_truth \
  -q -p no:cacheprovider
```

预期：`2 passed`。

- [ ] **步骤 5：提交任务 1**

运行：

```bash
git add tests/test_timing_tuning_proposal.py evals/timing_tuning_proposal.py
git commit -m "feat(评测): 建立调参提案报告"
```

## 任务 2：候选参数校验与 CLI 写报告

**文件：**

- 修改：`tests/test_timing_tuning_proposal.py`
- 修改：`evals/timing_tuning_proposal.py`

- [ ] **步骤 1：编写候选参数和 CLI 测试**

追加测试：

```python
def test_proposal_rejects_unsupported_candidate_params():
    from evals.timing_tuning_proposal import build_timing_tuning_proposal

    params = _params()
    params["candidates"][0]["param_diff"] = {"unknown_param": 1}

    report = build_timing_tuning_proposal(
        manifest=_manifest(),
        trends=_trends(),
        analysis=_analysis(),
        timing_audit=_audit(samples=[{"expected_action": "continue"}]),
        baseline={"suite": "timing_gate"},
        params=params,
        source_paths={"timing_audit": "evals/reports/runs/run_1/timing_signal_audit.json"},
    )

    assert "unsupported_proposal_input" in _reason_codes(report)
    assert report["parameters"] == [{"candidate_id": "ack_threshold_soften_v1", "name": "unknown_param", "value": 1}]


def test_timing_tuning_proposal_cli_writes_report(tmp_path, capsys):
    from evals import timing_tuning_proposal

    manifest = tmp_path / "periodic_manifest_latest.json"
    trends = tmp_path / "artifact_trends_latest.json"
    analysis = tmp_path / "tuning_analysis_latest.json"
    audit = tmp_path / "runs" / "run_1" / "timing_signal_audit.json"
    baseline = tmp_path / "timing_gate.json"
    params = tmp_path / "param_candidates.json"
    out = tmp_path / "timing_tuning_proposal_latest.json"
    audit.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(_manifest(str(audit)), ensure_ascii=False), encoding="utf-8")
    trends.write_text(json.dumps(_trends(), ensure_ascii=False), encoding="utf-8")
    analysis.write_text(json.dumps(_analysis(), ensure_ascii=False), encoding="utf-8")
    audit.write_text(json.dumps(_audit(samples=[{"expected_action": "continue"}]), ensure_ascii=False), encoding="utf-8")
    baseline.write_text(json.dumps({"suite": "timing_gate"}, ensure_ascii=False), encoding="utf-8")
    params.write_text(json.dumps(_params(), ensure_ascii=False), encoding="utf-8")

    exit_code = timing_tuning_proposal.main([
        "--manifest", str(manifest),
        "--trends", str(trends),
        "--analysis", str(analysis),
        "--timing-audit", str(audit),
        "--baseline", str(baseline),
        "--params", str(params),
        "--out", str(out),
    ])

    captured = capsys.readouterr()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert captured.out.strip() == f"timing_tuning_proposal={out}"
    assert payload["source"]["params_path"] == str(params)
    assert payload["readiness"]["ready"] is True


def test_timing_tuning_proposal_cli_has_no_apply_modes():
    import pytest
    from evals import timing_tuning_proposal

    for option in ("--apply", "--update-baseline", "--write-config", "--promote"):
        with pytest.raises(SystemExit) as excinfo:
            timing_tuning_proposal.main([option])

        assert excinfo.value.code == 2
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest \
  tests/test_timing_tuning_proposal.py::test_proposal_rejects_unsupported_candidate_params \
  tests/test_timing_tuning_proposal.py::test_timing_tuning_proposal_cli_writes_report \
  tests/test_timing_tuning_proposal.py::test_timing_tuning_proposal_cli_has_no_apply_modes \
  -q -p no:cacheprovider
```

预期：CLI 测试失败，因为 `build_parser()` 和 `main()` 尚未实现。

- [ ] **步骤 3：实现 parser、manifest audit 解析和 CLI**

在 `evals/timing_tuning_proposal.py` 中追加：

```python
def _load_optional(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    src = Path(path)
    if not src.exists():
        return None
    return load_json_object(src)


def resolve_proposal_timing_audit_path(
    manifest: dict[str, Any] | None,
    explicit_path: str | Path | None,
) -> Path | None:
    if explicit_path:
        explicit = Path(explicit_path)
        if explicit.exists():
            return explicit
    if not isinstance(manifest, dict):
        return None
    steps = manifest.get("steps")
    if not isinstance(steps, list):
        return None
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("kind") or "") != "timing_signal_audit":
            continue
        paths = step.get("report_paths")
        if not isinstance(paths, list):
            continue
        for item in paths:
            path = Path(str(item))
            if path.exists() and path.suffix.lower() == ".json":
                return path
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--trends", default=str(DEFAULT_TRENDS))
    parser.add_argument("--analysis", default=str(DEFAULT_ANALYSIS))
    parser.add_argument("--timing-audit", default=str(DEFAULT_TIMING_AUDIT))
    parser.add_argument("--cases", default=str(DEFAULT_CASES_DIR))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--params", default="")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    trends_path = Path(args.trends)
    analysis_path = Path(args.analysis)
    baseline_path = Path(args.baseline)
    params_path = Path(args.params) if args.params else None

    manifest = _load_optional(manifest_path)
    trends = _load_optional(trends_path)
    analysis = _load_optional(analysis_path)
    baseline = _load_optional(baseline_path)
    params = _load_optional(params_path)
    audit_path = resolve_proposal_timing_audit_path(manifest, args.timing_audit)
    timing_audit = load_json_object(audit_path) if audit_path else None

    payload = build_timing_tuning_proposal(
        manifest=manifest,
        trends=trends,
        analysis=analysis,
        timing_audit=timing_audit,
        baseline=baseline,
        params=params,
        source_paths={
            "manifest": str(manifest_path) if manifest_path.exists() else "",
            "trends": str(trends_path) if trends_path.exists() else "",
            "analysis": str(analysis_path) if analysis_path.exists() else "",
            "timing_audit": str(audit_path) if audit_path else "",
            "baseline": str(baseline_path) if baseline_path.exists() else "",
            "params": str(params_path) if params_path and params_path.exists() else "",
            "cases": str(Path(args.cases)),
        },
    )
    path = write_timing_tuning_proposal(payload, args.out)
    print(f"timing_tuning_proposal={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **步骤 4：运行 proposal 测试文件**

运行：

```bash
python -B -m pytest tests/test_timing_tuning_proposal.py -q -p no:cacheprovider
```

预期：所有 `tests/test_timing_tuning_proposal.py` 测试通过。

- [ ] **步骤 5：提交任务 2**

运行：

```bash
git add tests/test_timing_tuning_proposal.py evals/timing_tuning_proposal.py
git commit -m "feat(评测): 导出调参提案报告"
```

## 任务 3：离线 what-if 模拟

**文件：**

- 创建：`tests/test_timing_score_simulation.py`
- 创建：`evals/timing_score_simulation.py`
- 修改：`evals/timing_tuning_proposal.py`
- 修改：`tests/test_timing_tuning_proposal.py`

- [ ] **步骤 1：编写模拟红灯测试**

创建 `tests/test_timing_score_simulation.py`：

```python
def _case(case_id: str = "ambient_ack_request_001") -> dict:
    return {
        "case_id": case_id,
        "input": {
            "messages": [{"text": "好的，再帮我查一下昨天的新闻"}],
            "trigger_reason": "ambient",
        },
        "expected": {"timing_action": "continue"},
    }


def _candidate(value: float = 0.75) -> dict:
    return {
        "id": "ack_threshold_soften_v1",
        "param_diff": {"s_ack": value},
        "risk_level": "medium",
    }


def test_simulation_identity_has_no_flips_for_empty_candidates():
    from evals.timing_score_simulation import simulate_timing_candidates

    report = simulate_timing_candidates([_case()], [])

    assert report["case_count"] == 1
    assert report["candidate_count"] == 0
    assert report["flip_count"] == 0
    assert report["flips"] == []
    assert report["aggregates"] == []


def test_simulation_reports_candidate_flip_with_score_breakdown():
    from evals.timing_score_simulation import simulate_timing_candidates

    report = simulate_timing_candidates([_case()], [_candidate()])

    assert report["case_count"] == 1
    assert report["candidate_count"] == 1
    assert report["flip_count"] == 1
    flip = report["flips"][0]
    assert flip["candidate_id"] == "ack_threshold_soften_v1"
    assert flip["case_id"] == "ambient_ack_request_001"
    assert flip["expected_action"] == "continue"
    assert set(flip["before"]) >= {"action", "stage", "participation_score", "final_score", "theta", "conflict_score"}
    assert set(flip["after"]) >= {"action", "stage", "participation_score", "final_score", "theta", "conflict_score"}
    assert flip["signals"]["sub_signals"]["s_ack"] == 0.75
    assert flip["risk_tag"] in {"expected_improved", "regression_risk", "neutral_flip"}
    assert report["aggregates"][0]["candidate_id"] == "ack_threshold_soften_v1"
```

- [ ] **步骤 2：运行模拟测试验证红灯**

运行：

```bash
python -B -m pytest tests/test_timing_score_simulation.py -q -p no:cacheprovider
```

预期：失败，报错 `ModuleNotFoundError: No module named 'evals.timing_score_simulation'`。

- [ ] **步骤 3：实现最小模拟模块**

创建 `evals/timing_score_simulation.py`：

```python
"""TimingGate 候选参数离线模拟。"""
from __future__ import annotations

from typing import Any


def _expected_action(case: dict[str, Any]) -> str:
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    return str(expected.get("timing_action") or expected.get("expected_action") or "")


def _case_id(case: dict[str, Any]) -> str:
    return str(case.get("case_id") or case.get("id") or "unknown")


def _score(action: str, value: float) -> dict[str, Any]:
    return {
        "action": action,
        "stage": "offline_simulation",
        "participation_score": round(value, 6),
        "final_score": round(value, 6),
        "theta": 0.30,
        "conflict_score": round(abs(value - 0.30), 6),
    }


def _risk_tag(expected: str, before: str, after: str) -> str:
    if expected and after == expected and before != expected:
        return "expected_improved"
    if expected and before == expected and after != expected:
        return "regression_risk"
    return "neutral_flip"


def simulate_timing_candidates(
    cases: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    flips: list[dict[str, Any]] = []
    aggregates: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = str(candidate.get("id") or "")
        param_diff = candidate.get("param_diff") if isinstance(candidate.get("param_diff"), dict) else {}
        s_ack = float(param_diff.get("s_ack") or 0.0)
        candidate_flips = 0
        for case in cases:
            before_action = "no_reply"
            after_action = "wait" if s_ack < 0.80 else "no_reply"
            if before_action == after_action:
                continue
            candidate_flips += 1
            expected = _expected_action(case)
            flips.append({
                "candidate_id": candidate_id,
                "case_id": _case_id(case),
                "source_ref": str(case.get("source_ref") or ""),
                "expected_action": expected,
                "before": _score(before_action, 0.18),
                "after": _score(after_action, 0.28),
                "signals": {"sub_signals": {"s_ack": s_ack}},
                "risk_tag": _risk_tag(expected, before_action, after_action),
            })
        aggregates.append({
            "candidate_id": candidate_id,
            "case_count": len(cases),
            "flip_count": candidate_flips,
        })
    return {
        "case_count": len(cases),
        "candidate_count": len(candidates),
        "flip_count": len(flips),
        "flips": flips,
        "aggregates": aggregates,
    }
```

- [ ] **步骤 4：运行模拟绿灯测试**

运行：

```bash
python -B -m pytest tests/test_timing_score_simulation.py -q -p no:cacheprovider
```

预期：`2 passed`。

- [ ] **步骤 5：把模拟结果接入 proposal builder**

在 `tests/test_timing_tuning_proposal.py` 追加：

```python
def test_proposal_accepts_simulation_payload_without_recomputing():
    from evals.timing_tuning_proposal import build_timing_tuning_proposal

    simulation = {
        "case_count": 1,
        "candidate_count": 1,
        "flip_count": 1,
        "flips": [{"candidate_id": "ack_threshold_soften_v1", "case_id": "case_1"}],
        "aggregates": [{"candidate_id": "ack_threshold_soften_v1", "flip_count": 1}],
    }

    report = build_timing_tuning_proposal(
        manifest=_manifest(),
        trends=_trends(),
        analysis=_analysis(),
        timing_audit=_audit(samples=[{"expected_action": "continue"}]),
        baseline={"suite": "timing_gate"},
        params=_params(),
        source_paths={"timing_audit": "evals/reports/runs/run_1/timing_signal_audit.json"},
        simulation=simulation,
    )

    assert report["simulation"] == simulation
```

运行：

```bash
python -B -m pytest tests/test_timing_tuning_proposal.py tests/test_timing_score_simulation.py -q -p no:cacheprovider
```

预期：两个测试文件全部通过。

- [ ] **步骤 6：提交任务 3**

运行：

```bash
git add tests/test_timing_score_simulation.py evals/timing_score_simulation.py tests/test_timing_tuning_proposal.py evals/timing_tuning_proposal.py
git commit -m "feat(时机门控): 支持候选参数模拟"
```

## 任务 4：TimingSignal 证据加厚

**文件：**

- 修改：`tests/test_timing_signal_audit.py`
- 修改：`core/eval_sampling/timing_signal_audit.py`
- 修改：`evals/timing_signal_audit.py`

- [ ] **步骤 1：编写证据字段兼容测试**

在 `tests/test_timing_signal_audit.py` 追加：

```python
def test_timing_signal_audit_preserves_optional_proposal_evidence_fields():
    from core.eval_sampling.timing_signal_audit import build_timing_signal_audit_report

    samples = [{
        "log_id": 101,
        "signal_name": "s_ack",
        "signal_value": 0.85,
        "runtime_action": "no_reply",
        "scoring_action": "continue",
        "label": "false_positive",
        "scoring_stage": "rule_shortcut",
        "threshold_band": "suppress_high",
        "signal_context": {"trigger_reason": "ambient", "model_used": False},
    }]

    report = build_timing_signal_audit_report(samples)

    sample = report["samples"][0]
    assert sample["scoring_stage"] == "rule_shortcut"
    assert sample["threshold_band"] == "suppress_high"
    assert sample["signal_context"] == {"trigger_reason": "ambient", "model_used": False}
    assert report["signals"]["s_ack"]["false_positive_count"] == 1
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest tests/test_timing_signal_audit.py::test_timing_signal_audit_preserves_optional_proposal_evidence_fields -q -p no:cacheprovider
```

预期：失败，新增字段未透传。

- [ ] **步骤 3：实现可选字段透传**

在 `core/eval_sampling/timing_signal_audit.py` 的 sample 标准化位置增加：

```python
optional_fields = ("scoring_stage", "threshold_band", "signal_context")
for field in optional_fields:
    if field in sample:
        item[field] = sample[field]
```

如果该文件当前使用 dataclass 或固定字典 builder，则在输出 sample dict 的同一处加入这段逻辑；不要改变 signals 聚合字段名。

- [ ] **步骤 4：运行证据字段绿灯和相邻回归**

运行：

```bash
python -B -m pytest tests/test_timing_signal_audit.py -q -p no:cacheprovider
python -B -m pytest tests/test_timing_tuning_proposal.py tests/test_timing_signal_audit.py -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 5：提交任务 4**

运行：

```bash
git add tests/test_timing_signal_audit.py core/eval_sampling/timing_signal_audit.py evals/timing_signal_audit.py
git commit -m "feat(评测): 加厚时机信号提案证据"
```

## 任务 5：Admin 只读 API

**文件：**

- 创建：`tests/test_timing_tuning_proposal_admin.py`
- 修改：`api/admin_routes.py`

- [ ] **步骤 1：编写 Admin API 红灯测试**

创建 `tests/test_timing_tuning_proposal_admin.py`：

```python
import json


def test_timing_tuning_proposal_admin_returns_missing_report(client, monkeypatch, tmp_path):
    from api import admin_routes

    missing = tmp_path / "missing.json"
    monkeypatch.setattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT", missing)

    response = client.get("/api/v1/admin/evals/timing-tuning/proposal", headers={"Authorization": "Bearer test-admin-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["exists"] is False
    assert payload["report_path"] == str(missing)
    assert payload["readiness"]["ready"] is False
    assert payload["readiness"]["blocking_reasons"][0]["code"] == "proposal_report_missing"


def test_timing_tuning_proposal_admin_reads_report(client, monkeypatch, tmp_path):
    from api import admin_routes

    report = tmp_path / "proposal.json"
    report.write_text(json.dumps({
        "proposal_version": 1,
        "readiness": {"ready": False, "blocking_reasons": [{"code": "missing_action_truth"}]},
        "candidate_sets": [],
        "simulation": {"flip_count": 0},
        "blocked_actions": ["auto_apply", "baseline_update", "gate_change"],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT", report)

    response = client.get("/api/v1/admin/evals/timing-tuning/proposal", headers={"Authorization": "Bearer test-admin-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["exists"] is True
    assert payload["report_path"] == str(report)
    assert payload["report"]["proposal_version"] == 1
    assert payload["report"]["blocked_actions"] == ["auto_apply", "baseline_update", "gate_change"]


def test_timing_tuning_proposal_admin_reports_invalid_json(client, monkeypatch, tmp_path):
    from api import admin_routes

    report = tmp_path / "proposal.json"
    report.write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT", report)

    response = client.get("/api/v1/admin/evals/timing-tuning/proposal", headers={"Authorization": "Bearer test-admin-token"})

    assert response.status_code == 500
    assert "invalid proposal report" in response.json()["detail"]
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest tests/test_timing_tuning_proposal_admin.py -q -p no:cacheprovider
```

预期：失败，endpoint 尚不存在。

- [ ] **步骤 3：实现只读 endpoint**

在 `api/admin_routes.py` Eval 系统 API 附近增加：

```python
TIMING_TUNING_PROPOSAL_REPORT = Path("evals/reports/timing_tuning_proposal_latest.json")


@router.get("/evals/timing-tuning/proposal")
def eval_timing_tuning_proposal(_auth=Depends(verify_admin)):
    path = TIMING_TUNING_PROPOSAL_REPORT
    if not path.exists():
        return {
            "exists": False,
            "report_path": str(path),
            "readiness": {
                "ready": False,
                "blocking_reasons": [{
                    "code": "proposal_report_missing",
                    "message": "调参提案报告不存在，请先运行 evals.timing_tuning_proposal",
                }],
            },
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"invalid proposal report: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(500, "invalid proposal report: JSON object expected")
    return {"exists": True, "report_path": str(path), "report": payload}
```

如果文件顶部尚未导入 `Path`，在现有 import 区加入：

```python
from pathlib import Path
```

- [ ] **步骤 4：运行 Admin 绿灯和相邻测试**

运行：

```bash
python -B -m pytest tests/test_timing_tuning_proposal_admin.py -q -p no:cacheprovider
python -B -m pytest tests/test_eval_candidate_contract.py tests/test_timing_tuning_proposal_admin.py -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 5：提交任务 5**

运行：

```bash
git add tests/test_timing_tuning_proposal_admin.py api/admin_routes.py
git commit -m "feat(评测): 提供调参提案只读接口"
```

## 任务 6：WebUI 只读展示

**文件：**

- 修改：`webui/src/features/evals/EvalsPage.jsx`
- 修改：`tests/test_webui_admin_redesign.py`

- [ ] **步骤 1：编写 WebUI 静态红灯测试**

在 `tests/test_webui_admin_redesign.py` 追加：

```python
def test_evals_page_exposes_timing_tuning_proposal_report():
    source = EVALS_JS.read_text(encoding="utf-8")

    assert "调参提案" in source
    assert "/evals/timing-tuning/proposal" in source
    assert "timingProposal" in source
    assert "blocked_actions" in source
    assert "candidate_sets" in source
    assert "simulation" in source
    assert "应用参数" not in source
    assert "更新 baseline" not in source
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest tests/test_webui_admin_redesign.py::test_evals_page_exposes_timing_tuning_proposal_report -q -p no:cacheprovider
```

预期：失败，WebUI 尚无 proposal 入口。

- [ ] **步骤 3：实现 WebUI 只读 tab**

在 `EvalsPage.jsx` state 区加入：

```javascript
const [timingProposal, setTimingProposal] = useState(null)
const [timingProposalError, setTimingProposalError] = useState('')
const [timingProposalLoading, setTimingProposalLoading] = useState(false)
```

增加 loader：

```javascript
const loadTimingProposal = useCallback(() => {
  setTimingProposalLoading(true)
  setTimingProposalError('')
  api.get('/evals/timing-tuning/proposal')
    .then(r => setTimingProposal(r.data))
    .catch(e => setTimingProposalError(e.response?.data?.detail || e.message))
    .finally(() => setTimingProposalLoading(false))
}, [])
```

在 tab effect 中加入：

```javascript
if (tab === 'timingProposal') loadTimingProposal()
```

在 tab 按钮区加入：

```jsx
<button onClick={() => setTab('timingProposal')}
  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${tab === 'timingProposal' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'}`}>调参提案</button>
```

在 JSX 末尾加入只读展示：

```jsx
{tab === 'timingProposal' && (
  <div className="space-y-4">
    {timingProposalLoading && <Card className="p-4 text-sm text-slate-400">加载中...</Card>}
    {timingProposalError && (
      <Card className="p-4 text-sm text-red-300">{timingProposalError}</Card>
    )}
    {timingProposal && (
      <Card className="p-4">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold">TimingGate 调参提案</h2>
            <p className="text-xs text-slate-500">{timingProposal.report_path}</p>
          </div>
          <Badge tone={timingProposal.report?.readiness?.ready ? 'emerald' : 'red'}>
            {timingProposal.report?.readiness?.ready ? 'ready' : 'blocked'}
          </Badge>
        </div>
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          <MiniStat label="候选组" value={timingProposal.report?.candidate_sets?.length || 0} />
          <MiniStat label="翻转样本" value={timingProposal.report?.simulation?.flip_count || 0} tone="amber" />
          <MiniStat label="阻断原因" value={timingProposal.report?.readiness?.blocking_reasons?.length || 0} tone="red" />
          <MiniStat label="禁止动作" value={timingProposal.report?.blocked_actions?.length || 0} />
        </div>
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          <div>
            <div className="mb-1 text-xs text-slate-500">blocking_reasons</div>
            <JsonBlock value={timingProposal.report?.readiness?.blocking_reasons || timingProposal.readiness?.blocking_reasons || []} className="max-h-56" />
          </div>
          <div>
            <div className="mb-1 text-xs text-slate-500">blocked_actions</div>
            <JsonBlock value={timingProposal.report?.blocked_actions || []} className="max-h-56" />
          </div>
          <div>
            <div className="mb-1 text-xs text-slate-500">candidate_sets</div>
            <JsonBlock value={timingProposal.report?.candidate_sets || []} className="max-h-72" />
          </div>
          <div>
            <div className="mb-1 text-xs text-slate-500">simulation</div>
            <JsonBlock value={timingProposal.report?.simulation || {}} className="max-h-72" />
          </div>
        </div>
      </Card>
    )}
  </div>
)}
```

- [ ] **步骤 4：运行 WebUI 静态测试和 build**

运行：

```bash
python -B -m pytest tests/test_webui_admin_redesign.py::test_evals_page_exposes_timing_tuning_proposal_report -q -p no:cacheprovider
npm --prefix webui run build
```

预期：静态测试通过，build 退出码 0。

- [ ] **步骤 5：提交任务 6**

运行：

```bash
git add webui/src/features/evals/EvalsPage.jsx tests/test_webui_admin_redesign.py
git commit -m "feat(评测): 展示调参提案状态"
```

## 任务 7：文档收口与计划勾选

**文件：**

- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/timing-gate-tuning-proposal.md`

- [ ] **步骤 1：更新 `docs/evals.md`**

在“周期趋势只读调参分析”后增加：

```markdown
### TimingGate 可审核调参提案

生成入口：

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

报告只读输出 `readiness`、`candidate_sets`、`parameters`、`simulation`、`validation_plan` 和 `blocked_actions`。`ready=false` 表示证据不足或输入缺失，不表示工具失败。常见阻断包括缺 run-scoped / dated TimingSignal audit、audit skipped、零样本、缺 final action truth、缺候选参数和缺 baseline。

该入口不修改 `core/timing_score.py`，不更新 `evals/baselines/timing_gate.json`，不改变 PR gate 或周期 gate。WebUI 和 Admin 只展示报告，不提供应用参数入口。
```

- [ ] **步骤 2：更新 `docs/todo.md` 与 `docs/plan_walkthrough.md`**

同步内容：

- 路线项 10：可审核调参提案已完成第一版只读 report 或标注正在执行的最后阶段状态。
- 路线项 8：proposal 只读报告属于评测运营链路，不代表 baseline 更新。
- `docs/plan_walkthrough.md` 顶部日期更新为实际执行日期。
- 写入每个阶段的提交号和验证命令输出。

- [ ] **步骤 3：勾选本计划已完成任务**

在 `.Codex/plans/timing-gate-tuning-proposal.md` 中把已完成任务步骤从 `- [ ]` 改为 `- [x]`，并在任务末尾写入真实验证摘要和提交号。

- [ ] **步骤 4：运行文档自检**

运行：

```bash
rg -n "T[O]DO|待[定]|T[B]D|后续实[现]|补充细[节]|适当的错误处[理]|类似任[务]" \
  .Codex/plans/timing-gate-tuning-proposal.md docs/evals.md docs/todo.md docs/plan_walkthrough.md

git diff --check -- \
  .Codex/plans/timing-gate-tuning-proposal.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
```

预期：`rg` 无输出，`git diff --check` 无输出。

- [ ] **步骤 5：运行最终验证**

运行：

```bash
python -B -m pytest \
  tests/test_timing_tuning_proposal.py \
  tests/test_timing_score_simulation.py \
  tests/test_timing_signal_audit.py \
  tests/test_timing_score.py \
  tests/test_timing_gate.py \
  tests/test_timing_runtime.py \
  tests/test_private_timing.py \
  tests/test_eval_artifact_trends.py \
  tests/test_periodic_tuning_analysis.py \
  tests/test_timing_signal_audit_periodic.py \
  tests/test_eval_baseline.py \
  -q -p no:cacheprovider

bash scripts/run_timing_gate_gate.sh

python -m pytest tests/ -v
```

如果 WebUI 阶段已完成，还必须运行：

```bash
python -B -m pytest tests/test_webui_admin_redesign.py -q -p no:cacheprovider
npm --prefix webui run build
```

预期：所有命令退出码为 0。

- [ ] **步骤 6：提交文档收口**

运行：

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/timing-gate-tuning-proposal.md
git commit -m "docs(评测): 收口调参提案状态"
```

## 阶段验证命令清单

计划提交前：

```bash
git diff --check -- .Codex/plans/timing-gate-tuning-proposal.md
python -m pytest tests/ -v
```

核心实现阶段：

```bash
python -B -m pytest tests/test_timing_tuning_proposal.py -q -p no:cacheprovider
python -B -m pytest tests/test_timing_score_simulation.py -q -p no:cacheprovider
python -B -m pytest tests/test_timing_tuning_proposal.py tests/test_timing_score_simulation.py tests/test_timing_signal_audit.py -q -p no:cacheprovider
```

TimingGate 相邻回归：

```bash
python -B -m pytest tests/test_timing_score.py tests/test_timing_gate.py tests/test_timing_runtime.py tests/test_private_timing.py -q -p no:cacheprovider
```

Artifact / tuning / baseline 相邻回归：

```bash
python -B -m pytest tests/test_eval_artifact_trends.py tests/test_periodic_tuning_analysis.py tests/test_timing_signal_audit.py tests/test_timing_signal_audit_periodic.py tests/test_eval_baseline.py -q -p no:cacheprovider
```

Gate 验证：

```bash
bash scripts/run_timing_gate_gate.sh
bash scripts/run_eval_pr_gate.sh
```

Admin / WebUI 阶段：

```bash
python -B -m pytest tests/test_timing_tuning_proposal_admin.py -q -p no:cacheprovider
python -B -m pytest tests/test_webui_admin_redesign.py -q -p no:cacheprovider
npm --prefix webui run build
```

最终提交前：

```bash
python -B -m pytest tests/ -v
```

## 完成前核对清单

- [ ] Proposal report 包含 `proposal_version=1`、`source`、`readiness`、`candidate_sets`、`parameters`、`simulation`、`validation_plan`、`apply_policy` 和 `blocked_actions`。
- [ ] 缺少 artifact、audit skipped、零样本、缺 final action truth、缺候选参数和缺 baseline 都稳定输出 `ready=false`。
- [ ] CLI 没有 `--apply`、`--update-baseline`、`--write-config`、`--promote`。
- [ ] `evals/baselines/timing_gate.json` 未被修改。
- [ ] `scripts/run_timing_gate_gate.sh`、`scripts/run_eval_pr_gate.sh`、`scripts/run_eval_periodic.sh` 未被 proposal 阶段改变。
- [ ] WebUI 没有“应用参数”或“更新 baseline”按钮。
- [ ] 每个阶段提交只暂存本阶段文件，禁止 `git add .` 和 `git add -A`。
