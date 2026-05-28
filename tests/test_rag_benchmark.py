import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import (
    Base,
    GroupMemory,
    KnowledgeChunk,
    KnowledgeDocument,
    RagDebugRun,
    SemanticIndexItem,
    StickerMemory,
)


def _session_for(path: Path):
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_case_loader_merges_manual_and_generated(tmp_path):
    from evals.rag_benchmark.cases import load_cases

    manual_dir = tmp_path / "manual"
    generated_dir = tmp_path / "generated"
    manual_dir.mkdir()
    generated_dir.mkdir()
    (manual_dir / "sticker_manual.json").write_text(json.dumps({
        "id": "sticker_manual",
        "suite": "rag_benchmark",
        "source_type": "sticker",
        "case_type": "constraint_only",
        "query": "表情包",
        "expected": {"candidate_ids": [], "allow_empty": True, "max_reranker_candidates": 10},
    }, ensure_ascii=False), encoding="utf-8")
    (generated_dir / "memory.jsonl").write_text(
        "\n".join([
            json.dumps({
                "id": "memory_generated",
                "suite": "rag_benchmark",
                "source_type": "memory",
                "case_type": "positive",
                "query": "RAG benchmark",
                "expected": {"candidate_ids": ["memory_digest:1:card:0"]},
                "meta": {"origin": "generated_exact"},
            }, ensure_ascii=False),
            "",
        ]),
        encoding="utf-8",
    )

    cases = load_cases(manual_dir=manual_dir, generated_dir=generated_dir)

    assert [case.id for case in cases] == ["sticker_manual", "memory_generated"]
    assert cases[0].case_type == "constraint_only"
    assert cases[1].meta["origin"] == "generated_exact"


def test_scorer_supports_positive_negative_and_constraint_only():
    from evals.rag_benchmark.schema import BenchmarkCandidate, BenchmarkCase, BenchmarkResult
    from evals.rag_benchmark.scoring import score_case

    positive = BenchmarkCase(
        id="memory_positive",
        source_type="memory",
        case_type="positive",
        query="RAG",
        expected={"candidate_ids": ["memory_digest:42:card:0"], "hit_at": 5},
    )
    positive_result = BenchmarkResult(
        case_id="memory_positive",
        source_type="memory",
        candidate_ids=["memory_digest:9:card:0", "memory_digest:42:card:0"],
        candidates=[
            BenchmarkCandidate(candidate_id="memory_digest:9:card:0", source_type="memory_digest", rank=1),
            BenchmarkCandidate(candidate_id="memory_digest:42:card:0", source_type="memory_digest", rank=2),
        ],
        latency_ms=12,
    )

    score = score_case(positive, positive_result)
    assert score.ok is True
    assert score.rank == 2
    assert score.hit_at["1"] is False
    assert score.hit_at["3"] is True
    assert score.mrr == 0.5

    negative = BenchmarkCase(
        id="sticker_negative",
        source_type="sticker",
        case_type="negative",
        query="贴纸",
        expected={"forbidden_candidate_ids": ["sticker:7:sticker"], "allow_empty": True},
    )
    negative_result = BenchmarkResult(
        case_id="sticker_negative",
        source_type="sticker",
        candidate_ids=["sticker:7:sticker"],
        candidates=[BenchmarkCandidate(candidate_id="sticker:7:sticker", source_type="sticker", rank=1)],
    )
    assert score_case(negative, negative_result).ok is False
    assert score_case(negative, negative_result).forbidden_hits == ["sticker:7:sticker"]

    constraint = BenchmarkCase(
        id="sticker_constraint",
        source_type="sticker",
        case_type="constraint_only",
        query="表情包",
        expected={"candidate_ids": [], "allow_empty": True, "max_reranker_candidates": 10},
    )
    constraint_result = BenchmarkResult(
        case_id="sticker_constraint",
        source_type="sticker",
        candidate_ids=[],
        candidates=[],
        reranker_candidates_count=8,
    )
    assert score_case(constraint, constraint_result).ok is True


def test_aggregate_metrics_separates_exact_weak_and_manual():
    from evals.rag_benchmark.schema import BenchmarkCase, BenchmarkResult
    from evals.rag_benchmark.scoring import aggregate_scores, score_case

    cases = [
        BenchmarkCase(
            id="exact",
            source_type="memory",
            case_type="positive",
            query="exact",
            expected={"candidate_ids": ["memory_digest:1:card:0"]},
            meta={"origin": "generated_exact"},
        ),
        BenchmarkCase(
            id="manual",
            source_type="sticker",
            case_type="constraint_only",
            query="表情包",
            expected={"allow_empty": True, "max_reranker_candidates": 10},
            meta={"origin": "manual_hard"},
        ),
    ]
    results = [
        BenchmarkResult(case_id="exact", source_type="memory", candidate_ids=["memory_digest:1:card:0"]),
        BenchmarkResult(case_id="manual", source_type="sticker", candidate_ids=[], reranker_candidates_count=5),
    ]
    scores = [score_case(case, result) for case, result in zip(cases, results)]

    report = aggregate_scores(cases, scores)

    assert report["overall"]["total_cases"] == 2
    assert report["overall_exact"]["positive_cases"] == 1
    assert report["overall_manual"]["total_cases"] == 1


