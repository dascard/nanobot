"""Tests for persona preprocessing layer: state machine + embedding + confidence."""
import json
from datetime import datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, PersonaFact
from core.persona_preprocess import (
    PersonaStateMachine,
    build_candidate_extraction_prompt,
    compute_confidence,
    confidence_label,
    cosine_similarity,
    filter_user_messages,
    _from_blob,
    _to_blob,
)


# ── Fixtures ──

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def state_machine(db_session):
    return PersonaStateMachine(db_session, "test_user_01")


# ── 纯函数测试 ──

class TestConfidence:
    def test_fresh_evidence_curve(self):
        s1 = compute_confidence(1, 0)
        s3 = compute_confidence(3, 0)
        s5 = compute_confidence(5, 0)
        assert 0.55 < s1 < 0.65
        assert 0.75 < s3 < 0.90
        assert s5 > 0.85

    def test_decay_over_time(self):
        s_fresh = compute_confidence(3, 0)
        s_old = compute_confidence(3, 30)
        assert s_old < s_fresh
        assert 0.5 < s_old < 0.7

    def test_recency_floor_zero(self):
        s = compute_confidence(5, 100)
        assert s >= 0

    def test_label_confirm(self):
        assert confidence_label(0.8) == "确认"
        assert confidence_label(0.76) == "确认"

    def test_label_possible(self):
        assert confidence_label(0.5) == "可能"
        assert confidence_label(0.41) == "可能"

    def test_label_pending(self):
        assert confidence_label(0.3) == "待确认"
        assert confidence_label(0.0) == "待确认"


class TestFilterUserMessages:
    def test_filters_only_user(self):
        logs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "tool", "content": "exec"},
            {"role": "ambient", "content": "joined"},
            {"role": "User", "content": "ok"},
        ]
        result = filter_user_messages(logs)
        assert len(result) == 2
        assert all(r["role"].lower() == "user" for r in result)

    def test_empty_list(self):
        assert filter_user_messages([]) == []

    def test_all_non_user(self):
        logs = [{"role": "assistant", "content": "x"}] * 5
        assert filter_user_messages(logs) == []


class TestCosineSimilarity:
    def test_identical(self):
        v = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        v = v / np.linalg.norm(v)
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal(self):
        a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([-1.0, 0.0], dtype=np.float32)
        assert cosine_similarity(a, b) == pytest.approx(-1.0)


class TestBlobRoundtrip:
    def test_to_from_blob(self):
        vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        blob = _to_blob(vec)
        restored = _from_blob(blob)
        assert np.allclose(vec, restored)

    def test_normalized_vector(self):
        vec = np.random.randn(128).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        blob = _to_blob(vec)
        restored = _from_blob(blob)
        assert restored.shape == vec.shape
        assert np.allclose(vec, restored)


# ── State Machine 测试 ──

class TestCanonicalize:
    def test_keep_longer_existing(self):
        result = PersonaStateMachine._canonicalize(
            "用户喜欢简洁直接的代码回答，不要多余解释",
            "用户喜欢简洁代码",
        )
        assert "不要多余解释" in result

    def test_adopt_richer_new(self):
        result = PersonaStateMachine._canonicalize(
            "喜欢代码",
            "用户偏好收到完整可运行的代码而非文字解释",
        )
        assert result == "用户偏好收到完整可运行的代码而非文字解释"


class TestProcessCandidatesCreate:
    @pytest.mark.slow
    def test_create_new_clusters(self, state_machine, db_session):
        candidates = [
            {"text": "用户喜欢简洁代码", "evidence": "别废话", "domain": "编程"},
            {"text": "用户偏好命令行", "evidence": "用CLI", "domain": "工具"},
        ]
        stats = state_machine.process_candidates(candidates)
        assert stats["created"] == 2
        assert stats["merged"] == 0

        facts = db_session.query(PersonaFact).filter(
            PersonaFact.user_id == "test_user_01"
        ).all()
        assert len(facts) == 2
        assert facts[0].cluster_id is not None
        assert facts[1].cluster_id is not None
        assert facts[0].cluster_id != facts[1].cluster_id

    def test_empty_candidates(self, state_machine):
        stats = state_machine.process_candidates([])
        assert stats == {"created": 0, "merged": 0, "conflicts": 0}

    def test_skip_empty_text(self, state_machine):
        candidates = [{"text": "", "domain": "编程"}]
        stats = state_machine.process_candidates(candidates)
        assert stats["created"] == 0


