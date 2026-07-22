from pathlib import Path


def test_persona_preprocess_candidate_prompt_symbols_are_facades():
    import core.persona_candidate_prompt as prompt
    import core.persona_preprocess as preprocess

    expected = {
        "filter_user_messages": prompt.filter_user_messages,
        "format_candidate_logs": prompt.format_candidate_logs,
        "build_candidate_extraction_prompt": prompt.build_candidate_extraction_prompt,
        "get_candidate_extraction_system_prompt": prompt.get_candidate_extraction_system_prompt,
    }
    for name, target in expected.items():
        assert getattr(preprocess, name) is target


def test_persona_preprocess_split_keeps_file_under_800_lines():
    line_count = len(Path("core/persona_preprocess.py").read_text(encoding="utf-8").splitlines())
    assert line_count < 800
