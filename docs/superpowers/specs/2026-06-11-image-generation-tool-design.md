# 生图工具设计

## 背景

用户希望在现有 KT 工具体系中添加一个生图工具，使用 new-api 的 `gpt-image` 模型，通过 `/v1/responses` 和 `image_generation` 内置工具生成 PNG 图片。

## 目标

- 新增 `image_generation` KT 工具，模型在用户明确要求生成图片、画图、做贴纸风格图片等场景下调用。
- 请求 new-api：`POST {NEW_API_BASE_URL}/responses`，`Accept: text/event-stream`，`stream: true`。
- 解析 SSE 中 `response.output_item.done` 事件里的 `image_generation_call.result` 或 `b64_json`。
- 返回可直接放进 `reply(content)` 的短 token：`[generated_image:...]`，由 `reply` 工具在最终发送前展开为 OneBot CQ 图片码。

## 非目标

- 生图工具自身不新增图片托管 HTTP 路由；后续管理端 Gallery 可单独提供鉴权读取接口。
- 不把生成图片写入数据库。
- 不改变 `image_summary` 的识图/OCR 职责。
- 不修改主聊天链路和模型路由策略。

## 方案选择

### 方案 A：独立工具返回 base64 CQ 图片码

新增 `image_generation` 工具，直接调用 new-api responses 流式接口，返回 JSON，其中包含 `send_code` 和少量元数据。

优点：改动小，能直接和现有 `reply` 工具协作，不需要文件托管。

缺点：图片 base64 会进入工具输出和日志，单次输出较大。

### 方案 B：保存本地文件并返回文件路径

工具保存 PNG 到 `data/generated_images`，返回本地路径。

优点：工具输出较小。

缺点：QQbot 推送服务未必能读取同一文件路径，可用性依赖部署拓扑。

### 方案 C：新增文件托管路由并返回 URL

工具保存 PNG，再通过 FastAPI 暴露带 token 的图片 URL。

优点：输出小，适合生产。

缺点：引入新路由、鉴权、清理策略，超过本次最小需求。

## 决策

采用方案 B 的变体：工具保存 PNG 到 `data/generated_images`，但不要求 QQbot 直接读取本地路径；模型只拿到短 token，最终 `reply` 工具在发送前读取文件并展开为 `[CQ:image,file=base64://...]`。这样保持改动集中，同时避免把大段 base64 暴露给下一轮模型上下文。

## 组件

- `creatures/nanobot/prompts/skills/image_generation/tool.py`：工具实现、payload 构造、SSE 解析、错误处理。
- `core/generated_images.py`：保存生成图片，并把 `[generated_image:...]` 展开成 CQ 图片码。
- `creatures/nanobot/prompts/skills/reply/tool.py`：最终发送前展开生成图片 token。
- `nanobot_kt/tools/image_generation.py`：KT config 导入桥接。
- `creatures/nanobot/config.yaml`：注册 package tool。
- `core/tool_registry.py`：加入工具元数据和默认启用策略。
- `core/tool_schema_preview.py`：让 WebUI/schema 预览能实例化真实 schema。
- `core/runtime_tool_service.py`、`core/config_registry.py`：轻量预设默认包含生图工具。
- `core/prompt_v2/template_registry.py`、`prompts.v2.default/tools/image_generation/usage.md`、`creatures/nanobot/prompts/system/27_tool_routing.md`：让提示词和 schema description 说明使用边界。
- `nanobot_kt/output.py`：流式进度提示显示“正在生成图片...”。

## 数据流

1. Planner 看到用户明确要生成图片时调用 `image_generation(prompt=...)`。
2. 工具组装 `/responses` payload，并设置 `tools=[{"type": "image_generation", ...}]`。
3. 工具读取 SSE 行，忽略普通文本 delta，直到发现完成的 `image_generation_call`。
4. 工具校验 base64 可解码，保存 PNG，返回 JSON：
   - `reply_token`：`[generated_image:...]`
   - `mime`：`image/png`
   - `model`
   - `size`
   - `quality`
   - `text_output`
5. Planner 把 `reply_token` 放进 `reply(content)`。
6. `reply` 工具把 token 展开为 `[CQ:image,file=base64://...]` 后发送给 QQbot。

## 错误处理

- 缺少 `prompt`：返回工具错误。
- 缺少 `NEW_API_KEY`：返回工具错误。
- HTTP/SSE/JSON/base64 解析失败：返回工具错误，记录日志。
- SSE 完成但没有图片结果：返回工具错误。

## 测试

- 工具元数据和参数 schema。
- 缺少 prompt 时失败。
- mock new-api SSE，验证请求 URL、headers、payload 和返回的 `reply_token`。
- SSE 无图片结果时失败。
- 注册链路：`config.yaml`、`TOOL_METADATA`、schema 预览、轻量预设。
