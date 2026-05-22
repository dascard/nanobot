"""兼容层：旧 PromptCompiler 名称转发到 PromptAssembler。

新代码应直接使用 `core.prompt_assembler.PromptAssembler`。
"""

from __future__ import annotations

from core.prompt_assembler import (
    PromptAssembler,
    PromptBuildContext,
    PromptBuildResult,
    ensure_user_input_block,
)
from core.prompts import get_prompt_manager
import core.prompt_assembler as _prompt_assembler_module


PromptContext = PromptBuildContext
CompiledPrompt = PromptBuildResult


class PromptCompiler(PromptAssembler):
    """兼容旧调用方的 `compile()` 方法。"""

    def compile(
        self,
        context: PromptContext,
        *,
        trace_id: str = "",
        run_id: str = "",
    ) -> CompiledPrompt:
        original_get_prompt_manager = _prompt_assembler_module.get_prompt_manager
        _prompt_assembler_module.get_prompt_manager = get_prompt_manager
        try:
            return self.build(context, trace_id=trace_id, run_id=run_id)
        finally:
            _prompt_assembler_module.get_prompt_manager = original_get_prompt_manager
