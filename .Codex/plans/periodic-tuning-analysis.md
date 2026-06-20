# 周期趋势只读调参分析实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新增只读调参分析报告，把周期趋势、TimingSignal raw audit 和可选 manifest 转成可复核建议，不自动调整参数。

**架构：** 新增 `evals/tuning_analysis.py`，只读取 JSON artifact，不访问生产 DB，不运行评测任务，不修改 baseline 或 gate。模块分为 JSON 输入、manifest 路径解析、纯函数分析、报告写入和 CLI 五层；测试以构造内存字典和临时 JSON 文件覆盖 readiness、信号证据、趋势建议、CLI 输出和不可自动 apply 边界。

**技术栈：** Python 标准库、pytest、JSON 文件、现有 `evals.artifact_trends` 输出合同、现有 `timing_signal_audit` 报告合同。

---

## 文件结构

- 创建：`evals/tuning_analysis.py`
  - 负责读取 JSON 对象、从 manifest 解析 TimingSignal audit 报告路径、构建分析报告、写文件和 CLI。
  - 不依赖数据库、不调用 `evals.run`、不修改任何输入 artifact。
- 创建：`tests/test_periodic_tuning_analysis.py`
  - 覆盖纯函数 readiness、signal evidence、趋势建议、`regression_refs` 和 CLI。
- 修改：`docs/evals.md`
  - 增加“周期趋势只读调参分析”说明和 CLI 示例。
  - 强调 `candidate_adjustment` 不是可自动应用的参数变更。
- 修改：`docs/todo.md`
  - 更新路线项 8 / 10 运营状态，标记只读调参分析完成后仍不代表已调参。
- 修改：`docs/plan_walkthrough.md`
  - 追加本阶段执行记录、验证结果和下一阶段边界。

## 子 agent 分工建议

本阶段文件边界清晰，可以并行读码和草拟，但最终写入由主线程合并：

- 子 agent A：只读 `tests/test_eval_artifact_trends.py`、`tests/test_timing_signal_audit.py` 和设计文档，产出测试样例建议；禁止修改文件。
- 子 agent B：只读 `evals/artifact_trends.py`、`evals/periodic_manifest.py` 和 `evals/timing_signal_audit.py`，产出输入合同和路径解析注意事项；禁止修改文件。
- 子 agent C：只读 `docs/evals.md`、`docs/todo.md`、`docs/plan_walkthrough.md`，产出文档更新清单；禁止修改文件。

主线程负责实现、验证、提交。不要让多个 agent 同时写 `evals/tuning_analysis.py` 或同一个测试文件。

## 数据合同

### 输入趋势报告

```json
{
  "trend_version": 1,
  "source": {
    "run_count": 3,
    "deduped_run_ids": ["run_1", "run_2", "run_3"]
  },
  "summary": {
    "latest_run_id": "run_3",
    "previous_run_id": "run_2"
  },
  "series": {
    "eval_suites": {},
    "rag_benchmark": [],
    "timing_signal_audit": []
  },
  "regressions": []
}
```

### 输入 raw audit

```json
{
  "total_samples": 20,
  "labeled_samples": 10,
  "signals": {
    "s_ack": {
      "samples": 10,
      "labeled_samples": 6,
      "false_positive_count": 2,
      "true_positive_count": 4,
      "unknown_count": 4,
      "false_positive_rate": 0.333333,
      "actions": {"no_reply": 8, "reply_now": 2},
      "suggestion": "review_threshold"
    }
  },
  "shadow": {
    "total_samples": 20,
    "action_mismatch_count": 3,
    "action_mismatch_rate": 0.15,
    "mismatches_by_signal": {"s_ack": 2}
  },
  "samples": []
}
```

### 输出分析报告

```json
{
  "analysis_version": 1,
  "generated_at": "2026-06-20T21:00:00",
  "source": {},
  "readiness": {
    "ready": false,
    "blocking_reasons": []
  },
  "summary": {
    "recommendation_count": 0,
    "must_review_count": 0,
    "no_change_count": 0,
    "label_more_samples_count": 0,
    "collect_more_artifact_count": 0
  },
  "signals": [],
  "recommendations": [],
  "regression_refs": []
}
```

## 任务 1：建立核心报告骨架和 readiness

**文件：**

- 创建：`tests/test_periodic_tuning_analysis.py`
- 创建：`evals/tuning_analysis.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_periodic_tuning_analysis.py` 中新增基础 helper 和 3 个红灯测试：

