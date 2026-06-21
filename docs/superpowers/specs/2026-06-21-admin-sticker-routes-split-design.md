# Admin Sticker 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前只剩
`api/admin_routes.py` 和 `api/routes.py` 两个硬项。其中 `api/admin_routes.py`
仍有 5535 行，但项目已经形成了 `api/admin/*_routes.py` 子路由拆分模式：
`system`、`db_browser`、`prompt_v2`、`persona`、`rag`、`session_memory` 等
模块均由 `api.admin_routes.router` 统一 include。

本阶段继续沿这个既有模式拆分，不改 `server.py` 的顶层导入，不改 admin 认证入口，不碰
聊天、模型、工具、Prompt Runtime、reply/eval 或 eval 工作台主链路。

## 只读审计结论

本轮并行审计了三个方向：

- `api/routes.py`：低风险候选是公开 sticker / generated image 接口、定时任务接口和
  memory digest / recall 接口。
- `api/admin_routes.py`：低风险且收益较高的候选是 Sticker / Generated Images 管理边界。
- 测试兼容面：普通 `/chat`、`/group/message` 和 admin 认证 monkeypatch 都存在大量旧路径
  依赖，第一刀应避开这些高耦合链路。

综合收益和风险，本阶段选择拆 `api/admin_routes.py` 中的 Sticker / Generated Images
管理边界。它约覆盖 979-1496 行，行数收益明显，已有 `tests/test_admin_api.py` 中
`TestGeneratedImagesAdmin`、`TestStickerCRUD` 和 route shadow 用例覆盖主要行为。

## 方案比较

### 方案 A：拆 `api/admin/sticker_routes.py`（推荐）

迁移 Sticker 管理、生成图片管理、重复贴纸治理、预览重试、批量删除和相关 request model。
`api.admin_routes` include 新 router，并 re-export 旧符号。

优点：

- 一次减少约 500 行，收益明显。
- 复用 admin 子路由既有模式。
- 不触碰聊天、模型、工具和 Prompt Runtime。
- 现有 HTTP 回归覆盖较强。

代价：

- `group_detail()` 仍需要 `_sticker_dict()` 展示群详情中的贴纸记录，旧模块需要从新模块导入该
  helper。
