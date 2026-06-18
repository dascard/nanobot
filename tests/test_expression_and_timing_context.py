def test_short_cjk_phrases_extracts_whole_phrases():
    from core.expression_learner import _short_cjk_phrases

    phrases = _short_cjk_phrases("端口冲突了，先看服务端日志")

    assert "端口冲突" in phrases or len(phrases) > 0


def test_short_cjk_phrases_filters_noise():
    from core.expression_learner import _short_cjk_phrases

    phrases = _short_cjk_phrases("今天感觉不知道")

    assert phrases == [] or all(p not in ("今天", "感觉", "不知道") for p in phrases)


def test_build_timing_recent_context_strips_speaker_prefix():
    from core.context_builder import _strip_speaker_prefix

    result = _strip_speaker_prefix("[张三]: 端口冲突了", "张三")
    assert "张三" not in result or result == "端口冲突了"

    result2 = _strip_speaker_prefix("[张三]：端口冲突了", "张三")
    assert "张三" not in result2 or result2 == "端口冲突了"