class TestProcessCandidatesWithMockEmbedder:
    """用 mock embedding 测试核心状态机逻辑，不依赖真实模型加载，CI 可用。"""

    def test_create_new_clusters_mock(self, state_machine, db_session, monkeypatch):
        import numpy as np

        def mock_embed(text: str) -> np.ndarray:
            seed = sum(ord(c) for c in text) % (2**31 - 1)
            rng = np.random.RandomState(seed)
            vec = rng.randn(768).astype(np.float32)
            return vec / np.linalg.norm(vec)

        monkeypatch.setattr("core.persona_preprocess.embed_text", mock_embed)

        candidates = [
            {"text": "用户喜欢简洁代码", "evidence": "别废话", "domain": "编程"},
            {"text": "用户偏好命令行", "evidence": "用CLI", "domain": "工具"},
        ]
        stats = state_machine.process_candidates(candidates)
        assert stats["created"] == 2
        assert stats["merged"] == 0

        facts = db_session.query(PersonaFact).filter(
            PersonaFact.user_id == "test_user_01"
        ).all()
        assert len(facts) == 2
        assert facts[0].cluster_id != facts[1].cluster_id

    def test_merge_duplicate_mock(self, state_machine, db_session, monkeypatch):
        import numpy as np

        def mock_embed(text: str) -> np.ndarray:
            # 同一文本返回相同向量，相近文本返回高相似度向量
            base = np.zeros(768, dtype=np.float32)
            # "喜欢简洁代码" 类文本映射到高相似区域
            idx = hash("代码偏好") % 768
            base[idx] = 1.0
            return base / np.linalg.norm(base)

        monkeypatch.setattr("core.persona_preprocess.embed_text", mock_embed)

        c1 = [{"text": "用户喜欢简洁代码", "evidence": "ev1", "domain": "编程"}]
        state_machine.process_candidates(c1)

        c2 = [{"text": "用户偏好简短回复", "evidence": "ev2", "domain": "编程"}]
        stats = state_machine.process_candidates(c2)
        assert stats["merged"] == 1

        facts = db_session.query(PersonaFact).filter(
            PersonaFact.user_id == "test_user_01"
        ).all()
        assert len(facts) == 1
        assert facts[0].evidence_count == 2

    def test_different_domain_no_merge_mock(self, state_machine, db_session, monkeypatch):
        import numpy as np

        # 用确定的正交向量模拟不同领域，避免随机碰撞
        _vec_cache = {}

        def mock_embed(text: str) -> np.ndarray:
            if text not in _vec_cache:
                # 为每个独特性文本分配固定维度上的正交向量
                vec = np.zeros(768, dtype=np.float32)
                idx = abs(hash(text)) % 768
                vec[idx] = 1.0
                _vec_cache[text] = vec
            return _vec_cache[text]

        monkeypatch.setattr("core.persona_preprocess.embed_text", mock_embed)

        c1 = [{"text": "用户喜欢简洁代码", "evidence": "ev1", "domain": "编程"}]
        state_machine.process_candidates(c1)

        c2 = [{"text": "用户对日本历史有浓厚兴趣", "evidence": "ev2", "domain": "历史"}]
        state_machine.process_candidates(c2)

        facts = db_session.query(PersonaFact).filter(
            PersonaFact.user_id == "test_user_01"
        ).all()
        assert len(facts) == 2
        assert facts[0].cluster_id != facts[1].cluster_id