```python
import json


def _trends(
    *,
    trend_version: int = 1,
    run_count: int = 3,
    latest_run_id: str = "run_3",
    previous_run_id: str = "run_2",
    timing_items: list[dict] | None = None,
    rag_items: list[dict] | None = None,
    eval_suites: dict[str, list[dict]] | None = None,
    regressions: list[dict] | None = None,
) -> dict:
    return {
        "trend_version": trend_version,
        "source": {
            "run_count": run_count,
            "deduped_run_ids": [f"run_{index}" for index in range(1, run_count + 1)],
        },
        "summary": {
            "latest_run_id": latest_run_id,
            "previous_run_id": previous_run_id,
            "latest_status": "passed",
        },
        "series": {
            "runs": [],
            "eval_suites": eval_suites or {},
            "rag_benchmark": rag_items or [],
            "timing_signal_audit": timing_items or [],
        },
        "regressions": regressions or [],
    }


def _audit(
    *,
    total_samples: int = 20,
    labeled_samples: int = 10,
    signals: dict | None = None,
    samples: list[dict] | None = None,
    source: dict | None = None,
) -> dict:
    return {
        "total_samples": total_samples,
        "labeled_samples": labeled_samples,
        "signals": signals or {},
        "shadow": {
            "total_samples": total_samples,
            "action_mismatch_count": 0,
            "action_mismatch_rate": 0.0,
            "mismatches_by_signal": {},
        },
        "samples": samples or [],
        "source": source or {"db": "data/nanobot.db"},
    }


def _reason_codes(report: dict) -> set[str]:
    return {
        item["code"]
        for item in report["readiness"]["blocking_reasons"]
    }


def _recommendation_codes(report: dict) -> set[str]:
    return {
        item["reason_code"]
        for item in report["recommendations"]
    }


def test_tuning_analysis_blocks_unsupported_trend_version():
    from evals.tuning_analysis import build_tuning_analysis

    report = build_tuning_analysis(_trends(trend_version=2), timing_audit=_audit())

    assert report["analysis_version"] == 1
    assert report["readiness"]["ready"] is False
    assert "unsupported_trend_version" in _reason_codes(report)
    assert "unsupported_trend_version" in _recommendation_codes(report)
    assert all(item["type"] != "candidate_adjustment" for item in report["recommendations"])


def test_tuning_analysis_blocks_insufficient_runs():
    from evals.tuning_analysis import build_tuning_analysis

    report = build_tuning_analysis(_trends(run_count=2), timing_audit=_audit())

    assert report["readiness"]["ready"] is False
    assert "insufficient_runs" in _reason_codes(report)
    recommendation = report["recommendations"][0]
    assert recommendation["type"] == "collect_more_artifact"
    assert recommendation["area"] == "artifact_health"
    assert recommendation["evidence"]["run_count"] == 2


def test_tuning_analysis_blocks_missing_skipped_and_zero_timing_audit():
    from evals.tuning_analysis import build_tuning_analysis

    missing = build_tuning_analysis(_trends(), timing_audit=None)
    skipped = build_tuning_analysis(
        _trends(),
        timing_audit=_audit(
            total_samples=0,
            labeled_samples=0,
            source={"mode": "skipped", "reason": "db_not_found"},
        ),
    )
    zero = build_tuning_analysis(_trends(), timing_audit=_audit(total_samples=0, labeled_samples=0))

    assert "timing_audit_missing" in _reason_codes(missing)
    assert "timing_audit_skipped" in _reason_codes(skipped)
    assert "timing_zero_samples" in _reason_codes(zero)
    assert "db_not_found" in skipped["source"]["timing_audit_reason"]
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest tests/test_periodic_tuning_analysis.py::test_tuning_analysis_blocks_unsupported_trend_version tests/test_periodic_tuning_analysis.py::test_tuning_analysis_blocks_insufficient_runs tests/test_periodic_tuning_analysis.py::test_tuning_analysis_blocks_missing_skipped_and_zero_timing_audit -q -p no:cacheprovider
```

预期：失败，报错 `ModuleNotFoundError: No module named 'evals.tuning_analysis'`。

- [ ] **步骤 3：编写最小实现**

创建 `evals/tuning_analysis.py`：

