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

from PIL import Image
from sqlalchemy.orm import Session

from core.database import StickerMemory
from core.sticker_memory import normalize_sticker_file_ref, CQ_IMAGE_PATTERN, _cq_unescape

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
        db.commit()

        # 内容去重
        dedupe_by_content_hash(db, row.id)

        return StickerPreviewCacheResult(ok=True, status="ok", local_path=local,
                                          content_hash=ch, width=width, height=height)
    except Exception as e:
        row.preview_status = "fetch_failed"; db.commit()
        return StickerPreviewCacheResult(ok=False, status="fetch_failed", error=str(e))


def dedupe_by_content_hash(db: Session, sticker_id: int) -> int | None:
    """content_hash 相同 → 标 duplicate，返回 canonical id。"""
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row or not row.content_hash or row.dedupe_status != "unique":
        return None

    existing = (
        db.query(StickerMemory)
        .filter(
            StickerMemory.chat_stream_id == row.chat_stream_id,
            StickerMemory.content_hash == row.content_hash,
            StickerMemory.id != row.id,
            StickerMemory.status.in_(["active", "disabled"]),
            StickerMemory.dedupe_status == "unique",
        )
        .order_by(StickerMemory.id.asc())
        .first()
    )
    if not existing:
        return None

    # 合并 usage/source_count/description
    existing.source_count = (existing.source_count or 1) + (row.source_count or 1)
    existing.usage_count = (existing.usage_count or 0) + (row.usage_count or 0)
    if not existing.description and row.description:
        existing.description = row.description

    row.status = "duplicate"
    row.duplicate_of_id = existing.id
    row.dedupe_status = "duplicate"
    db.commit()
    return existing.id
