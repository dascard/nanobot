# Prompt V2 条件分支实现计划

## 任务

- [x] 为边条件分支写后端红灯测试。
- [x] 为歧义出边写后端红灯测试。
- [x] 为前端选中路径写 UI 结构红灯测试。
- [x] 将 `ordered_nodes_for_chat()` 改为单路径遍历。
- [x] 在 `validate_flow()` 中拒绝同一条件下的多出边。
- [x] 将 WebUI 当前路径和高亮改为基于选中路径。
- [x] 跑 Prompt V2 与 WebUI 相关测试。
- [x] 跑前端 lint/build。

## 验证命令

- `python -m pytest tests/test_prompt_v2.py tests/test_prompt_v2_template_admin.py tests/test_webui_prompt_runtime_ui.py -v`
- `npm run lint`
- `npm run build`
