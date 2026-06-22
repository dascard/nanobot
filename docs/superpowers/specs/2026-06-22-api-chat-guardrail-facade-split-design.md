# 普通 API Chat Guardrail Facade 拆分设计

日期：2026-06-22

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前仍只剩普通
`api/routes.py` 超过 800 行。前序已经完成 task、memory、models、evolution、
history / log、sticker / media、Agent Step / Render、group utility / legacy timing、
group message、chat content helper、chat response contract、chat persistence、
chat request contract 和 Chat Runtime Facade 拆分。

当前 `api/routes.py` 为 1470 行，剩余显式 route 只有：

- `POST /chat`
- `GET /health`

`/health` 仍只有极少行数，并且多个 split 测试把它作为父模块哨兵端点，不作为本轮优先目标。
`/chat` 主链路仍同时包含私聊缓冲、guardrail、Private TimingGate、Prompt Runtime 输入组装、
KT bridge 调用、SSE、断连后台 push、聊天落库、进化触发和响应 envelope。完整迁移
`/chat` 仍然风险过高。

Chat Runtime Facade 拆分后，guardrail 仍有两类职责混在父模块：

- 输入构造：`_build_guardrail_input()` 已经由 `api/chat_content_helpers.py` 承载，父模块保留 wrapper。
- 检测结果兼容：`_detect_guardrail()` 在父模块中兼容新 `detect_injection()` 与旧测试桩
  `classify()`，并把 legacy `status` 归一化为 `safe`、`silent` 或 `injection`。

本阶段只拆第二类职责。父模块继续保留 `get_guardrail()` patch point、私聊缓冲预跑任务、
superuser passthrough 判断、分类时机、异常处理和 `guardrail_status` 到聊天持久化 / 响应的传播。

## 目标

新增 `api/chat_guardrail_facade.py`，把 guardrail 检测兼容层从 `api/routes.py` 迁入独立模块，
让新旧 guardrail 返回值归一化规则可以单独测试，并减少父模块中的安全检测细节。

本阶段迁移实现逻辑：

- 调用新 guardrail provider 的 `detect_injection(message, allow_passthrough=...)`。
- 兼容旧测试桩的 `classify(message, allow_injection_passthrough=...)`。
- 非 dict 返回值按空 dict 处理。
- legacy `status="silent"` 归一化为：
  - `status="silent"`
  - `injection=False`
  - `passthrough=allow_passthrough`
- legacy `status="injection"` 归一化为：
  - `status="injection"`
  - `injection=True`
  - `passthrough=False`
- legacy 其他状态归一化为：
  - `status="safe"`
  - `injection=False`
  - `passthrough=allow_passthrough`
- 提供一个纯 helper 将检测结果映射成父模块使用的 `guardrail_status`：
  `injection`、`silent` 或 `safe`。

父模块 `api.routes` 继续保留：

- `get_guardrail()` 旧 monkeypatch 入口。
- `_build_guardrail_input()` 父模块 wrapper。
- `_detect_guardrail()` 父模块 wrapper，保持 `__module__ == "api.routes"`。
- `_is_guardrail_superuser()`。
- 私聊缓冲的 `asyncio.to_thread(_detect_guardrail, ...)` 创建时机。
- 合并多条私聊后的二次检测时机。
- `_classifier_ran`、`guardrail_status`、Prompt Runtime injection mode 和落库语义。
- `/chat` 路由注册位置和 `/health` 哨兵端点。

## 非目标

- 不迁移 `get_guardrail()` 或 `clients.classifier_client` provider 获取逻辑。
- 不迁移 `_build_guardrail_input()`；它已经归属于 chat content helper。
- 不迁移 `_private_buffers`、`_private_lock`、私聊缓冲窗口常量或私聊缓冲状态机。
- 不改变 guardrail 分类时机、预跑任务、follower 等待、owner 取消清理或合并后复判逻辑。
- 不改变 superuser passthrough 判定。
- 不改变 Prompt Runtime injection 安全提示、`enriched_query`、metadata 或模板。
- 不迁移 `_stream_chat()`、聊天落库、response envelope、SSE 或 QQ push。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 方案比较

### 方案 A：完整迁移 guardrail 调度

把 `get_guardrail()`、输入构造、私聊缓冲预跑 task、最终复判和状态映射整体迁入
`api/chat_guardrail_facade.py`。

优点：父模块行数下降更多。缺点：会同时触碰父模块 monkeypatch 入口、私聊缓冲生命周期、
`asyncio.to_thread()` 时机、合并消息复判和异常清理。现有测试直接 patch
`api.routes.get_guardrail`，整体迁移会扩大兼容面。本阶段不采用。

### 方案 B：只拆兼容归一化层（推荐）

新增 `api/chat_guardrail_facade.py`，只迁移 `_detect_guardrail()` 的实现主体和
`guardrail_status` 映射 helper。父模块仍是编排者，只把旧 wrapper 转发给新模块。

优点：边界小，安全检测时机不变；新模块可单独测试新旧 provider 兼容行为；父模块仍保留
原有 patch point 和私聊缓冲控制流。缺点：行数收益有限，但能先把安全结果归一化规则从
`proxy_chat()` 周边剥离出来，为后续私聊缓冲或 streaming 拆分降低认知负担。

### 方案 C：先拆私聊缓冲基础件

新增 `api/chat_private_buffer.py`，迁移 `_private_buffers` 相关状态和 helper。

优点：行数收益更大。缺点：现有测试直接读取 `api.routes._private_buffers` 裸 dict，
并 patch `api.routes.asyncio.sleep`、`api.routes._time.time`；owner / follower 取消语义、
fake clock 和同对象兼容必须先设计更细。本阶段不采用。

