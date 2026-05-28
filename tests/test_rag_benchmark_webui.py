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
    assert "case_status" in source
    assert "result_status" in source
    assert "manual_dir_writable" in source
    assert "generated case 只读" in source
    assert "保存 Manual Case" in source
    assert "删除 Manual Case" in source
    assert "失败明细" in source


def test_rag_benchmark_markdown_report_is_plain_text():
    source = PAGE.read_text(encoding="utf-8")

    assert "dangerouslySetInnerHTML" not in source
    assert "<pre" in source
    assert "latestMarkdown" in source
