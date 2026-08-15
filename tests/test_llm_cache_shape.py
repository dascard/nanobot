from __future__ import annotations


def _request(history: str) -> dict:
    return {
        "model": "deepseek-chat",
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "固定系统提示"},
            {"role": "user", "content": history},
            {"role": "system", "content": "动态画像"},
            {"role": "user", "content": "当前问题"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "reply",
                    "description": "回复",
                    "parameters": {"type": "object"},
                },
            },
        ],
    }


def test_cache_shape_keeps_history_head_stable_when_epoch_only_appends():
    from foundation.llm.cache_shape import build_llm_cache_shape

    history = "甲" * 5000
    first = build_llm_cache_shape(
        _request(history),
        cache_context={"prefix_epoch": "epoch-7", "session_id": "group_7"},
    )
    appended = build_llm_cache_shape(
        _request(history + "追加内容"),
        cache_context={"prefix_epoch": "epoch-7", "session_id": "group_7"},
    )

    assert first["prefix_epoch"] == appended["prefix_epoch"] == "epoch-7"
    assert first["history_head_sha256"] == appended["history_head_sha256"]
    assert first["history_tail_sha256"] != appended["history_tail_sha256"]
    assert first["leading_system_sha256"] == appended["leading_system_sha256"]
    assert first["tools_sha256"] == appended["tools_sha256"]
    assert first["scope_sha256"] == appended["scope_sha256"]
    assert "group_7" not in str(first)


def test_cache_shape_miss_reason_identifies_prefix_breaks():
    from foundation.llm.cache_shape import infer_cache_miss_reason

    previous = {
        "prefix_epoch": "epoch-1",
        "leading_system_sha256": "system-a",
        "tools_sha256": "tools-a",
        "history_head_sha256": "history-a",
        "request_options_sha256": "options-a",
    }

    assert infer_cache_miss_reason(previous, None) == "cold_start"
    assert infer_cache_miss_reason(
        {**previous, "prefix_epoch": "epoch-2"},
        previous,
    ) == "prefix_epoch_changed"
    assert infer_cache_miss_reason(
        {**previous, "tools_sha256": "tools-b"},
        previous,
    ) == "tools_changed"
    assert infer_cache_miss_reason(previous, previous) == (
        "upstream_or_cache_eviction"
    )


def test_cache_shape_uses_manifest_history_range_after_dynamic_user_context():
    from foundation.llm.cache_shape import build_llm_cache_shape

    request = {
        "messages": [
            {"role": "system", "content": "固定策略"},
            {"role": "user", "content": "摘要"},
            {"role": "system", "content": "历史说明"},
            {"role": "user", "content": "历史问题"},
            {"role": "assistant", "content": "历史回答"},
            {"role": "user", "content": "动态画像"},
            {"role": "user", "content": "当前问题"},
        ]
    }

    shape = build_llm_cache_shape(
        request,
        cache_context={
            "history_message_start_index": 3,
            "history_message_end_index": 5,
        },
    )

    assert shape["history_source"] == "manifest"
    assert shape["history_messages"] == 2
