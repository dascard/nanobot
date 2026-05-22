"""私聊 timing gate 的运行时工具策略测试。"""


def test_superuser_general_query_uses_full_tool_policy():
    from core.private_timing import _infer_effort

    effort, tool_policy, intent = _infer_effort("随便聊两句", is_superuser=True)

    assert effort == "short"
    assert tool_policy == "full"
    assert intent == "superuser_query"
