# Prompt Runtime V2 模板编辑实现计划

**目标：** 让管理员可以在 WebUI 直接编辑 V2 提示词模板。

**架构：** 后端新增 V2 模板存储层和管理接口。模板默认从
`prompts.v2.default` 读取，运行时覆盖写入 `data/prompts_v2`。前端在
`Prompt Runtime V2` 页面内提供模板选择、编辑、变量展示和保存。

**技术栈：** FastAPI、React、Vite、pytest。

---

### 任务 1：编写失败测试

**文件：**
- 创建：`tests/test_prompt_v2_template_admin.py`
- 修改：`tests/test_webui_prompt_runtime_ui.py`

- [x] 覆盖 `/prompt-v2/templates` 列表、详情、保存和变量校验。
- [x] 覆盖 effective-preview 读取运行时 V2 模板。
- [x] 覆盖 WebUI 源码必须包含 V2 模板编辑器和保存接口调用。
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

- [x] 在 `Prompt Runtime V2` 页面加载模板列表。
- [x] 支持选择模板并加载正文。
- [x] 展示可插入变量和运行时目录。
- [x] 保存模板并刷新预览。

### 任务 4：验证构建产物

**文件：**
- 修改：`webui/dist/index.html`
- 修改：`webui/dist/assets/*`

- [x] 运行 targeted pytest。
- [x] 运行 prompt trace 兼容测试。
- [x] 运行 `npm run build` 生成生产产物。