class TestProcessCandidatesMerge:
    @pytest.mark.slow
    def test_merge_semantic_duplicate(self, state_machine, db_session):
        c1 = [{"text": "用户喜欢简洁直接的代码回答", "evidence": "ev1", "domain": "编程"}]
        state_machine.process_candidates(c1)

        c2 = [{"text": "用户偏好简短代码回复，不喜欢冗长文字", "evidence": "ev2", "domain": "编程"}]
        stats = state_machine.process_candidates(c2)
        assert stats["merged"] == 1

        facts = db_session.query(PersonaFact).filter(
            PersonaFact.user_id == "test_user_01"
        ).all()
        assert len(facts) == 1
        assert facts[0].evidence_count == 2

    @pytest.mark.slow
    def test_different_domain_no_merge(self, state_machine, db_session):
        c1 = [{"text": "用户喜欢简洁代码", "evidence": "ev1", "domain": "编程"}]
        state_machine.process_candidates(c1)

        c2 = [{"text": "用户对日本历史有浓厚兴趣", "evidence": "ev2", "domain": "历史"}]
        state_machine.process_candidates(c2)

        facts = db_session.query(PersonaFact).filter(
            PersonaFact.user_id == "test_user_01"
        ).all()
        assert len(facts) == 2


class TestDecay:
    def test_old_preference_archives(self, state_machine, db_session):
        old = datetime.now() - timedelta(days=100)
        fact = PersonaFact(
            user_id="test_user_01",
            content="test",
            fact_type="preference",
            first_seen=old,
            last_seen=old,
            evidence_count=1,
            confidence="确认",
        )
        db_session.add(fact)
        db_session.commit()

        state_machine._apply_decay(datetime.now())
        # _apply_decay 修改内存对象但不由它负责 flush
        # process_candidates 会在末尾 commit
        assert fact.confidence == "归档"

    def test_recent_fact_preserved(self, state_machine, db_session):
        recent = datetime.now() - timedelta(days=2)
        fact = PersonaFact(
            user_id="test_user_01",
            content="test",
            fact_type="preference",
            first_seen=recent,
            last_seen=recent,
            evidence_count=3,
            confidence="确认",
        )
        db_session.add(fact)
        db_session.commit()

        state_machine._apply_decay(datetime.now())
        assert fact.confidence != "归档"

    def test_behavior_decays_faster(self, state_machine, db_session):
        old = datetime.now() - timedelta(days=50)
        fact = PersonaFact(
            user_id="test_user_01",
            content="test",
            fact_type="behavior",
            first_seen=old,
            last_seen=old,
            evidence_count=1,
            confidence="确认",
        )
        db_session.add(fact)
        db_session.commit()

        state_machine._apply_decay(datetime.now())
        assert fact.confidence == "归档"


class TestBuildSummary:
    def test_empty_db(self, state_machine):
        summary = state_machine.build_summary()
        assert summary == "{}"

    def test_with_facts(self, state_machine, db_session):
        now = datetime.now()
        facts = [
            PersonaFact(user_id="test_user_01", content="喜欢简洁", confidence="确认",
                        evidence_count=3, fact_type="preference",
                        first_seen=now, last_seen=now),
            PersonaFact(user_id="test_user_01", content="历史兴趣", confidence="可能",
                        evidence_count=1, fact_type="preference",
                        first_seen=now, last_seen=now),
        ]
        db_session.add_all(facts)
        db_session.commit()

        summary = state_machine.build_summary()
        data = json.loads(summary)
        assert data["count"] == 2
        assert data["facts"][0]["content"] == "喜欢简洁"

    def test_archived_excluded(self, state_machine, db_session):
        now = datetime.now()
        facts = [
            PersonaFact(user_id="test_user_01", content="喜欢简洁", confidence="确认",
                        evidence_count=3, fact_type="preference",
                        first_seen=now, last_seen=now),
            PersonaFact(user_id="test_user_01", content="过期偏好", confidence="归档",
                        evidence_count=1, fact_type="preference",
                        first_seen=now, last_seen=now),
        ]
        db_session.add_all(facts)
        db_session.commit()

        summary = state_machine.build_summary()
        data = json.loads(summary)
        assert data["count"] == 1


class TestBuildPrompt:
    def test_includes_facts_and_logs(self):
        prompt = build_candidate_extraction_prompt(
            facts_summary="喜欢简洁代码",
            logs_text="user: 给我代码别废话\nuser: 这个怎么用",
        )
        assert "喜欢简洁代码" in prompt
        assert "给我代码别废话" in prompt
        assert "去重/计数/冲突由程序处理" in prompt
        assert '"candidates"' in prompt
