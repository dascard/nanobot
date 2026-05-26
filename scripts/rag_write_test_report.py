#!/usr/bin/env python3
"""生成 RAG 阶段测试报告。"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


REQUIRED_SECTIONS = [
    "实现范围",
    "不做范围",
    "测试函数与需求映射",
    "输入数据",
    "预期输出",
    "实际输出摘要",
    "pytest 命令",
    "git diff --check 结果",
    "Web debug 输入",
    "Web debug 输出",
    "性能摘要",
    "失败修复记录",
    "未覆盖风险",
]


def _read_text(path: Path | str | None, default: str = "") -> str:
    if not path:
        return default
    file_path = Path(path)
    if not file_path.exists():
        return default
    return file_path.read_text(encoding="utf-8").strip()


def _compact_json(raw: str) -> str:
    try:
        return json.dumps(json.loads(raw), ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return raw.strip()


def _performance_summary(raw: str) -> str:
    try:
        data = json.loads(raw)
    except Exception:
        return "未提供结构化性能数据。"

    keys = [
        "query",
        "fts_candidates",
        "embedding_candidates",
        "merged_candidates",
        "reranker_candidates",
        "final_items",
        "latency_ms",
        "degraded",
        "cache_hit",
    ]
    summary = {key: data.get(key) for key in keys if key in data}
    score_breakdown = data.get("score_breakdown")
    if isinstance(score_breakdown, dict):
        for key in ("latency_ms", "degraded", "cache_hit"):
            if key not in summary and key in score_breakdown:
                summary[key] = score_breakdown.get(key)
    if not summary:
        return "未提供结构化性能数据。"
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))


def run_git_diff_check() -> str:
    result = subprocess.run(
        ["git", "diff", "--check"],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = result.stdout.strip()
    if result.returncode == 0:
        return output or "通过：git diff --check 无输出。"
    return output or f"失败：git diff --check exit {result.returncode}。"


def build_report(
    *,
    phase: str,
    pytest_output_path: Path | str | None,
    web_debug_output_path: Path | str | None,
    implementation_scope: str,
    pytest_command: str = "python -m pytest tests/ -v",
    git_diff_check_output: str = "未运行。",
    failure_fixes: str = "本阶段红灯后按 TDD 补齐最小实现，随后重新验证通过。",
    uncovered_risks: str = "真实 reranker 模型、业务 RAG 接入和完整 Web 可视化将在后续阶段验证。",
) -> str:
    pytest_output = _read_text(pytest_output_path, "未提供 pytest 输出。")
    web_debug_output = _read_text(web_debug_output_path, "{}")
    compact_web_debug = _compact_json(web_debug_output)
    performance = _performance_summary(web_debug_output)

    return "\n".join([
        f"# RAG 阶段测试报告：{phase}",
        "",
        "## 实现范围",
        implementation_scope.strip() or "本阶段实现范围见阶段标题。",
        "",
        "## 不做范围",
        "不接入具体业务 RAG 召回，不加载真实 embedding/reranker 模型，不执行生产验收。",
        "",
        "## 测试函数与需求映射",
        "- semantic scoring：weighted score、BM25、source weight、source quota、relevance gate。",
        "- fts：FTS5 availability degraded 标记和 MATCH query 安全构造。",
        "- reranker：分数归一化、fake provider、provider 基础契约。",
        "- rag debug：schema、API 保存/查询、WebUI 路由注册。",
        "",
        "## 输入数据",
        "pytest fixture 使用 in-memory SQLite；Web debug 使用阶段内生成的调试 JSON。",
        "",
        "## 预期输出",
        "阶段目标测试通过，debug run 可落库，Web 入口可静态注册。",
        "",
        "## 实际输出摘要",
        "```text",
        pytest_output,
        "```",
        "",
        "## pytest 命令",
        f"`{pytest_command}`",
        "",
        "## git diff --check 结果",
        "```text",
        git_diff_check_output.strip(),
        "```",
        "",
        "## Web debug 输入",
        "```json",
        compact_web_debug,
        "```",
        "",
        "## Web debug 输出",
        "```json",
        compact_web_debug,
        "```",
        "",
        "## 性能摘要",
        "```json",
        performance,
        "```",
        "",
        "## 失败修复记录",
        failure_fixes.strip(),
        "",
        "## 未覆盖风险",
        uncovered_risks.strip(),
        "",
    ])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--pytest-output", required=True)
    parser.add_argument("--web-debug-output", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--implementation-scope", default="")
    parser.add_argument("--pytest-command", default="python -m pytest tests/ -v")
    args = parser.parse_args()

    out_path = Path(args.out)
    report = build_report(
        phase=args.phase,
        pytest_output_path=args.pytest_output,
        web_debug_output_path=args.web_debug_output,
        implementation_scope=args.implementation_scope,
        pytest_command=args.pytest_command,
        git_diff_check_output=run_git_diff_check(),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
