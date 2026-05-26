def test_rag_test_report_includes_required_sections(tmp_path):
    from scripts.rag_write_test_report import build_report

    pytest_output = tmp_path / "pytest.txt"
    pytest_output.write_text("15 passed, 17 warnings in 0.88s\n", encoding="utf-8")
    web_debug = tmp_path / "debug.json"
    web_debug.write_text('{"query":"端口冲突","latency_ms":12,"degraded":true}\n', encoding="utf-8")

    report = build_report(
        phase="01-semantic-scoring-fts5-reranker",
        pytest_output_path=pytest_output,
        web_debug_output_path=web_debug,
        implementation_scope="评分、FTS5、reranker 和 RAG debug 骨架。",
    )

    for title in [
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
    ]:
        assert f"## {title}" in report
    assert "15 passed" in report
    assert '"latency_ms":12' in report
