"""QQ 出站响应信封渲染。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.generated_images import public_generated_image_url
from core.asset_transport import expand_asset_download_refs_in_content
from core.message_envelope import sanitize_reply_meta
from core.sticker_memory import expand_sticker_refs_in_content

_GENERATED_IMAGE_RE = re.compile(r"\[generated_image:([A-Za-z0-9_.:-]+)\]")
_UNTRUSTED_CQ_FILE_RE = re.compile(r"\[CQ:file,[^\]]*\]", re.IGNORECASE)


@dataclass(slots=True)
class QQOutboundRenderResult:
    message: str
    messages: list[dict[str, Any]]
    reply_meta: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def render_qq_outbound_envelope(
    envelope: Mapping[str, Any] | None,
    *,
    allow_base64: bool = False,
) -> QQOutboundRenderResult:
    if not isinstance(envelope, Mapping):
        return QQOutboundRenderResult(message="", messages=[], reply_meta={})

    messages = _normalize_messages(envelope.get("messages"))
    if not messages:
        reply = str(envelope.get("reply") or "")
        if reply:
            messages = [{"type": "text", "text": reply}]

    return render_qq_message_items(
        messages,
        reply_meta=envelope.get("reply_meta"),
        allow_base64=allow_base64,
    )


def render_qq_message_items(
    messages: list[Mapping[str, Any]],
    *,
    reply_meta: Mapping[str, Any] | None = None,
    allow_base64: bool = False,
) -> QQOutboundRenderResult:
    rendered: list[str] = []
    normalized: list[dict[str, Any]] = []
    warnings: list[str] = []

    for item in messages:
        if not isinstance(item, Mapping):
            continue
        normalized_item = dict(item)
        normalized.append(normalized_item)
        text = _render_item(normalized_item, warnings=warnings)
        if text:
            rendered.append(text)

    return QQOutboundRenderResult(
        message="\n".join(rendered),
        messages=normalized,
        reply_meta=sanitize_reply_meta(reply_meta),
        warnings=warnings,
    )


def _normalize_messages(raw_messages: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_messages, list):
        return []
    return [dict(item) for item in raw_messages if isinstance(item, Mapping)]


def _render_item(item: Mapping[str, Any], *, warnings: list[str]) -> str:
    item_type = str(item.get("type") or "text")
    if item_type in {"text", "html"}:
        return _render_text(str(item.get("text") or ""), warnings=warnings)
    if item_type == "image":
        return _render_image(item, warnings=warnings)
    return ""


def _render_text(text: str, *, warnings: list[str]) -> str:
    expanded = _GENERATED_IMAGE_RE.sub(
        lambda match: _render_generated_image_token(match.group(1), warnings=warnings),
        text,
    )
    expanded = expand_asset_download_refs_in_content(expanded)
    expanded = _UNTRUSTED_CQ_FILE_RE.sub(
        "（文件消息已拒绝，请使用资产下载链接）",
        expanded,
    )
    return expand_sticker_refs_in_content(expanded)


def _render_image(item: Mapping[str, Any], *, warnings: list[str]) -> str:
    url = str(item.get("url") or "")
    if url:
        return f"[CQ:image,file={_cq_escape(url)}]"
    image_id = str(item.get("generated_image_id") or "")
    if image_id:
        return _render_generated_image_token(image_id, warnings=warnings)
    return ""


def _render_generated_image_token(image_id: str, *, warnings: list[str]) -> str:
    url = public_generated_image_url(image_id)
    if url:
        return f"[CQ:image,file={_cq_escape(url)}]"
    warnings.append(f"generated_image_without_public_url:{image_id}")
    return f"[generated_image:{image_id}]"


def _cq_escape(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
    )
