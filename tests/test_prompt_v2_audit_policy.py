import pytest


@pytest.mark.asyncio
async def test_compile_prompt_plan_strict_audit_error_carries_plan(monkeypatch):
    from core.prompt_v2.audit import PromptAuditError, PromptAuditResult
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest, PromptPlan

    def fake_audit(plan: PromptPlan):
        return PromptAuditResult(ok=False, issues=["forced audit failure"])

    monkeypatch.setattr("core.prompt_v2.compiler.audit_prompt_plan", fake_audit)

    with pytest.raises(PromptAuditError) as exc:
        await compile_prompt_plan(
            PromptCompileRequest(
                chat_type="private",
                user_id="u1",
                user_input="你好",
                persona_text="无已存储画像",
                runtime_tool_prompt="[RuntimeTool]\n只允许 reply/no_reply",
            ),
            strict_audit=True,
        )

    assert exc.value.issues == ["forced audit failure"]
    assert exc.value.plan is not None
    assert exc.value.plan.current_user_content == "<user_input>\n你好\n</user_input>"
