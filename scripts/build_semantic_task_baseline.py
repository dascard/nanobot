#!/usr/bin/env python3
"""从现有结构化遥测生成语义任务性能／成本基线。

脚本以 SQLite 只读模式运行，只选择状态、延迟、Token 数、成本数值、请求字节数
和不透明 run 分组。不会读取或输出 Prompt、响应正文、用户 ID、session ID、
trace ID、错误正文或请求 JSON。
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = 1
DEFAULT_OUTPUT = Path(
    "docs/architecture/semantic-task-performance-baseline.json"
)


class SemanticBaselineError(RuntimeError):
    """语义任务基线无法安全构建。"""


@dataclass(frozen=True, slots=True)
class TaskSource:
    task_id: str
    sources: tuple[str, ...]
    billing_class: str
    purpose: str


TASK_SOURCES = (
    TaskSource(
        task_id="private_timing_gate",
        sources=("classifier.timing_gate",),
        billing_class="local_free",
        purpose="私聊／群聊 TimingGate 结构化分类",
    ),
    TaskSource(
        task_id="private_decision",
        sources=("classifier.private_decision",),
        billing_class="local_free",
        purpose="私聊三态与 effort 分类",
    ),
    TaskSource(
        task_id="news_quality",
        sources=("news_daily.summarize_quality",),
        billing_class="provider_gateway",
        purpose="AI 日报质量摘要与仲裁",
    ),
    TaskSource(
        task_id="group_analysis",
        sources=("group_analysis",),
        billing_class="provider_gateway",
        purpose="群分析多方面模型任务",
    ),
)

REQUIRED_LOG_COLUMNS = {
    "run_id",
    "source",
    "provider",
    "model",
    "status",
    "response_status",
    "response_json",
    "request_json",
    "latency_ms",
    "created_at",
}


def render_json(value: Any) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _connect_readonly(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise SemanticBaselineError(f"数据库不存在：{database.name}")
    uri = f"{database.resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
    except sqlite3.Error as exc:
        raise SemanticBaselineError("无法以只读模式打开数据库") from exc
    return connection


def _table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    try:
        rows = connection.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
    except sqlite3.Error as exc:
        raise SemanticBaselineError(
            f"无法读取 {table_name} Schema"
        ) from exc
    return {str(row["name"]) for row in rows}


def _validate_schema(connection: sqlite3.Connection) -> set[str]:
    columns = _table_columns(connection, "llm_api_request_logs")
    missing = REQUIRED_LOG_COLUMNS - columns
    if missing:
        raise SemanticBaselineError(
            "llm_api_request_logs 缺少字段：" + ", ".join(sorted(missing))
        )
    return {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _percentile(values: Sequence[int | float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    fraction = position - lower
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
    return round(value, 3)


def _distribution(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: item[0]))


def _query_task_rows(
    connection: sqlite3.Connection,
    sources: tuple[str, ...],
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in sources)
    sql = f"""
        SELECT
            run_id,
            source,
            COALESCE(provider, '') AS provider,
            COALESCE(model, '') AS model,
            COALESCE(status, '') AS status,
            COALESCE(response_status, 0) AS response_status,
            latency_ms,
            created_at,
            LENGTH(COALESCE(request_json, '')) AS request_bytes,
            CASE WHEN json_valid(response_json)
                THEN COALESCE(
                    json_extract(response_json, '$.usage.prompt_tokens'),
                    json_extract(response_json, '$.usage.input_tokens')
                )
            END AS prompt_tokens,
            CASE WHEN json_valid(response_json)
                THEN COALESCE(
                    json_extract(response_json, '$.usage.completion_tokens'),
                    json_extract(response_json, '$.usage.output_tokens')
                )
            END AS completion_tokens,
            CASE WHEN json_valid(response_json)
                THEN json_extract(response_json, '$.usage.total_tokens')
            END AS total_tokens,
            CASE WHEN json_valid(response_json)
                THEN COALESCE(
                    json_extract(response_json, '$.cost'),
                    json_extract(response_json, '$.usage.cost')
                )
            END AS reported_cost
        FROM llm_api_request_logs
        WHERE source IN ({placeholders})
        ORDER BY id
    """
    try:
        return connection.execute(sql, sources).fetchall()
    except sqlite3.Error as exc:
        raise SemanticBaselineError(
            "读取结构化语义任务遥测失败；需要 SQLite JSON1"
        ) from exc


def _task_metrics(
    descriptor: TaskSource,
    rows: list[sqlite3.Row],
) -> dict[str, Any]:
    latencies = [
        value
        for row in rows
        if (value := _to_int(row["latency_ms"])) is not None
    ]
    request_bytes = [
        value
        for row in rows
        if (value := _to_int(row["request_bytes"])) is not None
    ]
    token_rows = [
        (
            _to_int(row["prompt_tokens"]),
            _to_int(row["completion_tokens"]),
            _to_int(row["total_tokens"]),
        )
        for row in rows
    ]
    token_rows = [
        values for values in token_rows if any(value is not None for value in values)
    ]
    costs = [
        value
        for row in rows
        if (value := _to_float(row["reported_cost"])) is not None
    ]
    run_ids = {
        str(row["run_id"])
        for row in rows
        if str(row["run_id"] or "").strip()
    }
    status_distribution = Counter(
        f"{str(row['status'] or '<empty>')}|http_{int(row['response_status'] or 0)}"
        for row in rows
    )
    provider_distribution = Counter(
        str(row["provider"] or "<empty>") for row in rows
    )
    model_distribution = Counter(
        str(row["model"] or "<empty>") for row in rows
    )
    success_calls = sum(
        1 for row in rows if str(row["status"] or "").lower() == "success"
    )
    calls = len(rows)
    first_at = min(
        (str(row["created_at"]) for row in rows if row["created_at"]),
        default=None,
    )
    last_at = max(
        (str(row["created_at"]) for row in rows if row["created_at"]),
        default=None,
    )

    prompt_total = sum(value[0] or 0 for value in token_rows)
    completion_total = sum(value[1] or 0 for value in token_rows)
    total_total = sum(
        value[2]
        if value[2] is not None
        else (value[0] or 0) + (value[1] or 0)
        for value in token_rows
    )
    return {
        "purpose": descriptor.purpose,
        "sources": list(descriptor.sources),
        "billing_class": descriptor.billing_class,
        "calls": calls,
        "success_calls": success_calls,
        "failure_calls": calls - success_calls,
        "failure_rate": (
            round((calls - success_calls) / calls, 6) if calls else None
        ),
        "observed_window": {"first_at": first_at, "last_at": last_at},
        "distinct_runs": len(run_ids),
        "calls_per_run": (
            round(calls / len(run_ids), 3) if run_ids else None
        ),
        "status_distribution": _distribution(status_distribution),
        "provider_distribution": _distribution(provider_distribution),
        "model_distribution": _distribution(model_distribution),
        "latency_ms": {
            "coverage_calls": len(latencies),
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": max(latencies, default=None),
        },
        "request_bytes": {
            "coverage_calls": len(request_bytes),
            "average": (
                round(sum(request_bytes) / len(request_bytes), 3)
                if request_bytes
                else None
            ),
            "p95": _percentile(request_bytes, 0.95),
            "max": max(request_bytes, default=None),
        },
        "tokens": {
            "coverage_calls": len(token_rows),
            "prompt_total": prompt_total,
            "completion_total": completion_total,
            "total": total_total,
            "average_total_per_covered_call": (
                round(total_total / len(token_rows), 3)
                if token_rows
                else None
            ),
        },
        "cost": {
            "currency": "provider_reported_unit",
            "coverage_calls": len(costs),
            "reported_total": round(sum(costs), 8),
            "note": (
                "仅汇总 Provider 响应的数值 cost；空值不按 0 推断。"
            ),
        },
        "schema_invalid_rate": {
            "status": "not_observable",
            "reason": (
                "现有 llm_api_request_logs 没有类型化 contract_failure_code；"
                "不得从错误正文关键词猜测。"
            ),
        },
    }


def _private_flow_metrics(
    connection: sqlite3.Connection,
    tables: set[str],
) -> dict[str, Any]:
    if "chat_logs" not in tables:
        return {
            "status": "not_observable",
            "reason": "数据库不存在 chat_logs。",
            "stages": {},
            "actions": {},
        }
    columns = _table_columns(connection, "chat_logs")
    if not {"role", "meta_json"}.issubset(columns):
        return {
            "status": "not_observable",
            "reason": "chat_logs 缺少 role 或 meta_json。",
            "stages": {},
            "actions": {},
        }
    try:
        rows = connection.execute(
            """
            SELECT
                COALESCE(
                    json_extract(meta_json, '$.timing_gate.scoring.stage'),
                    '<missing>'
                ) AS stage,
                COALESCE(
                    json_extract(meta_json, '$.timing_gate.action'),
                    '<missing>'
                ) AS action,
                COUNT(*) AS samples
            FROM chat_logs
            WHERE role = 'user'
              AND json_valid(meta_json)
              AND json_extract(meta_json, '$.timing_gate.mode') = 'private'
            GROUP BY stage, action
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise SemanticBaselineError("聚合私聊 Timing 阶段失败") from exc
    stages: Counter[str] = Counter()
    actions: Counter[str] = Counter()
    for row in rows:
        count = int(row["samples"])
        stages[str(row["stage"])] += count
        actions[str(row["action"])] += count
    return {
        "status": "observed" if rows else "no_samples",
        "samples": sum(stages.values()),
        "stages": _distribution(stages),
        "actions": _distribution(actions),
        "limitation": (
            "仅统计已写入 timing_gate meta 的 user ChatLog；不能与完整 Agent "
            "调用直接相加为全量比例。"
        ),
    }


