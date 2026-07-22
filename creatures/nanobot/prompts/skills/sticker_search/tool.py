"""兼容旧导入路径；执行实现位于 ``nanobot_kt.tools``。"""

from typing import Any

from kohakuterrarium.modules.tool.base import ToolResult

from core.database import SessionLocal
from nanobot_kt.tools.sticker_search import (
    StickerSearchTool as _StickerSearchTool,
    execute_sticker_search,
)


class StickerSearchTool(_StickerSearchTool):
    """保留旧测试/扩展对模块级 ``SessionLocal`` 的注入语义。"""

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        del kwargs
        return await execute_sticker_search(args, session_factory=SessionLocal)


__all__ = ["StickerSearchTool"]
