# 生图工具实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 test-driven-development 逐步实现，完成前使用 verification-before-completion。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新增 `image_generation` KT 工具，通过 new-api `/v1/responses` 的 `image_generation` 工具生成 PNG，并返回可交给 `reply` 的短 token。

**架构：** 按现有 package tool 模式新增真实工具实现和 `nanobot_kt.tools` 导入桥接，在 config、工具元数据、schema 预览、Prompt V2 模板和轻量预设中完成注册。网络调用使用标准库 `urllib.request`，沿用 `image_summary` 的代理禁用和同步请求放入 `asyncio.to_thread` 模式。

**技术栈：** Python、KohakuTerrarium BaseTool、urllib、pytest、new-api Responses SSE。

---

### 任务 1：红灯测试

**文件：**
- 创建：`tests/test_image_generation_tool.py`

- [x] **步骤 1：编写失败的测试**

覆盖：
- `ImageGenerationTool` 元数据和 schema。
- 缺少 `prompt` 返回错误。
- mock SSE 成功结果，断言 URL 是 `/responses`，headers 含 `Accept: text/event-stream`，payload 使用 `model=gpt-image`、`tools[0].type=image_generation`、`stream=True`。
- mock SSE 无图片结果，返回错误。
- 工具注册出现在 `config.yaml`、`TOOL_METADATA`、`build_tool_schema`、轻量预设中。

- [x] **步骤 2：运行测试验证失败**

运行：`python -B -m pytest tests/test_image_generation_tool.py -q`

预期：FAIL，错误为模块或工具类不存在。

### 任务 2：实现工具

**文件：**
- 创建：`creatures/nanobot/prompts/skills/image_generation/tool.py`
- 创建：`core/generated_images.py`
- 创建：`nanobot_kt/tools/image_generation.py`
- 修改：`creatures/nanobot/prompts/skills/reply/tool.py`
- 修改：`config.py`

- [x] **步骤 1：新增配置常量**

在 `config.py` new-api 配置附近添加：
- `IMAGE_GENERATION_MODEL`
- `IMAGE_GENERATION_TIMEOUT`

- [x] **步骤 2：新增工具类**

实现：
- `tool_name == "image_generation"`
- DIRECT 执行
- 参数：`prompt` 必填，`size`、`quality`、`background` 可选
- `_build_payload`
- `_iter_sse_objects`
- `_call_new_api`
- `_execute`
- 生成图片保存到 `data/generated_images`，工具输出只返回 `[generated_image:...]`

- [x] **步骤 3：新增 KT 导入桥接**

`nanobot_kt/tools/image_generation.py` 从真实工具模块导入 `ImageGenerationTool`。

- [x] **步骤 4：扩展 reply token 展开**

`reply` 工具在最终发送前把 `[generated_image:...]` 展开为 `[CQ:image,file=base64://...]`。

- [x] **步骤 5：运行测试验证通过当前任务**

运行：`python -B -m pytest tests/test_image_generation_tool.py -q`

预期：工具行为相关测试通过，注册相关测试仍可能失败。

### 任务 3：注册与提示

**文件：**
- 修改：`creatures/nanobot/config.yaml`
- 修改：`core/tool_registry.py`
- 修改：`core/tool_schema_preview.py`
- 修改：`core/runtime_tool_service.py`
- 修改：`core/config_registry.py`
- 修改：`core/prompt_v2/template_registry.py`
- 修改：`creatures/nanobot/prompts/system/27_tool_routing.md`
- 修改：`nanobot_kt/output.py`
- 创建：`prompts.v2.default/tools/image_generation/usage.md`

- [x] **步骤 1：注册工具**

将 `image_generation` 加入 KT package tools、`TOOL_METADATA`、schema preview 映射。

- [x] **步骤 2：默认启用**

私聊和群聊默认开启，并加入轻量预设默认集合。

- [x] **步骤 3：补提示词边界**

说明 `image_generation` 只用于用户明确要求生成/画图/出图，不用于识图；识图继续用 `image_summary`。

- [x] **步骤 4：运行测试验证通过**

运行：`python -B -m pytest tests/test_image_generation_tool.py -q`

预期：全部通过。

### 任务 4：回归验证

**文件：**
- 无新增文件

- [x] **步骤 1：运行相关工具测试**

运行：`python -B -m pytest tests/test_image_generation_tool.py tests/test_tool_schema_config.py tests/test_final_tools.py tests/test_kt_framework.py::TestCreatureConfig::test_config_loads -q`

预期：全部通过。

- [x] **步骤 2：检查工作区 diff**

运行：`git diff --stat` 和 `git diff --check`

预期：没有空白错误，改动只包含本需求相关文件。
