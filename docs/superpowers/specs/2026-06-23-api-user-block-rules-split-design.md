# 普通 API 用户屏蔽规则拆分设计

日期：2026-06-23

## 背景

P3 超大文件拆分队列当前仍只剩 `api/routes.py`，但 `/chat` 主链路中的用户屏蔽判断仍直接写在父模块里。群聊入口 `app/group_ingress/helpers.py::check_user_blocked()` 也有一份同构实现：查询启用的 `UserBlockRule`，按 `target_type`、`all` 和群号归一化判断是否命中。

这两份逻辑重复，后续如果调整 `target_type` 或 `group_id` 匹配语义，容易出现私聊和群聊行为漂移。本阶段只抽取规则匹配 helper，不迁移入口响应流程。

## 目标

- 新增 `core/user_block_rules.py`，承载用户屏蔽规则的唯一匹配实现。
- 保持 `api.routes._check_user_blocked()` 为父模块 wrapper，`/chat` 调用点不变。
- 保持 `app.group_ingress.helpers.check_user_blocked()` 为群聊 wrapper，`GroupIngressService` 调用点不变。
- 保持现有失败兜底：规则查询或匹配异常时记录 warning，并返回 `False`，避免屏蔽逻辑故障导致整条消息失败。
- 保持 `target_type` 语义：命中请求 `target_type` 或规则为 `all` 时可屏蔽；`group` 规则带 `group_id` 时按 `normalize_group_session_id()` 比较。

## 非目标

- 不迁移 `/chat` 路由本体、私聊响应信封、ChatLog 落库、图片预缓存、Bridge、SSE、push envelope 或 response envelope。
- 不迁移群聊 `GroupIngressService` 的 timing annotation、content moderation 或 response envelope。
- 不改变 Admin block rule CRUD、DB schema、`UserBlockRule` 字段或 WebUI。
- 不新增规则模式、优先级、原因返回值或审计字段。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 现有契约

`UserBlockRule` 字段语义如下：

- `user_id`：被屏蔽用户。
- `target_type`：`private`、`group` 或兼容已有的 `all`。
- `group_id`：仅 `target_type="group"` 且非空时生效。
- `enabled`：仅 `1` 视为启用。

当前匹配流程：

1. 只查询同一 `user_id` 且 `enabled == 1` 的规则。
2. 对每条规则，如果 `rule.target_type` 不在 `(target_type, "all")`，跳过。
3. 如果规则是 `group` 且带 `group_id`：
   - 请求带 `group_id` 时，用 `normalize_group_session_id()` 归一化两边后比较。
   - 不相等则跳过。
4. 其余情况返回 `True`。
5. 任何异常记录 warning 后返回 `False`。

注意：现有实现中 `group` 规则带 `group_id` 而请求未传 `group_id` 时会命中，因为旧逻辑只在 `norm_group` 非空时比较。本阶段保持该语义，不顺手修正。

## 方案比较

### 方案 A：只抽纯 matcher，DB 查询留在 wrapper

新增 `matches_user_block_rule(rule, *, target_type, group_id)`，两个 wrapper 自己查询 DB 后调用 matcher。

优点是纯函数最容易测；缺点是查询逻辑仍重复，无法解决本阶段主要问题。

### 方案 B：抽 DB 查询与匹配，日志留在 wrapper

新增 `is_user_blocked(db, user_id, *, target_type="private", group_id="", rule_model=UserBlockRule, normalize_group_session_id=...)`。core helper 内部完成查询和匹配；wrapper 只负责捕获异常、记录各自 logger，并返回 bool。

优点是去重完整，同时保留父模块和群聊 helper 的日志 / monkeypatch 边界。推荐采用。

### 方案 C：core helper 同时负责日志和异常兜底

让 `core.user_block_rules.check_user_blocked()` 自己捕获异常并打日志，两个入口直接委托。

优点是 wrapper 更薄；缺点是日志命名空间会从 `nanobot.api` / `nanobot.group_ingress` 漂到 core，旧测试或线上定位口径可能变差。

## 选定设计

采用方案 B。

新增模块：

```python
def is_user_blocked(
    db: Any,
    user_id: str,
    *,
    target_type: str = "private",
    group_id: str = "",
    rule_model: Any = UserBlockRule,
    normalize_group_session_id: Callable[[str], str] = normalize_group_session_id,
) -> bool:
    ...
```

实现要点：

- 顶层从 `core.database` 导入 `UserBlockRule`，从 `core.group_runtime.ids` 导入 `normalize_group_session_id`。
- 查询条件保持 `rule_model.user_id == user_id` 和 `rule_model.enabled == 1`。
- `rule_model` 与 `normalize_group_session_id` 可注入，测试可用轻量 fake，不需要真实 DB。
- 异常不在 core helper 内吞掉；由 `api.routes._check_user_blocked()` 和 `app.group_ingress.helpers.check_user_blocked()` 保持现有 warning + `False` 行为。

父模块 wrapper：

- `api.routes._check_user_blocked()` 保持名称、参数、返回 bool 和 `__module__ == "api.routes"`。
- `app.group_ingress.helpers.check_user_blocked()` 保持名称、参数、返回 bool。
- 两者都委托 `core.user_block_rules.is_user_blocked()`。

## 测试策略

新增 `tests/test_user_block_rules.py`：

- 扫描新模块不导入 `api.routes`、不导入 `app.group_ingress.helpers`，且不包含 `asyncio.run` 或 `run_awaitable_sync`。
- 用 fake DB / fake rule model 覆盖 private、all、group exact、group normalized mismatch、disabled ignored。
- 覆盖 `group` 规则带 `group_id` 但请求未传 `group_id` 时保持旧语义命中。
- 覆盖两个 wrapper 保持旧模块路径并委托 core helper。
- 覆盖 wrapper 在 core helper 抛异常时返回 `False`。

相邻回归：

- `tests/test_admin_api.py::TestPrivateBlockFlow::test_blocked_user_chat_writes_log_with_files`
- 新增或复用一个群聊 block 行为测试，确认 `GroupIngressService` 命中 user block 后返回 `no_reply` 且记录 timing annotation。
- `tests/test_asyncio_run_policy.py`

## 风险与缓解

- **风险：** core helper 改变 group_id 缺失时的旧命中语义。
  **缓解：** 用测试锁定旧行为，本阶段不改变。
- **风险：** 父模块 wrapper 消失会破坏旧 monkeypatch 或测试定位。
  **缓解：** 测试断言 wrapper `__module__` 和委托参数。
- **风险：** 群聊 helper 反向依赖 API。
  **缓解：** 新模块放在 `core/`，测试扫描禁止导入 `api.routes` 和 `app.group_ingress.helpers`。

## 验证计划

1. 先提交设计文档。
2. 写实现计划并提交。
3. 先写红灯测试，确认 `core/user_block_rules.py` 缺失或 wrapper 尚未委托导致失败。
4. 实现 core helper 并运行定向测试。
5. 接入 `api.routes` 和 `app.group_ingress.helpers` wrapper，运行定向和相邻回归。
6. 更新 `docs/todo.md`、`docs/plan_walkthrough.md` 和计划执行记录，跑全量测试后提交收口文档。
