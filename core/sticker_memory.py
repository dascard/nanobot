"""表情包记忆——注册、检索和 Qwen 后台描述补全。"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import mimetypes
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from config import STICKER_AUTO_DESCRIBE_ENABLED
from core.database import SessionLocal, StickerMemory
from core.group_runtime.ids import normalize_group_stream_id

logger = logging.getLogger("nanobot.sticker_memory")

GLOBAL_STICKER_STREAM_ID = "global"
ACTIVE_STATUS = "active"
DEFAULT_AUTO_STATUS = "active"
CQ_IMAGE_PATTERN = re.compile(r"\[CQ:image,[^\]]*file=([^,\]]+)[^\]]*\]")
STICKER_REF_PATTERN = re.compile(r"\[sticker:(\d+)\]")


def _json_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result[:20]


def _loads_list(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except Exception:
        return []
    return _json_list(parsed)


def _dumps_list(values: Any) -> str:
    return json.dumps(_json_list(values), ensure_ascii=False)


def normalize_sticker_stream_id(group_id: str = "", chat_stream_id: str = "") -> str:
    if chat_stream_id:
        value = str(chat_stream_id).strip()
        if value == GLOBAL_STICKER_STREAM_ID:
            return value
        return normalize_group_stream_id(value)
    if group_id:
        return normalize_group_stream_id(group_id)
    return GLOBAL_STICKER_STREAM_ID


def build_sticker_hash(
    file_ref: str,
    *,
    sticker_hash: str = "",
    description: str = "",
) -> str:
    explicit = str(sticker_hash or "").strip()
    if explicit:
        return explicit
    ref = normalize_sticker_file_ref(file_ref)
    return hashlib.sha256(ref.encode("utf-8")).hexdigest()[:32]


def _cq_escape(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
    )


def normalize_sticker_file_ref(file_ref: str) -> str:
    return html.unescape(str(file_ref or "").strip())


def build_sticker_send_code(file_ref: str, send_code: str = "") -> str:
    explicit = str(send_code or "").strip()
    if explicit:
        match = CQ_IMAGE_PATTERN.fullmatch(explicit)
        if not match:
            return explicit
        ref = normalize_sticker_file_ref(_cq_unescape(match.group(1).strip()))
        return f"[CQ:image,file={_cq_escape(ref)}]"
    ref = normalize_sticker_file_ref(file_ref)
    if ref.startswith("[CQ:image,"):
        match = CQ_IMAGE_PATTERN.fullmatch(ref)
        if not match:
            return ref
        inner_ref = normalize_sticker_file_ref(_cq_unescape(match.group(1).strip()))
        return f"[CQ:image,file={_cq_escape(inner_ref)}]"
    return f"[CQ:image,file={_cq_escape(ref)}]"


def _cq_unescape(value: str) -> str:
    return html.unescape(str(value or ""))


def extract_sticker_send_codes(content: str) -> list[str]:
    codes: list[str] = []
    for match in CQ_IMAGE_PATTERN.finditer(str(content or "")):
        file_ref = normalize_sticker_file_ref(_cq_unescape(match.group(1).strip()))
        if not file_ref:
            continue
        code = build_sticker_send_code(file_ref)
        if code not in codes:
            codes.append(code)
    return codes


def _canonical_row_send_code(row: StickerMemory) -> str:
    return build_sticker_send_code(row.file_ref or "", row.send_code or "")


def sticker_to_dict(row: StickerMemory) -> dict[str, Any]:
    return {
        "id": row.id,
        "chat_stream_id": row.chat_stream_id,
        "sticker_hash": row.sticker_hash,
        "file_ref": row.file_ref,
        "send_code": _canonical_row_send_code(row),
        "reply_token": f"[sticker:{row.id}]",
        "name": row.name or "",
        "description": row.description or "",
        "tags": _loads_list(row.tags_json),
        "emotions": _loads_list(row.emotions_json),
        "source_type": row.source_type or "",
        "source_count": int(row.source_count or 0),
        "status": row.status or "",
        "usage_count": int(row.usage_count or 0),
        "last_used": row.last_used.isoformat(timespec="seconds") if row.last_used else None,
        "dedupe_status": row.dedupe_status or "unique",
        "duplicate_of_id": row.duplicate_of_id,
        "content_hash": row.content_hash or "",
        "describe_status": row.describe_status or "pending",
    }


def register_sticker(
    db: Session,
    *,
    chat_stream_id: str = "",
    group_id: str = "",
    file_ref: str,
    sticker_hash: str = "",
    send_code: str = "",
    name: str = "",
    description: str = "",
    tags: Any = None,
    emotions: Any = None,
    source_type: str = "manual",
    status: str = ACTIVE_STATUS,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stream_id = normalize_sticker_stream_id(group_id=group_id, chat_stream_id=chat_stream_id)
    ref = normalize_sticker_file_ref(file_ref)
    if not ref:
        raise ValueError("file_ref 不能为空")
    stable_hash = build_sticker_hash(ref, sticker_hash=sticker_hash, description=description)
    now = datetime.now()

    row = (
        db.query(StickerMemory)
        .filter(
            StickerMemory.chat_stream_id == stream_id,
            StickerMemory.sticker_hash == stable_hash,
        )
        .first()
    )
    if row is None:
        row = StickerMemory(
            chat_stream_id=stream_id,
            sticker_hash=stable_hash,
            file_ref=ref,
            send_code=build_sticker_send_code(ref, send_code),
            name=str(name or "").strip(),
            description=str(description or "").strip(),
            tags_json=_dumps_list(tags),
            emotions_json=_dumps_list(emotions),
            source_type=str(source_type or "manual").strip() or "manual",
            status=str(status or ACTIVE_STATUS).strip() or ACTIVE_STATUS,
            source_count=1,
            first_seen=now,
            last_seen=now,
            meta_json=json.dumps(meta or {}, ensure_ascii=False),
        )
        db.add(row)
    else:
        row.last_seen = now
        row.source_count = int(row.source_count or 0) + 1

        old_ref = row.file_ref or ""
        if ref and ref != old_ref:
            row.file_ref = ref
            row.send_code = build_sticker_send_code(ref, send_code)
            # 新 URL 到来：已有本地缓存不动，失败/过期则重置重试
            if row.preview_status != "ok" or not row.local_path:
                row.preview_status = "pending"
                row.local_path = ""
        else:
            row.file_ref = ref or row.file_ref
            row.send_code = build_sticker_send_code(ref, send_code) if ref else row.send_code
        if name:
            row.name = str(name).strip()
        if description:
            row.description = str(description).strip()
        if tags is not None:
            row.tags_json = _dumps_list(tags)
        if emotions is not None:
            row.emotions_json = _dumps_list(emotions)
        if source_type:
            row.source_type = str(source_type).strip()
        if status:
            row.status = str(status).strip()
        if meta:
            existing_meta = {}
            try:
                existing_meta = json.loads(row.meta_json or "{}")
            except Exception:
                existing_meta = {}
            existing_meta.update(meta)
            row.meta_json = json.dumps(existing_meta, ensure_ascii=False)

    db.commit()
    db.refresh(row)
    return sticker_to_dict(row)


def _score_row(row: StickerMemory, query: str, chat_stream_id: str) -> int:
    if not query:
        return int(row.usage_count or 0)
    text_parts = [
        row.name or "",
        row.description or "",
        " ".join(_loads_list(row.tags_json)),
        " ".join(_loads_list(row.emotions_json)),
    ]
    haystack = " ".join(text_parts).lower()
    q = query.lower()
    score = 0
    if row.chat_stream_id == chat_stream_id:
        score += 10
    if q and q in (row.name or "").lower():
        score += 8
    if q and q in (row.description or "").lower():
        score += 6
    for item in _loads_list(row.tags_json) + _loads_list(row.emotions_json):
        item_l = item.lower()
        if q == item_l:
            score += 7
        elif q and q in item_l:
            score += 4
    for token in q.split():
        if token and token in haystack:
            score += 2
    score += min(5, int(row.usage_count or 0))
    return score


def search_stickers(
    db: Session,
    query: str = "",
    *,
    chat_stream_id: str = "",
    group_id: str = "",
    limit: int = 5,
    include_global: bool = True,
) -> list[dict[str, Any]]:
    stream_id = normalize_sticker_stream_id(group_id=group_id, chat_stream_id=chat_stream_id)
    scopes = [stream_id]
    if include_global and GLOBAL_STICKER_STREAM_ID not in scopes:
        scopes.append(GLOBAL_STICKER_STREAM_ID)
    rows = (
        db.query(StickerMemory)
        .filter(StickerMemory.status == ACTIVE_STATUS, StickerMemory.chat_stream_id.in_(scopes))
        .all()
    )
    scored = []
    q = str(query or "").strip()
    for row in rows:
        score = _score_row(row, q, stream_id)
        if q and score <= (10 if row.chat_stream_id == stream_id else 0):
            continue
        scored.append((score, row))
    scored.sort(
        key=lambda item: (
            item[0],
            1 if item[1].chat_stream_id == stream_id else 0,
            int(item[1].usage_count or 0),
            item[1].last_seen or item[1].created_at,
        ),
        reverse=True,
    )
    return [sticker_to_dict(row) | {"score": score} for score, row in scored[: max(1, limit)]]


def record_sticker_use(db: Session, sticker_id: int) -> dict[str, Any]:
    row = db.query(StickerMemory).filter(StickerMemory.id == int(sticker_id)).first()
    if row is None:
        raise ValueError(f"表情包不存在: {sticker_id}")
    row.usage_count = int(row.usage_count or 0) + 1
    row.last_used = datetime.now()
    db.commit()
    db.refresh(row)
    return sticker_to_dict(row)


def record_sticker_uses_in_content(content: str, db: Session | None = None) -> int:
    send_codes = extract_sticker_send_codes(content)
    if not send_codes:
        return 0

    own_session = db is None
    session = db or SessionLocal()
    try:
        rows = (
            session.query(StickerMemory)
            .filter(StickerMemory.status == ACTIVE_STATUS, StickerMemory.send_code.in_(send_codes))
            .all()
        )
        if len(rows) < len(send_codes):
            known_ids = {row.id for row in rows}
            candidates = (
                session.query(StickerMemory)
                .filter(StickerMemory.status == ACTIVE_STATUS)
                .all()
            )
            for row in candidates:
                if row.id in known_ids:
                    continue
                if _canonical_row_send_code(row) in send_codes:
                    rows.append(row)
                    known_ids.add(row.id)
        now = datetime.now()
        for row in rows:
            row.usage_count = int(row.usage_count or 0) + 1
            row.last_used = now
        if rows:
            session.commit()
        return len(rows)
    except Exception as e:
        try:
            session.rollback()
        except Exception:
            pass
        logger.warning("[StickerMemory] record sent sticker failed: %s", e)
        return 0
    finally:
        if own_session:
            session.close()


def expand_sticker_refs_in_content(content: str, db: Session | None = None) -> str:
    text = str(content or "")
    sticker_ids = {int(match.group(1)) for match in STICKER_REF_PATTERN.finditer(text)}
    if not sticker_ids:
        return text

    own_session = db is None
    session = db or SessionLocal()
    try:
        rows = (
            session.query(StickerMemory)
            .filter(StickerMemory.status == ACTIVE_STATUS, StickerMemory.id.in_(sticker_ids))
            .all()
        )
        code_by_id = {
            int(row.id): f"[IMAGE:{row.file_ref}]"
            for row in rows
        }

        def replace_token(match: re.Match) -> str:
            sticker_id = int(match.group(1))
            return code_by_id.get(sticker_id, match.group(0))

        return STICKER_REF_PATTERN.sub(replace_token, text)
    except Exception as e:
        logger.warning("[StickerMemory] expand sticker refs failed: %s", e)
        return text
    finally:
        if own_session:
            session.close()


def disable_sticker(db: Session, sticker_id: int) -> dict[str, Any]:
    row = db.query(StickerMemory).filter(StickerMemory.id == int(sticker_id)).first()
    if row is None:
        raise ValueError(f"表情包不存在: {sticker_id}")
    row.status = "disabled"
    db.commit()
    db.refresh(row)
    return sticker_to_dict(row)


def _extract_emotions_from_keywords(keywords: list[str], summary: str) -> list[str]:
    text = " ".join(keywords + [summary])
    mapping = {
        "happy": ("笑", "开心", "快乐", "乐", "喜"),
        "angry": ("怒", "生气", "拍桌", "破防"),
        "surprised": ("震惊", "惊讶", "懵", "啊"),
        "sad": ("哭", "难过", "委屈"),
        "mocking": ("阴阳", "嘲讽", "绷", "乐子"),
        "confused": ("疑惑", "问号", "不懂"),
    }
    result = []
    for emotion, hints in mapping.items():
        if any(hint in text for hint in hints):
            result.append(emotion)
    return result[:5]


def describe_sticker_with_qwen(file_ref: str) -> dict[str, Any]:
    """调用现有 image_summary/Qwen 通道，为表情包生成轻量描述。"""
    from creatures.nanobot.prompts.skills.image_summary.tool import (
        ImageSummaryTool,
        _parse_json_payload,
    )

    tool = ImageSummaryTool()
    raw = tool._call_qwen(
        [file_ref],
        "这是一张聊天表情包。请重点识别可用于检索的中文描述、图片文字、梗点、情绪和适合使用的聊天场景。",
    )
    parsed = _parse_json_payload(raw)
    per_image = parsed.get("per_image") if isinstance(parsed.get("per_image"), list) else []
    first = per_image[0] if per_image and isinstance(per_image[0], dict) else {}
    summary = str(first.get("summary") or parsed.get("overall_summary") or "").strip()
    keywords = _json_list(parsed.get("keywords"))
    objects = _json_list(first.get("objects"))
    text_items = _json_list(first.get("text"))
    tags = _json_list(keywords + objects + text_items)
    return {
        "description": summary,
        "tags": tags,
        "emotions": _extract_emotions_from_keywords(tags, summary),
        "raw_summary": parsed,
    }


def _sticker_image_ref_for_describe(row: StickerMemory) -> str:
    """为表情包打标准备图片引用。

    优先使用已缓存的本地文件，但不直接把路径传给 image_summary
    ——image_pipeline 默认禁止读取本地文件，这里转为 data URL。
    """
    local = str(row.local_path or "").strip()
    if local:
        try:
            from core.sticker_preview import safe_existing_local_path

            safe = safe_existing_local_path(local)
            if safe:
                mime = mimetypes.guess_type(safe)[0] or "image/png"
                with open(safe, "rb") as f:
                    raw = f.read()
                b64 = base64.b64encode(raw).decode("ascii")
                return f"data:{mime};base64,{b64}"
        except Exception as e:
            logger.warning(
                "[StickerMemory] build local data-url failed id=%s: %s",
                getattr(row, "id", "?"),
                e,
            )

    return str(row.file_ref or "").strip()


def auto_describe_sticker(sticker_id: int, *, force: bool = False) -> None:
    if not force and not STICKER_AUTO_DESCRIBE_ENABLED:
        return
    db = SessionLocal()
    try:
        row = db.query(StickerMemory).filter(StickerMemory.id == int(sticker_id)).first()
        if row is None:
            return
        if not force:
            if row.description:
                return
            if row.describe_status == "ok":
                return
            if row.describe_attempts >= 3:
                return
            if row.describe_status == "disabled":
                return
        ref = _sticker_image_ref_for_describe(row)
        if not ref:
            row.describe_status = "failed"
            row.describe_attempts = (row.describe_attempts or 0) + 1
            row.describe_last_error = "no image reference"
            db.commit()
            return
        try:
            payload = describe_sticker_with_qwen(ref)
        except Exception as e:
            row.describe_status = "failed"
            row.describe_attempts = (row.describe_attempts or 0) + 1
            row.describe_last_error = str(e)[:1000]
            db.commit()
            logger.warning("[StickerMemory] auto describe failed id=%s: %s", sticker_id, e)
            return
        row.description = str(payload.get("description") or "").strip()
        if payload.get("tags"):
            row.tags_json = _dumps_list(payload.get("tags"))
        if payload.get("emotions"):
            row.emotions_json = _dumps_list(payload.get("emotions"))
        try:
            meta = json.loads(row.meta_json or "{}")
        except Exception:
            meta = {}
        meta["qwen_summary"] = payload.get("raw_summary")
        row.meta_json = json.dumps(meta, ensure_ascii=False)
        row.describe_status = "ok"
        row.describe_attempts = (row.describe_attempts or 0) + 1
        row.describe_last_error = ""
        row.described_at = datetime.now()
        db.commit()
    finally:
        db.close()
