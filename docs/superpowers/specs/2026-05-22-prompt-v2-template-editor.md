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

## 交互

`Prompt V2 模板` 页面分为两个工作区：`聊天编排` 和 `工具模板`。

`聊天编排` 展示可编辑的 PromptPlan canvas 编排图：

- 画布展示完整流程图，而不是用线性列表代替流程。
- 选择群聊或私聊时只切换高亮路径，不把模板列表伪装成图选项。
- 从后端加载 `chat_flow.json`，用 SVG 绘制节点连线。
- 节点可在 canvas 内拖拽，位置保存到 flow 节点属性。
- 画布支持滚轮缩放、空白区域拖动画布和平移/缩放重置。
- 自由添加模板节点或运行时注入节点。
- 删除节点时同时删除相关连接。
- 节点右侧输出端口可拖出预览线，松到目标节点左侧输入端口完成连接；不使用按钮式连线文案。
- 点击模板节点后在右侧编辑对应模板正文。
- 运行时节点在界面显示中文名称，例如"运行上下文""工具运行说明"，不把 `runtime_key` 暴露成表单标签。
- 编辑模板正文。
- 查看当前模板来源、hash、文件路径。
- 查看全局可插入变量白名单。
- 点击保存模板写入运行时模板目录。
- 点击保存编排图写入运行时 `chat_flow.json`。

`工具模板` 工作区：

- 按工具列出独立模板，而不是和聊天回复共用一个模板选择器。
- 点击工具卡片后在右侧编辑该工具当前使用的模板。
- 每个工具模板仍使用同一套全局变量白名单校验。

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
- WebUI 页面有独立工具模板工作区，能按工具选择并编辑模板。
- 保存 `identity_context` 时支持 `{{ character_name }}` 等全局白名单变量。
- 保存未声明变量时返回 400。
- 保存编排图后，`/prompt/effective-preview` 的 V2 plan 使用运行时 `chat_flow.json`。
- 群聊和私聊差异通过分支节点接入，不再维护两份大段重复主模板。
