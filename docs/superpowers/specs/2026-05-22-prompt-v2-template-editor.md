# Prompt Runtime V2 模板编辑规格

## 目标

在 WebUI 的 `Prompt V2 模板` 页面编辑 V2 规则模板和编排图。保存后真实
compiler 优先读取运行时模板与运行时编排图，预览和线上请求保持同一套组装逻辑。

## 范围

- V2 模板列表来自默认目录 `prompts.v2.default` 和运行时目录 `data/prompts_v2`，列表返回 `kind` 与 `tool_name`，用于区分聊天编排模板和工具模板。
- 编排图来自 `prompts.v2.default/chat_flow.json`，运行时覆盖写入 `data/prompts_v2/chat_flow.json`。
- WebUI 保存只写入运行时目录，不覆盖 Git 管理的默认模板。
- compiler 读取模板和编排图时优先使用运行时目录，缺失时回落到默认目录。
- 变量是全局白名单，任意模板节点都只能使用同一批允许变量。
- 当前用户输入、历史、画像、工具说明仍由 compiler 注入，不允许通过模板变量伪造。
- 默认聊天模板收口为公共主模板 `chat_main.md` 加 `chat_branch_group.md`、`chat_branch_private.md` 两个分支模板，不再保留重复的大段 group/private 主模板。
- 工具提示词模板不进入聊天主流程画布，按工具独立编辑；例如 `sql_analysis.md` 归属 `sql_analysis` 工具，`reply_contract_retry.md` 归属 `reply / no_reply` 合约链路。
- 工具模板必须参与真实运行：主模型请求中的 `runtime_tool_prompt` 与 OpenAI-compatible `tools[].function.description` 都要从 V2 工具模板读取；有二次 LLM 调用的工具也必须在调用前从 V2 模板渲染内部 system/user prompt，代码常量只允许作为模板缺失时的兜底。

## 交互

`Prompt V2 模板` 页面分为两个工作区：`聊天编排` 和 `工具模板`。

`聊天编排` 展示可编辑的 PromptPlan canvas 编排图：

- 画布展示完整流程图，而不是用线性列表代替流程。
- 选择群聊或私聊时只切换高亮路径，不把模板列表伪装成图选项。
- 从后端加载 `chat_flow.json`，用 SVG 绘制节点连线。
- 节点可在 canvas 内拖拽，位置保存到 flow 节点属性。
- 画布支持滚轮缩放、空白区域拖动画布和平移/缩放重置。
- 自由添加模板节点或运行时注入节点。
- 添加模板节点不再要求先在左侧选择模板；新节点会接到当前选中节点后面，随后在右侧节点检查器选择该节点使用的模板。
- 删除节点时同时删除相关连接。
- 节点右侧输出端口可拖出预览线，松到目标节点左侧输入端口完成连接；不使用按钮式连线文案。
- 点击已有连线后，在右侧检查器可删除当前连线。
- 点击模板节点后在右侧编辑对应模板正文。
- 右侧模板内容区提供“大窗编辑”按钮，打开居中的大尺寸浮窗编辑同一份模板正文，适合长规则维护。
- 运行时节点在界面显示中文名称，例如"运行上下文""工具运行说明"，不把 `runtime_key` 暴露成表单标签。
- 编辑模板正文。
- 查看当前模板来源、hash、文件路径。
- 查看全局可插入变量白名单。
- 点击保存模板写入运行时模板目录。
- 点击保存编排图写入运行时 `chat_flow.json`。

`工具模板` 工作区：

- 按工具列出独立模板，而不是和聊天回复共用一个模板选择器。
- 左侧工具列表是唯一的工具模板选择入口。
- 中间主区域必须直接展示并编辑当前工具提示词正文，不放只读展示卡片。
- 右侧展示当前工具元信息和真实 OpenAI-compatible tools schema，包括 description、parameters、required、source、category 和 risk_level。
- 工具 schema 来自实际工具类 `get_parameters_schema()`，不是从模板 description 伪造。
- 每个工具模板仍使用同一套全局变量白名单校验。
- 默认工具模板覆盖运行时可见工具：`reply`、`no_reply`、`sql_analysis`、`python_sandbox`、`ai_daily`、`news_search`、`image_summary`、`persona_update`、`schedule_task`、`group_analysis`、`sticker_search`。
- `group_analysis` 默认模板必须覆盖真实工具职责：群解析、时间窗口、消息清洗、话题总结、活跃用户称号、群聊金句、活跃度分布、聊天质量锐评和 HTML 日报直出；其内部 `topics/titles/quotes/quality` 四个 LLM 分支也必须有独立 V2 模板。
- `news_search` / `ai_daily` / `image_summary` 的内部 LLM system/user prompt 也必须有 V2 模板，避免 WebUI 模板和实际工具提示词脱节。

运行预览页只负责构造 effective-preview，不承担模板编辑职责。

## 后端接口

- `GET /api/v1/admin/prompt-v2/templates`
- `GET /api/v1/admin/prompt-v2/templates/{template_key}`
- `PUT /api/v1/admin/prompt-v2/templates/{template_key}`
- `GET /api/v1/admin/prompt-v2/flow`
- `PUT /api/v1/admin/prompt-v2/flow`

保存接口复用 `PromptSaveRequest`，并记录 `save_prompt_v2_template`
审计日志。编排图保存记录 `save_prompt_v2_flow` 审计日志。

## 验收

- WebUI 页面有 canvas 编排图、模板编辑器、节点拖拽、画布缩放/平移、端口拖拽连线、节点增删、连接修改和保存按钮。
- WebUI 页面可选择并删除已有连线。
- WebUI 页面有独立工具模板工作区，能按工具选择并编辑模板。
- 工具模板工作区中间主区域是提示词编辑器，右侧是实际 schema 预览。
- 保存 `identity_context` 时支持 `{{ character_name }}` 等全局白名单变量。
- 保存未声明变量时返回 400。
- 保存编排图后，`/prompt/effective-preview` 的 V2 plan 使用运行时 `chat_flow.json`。
- 群聊和私聊差异通过分支节点接入，不再维护两份大段重复主模板。
- 修改工具模板后，真实工具 schema description、`runtime_tool_prompt` 和对应工具内部 LLM prompt 必须能通过测试证明读取到同一份 V2 模板。
