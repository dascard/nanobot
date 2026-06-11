# 生成图片 Gallery 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 test-driven-development 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 Web 管理端展示 `image_generation` 工具生成的所有图片及提示词。

**架构：** 生成图片继续使用本地文件存储，新增 `.json` sidecar 保存提示词和模型元数据。后端通过 admin API 提供分页列表和鉴权图片读取，前端新增拆分页面 `GeneratedImagesPage`，接入侧边栏和路由。

**技术栈：** Python、FastAPI、pytest、React、Vite、Tailwind、现有 `AuthImage` 组件。

---

### 任务 1：生成图片元数据与后端测试

**文件：**
- 修改：`core/generated_images.py`
- 修改：`tests/test_image_generation_tool.py`
- 修改：`tests/test_admin_api.py`

- [x] **步骤 1：编写失败测试**

在 `tests/test_image_generation_tool.py` 中新增断言：
- `save_generated_image()` 写入 `id.json`。
- `list_generated_images(search="猫")` 能按提示词搜索。
- `get_generated_image_path(id)` 返回 PNG 路径。

在 `tests/test_admin_api.py` 中新增断言：
- `GET /api/v1/admin/generated-images` 返回分页项和提示词。
- `GET /api/v1/admin/generated-images/{id}/image` 返回 `image/png`。

- [x] **步骤 2：运行测试验证失败**

运行：`python -B -m pytest tests/test_image_generation_tool.py tests/test_admin_api.py::TestGeneratedImagesAdmin -q`

预期：FAIL，缺少列表函数和 API 路由。

### 任务 2：实现后端能力

**文件：**
- 修改：`core/generated_images.py`
- 修改：`creatures/nanobot/prompts/skills/image_generation/tool.py`
- 修改：`api/admin_routes.py`

- [x] **步骤 1：扩展保存函数**

`save_generated_image()` 增加 `metadata` 参数，并写入 JSON sidecar。工具调用时传入模型、尺寸、质量、背景和完整提示词。

- [x] **步骤 2：新增读取函数**

实现：
- `list_generated_images(page=1, limit=20, search="")`
- `get_generated_image(image_id)`
- `get_generated_image_path(image_id)`

- [x] **步骤 3：新增 admin API**

在 `api/admin_routes.py` 新增：
- `/generated-images`
- `/generated-images/{image_id}/image`

- [x] **步骤 4：运行后端测试**

运行：`python -B -m pytest tests/test_image_generation_tool.py tests/test_admin_api.py::TestGeneratedImagesAdmin -q`

预期：PASS。

### 任务 3：前端页面测试

**文件：**
- 修改：`tests/test_webui_app_split.py`

- [x] **步骤 1：编写失败测试**

断言：
- `GeneratedImagesPage` 不在 `App.jsx` 内定义。
- `App.jsx` 从 `./features/generated-images/GeneratedImagesPage` 导入。
- 侧边栏包含 `/generated-images`。
- 页面文件包含 `/generated-images` API、`AuthImage`、完整提示词展示。

- [x] **步骤 2：运行测试验证失败**

运行：`python -B -m pytest tests/test_webui_app_split.py -q`

预期：FAIL，页面文件和路由尚不存在。

### 任务 4：实现前端页面

**文件：**
- 创建：`webui/src/features/generated-images/GeneratedImagesPage.jsx`
- 修改：`webui/src/App.jsx`

- [x] **步骤 1：创建页面**

页面包含：
- 标题和总数。
- 搜索框、分页大小选择、刷新按钮。
- 图片网格。
- 点击预览弹窗。

- [x] **步骤 2：接入导航与路由**

在「数据治理」下新增导航项 `/generated-images`，路由指向 `GeneratedImagesPage`。

- [x] **步骤 3：运行前端静态测试**

运行：`python -B -m pytest tests/test_webui_app_split.py -q`

预期：PASS。

### 任务 5：验证

**文件：**
- 无新增

- [x] **步骤 1：运行相关测试**

运行：`python -B -m pytest tests/test_image_generation_tool.py tests/test_admin_api.py::TestGeneratedImagesAdmin tests/test_webui_app_split.py -q`

预期：PASS。

- [x] **步骤 2：构建 WebUI**

运行：`npm run build`

预期：exit 0。

- [x] **步骤 3：检查 diff**

运行：`git diff --check`

预期：无空白错误。
