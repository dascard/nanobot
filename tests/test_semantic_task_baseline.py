"""语义任务性能／成本基线聚合测试。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


def _create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE llm_api_request_logs (
            id INTEGER PRIMARY KEY,
            run_id TEXT,
            source TEXT,
            provider TEXT,
            model TEXT,
            status TEXT,
            response_status INTEGER,
            response_json TEXT,
            request_json TEXT,
            latency_ms INTEGER,
            created_at TEXT
        );
        CREATE TABLE chat_logs (
            id INTEGER PRIMARY KEY,
            role TEXT,
            meta_json TEXT
        );
        """
    )
    rows = [
        (
            "run-a",
            "classifier.timing_gate",
            "local_llama",
            "local-model",
            "success",
            200,
            {
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
                "cost": "0",
            },
            {"messages": [{"content": "不得出现在报告中的正文"}]},
            10,
            "2026-07-01 00:00:00",
        ),
        (
            "run-b",
            "classifier.timing_gate",
            "local_llama",
            "local-model",
            "error",
            500,
            {},
            {"messages": [{"content": "另一个秘密正文"}]},
            30,
            "2026-07-02 00:00:00",
        ),
        (
            "run-c",
            "news_daily.summarize_quality",
            "new-api",
            "paid-model",
            "success",
            200,
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
                "cost": "0.0125",
            },
            {"messages": [{"content": "新闻正文不得进入报告"}]},
            80,
            "2026-07-03 00:00:00",
        ),
    ]
    connection.executemany(
        """
        INSERT INTO llm_api_request_logs(
            run_id, source, provider, model, status, response_status,
            response_json, request_json, latency_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                source,
                provider,
                model,
                status,
                response_status,
                json.dumps(response, ensure_ascii=False),
                json.dumps(request, ensure_ascii=False),
                latency_ms,
                created_at,
            )
            for (
                run_id,
                source,
                provider,
                model,
                status,
                response_status,
                response,
                request,
                latency_ms,
                created_at,
            ) in rows
        ],
    )
    connection.executemany(
        "INSERT INTO chat_logs(role, meta_json) VALUES ('user', ?)",
        [
            (
                json.dumps(
                    {
                        "timing_gate": {
                            "mode": "private",
                            "action": "reply_now",
                            "scoring": {"stage": "rule_shortcut"},
                        }
                    }
                ),
            ),
            (
                json.dumps(
                    {
                        "timing_gate": {
                            "mode": "private",
                            "action": "no_reply",
                            "scoring": {"stage": "rule_fallback"},
                        }
                    }
                ),
            ),
        ],
    )
    connection.commit()
    connection.close()


def test_semantic_baseline_aggregates_only_numeric_and_structured_telemetry(
    tmp_path,
):
    from scripts.build_semantic_task_baseline import build_report, render_json

    database = tmp_path / "nanobot.db"
    _create_database(database)

    report = build_report(database, database_label="fixture.db")
    timing = report["tasks"]["private_timing_gate"]
    news = report["tasks"]["news_quality"]

    assert report["source"]["database"] == "fixture.db"
    assert timing["calls"] == 2
    assert timing["success_calls"] == 1
    assert timing["failure_calls"] == 1
    assert timing["latency_ms"]["coverage_calls"] == 2
    assert timing["latency_ms"]["p50"] == 20.0
    assert timing["latency_ms"]["p95"] == 29.0
    assert timing["latency_ms"]["p99"] == 29.8
    assert timing["latency_ms"]["max"] == 30
    assert timing["tokens"]["prompt_total"] == 10
    assert timing["tokens"]["coverage_calls"] == 1
    assert news["cost"]["reported_total"] == 0.0125
    assert news["billing_class"] == "provider_gateway"
    assert report["private_flow"]["stages"] == {
        "rule_fallback": 1,
        "rule_shortcut": 1,
    }
    rendered = render_json(report)
    assert "不得出现在报告中的正文" not in rendered
    assert "新闻正文不得进入报告" not in rendered
    assert "run-a" not in rendered
    assert report["observability_gaps"]


def test_semantic_baseline_rejects_missing_or_incompatible_database(tmp_path):
    from scripts.build_semantic_task_baseline import (
        SemanticBaselineError,
        build_report,
    )

    with pytest.raises(SemanticBaselineError, match="不存在"):
        build_report(tmp_path / "missing.db")

    incompatible = tmp_path / "incompatible.db"
    sqlite3.connect(incompatible).close()
    with pytest.raises(SemanticBaselineError, match="缺少"):
        build_report(incompatible)


def test_semantic_baseline_cli_writes_report_without_database_mutation(
    tmp_path,
):
    from scripts.build_semantic_task_baseline import main

    database = tmp_path / "nanobot.db"
    _create_database(database)
    before = database.stat().st_size
    output = tmp_path / "report.json"

    assert (
        main(
            [
                "--database",
                str(database),
                "--database-label",
                "fixture.db",
                "--output",
                str(output),
                "--write",
            ]
        )
        == 0
    )
    assert output.is_file()
    assert database.stat().st_size == before