- 新模块必须使用 `api.admin.common.verify_admin` / `audit_request`，确保
  `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 继续生效。

### 方案 B：拆 `api/routes.py` 的公开 media 接口

迁移 `/stickers/register`、公开 sticker image、公开 generated image 等普通 API 接口。

优点：

- 边界也较清晰。
- 不涉及私聊主流程。

代价：

- 普通 API 测试 fixture 只 monkeypatch `api.routes.verify_token`，拆出独立 router 后必须专门设计
  `verify_token` 兼容。
- 行数收益小于 admin sticker 管理边界。

### 方案 C：拆 admin Log Viewer

迁移 `/logs`、`/logs/{name}` 和 `/logs/frontend-error`。

优点：

- HTTP 面小，无数据库写事务。

代价：

- `tests/test_admin_logs_viewer.py` 依赖 `api.admin_routes.__file__` 控制日志目录，迁移时需要专门兼容
  旧 `__file__` 行为。
- 行数收益较小。

## 目标

1. 新增 `api/admin/sticker_routes.py`，承载 admin sticker 与 generated image 管理路由。
2. `api/admin_routes.py` 通过 `router.include_router(sticker_router)` 继续暴露原 HTTP 路径。
3. `api.admin_routes` re-export 迁移后的 request model、endpoint 函数和 `_sticker_dict()`，保持旧导入路径兼容。
4. `api/admin_routes.py` 行数显著下降，且不改变任何响应 shape、状态码、审计动作名或路由路径。
5. 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。

## 迁移范围

迁移到 `api/admin/sticker_routes.py`：

- Request model：
  - `StickerCreate`
  - `StickerUpdate`
  - `GeneratedImageCreate`
  - `NearDuplicateAction`
  - `SetCanonicalBody`
  - `MarkDuplicateBody`
- Helper / 状态：
  - `_sticker_dict()`
  - `_NEAR_DUP_SCAN_LOCK`
- 路由：
  - `POST /stickers`
  - `GET /stickers`
  - `GET /generated-images`
  - `POST /generated-images`
  - `GET /generated-images/{image_id}/image`
  - `GET /stickers/duplicate-groups`
  - `GET /stickers/{sticker_id:int}`
  - `PUT /stickers/{sticker_id}`
  - `POST /stickers/{sticker_id}/enable`
  - `POST /stickers/{sticker_id}/disable`
  - `GET /stickers/{sticker_id}/preview`
  - `POST /stickers/{sticker_id}/redescribe`
  - `POST /stickers/{sticker_id}/preview/retry`
  - `POST /stickers/dedupe/exact/backfill`
  - `GET /stickers/near-duplicate-candidates`
  - `POST /stickers/near-duplicate/scan`
  - `POST /stickers/phash/backfill`
  - `POST /stickers/near-duplicate-candidates/{candidate_id}/{action}`
  - `POST /stickers/{sticker_id}/set-canonical`
  - `POST /stickers/{sticker_id}/mark-duplicate`
  - `POST /stickers/batch-delete`
  - `DELETE /stickers/{sticker_id}`

保留在 `api/admin_routes.py`：

- `router = APIRouter(prefix="/api/v1/admin")`
- `NANOBOT_ADMIN_TOKEN`
- `verify_admin()`、`_audit()`、`_client_ip()`、`_audit_request()`
- `/overview`、`/groups`、`/groups/{group_id:path}`
- 群记忆、TimingGate、配置、模型、工具、reply/eval、eval 工作台、日志 viewer、settings 等其他子域

## 兼容策略

### 顶层 router

`server.py` 继续只导入 `api.admin_routes.router`。新模块不被 `server.py` 直接导入。

### 认证与审计

新模块使用 `api.admin.common.verify_admin` 和 `api.admin.common.audit_request`。
`api.admin.common._current_admin_token()` 已兼容读取 `api.admin_routes.NANOBOT_ADMIN_TOKEN`，
因此现有测试对旧 token 路径的 monkeypatch 仍会影响新路由。

### 旧符号导入

`api/admin_routes.py` 从 `api.admin.sticker_routes` 导入并 re-export：

- request model：`StickerCreate`、`StickerUpdate`、`GeneratedImageCreate`、
  `NearDuplicateAction`、`SetCanonicalBody`、`MarkDuplicateBody`
- helper：`_sticker_dict`
- endpoint 函数：迁移范围内所有路由函数
- router：以 `sticker_router` 的形式 include，不暴露为顶层 `router`

### `group_detail()` 依赖

`group_detail()` 继续调用 `_sticker_dict()`。实现后该名字来自新模块，但调用语义不变。

### 路由顺序

新模块内部保持静态路径优先于 `/{sticker_id:int}` 的定义顺序，尤其是：

- `/stickers/duplicate-groups`
- `/stickers/near-duplicate-candidates`
- `/stickers/dedupe/exact/backfill`
- `/stickers/near-duplicate/scan`
- `/stickers/phash/backfill`
- `/stickers/{sticker_id:int}`

同时新增注册测试，防止 include 后路由重复注册或 endpoint module 未迁移。

## 测试设计

新增 `tests/test_admin_sticker_routes_split.py`：

1. `test_admin_sticker_routes_are_registered_from_split_module`
   - 递归展开 `api.admin_routes.router`。
   - 断言关键路径存在且 endpoint module 为 `api.admin.sticker_routes`。
   - 关键路径包括 `/api/v1/admin/stickers`、`/api/v1/admin/generated-images`、
     `/api/v1/admin/stickers/near-duplicate-candidates`、
     `/api/v1/admin/stickers/{sticker_id:int}`。

2. `test_legacy_admin_routes_sticker_imports_still_work`
   - 断言 `api.admin_routes.StickerCreate is api.admin.sticker_routes.StickerCreate`。
   - 覆盖所有迁移 request model、`_sticker_dict()` 和主要 endpoint 函数。

3. `test_split_sticker_routes_use_legacy_admin_token_monkeypatch`
   - monkeypatch `api.admin_routes.NANOBOT_ADMIN_TOKEN = "split-token"`。
   - `GET /api/v1/admin/stickers` 使用 split token 返回 200。
   - 使用旧默认 token 返回 401。

4. `test_admin_sticker_routes_are_not_registered_twice`
   - 对关键路径断言只注册一次。

复用既有测试：

- `tests/test_admin_api.py::TestGeneratedImagesAdmin`
- `tests/test_admin_api.py::TestStickerCRUD`
- `tests/test_admin_api.py::TestAuth`
- `tests/test_webui_admin_redesign.py` 中 sticker duplicate 相关 UI 静态测试

## 验证门禁

实现阶段需要运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_sticker_routes_split.py -q
```

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_sticker_routes_split.py \
  tests/test_admin_api.py::TestGeneratedImagesAdmin \
  tests/test_admin_api.py::TestStickerCRUD \
  -q
```

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_api.py::TestAuth \
  tests/test_webui_admin_redesign.py -k "sticker_duplicate" \
  tests/test_asyncio_run_policy.py \
  -q
```

```bash
python -m compileall api/admin_routes.py api/admin/sticker_routes.py -q
wc -l api/admin_routes.py api/admin/sticker_routes.py tests/test_admin_sticker_routes_split.py
git diff --check -- api/admin_routes.py api/admin/sticker_routes.py tests/test_admin_sticker_routes_split.py
```

提交前最终运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

## 非目标

- 不拆 `api/routes.py`。
- 不拆 admin 认证、审计 helper 或 `api.admin.common`。
- 不迁移 `/overview`、`/groups`、群记忆、TimingGate、配置、模型、工具、reply/eval、eval 工作台或日志 viewer。
- 不改变 DB schema。
- 不改变 sticker / generated image 的 response shape、状态码、审计 action、路由路径、预览缓存行为或 duplicate canonical 语义。
- 不改 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。

## 风险与缓解

- **风险：路由重复注册。** 新增注册计数测试，迁移后删除旧模块本地 route 装饰器实现。
- **风险：静态路径被 `/{sticker_id:int}` 影响。** 保持静态路径定义在动态路径之前，并复用 route shadow 回归。
- **风险：旧 admin token monkeypatch 失效。** 新模块使用 `api.admin.common.verify_admin`，并新增 split token 回归。
- **风险：`group_detail()` 贴纸序列化漂移。** `_sticker_dict()` 原样迁移并由旧模块 re-export，`group_detail()` 继续调用同名 helper。
- **风险：近重复扫描锁迁移后并发语义改变。** `_NEAR_DUP_SCAN_LOCK` 随新模块迁移，endpoint 继续使用同一个模块级 lock。

## 后续顺序

本阶段完成后，`api/admin_routes.py` 仍会超过 800 行。下一刀建议在以下两个方向中选择：

1. `api/admin/group_memory_routes.py`：收益中等，需重点处理 `/groups/{group_id:path}` 路由顺序。
2. `api/admin/trace_routes.py`：read-only 观测边界，路径稳定，适合继续降低主文件体积。
