from pathlib import Path


def test_private_decision_prompt_does_not_wait_for_history_lookup():
    prompt = Path(
        "prompts.v2.default/tasks/private_decision.md"
    ).read_text(encoding="utf-8")

    assert "上一句/刚才/之前/聊天记录/你记得吗/我说过什么" in prompt
    assert "需要查历史或数据库不等于 wait" in prompt
    assert "不要因为缺少已注入上下文而 wait" in prompt
    assert '"action":"reply_now","complexity":4' in prompt


def test_private_decision_default_and_runtime_templates_are_synchronized():
    default = Path("prompts.v2.default/tasks/private_decision.md").read_bytes()
    runtime = Path("data/prompts_v2/tasks/private_decision.md").read_bytes()

    assert runtime == default
