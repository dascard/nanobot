"""表情包预览缓存——QQ 域名白名单 + 状态细分 + 安全代理下载。"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

import imagehash
from PIL import Image
from sqlalchemy.orm import Session

from core.database import StickerMemory
from core.sticker_memory import normalize_sticker_file_ref, CQ_IMAGE_PATTERN, _cq_unescape, _loads_list

import json as _json


def _safe_dict(raw) -> dict:
    try:
        parsed = _json.loads(raw) if isinstance(raw, str) else (raw or {})
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}

logger = logging.getLogger("nanobot.sticker_preview")

TRUSTED_STICKER_PREVIEW_HOSTS = {"multimedia.nt.qq.com.cn", "gchat.qpic.cn"}


@dataclass
class StickerPreviewCacheResult:
    ok: bool
    status: str
    local_path: str = ""
    content_hash: str = ""
    width: int = 0
    height: int = 0
    error: str = ""


def extract_preview_ref(row: StickerMemory) -> str:
    candidates = [row.file_ref or "", row.send_code or ""]
    for raw in candidates:
        ref = normalize_sticker_file_ref(raw)
        if not ref:
            continue
        m = CQ_IMAGE_PATTERN.fullmatch(ref)
        if m:
            inner = normalize_sticker_file_ref(_cq_unescape(m.group(1).strip()))
            if inner:
                return inner
        if ref.startswith("[IMAGE:") and ref.endswith("]"):
            inner = normalize_sticker_file_ref(ref[len("[IMAGE:"):-1])
            if inner:
                return inner
        if ref.startswith(("http://", "https://")):
            return ref
    return ""


def is_trusted_sticker_preview_host(hostname: str) -> bool:
    return (hostname or "").lower().rstrip(".") in TRUSTED_STICKER_PREVIEW_HOSTS


def is_blocked_host(hostname: str) -> bool:
    import ipaddress, socket
    host = (hostname or "").lower().rstrip(".")
    if is_trusted_sticker_preview_host(host):
        return False
    try:
        addr = ipaddress.ip_address(host)
        return bool(addr.is_private or addr.is_loopback or addr.is_link_local
                    or addr.is_reserved or addr.is_multicast or addr.is_unspecified)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
            if (addr.is_private or addr.is_loopback or addr.is_link_local
                    or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
                return True
        except ValueError:
            continue
    return False


def _cache_dir() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.abspath(os.path.join(base, "data", "stickers"))
    os.makedirs(path, exist_ok=True)
    return path


def safe_existing_local_path(local_path: str) -> str:
    if not local_path:
        return ""
    cd = _cache_dir()
    la = os.path.abspath(local_path)
    return la if la.startswith(cd + os.sep) and os.path.exists(la) else ""


def media_type_for_path(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",
            ".gif":"image/gif",".webp":"image/webp"}.get(ext, "application/octet-stream")


def cache_sticker_preview(db: Session, sticker_id: int, *, force: bool = False) -> StickerPreviewCacheResult:
    row = db.query(StickerMemory).filter(StickerMemory.id == int(sticker_id)).first()
    if row is None:
        return StickerPreviewCacheResult(ok=False, status="missing", error="sticker not found")

    if not force:
        existing = safe_existing_local_path(row.local_path or "")
        if existing:
            return StickerPreviewCacheResult(ok=True, status="ok", local_path=existing)
        if row.preview_status in {"blocked", "expired", "invalid_image", "invalid_ref", "fetch_failed"}:
            return StickerPreviewCacheResult(ok=False, status=row.preview_status, error=row.preview_status)

    ref = extract_preview_ref(row)
    if not ref:
        row.preview_status = "invalid_ref"; db.commit()
        return StickerPreviewCacheResult(ok=False, status="invalid_ref", error="no preview ref")

    u = urlparse(ref)
    if u.scheme not in {"http", "https"}:
        row.preview_status = "blocked"; db.commit()
        return StickerPreviewCacheResult(ok=False, status="blocked", error="unsupported scheme")
    if not u.hostname:
        row.preview_status = "blocked"; db.commit()
        return StickerPreviewCacheResult(ok=False, status="blocked", error="missing hostname")
    if is_blocked_host(u.hostname):
        row.preview_status = "blocked"; db.commit()
        return StickerPreviewCacheResult(ok=False, status="blocked", error="private IP blocked")

    try:
        req = urllib.request.Request(ref, headers={"User-Agent": "Nanobot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                ct = resp.headers.get("Content-Type", "")
                data = resp.read()
        except urllib.error.HTTPError as e:
            ct = (e.headers or {}).get("Content-Type", "")
            data = e.read() or b""
            body_preview = data[:500].decode("utf-8", errors="ignore")
            lower = body_preview.lower()
            if "download url has expired" in lower or "-5503007" in body_preview:
                row.preview_status = "expired"; db.commit()
                return StickerPreviewCacheResult(ok=False, status="expired", error=body_preview[:200])
            row.preview_status = "fetch_failed"; db.commit()
            return StickerPreviewCacheResult(ok=False, status="fetch_failed",
                                              error=f"http {e.code}: {body_preview[:200]}")

        if not ct.lower().startswith("image/"):
            body_preview = data[:500].decode("utf-8", errors="ignore")
            lower = body_preview.lower()
            if "download url has expired" in lower or "-5503007" in body_preview:
                row.preview_status = "expired"; db.commit()
                return StickerPreviewCacheResult(ok=False, status="expired", error=body_preview[:200])
            row.preview_status = "invalid_image"; db.commit()
            return StickerPreviewCacheResult(ok=False, status="invalid_image", error="not an image")

        if len(data) < 512:
            row.preview_status = "invalid_image"; db.commit()
            return StickerPreviewCacheResult(ok=False, status="invalid_image", error="too small")

        try:
            img = Image.open(io.BytesIO(data))
            img.verify()
            img2 = Image.open(io.BytesIO(data))
            width, height = img2.size
        except Exception as e:
            row.preview_status = "invalid_image"; db.commit()
            return StickerPreviewCacheResult(ok=False, status="invalid_image", error=str(e))

        ext_map = {"image/png":".png","image/jpeg":".jpg","image/gif":".gif","image/webp":".webp"}
        ext = ext_map.get(ct.split(";")[0].lower(), ".img")
        ch = hashlib.sha256(data).hexdigest()
        local = os.path.join(_cache_dir(), f"{ch[:32]}{ext}")
        with open(local, "wb") as f:
            f.write(data)
        row.local_path = local
        row.preview_status = "ok"
        if hasattr(row, "content_hash"): row.content_hash = ch
        if hasattr(row, "byte_size"): row.byte_size = len(data)
        if hasattr(row, "width"): row.width = width
        if hasattr(row, "height"): row.height = height

        # 计算感知哈希（动图取首帧，透明背景转白底）
        if local:
            try:
                img = _image_for_hash(local)
                row.phash = str(imagehash.phash(img))
                row.dhash = str(imagehash.dhash(img))
                row.ahash = str(imagehash.average_hash(img))
            except Exception:
                pass  # 哈希计算失败不影响主流程

        db.commit()

        # 内容去重
        dedupe_by_content_hash(db, row.id)

        return StickerPreviewCacheResult(ok=True, status="ok", local_path=local,
                                          content_hash=ch, width=width, height=height)
    except Exception as e:
        row.preview_status = "fetch_failed"; db.commit()
        return StickerPreviewCacheResult(ok=False, status="fetch_failed", error=str(e))


def _canonical_score(r: StickerMemory) -> tuple:
    """canonical 评分：值越低越优先。"""
    status_order = {"active": 0, "disabled": 1, "duplicate": 2}
    return (
        status_order.get(r.status or "", 3),
        0 if (r.preview_status or "") == "ok" else 1,
        0 if safe_existing_local_path(r.local_path or "") else 1,
        0 if (r.describe_status or "") == "ok" else 1,
        0 if r.description else 1,
        -(len(_loads_list(r.tags_json)) + len(_loads_list(r.emotions_json))),
        -(r.usage_count or 0),
        -(r.source_count or 0),
        r.id or 0,
    )


def dedupe_by_content_hash(db: Session, sticker_id: int, *, force_set_canonical: int = 0) -> int | None:
    """content_hash 全局去重——不再按 chat_stream_id 限定。

    选出最佳 canonical，其余标 duplicate；合并 tags/description/usage/source。
    force_set_canonical: 人工强制指定 canonical id。
    """
    import json as _json

    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row or not row.content_hash:
        return None

    all_rows = (
        db.query(StickerMemory)
        .filter(StickerMemory.content_hash == row.content_hash,
                StickerMemory.status != "deleted")
        .all()
    )
    if len(all_rows) <= 1:
        return None

    if force_set_canonical and any(r.id == force_set_canonical for r in all_rows):
        canonical = next(r for r in all_rows if r.id == force_set_canonical)
    else:
        # duplicate 不能抢 canonical（除非全部都是 duplicate）
        non_dup = [r for r in all_rows if r.status != "duplicate"]
        pool = sorted(non_dup if non_dup else all_rows, key=_canonical_score)
        canonical = pool[0]

    merged_source = sum(r.source_count or 0 for r in all_rows)
    merged_usage = sum(r.usage_count or 0 for r in all_rows)

    all_tags: list[str] = []
    all_emotions: list[str] = []
    desc_candidates: list[dict] = []
    source_streams: list[str] = []
    source_ids: list[int] = []

    for r in all_rows:
        if r.chat_stream_id and r.chat_stream_id not in source_streams:
            source_streams.append(r.chat_stream_id)
        if r.id not in source_ids:
            source_ids.append(r.id)
        for t in _loads_list(r.tags_json):
            if t not in all_tags:
                all_tags.append(t)
        for e in _loads_list(r.emotions_json):
            if e not in all_emotions:
                all_emotions.append(e)
        if r.description:
            desc_candidates.append({
                "id": r.id,
                "description": r.description,
                "is_canonical": r.id == canonical.id,
            })

    canonical.dedupe_status = "unique"
    canonical.duplicate_of_id = None
    if canonical.status == "duplicate":
        canonical.status = "active"
    canonical.source_count = merged_source
    canonical.usage_count = merged_usage
    canonical.tags_json = _json.dumps(all_tags, ensure_ascii=False)
    canonical.emotions_json = _json.dumps(all_emotions, ensure_ascii=False)
    if not canonical.description and desc_candidates:
        canonical.description = desc_candidates[0]["description"]

    meta = _safe_dict(canonical.meta_json)
    meta["description_candidates"] = desc_candidates
    meta["source_streams"] = source_streams
    meta["source_record_ids"] = source_ids
    canonical.meta_json = _json.dumps(meta, ensure_ascii=False)

    for dup in all_rows:
        if dup.id == canonical.id:
            continue
        dup.status = "duplicate"
        dup.duplicate_of_id = canonical.id
        dup.dedupe_status = "duplicate"

    db.commit()
    return canonical.id


def backfill_exact_dedupe(db: Session) -> dict:
    """全库 content_hash 重复分组批量去重。返回报告。"""
    from sqlalchemy import func

    dup_hashes = (
        db.query(StickerMemory.content_hash, func.count(StickerMemory.id).label("cnt"))
        .filter(StickerMemory.content_hash.isnot(None), StickerMemory.content_hash != "",
                StickerMemory.status != "deleted")
        .group_by(StickerMemory.content_hash)
        .having(func.count(StickerMemory.id) > 1)
        .all()
    )
    result = {"total_groups": len(dup_hashes), "total_duplicates": 0, "canonical_ids": [], "errors": []}
    for ch, _ in dup_hashes:
        try:
            first = db.query(StickerMemory).filter(
                StickerMemory.content_hash == ch, StickerMemory.status != "deleted").first()
            if first:
                cid = dedupe_by_content_hash(db, first.id)
                if cid:
                    result["canonical_ids"].append(cid)
                    cnt = db.query(StickerMemory).filter(
                        StickerMemory.content_hash == ch, StickerMemory.dedupe_status == "duplicate").count()
                    result["total_duplicates"] += cnt
        except Exception as e:
            result["errors"].append(str(e)[:200])
    return result


def _image_for_hash(path: str) -> Image.Image:
    """预处理：动图取首帧，RGBA 转白底 RGB，稳定 hash。"""
    img = Image.open(path)
    try:
        img.seek(0)
    except Exception:
        pass
    img = img.convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.alpha_composite(img)
    return bg.convert("RGB")


def _hash_distance(a: str, b: str) -> int:
    """imagehash hex 字符串 → 汉明距离。"""
    if not a or not b:
        return 999
    try:
        return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)
    except Exception:
        return 999


def backfill_phash(db: Session, limit: int = 200) -> dict:
    """历史表情补 phash/dhash/ahash。扫描 local_path 存在且 hash 为空的表情。"""
    rows = (
        db.query(StickerMemory)
        .filter(StickerMemory.local_path != "", StickerMemory.phash == "")
        .limit(limit)
        .all()
    )
    ok = 0
    skip = 0
    for r in rows:
        local = safe_existing_local_path(r.local_path or "")
        if not local:
            skip += 1
            continue
        try:
            img = _image_for_hash(local)
            r.phash = str(imagehash.phash(img))
            r.dhash = str(imagehash.dhash(img))
            r.ahash = str(imagehash.average_hash(img))
            ok += 1
        except Exception:
            skip += 1
    db.commit()
    return {"scanned": len(rows), "ok": ok, "skipped": skip}


def find_near_duplicates(db: Session, sticker_id: int) -> list[dict]:
    """查找感知哈希接近的候选重复。返回 [{sticker_b_id, phash_dist, dhash_dist}]。"""
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row or not row.phash:
        return []

    candidates = (
        db.query(StickerMemory)
        .filter(
            StickerMemory.id != sticker_id,
            StickerMemory.status == "active",
            StickerMemory.dedupe_status != "duplicate",
            StickerMemory.phash != "",
            StickerMemory.dhash != "",
        )
        .all()
    )

    results = []
    for c in candidates:
        ph_dist = _hash_distance(row.phash, c.phash)
        dh_dist = _hash_distance(row.dhash, c.dhash)
        if ph_dist <= 8 and dh_dist <= 8:
            results.append({
                "sticker_b_id": c.id,
                "phash_dist": ph_dist,
                "dhash_dist": dh_dist,
            })

    results.sort(key=lambda x: x["phash_dist"] + x["dhash_dist"])
    return results[:20]


def scan_near_duplicates(db: Session, limit: int = 100) -> dict:
    """扫描全库，为有 phash 的 active sticker 查找近邻重复候选。"""
    from core.database import StickerDuplicateCandidate

    rows = (
        db.query(StickerMemory)
        .filter(StickerMemory.status == "active", StickerMemory.dedupe_status != "duplicate",
                StickerMemory.phash != "")
        .limit(limit)
        .all()
    )

    created = 0
    existing_pairs = {
        (min(a_id, b_id), max(a_id, b_id))
        for a_id, b_id in db.query(
            StickerDuplicateCandidate.sticker_a_id,
            StickerDuplicateCandidate.sticker_b_id,
        ).all()
    }
    for row in rows:
        nears = find_near_duplicates(db, row.id)
        for n in nears:
            pair = (min(row.id, n["sticker_b_id"]), max(row.id, n["sticker_b_id"]))
            if pair in existing_pairs:
                continue
            db.add(StickerDuplicateCandidate(
                sticker_a_id=pair[0],
                sticker_b_id=pair[1],
                phash_dist=n["phash_dist"],
                dhash_dist=n["dhash_dist"],
                content_hash=row.content_hash or "",
            ))
            existing_pairs.add(pair)
            created += 1
    db.commit()
    return {"scanned": len(rows), "candidates_created": created}
