def test_group_learning_rules_extract_whole_phrase_signal():
    from core.group_learning import dry_run_learning_rules

    result = dry_run_learning_rules("端口冲突了，先看服务端日志")
    phrases = [
        item.canonical_content
        for item in result.matches
        if item.candidate_type == "expression"
    ]

    assert phrases


def test_group_learning_rules_do_not_treat_math_as_slang_definition():
    from core.group_learning import dry_run_learning_rules

    result = dry_run_learning_rules(
        "梦铃股: 500股 × 140.62 = 70310.00金币"
    )

    assert not [
        item
        for item in result.matches
        if item.candidate_type == "slang"
    ]


def test_build_timing_recent_context_strips_speaker_prefix():
    from core.context_builder import _strip_speaker_prefix

    result = _strip_speaker_prefix("[张三]: 端口冲突了", "张三")
    assert "张三" not in result or result == "端口冲突了"

    result2 = _strip_speaker_prefix("[张三]：端口冲突了", "张三")
    assert "张三" not in result2 or result2 == "端口冲突了"
