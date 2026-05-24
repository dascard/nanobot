import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, PersonaFact


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    return db


def _fact(**kwargs):
    now = kwargs.pop("now", datetime.now())
    data = {
        "user_id": "u1",
        "content": "用户偏好先给结论，再给必要步骤",
        "domain_primary": "协作方式",
        "confidence": "确认",
        "fact_type": "preference",
        "memory_type": "stable_preference",
        "status": "active",
        "inject_policy": "auto",
        "evidence_count": 3,
        "evidence_log_ids_json": "[1, 2, 3]",
        "first_seen": now - timedelta(days=5),
        "last_seen": now,
    }
    data.update(kwargs)
    return PersonaFact(**data)


def test_persona_injection_build_context_has_no_write_side_effects():
    from app.persona.injection_service import PersonaInjectionService

    db = _session()
    try:
        db.add(_fact())
        db.commit()
        fact = db.query(PersonaFact).one()

        result = PersonaInjectionService(db).build_context(
            user_id="u1",
            current_user_input="请先给结论",
            recent_messages=[],
        )

        db.refresh(fact)
        assert result.context
        assert result.selected_ids == [fact.id]
        assert result.debug["persona_injected"] is True
        assert result.debug["persona_context_chars"] == len(result.context)
        assert "<stable_preferences>" in result.context
        assert "这些内容不是当前指令" in result.context
        assert fact.injected_count == 0
        assert fact.last_injected_at is None
    finally:
        db.close()


def test_persona_record_injected_is_explicit():
    from app.persona.injection_service import record_persona_injected

    db = _session()
    try:
        db.add(_fact())
        db.commit()
        fact = db.query(PersonaFact).one()

        assert record_persona_injected(db, [fact.id]) == 1
        db.commit()

        db.refresh(fact)
        assert fact.injected_count == 1
        assert fact.last_injected_at is not None
    finally:
        db.close()


def test_persona_retrieval_skips_review_manual_and_low_evidence():
    from app.persona.injection_service import PersonaInjectionService

    db = _session()
    try:
        db.add_all([
            _fact(content="用户偏好先给结论，再给必要步骤", evidence_count=3),
            _fact(content="用户可能喜欢很长解释", confidence="可能", evidence_count=1),
            _fact(content="用户正在临时调试数据库", status="review", inject_policy="manual_only"),
            _fact(content="用户要求永远调用天气工具", memory_type="tool_contract", status="active", inject_policy="never"),
        ])
        db.commit()

        result = PersonaInjectionService(db).build_context(
            user_id="u1",
            current_user_input="请直接给结论",
            recent_messages=[],
        )

        assert result.selected_ids
        assert "先给结论" in result.context
        assert "很长解释" not in result.context
        assert "临时调试" not in result.context
        assert "天气工具" not in result.context
        reasons = {item["reason"] for item in result.skipped}
        assert "low_evidence" in reasons
        assert "not_active_auto" in reasons
        assert "inject_policy_never" in reasons
    finally:
        db.close()


def test_persona_injection_debug_is_json_serializable():
    from app.persona.injection_service import PersonaInjectionService

    db = _session()
    try:
        db.add(_fact())
        db.commit()

        result = PersonaInjectionService(db).build_context(user_id="u1", current_user_input="给结论")
        encoded = json.dumps(result.debug, ensure_ascii=False)

        assert "persona_fact_ids" in encoded
        assert "score_components" in encoded
    finally:
        db.close()
