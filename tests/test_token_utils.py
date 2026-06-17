def test_estimate_tokens_counts_cjk_ascii_and_other_unicode():
    from core.token_utils import estimate_tokens

    assert estimate_tokens("") == 0
    assert estimate_tokens("你好") == 2
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("🙂🙂") == 1
    assert estimate_tokens("你a🙂") == 2


def test_remaining_token_estimators_share_same_formula():
    from app.session_memory.windowing import estimate_tokens as window_tokens
    from core.context_builder import estimate_tokens as context_tokens
    from core.legacy_adapter import PromptAuditorAgent
    from core.prompt_v2.section_renderer import estimate_tokens as section_tokens
    from core.prompts.manager import _estimate_tokens as prompt_tokens
    from core.token_utils import estimate_tokens

    text = "你好 abc 🙂 全角："
    expected = estimate_tokens(text)

    assert context_tokens(text) == expected
    assert window_tokens(text) == expected
    assert section_tokens(text) == expected
    assert prompt_tokens(text) == expected
    assert PromptAuditorAgent._estimate_tokens(text) == expected
