# Prompt V2 模板工作台修复计划

## 任务

- [x] 将 Prompt V2 模板页改成固定高度三栏工作台。
- [x] 收窄左侧 rail，并将运行时覆盖、添加节点、路径顺序折叠。
- [x] 将画布高度改为工作区填充，最小高度 720px。
- [x] 右侧详情区改为同高内部滚动面板。
- [x] 为画布 wheel 缩放接入 passive:false 原生监听。
- [x] 统一内部滚动条和 overscroll 行为。
- [x] 补充 UI 结构与滚轮行为断言。
- [x] 通过 pytest、lint、build 与浏览器检查。

## 验证命令

- `python -m pytest tests/test_webui_prompt_runtime_ui.py tests/test_webui_admin_redesign.py tests/test_webui_app_split.py -v`
- `npm run lint`
- `npm run build`
- Playwright MCP 桌面工作台尺寸与 wheel defaultPrevented 检查