```python
"""周期趋势只读调参分析工具。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_TRENDS = Path("evals/reports/artifact_trends_latest.json")
DEFAULT_TIMING_AUDIT = Path("evals/reports/timing_signal_audit_latest.json")
DEFAULT_MANIFEST = Path("evals/reports/periodic_manifest_latest.json")
DEFAULT_OUT = Path("evals/reports/tuning_analysis_latest.json")


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _reason(code: str, message: str, **extra: Any) -> dict[str, Any]:
    item = {"code": code, "message": message}
    item.update(extra)
    return item


def _recommendation(
    type_: str,
    area: str,
    severity: str,
    reason_code: str,
    message: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": type_,
        "area": area,
        "severity": severity,
        "reason_code": reason_code,
        "message": message,
        "evidence": evidence or {},
    }


def _trend_source(trends: dict[str, Any]) -> dict[str, Any]:
    source = trends.get("source") if isinstance(trends.get("source"), dict) else {}
    summary = trends.get("summary") if isinstance(trends.get("summary"), dict) else {}
    return {
        "trend_version": trends.get("trend_version"),
        "run_count": _as_int(source.get("run_count")),
        "latest_run_id": summary.get("latest_run_id"),
        "previous_run_id": summary.get("previous_run_id"),
    }


def _audit_source(timing_audit: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(timing_audit, dict):
        return {"timing_audit_mode": "", "timing_audit_reason": ""}
    source = timing_audit.get("source") if isinstance(timing_audit.get("source"), dict) else {}
    return {
        "timing_audit_mode": str(source.get("mode") or ""),
        "timing_audit_reason": str(source.get("reason") or ""),
    }


def _summarize(recommendations: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "recommendation_count": len(recommendations),
        "must_review_count": sum(1 for item in recommendations if item.get("type") == "manual_review"),
        "no_change_count": sum(1 for item in recommendations if item.get("type") == "no_change"),
        "label_more_samples_count": sum(1 for item in recommendations if item.get("type") == "label_more_samples"),
        "collect_more_artifact_count": sum(1 for item in recommendations if item.get("type") == "collect_more_artifact"),
    }


def build_tuning_analysis(
    trends: dict[str, Any],
    *,
    timing_audit: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    source_paths: dict[str, str] | None = None,
    min_runs: int = 3,
    min_total_samples: int = 20,
    min_label_coverage: float = 0.30,
    min_signal_labeled_samples: int = 5,
    high_false_positive_rate: float = 0.20,
) -> dict[str, Any]:
    source_paths = source_paths or {}
    source = {
        **_trend_source(trends),
        **_audit_source(timing_audit),
        "trends_path": source_paths.get("trends", ""),
        "timing_audit_path": source_paths.get("timing_audit", ""),
        "manifest_path": source_paths.get("manifest", ""),
    }
    blocking: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []

    if trends.get("trend_version") != 1:
        blocking.append(_reason("unsupported_trend_version", "只支持 trend_version=1 的趋势报告"))
        recommendations.append(_recommendation(
            "collect_more_artifact",
            "artifact_health",
            "high",
            "unsupported_trend_version",
            "趋势报告版本不受支持，需要重新生成 artifact trends",
            {"trend_version": trends.get("trend_version")},
        ))

    run_count = _as_int(source.get("run_count"))
    if run_count < min_runs:
        blocking.append(_reason("insufficient_runs", f"至少需要 {min_runs} 个周期 run 才生成调参候选建议"))
        recommendations.append(_recommendation(
            "collect_more_artifact",
            "artifact_health",
            "medium",
            "insufficient_runs",
            f"当前只有 {run_count} 个周期 run，先积累更多周期趋势",
            {"run_count": run_count, "min_runs": min_runs},
        ))

    if not isinstance(timing_audit, dict):
        blocking.append(_reason("timing_audit_missing", "缺少 TimingSignal audit 报告"))
        recommendations.append(_recommendation(
            "collect_more_artifact",
            "timing_signal",
            "medium",
            "timing_audit_missing",
            "缺少 TimingSignal audit 报告，不能分析信号假阳率",
        ))
    else:
        audit_source = timing_audit.get("source") if isinstance(timing_audit.get("source"), dict) else {}
        if audit_source.get("mode") == "skipped":
            blocking.append(_reason("timing_audit_skipped", "TimingSignal audit 被跳过", reason=audit_source.get("reason")))
            recommendations.append(_recommendation(
                "collect_more_artifact",
                "timing_signal",
                "medium",
                "timing_audit_skipped",
                "本轮 TimingSignal audit 被跳过，需要可审计的真实样本报告",
                {"reason": audit_source.get("reason")},
            ))
        total_samples = _as_int(timing_audit.get("total_samples"))
        if total_samples <= 0:
            blocking.append(_reason("timing_zero_samples", "TimingSignal audit 样本数为 0"))
            recommendations.append(_recommendation(
                "collect_more_artifact",
                "timing_signal",
                "medium",
                "timing_zero_samples",
                "TimingSignal audit 没有样本，不能推断信号质量",
                {"total_samples": total_samples},
            ))

    ready = not blocking
    return {
        "analysis_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "readiness": {"ready": ready, "blocking_reasons": blocking},
        "summary": _summarize(recommendations),
        "signals": [],
        "recommendations": recommendations,
        "regression_refs": [],
    }
```

- [ ] **步骤 4：运行测试验证绿灯**

运行：

```bash
python -B -m pytest tests/test_periodic_tuning_analysis.py::test_tuning_analysis_blocks_unsupported_trend_version tests/test_periodic_tuning_analysis.py::test_tuning_analysis_blocks_insufficient_runs tests/test_periodic_tuning_analysis.py::test_tuning_analysis_blocks_missing_skipped_and_zero_timing_audit -q -p no:cacheprovider
```

预期：`3 passed`。

- [ ] **步骤 5：Commit**

运行：

