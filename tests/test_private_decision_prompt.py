def test_private_decision_prompt_does_not_wait_for_history_lookup():
    from clients.classifier_client import PRIVATE_DECISION_PROMPT

    assert "上一句/刚才/之前/聊天记录/你记得吗/我说过什么" in PRIVATE_DECISION_PROMPT
    assert "需要查历史或数据库不等于 wait" in PRIVATE_DECISION_PROMPT
    assert "不要因为缺少已注入上下文而 wait" in PRIVATE_DECISION_PROMPT
    assert '"action":"reply_now","complexity":4' in PRIVATE_DECISION_PROMPT
