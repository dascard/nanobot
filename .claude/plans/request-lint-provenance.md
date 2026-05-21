# LLM 请求 lint 与 provenance 实现计划

1. RED 测试：
   - 新增 `tests/test_llm_request_linter.py`。
   - 验证 linter 能提取 actual tools、policy enabled/disabled、KT framework docs 和内部 user 消息问题。
   - 验证 `LLMRequestTracer.record_request()` 会把 lint 字段写入 `LLMApiRequestLog`。

2. 后端实现：
   - 新增 `core/llm_request_linter.py`。
   - `LLMApiRequestLog` 增加 lint/provenance 字段。
   - `init_db()` 热迁移旧库字段。
   - `LLMRequestTracer.record_request()` 在真实请求记录点运行非阻塞 lint。

3. 前端实现：
   - LLM API 日志详情展示 Request Lint。
   - 展示 Actual Sent Tools、Policy Enabled、Policy Disabled、Framework Docs。
   - 展开查看 Message Sources 和 raw lint JSON。

4. 验证：
   - `python -B -m pytest tests/test_llm_request_linter.py tests/test_llm_request_tracing.py tests/test_prompt_trace_admin.py tests/test_reply_admin.py -q`
   - `npm run build` in `webui/`
   - `git diff --check`
   - vendor 目录状态检查。
