"""KT 多模态消息公开类型的可选导入边界。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


try:
    from kohakuterrarium.llm.message import (
        ImagePart as ImagePart,
        TextPart as TextPart,
        content_parts_to_dicts as content_parts_to_dicts,
        make_multimodal_content as make_multimodal_content,
    )
    KT_MESSAGE_API_AVAILABLE = True
except ModuleNotFoundError as exc:
    if not str(exc.name or "").startswith("kohakuterrarium"):
        raise
    KT_MESSAGE_API_AVAILABLE = False

    @dataclass
    class TextPart:
        text: str
        type: Literal["text"] = "text"

        def to_dict(self) -> dict[str, Any]:
            return {"type": "text", "text": self.text}

    @dataclass
    class ImagePart:
        url: str
        detail: Literal["auto", "low", "high"] = "low"
        source_type: str | None = None
        source_name: str | None = None
        type: Literal["image_url"] = "image_url"

        def to_dict(self) -> dict[str, Any]:
            result: dict[str, Any] = {
                "type": "image_url",
                "image_url": {
                    "url": self.url,
                    "detail": self.detail,
                },
            }
            if self.source_type or self.source_name:
                result["meta"] = {
                    "source_type": self.source_type,
                    "source_name": self.source_name,
                }
            return result

    def content_parts_to_dicts(parts: list[Any]) -> list[dict[str, Any]]:
        return [
            dict(part)
            if isinstance(part, dict)
            else part.to_dict()
            for part in parts
        ]

    def make_multimodal_content(
        text: str,
        *,
        images: list[ImagePart] | None = None,
    ) -> str | list[dict[str, Any]]:
        if not images:
            return str(text or "")
        return [
            {"type": "text", "text": str(text or "")},
            *(image.to_dict() for image in images),
        ]


__all__ = [
    "ImagePart",
    "KT_MESSAGE_API_AVAILABLE",
    "TextPart",
    "content_parts_to_dicts",
    "make_multimodal_content",
]
