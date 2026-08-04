"""图片下载、缓存、压缩与多模态封装工具。"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import mimetypes
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from nanobot_kt.optional_message_api import ImagePart

from config import (
    IMAGE_PREPROCESS_ALLOW_LOCAL_FILES,
    IMAGE_PREPROCESS_CACHE_DIR,
    IMAGE_PREPROCESS_DOWNLOAD_TIMEOUT,
    IMAGE_PREPROCESS_LOCAL_FILE_ROOTS,
    IMAGE_PREPROCESS_MAX_BYTES,
    IMAGE_PREPROCESS_MAX_SIDE,
    IMAGE_PREPROCESS_MIN_QUALITY,
    IMAGE_PREPROCESS_RAW_MAX_BYTES,
    IMAGE_PREPROCESS_START_QUALITY,
)

logger = logging.getLogger("nanobot.image_pipeline")


@dataclass(frozen=True)
class PreparedImage:
    source: str
    cache_path: str
    mime: str
    size_bytes: int
    width: int
    height: int
    data_url: str


def _normalize_sources(files: Any) -> list[str]:
    if not isinstance(files, list):
        return []
    normalized: list[str] = []
    for file in files:
        if not isinstance(file, str):
            continue
        item = file.strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _ensure_cache_dir() -> Path:
    path = Path(IMAGE_PREPROCESS_CACHE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _local_file_roots() -> list[Path]:
    raw_roots = IMAGE_PREPROCESS_LOCAL_FILE_ROOTS
    if isinstance(raw_roots, (list, tuple, set)):
        parts = [str(item) for item in raw_roots]
    else:
        parts = str(raw_roots or "").split(os.pathsep)
    roots: list[Path] = []
    for part in parts:
        value = part.strip()
        if value:
            roots.append(Path(value).expanduser().resolve())
    return roots


def _resolve_allowed_local_file(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser().resolve()
    roots = _local_file_roots()
    if not roots:
        raise ValueError("未配置本地图片允许目录")
    if not any(path == root or root in path.parents for root in roots):
        raise ValueError("本地图片路径不在允许目录内")
    return path


def _guess_mime(source: str, default: str = "image/jpeg") -> str:
    if source.startswith("data:"):
        header = source.split(";", 1)[0]
        if header.startswith("data:") and "/" in header:
            return header[5:]
        return default
    if source.startswith("file://"):
        suffix = Path(urllib.parse.urlparse(source).path).suffix.lower()
        return mimetypes.types_map.get(suffix, default)
    suffix = Path(urllib.parse.urlparse(source).path).suffix.lower()
    return mimetypes.types_map.get(suffix, default)


def _cache_key(source: str) -> str:
    digest = hashlib.sha256(
        f"{source}|{IMAGE_PREPROCESS_MAX_BYTES}|{IMAGE_PREPROCESS_MAX_SIDE}|"
        f"{IMAGE_PREPROCESS_START_QUALITY}|{IMAGE_PREPROCESS_MIN_QUALITY}".encode("utf-8")
    ).hexdigest()
    return digest


def _is_qq_multimedia_download(parsed: urllib.parse.ParseResult) -> bool:
    return (
        parsed.scheme in {"http", "https"}
        and parsed.netloc.lower() == "multimedia.nt.qq.com.cn"
        and parsed.path.startswith("/download")
    )


def _looks_like_placeholder_source(source: str, parsed: urllib.parse.ParseResult) -> bool:
    value = source.strip()
    if value.startswith("[图片") or value.startswith("[image"):
        return True
    if not _is_qq_multimedia_download(parsed):
        return False
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    for key in ("fileid", "r", "rr", "rkey"):
        values = query.get(key) or []
        if any(str(item).strip().lower() == "xxx" for item in values):
            return True
    return False


def _download_headers(parsed: urllib.parse.ParseResult) -> dict[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 NanobotImagePipeline",
    }
    if _is_qq_multimedia_download(parsed):
        headers.update(
            {
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://im.qq.com/",
            }
        )
    return headers


def _open_http_request(req: urllib.request.Request, parsed: urllib.parse.ParseResult):
    if _is_qq_multimedia_download(parsed):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(req, timeout=IMAGE_PREPROCESS_DOWNLOAD_TIMEOUT)
    return urllib.request.urlopen(req, timeout=IMAGE_PREPROCESS_DOWNLOAD_TIMEOUT)


def _http_error_body_preview(error: urllib.error.HTTPError, *, limit: int = 2048) -> str:
    try:
        body = error.read(limit)
    except Exception:
        return ""
    if not body:
        return ""
    if isinstance(body, str):
        return body[:limit]
    return body.decode("utf-8", errors="replace")[:limit]


def _http_header_value(headers: Any, key: str) -> str:
    try:
        value = headers.get(key)
    except Exception:
        value = None
    return str(value or "")


def _format_download_http_error(source: str, error: urllib.error.HTTPError) -> str:
    parsed = urllib.parse.urlparse(source)
    body_preview = _http_error_body_preview(error)
    retcode = _http_header_value(error.headers, "X-ErrNo")
    retmsg = ""

    if body_preview:
        try:
            payload = json.loads(body_preview)
            if isinstance(payload, dict):
                retcode = str(payload.get("retcode") if payload.get("retcode") is not None else retcode)
                retmsg = str(payload.get("retmsg") or "")
        except Exception:
            pass

    if _is_qq_multimedia_download(parsed):
        detail = " ".join(
            part
            for part in (
                f"retcode={retcode}" if retcode else "",
                f"retmsg={retmsg}" if retmsg else "",
            )
            if part
        )
        if retcode == "-5503007" or "expired" in retmsg.lower():
            return f"QQ图片链接已过期: {detail or body_preview or error.reason}"
        if retcode == "-5503010" or "invalid rkey" in retmsg.lower():
            return f"QQ图片链接无效: {detail or body_preview or error.reason}"
        return f"QQ图片下载失败: HTTP {error.code} {error.reason}; {detail or body_preview}".rstrip("; ")

    return f"图片下载失败: HTTP {error.code} {error.reason}"


def _download_source_bytes(source: str) -> tuple[bytes, str]:
    if source.startswith("data:"):
        header, b64 = source.split(",", 1)
        mime = header[5:].split(";", 1)[0] if header.startswith("data:") else "image/jpeg"
        raw = base64.b64decode(b64)
        if len(raw) > IMAGE_PREPROCESS_RAW_MAX_BYTES:
            raise ValueError(f"图片过大: {len(raw)} bytes > {IMAGE_PREPROCESS_RAW_MAX_BYTES} bytes")
        return raw, mime or "image/jpeg"

    parsed = urllib.parse.urlparse(source)
    if parsed.scheme == "file":
        if not IMAGE_PREPROCESS_ALLOW_LOCAL_FILES:
            raise ValueError("默认禁止读取本地文件图片")
        path = _resolve_allowed_local_file(urllib.parse.unquote(parsed.path))
        data = path.read_bytes()
        if len(data) > IMAGE_PREPROCESS_RAW_MAX_BYTES:
            raise ValueError(f"图片过大: {len(data)} bytes > {IMAGE_PREPROCESS_RAW_MAX_BYTES} bytes")
        return data, _guess_mime(source)

    if parsed.scheme == "" and Path(source).exists():
        if not IMAGE_PREPROCESS_ALLOW_LOCAL_FILES:
            raise ValueError("默认禁止读取本地文件图片")
        path = _resolve_allowed_local_file(source)
        data = path.read_bytes()
        if len(data) > IMAGE_PREPROCESS_RAW_MAX_BYTES:
            raise ValueError(f"图片过大: {len(data)} bytes > {IMAGE_PREPROCESS_RAW_MAX_BYTES} bytes")
        return data, _guess_mime(str(path))

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("仅支持 http(s)、data 图片地址")

    if _looks_like_placeholder_source(source, parsed):
        raise ValueError("图片源是占位符或已脱敏，无法下载，请让用户重新发送图片")

    req = urllib.request.Request(
        source,
        headers=_download_headers(parsed),
        method="GET",
    )
    try:
        resp_ctx = _open_http_request(req, parsed)
    except urllib.error.HTTPError as exc:
        raise ValueError(_format_download_http_error(source, exc)) from exc

    with resp_ctx as resp:
        mime = (resp.headers.get_content_type() if hasattr(resp.headers, "get_content_type") else "") or _guess_mime(source)
        content_length = resp.headers.get("Content-Length") if hasattr(resp.headers, "get") else None
        if content_length:
            try:
                if int(content_length) > IMAGE_PREPROCESS_RAW_MAX_BYTES:
                    raise ValueError(
                        f"图片过大: {content_length} bytes > {IMAGE_PREPROCESS_RAW_MAX_BYTES} bytes"
                    )
            except ValueError as e:
                if "图片过大" in str(e):
                    raise
        raw = resp.read(IMAGE_PREPROCESS_RAW_MAX_BYTES + 1)
        if len(raw) > IMAGE_PREPROCESS_RAW_MAX_BYTES:
            raise ValueError(f"图片过大: {len(raw)} bytes > {IMAGE_PREPROCESS_RAW_MAX_BYTES} bytes")
        return raw, mime


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image.copy()


def _resize_image(image: Image.Image, max_side: int) -> Image.Image:
    resized = image.copy()
    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
    resized.thumbnail((max_side, max_side), resampling)
    return resized


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def _compress_image(raw_bytes: bytes) -> tuple[bytes, int, int]:
    with Image.open(io.BytesIO(raw_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        base = _flatten_to_rgb(img)
        min_side = 256
        size_steps = [
            IMAGE_PREPROCESS_MAX_SIDE,
            max(min_side, int(IMAGE_PREPROCESS_MAX_SIDE * 0.85)),
            max(min_side, int(IMAGE_PREPROCESS_MAX_SIDE * 0.7)),
            max(min_side, int(IMAGE_PREPROCESS_MAX_SIDE * 0.55)),
            max(min_side, int(IMAGE_PREPROCESS_MAX_SIDE * 0.4)),
        ]
        quality_steps = [
            IMAGE_PREPROCESS_START_QUALITY,
            85,
            75,
            65,
            55,
            IMAGE_PREPROCESS_MIN_QUALITY,
        ]

        best_attempt: tuple[bytes, int, int] | None = None

        for max_side in size_steps:
            resized = _resize_image(base, max_side)
            for quality in quality_steps:
                data = _encode_jpeg(resized, quality)
                if best_attempt is None or len(data) < len(best_attempt[0]):
                    best_attempt = (data, resized.size[0], resized.size[1])
                if len(data) <= IMAGE_PREPROCESS_MAX_BYTES:
                    return data, resized.size[0], resized.size[1]

        if best_attempt is None:
            raise ValueError("图片压缩失败")

        best_data, best_width, best_height = best_attempt
        logger.warning(
            "Image still above target after compression, using smallest attempt: raw=%d target=%d best=%d",
            len(raw_bytes),
            IMAGE_PREPROCESS_MAX_BYTES,
            len(best_data),
        )
        return best_data, best_width, best_height


def prepare_image(source: str) -> PreparedImage:
    cache_dir = _ensure_cache_dir()
    key = _cache_key(source)
    cache_path = cache_dir / f"{key}.jpg"

    if cache_path.exists() and cache_path.stat().st_size > 0:
        data = cache_path.read_bytes()
        mime = "image/jpeg"
        try:
            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
        except Exception:
            cache_path.unlink(missing_ok=True)
        else:
            data_url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
            return PreparedImage(
                source=source,
                cache_path=str(cache_path),
                mime=mime,
                size_bytes=len(data),
                width=width,
                height=height,
                data_url=data_url,
            )

    raw_bytes, _ = _download_source_bytes(source)
    compressed, width, height = _compress_image(raw_bytes)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=cache_dir,
            prefix=f"{key}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(compressed)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, cache_path)
    finally:
        if tmp_path is not None and tmp_path.exists() and tmp_path != cache_path:
            try:
                tmp_path.unlink()
            except OSError:
                pass
    data_url = f"data:image/jpeg;base64,{base64.b64encode(compressed).decode('ascii')}"
    return PreparedImage(
        source=source,
        cache_path=str(cache_path),
        mime="image/jpeg",
        size_bytes=len(compressed),
        width=width,
        height=height,
        data_url=data_url,
    )


def prepare_image_parts(
    files: Any,
    *,
    source_type: str,
    source_name_prefix: str,
    detail: str = "low",
) -> list[ImagePart]:
    prepared_parts: list[ImagePart] = []
    errors: list[dict] = []
    sources = _normalize_sources(files)
    for idx, source in enumerate(sources, start=1):
        try:
            prepared = prepare_image(source)
        except Exception as e:
            logger.warning("[image_pipeline] skip unsupported source=%r error=%s", source[:200], e)
            errors.append({"source": str(source)[:120], "error": str(e)})
            continue
        prepared_parts.append(
            ImagePart(
                url=prepared.data_url,
                detail=detail,
                source_type=source_type,
                source_name=f"{source_name_prefix}_{idx}",
            )
        )
        logger.info(
            "Prepared image source=%s cache=%s size=%dKB dims=%dx%d",
            source[:120],
            prepared.cache_path,
            prepared.size_bytes // 1024,
            prepared.width,
            prepared.height,
        )
    if not prepared_parts:
        err_detail = "; ".join(f"{e['source']}: {e['error']}" for e in errors)
        raise ValueError(f"没有可用图片源 (共{len(sources)}个, 全部失败): {err_detail}")
    if errors:
        logger.warning("[image_pipeline] %d/%d sources skipped", len(errors), len(sources))
    return prepared_parts


def precache_image_sources(files: Any, *, source_type: str = "", source_name_prefix: str = "") -> list[dict[str, Any]]:
    """预热图片缓存，不生成 ImagePart。用于保存临时 URL 的第一时间缓存。"""
    results: list[dict[str, Any]] = []
    sources = _normalize_sources(files)
    for idx, source in enumerate(sources, start=1):
        try:
            prepared = prepare_image(source)
        except Exception as exc:
            logger.warning(
                "[image_pipeline] precache failed source=%r type=%s prefix=%s error=%s",
                source[:200],
                source_type,
                source_name_prefix,
                exc,
            )
            results.append({"source": source, "ok": False, "error": str(exc)})
            continue
        logger.info(
            "[image_pipeline] precache ok source=%s cache=%s size=%dKB dims=%dx%d name=%s_%d",
            source[:120],
            prepared.cache_path,
            prepared.size_bytes // 1024,
            prepared.width,
            prepared.height,
            source_name_prefix or source_type or "image",
            idx,
        )
        results.append(
            {
                "source": source,
                "ok": True,
                "cache_path": prepared.cache_path,
                "size_bytes": prepared.size_bytes,
                "width": prepared.width,
                "height": prepared.height,
            }
        )
    return results
