# KT 自动工具文档清理实现计划

1. RED 测试：
   - 新增 `tests/test_llm_request_sanitizer.py`。
   - 验证 system 中 KT 自动工具文档被清理。
   - 验证 user 消息不被清理。
   - 验证 NewAPI payload 和 OpenAI SDK tracer 都在请求前清理。

2. 实现 sanitizer：
   - 新增 `core/llm_request_sanitizer.py`。
   - 用 Markdown heading 识别 KT 自动段落。
   - 只处理 system role。
   - 返回副本，避免原地修改调用方数据。

3. 接入真实出口：
   - `clients/new_api_client.py` 调用 `sanitize_payload_messages()`。
   - `core/llm_sdk_tracing.py` 调用 `sanitize_sdk_kwargs()`。
   - 保证日志记录和真实发送都使用同一份清理结果。

4. 验证：
   - `python -B -m pytest tests/test_llm_request_sanitizer.py tests/test_final_tools.py tests/test_llm_request_tracing.py tests/test_llm_request_linter.py tests/test_kt_framework.py tests/test_history.py -q`
   - `git diff --check`
   - vendor 目录状态检查。
