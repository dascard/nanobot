"""模型候选合并身份测试。"""

from nanobot_kt.model_attempts import merge_model_candidates


def test_merge_keeps_same_model_from_different_codex_accounts():
    candidates = merge_model_candidates(
        None,
        [
            {
                "id": "gpt-5.6-codex",
                "_candidate_key": "gpt-5.6-codex@codex:ca_account_a_0001",
            },
            {
                "id": "gpt-5.6-codex",
                "_candidate_key": "gpt-5.6-codex@codex:ca_account_b_0002",
            },
        ],
    )

    assert [item["_candidate_key"] for item in candidates] == [
        "gpt-5.6-codex@codex:ca_account_a_0001",
        "gpt-5.6-codex@codex:ca_account_b_0002",
    ]


def test_merge_preserves_legacy_model_id_deduplication():
    candidates = merge_model_candidates(
        {"id": "shared-model"},
        [{"id": "shared-model"}, {"id": "fallback-model"}],
    )

    assert [item["id"] for item in candidates] == [
        "shared-model",
        "fallback-model",
    ]