```bash
git add tests/test_periodic_tuning_analysis.py evals/tuning_analysis.py
git commit -m "feat(评测): 建立调参分析骨架"
```

## 任务 2：TimingSignal 标注覆盖、信号证据和假阳复核建议

**文件：**

- 修改：`tests/test_periodic_tuning_analysis.py`
- 修改：`evals/tuning_analysis.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_periodic_tuning_analysis.py` 追加：

```python
def test_tuning_analysis_recommends_labeling_for_low_label_coverage():
    from evals.tuning_analysis import build_tuning_analysis

    report = build_tuning_analysis(
        _trends(),
        timing_audit=_audit(total_samples=20, labeled_samples=2),
    )

    assert report["readiness"]["ready"] is False
    assert "low_label_coverage" in _reason_codes(report)
    assert "low_label_coverage" in _recommendation_codes(report)
    assert report["summary"]["label_more_samples_count"] == 1


def test_tuning_analysis_flags_high_signal_false_positive_rate_with_evidence():
    from evals.tuning_analysis import build_tuning_analysis

    signals = {
        "s_ack": {
            "samples": 10,
            "labeled_samples": 6,
            "false_positive_count": 2,
            "true_positive_count": 4,
            "unknown_count": 4,
            "false_positive_rate": 0.333333,
            "actions": {"no_reply": 8, "reply_now": 2},
            "suggestion": "review_threshold",
        }
    }
    samples = [
        {
            "log_id": 101,
            "signal_name": "s_ack",
            "signal_value": 0.85,
            "label": "false_positive",
            "runtime_action": "no_reply",
            "scoring_action": "reply_now",
            "action_mismatch": True,
            "text_preview": "好的，再帮我查下昨天的新闻",
        },
        {
            "log_id": 102,
            "signal_name": "s_ack",
            "signal_value": 0.85,
            "label": "false_positive",
            "runtime_action": "no_reply",
            "scoring_action": "no_reply",
            "action_mismatch": False,
            "text_preview": "嗯，继续说",
        },
    ]

    report = build_tuning_analysis(_trends(), timing_audit=_audit(signals=signals, samples=samples))

    signal = report["signals"][0]
    assert signal["name"] == "s_ack"
    assert signal["label_coverage_rate"] == 0.6
    assert signal["false_positive_rate"] == 0.333333
    assert signal["mismatch_count"] == 0
    assert signal["evidence_samples"][0]["log_id"] == 101
    review = [
        item for item in report["recommendations"]
        if item["reason_code"] == "high_false_positive_rate"
    ][0]
    assert review["type"] == "manual_review"
    assert review["area"] == "timing_signal"
    assert review["evidence"]["signal"] == "s_ack"
    assert review["evidence"]["sample_log_ids"] == [101, 102]
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest tests/test_periodic_tuning_analysis.py::test_tuning_analysis_recommends_labeling_for_low_label_coverage tests/test_periodic_tuning_analysis.py::test_tuning_analysis_flags_high_signal_false_positive_rate_with_evidence -q -p no:cacheprovider
```

预期：失败，原因是尚未生成 `low_label_coverage`、`signals[]` 和高假阳率建议。

- [ ] **步骤 3：实现 signal 聚合和证据选择**

在 `evals/tuning_analysis.py` 增加 helper：

```python
def _label_coverage(labeled: int, total: int) -> float:
    return round(labeled / total, 6) if total else 0.0


def _samples_for_signal(samples: list[dict[str, Any]], signal_name: str) -> list[dict[str, Any]]:
    return [
        item for item in samples
        if str(item.get("signal_name") or "") == signal_name
    ]


def _evidence_samples(samples: list[dict[str, Any]], signal_name: str) -> list[dict[str, Any]]:
    selected = sorted(
        _samples_for_signal(samples, signal_name),
        key=lambda item: (
            not bool(item.get("action_mismatch")),
            str(item.get("label") or "") != "false_positive",
            _as_int(item.get("log_id")),
        ),
    )[:3]
    return [
        {
            "log_id": item.get("log_id"),
            "signal_value": _as_float(item.get("signal_value")),
            "runtime_action": str(item.get("runtime_action") or ""),
            "scoring_action": str(item.get("scoring_action") or ""),
            "action_mismatch": bool(item.get("action_mismatch")),
            "text_preview": str(item.get("text_preview") or ""),
        }
        for item in selected
    ]


def _signal_items(timing_audit: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(timing_audit, dict):
        return []
    signals = timing_audit.get("signals") if isinstance(timing_audit.get("signals"), dict) else {}
    shadow = timing_audit.get("shadow") if isinstance(timing_audit.get("shadow"), dict) else {}
    mismatches = shadow.get("mismatches_by_signal") if isinstance(shadow.get("mismatches_by_signal"), dict) else {}
    samples = timing_audit.get("samples") if isinstance(timing_audit.get("samples"), list) else []
    items: list[dict[str, Any]] = []
    for name, stats in sorted(signals.items()):
        if not isinstance(stats, dict):
            continue
        total = _as_int(stats.get("samples"))
        labeled = _as_int(stats.get("labeled_samples"))
        items.append({
            "name": str(name),
            "samples": total,
            "labeled_samples": labeled,
            "label_coverage_rate": _label_coverage(labeled, total),
            "false_positive_rate": _as_float(stats.get("false_positive_rate")),
            "suggestion": str(stats.get("suggestion") or ""),
            "runtime_actions": stats.get("actions") if isinstance(stats.get("actions"), dict) else {},
            "mismatch_count": _as_int(mismatches.get(name)),
            "evidence_samples": _evidence_samples(samples, str(name)),
        })
    return items
```

