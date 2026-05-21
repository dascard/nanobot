# Legacy prompt common base 实现计划

1. RED 测试：
   - 更新 `tests/test_prompt_contract.py`，要求默认 `prompt.md` 为 common base。
   - 更新 `tests/test_legacy_prompt_runtime.py`，要求 runtime build 默认不包含 group/private/tool routing fragment。

2. 构建脚本：
   - `scripts/build_nanobot_prompt.py` 默认 chat_type 改为 `base`。
   - `prompt.md` 对应 `base` 输出。
   - 保留显式 `group` / `private` 构建能力，但输出到独立文件名。

3. Runtime 构建：
   - `core/legacy_prompt_runtime.py` 默认 `chat_type="base"`。
   - `/prompt/build` 调用 base 构建。

4. Prompt fragments：
   - 精简 `00_identity.md`、`05_core.md`、`10_chat_style.md`、`30_tool_discipline.md`。
   - 同步 `prompts.legacy.default/fragments/` 默认片段。
   - 重新生成 `creatures/nanobot/prompt.md`。

5. 验证：
   - `python -B -m pytest tests/test_prompt_contract.py tests/test_legacy_prompt_runtime.py tests/test_prompt_manager.py tests/test_prompt_trace_admin.py tests/test_llm_request_linter.py tests/test_llm_request_sanitizer.py tests/test_final_tools.py tests/test_kt_framework.py tests/test_history.py -q`
   - `git diff --check`
   - vendor 目录状态检查。
