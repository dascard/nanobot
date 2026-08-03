"""测试 llm_trace_context ContextVar 传递与嵌套。"""

from core.llm_trace_context import (
    get_llm_cache_context,
    get_llm_trace_execution_vars,
    get_llm_trace_vars,
    llm_phase,
    llm_route_attempt_index,
    llm_run_id,
    llm_source,
    llm_trace_id,
    llm_trace_scope,
)


def test_contextvar_default_empty():
    assert llm_trace_id.get() == ""
    assert llm_run_id.get() == ""
    assert llm_source.get() == ""
    assert llm_phase.get() == ""
    assert llm_route_attempt_index.get() == 0


def test_contextvar_set_and_reset():
    tok_t = llm_trace_id.set("trace-123")
    tok_r = llm_run_id.set("run-456")
    tok_s = llm_source.set("replyer")

    assert llm_trace_id.get() == "trace-123"
    assert llm_run_id.get() == "run-456"
    assert llm_source.get() == "replyer"

    llm_source.reset(tok_s)
    llm_run_id.reset(tok_r)
    llm_trace_id.reset(tok_t)

    assert llm_trace_id.get() == ""
    assert llm_run_id.get() == ""
    assert llm_source.get() == ""


def test_scope_inherits_run_id_and_execution_phase():
    """内层 scope 继承链路字段，可选覆盖 source 和 phase。"""
    with llm_trace_scope(
        trace_id="t1",
        run_id="r1",
        source="replyer",
        phase="agent.tool_round",
        route_attempt_index=1,
        cache_context={"prefix_epoch": "epoch-1"},
    ):
        assert get_llm_trace_vars() == ("t1", "r1", "replyer")
        assert get_llm_trace_execution_vars() == ("agent.tool_round", 1)
        assert get_llm_cache_context() == {"prefix_epoch": "epoch-1"}
        with llm_trace_scope(
            source="group_analysis",
            phase="model.route_retry",
            route_attempt_index=2,
        ):
            assert get_llm_trace_vars() == ("t1", "r1", "group_analysis")
            assert get_llm_trace_execution_vars() == ("model.route_retry", 2)
            assert get_llm_cache_context() == {"prefix_epoch": "epoch-1"}
        assert get_llm_trace_vars() == ("t1", "r1", "replyer")
        assert get_llm_trace_execution_vars() == ("agent.tool_round", 1)
        assert get_llm_cache_context() == {"prefix_epoch": "epoch-1"}
    assert get_llm_trace_vars() == ("", "", "")
    assert get_llm_trace_execution_vars() == ("", 0)
    assert get_llm_cache_context() == {}


def test_get_llm_trace_vars():
    with llm_trace_scope(trace_id="tx", run_id="rx", source="news_search"):
        t, r, s = get_llm_trace_vars()
        assert t == "tx"
        assert r == "rx"
        assert s == "news_search"