在 `build_tuning_analysis()` 中：

```python
    signal_items = _signal_items(timing_audit)
    if isinstance(timing_audit, dict):
        total_samples = _as_int(timing_audit.get("total_samples"))
        labeled_samples = _as_int(timing_audit.get("labeled_samples"))
        if total_samples > 0 and _label_coverage(labeled_samples, total_samples) < min_label_coverage:
            blocking.append(_reason(
                "low_label_coverage",
                f"TimingSignal 标注覆盖率低于 {min_label_coverage:.0%}",
                label_coverage_rate=_label_coverage(labeled_samples, total_samples),
            ))
            recommendations.append(_recommendation(
                "label_more_samples",
                "timing_signal",
                "medium",
                "low_label_coverage",
                "TimingSignal 标注覆盖不足，需要先补标注再讨论调参",
                {
                    "labeled_samples": labeled_samples,
                    "total_samples": total_samples,
                    "label_coverage_rate": _label_coverage(labeled_samples, total_samples),
                    "min_label_coverage": min_label_coverage,
                },
            ))

    for signal in signal_items:
        if signal["labeled_samples"] < min_signal_labeled_samples:
            recommendations.append(_recommendation(
                "label_more_samples",
                "timing_signal",
                "low",
                "low_signal_label_count",
                f"{signal['name']} 标注样本不足，需要补充该信号样本",
                {
                    "signal": signal["name"],
                    "labeled_samples": signal["labeled_samples"],
                    "min_signal_labeled_samples": min_signal_labeled_samples,
                },
            ))
            continue
        if signal["false_positive_rate"] >= high_false_positive_rate:
            recommendations.append(_recommendation(
                "manual_review",
                "timing_signal",
                "medium",
                "high_false_positive_rate",
                f"{signal['name']} 标注样本假阳率偏高，需要人工复核信号提取器",
                {
                    "signal": signal["name"],
                    "false_positive_rate": signal["false_positive_rate"],
                    "labeled_samples": signal["labeled_samples"],
                    "sample_log_ids": [
                        item["log_id"]
                        for item in signal["evidence_samples"]
                        if item.get("log_id") is not None
                    ],
                },
            ))
```

并把返回值中的 `"signals": []` 改为 `"signals": signal_items`。

- [ ] **步骤 4：运行测试验证绿灯**

运行：

```bash
python -B -m pytest tests/test_periodic_tuning_analysis.py -q -p no:cacheprovider
```

预期：任务 1 和任务 2 的测试全部通过。

- [ ] **步骤 5：Commit**

运行：

```bash
git add tests/test_periodic_tuning_analysis.py evals/tuning_analysis.py
git commit -m "feat(评测): 分析时机信号证据"
```

## 任务 3：趋势退化建议和 no-change 结论

**文件：**

- 修改：`tests/test_periodic_tuning_analysis.py`
- 修改：`evals/tuning_analysis.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_periodic_tuning_analysis.py` 追加：

```python
def test_tuning_analysis_recommends_review_for_timing_rag_and_eval_regressions():
    from evals.tuning_analysis import build_tuning_analysis

    trends = _trends(
        timing_items=[{
            "run_id": "run_3",
            "action_mismatch_count_delta": 2,
            "action_mismatch_rate_delta": 0.1,
        }],
        rag_items=[{
            "run_id": "run_3",
            "pass_rate_delta": -0.1,
            "hit@5_delta": -0.25,
            "mrr_delta": -0.3,
        }],
        eval_suites={
            "timing_gate": [{
                "run_id": "run_3",
                "suite": "timing_gate",
                "pass_rate_delta": -0.2,
                "failed_delta": 2,
                "new_failed_count": 1,
            }]
        },
        regressions=[{"type": "rag_mrr_drop", "run_id": "run_3", "delta": -0.3}],
    )

    report = build_tuning_analysis(trends, timing_audit=_audit())
    codes = _recommendation_codes(report)

    assert "timing_action_mismatch_increase" in codes
    assert "rag_metric_drop" in codes
    assert "eval_suite_regression" in codes
    assert report["summary"]["must_review_count"] == 3
    assert report["regression_refs"] == [
        {"type": "rag_mrr_drop", "run_id": "run_3", "delta": -0.3, "source": "artifact_trends"}
    ]
    assert all(item["type"] != "candidate_adjustment" for item in report["recommendations"])


def test_tuning_analysis_emits_no_change_when_ready_and_stable():
    from evals.tuning_analysis import build_tuning_analysis

    report = build_tuning_analysis(_trends(), timing_audit=_audit())

    assert report["readiness"]["ready"] is True
    assert report["recommendations"] == [{
        "type": "no_change",
        "area": "artifact_health",
        "severity": "info",
        "reason_code": "stable_metrics",
        "message": "周期趋势和 TimingSignal audit 未显示需要调参的退化信号",
        "evidence": {"run_count": 3},
    }]
    assert report["summary"]["no_change_count"] == 1
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest tests/test_periodic_tuning_analysis.py::test_tuning_analysis_recommends_review_for_timing_rag_and_eval_regressions tests/test_periodic_tuning_analysis.py::test_tuning_analysis_emits_no_change_when_ready_and_stable -q -p no:cacheprovider
```

