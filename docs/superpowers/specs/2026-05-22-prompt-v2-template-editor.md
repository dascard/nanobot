# Prompt Runtime V2 模板编辑规格

## 目标

在 WebUI 的 `Prompt Runtime V2` 页面直接编辑 V2 规则模板，保存后真实
compiler 优先读取运行时模板，预览和线上请求保持同一套组装逻辑。

## 范围

- V2 模板列表来自默认目录 `prompts.v2.default` 和运行时目录 `data/prompts_v2`。
- WebUI 保存只写入运行时目录，不覆盖 Git 管理的默认模板。
- compiler 读取模板时优先使用运行时目录，缺失时回落到默认目录。
- 当前只对已声明的 section 变量做白名单渲染和校验。
- 当前用户输入、历史、画像、工具说明仍由 compiler 注入，不允许通过模板变量伪造。

## 交互

`Prompt Runtime V2` 页面新增 `V2 模板编辑` 区域：

- 选择模板。
- 编辑模板正文。
- 查看当前模板来源、hash、文件路径。
- 查看可插入变量。
- 点击保存写入运行时模板目录。

保存后重新加载模板列表，并触发现有 effective-preview 重新生成。

## 后端接口

- `GET /api/v1/admin/prompt-v2/templates`
- `GET /api/v1/admin/prompt-v2/templates/{template_key}`
- `PUT /api/v1/admin/prompt-v2/templates/{template_key}`

保存接口复用 `PromptSaveRequest`，并记录 `save_prompt_v2_template`
审计日志。

## 验收

- WebUI 页面有 V2 模板编辑器和保存按钮。
- 保存 `identity_context` 时支持 `{{ character_name }}` 等白名单变量。
- 保存未声明变量时返回 400。
- 保存 `chat_private` 后，`/prompt/effective-preview` 的 V2 plan 使用运行时模板。
