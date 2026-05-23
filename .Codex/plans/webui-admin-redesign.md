# WebUI 管理台重设计实现计划

**目标：** 修复 Nanobot WebUI 的结构性 UI/UX 问题：移动端不可用、导航过长、表单不可访问、复杂页面密度失控、Prompt flow 缺少小屏降级、emoji 操作按钮不一致。

**设计来源：** `docs/superpowers/specs/2026-05-23-webui-admin-redesign.md` 与 `design-system/nanobot-admin/MASTER.md`。

**技术栈：** React、Vite、Tailwind、pytest、Playwright。

---

### 任务 1：补充失败测试

**文件：**
- 创建：`tests/test_webui_admin_redesign.py`
- 修改：`tests/test_webui_prompt_runtime_ui.py`

- [x] 覆盖导航分组和移动端抽屉入口。
- [x] 覆盖登录 token 输入具有 label。
- [x] 覆盖模型筛选、LLM 日志筛选、Reply case 输入具备 label / aria-label。
- [x] 覆盖贴纸治理不再使用 emoji 操作按钮。
- [x] 覆盖 Prompt flow 存在移动端结构化降级。

### 任务 2：统一基础组件

**文件：**
- 修改：`webui/src/components/ui.jsx`
- 修改：`webui/src/index.css`
- 修改：`webui/package.json`
- 修改：`webui/package-lock.json`

- [x] 引入图标组件依赖。
- [x] 增加 `PageHeader`、`Toolbar`、`Field`、`IconButton`、`SectionHeader`。
- [x] 收口 `Card`、`Modal`、`JsonBlock` 的圆角、对比度和响应式宽度。
- [x] 增加减少动画偏好处理。

### 任务 3：重构应用布局和导航

**文件：**
- 修改：`webui/src/App.jsx`

- [x] 将扁平导航改为分组导航。
- [x] 桌面保留侧栏，移动端改为顶部栏 + 抽屉。
- [x] 主内容改为自然滚动和响应式内边距。
- [x] 登录页改为后台风格，并给 token 输入增加显式 label。

### 任务 4：修复重点页面可用性

**文件：**
- 修改：`webui/src/App.jsx`

- [x] Prompt flow 桌面保留 canvas，小屏提供节点/连线列表降级。
- [x] LLM API 日志筛选栏使用显式 label。
- [x] 模型管理筛选栏使用显式 label。
- [x] Reply 评估新建和编辑 case 表单使用显式 label。
- [x] 贴纸治理 emoji 按钮替换为图标按钮。

### 任务 5：验证

**文件：**
- 修改：`webui/dist/index.html`
- 修改：`webui/dist/assets/*`

- [x] 运行新增和相关 pytest。
- [x] 运行 `npm run build`。
- [x] 启动本地前后端并用 Playwright 检查桌面、移动端重点页面。
- [x] 确认没有遗留前端服务进程。