预期：失败，原因是趋势退化建议和 `no_change` 尚未实现。

- [ ] **步骤 3：实现趋势建议**

在 `evals/tuning_analysis.py` 增加：

```python
def _series(trends: dict[str, Any]) -> dict[str, Any]:
    return trends.get("series") if isinstance(trends.get("series"), dict) else {}


def _latest_timing_item(trends: dict[str, Any]) -> dict[str, Any] | None:
    items = _series(trends).get("timing_signal_audit")
    return items[-1] if isinstance(items, list) and items and isinstance(items[-1], dict) else None


def _latest_rag_item(trends: dict[str, Any]) -> dict[str, Any] | None:
    items = _series(trends).get("rag_benchmark")
    return items[-1] if isinstance(items, list) and items and isinstance(items[-1], dict) else None


def _latest_eval_items(trends: dict[str, Any]) -> list[dict[str, Any]]:
    suites = _series(trends).get("eval_suites")
    if not isinstance(suites, dict):
        return []
    latest: list[dict[str, Any]] = []
    for suite_items in suites.values():
        if isinstance(suite_items, list) and suite_items and isinstance(suite_items[-1], dict):
            latest.append(suite_items[-1])
    return latest


def _regression_refs(trends: dict[str, Any]) -> list[dict[str, Any]]:
    regressions = trends.get("regressions")
    if not isinstance(regressions, list):
        return []
    refs = []
    for item in regressions:
        if isinstance(item, dict):
            ref = dict(item)
            ref["source"] = "artifact_trends"
            refs.append(ref)
    return refs
```

在 `build_tuning_analysis()` 的建议生成后加入：

```python
    timing_item = _latest_timing_item(trends)
    if timing_item and (
        _as_float(timing_item.get("action_mismatch_count_delta")) > 0
        or _as_float(timing_item.get("action_mismatch_rate_delta")) > 0
    ):
        recommendations.append(_recommendation(
            "manual_review",
            "timing_shadow",
            "medium",
            "timing_action_mismatch_increase",
            "TimingSignal runtime / scoring action mismatch 上升，需要复核时机决策链路",
            {
                "run_id": timing_item.get("run_id"),
                "action_mismatch_count_delta": timing_item.get("action_mismatch_count_delta"),
                "action_mismatch_rate_delta": timing_item.get("action_mismatch_rate_delta"),
            },
        ))

    rag_item = _latest_rag_item(trends)
    if rag_item and any(_as_float(rag_item.get(key)) < 0 for key in ("pass_rate_delta", "hit@5_delta", "mrr_delta")):
        recommendations.append(_recommendation(
            "manual_review",
            "rag_benchmark",
            "medium",
            "rag_metric_drop",
            "RAG benchmark 指标下降，需要复核检索或样本变化",
            {
                "run_id": rag_item.get("run_id"),
                "pass_rate_delta": rag_item.get("pass_rate_delta"),
                "hit@5_delta": rag_item.get("hit@5_delta"),
                "mrr_delta": rag_item.get("mrr_delta"),
            },
        ))

    for item in _latest_eval_items(trends):
        if (
            _as_float(item.get("pass_rate_delta")) < 0
            or _as_float(item.get("failed_delta")) > 0
            or _as_int(item.get("new_failed_count")) > 0
        ):
            recommendations.append(_recommendation(
                "manual_review",
                "eval_suite",
                "medium",
                "eval_suite_regression",
                f"{item.get('suite') or 'eval suite'} 指标退化，需要复核失败样本",
                {
                    "run_id": item.get("run_id"),
                    "suite": item.get("suite"),
                    "pass_rate_delta": item.get("pass_rate_delta"),
                    "failed_delta": item.get("failed_delta"),
                    "new_failed_count": item.get("new_failed_count"),
                },
            ))

    if not blocking and not recommendations:
        recommendations.append(_recommendation(
            "no_change",
            "artifact_health",
            "info",
            "stable_metrics",
            "周期趋势和 TimingSignal audit 未显示需要调参的退化信号",
            {"run_count": run_count},
        ))
```

