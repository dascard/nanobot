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
    assert exc.value.plan.current_user_content == (
        "<message_meta>\n{}\n</message_meta>\n"
        "<user_input>\n你好\n</user_input>"
    )


@pytest.mark.asyncio
async def test_compile_prompt_plan_defaults_to_strict_audit(monkeypatch):
    from core.prompt_v2.audit import PromptAuditError, PromptAuditResult
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    monkeypatch.setattr(
        "core.prompt_v2.compiler.audit_prompt_plan",
        lambda _plan: PromptAuditResult(ok=False, issues=["forced default audit failure"]),
    )

    with pytest.raises(PromptAuditError, match="forced default audit failure"):
        await compile_prompt_plan(
            PromptCompileRequest(
                chat_type="private",
                user_id="u1",
                user_input="你好",
                runtime_tool_prompt="[RuntimeTool]\n只允许 reply/no_reply",
            )
        )


@pytest.mark.asyncio
async def test_core_preview_explicitly_requests_strict_audit(monkeypatch):
    from types import SimpleNamespace

    from core.prompt_v2.preview import build_preview_plan
    from core.prompt_v2.schema import PromptCompileRequest

    captured = {}

    async def fake_compile(request, *, strict_audit=False):
        captured["request"] = request
        captured["strict_audit"] = strict_audit
        return SimpleNamespace(prompt_key="chat_private")

    monkeypatch.setattr("core.prompt_v2.preview.compile_prompt_plan", fake_compile)

    result = await build_preview_plan(PromptCompileRequest(user_input="你好"))

    assert result.prompt_key == "chat_private"
    assert captured["strict_audit"] is True
