# 模型路由 enable_thinking 设计

## 背景

模型路由编辑页需要支持为每个 route 配置 `enable_thinking`。该字段用于控制 OpenAI-compatible 请求中的 thinking 行为，尤其是 DeepSeek / R1 / thinking 类模型默认可能返回 `reasoning_content` 或暴露推理片段。

## 目标

- WebUI 路由编辑表单提供 `enable_thinking` 选择。
- 后端路由编辑接口保存该字段，并在 `/models/status` 和 `/models/routes/{route_key}/resolved` 中返回。
- 真实模型请求根据 route 配置生成 payload 或 controller `extra_body`。
- 保持现有默认行为：未配置时为 `auto`，DeepSeek/R1/reasoning 模型默认禁用 thinking。

## 字段语义

`enable_thinking` 使用三态字符串：

- `auto`：沿用系统默认策略。
- `true`：启用 thinking，不注入禁用参数。
- `false`：禁用 thinking，注入 `{"thinking": {"type": "disabled"}}`。

## 影响范围

- `api/admin_routes.py`：路由编辑接口 schema、保存逻辑和状态返回。
- `clients/classifier_client.py`：解析 route 时返回 `enable_thinking`，子路由继承 timing_gate。
- `clients/new_api_client.py`：构建 chat/completions payload 时应用 thinking 策略。
- `nanobot_kt/bridge.py`：reply route 同步到 KT controller 时设置 `extra_body`。
- `webui/src/App.jsx`：路由卡片显示和编辑弹窗选择器。

## 验证

- 后端 API 测试验证保存与读取。
- 路由解析测试验证继承。
- payload 单元测试验证 `auto/true/false` 行为。
- 前端执行构建验证。
