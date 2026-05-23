# Prompt Runtime V2 模板编辑实现计划

**目标：** 让管理员可以在 WebUI 直接编辑 V2 提示词模板和 PromptPlan 编排图。

**架构：** 后端新增 V2 模板存储层、编排图存储层和管理接口。模板默认从
`prompts.v2.default` 读取，运行时覆盖写入 `data/prompts_v2`；编排图默认从
`prompts.v2.default/chat_flow.json` 读取，运行时覆盖写入 `data/prompts_v2/chat_flow.json`。
前端在 `Prompt V2 模板` 页面内提供节点增删、连接修改、模板选择、编辑、全局变量展示和保存。

**技术栈：** FastAPI、React、Vite、pytest。

---

### 任务 1：编写失败测试

**文件：**
- 创建：`tests/test_prompt_v2_template_admin.py`
- 修改：`tests/test_webui_prompt_runtime_ui.py`

- [x] 覆盖 `/prompt-v2/templates` 列表、详情、保存和变量校验。
- [x] 覆盖 `/prompt-v2/flow` 读取、保存和 effective-preview 使用运行时编排图。
- [x] 覆盖 effective-preview 读取运行时 V2 模板。
- [x] 覆盖 WebUI 源码必须包含 V2 编排图、模板编辑器和保存接口调用。
- [x] 运行测试，确认新行为失败。

### 任务 2：实现后端模板存储

**文件：**
- 修改：`core/prompt_v2/template_loader.py`
- 创建：`core/prompt_v2/template_store.py`
- 修改：`api/admin_routes.py`

- [x] 增加运行时模板目录。
- [x] compiler 加载模板时优先运行时目录。
- [x] 新增列表、详情、保存接口。
- [x] 保存时执行变量白名单校验。

### 任务 3：实现前端编辑器

**文件：**
- 修改：`webui/src/App.jsx`

- [x] 在 `Prompt V2 模板` 页面加载模板列表。
- [x] 在 `Prompt V2 模板` 页面加载编排图。
- [x] 支持选择模板并加载正文。
- [x] 支持添加和删除模板节点、运行时节点。
- [x] 支持修改节点连接关系。
- [x] 按编排图拓扑顺序展示可编辑模板和运行时注入块。
- [x] 展示全局可插入变量白名单和运行时目录。
- [x] 保存模板并刷新预览。
- [x] 保存编排图并刷新预览。

### 任务 3.5：收口默认模板

**文件：**
- 创建：`prompts.v2.default/chat_main.md`
- 创建：`prompts.v2.default/chat_branch_group.md`
- 创建：`prompts.v2.default/chat_branch_private.md`
- 创建：`prompts.v2.default/chat_flow.json`
- 删除：`prompts.v2.default/chat_group.md`
- 删除：`prompts.v2.default/chat_private.md`

- [x] 公共规则放入 `chat_main.md`。
- [x] 群聊差异规则放入 `chat_branch_group.md`。
- [x] 私聊差异规则放入 `chat_branch_private.md`。
- [x] 默认编排图连接公共节点和 chat_type 分支节点。

### 任务 4：验证构建产物

**文件：**
- 修改：`webui/dist/index.html`
- 修改：`webui/dist/assets/*`

- [x] 运行 targeted pytest。
- [x] 运行 prompt trace 兼容测试。
- [x] 运行 `npm run build` 生成生产产物。
