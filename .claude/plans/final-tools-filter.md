# 最终 tools_schema 硬裁剪实现计划

1. RED 测试：
   - 新增 `tests/test_final_tools.py`。
   - 验证 payload 过滤会移除未允许工具和 `skill`。
   - 验证空工具集会移除 `tools` / `tool_choice`。
   - 验证 OpenAI SDK tracer 在调用原始 SDK 前过滤。
   - 验证 NewAPIClient `_build_payload()` 也使用当前 final tools scope。

2. 后端实现：
   - 新增 `core/final_tools.py`。
   - 提供 `FinalToolSet`、ContextVar scope、`resolve_final_tools()`、`filter_payload_tools()`。
   - `NewAPIClient._build_payload()` 调用 `filter_payload_tools()`。
   - OpenAI SDK tracer 使用过滤后的 kwargs 记录并调用原始 SDK。

3. bridge 接入：
   - 每轮 handle_message 中解析 `FinalToolSet`。
   - 设置当前 final tools scope。
   - run 结束时重置 scope，避免串到下一轮。
   - 继续保留现有 registry 移除逻辑作为框架层补充。

4. 验证：
   - `python -B -m pytest tests/test_final_tools.py tests/test_llm_request_tracing.py tests/test_llm_request_linter.py tests/test_kt_framework.py tests/test_history.py -q`
   - `git diff --check`
   - vendor 目录状态检查。
