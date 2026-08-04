def test_sql_analysis_tool_schema_guides_chat_history_queries():
    from nanobot_kt.tools.sql_analysis import SQLAnalysisTool

    tool = SQLAnalysisTool()
    schema = tool.get_parameters_schema()
    sql_desc = schema["properties"]["sql"]["description"]
    combined = tool.description + "\n" + sql_desc

    assert "chat_logs" in combined
    assert "conversation_turns" in combined
    assert "上一句" in combined
    assert "memory_read" in combined
    assert "ORDER BY id DESC LIMIT" in combined
    assert "缺 LIMIT" in combined
    assert "role='ambient'" in combined
    assert "current_message_id" in combined
    assert sql_desc.count("chat_logs 原始消息档案") == 0
    assert tool.description.count("chat_logs 原始消息档案") == 1