把返回值的 `regression_refs` 改为 `_regression_refs(trends)`，并确保 `ready = not blocking` 在所有 blocking 生成后计算。

- [ ] **步骤 4：运行测试验证绿灯**

运行：

```bash
python -B -m pytest tests/test_periodic_tuning_analysis.py -q -p no:cacheprovider
```

预期：当前测试全部通过。

- [ ] **步骤 5：Commit**

运行：

```bash
git add tests/test_periodic_tuning_analysis.py evals/tuning_analysis.py
git commit -m "feat(评测): 生成趋势复核建议"
```

## 任务 4：manifest 路径解析、JSON 读写和 CLI

**文件：**

- 修改：`tests/test_periodic_tuning_analysis.py`
- 修改：`evals/tuning_analysis.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_periodic_tuning_analysis.py` 追加：

```python
def test_resolve_timing_audit_path_prefers_explicit_then_manifest(tmp_path):
    from evals.tuning_analysis import resolve_timing_audit_path

    explicit = tmp_path / "explicit.json"
    explicit.write_text("{}", encoding="utf-8")
    manifest_report = tmp_path / "manifest-audit.json"
    manifest_report.write_text("{}", encoding="utf-8")
    manifest = {
        "steps": [{
            "kind": "timing_signal_audit",
            "suite": "timing_signal_audit",
            "report_paths": [str(manifest_report)],
        }]
    }

    assert resolve_timing_audit_path(manifest, explicit) == explicit
    assert resolve_timing_audit_path(manifest, None) == manifest_report
    assert resolve_timing_audit_path({"steps": []}, None) is None


def test_tuning_analysis_cli_writes_report(tmp_path, capsys):
    from evals import tuning_analysis

    trends_path = tmp_path / "artifact_trends_latest.json"
    audit_path = tmp_path / "timing_signal_audit_latest.json"
    manifest_path = tmp_path / "periodic_manifest_latest.json"
    out_path = tmp_path / "tuning_analysis_latest.json"
    trends_path.write_text(json.dumps(_trends(), ensure_ascii=False), encoding="utf-8")
    audit_path.write_text(json.dumps(_audit(), ensure_ascii=False), encoding="utf-8")
    manifest_path.write_text(json.dumps({"steps": []}, ensure_ascii=False), encoding="utf-8")

    exit_code = tuning_analysis.main([
        "--trends",
        str(trends_path),
        "--timing-audit",
        str(audit_path),
        "--manifest",
        str(manifest_path),
        "--out",
        str(out_path),
    ])

    captured = capsys.readouterr()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert captured.out.strip() == f"tuning_analysis={out_path}"
    assert payload["analysis_version"] == 1
    assert payload["source"]["trends_path"] == str(trends_path)
    assert payload["source"]["timing_audit_path"] == str(audit_path)
    assert payload["source"]["manifest_path"] == str(manifest_path)
    assert payload["readiness"]["ready"] is True
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest tests/test_periodic_tuning_analysis.py::test_resolve_timing_audit_path_prefers_explicit_then_manifest tests/test_periodic_tuning_analysis.py::test_tuning_analysis_cli_writes_report -q -p no:cacheprovider
```

预期：失败，原因是 `resolve_timing_audit_path()` 和 CLI 尚未实现。

- [ ] **步骤 3：实现文件和 CLI 层**

在 `evals/tuning_analysis.py` 增加：

```python
def load_json_object(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path}")
    return payload


def resolve_timing_audit_path(
    manifest: dict[str, Any] | None,
    explicit_path: str | Path | None,
) -> Path | None:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.exists() else None
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


def write_tuning_analysis(payload: dict[str, Any], out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
```

追加 CLI：

