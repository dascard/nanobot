# 普通 API Memory 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把普通 `api/routes.py` 中的 memory HTTP 层迁移到 `api/memory_routes.py`，保持旧导入、鉴权 monkeypatch、dependency override、响应结构、日期校验和异步策略不变，并继续推进 `api/routes.py` 超大文件治理。

**架构：** `api.routes` 继续作为 `/api/v1` 聚合 router，`server.py` 不新增 include 入口。新增 `api.memory_routes.router` 承载 `/memory/digests`、`/memory/digests/run` 和 `/memory/recall`；父模块在原 memory 路由位置 include 子 router，并 re-export 迁移后的 request model、endpoint 和 legacy helper。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLAlchemy、pytest、项目既有 `api.common_auth`、`MemoryDigestRetrievalService`、`core.daily_digest.generate_daily_digest_for_date`。

---

## 当前状态（2026-06-21）

- [x] 已完成 `api/routes.py` 下一刀边界审计。
- [x] 已并行分派两个只读子 agent：
  - memory 审计确认纯路由搬迁可控，但 `_safe_meta` 必须留在父模块。
  - models/evolution 审计确认 `models` 风险最低但行数收益小，`evolution` 牵涉 legacy 初始化兼容。
- [x] 已选定本阶段采用 memory 路由拆分。
- [x] 已写设计文档：`docs/superpowers/specs/2026-06-21-api-memory-routes-split-design.md`。
- [x] 设计提交：`e322638 docs(普通API): 设计记忆路由拆分`。
- [ ] 任务 1：补普通 API memory route split 红灯测试并提交。
- [ ] 任务 2：拆出 `api/memory_routes.py` 并由 `api.routes` include / re-export 后提交。
- [ ] 任务 3：更新 `docs/todo.md`、`docs/plan_walkthrough.md` 和本计划执行记录后提交。

## 子 agent 分工约定

主线程负责最终编辑、验证和提交。写入阶段不要让多个 agent 同时修改 `api/routes.py`。

- **Worker A：测试文件。** 只允许创建或修改 `tests/test_api_memory_routes_split.py`。
- **Worker B：memory 路由迁移。** 只允许创建 `api/memory_routes.py`，并修改
  `api/routes.py` 的 memory import、include 和旧本地 memory 区块。
- **Reviewer：验证审查。** 只读检查 diff、route module、重复注册、反向导入、
  asyncio 策略、行数和测试输出。

接口约定：

- `api.memory_routes.router` 不带 `/api/v1` 前缀，由父 `api.routes.router` include。
- `api.memory_routes` 使用 `api.common_auth.verify_token`，不得导入 `api.routes`。
- `api.routes` 必须 re-export `MemoryDigestRunRequest`、三个 endpoint 和四个 memory helper。
- `_safe_meta` 必须留在 `api.routes`，因为 `_persist_chat_turn()` 仍调用它。
- `run_memory_digests()` 保持同步 endpoint，不把 daily digest 调用包装成 awaitable。
- 生产代码不得新增 `asyncio.run()`，不得新增 `run_awaitable_sync`，不得新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query`、conversation 结构或工具输出契约。

## 文件职责

- 创建：`tests/test_api_memory_routes_split.py`
  - 锁定 `/memory/digests`、`/memory/digests/run` 和 `/memory/recall` endpoint module 为 `api.memory_routes`。
  - 锁定 `api.routes` 旧导入兼容。
  - 锁定迁移 route 未重复注册。
  - 锁定拆分路由继续兼容 `api.routes.NANOBOT_API_TOKEN` monkeypatch。
  - 锁定 `/evolution/trigger`、`/models/list`、`/models/sync` 和 `/health` 本阶段仍留在父模块。
  - 锁定 `_safe_meta` 留在父模块。
  - 静态扫描新模块没有反向导入父模块、没有 `asyncio.run`、没有 `run_awaitable_sync`。
- 创建：`api/memory_routes.py`
  - 定义 `MemoryDigestRunRequest`。
  - 定义 `_validate_memory_digest_date_filters()`、`_short_text()`、`_calc_recall_confidence()`、`_build_expand_chain()`。
  - 定义 `get_memory_digests()`、`run_memory_digests()`、`recall_memory()`。
  - 定义 `router = APIRouter(tags=["memory"])`。
- 修改：`api/routes.py`
  - 从 `api.memory_routes` import `router as memory_router`、`MemoryDigestRunRequest`、四个 helper 和三个 endpoint。
  - 删除本地 `MemoryDigestRunRequest` 与 memory helper 定义。
  - 删除本地三个 memory endpoint 实现。
  - 在原 memory 路由所在区域 include `memory_router`。
  - 删除只服务 memory 的 import：`MemoryDigest`、`generate_daily_digest_for_date`、`MemoryDigestRetrievalService`、`validate_digest_date`；`datetime` 顶层 import 保留，`timedelta` 顶层 import 删除。
- 收口阶段修改：`docs/todo.md`、`docs/plan_walkthrough.md`、`.Codex/plans/api-memory-routes-split.md`。

## 任务 1：补普通 API memory split 红灯测试

**文件：**
- 创建：`tests/test_api_memory_routes_split.py`

- [ ] **步骤 1：创建测试文件**

创建 `tests/test_api_memory_routes_split.py`：

```python
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


