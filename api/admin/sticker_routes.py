"""Admin Sticker 与 Generated Images 路由。"""

from __future__ import annotations

import json
import logging
import threading
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.admin.common import audit_request as _audit_request
from api.admin.common import verify_admin
from core.database import StickerMemory, get_db

logger = logging.getLogger("nanobot.admin")
router = APIRouter(tags=["admin-sticker"])


class StickerCreate(BaseModel):
    group_id: str = ""
    chat_stream_id: str = ""
    file_ref: str
    sticker_hash: str = ""
    send_code: str = ""
    name: str = ""
    description: str = ""
    tags: list[str] = []
    emotions: list[str] = []
    status: Literal["active", "disabled"] = "active"


class StickerUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    emotions: Optional[list[str]] = None
    status: Optional[Literal["active", "disabled", "deleted"]] = None


class GeneratedImageCreate(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    size: Literal["1024x1024", "1024x1536", "1536x1024", "auto"] = "1024x1024"
    quality: Literal["low", "medium", "high", "auto"] = "high"
    background: Literal["auto", "transparent", "opaque"] = "auto"


def _safe_json(raw):
    try:
        return json.loads(raw or "[]")
    except Exception:
        return []


def _iso(v) -> str:
    return v.isoformat(sep=" ", timespec="seconds") if v else ""


def _sticker_dict(r: StickerMemory) -> dict:
    return {
        "id": r.id, "chat_stream_id": r.chat_stream_id,
        "sticker_hash": r.sticker_hash, "file_ref": r.file_ref,
        "name": r.name, "description": r.description,
        "tags": _safe_json(r.tags_json),
        "emotions": _safe_json(r.emotions_json),
        "source_type": r.source_type, "source_count": r.source_count,
        "status": r.status, "usage_count": r.usage_count,
        "first_seen": str(r.first_seen) if r.first_seen else "",
        "last_seen": str(r.last_seen) if r.last_seen else "",
        "last_used": str(r.last_used) if r.last_used else "",
        "local_path": r.local_path or "",
        "preview_status": r.preview_status or "pending",
        "content_hash": r.content_hash or "",
        "byte_size": r.byte_size or 0,
        "width": r.width or 0,
        "height": r.height or 0,
        "duplicate_of_id": r.duplicate_of_id,
        "dedupe_status": r.dedupe_status or "unique",
        "describe_status": r.describe_status or "pending",
        "describe_attempts": r.describe_attempts or 0,
        "describe_last_error": r.describe_last_error or "",
        "described_at": str(r.described_at) if r.described_at else "",
    }


@router.post("/stickers")
def create_sticker(body: StickerCreate, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.sticker_memory import register_sticker
    try:
        sticker = register_sticker(
            db,
            group_id=body.group_id,
            chat_stream_id=body.chat_stream_id,
            file_ref=body.file_ref,
            sticker_hash=body.sticker_hash,
            send_code=body.send_code,
            name=body.name,
            description=body.description,
            tags=body.tags,
            emotions=body.emotions,
            source_type="manual",
            status=body.status,
            meta={"source": "webui"},
        )
    except Exception as e:
        raise HTTPException(400, str(e))
    _audit_request(db, request, "create_sticker", "sticker", sticker.get("id"), {
        "name": body.name, "status": body.status,
        "stream_id": sticker.get("chat_stream_id", ""),
        "description": body.description[:80] if body.description else "",
        "tags": body.tags[:5],
    })
    return sticker


@router.get("/stickers")
def list_stickers(
    search: str = "", status: str = "", page: int = 1, limit: int = 20,
    preview_status: str = "", describe_status: str = "",
    dedupe_status: str = "", failure: str = "",
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    q = db.query(StickerMemory)
    if search:
        q = q.filter(
            StickerMemory.name.contains(search) |
            StickerMemory.description.contains(search)
        )
    if status:
        q = q.filter(StickerMemory.status == status)
    if preview_status:
        q = q.filter(StickerMemory.preview_status == preview_status)
    if describe_status:
        q = q.filter(StickerMemory.describe_status == describe_status)
    if dedupe_status:
        q = q.filter(StickerMemory.dedupe_status == dedupe_status)
    if failure == "preview_failed":
        q = q.filter(StickerMemory.preview_status.notin_(["ok", "pending", ""]))
    elif failure == "describe_failed":
        q = q.filter(StickerMemory.describe_status == "failed")
    elif failure == "unlabeled":
        q = q.filter(StickerMemory.describe_status.in_(["pending", "failed"]))
    elif failure == "duplicate":
        q = q.filter(
            (StickerMemory.dedupe_status == "duplicate") |
            (StickerMemory.status == "duplicate")
        )
    total = q.count()
    rows = q.order_by(StickerMemory.id.desc()).offset((page - 1) * limit).limit(limit).all()
    return {"total": total, "page": page, "items": [_sticker_dict(r) for r in rows]}


@router.get("/generated-images")
def list_generated_images(
    page: int = 1,
    limit: int = 20,
    search: str = "",
    _auth=Depends(verify_admin),
):
    from core.generated_images import list_generated_images as _list_generated_images

    data = _list_generated_images(page=page, limit=limit, search=search)
    for item in data["items"]:
        item["image_url"] = f"/api/v1/admin/generated-images/{item['id']}/image"
    return data


@router.post("/generated-images")
async def create_generated_image(
    body: GeneratedImageCreate,
    _auth=Depends(verify_admin),
):
    from core.generated_images import GENERATED_IMAGE_REF_PATTERN, get_generated_image
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    prompt = str(body.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is required")

    result = await ImageGenerationTool().execute({
        "prompt": prompt,
        "size": body.size,
        "quality": body.quality,
        "background": body.background,
    })
    if not result.success:
        error = str(getattr(result, "error", "") or "image generation failed")
        raise HTTPException(status_code=502, detail=error)

    try:
        payload = json.loads(result.output or "{}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"invalid image generation output: {exc}")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="invalid image generation output")

    reply_token = str(payload.get("reply_token") or "")
    match = GENERATED_IMAGE_REF_PATTERN.search(reply_token)
    if not match:
        raise HTTPException(status_code=500, detail="image generation output missing reply_token")

    try:
        item = get_generated_image(match.group(1))
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="generated image file not found")
    item["image_url"] = f"/api/v1/admin/generated-images/{item['id']}/image"
    return {
        "ok": True,
        "item": item,
        "tool_output": {
            "reply_token": reply_token,
            "mime": payload.get("mime") or item.get("mime") or "image/png",
            "model": payload.get("model") or item.get("model") or "",
            "size": payload.get("size") or item.get("size") or "",
            "quality": payload.get("quality") or item.get("quality") or "",
            "background": payload.get("background") or item.get("background") or "",
            "text_output": payload.get("text_output") or "",
            "revised_prompt": payload.get("revised_prompt") or "",
        },
    }


@router.get("/generated-images/{image_id}/image")
def generated_image_file(image_id: str, _auth=Depends(verify_admin)):
    from core.generated_images import get_generated_image_path

    try:
        path = get_generated_image_path(image_id)
    except FileNotFoundError:
        raise HTTPException(404, "generated image not found")
    return FileResponse(path, media_type="image/png")


@router.get("/stickers/duplicate-groups")
def sticker_duplicate_groups(limit: int = 50, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    rows = db.execute(text("""
        SELECT content_hash, COUNT(*) AS n
        FROM sticker_memories
        WHERE content_hash IS NOT NULL AND content_hash != ''
        GROUP BY content_hash
        HAVING n > 1
        ORDER BY n DESC
        LIMIT :limit
    """), {"limit": max(1, min(limit, 200))}).fetchall()
    groups = []
    for content_hash, n in rows:
        stickers = (
            db.query(StickerMemory)
            .filter(StickerMemory.content_hash == content_hash)
            .order_by(StickerMemory.status.asc(), StickerMemory.usage_count.desc(), StickerMemory.id.asc())
            .all()
        )
        # canonical: active, 非 duplicate, duplicate_of_id 为空
        canonical = next((r for r in stickers if r.status == "active"
                         and r.dedupe_status != "duplicate"
                         and not r.duplicate_of_id), None)
        groups.append({
            "content_hash": content_hash,
            "count": n,
            "canonical_id": canonical.id if canonical else None,
            "needs_canonical": canonical is None,
            "items": [_sticker_dict(r) for r in stickers],
        })
    return {"groups": groups}


@router.get("/stickers/{sticker_id:int}")
def get_sticker(sticker_id: int, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    return _sticker_dict(row)


@router.put("/stickers/{sticker_id}")
def update_sticker(sticker_id: int, body: StickerUpdate, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    updates = {}
    for field, attr in [("name", "name"), ("description", "description"), ("status", "status")]:
        val = getattr(body, field, None)
        if val is not None:
            setattr(row, attr, val); updates[field] = val
    if body.tags is not None:
        row.tags_json = json.dumps(body.tags, ensure_ascii=False); updates["tags"] = body.tags
    if body.emotions is not None:
        row.emotions_json = json.dumps(body.emotions, ensure_ascii=False); updates["emotions"] = body.emotions
    db.commit()
    _audit_request(db, request, "update_sticker", "sticker", sticker_id, updates)
    return _sticker_dict(row)


@router.post("/stickers/{sticker_id}/enable")
def enable_sticker(sticker_id: int, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    if row.dedupe_status == "duplicate":
        raise HTTPException(400, "duplicate sticker cannot be enabled directly")
    row.status = "active"; db.commit()
    _audit_request(db, request, "enable_sticker", "sticker", sticker_id)
    return {"ok": True}


@router.post("/stickers/{sticker_id}/disable")
def disable_sticker(sticker_id: int, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    row.status = "disabled"; db.commit()
    _audit_request(db, request, "disable_sticker", "sticker", sticker_id)
    return {"ok": True}


@router.get("/stickers/{sticker_id}/preview")
def preview_sticker(sticker_id: int, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from fastapi.responses import FileResponse
    from core.sticker_preview import (
        cache_sticker_preview, media_type_for_path, safe_existing_local_path,
    )

    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")

    existing = safe_existing_local_path(row.local_path or "")
    if existing:
        return FileResponse(existing, media_type=media_type_for_path(existing))

    result = cache_sticker_preview(db, sticker_id)
    if result.ok and result.local_path:
        return FileResponse(result.local_path, media_type=media_type_for_path(result.local_path))

    status_code = 400 if result.status == "blocked" else 404
    raise HTTPException(status_code, f"preview {result.status}: {result.error}")


@router.post("/stickers/{sticker_id}/redescribe")
def redescribe_sticker(sticker_id: int, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.sticker_memory import auto_describe_sticker

    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    try:
        auto_describe_sticker(sticker_id, force=True)
        db.refresh(row)
        ok = row.describe_status == "ok"
        _audit_request(db, request, "redescribe_sticker", "sticker", sticker_id)
        return {"ok": ok, "describe_status": row.describe_status, "description": row.description or "",
                "error": row.describe_last_error if not ok else ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/stickers/{sticker_id}/preview/retry")
def retry_preview(sticker_id: int, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.sticker_preview import cache_sticker_preview

    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    row.preview_status = "pending"
    row.local_path = ""
    db.commit()

    result = cache_sticker_preview(db, sticker_id, force=True)
    return {
        "ok": result.ok,
        "status": result.status,
        "error": result.error,
        "sticker_id": sticker_id,
    }


@router.post("/stickers/dedupe/exact/backfill")
def stickers_dedupe_backfill(
    request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    from core.sticker_preview import backfill_exact_dedupe
    result = backfill_exact_dedupe(db)
    _audit_request(db, request, "sticker.dedupe.backfill", "sticker", "", result)
    return result


@router.get("/stickers/near-duplicate-candidates")
def list_near_duplicate_candidates(
    limit: int = 50, db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    from core.database import StickerDuplicateCandidate, StickerMemory as SM
    rows = (
        db.query(StickerDuplicateCandidate)
        .filter(StickerDuplicateCandidate.status == "pending")
        .order_by((StickerDuplicateCandidate.phash_dist + StickerDuplicateCandidate.dhash_dist).asc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    items = []
    for r in rows:
        a = db.query(SM).filter(SM.id == r.sticker_a_id).first()
        b = db.query(SM).filter(SM.id == r.sticker_b_id).first()
        if a and b:
            items.append({
                "id": r.id,
                "sticker_a": _sticker_dict(a),
                "sticker_b": _sticker_dict(b),
                "phash_dist": r.phash_dist,
                "dhash_dist": r.dhash_dist,
                "content_hash": r.content_hash,
                "status": r.status,
                "created_at": _iso(r.created_at),
            })
    return {"items": items, "total": len(rows)}


_NEAR_DUP_SCAN_LOCK = threading.Lock()


@router.post("/stickers/near-duplicate/scan")
def scan_near_duplicates_endpoint(
    request: Request, limit: int = 100,
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    if not _NEAR_DUP_SCAN_LOCK.acquire(blocking=False):
        raise HTTPException(409, "扫描正在进行中，请稍后再试")
    try:
        from core.sticker_preview import scan_near_duplicates
        result = scan_near_duplicates(db, limit=min(limit, 500))
        _audit_request(db, request, "sticker.near_duplicate.scan", "sticker", "", result)
        return result
    except Exception as e:
        logger.exception("scan near duplicates failed")
        raise HTTPException(500, f"扫描失败: {str(e)[:300]}")
    finally:
        _NEAR_DUP_SCAN_LOCK.release()


@router.post("/stickers/phash/backfill")
def backfill_phash_endpoint(
    request: Request, limit: int = 200,
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    from core.sticker_preview import backfill_phash
    result = backfill_phash(db, limit=min(limit, 1000))
    _audit_request(db, request, "sticker.phash.backfill", "sticker", "", result)
    return result


class NearDuplicateAction(BaseModel):
    action: str = "ignore"  # ignore or confirm
    canonical_id: int = 0


@router.post("/stickers/near-duplicate-candidates/{candidate_id}/{action}")
def update_near_duplicate_candidate(
    candidate_id: int, action: str, request: Request,
    body: NearDuplicateAction = NearDuplicateAction(),
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    from core.database import StickerDuplicateCandidate, StickerMemory as SM

    row = db.query(StickerDuplicateCandidate).filter(StickerDuplicateCandidate.id == candidate_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    if row.status != "pending":
        raise HTTPException(400, f"candidate already {row.status}")
    if action == "ignore":
        row.status = "ignored"
        db.commit()
        _audit_request(db, request, "sticker.near_duplicate.ignore", "sticker", str(candidate_id))
        return {"ok": True, "status": row.status}

    if action != "confirm":
        raise HTTPException(400, "action must be ignore or confirm")

    # 确认疑似重复：将 sticker_b 标记为 sticker_a 的 duplicate
    canonical_id = body.canonical_id or row.sticker_a_id
    if canonical_id not in {row.sticker_a_id, row.sticker_b_id}:
        raise HTTPException(400, "canonical_id must be sticker_a_id or sticker_b_id")
    dup_id = row.sticker_b_id if canonical_id == row.sticker_a_id else row.sticker_a_id

    canonical = db.query(SM).filter(SM.id == canonical_id).first()
    dup = db.query(SM).filter(SM.id == dup_id).first()
    if not canonical or not dup:
        raise HTTPException(404, "sticker not found")
    if canonical.status == "duplicate" or canonical.dedupe_status == "duplicate" or canonical.duplicate_of_id:
        raise HTTPException(400, "canonical is itself duplicate")

    dup.status = "duplicate"
    dup.dedupe_status = "duplicate"
    dup.duplicate_of_id = canonical.id

    import json as _json
    try:
        meta = _json.loads(dup.meta_json or "{}")
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    meta["dedupe_reason"] = "near_duplicate"
    meta["near_duplicate_candidate_id"] = candidate_id
    meta["phash_dist"] = row.phash_dist
    meta["dhash_dist"] = row.dhash_dist
    dup.meta_json = _json.dumps(meta, ensure_ascii=False)

    row.status = "confirmed"
    db.commit()
    _audit_request(db, request, "sticker.near_duplicate.confirm", "sticker",
                   str(dup_id), {"canonical_id": canonical_id, "candidate_id": candidate_id})
    return {"ok": True, "status": "confirmed", "duplicate_id": dup_id}


class SetCanonicalBody(BaseModel):
    activate: bool = Field(default=True)


@router.post("/stickers/{sticker_id}/set-canonical")
def sticker_set_canonical(
    sticker_id: int, request: Request, body: SetCanonicalBody = SetCanonicalBody(),
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    from core.sticker_preview import dedupe_by_content_hash

    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    if not row.content_hash:
        raise HTTPException(400, "no content_hash")

    if body.activate and row.status in ("duplicate", "disabled"):
        row.status = "active"
        # 不 commit，让 dedupe_by_content_hash 统一提交

    canonical_id = dedupe_by_content_hash(db, sticker_id, force_set_canonical=sticker_id)
    _audit_request(db, request, "sticker.set_canonical", "sticker", str(sticker_id),
                   {"canonical_id": canonical_id})
    return {"ok": True, "canonical_id": canonical_id}


class MarkDuplicateBody(BaseModel):
    canonical_id: int = Field(default=0)


@router.post("/stickers/{sticker_id}/mark-duplicate")
def sticker_mark_duplicate(
    sticker_id: int, request: Request, body: MarkDuplicateBody = MarkDuplicateBody(),
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    if not body.canonical_id:
        raise HTTPException(400, "canonical_id required")

    canonical = db.query(StickerMemory).filter(StickerMemory.id == body.canonical_id).first()
    if not canonical:
        raise HTTPException(404, "canonical not found")
    if canonical.id == sticker_id:
        raise HTTPException(400, "cannot mark self as duplicate")
    if canonical.content_hash != row.content_hash:
        raise HTTPException(400, "content_hash mismatch")
    if canonical.status == "duplicate" or canonical.dedupe_status == "duplicate" or canonical.duplicate_of_id:
        raise HTTPException(400, "canonical is itself a duplicate — 不能形成链式重复")

    row.status = "duplicate"
    row.dedupe_status = "duplicate"
    row.duplicate_of_id = canonical.id
    db.commit()
    _audit_request(db, request, "sticker.mark_duplicate", "sticker", str(sticker_id),
                   {"canonical_id": canonical.id})
    return {"ok": True}


@router.post("/stickers/batch-delete")
def batch_delete_stickers(body: dict, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    raw = body.get("ids", [])
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "ids required")
    ids: set[int] = set()
    for x in raw:
        try:
            ids.add(int(x))
        except (ValueError, TypeError):
            continue
    if not ids:
        raise HTTPException(400, "no valid ids")
    if len(ids) > 500:
        raise HTTPException(400, f"too many ids, max 500")
    rows = db.query(StickerMemory).filter(StickerMemory.id.in_(list(ids))).all()
    count = 0
    for row in rows:
        if row.status != "deleted":
            row.status = "deleted"
            count += 1
    db.commit()
    _audit_request(db, request, "batch_delete_stickers", "sticker", f"batch_{len(ids)}", {
        "count": count, "ids_sample": sorted(ids)[:50],
    })
    return {"ok": True, "deleted": count}


@router.delete("/stickers/{sticker_id}")
def delete_sticker(sticker_id: int, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    row.status = "deleted"; db.commit()
    _audit_request(db, request, "soft_delete_sticker", "sticker", sticker_id)
    return {"ok": True}
