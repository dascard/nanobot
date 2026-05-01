"""图片下载、缓存、压缩与多模态封装工具。"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import mimetypes
import os
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from kohakuterrarium.llm.message import ImagePart

from config import (
    IMAGE_PREPROCESS_ALLOW_LOCAL_FILES,
    IMAGE_PREPROCESS_CACHE_DIR,
    IMAGE_PREPROCESS_DOWNLOAD_TIMEOUT,
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
        path = Path(urllib.parse.urlparse(source).path)
        data = path.read_bytes()
        if len(data) > IMAGE_PREPROCESS_RAW_MAX_BYTES:
            raise ValueError(f"图片过大: {len(data)} bytes > {IMAGE_PREPROCESS_RAW_MAX_BYTES} bytes")
        return data, _guess_mime(source)

    if parsed.scheme == "" and Path(source).exists():
        if not IMAGE_PREPROCESS_ALLOW_LOCAL_FILES:
            raise ValueError("默认禁止读取本地文件图片")
        path = Path(source)
        data = path.read_bytes()
        if len(data) > IMAGE_PREPROCESS_RAW_MAX_BYTES:
            raise ValueError(f"图片过大: {len(data)} bytes > {IMAGE_PREPROCESS_RAW_MAX_BYTES} bytes")
        return data, _guess_mime(str(path))

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("仅支持 http(s)、data 图片地址")

    req = urllib.request.Request(
        source,
        headers={"User-Agent": "Mozilla/5.0 NanobotImagePipeline"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=IMAGE_PREPROCESS_DOWNLOAD_TIMEOUT) as resp:
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
    for idx, source in enumerate(_normalize_sources(files), start=1):
        prepared = prepare_image(source)
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
    return prepared_parts
