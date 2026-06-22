# 普通 API Sticker / Media 路由拆分设计

日期：2026-06-22

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前仍只剩普通
`api/routes.py`。前序已完成 task、memory、models、evolution 和 history / log
拆分，`api/routes.py` 当前为 2134 行。本阶段继续降低该文件职责密度，但仍避开
`/chat` 与 `/group/message` 主链路，避免碰到 prompt runtime、conversation 结构、
消息信封和群聊时机决策。

本阶段选择 sticker / media route-only 拆分。它覆盖普通 API 中表情包注册、搜索、
禁用，以及公开 sticker / generated image 图片代理端点；这些 endpoint 与聊天主流程
没有直接依赖，且行数收益高于 `chat-step / render` 小刀。

## 候选方案

### 方案 A：Sticker / Media 路由拆分（推荐）

新增 `api/sticker_media_routes.py`，迁移：

- `StickerRegisterRequest`
- `POST /stickers/register`
- `GET /stickers/search`
- `GET /stickers/{sticker_id}/image`
- `GET /generated-images/{image_id}/image`
- `POST /stickers/{sticker_id}/disable`

收益约 130-160 行。职责边界清晰，核心行为已在 `core.sticker_memory`、
`core.sticker_preview` 和 `core.generated_images` 中，API 层主要负责依赖注入、
公开图片 token 校验、HTTP 错误映射和返回 `FileResponse`。

主要风险：

- `public_sticker_image()` 和 `public_generated_image()` 不能增加普通 API bearer
  鉴权，只能保留现有可选环境 token。
- `/stickers/register` 与 `/stickers/search` 必须继续早于动态
  `/stickers/{sticker_id}/...` 路由。
- 旧 `api.routes` 导入兼容必须保留，尤其是测试和脚本中从父模块导入
  `StickerRegisterRequest` 与 `register_sticker_endpoint()` 的用法。

### 方案 B：Agent Step / Render 路由拆分

新增 `api/agent_step_routes.py`，迁移 `/chat-step` 和遗留 `/render`。风险最低，
因为核心协议在 `core.agent_step`，路由层只做 SSE framing 与 payload 转换；但收益
约 25-35 行，低于本阶段优先级。该方案保留为下一轮小刀候选。

### 方案 C：聊天图片 helper 拆分

迁移 `_normalize_files()`、`_schedule_image_precache()`、`_build_multimodal_user_input_text()`、
`_build_chatlog_user_content()` 等聊天图片相关 helper。该方案看似同属 media，但实际
直接服务 `/chat`、guardrail、history 落库和 prompt 输入构造，风险高于 route-only
拆分。本阶段不采用。

## 目标

新增 `api/sticker_media_routes.py` 并从 `api/routes.py` 迁移 sticker / media HTTP 层。
完成后必须满足：

- 路径、方法、请求参数、状态码、响应结构和数据库写入语义不变。
- `api.sticker_media_routes.router` 不带 `/api/v1` 前缀，由父
  `api.routes.router` include。
- `api.routes` 继续 re-export：
  - `StickerRegisterRequest`
  - `register_sticker_endpoint`
  - `search_sticker_endpoint`
  - `public_sticker_image`
  - `public_generated_image`
  - `disable_sticker_endpoint`
- `api.sticker_media_routes` 使用 `api.common_auth.verify_token`，保持
  `api.routes.NANOBOT_API_TOKEN` monkeypatch 兼容。
- `public_sticker_image()` 继续只使用 `NANOBOT_STICKER_IMAGE_TOKEN` 可选 query token，
  不增加 `verify_token` 依赖。
- `public_generated_image()` 继续只使用 `NANOBOT_GENERATED_IMAGE_TOKEN` 可选 query token，
  不增加 `verify_token` 依赖。
- `/stickers/register` 与 `/stickers/search` 继续早于动态 sticker 路由。
- `register_sticker_endpoint()` 继续通过 `BackgroundTasks.add_task()` 排队
  `auto_describe_sticker()`，不改为同步等待或 async 包装。
- `search_sticker_endpoint()` 继续把 `limit` clamp 到 1-20。
- `public_sticker_image()` 继续支持 duplicate row 跳转到 canonical sticker，并保留
  inactive / missing / cache unavailable / local file missing 的 HTTP 映射。
- `public_generated_image()` 继续把缺失图片映射为 404，并返回 `image/png`。
- `api.routes` 继续保留：
  - `/chat`
  - `/group/message`
  - `ChatProxyRequest`
  - `GroupMessageRequest`
  - `_persist_chat_turn()`
  - `_safe_meta()`
  - `_normalize_files()`
  - `_schedule_image_precache()`
  - `_build_multimodal_user_input_text()`
  - `_build_chatlog_user_content()`
  - `_build_conversation_user_content()`
  - `_group_sticker_payloads`
  - `_register_group_stickers_from_message`
  - `memory`
  - `init_legacy_memory()`
  - `evolution_task`
  - `/health`
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约、
  message envelope 或 QQ outbound renderer。

## 模块边界

### `api/sticker_media_routes.py`

职责：

- 定义 `router = APIRouter(tags=["sticker-media"])`。
- 定义 `StickerRegisterRequest`。
- 定义 sticker 注册、搜索、公开图片代理、generated image 公开图片代理和 disable
  endpoint。
- 持有本模块 logger：`logging.getLogger("nanobot.routes.sticker_media")`。

依赖：