def test_sampler_uses_readonly_db_and_filters_real_gates(tmp_path):
    from evals.rag_benchmark.sample import sample_cases_from_db

    db_path = tmp_path / "rag.db"
    db = _session_for(db_path)
    db.add_all([
        GroupMemory(
            id=1,
            group_id="group_1",
            memory_type="topic",
            content="群里持续讨论 RAG benchmark",
            content_hash="gm-valid",
            evidence_log_ids_json="[1]",
            confidence=0.8,
            evidence_count=1,
            decay_score=0.9,
            status="active",
            inject_policy="auto",
        ),
        GroupMemory(
            id=2,
            group_id="group_1",
            memory_type="topic",
            content="没有证据的记忆",
            content_hash="gm-no-evidence",
            evidence_log_ids_json="[]",
            confidence=0.9,
            evidence_count=0,
            decay_score=0.9,
            status="active",
            inject_policy="auto",
        ),
        StickerMemory(
            id=10,
            chat_stream_id="qq:1:group",
            sticker_hash="ok",
            file_ref="https://example.com/ok.png",
            send_code="[CQ:image,file=https://example.com/ok.png]",
            description="开心拍桌表情包",
            tags_json=json.dumps(["开心", "拍桌"], ensure_ascii=False),
            emotions_json=json.dumps(["happy"], ensure_ascii=False),
            status="active",
            describe_status="ok",
            dedupe_status="unique",
        ),
        StickerMemory(
            id=11,
            chat_stream_id="qq:1:group",
            sticker_hash="dup",
            file_ref="https://example.com/dup.png",
            description="重复表情包",
            tags_json=json.dumps(["重复"], ensure_ascii=False),
            emotions_json="[]",
            status="active",
            describe_status="ok",
            dedupe_status="duplicate",
            duplicate_of_id=10,
        ),
        KnowledgeDocument(
            id=20,
            document_kind="ai_daily",
            title="RAG 文档",
            status="active",
            trust_level="medium",
        ),
        KnowledgeChunk(
            id=21,
            document_id=20,
            chunk_id="chunk-a",
            title="RAG 文档",
            text="RAG benchmark 需要 citation",
            citation_json=json.dumps({"document_id": "20", "chunk_id": "chunk-a", "title": "RAG 文档", "trust_level": "medium"}, ensure_ascii=False),
            status="active",
            trust_level="medium",
        ),
        SemanticIndexItem(
            id=30,
            source_type="knowledge",
            source_id="20",
            source_sub_id="chunk-a",
            status="active",
            visibility="recall",
            title="RAG 文档",
            text="RAG benchmark 需要 citation",
            lexical_text="RAG benchmark citation",
            meta_json=json.dumps({"citation": {"document_id": "20", "chunk_id": "chunk-a", "title": "RAG 文档", "trust_level": "medium"}}, ensure_ascii=False),
        ),
        SemanticIndexItem(
            id=31,
            source_type="knowledge",
            source_id="20",
            source_sub_id="chunk-missing-citation",
            status="active",
            visibility="recall",
            title="缺 citation",
            text="不应抽样",
            lexical_text="不应抽样",
            meta_json="{}",
        ),
        RagDebugRun(trace_id="before", source_type="memory", query="x"),
    ])
    db.commit()
    db.close()

    cases = sample_cases_from_db(db_path, per_source=10)

    ids = {case.id for case in cases}
    expected_ids = {cid for case in cases for cid in case.expected.candidate_ids}
    assert "group_memory_generated_exact_1" in ids
    assert "group_memory:2:memory" not in expected_ids
    assert "sticker:10:sticker" in expected_ids
    assert "sticker:11:sticker" not in expected_ids
    assert "knowledge:20:chunk-a" in expected_ids
    assert "knowledge:20:chunk-missing-citation" not in expected_ids

    db_after = _session_for(db_path)
    assert db_after.query(RagDebugRun).count() == 1
    db_after.close()


def test_memory_sampler_probes_retrievable_exact_query(tmp_path, monkeypatch):
    from evals.rag_benchmark import sample as sampler

    db_path = tmp_path / "memory.db"
    db = _session_for(db_path)
    db.add(SemanticIndexItem(
        id=40,
        source_type="memory_digest",
        source_id="400",
        source_sub_id="digest:level2",
        status="active",
        visibility="recall",
        title="记忆摘要 L2",
        text="[System: Older context truncated...]\n[12:00] ambient(A): [A]: 独特 RAG benchmark 线索",
        lexical_text="[System: Older context truncated...]\n[12:00] ambient(A): [A]: 独特 RAG benchmark 线索",
    ))
    db.commit()

    monkeypatch.setattr(sampler, "_table_exists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        sampler,
        "_memory_probe_rank",
        lambda _db, query, row, candidate_id: 1 if query == "独特 RAG benchmark 线索" else None,
    )

    cases = sampler.sample_cases(db, per_source=1)

    assert len(cases) == 1
    assert cases[0].id == "memory_generated_exact_40"
    assert cases[0].query == "独特 RAG benchmark 线索"
    assert cases[0].meta["sensitivity"] == "local_db"
    db.close()


