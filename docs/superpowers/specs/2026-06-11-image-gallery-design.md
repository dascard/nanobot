# 生成图片 Gallery 设计

## 背景

`image_generation` 工具已经把 new-api 生成的 PNG 保存到本地，并通过 `[generated_image:...]` token 交给 `reply` 展开。当前保存逻辑只保留图片文件，Web 端无法集中查看生成结果，也无法回溯生成提示词。

## 目标

- Web 管理端新增「生成图片」页面，展示所有由 `image_generation` 工具保存的图片。
- 每张图展示原始提示词、生成时间、模型、尺寸、质量、文件大小等基础信息。
- 支持按提示词搜索、分页、刷新和点击放大预览。
- 图片接口必须走 admin 鉴权，不直接暴露本地路径。

## 非目标

- 不新增数据库表或迁移。
- 不提供删除、编辑、重新生成、收藏等管理操作。
- 不把生成图片混入表情包库。
- 不改变现有 `reply` token 展开协议。

## 方案

采用文件 sidecar 元数据方案：

1. `save_generated_image()` 保存 `id.png` 的同时保存 `id.json`。
2. JSON 元数据包含 `id`、`prompt`、`created_at`、`bytes`、`sha256`、`model`、`size`、`quality`、`background`。
3. `core.generated_images` 提供 `list_generated_images()`、`get_generated_image()` 和安全路径解析。
4. 后端新增 admin API：
   - `GET /api/v1/admin/generated-images?page=&limit=&search=`
   - `GET /api/v1/admin/generated-images/{image_id}/image`
5. 前端新增 `GeneratedImagesPage`，使用现有 `AuthImage` 获取鉴权图片 Blob。

## 数据流

1. 用户触发生图，`ImageGenerationTool` 收到模型返回的 base64 PNG。
2. 工具调用 `save_generated_image(image_b64, metadata={...})`。
3. 保存模块写入 PNG 和 JSON sidecar。
4. WebUI 请求列表接口，后端扫描 JSON sidecar 并按 `created_at` 倒序返回。
5. WebUI 用 `AuthImage` 请求单图接口，后端校验 ID 后返回 `FileResponse`。

## UI 设计

后台工具页风格保持安静、密集、可扫描：

- 顶部：标题、总数、刷新按钮。
- 工具栏：提示词搜索框、分页大小。
- 主体：响应式图片网格，卡片包含缩略图、提示词摘要、模型/尺寸/质量、生成时间和文件大小。
- 空状态：说明暂无生成图片或当前搜索无结果。
- 点击图片：打开大图预览弹窗，右侧显示完整提示词和元数据。

## 错误处理

- 元数据 JSON 损坏：列表跳过该项并记录 warning。
- PNG 缺失：列表仍返回该项但标记 `missing_file=true`，图片接口返回 404。
- 非法 `image_id`：返回 404。
- `page`、`limit` 做边界限制，`limit` 最大 100。

## 测试

- `core.generated_images`：保存时写入 JSON，列表按时间倒序，搜索提示词，非法 ID 不能越权访问路径。
- Admin API：列表接口返回分页结果，图片接口返回 PNG，缺失图片返回 404。
- WebUI 静态测试：新增页面从 `App.jsx` 拆分导入，路由和导航存在，页面使用 `AuthImage` 和 `/generated-images` API。
