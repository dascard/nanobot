"""KT 输出基类的可选导入边界。"""

from __future__ import annotations


try:
    from kohakuterrarium.modules.output.base import BaseOutputModule as BaseOutputModule
    KT_OUTPUT_API_AVAILABLE = True
except ModuleNotFoundError as exc:
    if not str(exc.name or "").startswith("kohakuterrarium"):
        raise
    KT_OUTPUT_API_AVAILABLE = False

    class BaseOutputModule:
        def __init__(self) -> None:
            self.running = False

        @property
        def is_running(self) -> bool:
            return self.running

        async def start(self) -> None:
            self.running = True

        async def stop(self) -> None:
            await self.flush()
            self.running = False

        async def flush(self) -> None:
            return None


__all__ = ["BaseOutputModule", "KT_OUTPUT_API_AVAILABLE"]