_MEMORY_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/memory/digests"),
    ("POST", "/api/v1/memory/digests/run"),
    ("GET", "/api/v1/memory/recall"),
)

_PARENT_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/evolution/trigger"),
    ("GET", "/api/v1/models/list"),
    ("POST", "/api/v1/models/sync"),
    ("GET", "/api/v1/health"),
)

_MEMORY_ROUTE_EXPORTS = (
    "MemoryDigestRunRequest",
    "_validate_memory_digest_date_filters",
    "_short_text",
    "_calc_recall_confidence",
    "_build_expand_chain",
    "get_memory_digests",
    "run_memory_digests",
    "recall_memory",
)


def _api_route_entries():
    from server import app

    def _iter_routes(routes, prefix: str = ""):
        for route in routes:
            endpoint = getattr(route, "endpoint", None)
            route_path = getattr(route, "path", None)
            if endpoint is not None and route_path is not None:
                yield prefix + route_path, route
                continue

            original_router = getattr(route, "original_router", None)
            if original_router is None:
                continue
            include_context = getattr(route, "include_context", None)
            include_prefix = getattr(include_context, "prefix", "")
            yield from _iter_routes(original_router.routes, prefix + include_prefix)

    return list(_iter_routes(app.routes))


def _api_routes_for(path: str, method: str | None = None):
    return [
        route
        for route_path, route in _api_route_entries()
        if route_path == path and (method is None or method in getattr(route, "methods", set()))
    ]


def test_api_memory_routes_are_registered_from_split_module():
    for method, path in _MEMORY_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.memory_routes"}


def test_legacy_api_routes_memory_imports_still_work():
    from api import memory_routes
    from api import routes

    for name in _MEMORY_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(memory_routes, name)

    body = routes.MemoryDigestRunRequest(target_date="2026-06-20", user_id="u1", force=True)
    assert body.target_date == "2026-06-20"
    assert body.user_id == "u1"
    assert body.force is True


