import math
from datetime import UTC, datetime, timedelta


def test_weighted_score_renormalizes_none_components():
    from core.semantic.scoring import weighted_score

    score = weighted_score(
        {"reranker": 0.8, "semantic": None},
        {"reranker": 0.7, "semantic": 0.3},
    )

    assert score == 0.8


def test_zero_score_does_not_renormalize_weight():
    from core.semantic.scoring import weighted_score

    score = weighted_score(
        {"reranker": 0.8, "semantic": 0.0},
        {"reranker": 0.7, "semantic": 0.3},
    )

    assert score == 0.56


def test_sqlite_bm25_smaller_is_better():
    from core.semantic.scoring import normalize_sqlite_bm25

    assert normalize_sqlite_bm25(1.0, best=1.0, worst=5.0) == 1.0
    assert normalize_sqlite_bm25(5.0, best=1.0, worst=5.0) == 0.0
    assert normalize_sqlite_bm25(3.0, best=1.0, worst=5.0) == 0.5


def test_recency_score_decays_from_latest_to_old():
    from core.semantic.scoring import recency_score

    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)

    latest = recency_score(now, now=now, half_life_days=30)
    old = recency_score(now - timedelta(days=90), now=now, half_life_days=30)

    assert latest == 1.0
    assert 0.05 <= old < 0.2
    assert latest > old


def test_recency_score_missing_and_future_timestamps_are_stable():
    from core.semantic.scoring import recency_score

    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)

    assert recency_score(None, now=now) == 0.5
    assert recency_score(now + timedelta(days=1), now=now) == 1.0


def test_recency_score_accepts_iso_z_timestamp_with_naive_reference():
    from core.semantic.scoring import recency_score

    now = datetime(2026, 6, 17, 12, 0, 0)  # noqa: DTZ001 - verifies naive reference compatibility

    assert recency_score("2026-06-17T12:00:00Z", now=now) == 1.0


def test_fts5_unavailable_marks_degraded():
    from core.semantic.fts import fts5_status

    class BrokenConnection:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("no fts5")

    status = fts5_status(BrokenConnection())

    assert status["fts_unavailable"] is True
    assert status["degraded"] is True
    assert status["fallback_reason"] == "fts_unavailable"


def test_fts5_query_escapes_special_characters():
    from core.semantic.fts import build_fts5_match_query

    query = build_fts5_match_query('foo OR bar a:b near(test) "quote"')

    assert '"foo"' in query
    assert '"bar"' in query
    assert "a:b" not in query
    assert "near(test)" not in query


def test_fts5_query_empty_for_short_cjk_query():
    from core.semantic.fts import build_fts5_match_query

    assert build_fts5_match_query("图") == ""


def test_reranker_score_is_normalized():
    from core.semantic.reranker import normalize_reranker_score

    assert normalize_reranker_score(0.0, mode="sigmoid") == 0.5
    assert normalize_reranker_score(3.0, mode="minmax", best=5.0, worst=1.0) == 0.5
    assert normalize_reranker_score(2.0, mode="identity") == 1.0
    assert normalize_reranker_score(-1.0, mode="identity") == 0.0


def test_local_cross_encoder_downloads_model_before_loading(monkeypatch, tmp_path):
    import sys
    import types

    from core.semantic.reranker import LocalCrossEncoderRerankerProvider, SemanticCandidate

    model_dir = tmp_path / "models" / "bge-reranker-v2-m3"
    calls = []

    def fake_snapshot_download(*, repo_id, local_dir, **_kwargs):
        calls.append((repo_id, local_dir))
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        return local_dir

    class FakeCrossEncoder:
        loaded_model_name = ""

        def __init__(self, model_name):
            FakeCrossEncoder.loaded_model_name = model_name

        def predict(self, pairs):
            return [0.8 for _pair in pairs]

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        types.SimpleNamespace(snapshot_download=fake_snapshot_download),
    )
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(CrossEncoder=FakeCrossEncoder),
    )

    provider = LocalCrossEncoderRerankerProvider(
        str(model_dir),
        download_repo_id="BAAI/bge-reranker-v2-m3",
    )
    result = provider.rerank(
        "端口冲突",
        [SemanticCandidate(candidate_id="c1", source_type="memory", text="端口冲突处理")],
    )

    assert calls == [("BAAI/bge-reranker-v2-m3", str(model_dir))]
    assert FakeCrossEncoder.loaded_model_name == str(model_dir)
    assert result[0].candidate_id == "c1"


def test_reranker_none_triggers_degraded_mode():
    from core.semantic.scoring import passes_relevance_gate

    assert passes_relevance_gate({"reranker": None, "semantic": 0.9}, degraded=False) is False
    assert passes_relevance_gate({"reranker": 0.3, "semantic": 0.9}, degraded=False) is False
    assert passes_relevance_gate({"reranker": None, "semantic": 0.9}, degraded=True) is True


def test_source_weights_are_normalized_over_enabled_sources():
    from core.semantic.scoring import normalize_source_weights

    weights = normalize_source_weights(
        {"digest": 2.0, "summary": 1.0, "knowledge": 10.0},
        {"digest", "summary"},
    )

    assert set(weights) == {"digest", "summary"}
    assert math.isclose(sum(weights.values()), 1.0)
    assert weights["digest"] > weights["summary"]


def test_source_quota_when_sources_more_than_total_k():
    from core.semantic.scoring import allocate_source_quotas

    quotas = allocate_source_quotas(
        3,
        {"a": 5.0, "b": 4.0, "c": 3.0, "d": 2.0, "e": 1.0},
        min_per_source=3,
    )

    assert sum(quotas.values()) == 3
    assert quotas == {"a": 1, "b": 1, "c": 1, "d": 0, "e": 0}


def test_source_quota_sum_never_exceeds_total_k():
    from core.semantic.scoring import allocate_source_quotas

    for total_k in range(0, 8):
        quotas = allocate_source_quotas(
            total_k,
            {"digest": 0.5, "summary": 0.3, "knowledge": 0.2},
            min_per_source=3,
        )
        assert sum(quotas.values()) <= total_k


def test_fake_semantic_providers_are_deterministic():
    from tests.fakes.semantic import FakeEmbeddingProvider, FakeRerankerProvider
    from core.semantic.reranker import SemanticCandidate

    embedding = FakeEmbeddingProvider(dim=4).embed(["端口冲突"])[0]
    assert len(embedding) == 4
    assert embedding == FakeEmbeddingProvider(dim=4).embed(["端口冲突"])[0]

    candidates = [
        SemanticCandidate(candidate_id="a", source_type="memory", text="端口冲突解决方式"),
        SemanticCandidate(candidate_id="b", source_type="memory", text="天气"),
    ]
    results = FakeRerankerProvider({"a": 2.0, "b": -2.0}).rerank("端口冲突", candidates)
    assert [item.candidate_id for item in results] == ["a", "b"]
    assert results[0].score > results[1].score
