"""生成图片临时缓存和 reply token 展开。"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import Any

from config import LOG_DIR


logger = logging.getLogger("nanobot.generated_images")

GENERATED_IMAGE_DIR = os.environ.get(
    "IMAGE_GENERATION_OUTPUT_DIR",
    os.path.join(LOG_DIR, "generated_images"),
)

GENERATED_IMAGE_REF_PATTERN = re.compile(r"\[generated_image:([A-Za-z0-9_-]{8,96})\]")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,96}$")


def _compact_b64(value: str) -> str:
    return "".join(str(value or "").split())


def _path_for_token(token: str) -> str:
    if not _TOKEN_RE.fullmatch(str(token or "")):
        raise ValueError("invalid generated image token")
    return os.path.abspath(os.path.join(GENERATED_IMAGE_DIR, f"{token}.png"))


def _meta_path_for_token(token: str) -> str:
    if not _TOKEN_RE.fullmatch(str(token or "")):
        raise ValueError("invalid generated image token")
    return os.path.abspath(os.path.join(GENERATED_IMAGE_DIR, f"{token}.json"))


def _iso_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def save_generated_image(
    image_b64: str,
    *,
    prompt: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """保存生成图片，返回可交给 reply(content) 的短 token。"""
    compact = _compact_b64(image_b64)
    raw = base64.b64decode(compact, validate=True)
    digest = hashlib.sha256(raw).hexdigest()[:16]
    created_ts = time.time()
    token = f"img_{int(created_ts * 1000)}_{digest}_{uuid.uuid4().hex[:8]}"
    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    path = _path_for_token(token)
    with open(path, "wb") as fh:
        fh.write(raw)

    extra = dict(metadata or {})
    row = {
        "id": token,
        "reply_token": f"[generated_image:{token}]",
        "path": path,
        "filename": os.path.basename(path),
        "bytes": len(raw),
        "sha256": digest,
        "prompt": str(prompt or ""),
        "prompt_preview": str(prompt or "")[:200],
        "created_at": _iso_from_ts(created_ts),
        "created_at_ts": created_ts,
        "mime": "image/png",
        "model": str(extra.get("model") or ""),
        "size": str(extra.get("size") or ""),
        "quality": str(extra.get("quality") or ""),
        "background": str(extra.get("background") or ""),
    }
    meta_path = _meta_path_for_token(token)
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(row, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return row


def _load_generated_image_meta(token: str) -> dict[str, Any] | None:
    try:
        meta_path = _meta_path_for_token(token)
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        if not isinstance(meta, dict):
            return None
        meta["id"] = token
        meta.setdefault("reply_token", f"[generated_image:{token}]")
        meta.setdefault("mime", "image/png")
        meta.setdefault("path", _path_for_token(token))
        return meta
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("[generated_images] failed to load metadata token=%s: %s", token, exc)
        return None


def _fallback_meta_for_png(token: str) -> dict[str, Any] | None:
    try:
        path = _path_for_token(token)
        stat = os.stat(path)
        return {
            "id": token,
            "reply_token": f"[generated_image:{token}]",
            "path": path,
            "filename": os.path.basename(path),
            "bytes": stat.st_size,
            "sha256": "",
            "prompt": "",
            "prompt_preview": "",
            "created_at": _iso_from_ts(stat.st_mtime),
            "created_at_ts": stat.st_mtime,
            "mime": "image/png",
            "model": "",
            "size": "",
            "quality": "",
            "background": "",
        }
    except Exception:
        return None


def _public_item(meta: dict[str, Any]) -> dict[str, Any]:
    token = str(meta.get("id") or "")
    path = _path_for_token(token)
    missing = not os.path.exists(path)
    if not missing:
        try:
            meta["bytes"] = os.path.getsize(path)
        except OSError:
            pass
    return {
        "id": token,
        "reply_token": meta.get("reply_token") or f"[generated_image:{token}]",
        "filename": meta.get("filename") or f"{token}.png",
        "prompt": str(meta.get("prompt") or ""),
        "prompt_preview": str(meta.get("prompt_preview") or str(meta.get("prompt") or "")[:200]),
        "created_at": str(meta.get("created_at") or ""),
        "created_at_ts": float(meta.get("created_at_ts") or 0),
        "bytes": int(meta.get("bytes") or 0),
        "sha256": str(meta.get("sha256") or ""),
        "mime": str(meta.get("mime") or "image/png"),
        "model": str(meta.get("model") or ""),
        "size": str(meta.get("size") or ""),
        "quality": str(meta.get("quality") or ""),
        "background": str(meta.get("background") or ""),
        "missing_file": missing,
    }


def get_generated_image(image_id: str) -> dict[str, Any]:
    token = str(image_id or "").strip()
    if not _TOKEN_RE.fullmatch(token):
        raise FileNotFoundError("generated image not found")
    meta = _load_generated_image_meta(token) or _fallback_meta_for_png(token)
    if not meta:
        raise FileNotFoundError("generated image not found")
    return _public_item(meta)


def get_generated_image_path(image_id: str) -> str:
    item = get_generated_image(image_id)
    path = _path_for_token(item["id"])
    if not os.path.exists(path):
        raise FileNotFoundError("generated image file not found")
    return path


def list_generated_images(
    *,
    page: int = 1,
    limit: int = 20,
    search: str = "",
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 20), 100))
    query = str(search or "").strip().lower()

    items: list[dict[str, Any]] = []
    try:
        names = os.listdir(GENERATED_IMAGE_DIR)
    except FileNotFoundError:
        names = []

    tokens = sorted({
        name.rsplit(".", 1)[0]
        for name in names
        if name.endswith(".png") or name.endswith(".json")
    })
    for token in tokens:
        if not _TOKEN_RE.fullmatch(token):
            continue
        meta = _load_generated_image_meta(token) or _fallback_meta_for_png(token)
        if not meta:
            continue
        item = _public_item(meta)
        haystack = " ".join([
            item["id"],
            item["prompt"],
            item["model"],
            item["size"],
            item["quality"],
        ]).lower()
        if query and query not in haystack:
            continue
        items.append(item)

    items.sort(key=lambda item: (item.get("created_at_ts") or 0, item.get("id") or ""), reverse=True)
    total = len(items)
    start = (page - 1) * limit
    page_items = items[start : start + limit]
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": page_items,
    }


def expand_generated_image_refs_in_content(content: str) -> str:
    """把 `[generated_image:...]` 展开成 OneBot base64 CQ 图片码。"""
    text = str(content or "")

    def _replace(match: re.Match[str]) -> str:
        token = match.group(1)
        try:
            path = _path_for_token(token)
            with open(path, "rb") as fh:
                image_b64 = base64.b64encode(fh.read()).decode("ascii")
            return f"[CQ:image,file=base64://{image_b64}]"
        except Exception as exc:
            logger.warning("[generated_images] failed to expand token=%s: %s", token, exc)
            return match.group(0)

    return GENERATED_IMAGE_REF_PATTERN.sub(_replace, text)