def build_report(
    database: Path,
    *,
    database_label: str | None = None,
) -> dict[str, Any]:
    """构建不含正文和身份字段的聚合报告。"""

    connection = _connect_readonly(database)
    try:
        tables = _validate_schema(connection)
        tasks = {
            descriptor.task_id: _task_metrics(
                descriptor,
                _query_task_rows(connection, descriptor.sources),
            )
            for descriptor in TASK_SOURCES
        }
        private_flow = _private_flow_metrics(connection, tables)
    finally:
        connection.close()

    observed_first = min(
        (
            metrics["observed_window"]["first_at"]
            for metrics in tasks.values()
            if metrics["observed_window"]["first_at"]
        ),
        default=None,
    )
    observed_last = max(
        (
            metrics["observed_window"]["last_at"]
            for metrics in tasks.values()
            if metrics["observed_window"]["last_at"]
        ),
        default=None,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "database": database_label or database.name,
            "mode": "sqlite_readonly_aggregate",
            "observed_window": {
                "first_at": observed_first,
                "last_at": observed_last,
            },
            "selected_fields": [
                "source",
                "provider",
                "model",
                "status",
                "response_status",
                "latency_ms",
                "created_at",
                "request_json length",
                "response_json usage numeric fields",
                "response_json cost numeric field",
                "run_id distinct count only",
            ],
            "forbidden_fields": [
                "Prompt／request_json 正文",
                "response_json 正文",
                "error 正文",
                "user_id",
                "session_id",
                "trace_id",
                "run_id 原值",
            ],
        },
        "tasks": tasks,
        "private_flow": private_flow,
        "observability_gaps": [
            {
                "metric": "schema_invalid_rate",
                "reason": (
                    "当前日志没有结构化 contract failure code；阶段 4 "
                    "Task Runtime 补齐后才能可靠统计。"
                ),
            },
            {
                "metric": "news_candidate_count",
                "reason": "当前 news quality 调用日志没有候选数量字段。",
            },
            {
                "metric": "group_messages_per_session",
                "reason": (
                    "当前 group_analysis 日志没有 canonical session 和输入消息数；"
                    "只能观察调用数、run 数和请求字节数。"
                ),
            },
            {
                "metric": "private_end_to_end_proportion",
                "reason": (
                    "只有部分 ChatLog 写入 Timing stage，不能把模型调用数、"
                    "规则短路数和 Agent Run 数拼成伪全量比例。"
                ),
            },
            {
                "metric": "currency_normalized_cost",
                "reason": (
                    "Provider cost 没有币种字段；当前只保留网关报告数值，"
                    "不跨 Provider 推断货币。"
                ),
            },
        ],
    }


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成不含正文和身份字段的语义任务性能／成本基线",
    )
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="SQLite 数据库路径；始终以只读模式打开",
    )
    parser.add_argument(
        "--database-label",
        default=None,
        help="报告中的安全数据库标签，默认只使用文件名",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="报告输出路径",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="原子写入报告；未指定时输出到 stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        report = build_report(
            arguments.database,
            database_label=arguments.database_label,
        )
    except SemanticBaselineError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rendered = render_json(report)
    if arguments.write:
        _write_atomic(arguments.output, rendered)
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