- `logging`
- `os`
- `hmac.compare_digest`
- `fastapi.APIRouter`
- `fastapi.BackgroundTasks`
- `fastapi.Depends`
- `fastapi.HTTPException`
- `fastapi.responses.FileResponse`
- `pydantic.BaseModel`
- `pydantic.Field`
- `sqlalchemy.orm.Session`
- `api.common_auth.verify_token`
- `core.database.get_db`
- `core.database.StickerMemory`
- `core.generated_images.get_generated_image_path`
- `core.sticker_memory.auto_describe_sticker`
- `core.sticker_memory.disable_sticker`
- `core.sticker_memory.register_sticker`
- `core.sticker_memory.search_stickers`
- `core.sticker_preview.cache_sticker_preview`
- `core.sticker_preview.media_type_for_path`
- `core.sticker_preview.safe_existing_local_path`

兼容策略：

- 子模块不导入 `api.routes`。
- 有 bearer 鉴权的端点继续依赖 `api.common_auth.verify_token`。
- 公开图片代理端点继续无 bearer 依赖，仅保留环境 token。
- `api.routes` 从子模块导入并 re-export 迁移符号。

禁止：

- 不迁移 `/chat`。
- 不迁移 `/group/message`。
- 不迁移任何聊天图片 helper。
- 不迁移群聊 sticker facade。
- 不迁移 `core.qq_outbound_renderer` 或 generated image 文本展开逻辑。
- 不修改 `server.py` 或 `bootstrap/lifespan.py`。

### `api/routes.py`

职责：

- 继续作为 `/api/v1` 聚合 router。
- 从 `api.sticker_media_routes` import `router as sticker_media_router` 和旧导入兼容符号。
- 删除本地 `StickerRegisterRequest` 和 5 个 sticker / media endpoint 实现。
- 在尾部普通子路由 include 区加入：

```python
router.include_router(sticker_media_router)
```

include 顺序建议：

```python
router.include_router(evolution_router)
router.include_router(history_log_router)
router.include_router(memory_router)
router.include_router(model_router)
router.include_router(task_router)
router.include_router(sticker_media_router)
```

子模块内部 endpoint 顺序必须保持：

1. `POST /stickers/register`
2. `GET /stickers/search`
3. `GET /stickers/{sticker_id}/image`
4. `GET /generated-images/{image_id}/image`
5. `POST /stickers/{sticker_id}/disable`

## 测试策略

新增 `tests/test_api_sticker_media_routes_split.py`，覆盖拆分契约：

- 5 个 endpoint 注册来源均为 `api.sticker_media_routes`。
- 5 个 endpoint 不重复注册。
- `api.routes` 旧导入符号与 `api.sticker_media_routes` 为同一对象。
- `/stickers/search` 继续使用旧 `api.routes.NANOBOT_API_TOKEN` monkeypatch。
- `api/sticker_media_routes.py` 不导入 `api.routes`，不包含 `asyncio.run` 或
  `run_awaitable_sync`。
- `/stickers/register` 和 `/stickers/search` 的 route index 早于动态 sticker 路由。
- 公开 sticker image 端点保留环境 token 边界：错误 token 返回 403，正确 token 下
  缺失 sticker 返回 404。
- 公开 generated image 端点保留环境 token 边界：错误 token 返回 403，正确 token 下
  缺失 generated image 返回 404。
- `/chat`、`/group/message`、`_persist_chat_turn()`、`_safe_meta()` 留在父模块。
- `_group_sticker_payloads` 与 `_register_group_stickers_from_message` 继续指向
  `app.group_ingress.helpers`。

继续运行现有行为回归：

- `tests/test_api.py::test_sticker_register_search_and_disable_api`
- `tests/test_api.py::test_public_sticker_image_returns_cached_file`
- `tests/test_api.py::test_sticker_register_auto_describe_adds_background_task`
- `tests/test_sticker_memory.py`
- `tests/test_sticker_rag.py`
- `tests/test_sticker_tool.py`
- `tests/test_image_generation_tool.py`
- `tests/test_push_envelope.py`
- `tests/test_qq_outbound_renderer.py`

全量验证：

- `python -B -m pytest -p no:cacheprovider tests/ -v`

## 提交拆分

本阶段按以下提交粒度推进：

1. 设计文档：`docs(普通API): 设计贴纸媒体路由拆分`
2. 实现计划：`docs(计划): 记录贴纸媒体路由拆分计划`
3. 红灯测试：`test(普通API): 锁定贴纸媒体路由拆分契约`
4. 代码拆分：`refactor(普通API): 拆分贴纸媒体路由`
5. 文档收口：`docs(计划): 收口贴纸媒体路由拆分`

## 验收清单

- [ ] 新增 `api/sticker_media_routes.py`。
- [ ] `api.sticker_media_routes` 不导入 `api.routes`。
- [ ] `api.sticker_media_routes` 不包含 `asyncio.run` 或 `run_awaitable_sync`。
- [ ] `api.routes` re-export `StickerRegisterRequest` 和 5 个 endpoint。
- [ ] 5 个 sticker / media endpoint 注册来源均为 `api.sticker_media_routes`。
- [ ] 5 个 sticker / media endpoint 没有重复注册。
- [ ] `/stickers/register` 与 `/stickers/search` 早于动态 sticker 路由。
- [ ] bearer 鉴权端点继续兼容 `api.routes.NANOBOT_API_TOKEN` monkeypatch。
- [ ] 公开图片代理端点不增加 bearer 鉴权。
- [ ] `NANOBOT_STICKER_IMAGE_TOKEN` 和 `NANOBOT_GENERATED_IMAGE_TOKEN` 行为不变。
- [ ] `/chat` 与 `/group/message` 主链路未迁移。
- [ ] `_persist_chat_turn()`、`_safe_meta()`、聊天图片 helper 和群聊 sticker facade
  继续留在父模块。
- [ ] 现有 sticker、generated image、push renderer 相关回归通过。
- [ ] 全量 `tests/` 回归 0 failures。
