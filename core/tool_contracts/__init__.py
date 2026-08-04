"""代码持有的模型工具输入与执行结果契约。"""

from core.tool_contracts.result import ToolServiceResult
from core.tool_contracts.rich_output import build_rich_output


__all__ = ["ToolServiceResult", "build_rich_output"]