### 方案 D：先拆 streaming helper

新增 `api/chat_streaming.py`，迁移 delta 合并、bounded queue drain 或 abort envelope builder。

优点：streaming 块更大，长期收益高。缺点：当前 `_stream_chat()` 同时绑定 SSE 顺序、
runner 生命周期、断连后台 `UnitOfWork`、push envelope、图片 token 展开、Prompt Runtime
audit failure、私聊 buffer finalization 和 `_persist_chat_turn()`。本阶段不采用。

## 选定设计

采用方案 B，新增 `api/chat_guardrail_facade.py`。

### 新模块职责

`api/chat_guardrail_facade.py` 负责：

```python
from typing import Any


def detect_guardrail(
    guardrail: Any,
    message: str,
    *,
    allow_passthrough: bool = False,
) -> dict[str, Any]:
    ...


def guardrail_status_from_result(result: dict[str, Any] | None) -> str:
    ...
```

`detect_guardrail()` 不创建 task、不访问数据库、不导入 `api.routes`、不获取 provider。
它只处理一个已传入的 guardrail 对象，并返回归一化 dict。

`guardrail_status_from_result()` 的输入为检测结果，输出严格限定为：

- `"injection"`
- `"silent"`
- `"safe"`

### 父模块兼容门面

`api/routes.py` 新增导入：

```python
from api import chat_guardrail_facade
```

父模块保留 `_detect_guardrail()` wrapper：

```python
def _detect_guardrail(guardrail, message: str, *, allow_passthrough: bool = False) -> dict:
    return chat_guardrail_facade.detect_guardrail(
        guardrail,
        message,
        allow_passthrough=allow_passthrough,
    )
```

父模块在 guardrail 分类完成后用新 helper 映射状态：

```python
guardrail_status = chat_guardrail_facade.guardrail_status_from_result(result)
```

父模块不把 `get_guardrail()` 传给新模块，不让新模块知道 `_private_buffers` 或 `ChatProxyRequest`。

## 测试设计

新增 `tests/test_api_chat_guardrail_facade_split.py`，覆盖：

- 新模块不反向导入 `api.routes`，不包含 `asyncio.run` 或 `run_awaitable_sync`。
- 新 `detect_injection()` provider 会收到 `allow_passthrough` 参数，返回值原样保留。
- legacy `classify()` provider 会收到 `allow_injection_passthrough` 参数。
- legacy `status="silent"`、`status="injection"`、其他状态和非 dict 返回值均按设计归一化。
- `guardrail_status_from_result()` 只输出 `injection`、`silent` 或 `safe`。
- 父模块 `_detect_guardrail.__module__` 仍是 `api.routes`，且 wrapper 输出与新模块一致。
- `/chat` 仍使用 `api.routes.get_guardrail` patch point；superuser passthrough 测试继续通过。

同时更新现有 chat split 扫描测试，把 `api/chat_guardrail_facade.py` 加入禁止反向导入和禁止
同步 awaitable 扫描列表：

- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

## 验证计划

阶段验证按以下顺序执行：

1. 红灯：
   `python -B -m pytest -p no:cacheprovider tests/test_api_chat_guardrail_facade_split.py -v`
   应因新模块不存在而失败。
2. 相邻扫描红灯：运行四个 split 扫描测试中
   `test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable`，应因新模块不存在而失败。
3. 实现后绿灯：
   `python -B -m pytest -p no:cacheprovider tests/test_api_chat_guardrail_facade_split.py -v`。
4. 相邻 split 回归：
   `python -B -m pytest -p no:cacheprovider tests/test_api_history_log_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_group_message_routes_split.py tests/test_api_sticker_media_routes_split.py -v`。
5. `/chat` guardrail 回归：
   `python -B -m pytest -p no:cacheprovider tests/test_api.py::test_superuser_bypasses_injection_guardrail tests/test_api.py::test_superuser_image_only_message_bypasses_injection_guardrail tests/test_api.py::test_image_only_message_uses_multimodal_prompt_placeholder tests/test_api.py::test_private_buffer_silent_releases_waiters -v`。
6. asyncio 策略回归：
   `python -B -m pytest -p no:cacheprovider tests/test_asyncio_run_policy.py -v`。
7. 文档收口前全量：
   `python -B -m pytest -p no:cacheprovider tests/ -v`。

## 风险与约束

- `api.routes.get_guardrail` 必须继续是 `/chat` 获取 guardrail provider 的唯一 patch point。
- `_detect_guardrail()` wrapper 必须保留父模块函数身份，避免测试和外部 monkeypatch 入口漂移。
- 私聊缓冲创建 task 的位置不能移动；否则可能改变 owner / follower 竞态和取消清理语义。
- `guardrail_status` 的取值必须保持 `injection`、`silent`、`safe`，否则会影响 Prompt Runtime
  injection mode、响应 meta 和落库 meta。
- 新模块不得导入 `api.routes`，也不得创建事件循环或同步包装 awaitable。

## 验收标准

- `api/chat_guardrail_facade.py` 存在，并独立承载 guardrail 检测兼容归一化逻辑。
- `api.routes._detect_guardrail()` 仍存在，`__module__` 仍为 `api.routes`。
- `/chat` 仍通过 `api.routes.get_guardrail()` 获取 guardrail provider。
- superuser passthrough、image-only guardrail 输入、legacy `classify()` 测试桩和 `detect_injection()`
  provider 均保持兼容。
- 新模块无 `from api.routes`、`import api.routes`、`asyncio.run` 或 `run_awaitable_sync`。
- 全量测试通过后才能提交文档收口。