def test_split_memory_routes_use_legacy_api_token_monkeypatch(db_session, monkeypatch):
    from core.database import get_db
    from server import app

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")
    try:
        with TestClient(app) as test_client:
            ok = test_client.get(
                "/api/v1/memory/digests",
                headers={"Authorization": "Bearer split-token"},
            )
            wrong = test_client.get(
                "/api/v1/memory/digests",
                headers={"Authorization": "Bearer wrong"},
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_api_memory_routes_are_not_registered_twice():
    for method, path in _MEMORY_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_memory_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/memory_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_non_memory_tail_routes_stay_in_parent_routes():
    for method, path in _PARENT_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.routes"}


def test_safe_meta_stays_in_parent_routes():
    from api import routes

    assert routes._safe_meta.__module__ == "api.routes"
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_memory_routes_split.py
```

预期：FAIL。失败点应指向 `api.memory_routes` 尚不存在、memory endpoint module 仍为
`api.routes`、`api/memory_routes.py` 文件尚不存在。

- [ ] **步骤 3：提交红灯测试**

运行：

```bash
git add tests/test_api_memory_routes_split.py
git commit -m "test(普通API): 锁定记忆路由拆分契约"
```

## 任务 2：拆出 `api.memory_routes`

**文件：**
- 创建：`api/memory_routes.py`
- 修改：`api/routes.py`

- [ ] **步骤 1：创建 `api/memory_routes.py`**

创建 `api/memory_routes.py`，内容从 `api/routes.py` 当前 memory 区块搬迁，保持行为不变：

```python
"""普通 API 记忆摘要路由。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from app.memory_digest.retrieval_service import MemoryDigestRetrievalService, validate_digest_date
from core.daily_digest import generate_daily_digest_for_date
from core.database import ChatLog, MemoryDigest, get_db


router = APIRouter(tags=["memory"])


class MemoryDigestRunRequest(BaseModel):
    target_date: Optional[str] = None
    user_id: Optional[str] = None
    force: bool = False


def _validate_memory_digest_date_filters(
    *,
    digest_date: str = "",
    date_start: str = "",
    date_end: str = "",
) -> tuple[str, str, str]:
    try:
        return (
            validate_digest_date(digest_date, "digest_date"),
            validate_digest_date(date_start, "date_start"),
            validate_digest_date(date_end, "date_end"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _short_text(text: str, limit: int = 400) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + "...[截断]"


def _calc_recall_confidence(keyword: str, content: str, meta: dict) -> float:
    if not keyword.strip():
        return 0.5
    content_l = (content or "").lower()
    key_l = keyword.lower()
    hits = content_l.count(key_l)

    score = min(0.95, 0.3 + min(0.45, hits * 0.08))
    tags = (meta.get("tags") or {}) if isinstance(meta, dict) else {}
    value_signal = float((tags.get("value_signal_score") or 0))
    if value_signal > 0:
        score = min(0.98, score + min(0.2, value_signal * 0.03))
    return round(max(0.05, score), 3)


def _build_expand_chain(db: Session, base: MemoryDigest, reveal_to_level: int) -> list[MemoryDigest]:
    reveal_to_level = max(0, min(2, reveal_to_level))
    chain = [base]
    current = base

    while current.parent_id is not None and current.level > reveal_to_level:
        parent = db.query(MemoryDigest).filter(MemoryDigest.id == current.parent_id).first()
        if not parent:
            break
        chain.append(parent)
        current = parent

    return chain
```

随后把三个 endpoint 放入同一文件：

```python
@router.get("/memory/digests")
def get_memory_digests(
    user_id: str = "",
    session_id: str = "",
    digest_date: str = "",
    date_start: str = "",
    date_end: str = "",
    level: int = -1,
    limit: int = 50,
    include_content: bool = False,
    include_legacy: bool = True,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """按条件查询每日记忆摘要（支持渐进式披露层级）。"""
    digest_date, date_start, date_end = _validate_memory_digest_date_filters(
        digest_date=digest_date,
        date_start=date_start,
        date_end=date_end,
    )
    items = MemoryDigestRetrievalService(db).list_digests(
        user_id=user_id,
        session_id=session_id,
        digest_date=digest_date,
        date_start=date_start,
        date_end=date_end,
        level=level if level >= 0 else None,
        limit=limit,
        include_content=include_content,
        include_legacy=include_legacy,
    )

    return {
        "status": "ok",
        "count": len(items),
        "digests": items,
    }


@router.post("/memory/digests/run")
def run_memory_digests(
    req: MemoryDigestRunRequest,
    _auth=Depends(verify_token),
):
    """手动触发指定日期的每日记忆摘要任务。"""
    target_date = req.target_date
    if not target_date:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    created = generate_daily_digest_for_date(
        target_date=target_date,
        user_id=req.user_id,
        force=req.force,
    )
    return {
        "status": "ok",
        "target_date": target_date,
        "force": req.force,
        "created_sessions": created,
    }


@router.get("/memory/recall")
def recall_memory(
    keyword: str,
    user_id: str = "",
    session_id: str = "",
    digest_date: str = "",
    date_start: str = "",
    date_end: str = "",
    limit: int = 20,
    reveal_to_level: int = 2,
    include_content: bool = False,
    include_legacy: bool = False,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """
    记忆召回：优先命中 level=2（紧凑层），再按需向 level=1/0 展开。
    返回每条结果的置信度和来源日志范围。
    """
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword is required")
    digest_date, date_start, date_end = _validate_memory_digest_date_filters(
        digest_date=digest_date,
        date_start=date_start,
        date_end=date_end,
    )

    results = MemoryDigestRetrievalService(db).recall(
        keyword=keyword,
        user_id=user_id,
        session_id=session_id,
        digest_date=digest_date,
        date_start=date_start,
        date_end=date_end,
        limit=limit,
        reveal_to_level=reveal_to_level,
        include_content=include_content,
        include_legacy=include_legacy,
    )

    news_hits = (
        db.query(ChatLog)
        .filter(
            ChatLog.role == "tool",
            ChatLog.content.like("%[ai_daily]%"),
            ChatLog.content.like(f"%{keyword}%"),
        )
        .order_by(ChatLog.id.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    news_items = []
    for row in news_hits:
        news_items.append(
            {
                "log_id": row.id,
                "user_id": row.user_id,
                "session_id": row.session_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "confidence": _calc_recall_confidence(keyword, row.content or "", {}),
                "source_range": {
                    "start_log_id": row.id,
                    "end_log_id": row.id,
                },
                "content": row.content if include_content else None,
            }
        )

    return {
        "status": "ok",
        "keyword": keyword,
        "digest_hits": len(results),
        "news_hits": len(news_items),
        "items": results,
        "news_items": news_items,
    }
```

- [ ] **步骤 2：修改 `api/routes.py` import 与 re-export**

将 `core.database` import 中的 `MemoryDigest` 删除；删除 `generate_daily_digest_for_date`、
`MemoryDigestRetrievalService`、`validate_digest_date` import；把顶层
`from datetime import datetime, timedelta` 改成 `from datetime import datetime`。

新增：

```python
from api.memory_routes import (
    MemoryDigestRunRequest,
    _build_expand_chain,
    _calc_recall_confidence,
    _short_text,
    _validate_memory_digest_date_filters,
    get_memory_digests,
    recall_memory,
    router as memory_router,
    run_memory_digests,
)
```

- [ ] **步骤 3：删除父模块本地 memory 定义并 include 子 router**

删除 `api/routes.py` 中本地 `MemoryDigestRunRequest`、四个 memory helper、三个 memory
endpoint 的实现。保留 `_safe_meta()`。

在原 memory endpoint 所在的末尾区域加入：

```python
router.include_router(memory_router)
```

该 include 应位于 `/evolution/trigger` 之后、`/models/list` 之前，保持尾部路由顺序清晰。

- [ ] **步骤 4：运行 split 定向测试验证绿灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_memory_routes_split.py
```

预期：PASS。

- [ ] **步骤 5：运行 memory 行为回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_memory_digest.py
```

预期：PASS。

- [ ] **步骤 6：运行相邻回归与静态检查**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_asyncio_run_policy.py tests/test_api_task_routes_split.py
python -B -m compileall api/routes.py api/memory_routes.py
rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/memory_routes.py
git diff --check -- api/routes.py api/memory_routes.py tests/test_api_memory_routes_split.py
```

预期：

- pytest PASS。
- compileall 成功。
- `rg` 无命中，退出码为 1。
- `git diff --check` 无输出。

- [ ] **步骤 7：提交 memory 路由拆分**

运行：

```bash
git add api/routes.py api/memory_routes.py
git commit -m "refactor(普通API): 拆分记忆路由"
```

## 任务 3：文档收口与全量验证

**文件：**
- 修改：`.Codex/plans/api-memory-routes-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：更新计划执行记录**

在本计划的「当前状态」中把任务 1 和任务 2 标记为完成，并新增「执行记录」章节，记录：

- 红灯命令、失败数量和失败原因。
- split 绿灯命令和通过数量。
- memory 行为回归命令和通过数量。
- 静态检查结果。
- `wc -l api/routes.py api/memory_routes.py tests/test_api_memory_routes_split.py` 行数。

- [ ] **步骤 2：更新 `docs/todo.md`**

在「超大文件 >800 行拆分」条目下记录：

- `api/routes.py` 已完成 memory 路由拆分。
- 新增 `api/memory_routes.py`。
- `api/routes.py` 最新行数。
- 下一候选仍为 `models`、`evolution route-only` 或后续更大低耦合边界。

- [ ] **步骤 3：更新 `docs/plan_walkthrough.md`**

追加 2026-06-21 的 memory 路由拆分执行记录，包含：

- 设计文档提交。
- 计划文档提交。
- 红灯测试提交。
- 实现提交。
- 验证命令和结果。
- 下一步建议。

- [ ] **步骤 4：运行全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

- [ ] **步骤 5：文档格式与状态检查**

运行：

```bash
git diff --check -- .Codex/plans/api-memory-routes-split.md docs/todo.md docs/plan_walkthrough.md
git status --short
```

预期：`git diff --check` 无输出；`git status --short` 中本阶段只包含计划与文档相关改动，以及历史无关脏项。

- [ ] **步骤 6：提交文档收口**

运行：

```bash
git add .Codex/plans/api-memory-routes-split.md docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(计划): 收口记忆路由拆分"
```

## 最终验收清单

- [ ] `tests/test_api_memory_routes_split.py` 经历红灯再绿灯。
- [ ] `api.memory_routes` 不导入 `api.routes`。
- [ ] `api.routes` re-export memory request model、endpoint 和 helper。
- [ ] `_safe_meta` 留在 `api.routes`。
- [ ] `/evolution/trigger`、`/models/list`、`/models/sync` 和 `/health` 仍留在 `api.routes`。
- [ ] `tests/test_memory_digest.py` 通过。
- [ ] `tests/test_asyncio_run_policy.py` 通过。
- [ ] 全量 `tests/` 回归 0 failures。
- [ ] 每个阶段性改动都有独立 commit。
