from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memory_page_contains_summary_tabs_and_session_ranges():
    source = (ROOT / "webui/src/App.jsx").read_text(encoding="utf-8")

    assert "近期摘要" in source
    assert "长期摘要" in source
    assert "session_id" in source
    assert "kind: isRecent ? 'recent' : 'long'" in source
    assert "latest_digest_preview" in source
    assert "无长期摘要预览" in source
    assert "turn_start" in source
    assert "source_start_log_id" in source


def test_logs_page_contains_all_lines_and_error_context_mode():
    source = (ROOT / "webui/src/App.jsx").read_text(encoding="utf-8")

    assert '<option value="all">所有</option>' in source
    assert "ERROR 上下文" in source
    assert "group_errors" in source


def test_reply_and_reasoning_views_expose_counts_and_missing_reasoning():
    agent_detail = (ROOT / "webui/src/features/agent-runs/AgentRunDetailPage.jsx").read_text(encoding="utf-8")
    reply_eval = (ROOT / "webui/src/features/reply-eval/ReplyEvalPage.jsx").read_text(encoding="utf-8")
    trace_view = (ROOT / "webui/src/components/TraceView.jsx").read_text(encoding="utf-8")

    assert "total_final_action_count" in agent_detail
    assert "prompt_miss_count" in agent_detail
    assert "reply_tool_call_count" in reply_eval
    assert "/reply-eval/traffic" in reply_eval
    assert "真实流量" in reply_eval
    assert "retry_failed_after_prompt_count" in reply_eval
    assert "本次未返回 reasoning_content" in trace_view


def test_timing_gate_detail_exposes_scoring_breakdown():
    source = (ROOT / "webui/src/App.jsx").read_text(encoding="utf-8")

    assert "const scoring = event.scoring || {}" in source
    assert "规则评分" in source
    assert "信号分解" in source
    assert "模型参与" in source
    assert "participation_score" in source
    assert "final_score" in source
    assert "s_ack" in source
    assert "s_transport" in source
    assert "s_transport_tier" in source
    assert "w_marker" in source
    assert "w_file" in source
    assert "w_incomplete" in source
    assert "conflict_score" in source
    assert "soft_reject_cap" in source
    assert "scoring.delay_seconds" in source
    assert "model_weight" in source


def test_timing_gate_manual_test_repeats_matches_backend_cap():
    source = (ROOT / "webui/src/App.jsx").read_text(encoding="utf-8")

    assert 'max="5"' in source
    assert "setRepeats(5)" in source
    assert "5次" in source
    assert "20次" not in source
