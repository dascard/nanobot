# Web 生图测试与 SQLite 锁日志设计

## 背景

管理端已经有「生成图片」Gallery，可以展示 `image_generation` 工具生成的图片及提示词，但缺少直接在 Web 端输入提示词并触发生图的入口。当前测试生图需要走真实聊天链路，不适合调试 new-api 参数、图片保存和 Gallery 展示。

线上日志还出现类似：

- `[SQLite] write locked; retrying label=llm_api_request_log attempt=2/4`
- `[SQLite] write locked; retrying label=group_ambient_log attempt=2/4`

代码已有 SQLite `busy_timeout`、WAL 和应用层重试。根因调查显示，这两个 label 都是短写事务，日志来自 `run_sqlite_locked_retry()` 在第一次可恢复锁竞争后立即用 warning 输出。也就是说这类日志不一定代表写入失败，更多是可恢复锁竞争被提升成 warning 噪声。

## 目标

- 在 Web 管理端的「生成图片」页面提供一个直接生图测试面板。
- 测试面板支持提示词、尺寸、质量、背景参数。
- 生图完成后立即展示本次结果，并刷新 Gallery。
- 后端新增 admin 鉴权 POST 接口，直接复用 `ImageGenerationTool`。
- SQLite 可恢复锁重试不再在第一次重试就输出 warning；只有接近重试耗尽时才 warning。

## 非目标

- 不接入完整 KT 对话流程，不模拟 QQ 发送。
- 不新增删除、重新生成、收藏等 Gallery 管理动作。
- 不改变 SQLite 存储引擎或引入写队列。
- 不改变现有 retry 次数和延迟环境变量。

## API 设计

新增：

`POST /api/v1/admin/generated-images`

请求体：

```json
{
  "prompt": "画一只红熊猫喝奶茶，贴纸风格",
  "size": "1024x1024",
  "quality": "high",
  "background": "auto"
}
```

响应体：

```json
{
  "ok": true,
  "item": {
    "id": "img_...",
    "prompt": "...",
    "image_url": "/api/v1/admin/generated-images/img_.../image"
  },
  "tool_output": {
    "reply_token": "[generated_image:img_...]",
    "text_output": ""
  }
}
```

错误处理：

- 空提示词返回 422。
- 工具返回错误或 new-api 调用失败返回 502。
- 工具输出损坏、缺少 `reply_token` 或找不到保存文件返回 500。

## 前端设计

在 `GeneratedImagesPage` 顶部增加一个工具面板：

- 左侧：提示词 textarea。
- 右侧：尺寸、质量、背景选择器和生成按钮。
- 下方：最近生成结果缩略图、提示词、模型和规格。

交互：

1. 用户输入提示词并点击生成。
2. 前端调用 `POST /generated-images`。
3. 成功后展示最近结果，清空搜索并回到第一页。
4. 调用列表接口刷新 Gallery。
5. 失败时在面板内显示错误，不影响当前 Gallery。

## SQLite 锁日志策略

保留现有重试逻辑，只调整日志级别：

- 早期可恢复 retry 使用 `logger.info()`。
- 下一次已经是最后一次尝试时使用 `logger.warning()`。
- 最终耗尽后仍由调用方现有异常处理输出业务 warning。

这样仍能保留锁竞争诊断信息，但不会让一次正常恢复的锁等待污染 warning 日志。

## 测试

- Admin API：POST 生图接口会调用工具、返回 Gallery item、图片可读取。
- Admin API：工具失败时返回 502。
- WebUI 静态测试：页面包含测试面板、POST `/generated-images`、参数控件和最近结果展示。
- SQLite retry：单次锁竞争只记 info 不记 warning；接近耗尽时仍记 warning。
