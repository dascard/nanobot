"""用户画像治理 Admin API。"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.admin.common import audit_request, verify_admin
from core.database import ChatLog, Persona, PersonaFact, User, get_db
from core.persona_preprocess import (
    CANDIDATE_EXTRACTION_SYSTEM_PROMPT,
    PersonaStateMachine,
    build_candidate_extraction_prompt,
    content_hash,
    format_candidate_logs,
)
from core.time_utils import db_now_naive


router = APIRouter(prefix="/persona", tags=["admin-persona"])


def _safe_json(value: str | None, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except Exception:
        return fallback
    return parsed


def _extract_llm_message_content(resp: dict[str, Any]) -> Any:
    choices = resp.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    return message.get("content")


def _fact_dict(row: PersonaFact) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "domain_primary": row.domain_primary,
        "content": row.content,
        "evidence_count": row.evidence_count,
        "source_log_ids": _safe_json(row.source_log_ids, []),
        "evidence_log_ids": _safe_json(row.evidence_log_ids_json, []),
        "evidence_log_ids_json": row.evidence_log_ids_json or "[]",
        "first_seen": row.first_seen.isoformat() if row.first_seen else None,
        "last_seen": row.last_seen.isoformat() if row.last_seen else None,
        "confidence": row.confidence,
        "fact_type": row.fact_type,
        "memory_type": row.memory_type,
        "status": row.status,
        "inject_policy": row.inject_policy,
        "content_hash": row.content_hash,
        "disabled_reason": row.disabled_reason,
        "rejected_reason": row.rejected_reason,
        "candidate_meta": _safe_json(row.candidate_meta_json, {}),
        "last_injected_at": row.last_injected_at.isoformat() if row.last_injected_at else None,
        "injected_count": int(row.injected_count or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class PersonaFactUpdateRequest(BaseModel):
    content: str | None = None
    status: str | None = Field(default=None, pattern="^(review|active|disabled|archived|rejected)$")
    inject_policy: str | None = Field(default=None, pattern="^(auto|manual_only|never)$")
    memory_type: Literal[
        "stable_preference",
        "interaction_style",
        "stable_background",
        "long_term_project",
    ] | None = None
    confidence: str | None = Field(default=None, pattern="^(确认|可能|待确认|归档)$")
    disabled_reason: str | None = None
    rejected_reason: str | None = None


class PersonaInjectionPreviewRequest(BaseModel):
    user_input: str = ""
    max_items: int = Field(default=6, ge=1, le=20)
    max_chars: int = Field(default=900, ge=100, le=4000)


class PersonaExtractRequest(BaseModel):
    window_hours: int = Field(default=168, ge=0, le=24 * 180)
    limit: int = Field(default=80, ge=1, le=300)
    instructions: str = ""


@router.get("/users")
def persona_users(
    q: str = "",
    limit: int = Query(default=80, ge=1, le=300),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    user_ids: set[str] = set()
    if q:
        rows = (
            db.query(User.id)
            .filter(User.id.contains(q))
            .order_by(User.created_at.desc())
            .limit(limit)
            .all()
        )
        user_ids.update(str(row[0]) for row in rows)
    else:
        user_ids.update(str(row[0]) for row in db.query(Persona.user_id).limit(limit).all())
        user_ids.update(str(row[0]) for row in db.query(PersonaFact.user_id).limit(limit).all())
        user_ids.update(str(row[0]) for row in db.query(ChatLog.user_id).order_by(ChatLog.id.desc()).limit(limit).all())

    items = []
    for uid in list(dict.fromkeys(user_ids))[:limit]:
        fact_count = db.query(PersonaFact).filter(PersonaFact.user_id == uid).count()
        injectable_count = db.query(PersonaFact).filter(
            PersonaFact.user_id == uid,
            PersonaFact.status == "active",
            PersonaFact.inject_policy == "auto",
        ).count()
        latest = (
            db.query(PersonaFact)
            .filter(PersonaFact.user_id == uid)
            .order_by(PersonaFact.last_seen.desc())
            .first()
        )
        user = db.query(User).filter(User.id == uid).first()
        items.append({
            "user_id": uid,
            "name": user.name if user else "",
            "fact_count": fact_count,
            "injectable_count": injectable_count,
            "latest_fact_at": latest.last_seen.isoformat() if latest and latest.last_seen else None,
        })
    items.sort(key=lambda x: (x["fact_count"], x["latest_fact_at"] or ""), reverse=True)
    return {"items": items, "total": len(items)}


@router.get("/users/{user_id}/facts")
def persona_user_facts(
    user_id: str,
    status: str = "",
    memory_type: str = "",
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    q = db.query(PersonaFact).filter(PersonaFact.user_id == user_id)
    if status:
        q = q.filter(PersonaFact.status == status)
    if memory_type:
        q = q.filter(PersonaFact.memory_type == memory_type)
    rows = q.order_by(PersonaFact.evidence_count.desc(), PersonaFact.last_seen.desc(), PersonaFact.id.desc()).limit(300).all()
    return {"user_id": user_id, "items": [_fact_dict(row) for row in rows], "total": len(rows)}


@router.patch("/facts/{fact_id}")
def persona_update_fact(
    fact_id: int,
    body: PersonaFactUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    row = db.query(PersonaFact).filter(PersonaFact.id == fact_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="persona fact not found")

    updates: dict[str, Any] = {}
    target_content = row.content
    target_memory_type = row.memory_type
    target_hash = row.content_hash or content_hash(row.content or "")
    if body.content is not None or body.memory_type is not None:
        new_content = body.content.strip() if body.content is not None else str(row.content or "").strip()
        if not new_content:
            raise HTTPException(status_code=400, detail="content is empty")
        new_memory_type = body.memory_type or row.memory_type
        new_hash = content_hash(new_content)
        duplicate = db.query(PersonaFact).filter(
            PersonaFact.id != row.id,
            PersonaFact.user_id == row.user_id,
            PersonaFact.memory_type == new_memory_type,
            PersonaFact.content_hash == new_hash,
        ).first()
        if duplicate:
            raise HTTPException(status_code=409, detail="已有相同画像，可合并或归档")
        target_content = new_content
        target_memory_type = new_memory_type
        target_hash = new_hash
    if body.content is not None:
        row.content = target_content
        row.content_hash = target_hash
        updates["content"] = target_content
    if body.memory_type is not None:
        row.memory_type = target_memory_type
        row.content_hash = target_hash
        updates["memory_type"] = target_memory_type
    for field in ("status", "inject_policy", "confidence", "disabled_reason", "rejected_reason"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
            updates[field] = value
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="已有相同画像，可合并或归档") from exc
    audit_request(db, request, "update_persona_fact", "persona_fact", str(fact_id), updates)
    db.refresh(row)
    return {"fact": _fact_dict(row)}


@router.post("/users/{user_id}/injection-preview")
def persona_injection_preview(
    user_id: str,
    body: PersonaInjectionPreviewRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from app.persona.injection_service import PersonaInjectionService

    result = PersonaInjectionService(db).build_context(
        user_id=user_id,
        current_user_input=body.user_input,
        recent_messages=[],
        max_items=body.max_items,
        max_chars=body.max_chars,
    )
    return {
        "user_id": user_id,
        "persona_context": result.context,
        "persona_fact_ids": result.selected_ids,
        "persona_skipped": result.skipped,
        "persona_context_chars": result.debug.get("persona_context_chars", 0),
        "score_components": result.score_components,
        "debug": result.debug,
    }


@router.post("/users/{user_id}/extract")
async def persona_extract_user(
    user_id: str,
    body: PersonaExtractRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    cutoff = None if body.window_hours <= 0 else db_now_naive() - timedelta(hours=body.window_hours)
    q = db.query(ChatLog).filter(ChatLog.user_id == user_id, ChatLog.role == "user")
    if cutoff is not None:
        q = q.filter(ChatLog.created_at >= cutoff)
    logs = q.order_by(ChatLog.id.desc()).limit(body.limit).all()
    logs.reverse()
    if not logs:
        return {"user_id": user_id, "raw_count": 0, "stats": {"created": 0, "merged": 0, "conflicts": 0, "rejected": 0}}

    log_dicts = [
        {
            "id": row.id,
            "role": row.role,
            "content": row.content,
            "created_at": row.created_at.isoformat() if row.created_at else "",
        }
        for row in logs
    ]
    sm = PersonaStateMachine(db, user_id)
    facts_summary = sm.build_summary()
    logs_text = format_candidate_logs(log_dicts)
    instructions = body.instructions.strip()
    prompt = build_candidate_extraction_prompt(
        facts_summary,
        f"{logs_text}\n\n## 管理员提取指引\n{instructions}" if instructions else logs_text,
    )

    from clients.new_api_client import NewAPIClient
    from clients.classifier_client import resolve_model_route
    from config import NEW_API_BASE_URL, NEW_API_KEY
    from core.legacy_adapter import EvolutionUtils

    route = resolve_model_route("fast")
    client = NewAPIClient(
        api_key=route.get("api_key") or NEW_API_KEY,
        base_url=route.get("base_url") or NEW_API_BASE_URL,
    )
    resp = await client.chat_completion(
        messages=[
            {"role": "system", "content": CANDIDATE_EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        manual_model=str(route.get("model") or ""),
        temperature=0.1,
        max_tokens=1600,
        llm_source="persona_extract_admin",
        enable_thinking=False,
    )
    if not isinstance(resp, dict) or "choices" not in resp:
        raise HTTPException(status_code=502, detail=str(resp.get("error", resp))[:300] if isinstance(resp, dict) else "LLM failed")
    raw = _extract_llm_message_content(resp)
    parsed = EvolutionUtils.json_repair(raw)
    if not isinstance(parsed, dict) or parsed.get("parse_error"):
        raise HTTPException(status_code=502, detail="画像提取 LLM 返回空内容或非 JSON")
    candidates = parsed.get("candidates", []) if isinstance(parsed, dict) else []
    stats = sm.process_candidates(candidates)
    persona_summary = sm.build_summary()
    persona = db.query(Persona).filter(Persona.user_id == user_id).first()
    if persona:
        persona.persona_json = persona_summary
        if str(persona.status or "") == "archived":
            persona.status = "review"
        persona.updated_at = db_now_naive()
    else:
        db.add(Persona(user_id=user_id, persona_json=persona_summary))
    db.commit()
    audit_request(db, request, "extract_persona", "persona", user_id, {"stats": stats, "raw_count": len(logs)})
    return {
        "user_id": user_id,
        "raw_count": len(logs),
        "candidate_count": len(candidates),
        "stats": stats,
        "summary": persona_summary,
    }
