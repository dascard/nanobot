"""消息内容段的严格 Mapping 兼容解析与传输中立序列化。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from foundation.message_contract.contracts import (
    AssetContent,
    ContentPart,
    FileContent,
    ForwardContent,
    ImageContent,
    MessageContractError,
    TextContent,
    TextFormat,
)


def _strict_fields(
    payload: Mapping[str, Any],
    *,
    allowed: frozenset[str],
) -> None:
    unknown = {str(key) for key in payload} - allowed
    if unknown:
        raise MessageContractError(
            "unknown_content_fields",
            "内容段包含未知字段",
        )


def parse_content_part(payload: Mapping[str, Any]) -> ContentPart:
    if not isinstance(payload, Mapping):
        raise MessageContractError(
            "invalid_content_part",
            "内容段必须是对象",
        )
    part_type = payload.get("type")
    if type(part_type) is not str:
        raise MessageContractError(
            "invalid_content_part",
            "内容段 type 必须是字符串",
        )

    if part_type in {"text", "html", "markdown"}:
        _strict_fields(
            payload,
            allowed=frozenset({"type", "text", "format"}),
        )
        default_format = {
            "text": TextFormat.PLAIN,
            "html": TextFormat.HTML,
            "markdown": TextFormat.MARKDOWN,
        }[part_type]
        return TextContent(
            text=payload.get("text", ""),
            format=payload.get("format", default_format),
        )
    if part_type == "image":
        _strict_fields(
            payload,
            allowed=frozenset(
                {"type", "ref", "url", "media_type", "alt_text"}
            ),
        )
        return ImageContent(
            ref=payload.get("ref") or payload.get("url") or "",
            media_type=payload.get("media_type", ""),
            alt_text=payload.get("alt_text", ""),
        )
    if part_type == "file":
        _strict_fields(
            payload,
            allowed=frozenset(
                {"type", "ref", "name", "media_type", "size_bytes"}
            ),
        )
        return FileContent(
            ref=payload.get("ref", ""),
            name=payload.get("name", ""),
            media_type=payload.get("media_type", ""),
            size_bytes=payload.get("size_bytes"),
        )
    if part_type == "asset":
        _strict_fields(
            payload,
            allowed=frozenset(
                {"type", "ref", "name", "media_type", "size_bytes"}
            ),
        )
        return AssetContent(
            ref=payload.get("ref", ""),
            name=payload.get("name", ""),
            media_type=payload.get("media_type", ""),
            size_bytes=payload.get("size_bytes"),
        )
    if part_type == "forward":
        _strict_fields(
            payload,
            allowed=frozenset({"type", "ref", "summary"}),
        )
        return ForwardContent(
            ref=payload.get("ref", ""),
            summary=payload.get("summary", ""),
        )
    raise MessageContractError(
        "unsupported_content_part",
        "内容段 type 不受支持",
    )


def _optional_payload_fields(
    payload: dict[str, Any],
    **fields: object,
) -> dict[str, Any]:
    for key, value in fields.items():
        if value in ("", None):
            continue
        payload[key] = value
    return payload


def content_part_to_payload(part: ContentPart) -> dict[str, Any]:
    if isinstance(part, TextContent):
        part_type = {
            TextFormat.PLAIN: "text",
            TextFormat.MARKDOWN: "markdown",
            TextFormat.HTML: "html",
        }[part.format]
        return {"type": part_type, "text": part.text}
    if isinstance(part, ImageContent):
        return _optional_payload_fields(
            {"type": "image", "url": part.ref},
            media_type=part.media_type,
            alt_text=part.alt_text,
        )
    if isinstance(part, FileContent):
        return _optional_payload_fields(
            {"type": "file", "ref": part.ref},
            name=part.name,
            media_type=part.media_type,
            size_bytes=part.size_bytes,
        )
    if isinstance(part, AssetContent):
        return _optional_payload_fields(
            {"type": "asset", "ref": part.ref},
            name=part.name,
            media_type=part.media_type,
            size_bytes=part.size_bytes,
        )
    if isinstance(part, ForwardContent):
        return _optional_payload_fields(
            {"type": "forward", "ref": part.ref},
            summary=part.summary,
        )
    raise MessageContractError(
        "unsupported_content_part",
        "无法序列化未知内容段",
    )


__all__ = ["content_part_to_payload", "parse_content_part"]
