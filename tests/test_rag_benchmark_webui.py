from pathlib import Path


APP = Path("webui/src/App.jsx")
PAGE = Path("webui/src/features/rag/RagBenchmarkPage.jsx")


def test_rag_benchmark_navigation_is_next_to_debug():
    source = APP.read_text(encoding="utf-8")

    assert "import { RagBenchmarkPage }" in source
    assert "{ to: '/rag-debug', label: 'RAG Debug', icon: Search }" in source
    assert "{ to: '/rag-benchmark', label: 'RAG Benchmark', icon: BarChart3 }" in source
    assert source.index("'/rag-debug'") < source.index("'/rag-benchmark'") < source.index("'/reply-eval'")
    assert '<Route path="/rag-benchmark" element={<RagBenchmarkPage />}' in source


def test_rag_benchmark_page_exposes_provider_modes_and_case_controls():
    source = PAGE.read_text(encoding="utf-8")

    assert "provider_mode" in source
    assert "deterministic" in source
    assert "no_reranker_baseline" in source
    assert "runtime" in source
    assert "sample_before_run" in source
    assert "include_manual" in source
    assert "include_generated" in source
    assert "source_types" in source
    assert "case_types" in source
    assert "include manual" in source
    assert "include generated" in source
    assert "case_status" in source
    assert "result_status" in source
    assert "manual_dir_writable" in source
    assert "generated case 只读" in source
    assert "保存 Manual Case" in source
    assert "删除 Manual Case" in source
    assert "失败明细" in source
    assert "查询内容" in source
    assert "expected candidate ids" in source
    assert "高级 JSON" in source
    assert "指标说明" in source
    assert "召回候选" in source
    assert "caseResults" in source
    assert "metadataItems" in source
    assert "first_seen" in source
    assert "last_seen" in source
    assert "updated_at" in source
    assert "evidence_count" in source
    assert "score components" in source
    assert "hit@1 表示 expected candidate 排在第 1 位的 positive case 比例" in source


def test_rag_benchmark_page_exposes_gate_and_baseline_diff():
    source = PAGE.read_text(encoding="utf-8")

    assert "baseline_path" in source
    assert "min_pass_rate" in source
    assert "max_new_failures" in source
    assert "baseline_diff" in source
    assert "gate" in source
    assert "Gate passed" in source
    assert "Gate failed" in source
    assert "new_failed_cases" in source
    assert "fixed_cases" in source
    assert "still_failed_cases" in source


def test_rag_benchmark_markdown_report_is_plain_text():
    source = PAGE.read_text(encoding="utf-8")

    assert "dangerouslySetInnerHTML" not in source
    assert "<pre" in source
    assert "latestMarkdown" in source
