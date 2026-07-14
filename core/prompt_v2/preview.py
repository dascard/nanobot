from __future__ import annotations

from core.prompt_v2.compiler import compile_prompt_plan
from core.prompt_v2.schema import PromptCompileRequest


async def build_preview_plan(request: PromptCompileRequest):
    return await compile_prompt_plan(request, strict_audit=True)