```python
def _load_optional(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    src = Path(path)
    if not src.exists():
        return None
    return load_json_object(src)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trends", default=str(DEFAULT_TRENDS))
    parser.add_argument("--timing-audit", default=str(DEFAULT_TIMING_AUDIT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    trends_path = Path(args.trends)
    trends = load_json_object(trends_path) if trends_path.exists() else {"trend_version": None, "source": {}, "summary": {}, "series": {}, "regressions": []}
    manifest_path = Path(args.manifest)
    manifest = _load_optional(manifest_path)
    timing_path = resolve_timing_audit_path(manifest, args.timing_audit)
    timing_audit = load_json_object(timing_path) if timing_path is not None else None
    payload = build_tuning_analysis(
        trends,
        timing_audit=timing_audit,
        manifest=manifest,
        source_paths={
            "trends": str(trends_path),
            "timing_audit": str(timing_path) if timing_path is not None else "",
            "manifest": str(manifest_path) if manifest_path.exists() else "",
        },
    )
    path = write_tuning_analysis(payload, args.out)
    print(f"tuning_analysis={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **步骤 4：运行测试验证绿灯**

运行：

```bash
python -B -m pytest tests/test_periodic_tuning_analysis.py -q -p no:cacheprovider
python -B -m pytest tests/test_periodic_tuning_analysis.py tests/test_eval_artifact_trends.py tests/test_timing_signal_audit.py -q -p no:cacheprovider
```

预期：新增测试全部通过，相关回归通过。

- [ ] **步骤 5：Commit**

运行：

```bash
git add tests/test_periodic_tuning_analysis.py evals/tuning_analysis.py
git commit -m "feat(评测): 导出调参分析报告"
```

## 任务 5：文档收口和最终验证

**文件：**

- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：更新文档**

在 `docs/evals.md` 的“跨 artifact 周期趋势”之后增加：

````markdown
### 周期趋势只读调参分析

跨 artifact 趋势生成后，可以运行只读调参分析：

```bash
python -B -m evals.tuning_analysis \
  --trends evals/reports/artifact_trends_latest.json \
  --timing-audit evals/reports/timing_signal_audit_latest.json \
  --manifest evals/reports/periodic_manifest_latest.json \
  --out evals/reports/tuning_analysis_latest.json
```

报告输出 `readiness`、`signals`、`recommendations` 和
`regression_refs`。它只读现有 artifact，不读取生产 DB，不更新
baseline，不改变 PR gate 或周期 gate。`candidate_adjustment` 只表示可进入
人工复核的方向，不包含可自动应用的参数值。
````

在 `docs/todo.md` 的路线项 8 / 10 状态中补一句：

```markdown
周期趋势只读调参分析已把 trends、raw TimingSignal audit 和 manifest 转成复核建议；它仍不自动调整 TimingGate、RAG 或 capability 参数。
```

在 `docs/plan_walkthrough.md` 增加本阶段记录。写入前先运行：

```bash
git log --oneline -- .Codex/plans/periodic-tuning-analysis.md evals/tuning_analysis.py docs/evals.md docs/todo.md docs/plan_walkthrough.md | head -n 12
```

新增段落必须包含设计提交 `4c5be89 docs(评测): 设计周期调参分析`、本计划提交、实现提交和文档收口提交的真实短 hash。段落还必须说明：本阶段新增 `evals.tuning_analysis`，输出只读 `tuning_analysis_latest.json`，把趋势退化和 raw audit 证据转成复核、补标注、补 artifact 或暂不调整建议；第一版不自动 apply 参数、不更新 baseline、不改变 gate。

- [ ] **步骤 2：运行文档自检**

运行：

```bash
rg -n "T[O]DO|待[定]|占[位]|待[执]行|FIX[ME]" docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/periodic-tuning-analysis.md tests/test_periodic_tuning_analysis.py evals/tuning_analysis.py
git diff --check -- docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/periodic-tuning-analysis.md tests/test_periodic_tuning_analysis.py evals/tuning_analysis.py
```

预期：`rg` 无输出，`git diff --check` 退出码为 0。

- [ ] **步骤 3：运行最终验证**

运行：

```bash
python -B -m pytest tests/test_periodic_tuning_analysis.py -q -p no:cacheprovider
python -B -m pytest tests/test_periodic_tuning_analysis.py tests/test_eval_artifact_trends.py tests/test_timing_signal_audit.py -q -p no:cacheprovider
python -B -m pytest tests/ -q -p no:cacheprovider
python -B -m evals.tuning_analysis --out tmp/tuning_analysis_latest.json
rm -f tmp/tuning_analysis_latest.json
```

预期：

- 新增测试通过。
- 相关回归通过。
- 全量测试通过。
- CLI smoke 退出码为 0，并打印 `tuning_analysis=tmp/tuning_analysis_latest.json`。
- 临时输出文件已清理。

- [ ] **步骤 4：Commit**

运行：

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(评测): 收口调参分析状态"
```

## 完成前核对清单

- [ ] `evals/tuning_analysis.py` 不包含任何写 baseline、写配置或 apply 参数的路径。
- [ ] CLI 不提供 `--apply`、`--update-baseline` 或 `--set-threshold`。
- [ ] 输入缺失时能写出合法报告和 blocking reason。
- [ ] JSON 损坏时返回非 0，避免把坏 artifact 当作有效证据。
- [ ] `artifact_trends.regressions` 只进入 `regression_refs`，不会自动变成参数变更。
- [ ] `candidate_adjustment` 不包含可直接应用的参数值；如果第一版没有足够证据，也可以不生成该类型。
- [ ] 全量测试在最终提交前通过。
- [ ] 每个阶段只暂存本阶段文件，不使用 `git add .` 或 `git add -A`。
