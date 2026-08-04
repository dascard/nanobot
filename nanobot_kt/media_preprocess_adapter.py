"""KT 图片管线到框架无关预缓存 Port 的适配器。"""

from __future__ import annotations

from collections.abc import Mapping

from nanobot_kt.image_pipeline import precache_image_sources


class KtImagePrecacheAdapter:
    def precache(
        self,
        sources: tuple[str, ...],
        *,
        source_type: str,
        source_name_prefix: str,
    ) -> tuple[Mapping[str, object], ...]:
        results = precache_image_sources(
            list(sources),
            source_type=source_type,
            source_name_prefix=source_name_prefix,
        )
        return tuple(dict(item) for item in results)


__all__ = ["KtImagePrecacheAdapter"]