def test_memory_sampler_probe_uses_deterministic_provider(db_session, monkeypatch):
    from evals.rag_benchmark import sample as sampler
    from evals.rag_benchmark.schema import BenchmarkResult

    row = SemanticIndexItem(
        id=41,
        source_type="memory_digest",
        source_id="401",
        source_sub_id="digest:level2",
        status="active",
        visibility="recall",
        title="RAG",
        text="RAG benchmark probe",
        lexical_text="RAG benchmark probe",
    )
    seen = {}

    def fake_run_case(db, case, **kwargs):
        seen.update(kwargs)
        return BenchmarkResult(
            case_id=case.id,
            source_type="memory",
            candidate_ids=["memory_digest:401:digest:level2"],
        )

    monkeypatch.setattr("evals.rag_benchmark.adapters.run_case_with_adapter", fake_run_case)

    rank = sampler._memory_probe_rank(
        db_session,
        query="RAG benchmark probe",
        row=row,
        candidate_id="memory_digest:401:digest:level2",
    )

    assert rank == 1
    assert seen["provider_mode"] == "deterministic"
    assert seen["readonly"] is True
    assert "use_runtime_providers" not in seen


def test_adapters_standardize_results_without_admin_debug_writes(db_session, monkeypatch):
    from evals.rag_benchmark.adapters import run_case_with_adapter
    from evals.rag_benchmark.schema import BenchmarkCase

    class FakeMemoryService:
        def __init__(self, *args, **kwargs):
            pass

        def query(self, *args, **kwargs):
            return {
                "degraded": False,
                "fallback_reason": "",
                "stats": {"merged_candidates": 2, "reranker_candidates": 1, "reranker_latency_ms": 3},
                "debug_trace": {
                    "final_candidates": [
                        {"candidate_id": "memory_digest:1:card:0", "source_type": "memory_digest", "final_score": 0.9}
                    ]
                },
            }

    monkeypatch.setattr("core.memory_rag.MemoryRagService", FakeMemoryService)
    case = BenchmarkCase(
        id="memory_adapter",
        source_type="memory",
        case_type="positive",
        query="RAG",
        expected={"candidate_ids": ["memory_digest:1:card:0"]},
    )

    result = run_case_with_adapter(db_session, case, use_runtime_providers=False)

    assert result.candidate_ids == ["memory_digest:1:card:0"]
    assert result.merged_candidates_count == 2
    assert result.reranker_candidates_count == 1
    assert result.reranker_latency_ms == 3
    assert db_session.query(RagDebugRun).count() == 0


def test_deterministic_reranker_is_stable_and_expected_blind():
    from core.semantic.reranker import SemanticCandidate
    from evals.rag_benchmark.adapters import DeterministicRerankerProvider

    provider = DeterministicRerankerProvider()
    candidates = [
        SemanticCandidate(candidate_id="candidate:b", source_type="memory", title="普通内容", text="无关文本"),
        SemanticCandidate(candidate_id="candidate:a", source_type="memory", title="RAG benchmark", text="readonly case"),
    ]

    first = provider.rerank("RAG benchmark readonly", candidates, top_k=2)
    second = provider.rerank("RAG benchmark readonly", list(reversed(candidates)), top_k=2)
    third = provider.rerank("RAG benchmark readonly", candidates, top_k=2)

    assert [item.candidate_id for item in first] == [item.candidate_id for item in second]
    assert [item.score for item in first] == [item.score for item in third]
    assert first[0].candidate_id == "candidate:a"
    assert not hasattr(provider, "expected")


def test_reporter_writes_to_tmp_by_default(tmp_path, monkeypatch):
    from evals.rag_benchmark.report import write_reports
    from evals.rag_benchmark.schema import BenchmarkCase, BenchmarkResult
    from evals.rag_benchmark.scoring import score_case

    monkeypatch.chdir(tmp_path)
    case = BenchmarkCase(
        id="manual",
        source_type="sticker",
        case_type="constraint_only",
        query="表情包",
        expected={"allow_empty": True, "max_reranker_candidates": 10},
        meta={"origin": "manual_hard"},
    )
    result = BenchmarkResult(case_id="manual", source_type="sticker", candidate_ids=[], reranker_candidates_count=5)
    score = score_case(case, result)

    paths = write_reports([case], [result], [score])

    assert paths["json"] == Path("tmp/rag_benchmark/reports/latest.json")
    assert paths["markdown"] == Path("tmp/rag_benchmark/reports/latest.md")
    assert paths["json"].exists()
    assert paths["markdown"].exists()
