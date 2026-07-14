# 全会话专属指导实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `subagent-driven-development`（推荐）或 `executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。实现代码前必须先使用 `test-driven-development`，完成声明前必须使用 `verification-before-completion`。

**目标：** 为所有群聊和私聊 session 增加受限的 `session_guidance`，通过统一 canonical 会话身份解析、Prompt Runtime 独立 section 和 Admin WebUI 管理实现即时、隔离、可审计的会话级回答风格。

**架构：** 扩展现有 `ChatStreamConfig`，以 `platform + chat_type + external_session_id` 生成 canonical `chat_stream_id`。Bridge 在模型调用前解析指导并通过纯 `PromptCompileRequest` 传给 Prompt Compiler；Compiler 在 `identity_context` 与 `persona_reference` 之间生成唯一、可空的 `session_guidance` system section。Admin API 和 WebUI 复用同一 identity、validator、resolver 与 preview 编译路径。

**技术栈：** Python 3、FastAPI、Pydantic、SQLAlchemy、SQLite、pytest、React 19、Vite、Tailwind CSS。

**设计依据：** `docs/superpowers/specs/2026-07-12-session-guidance-design.md`

**前置计划：** `.codex/plans/prompt-runtime-contract-remediation.md`

**统一执行索引：** `.codex/plans/session-guidance-rollout.md`

---

## 执行约束

- 不修改 QQbot 端、QQ push 协议或入站请求协议。
- 不修改聊天历史、幂等 claim、恢复记录和 outbox 使用的运行时 `session_id`。
- 当前工作区存在用户自己的未跟踪文件；只按文件路径暂存，禁止 `git add -A` 和 `git add .`。
- 用户未明确说“提交”前，不执行任何 `git commit`。各任务的提交步骤只是授权后的检查点。
- 不在 `session_guidance` 中加入 Jinja 或 Prompt 变量渲染。
- 不缓存指导正文或 Prompt 编译结果。
- 所有本地测试命令清除代理环境变量。

## 文件结构与职责

### 新建文件

- `core/chat_stream_identity.py`：通用 canonical chat stream identity 解析、编码和兼容 alias 转换。
- `core/session_guidance.py`：指导正文校验、摘要和数据库只读解析服务。
- `core/prompt_v2/flow_migrations.py`：`session_guidance` flow 节点的幂等迁移、备份与回滚。
- `app/session_config/__init__.py`：会话配置应用层包。
- `app/session_config/discovery_service.py`：Admin 有效 session 发现、去重和 legacy 冲突标记。
- `scripts/manage_prompt_flow.py`：Prompt flow 迁移检查与回滚命令入口。
- `webui/src/features/session-config/SessionConfigsPage.jsx`：会话策略列表、编辑、草稿预览和清空交互。
- `tests/test_chat_stream_identity.py`：canonical identity 单元测试。
- `tests/test_session_guidance_schema.py`：数据库字段、身份迁移、备份和冲突测试。
- `tests/test_session_guidance.py`：指导 validator 与 resolver 单元测试。
- `tests/test_prompt_v2_session_guidance.py`：Prompt section、flow contract 和权限顺序测试。
- `tests/test_prompt_flow_session_guidance_migration.py`：runtime flow 迁移与回滚测试。
- `tests/test_prompt_runtime_session_guidance.py`：Bridge、Prompt Runtime 和 fail-closed 集成测试。
- `tests/test_admin_session_guidance.py`：Admin CRUD、发现、清空和脱敏审计测试。
- `tests/test_webui_session_guidance.py`：WebUI 源码契约测试。

### 修改文件

- `core/group_runtime/ids.py`：现有群聊 helper 转调 canonical identity。
- `core/expression_memory.py`：通用 `normalize_chat_stream_id()` 转调 canonical identity。
- `core/database.py`：`ChatStreamConfig` 增加正文与更新时间字段。
- `core/schema_migrations.py`：增加字段迁移、身份规范化迁移和迁移前 SQLite backup。
- `core/prompt_v2/schema.py`：`PromptCompileRequest` 增加指导正文与安全摘要。
- `core/prompt_v2/context_adapters.py`：固定 `<session_guidance>` wrapper。
- `core/prompt_v2/compiler.py`：编译可空、唯一的指导 section。
- `core/prompt_v2/flow.py`：runtime key、保留节点、默认 flow 与运行契约。
- `core/prompt_v2/audit.py`：唯一性、状态和相对顺序审计。
- `core/prompt_v2/template_registry.py`：初始化时执行 flow 幂等迁移。
- `prompts.v2.default/chat/flow.json`：默认编排图加入指导节点。
- `data/prompts_v2/chat/flow.json`：仓库内有效 runtime 编排图同步节点。
- `prompts.v2.default/chat/main.md`：声明指导权限和冲突顺序。
- `data/prompts_v2/chat/main.md`：同步运行时公共规则。
- `nanobot_kt/bridge.py`：公共群聊/私聊运行时解析并透传指导。
- `nanobot_kt/prompt_runtime.py`：`PromptRuntimeInput` 及编译请求透传。
- `bootstrap/prompt_runtime.py`：迁移失败阻止启动，不再只记录 warning。
- `api/admin/chat_config_routes.py`：通用 upsert、列表摘要、详情正文、清空和脱敏审计。
- `api/admin_routes.py`：兼容导出及有效预览 override 字段。
- `app/prompt_runtime/preview_service.py`：数据库有效指导与未保存草稿预览。
- `webui/src/App.jsx`：导航改名、导入独立页面并移除内联旧页面。
- `.gitignore`：忽略运行时 Prompt flow 备份目录。
- `tests/test_group_runtime_ids.py`：现有群聊 helper 兼容回归。
- `tests/test_schema_migrations.py`：迁移注册和重复执行回归。
- `tests/test_sqlite_backup.py`：身份迁移前备份回归。
- `tests/test_prompt_v2.py`：默认 flow 和现有 role 序列兼容断言。
- `tests/test_prompt_v2_audit_policy.py`：新增保留 section 审计回归。
- `tests/test_prompt_v2_template_registry.py`：初始化迁移返回值和幂等行为。
- `tests/test_prompt_runtime_bootstrap.py`：Prompt flow 迁移失败必须中止启动。
- `tests/test_bridge_prompt_v2.py`：Bridge 输入字段兼容断言。
- `tests/test_prompt_v2_template_admin.py`：有效预览 override 与错误码。
- `tests/test_prompt_trace_admin.py`：trace 只增加无正文摘要。
- `tests/test_admin_chat_config_routes_split.py`：新路由签名和 legacy facade 导出。
- `tests/test_api.py`：现有 effective 配置行为兼容。
- `tests/test_webui_admin_redesign.py`：导航和独立页面导入兼容。

---

### 任务 1：统一 canonical chat stream identity

**文件：**

- 创建：`core/chat_stream_identity.py`
- 修改：`core/group_runtime/ids.py`
- 修改：`core/expression_memory.py`
- 创建测试：`tests/test_chat_stream_identity.py`
- 修改测试：`tests/test_group_runtime_ids.py`

- [x] **步骤 1：编写 canonical identity 失败测试**

在 `tests/test_chat_stream_identity.py` 写出完整公共契约：

```python
import pytest


@pytest.mark.parametrize(
    ("platform", "chat_type", "session_id", "expected"),
    [
        (" QQ ", "group", "group_123", "qq:123:group"),
        ("qq", "private", "private_456", "qq:456:private"),
        ("web", "private", "default_session", "web:default_session:private"),
        ("web", "group", "群:研发", "web:%E7%BE%A4%3A%E7%A0%94%E5%8F%91:group"),
    ],
)
def test_resolve_chat_stream_identity(platform, chat_type, session_id, expected):
    from core.chat_stream_identity import resolve_chat_stream_identity

    identity = resolve_chat_stream_identity(
        platform=platform,
        chat_type=chat_type,
        session_id=session_id,
    )

    assert identity.chat_stream_id == expected


def test_parse_canonical_identity_round_trips_encoded_external_id():
    from core.chat_stream_identity import parse_canonical_chat_stream_id

    identity = parse_canonical_chat_stream_id(
        "web:%E7%BE%A4%3A%E7%A0%94%E5%8F%91:group"
    )

    assert identity.platform == "web"
    assert identity.chat_type == "group"
    assert identity.external_session_id == "群:研发"
    assert identity.chat_stream_id == "web:%E7%BE%A4%3A%E7%A0%94%E5%8F%91:group"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"platform": "qq", "chat_type": "group", "session_id": "private_1"},
        {"platform": "qq", "chat_type": "private", "session_id": "qq:1:group"},
        {"platform": "bad platform", "chat_type": "private", "session_id": "x"},
        {"platform": "qq", "chat_type": "unknown", "session_id": "x"},
        {"platform": "qq", "chat_type": "private", "session_id": ""},
    ],
)
def test_resolve_chat_stream_identity_rejects_ambiguous_or_mismatched_values(kwargs):
    from core.chat_stream_identity import ChatStreamIdentityError, resolve_chat_stream_identity

    with pytest.raises(ChatStreamIdentityError):
        resolve_chat_stream_identity(**kwargs)


@pytest.mark.parametrize(
    "value",
    [
        "web:%:private",
        "web:%2:private",
        "web:%GG:private",
        "web:%FF:private",
        "web:%e7%be%a4:private",
    ],
)
def test_parse_canonical_identity_rejects_malformed_or_noncanonical_percent_encoding(value):
    from core.chat_stream_identity import ChatStreamIdentityError, parse_canonical_chat_stream_id

    with pytest.raises(ChatStreamIdentityError):
        parse_canonical_chat_stream_id(value)
```

同步扩展 `tests/test_group_runtime_ids.py`，锁定旧 helper 的返回值仍为
`group_123` 和 `qq:123:group`。

- [x] **步骤 2：运行测试确认红灯**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_chat_stream_identity.py tests/test_group_runtime_ids.py -v
```

预期：FAIL，首个错误为 `ModuleNotFoundError: No module named 'core.chat_stream_identity'`。

- [x] **步骤 3：实现不可变 identity 与严格解析**

在 `core/chat_stream_identity.py` 实现以下稳定接口：

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote


_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_CHAT_TYPES = {"group", "private"}
_ENCODE_SAFE = "-._~"


class ChatStreamIdentityError(ValueError):
    """会话配置身份无法无歧义规范化。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ChatStreamIdentity:
    platform: str
    chat_type: str
    external_session_id: str
    encoded_external_session_id: str
    chat_stream_id: str


def parse_canonical_chat_stream_id(chat_stream_id: str) -> ChatStreamIdentity:
    ...


def resolve_chat_stream_identity(
    *,
    platform: str,
    chat_type: str,
    session_id: str,
) -> ChatStreamIdentity:
    ...


def canonicalize_legacy_chat_stream_id(value: str) -> str | None:
    """只转换 group_/private_ 明确别名；裸 ID 返回 None。"""
    ...
```

实现要求：

- canonical 输入必须解析后重新序列化完全相同；拒绝非规范百分号编码。
- raw external ID 使用 `quote(value, safe=_ENCODE_SAFE)`。
- 解析前拒绝不是 `%[0-9A-F]{2}` 的 escape；使用
  `unquote(encoded, encoding="utf-8", errors="strict")` 后重新编码验证，避免非法
  UTF-8、双重编码和大小写漂移。
- 显式 `chat_type` 与前缀或 canonical 尾段不一致时抛错。
- 外部 ID 为空、含 NUL、Unicode `Cc` 控制字符或 `Cs` 代理码点时抛错。
- 不改变调用方原运行时 `session_id`。

让 `normalize_group_stream_id()` 和 `normalize_chat_stream_id()` 转调新接口；
`normalize_group_session_id()` 继续只处理运行时 `group_` 格式。

- [x] **步骤 4：运行 identity 测试确认绿灯**

运行同步骤 2。

预期：全部 PASS；现有 `tests/test_group_runtime_ids.py` 无回归。

验收证据（2026-07-13）：

- 初始红灯：`35 failed, 3 passed`，命中新模块缺失与旧 helper 未编码分隔符。
- 复审补充红灯：控制字符、三段 raw ID 与 surrogate 用例均命中真实缺口。
- 最终定向测试：`43 passed, 0 failed`。
- 群记忆、贴纸、群运行时与入站恢复关联回归：`95 passed, 0 failed`。
- 后端全量测试：`3141 passed, 6 skipped, 0 failed`。
- 任务文件 Ruff、全模块 `compileall`、`git diff --check` 均通过；第二轮独立复审
  `GO`，无 Critical 或 Important。

- [x] **步骤 5：授权后的提交检查点（由最终聚合提交完成）**

仅在用户明确说“提交”后执行：

```bash
git add --chmod=-x core/chat_stream_identity.py tests/test_chat_stream_identity.py
git add core/group_runtime/ids.py core/expression_memory.py tests/test_group_runtime_ids.py
git commit -m "feat(会话配置): 统一会话配置身份"
```

---

### 任务 2：增加数据库字段与历史身份迁移

**文件：**

- 修改：`core/database.py`
- 修改：`core/schema_migrations.py`
- 创建测试：`tests/test_session_guidance_schema.py`
- 修改测试：`tests/test_schema_migrations.py`
- 修改测试：`tests/test_sqlite_backup.py`

- [x] **步骤 1：编写字段迁移和 identity 迁移失败测试**

在 `tests/test_session_guidance_schema.py` 创建旧版 SQLite 表，并覆盖三种情形：

```python
from sqlalchemy import create_engine, inspect, text


def _legacy_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE chat_stream_configs ("
            "chat_stream_id TEXT PRIMARY KEY, "
            "talk_value FLOAT DEFAULT 0.5, "
            "meta_json TEXT DEFAULT '{}')"
        ))
    return engine


def test_session_guidance_columns_migration_is_idempotent(tmp_path):
    from core.schema_migrations import run_schema_migrations

    engine = _legacy_engine(tmp_path)
    run_schema_migrations(engine, db_path=str(tmp_path / "legacy.db"))
    run_schema_migrations(engine, db_path=str(tmp_path / "legacy.db"))

    columns = {column["name"] for column in inspect(engine).get_columns("chat_stream_configs")}
    assert {"session_guidance", "session_guidance_updated_at"} <= columns


def test_identity_migration_renames_uncontested_alias(tmp_path):
    from core.schema_migrations import run_schema_migrations

    engine = _legacy_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO chat_stream_configs(chat_stream_id, talk_value) "
            "VALUES ('private_456', 0.7)"
        ))

    run_schema_migrations(engine, db_path=str(tmp_path / "legacy.db"))

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT chat_stream_id, talk_value FROM chat_stream_configs"
        )).fetchall()
    assert rows == [("qq:456:private", 0.7)]


def test_identity_migration_preserves_alias_when_canonical_conflicts(tmp_path):
    from core.schema_migrations import run_schema_migrations

    engine = _legacy_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO chat_stream_configs(chat_stream_id, talk_value) VALUES "
            "('group_123', 0.2), ('qq:123:group', 0.8)"
        ))

    run_schema_migrations(engine, db_path=str(tmp_path / "legacy.db"))

    with engine.connect() as conn:
        ids = {row[0] for row in conn.execute(text(
            "SELECT chat_stream_id FROM chat_stream_configs"
        ))}
    assert ids == {"group_123", "qq:123:group"}
```

在 `tests/test_sqlite_backup.py` 增加文件 SQLite 测试，断言存在可重命名 alias 且
migration 尚未应用时 `create_sqlite_snapshot()` 恰好调用一次；内存 SQLite 不创建
文件备份。不能只证明“调用过备份函数”，还必须打开实际 `.bak.*` 文件并断言：

- 备份中仍存在迁移前 alias；
- 运行库已改为 canonical；
- 把备份复制到新的恢复路径后，SQLAlchemy 可以正常打开并读取原配置。

再 monkeypatch 第二行转换抛异常，断言 migration 整体回滚：原始 ID 集合不变，
migration version 未写入，不能留下半迁移状态。

- [x] **步骤 2：运行迁移测试确认红灯**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_session_guidance_schema.py tests/test_schema_migrations.py tests/test_sqlite_backup.py -v
```

预期：FAIL，缺少 `session_guidance` 列和两个 migration version。

- [x] **步骤 3：修改 SQLAlchemy 模型**

在 `ChatStreamConfig` 中增加：

```python
session_guidance = Column(Text, default="", nullable=False)
session_guidance_updated_at = Column(DateTime, nullable=True)
```

- [x] **步骤 4：实现两个幂等 migration**

在 `core/schema_migrations.py` 增加常量和函数：

```python
_SESSION_GUIDANCE_COLUMNS_VERSION = "20260712_chat_stream_session_guidance_columns"
_CHAT_STREAM_IDENTITY_VERSION = "20260712_chat_stream_identity_normalization"


def _chat_stream_session_guidance_columns(conn, engine, db_path):
    _add_missing_columns(conn, "chat_stream_configs", {
        "session_guidance": "TEXT NOT NULL DEFAULT ''",
        "session_guidance_updated_at": "TIMESTAMP",
    })


def _chat_stream_identity_normalization(conn, engine, db_path):
    ...


def _chat_stream_identity_needs_backup(conn) -> bool:
    ...
```

身份迁移只处理 `group_` 和 `private_` 明确 alias：目标不存在时更新主键；目标存在
时保留两行。裸 ID 和非法值不改写。将两个 version 注册到 `MIGRATIONS`。

`run_schema_migrations()` 对 SQLite 先获取带有界重试的 `BEGIN IMMEDIATE`，再使用同一
连接检查 version 和候选；文件型 SQLite 存在可重命名 alias 时，在任何 DDL/DML 前
调用 `_backup_sqlite_db()`。锁覆盖候选读取、快照、迁移和 version 写入，避免并发
runner 重复快照或预检后新 alias 被无快照改写。非 SQLite 依赖自身事务，不进入
SQLite 文件快照路径。备份失败必须阻止迁移。
所有 alias 更新和 migration version 写入位于同一事务；任一行转换异常必须回滚全部
更新。备份调用层级沿用仓库现状：`run_schema_migrations()` 调用
`_backup_sqlite_db()`，后者委托公共 `create_sqlite_snapshot()`；测试分别在正确层级
打桩，不能把两者写成同一个函数名或绕过 wrapper。

- [x] **步骤 5：运行迁移测试确认绿灯**

运行同步骤 2。

预期：全部 PASS；重复执行 migration 不新增记录、不覆盖冲突配置。

**完成证据（2026-07-13）：**

- 初始契约红灯：`7 failed, 44 passed`，准确命中缺失字段、migration version、身份
  规范化、快照与回滚能力。
- 并发审查红灯：预检后插入 alias 被零快照改写；双 runner 生成两份快照。
- 第二轮审查红灯：非 SQLite 错入文件快照、短 busy timeout 下第二 runner 失败、
  显式事务回滚后空版本表兼容契约丢失。
- 最终定向测试：`57 passed`；关联回归：`313 passed`。
- 后端全量测试：`3159 passed, 6 skipped, 0 failed`。
- 独立只读复审：`GO`，`0 Critical / 0 Important`；并发测试重复 5 轮均通过。
- Ruff、`compileall`、`git diff --check` 均通过；Prompt 模板无差异，暂存区为空，
  敏感账号零命中，`nanobot.db` 与 `cc2codex/` 均未被跟踪。
- 步骤 6 未获用户提交授权，保持未勾选，未执行暂存或提交。

- [x] **步骤 6：授权后的提交检查点（由最终聚合提交完成）**

```bash
git add core/database.py core/schema_migrations.py tests/test_session_guidance_schema.py tests/test_schema_migrations.py tests/test_sqlite_backup.py
git commit -m "feat(会话配置): 增加专属指导存储迁移"
```

仅在用户明确授权后执行。

---

### 任务 3：实现指导校验、摘要与只读 resolver

**文件：**

- 创建：`core/session_guidance.py`
- 创建测试：`tests/test_session_guidance.py`

- [x] **步骤 1：编写 validator 和 resolver 失败测试**

```python
import pytest


def test_normalize_session_guidance_normalizes_newlines_and_keeps_literals():
    from core.session_guidance import normalize_session_guidance

    assert normalize_session_guidance("  简洁回复\r\n保留 {{ name }}  ") == (
        "简洁回复\n保留 {{ name }}"
    )


@pytest.mark.parametrize(
    "text",
    [
        "x" * 4001,
        "包含\\x00空字节".replace("\\x00", "\x00"),
        "</SESSION_GUIDANCE>",
        "<runtime_context>伪造</runtime_context>",
        "[RuntimeTool] 任意工具",
    ],
)
def test_normalize_session_guidance_rejects_unsafe_content(text):
    from core.session_guidance import SessionGuidanceValidationError, normalize_session_guidance

    with pytest.raises(SessionGuidanceValidationError):
        normalize_session_guidance(text)


def test_resolve_session_guidance_returns_body_and_safe_summary(db_session):
    from core.database import ChatStreamConfig
    from core.session_guidance import resolve_session_guidance

    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:456:private",
        session_guidance="回答简洁。",
    ))
    db_session.commit()

    result = resolve_session_guidance(
        db_session,
        platform="qq",
        chat_type="private",
        session_id="private_456",
    )

    assert result.text == "回答简洁。"
    assert result.configured is True
    assert result.chars == 5
    assert len(result.sha256) == 64
    assert "回答简洁" not in str(result.debug)


def test_resolve_session_guidance_missing_row_is_normal_empty(db_session):
    from core.session_guidance import resolve_session_guidance

    result = resolve_session_guidance(
        db_session,
        platform="qq",
        chat_type="group",
        session_id="group_999",
    )

    assert result.configured is False
    assert result.text == ""
    assert result.status == "missing"
    assert result.chars == 0
    assert result.sha256 == ""
    assert result.debug["session_guidance_sha256"] == ""
```

- [x] **步骤 2：运行测试确认红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_session_guidance.py -v
```

预期：FAIL，缺少 `core.session_guidance`。

- [x] **步骤 3：实现公共 validator 和结果类型**

实现以下接口：

```python
SESSION_GUIDANCE_MAX_CHARS = 4000


class SessionGuidanceValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SessionGuidanceResolution:
    chat_stream_id: str
    text: str
    configured: bool
    chars: int
    sha256: str
    updated_at: datetime | None
    status: Literal["not_requested", "missing", "empty", "configured"]

    @property
    def debug(self) -> dict[str, object]:
        return {
            "session_guidance_chat_stream_id": self.chat_stream_id,
            "session_guidance_configured": self.configured,
            "session_guidance_chars": self.chars,
            "session_guidance_sha256": self.sha256,
            "session_guidance_resolution_status": self.status,
        }


def normalize_session_guidance(text: str) -> str:
    ...


def summarize_session_guidance(
    *,
    chat_stream_id: str,
    text: str,
    updated_at: datetime | None,
    status: Literal["not_requested", "missing", "empty", "configured"],
) -> SessionGuidanceResolution:
    ...


def resolve_session_guidance(
    db,
    *,
    platform: str,
    chat_type: str,
    session_id: str,
) -> SessionGuidanceResolution:
    ...
```

保留标记匹配大小写不敏感。数据库异常、identity 异常和非法持久化正文不得吞掉；
让异常传播到调用方实现 fail-closed。不存在配置行或正文为空返回正常 empty 结果。

`summarize_session_guidance()` 只有在规范化正文非空时计算 SHA-256。正文为空时统一：

```text
configured=False
chars=0
sha256=""
```

resolver 状态只使用：

```text
not_requested | missing | empty | configured
```

其中 `not_requested` 只用于没有 identity 的通用预览，`missing` 表示没有配置行，
`empty` 表示配置行存在但正文为空，`configured` 表示正文非空。这个 resolver status
不能与 Compiler section 的 `empty/emitted` 混用。Admin 摘要、Prompt debug 和
AgentRun meta 沿用正文摘要 hash 语义；`section_hashes["session_guidance"]` 是 flow
结构 hash，可以保留空节点的确定性 hash，二者不得混为一谈。

校验异常、resolver 异常、日志和异常消息只能包含错误码、配置键和长度等安全字段，
不得拼接原始指导正文。

- [x] **步骤 4：运行测试确认绿灯**

运行同步骤 2，预期全部 PASS。

**完成证据（2026-07-13）：**

- 初始契约红灯：`37 failed`，准确命中缺少 `core.session_guidance`；后续审查红灯
  覆盖首尾控制字符、关闭标签空白、状态/身份不一致、ORM 隐式 autoflush 和对象
  `repr` 泄露正文。
- 最终定向测试：`44 passed`；关联回归：`204 passed`。
- 后端全量测试：`3203 passed, 6 skipped, 0 failed`。
- 独立只读复审：`GO`，`0 Critical / 0 Important`；复审定向测试 `91 passed`。
- Ruff、`compileall`、`git diff --check` 均通过；Prompt 模板无差异，暂存区为空，
  敏感账号零命中，`nanobot.db` 与 `cc2codex/` 均未被跟踪。
- 新增文件在当前挂载盘显示可执行权限；步骤 5 获得授权后必须使用
  `git add --chmod=-x`，避免把 `100755` 写入 Git。
- 步骤 5 未获用户提交授权，保持未勾选，未执行暂存或提交。

- [x] **步骤 5：授权后的提交检查点（由最终聚合提交完成）**

```bash
git add --chmod=-x core/session_guidance.py tests/test_session_guidance.py
git commit -m "feat(会话配置): 增加专属指导解析服务"
```

---

### 任务 4：把 `session_guidance` 纳入 Prompt Compiler 和 flow contract

**前置条件：** `.codex/plans/prompt-runtime-contract-remediation.md` 已完成并通过专项
兼容门。本任务扩展已经强化的共享 flow contract，不重新实现另一套核心 audit。

**文件：**

- 修改：`core/prompt_v2/schema.py`
- 修改：`core/prompt_v2/context_adapters.py`
- 修改：`core/prompt_v2/compiler.py`
- 修改：`core/prompt_v2/flow_contract.py`
- 修改：`core/prompt_v2/flow.py`
- 修改：`core/prompt_v2/audit.py`
- 修改：`core/session_guidance.py`（把 ORM import 收窄到 resolver，保持 Compiler 纯依赖）
- 修改：`prompts.v2.default/chat/flow.json`
- 修改：`data/prompts_v2/chat/flow.json`
- 修改：`prompts.v2.default/chat/main.md`
- 本地同步：`data/prompts_v2/chat/main.md`（被 `data/*` 忽略，不强制纳入 Git）
- 创建测试：`tests/test_prompt_v2_session_guidance.py`
- 修改测试：`tests/test_prompt_v2.py`
- 修改测试：`tests/test_prompt_v2_audit_policy.py`
- 修改测试：`tests/test_prompt_v2_core_contract.py`
- 修改测试：`tests/test_prompt_v2_template_admin.py`

- [x] **步骤 1：编写四分支 section 顺序失败测试**

```python
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform", "chat_type"),
    [("qq", "group"), ("qq", "private"), ("web", "group"), ("web", "private")],
)
async def test_session_guidance_is_unique_between_identity_and_persona(platform, chat_type):
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            platform=platform,
            chat_type=chat_type,
            session_id=("group_123" if chat_type == "group" else "private_456"),
            user_id="u1",
            user_input="当前消息",
            persona_text="画像",
            session_guidance="保持简洁，使用中文。",
            session_guidance_chat_stream_id=(
                f"{platform}:123:group"
                if chat_type == "group"
                else f"{platform}:456:private"
            ),
        ),
        strict_audit=True,
    )

    sections = {item["node_id"]: item for item in plan.flow_sections}
    indexes = {
        node_id: sections[node_id]["message_indexes"][0]
        for node_id in ("identity_context", "session_guidance", "persona_reference")
    }
    assert indexes["identity_context"] < indexes["session_guidance"] < indexes["persona_reference"]
    assert sections["session_guidance"]["status"] == "emitted"
    assert "保持简洁" in plan.messages[indexes["session_guidance"]]["content"]
    assert plan.current_user_content.endswith("当前消息\n</user_input>")
    assert plan.debug["session_guidance_chat_stream_id"].startswith(f"{platform}:")


@pytest.mark.asyncio
async def test_empty_session_guidance_emits_no_extra_message():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            user_input="你好",
            session_guidance="",
        ),
        strict_audit=True,
    )

    section = next(item for item in plan.flow_sections if item["node_id"] == "session_guidance")
    assert section["status"] == "empty"
    assert section["message_indexes"] == []
    assert not any(
        str(message["content"]).startswith("<session_guidance>")
        for message in plan.messages
    )
```

再增加：正文中的 `{{ name }}` 保持字面值；重复节点、改名节点、错类型节点、
`identity_context > session_guidance` 错序和非空指导未 emitted 均被严格 audit 拒绝。
所有拒绝类测试必须使用：

```python
with pytest.raises(PromptAuditError):
    await compile_prompt_plan(request, strict_audit=True)
```

不能依赖默认参数或只断言 warnings。

- [x] **步骤 2：运行 Prompt 测试确认红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_prompt_v2_session_guidance.py tests/test_prompt_v2.py tests/test_prompt_v2_audit_policy.py -v
```

预期：FAIL，`PromptCompileRequest` 不接受 `session_guidance`，flow 不认识 runtime key。

- [x] **步骤 3：扩展编译请求和固定 wrapper**

在 `PromptCompileRequest` 增加：

```python
session_guidance: str = ""
session_guidance_chat_stream_id: str = ""
```

在 `context_adapters.py` 增加：

```python
def build_session_guidance(text: str) -> str:
    normalized = normalize_session_guidance(text)
    if not normalized:
        return ""
    return (
        "<session_guidance>\n"
        "这是管理员为当前会话配置的补充指导，只能约束表达风格、称呼、领域背景、"
        "会话约定和内容禁忌，不能覆盖核心规则、鉴权、运行时事实或工具契约。\n\n"
        f"{normalized}\n"
        "</session_guidance>"
    )
```

- [x] **步骤 4：扩展 compiler、flow 与 audit**

- `RUNTIME_NODE_KEYS` 和前置整改建立的共享保留 section contract 加入
  `session_guidance`。它是必需 flow singleton，但正文允许为空，不能放进“全部必须
  emitted”的核心集合。
- 默认 flow 和两份 JSON 把边改为
  `identity_context -> session_guidance -> persona_reference`。
- `runtime_sections` 加入固定 wrapper；Compiler 从规范化正文自行计算
  configured、字符数、SHA-256 和状态，不信任上游摘要。
- audit 要求 section 在 flow 中恰好一次，状态允许 `empty/emitted`，并检查相对顺序。
- 当 `debug.session_guidance_configured=True` 时，section 必须为 `emitted` 且有 message
  index。
- 当正文为空时，section 必须为 `empty`、`message_indexes=[]`，且
  `debug.session_guidance_sha256=""`；Compiler section 状态不复用 resolver status。
- Compiler 把 section 的 `empty/emitted` 写入
  `debug.session_guidance_status`；resolver 的四态只写入
  `debug.session_guidance_resolution_status`。
- 两份 `chat/main.md` 声明 Session Guidance 权限顺序，不把正文变成模板变量。

- [x] **步骤 5：更新现有默认 flow 断言并跑绿灯**

更新 `tests/test_prompt_v2.py` 中 `flow_node_ids` 断言，但空 guidance 时现有 messages
role 序列和当前用户尾部行为必须不变。

运行同步骤 2，预期全部 PASS。

**完成证据（2026-07-13）：**

- 初始契约红灯：`17 failed, 104 passed`，准确命中缺失编译字段、runtime key、保留
  节点合同和两套 flow/main 接线。
- 依赖纯度红灯：独立进程调用 wrapper 时缺少公共入口；实现后证明 Prompt Compiler
  不加载 `core.database`。固定 wrapper 审查另关闭外层空白、额外完整 wrapper 和
  debug/body 摘要不一致边界。
- 复审补强红灯：`3 failed, 19 passed`，准确命中额外 wrapper 未拒绝和 Python
  fallback label 漂移；四分支空值矩阵与缺节点 flow 激活入口同时固化。
- 最终核心定向测试：`130 passed`；关联回归：`266 passed`。
- 后端全量测试：`3225 passed, 6 skipped, 0 failed`。
- 两轮独立只读复审均为 `GO`，最终 `0 Critical / 0 Important`；复审定向复测
  `63 passed`。
- Ruff、`compileall`、`git diff --check` 均通过；两套 flow 和两套 main 当前字节
  一致，暂存区为空，敏感账号零命中，`nanobot.db` 与 `cc2codex/` 均未被跟踪，
  QQbot/OneBot/NapCat/CQ renderer 无差异。
- `data/prompts_v2/chat/main.md` 是被 `data/*` 忽略的部署本地副本；已同步当前
  工作区，但提交时不使用 `git add -f`。版本化 canonical `main.md` 与固定 wrapper
  共同提供默认权限说明。
- 新增文件在当前挂载盘显示可执行权限；步骤 6 获得授权后必须逐文件使用
  `git add --chmod=-x`，避免把 `100755` 写入 Git。
- 步骤 6 未获用户提交授权，保持未勾选，未执行暂存或提交。

- [x] **步骤 6：授权后的提交检查点（由最终聚合提交完成）**

```bash
git add core/prompt_v2/schema.py core/prompt_v2/context_adapters.py core/prompt_v2/compiler.py core/prompt_v2/flow.py core/prompt_v2/audit.py prompts.v2.default/chat/flow.json data/prompts_v2/chat/flow.json prompts.v2.default/chat/main.md tests/test_prompt_v2.py tests/test_prompt_v2_audit_policy.py tests/test_prompt_v2_core_contract.py tests/test_prompt_v2_template_admin.py
git add --chmod=-x core/prompt_v2/flow_contract.py tests/test_prompt_v2_session_guidance.py
git commit -m "feat(提示词): 编排会话专属指导节点"
```

---

### 任务 5：实现 runtime flow 幂等迁移、原子备份与回滚

**文件：**

- 创建：`core/prompt_v2/flow_migrations.py`
- 创建：`core/prompt_v2/flow_storage.py`
- 创建：`scripts/manage_prompt_flow.py`
- 修改：`core/prompt_v2/flow.py`
- 修改：`core/prompt_v2/template_registry.py`
- 修改：`bootstrap/prompt_runtime.py`
- 修改：`.gitignore`
- 创建测试：`tests/test_prompt_flow_session_guidance_migration.py`
- 修改测试：`tests/test_prompt_v2.py`
- 修改测试：`tests/test_prompt_v2_template_registry.py`
- 修改测试：`tests/test_prompt_runtime_bootstrap.py`

- [x] **步骤 1：编写 flow 迁移红灯测试**

测试输入使用旧 flow：`identity_context -> custom_node -> persona_reference`，并断言：

```python
def test_migrate_flow_inserts_guidance_after_identity_and_preserves_custom_node():
    from core.prompt_v2.flow_migrations import migrate_session_guidance_flow

    migrated, changed = migrate_session_guidance_flow(_old_custom_flow())

    assert changed is True
    assert [node["id"] for node in migrated["nodes"]].count("session_guidance") == 1
    assert {edge["from"] + "->" + edge["to"] for edge in migrated["edges"]} >= {
        "identity_context->session_guidance",
        "session_guidance->custom_node",
        "custom_node->persona_reference",
    }
```

补充测试：

- 重复迁移 `changed=False` 且 JSON 不变；
- 条件边的 `platforms/chat_types` 原样复制；
- 缺少 `identity_context` 或迁移后 contract 无效时抛错；
- 已经存在 `session_guidance`，但 node type、runtime key、重复数量或边关系不符合
  contract 时必须抛错，不能把非法节点当成“已迁移”跳过；
- 文件升级先写唯一 backup，再原子替换；
- 失败时 runtime flow 字节不变；
- 备份列表只返回文件名、时间、大小和 SHA-256，不返回正文；
- `rollback_session_guidance_flow()` 可显式选择备份并恢复原始 bytes；
- 绝对路径、`..` 和带路径分隔符的 backup name 被拒绝；
- `bootstrap.init_prompt_runtimes()` 遇到迁移异常重新抛出，而不是只写 warning。

- [x] **步骤 2：运行测试确认红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_prompt_flow_session_guidance_migration.py tests/test_prompt_v2_template_registry.py tests/test_prompt_runtime_bootstrap.py -v
```

预期：FAIL，缺少 flow migration 模块与回滚入口。

- [x] **步骤 3：实现纯 flow 迁移函数**

提供以下接口：

```python
def migrate_session_guidance_flow(flow: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    ...


def upgrade_runtime_flow_file(
    runtime_flow_path: Path,
    *,
    backup_dir: Path,
) -> dict[str, Any]:
    ...


def list_session_guidance_flow_backups(
    *,
    backup_dir: Path,
) -> list[dict[str, Any]]:
    ...


def rollback_session_guidance_flow(
    runtime_flow_path: Path,
    *,
    backup_dir: Path,
    backup_name: str,
) -> Path:
    ...
```

迁移算法：复制旧 dict；在 nodes 中定位 `identity_context`；新增一条无条件
`identity_context -> session_guidance` 边；把原有 identity 下游边的 `from` 改为
`session_guidance`，并原样保留其目标及 `platforms/chat_types` 等条件字段。运行
`validate_flow()` 和 runtime contract；验证通过后才备份，并用同目录临时文件
`replace()` 原子写回。

- [x] **步骤 4：接入 bootstrap 和 CLI 回滚**

`init_prompt_v2_runtime_dir()` 返回以下新增字段：

```python
{
    "flow_migrated": bool,
    "flow_backup_path": str,
}
```

`bootstrap/prompt_runtime.py` 记录迁移结果；初始化异常必须 `logger.exception()` 后
重新抛出，阻止服务带无效 flow 启动。

`scripts/manage_prompt_flow.py` 提供：

```bash
python scripts/manage_prompt_flow.py check-session-guidance
python scripts/manage_prompt_flow.py list-session-guidance-backups
python scripts/manage_prompt_flow.py rollback-session-guidance --backup-name chat-flow.20260712T120000Z.abcdef123456.json.bak
```

默认 runtime 目录由现有环境变量解析；回滚从
`data/prompt_template_backups/session_guidance_flow/` 读取指定合法备份。回滚前先备份
当前 flow；旧备份只要求是合法 JSON 且能通过基础 `validate_flow()`，不能要求包含
新节点。`.gitignore` 忽略该运行时备份目录。运维顺序固定为：停止服务、使用新版本
脚本恢复旧 flow、回退代码、再启动旧服务，避免新版本启动时立刻重新迁移。

- [x] **步骤 5：运行测试确认绿灯并手工检查 CLI help**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_prompt_flow_session_guidance_migration.py tests/test_prompt_v2_template_registry.py tests/test_prompt_runtime_bootstrap.py -v
python scripts/manage_prompt_flow.py --help
```

预期：pytest 全部 PASS；CLI 列出 `check-session-guidance` 和
`list-session-guidance-backups`、`rollback-session-guidance`，不修改仓库文件。

**完成证据（2026-07-13）：**

- 初始红灯：`25 failed, 16 passed`，准确命中迁移模块、CLI、registry 返回字段、
  迁移日志和 bootstrap fail-closed 缺失。
- 安全复审红灯依次复现：悬空及祖先 symlink 越界、备份摘要不匹配仍回滚、首次复制
  或迁移覆盖并发 Admin 保存；均先形成失败测试，再实施最小修复。
- 新增 `flow_storage.py`，让首次 flow 安装、upgrade、rollback 与 `save_flow()` 共用
  线程锁和 `fcntl.flock`，并使用同目录临时文件、文件/目录 fsync 和原子替换。
- 纯迁移保持未知顶层、node、edge metadata；备份保留迁移前精确 bytes，唯一文件名
  带 SHA-256 前缀，列表和回滚均校验摘要且不返回正文。
- runtime、备份和 CLI 只读检查逐级拒绝 symlink；初始化、加载或运行合同失败均
  `logger.exception()` 后原样阻止启动。
- 最终任务定向测试：`119 passed`；关联回归：`194 passed`；后端全量测试：
  `3263 passed, 6 skipped, 0 failed`。
- 三轮独立只读审查先后关闭累计 6 个 Important，最终结论为 `GO`，
  `0 Critical / 0 Important`；跨进程锁阻塞行为也已独立实测。
- Ruff、`compileall`、CLI help、CLI 只读备份列表与 `git diff --check` 均通过；
  CLI 暴露 `check-session-guidance`、`list-session-guidance-backups`、
  `rollback-session-guidance` 三个命令。
- 步骤 6 未获用户提交授权，保持未勾选，未执行暂存或提交。

- [x] **步骤 6：授权后的提交检查点（由最终聚合提交完成）**

```bash
git add core/prompt_v2/flow.py core/prompt_v2/template_registry.py bootstrap/prompt_runtime.py .gitignore tests/test_prompt_v2.py tests/test_prompt_v2_template_registry.py tests/test_prompt_runtime_bootstrap.py
git add --chmod=-x core/prompt_v2/flow_migrations.py core/prompt_v2/flow_storage.py scripts/manage_prompt_flow.py tests/test_prompt_flow_session_guidance_migration.py
git commit -m "feat(提示词): 增加运行时流程迁移回滚"
```

---

### 任务 6：在 Bridge 公共链路解析并注入指导

**文件：**

- 修改：`core/prompt_v2/schema.py`
- 修改：`nanobot_kt/bridge.py`
- 修改：`nanobot_kt/prompt_runtime.py`
- 创建测试：`tests/test_prompt_runtime_session_guidance.py`
- 修改测试：`tests/test_bridge_prompt_v2.py`
- 修改测试：`tests/test_prompt_trace_admin.py`
- 修改测试：`tests/test_kt_framework.py`
- 修改测试：`tests/test_reply_dry_run_context.py`
- 修改测试：`tests/test_streaming_bridge.py`

- [x] **步骤 1：编写群聊、私聊与 fail-closed 红灯测试**

覆盖：

```python
@pytest.mark.parametrize(
    ("chat_type", "session_id", "stream_id"),
    [
        ("group", "group_123", "qq:123:group"),
        ("private", "private_456", "qq:456:private"),
    ],
)
def test_bridge_prompt_input_carries_resolved_session_guidance(
    db_session, chat_type, session_id, stream_id
):
    ...


@pytest.mark.asyncio
async def test_guidance_db_failure_stops_before_prompt_compile(monkeypatch):
    called = {"compile": False}

    def fail_resolve(*args, **kwargs):
        raise RuntimeError("guidance db failed")

    async def forbidden_compile(*args, **kwargs):
        called["compile"] = True
        raise AssertionError("compiler must not run")

    monkeypatch.setattr("nanobot_kt.bridge.resolve_session_guidance", fail_resolve)
    monkeypatch.setattr("nanobot_kt.prompt_runtime.compile_prompt_plan", forbidden_compile)
    ...
    assert called["compile"] is False


@pytest.mark.asyncio
async def test_private_superuser_uses_private_canonical_identity_for_guidance(monkeypatch):
    captured = {}

    def fake_resolve(db, *, platform, chat_type, session_id):
        captured.update(
            platform=platform,
            chat_type=chat_type,
            session_id=session_id,
        )
        return _empty_guidance("qq:456:private")

    # Bridge metadata: chat_type=private, is_superuser=True。
    # runtime_chat_type 可以是 private_superuser，但 resolver 必须收到 private。
    ...

    assert captured == {
        "platform": "qq",
        "chat_type": "private",
        "session_id": "private_456",
    }
```

同时断言 `PromptRuntimeInput`、`PromptCompileRequest` 和 trace debug 包含 platform、
canonical 配置键、hash/字符数和安全状态，但 debug、普通日志、异常文本及
`AgentRun.meta_json` 不包含正文。

- [x] **步骤 2：运行测试确认红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_prompt_runtime_session_guidance.py tests/test_bridge_prompt_v2.py tests/test_prompt_trace_admin.py -v
```

预期：FAIL，runtime dataclass 不含指导字段，Bridge 未调用 resolver。

- [x] **步骤 3：扩展 Runtime dataclass 和透传**

给 `PromptRuntimeAssemblyContext`、`PromptRuntimeInput` 增加：

```python
session_guidance: str = ""
session_guidance_chat_stream_id: str = ""
```

`build_prompt_runtime()` 把两个字段传到 `PromptCompileRequest`。meta update 只复制
Compiler 重新计算的安全摘要字段，不合并正文，也不接受调用方提供 hash/字符数。

- [x] **步骤 4：在已有 UnitOfWork 中解析指导**

在 Bridge 已有 `build_tool_plan()` 数据库工作单元内调用：

```python
guidance = resolve_session_guidance(
    uow.db,
    platform=platform,
    chat_type=chat_type,
    session_id=session_id,
)
```

canonical identity 必须使用基础 `chat_type`（只允许 `group/private`）。
`runtime_chat_type="private_superuser"` 只用于工具策略，禁止传给
`resolve_session_guidance()`、`resolve_chat_stream_identity()` 或
`PromptCompileRequest.chat_type`。

将 `guidance.text` 和 `guidance.chat_stream_id` 传入 Prompt Runtime。异常不降级为空配置，
直接传播到现有路由技术失败和 claim settlement。

在调用 `RunTracer.start_run()` 之前构造初始 `run_meta` 时就写入：

```python
{
    "platform": platform,
    "chat_type": chat_type,
}
```

指导解析成功后只向同一个 `run_meta` 增加 canonical 配置键、configured、字符数、
完整 SHA-256 和安全状态，不增加正文。这样 resolver/DB 早期失败、正在运行中的
AgentRun 和最终 `RunTracer.finish_run(meta=run_meta)` 都保留正确 platform。
`DYNAMIC_SYSTEM_PREFIXES` 增加 `<session_guidance>`。

- [x] **步骤 5：运行测试确认绿灯**

运行同步骤 2，预期全部 PASS，且 caplog 中无指导正文。

验收证据：任务定向矩阵 `116 passed`，关联矩阵 `355 passed`，后端全量测试
`3268 passed, 6 skipped, 0 failed`；Ruff、`compileall` 与 `git diff --check`
均通过。生产实现独立复审结论为 `GO`；全量回归暴露的旧测试夹具依赖缺口已完成
红绿修复并纳入上述矩阵。

- [x] **步骤 6：授权后的提交检查点（由最终聚合提交完成）**

```bash
git add nanobot_kt/bridge.py nanobot_kt/prompt_runtime.py tests/test_prompt_runtime_session_guidance.py tests/test_bridge_prompt_v2.py tests/test_prompt_trace_admin.py
git commit -m "feat(运行时): 注入会话专属指导"
```

---

### 任务 7：扩展 Admin 会话配置 CRUD、发现与脱敏审计

**文件：**

- 创建：`app/session_config/__init__.py`
- 创建：`app/session_config/discovery_service.py`
- 修改：`api/admin/chat_config_routes.py`
- 修改：`api/admin_routes.py`
- 创建测试：`tests/test_admin_session_guidance.py`
- 修改测试：`tests/test_admin_chat_config_routes_split.py`
- 修改测试：`tests/test_api.py`

- [x] **步骤 1：编写 Admin API 红灯测试**

至少覆盖：

```python
def test_admin_upsert_session_guidance_canonicalizes_private_identity(
    client, auth_header, db_session
):
    response = client.put(
        "/api/v1/admin/configs",
        headers=auth_header,
        json={
            "platform": "qq",
            "chat_type": "private",
            "session_id": "private_456",
            "session_guidance": "回答简洁。",
        },
    )

    assert response.status_code == 200
    assert response.json()["chat_stream_id"] == "qq:456:private"


@pytest.mark.parametrize("query", ["", "?effective=1"])
def test_admin_config_lists_never_serialize_guidance_body(
    client, auth_header, db_session, query
):
    secret = "GUIDANCE_BODY_MUST_NOT_APPEAR_7f0f"
    ...
    response = client.get(
        f"/api/v1/admin/configs{query}",
        headers=auth_header,
    )
    assert response.status_code == 200
    assert secret not in response.text
    for item in response.json()["items"]:
        assert "session_guidance" not in item


def test_admin_config_detail_returns_guidance_to_authenticated_admin(client, auth_header):
    response = client.get(
        "/api/v1/admin/configs/qq%3A456%3Aprivate",
        headers=auth_header,
    )
    assert response.json()["session_guidance"] == "回答简洁。"

    unauthenticated = client.get(
        "/api/v1/admin/configs/qq%3A456%3Aprivate",
    )
    assert unauthenticated.status_code == 401


def test_admin_guidance_audit_contains_only_hash_and_length(client, auth_header, db_session):
    ...
    detail = json.loads(db_session.query(AdminAuditLog).order_by(AdminAuditLog.id.desc()).first().detail_json)
    assert "回答简洁" not in json.dumps(detail, ensure_ascii=False)
    assert detail["new_chars"] > 0
    assert len(detail["new_sha256"]) == 64


def test_admin_static_upsert_requires_session_guidance(client, auth_header):
    response = client.put(
        "/api/v1/admin/configs",
        headers=auth_header,
        json={
            "platform": "qq",
            "chat_type": "private",
            "session_id": "private_456",
        },
    )
    assert response.status_code == 422
```

再覆盖：未认证读取正文为 401；`session_guidance=""` 只清空指导；DELETE 删除整行；
4,001 字符与保留标记返回 422 且原值不变；群聊/私聊发现；平台和类型过滤；legacy
alias/canonical 冲突标记；裸动态路径返回 422。

- [x] **步骤 2：运行 Admin 测试确认红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_admin_session_guidance.py tests/test_admin_chat_config_routes_split.py tests/test_api.py -v
```

预期：FAIL，静态 `PUT /configs` 不存在，列表契约未脱敏。

- [x] **步骤 3：实现会话发现服务**

定义：

```python
@dataclass(frozen=True)
class DiscoveredChatStream:
    chat_stream_id: str
    platform: str
    chat_type: str
    session_id: str
    session_name: str
    identity_status: Literal["canonical", "legacy_alias", "unresolved", "invalid"]
    identity_conflict: bool
    legacy_aliases: tuple[str, ...]
    sources: tuple[str, ...]


def discover_chat_streams(db, *, runtime_snapshot: dict[str, Any]) -> list[DiscoveredChatStream]:
    ...
```

来源为 `ChatStreamConfig`、`AgentRun`、runtime snapshot、`ChatLog` 和
`ConversationTurn`。`AgentRun` 的 platform 从
`safe_json(row.meta_json).get("platform")` 读取；缺失 platform 的旧记录按 `qq`
处理。只接受基础 `row.chat_type in {"group", "private"}`，不得把
`private_superuser` 生成 canonical chat type。有明确前缀但缺平台的旧记录按 `qq`
处理；裸 ID 不猜测，标为 `unresolved/invalid` 且只读。

canonical 与 alias 同时存在时只让 canonical 行生效，`identity_conflict=True`，
`legacy_aliases` 列出保留行；alias 的 talk value、guidance 和其他策略不得覆盖
canonical 行，也不删除任何数据。

- [x] **步骤 4：实现 API schema、摘要和写入事务**

新增：

```python
class ConfigUpsert(ConfigUpdate):
    platform: str
    chat_type: Literal["group", "private"]
    session_id: str
    session_guidance: str


@router.put("/configs")
def upsert_config(body: ConfigUpsert, request: Request, db: Session = Depends(get_db), ...):
    ...
```

`ConfigUpdate` 增加 `session_guidance: str | None = None`，供兼容动态路径表达“未提供时
不修改”；静态 `ConfigUpsert` 必须用上面的必填 `session_guidance: str` 覆盖继承字段。
动态路径中字段未提供或为 `null` 表示不修改，空字符串表示清空，非空字符串表示校验
后保存。列表使用 summary serializer，不返回正文；单条详情 serializer 才返回正文。
新旧 PUT 均调用同一个 `_apply_config_update()`，更新指导时设置
`session_guidance_updated_at=db_now_naive()`。

审计 detail 使用 old/new 字符数和 SHA-256。禁止把 `body.model_dump()` 或原始 updates
直接传给 `audit()`。更新 `api/admin_routes.py` 的 legacy facade 导出和路由签名测试。
清空后的审计必须是 `new_chars=0`、`new_sha256=""`。SHA-256 保存完整 64 位，不能只
存日志展示用短 hash。

- [x] **步骤 5：运行 Admin 测试确认绿灯**

运行同步骤 2，预期全部 PASS；现有 effective 列表字段保持兼容。

验收证据：初始红灯 `22 failed, 86 passed`；任务定向矩阵
`115 passed`，关联回归 `221 passed`，后端全量测试
`3292 passed, 6 skipped, 0 failed`。会话发现已在 SQL 端按 session 收敛，删除与
脱敏审计保持单事务，非法分页边界返回 422；独立复审结论为 `GO`。全仓 Ruff、
`compileall` 与 `git diff --check` 均通过；全仓 Ruff 发现的既有测试前向引用问题已用
字符串注解完成最小修正，对应参数化测试 `3 passed`。

- [x] **步骤 6：授权后的提交检查点（由最终聚合提交完成）**

```bash
git add app/session_config/__init__.py app/session_config/discovery_service.py api/admin/chat_config_routes.py api/admin_routes.py tests/test_admin_session_guidance.py tests/test_admin_chat_config_routes_split.py tests/test_api.py
git commit -m "feat(管理端): 增加会话指导配置接口"
```

---

### 任务 8：支持数据库有效指导和未保存草稿预览

**文件：**

- 修改：`api/admin_routes.py`
- 修改：`app/prompt_runtime/preview_service.py`
- 修改测试：`tests/test_prompt_v2_template_admin.py`
- 修改测试：`tests/test_prompt_trace_admin.py`

- [x] **步骤 1：编写 preview override 红灯测试**

```python
def test_effective_preview_uses_unsaved_session_guidance_without_persisting(
    client, auth_header, db_session
):
    response = client.post(
        "/api/v1/admin/prompt/effective-preview",
        headers=auth_header,
        json={
            "engine": "prompt",
            "platform": "qq",
            "chat_type": "group",
            "session_id": "group_123",
            "group_id": "123",
            "user_input": "预览消息",
            "session_guidance_override": "未保存草稿",
        },
    )

    assert response.status_code == 200
    guidance = next(
        message for message in response.json()["messages"]
        if "<session_guidance>" in str(message["content"])
    )
    assert "未保存草稿" in guidance["content"]
    assert db_session.query(ChatStreamConfig).count() == 0
```

再覆盖：override 缺省读取 DB；override 空字符串预览清空；保留标记返回 422；有效
flow 编译失败返回 400；无 session 且无 override 的通用 Prompt 预览保持旧行为。
测试 monkeypatch 模型调用为直接抛 `AssertionError`，证明 effective preview 从不调用
模型；预览前后 dump `ChatStreamConfig`，证明草稿和 DB 有效预览都不写数据库。

再 monkeypatch compiler 捕获关键字参数：

```python
async def fake_compile(request, *, strict_audit=False):
    captured.append(strict_audit)
    return plan

assert captured == [True]
```

- [x] **步骤 2：运行预览测试确认红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_prompt_v2_template_admin.py tests/test_prompt_trace_admin.py -v
```

预期：FAIL，`EffectivePromptPreviewRequest` 丢弃或拒绝 override，messages 中无草稿。

- [x] **步骤 3：实现三态 override**

给请求模型增加：

```python
session_guidance_override: Any | None = None
```

请求模型接收受控原始值，避免 Pydantic 默认 422 在 `input` 字段回显对象或数组中的
草稿正文；实际字符串类型、长度、控制字符和保留标记仍统一由服务层 validator 校验。

`preview_effective_prompt_v2()` 规则：

- `override is None` 且 session ID 非空：调用数据库 resolver；
- `override is not None`：调用 validator 并构造 preview resolution，不写 DB；
- session ID 为空且无 override：使用 status=`not_requested` 的空指导，保留现有通用
  preview；
- override 非 `None` 但 identity 缺失：返回 422。

把指导正文和安全 debug 传入 `PromptCompileRequest`，响应增加指导摘要，不新增第二套
wrapper 或编译逻辑。有效预览必须调用：

```python
await compile_prompt_plan(prompt_request, strict_audit=True)
```

捕获 `PromptAuditError` 或 `PromptFlowError` 后返回 HTTP 400；不得把 audit issues 作为
warning 返回 200。异常和 trace 不得复制草稿正文。

- [x] **步骤 4：运行预览测试确认绿灯**

运行同步骤 2，预期全部 PASS。

执行记录：首轮红灯 `7 failed, 12 passed`；复审补充对象/数组 HTTP 泄露用例后红灯
`2 failed, 2 passed`，修复后任务与关联矩阵 `184 passed`。最终后端全量测试
`3299 passed, 6 skipped, 0 failed`，项目源码 Ruff、`compileall` 与
`git diff --check` 均通过；独立复审结论为 `GO`。全仓 Ruff 仅命中未纳入 Git 的
`.codex/skills/ui-ux-pro-max` 上游脚本既有告警，明确排除该本地技能目录后通过。

- [x] **步骤 5：授权后的提交检查点（由最终聚合提交完成）**

```bash
git add api/admin_routes.py app/prompt_runtime/preview_service.py tests/test_prompt_v2_template_admin.py tests/test_prompt_trace_admin.py
git commit -m "feat(提示词): 支持会话指导草稿预览"
```

---

### 任务 9：实现 Admin WebUI 会话策略页面

**文件：**

- 创建：`webui/src/features/session-config/SessionConfigsPage.jsx`
- 修改：`webui/src/App.jsx`
- 重新生成：`webui/dist/index.html`
- 构建产物：执行 build 后按 `git status --short webui/dist/assets` 记录实际新增、修改和
  删除的 hashed asset 路径；计划中不伪造 hash 文件名
- 创建测试：`tests/test_webui_session_guidance.py`
- 修改测试：`tests/test_webui_admin_redesign.py`

- [x] **步骤 1：编写 WebUI 源码契约红灯测试**

```python
from pathlib import Path


APP = Path("webui/src/App.jsx")
PAGE = Path("webui/src/features/session-config/SessionConfigsPage.jsx")


def test_admin_navigation_uses_session_strategy_page():
    app = APP.read_text(encoding="utf-8")
    assert "label: '会话策略'" in app
    assert "<SessionConfigsPage />" in app
    assert "function ConfigsPage()" not in app


def test_session_config_page_exposes_identity_filters_and_guidance_actions():
    source = PAGE.read_text(encoding="utf-8")
    for marker in (
        "platform",
        "chat_type",
        "configured",
        "session_guidance",
        "4,000",
        "预览草稿",
        "清空专属指导",
        "删除整条覆写",
        "/prompt/effective-preview",
    ):
        assert marker in source
```

再断言列表不渲染指导正文；textarea 有显式 label，字符计数使用
`Array.from(guidance).length`，超过 4,000 时禁用保存和预览；新建表单要求
platform/chat type/session ID；冲突状态有可见 Badge。不要使用 `guidance.length` 或
`maxLength={4000}`，避免把一个非 BMP Unicode 字符按两个 UTF-16 code unit 计数。

- [x] **步骤 2：运行源码测试确认红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_webui_session_guidance.py tests/test_webui_admin_redesign.py -v
```

预期：FAIL，新 feature 文件不存在，导航仍显示“群聊策略”。

- [x] **步骤 3：抽取并扩展独立页面**

将现有 `ConfigsPage` 和 `ConfigEditModal` 从 `App.jsx` 移入
`SessionConfigsPage.jsx`，导出：

```jsx
export function SessionConfigsPage() {
  // filters: platform / chat_type / configured / search / effective
  // list: 仅消费摘要字段
}
```

编辑时先 `GET /configs/{canonical}` 获取正文。保存使用静态 `PUT /configs`，body
携带显式身份和所有编辑字段。清空按钮只发送 `session_guidance: ''`；删除按钮继续
调用动态 DELETE 并明确提示会删除全部覆写。

草稿预览 POST `/prompt/effective-preview`，使用当前未保存 textarea 值作为
`session_guidance_override`；在 wide Modal 中显示 `flow_sections`、section hashes
和 messages，不调用模型。

页面显示“正文会发送给模型并进入高权限 LLM 请求日志，禁止保存 Token、密码和
隐私”。`identity_conflict` 显示醒目警告和 legacy aliases；无法规范化的 session
只读展示，不允许编辑指导。

- [x] **步骤 4：更新导航、路由与错误状态**

`App.jsx`：

```jsx
import { SessionConfigsPage } from './features/session-config/SessionConfigsPage'
// NAV: { to: '/configs', label: '会话策略', icon: Gauge }
// Route: <Route path="/configs" element={<SessionConfigsPage />} />
```

页面内 API 错误必须展示在弹窗中，不只调用 `alert()`；保存和预览期间禁用重复提交。

- [x] **步骤 5：运行源码测试、lint 和生产构建**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_webui_session_guidance.py tests/test_webui_admin_redesign.py -v
npm --prefix webui run lint
npm --prefix webui run build
```

预期：pytest 全部 PASS；ESLint 0 errors；Vite build exit 0。

构建后运行 `git status --short webui/dist`，记录 Vite 实际生成和删除的 hashed asset
文件名；后续暂存必须逐个使用这些实际路径，不使用通配符，也不遗漏旧 asset 删除。
在授权暂存之前，把这些真实路径写入任务执行记录；若团队要求计划文件本身只列精确
路径，则先回填实际路径，再进入暂存步骤。

执行记录（2026-07-14 提交前复验）：

- 源码契约红灯：初始 `7 failed, 22 passed`；审查修复补充红灯分别命中详情加载失败、
  runtime session identity 和默认配置清空分流。
- 任务源码契约：`31 passed`；全部 WebUI Python 测试：`57 passed`；关联 Admin 配置与
  Prompt 预览测试：`40 passed`。
- 新页面定向 ESLint：`0 errors, 0 warnings`。仓库级 ESLint 保持任务前既有基线
  `9 errors, 5 warnings`，问题均不位于新增页面，未在本任务扩散修改范围。
- Vite 生产构建成功：`1819 modules transformed`。实际新增
  `webui/dist/assets/index-Cp1kPXrT.js`、`webui/dist/assets/index-p6btu-hO.css`；实际删除
  `webui/dist/assets/index-C0rA7_Bq.js`、`webui/dist/assets/index-Dy9U4F7L.css`；并更新
  `webui/dist/index.html`。
- Playwright 实际浏览器验收覆盖 `1440×900` 与 `375×812`：无页面级横向溢出、无
  console/page error；详情加载、桌面表格、移动卡片、导航、草稿预览与 runtime/external
  session ID 分流均通过。
- 独立只读复审：`GO`，`0 Critical / 0 Important`。

- [x] **步骤 6：授权后的提交检查点（由最终聚合提交完成）**

```bash
git add webui/src/features/session-config/SessionConfigsPage.jsx webui/src/App.jsx tests/test_webui_session_guidance.py tests/test_webui_admin_redesign.py webui/dist/index.html
# 再按 git status 输出逐个 git add/git rm 实际生成或删除的 webui/dist/assets 文件
git commit -m "feat(管理端): 增加会话策略配置页面"
```

---

### 任务 10：补全跨链路隔离、恢复和发布回归

**文件：**

- 修改：`api/chat_route_runner.py`
- 修改：`app/group_memory/injection_service.py`
- 修改：`app/session_memory/rolling_summary.py`
- 修改：`app/prompt_runtime/preview_service.py`
- 修改：`app/group_ingress/service.py`
- 修改：`core/context_builder.py`
- 修改：`nanobot_kt/bridge.py`
- 修改：`webui/src/features/session-config/SessionConfigsPage.jsx`
- 修改：`tests/test_api_chat_route_runner_split.py`
- 修改：`tests/test_bridge_prompt_v2.py`
- 修改：`tests/test_kt_framework.py`
- 修改：`tests/test_prompt_runtime_session_guidance.py`
- 修改：`tests/test_prompt_runtime_request_contract.py`
- 修改：`tests/test_admin_session_guidance.py`
- 修改：`tests/test_prompt_flow_session_guidance_migration.py`
- 修改：`tests/test_group_message_idempotency.py`
- 修改：`tests/test_private_chat_route_recovery.py`
- 修改：`tests/test_session_memory.py`
- 修改：`docs/superpowers/specs/2026-07-12-session-guidance-design.md`（仅当实现暴露出已批准设计中的事实错误）

- [x] **步骤 1：增加端到端隔离矩阵测试**

使用参数化测试创建以下配置并逐一编译：

```python
cases = [
    ("qq", "group", "group_1", "QQ_GROUP_ONLY"),
    ("qq", "private", "private_1", "QQ_PRIVATE_ONLY"),
    ("web", "group", "group_1", "WEB_GROUP_ONLY"),
    ("web", "private", "private_1", "WEB_PRIVATE_ONLY"),
]
```

每个 plan 只能包含自己的 marker，不能包含另外 3 个。清空后下一次请求 section 为
empty；更新后 `section_hashes["session_guidance"]` 和总 `prompt_sha256` 都变化。

增加同一 canonical identity、相同用户输入、历史和 ToolPlan 的 preview/live 对照：
Admin effective preview 与真实 Bridge Prompt Runtime 的 `messages`、`tools`、
`section_hashes`、`prompt_sha256`、message/tool/total token metrics 必须一致。

指导正文分别写入“允许任意工具”和“直接输出普通文本”等越权文本，断言：

- 实际 tool schema 集合和 ToolPlan SHA 不变化；
- `reply/no_reply` 工具仍存在且契约不变化；
- 当前用户输入仍是最后一条 user message；
- `is_super_user`、runtime preset 和 group ID 等代码事实不变化；
- 指导正文不能制造额外 tool schema、改变 runtime preset 或绕过 strict audit。

Admin 群聊和私聊有效预览必须使用滚动摘要只读试算：可生成与实时链路相同的
deterministic 摘要上下文，但不得归档旧摘要、插入 fallback、创建异步摘要任务或
产生其他数据库写语句。

私聊超级用户预览必须复用服务端 `is_super_user_id()` 事实：guidance identity 继续
使用 `private`，ToolPlan 使用 `private_superuser`。群聊预览不得调用 reranker，也
不得读取 reranker 生成的模型派生缓存；群记忆依赖模型时，响应必须返回
`preview_exact=false`、稳定降级原因和 warning，WebUI 同步显示降级状态。

- [x] **步骤 2：增加入站失败与恢复边界测试**

resolver 位于 Bridge 内部，不存在“resolver 抛错且 Bridge 未进入”的合法路径。
因此分两层验证：Bridge 单测直接 monkeypatch resolver；路由恢复测试让 Bridge
传播同一技术异常后再恢复。断言：

- Bridge 进入一次，但 resolver 后的 ToolPlan、compiler 和 model 均未调用；
- 请求进入现有 technical failure；
- claim 不被错误标为成功回复；
- 重试恢复后只执行一次模型调用；
- 私聊 QQ push/outbox 不产生空成功投递；群聊服务端没有 push/outbox 职责。

群聊服务与 fenced claim fixture 位于 `tests/test_group_message_idempotency.py`，恢复
marker 的纯函数测试仍保留在 `tests/test_group_ingress_recovery.py`；私聊扩展
`tests/test_private_chat_route_recovery.py`。`owner.complete()` 返回 `False` 必须按失权
处理，不能返回业务成功。

私聊流式结果必须先成功完成 claim 再发送 `done`。结算失败发送安全错误且不得补
QQ push/outbox；`done` 已交给传输层后关闭迭代器不得补发；只有 `done` 前断连才执行
outbox 登记、同一 shielded settlement 完成和 QQ 投递。若断连发生在 settlement 已
启动但尚未返回的窗口，必须先验证同一任务成功再登记；`False` 或异常不得留下 pending
outbox。Bridge 模型能力路由必须复用首次校验的 wire schema 快照，不得第二次读取
ToolPlan 后 fail-open。

- [x] **步骤 3：运行功能相关完整回归集**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_chat_stream_identity.py tests/test_group_runtime_ids.py tests/test_session_guidance_schema.py tests/test_session_guidance.py tests/test_prompt_v2_session_guidance.py tests/test_prompt_flow_session_guidance_migration.py tests/test_prompt_runtime_session_guidance.py tests/test_prompt_runtime_request_contract.py tests/test_admin_session_guidance.py tests/test_webui_session_guidance.py tests/test_group_ingress_recovery.py tests/test_group_message_idempotency.py tests/test_private_chat_route_recovery.py tests/test_api_chat_route_runner_split.py tests/test_bridge_prompt_v2.py tests/test_kt_framework.py -v
```

预期：全部 PASS，0 failures。

- [x] **步骤 4：运行静态检查和变更边界检查**

```bash
python -m compileall core app api nanobot_kt bootstrap scripts
git diff --check
git status --short
```

人工核对：没有修改 QQbot；没有新增环境变量；没有把指导正文写进普通日志或 Admin
审计；没有暂存用户原有未跟踪文件。

- [x] **步骤 5：运行全量后端测试和 WebUI 构建**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/ -v
npm --prefix webui run lint
(cd webui && npm exec eslint -- src/features/session-config/SessionConfigsPage.jsx)
npm --prefix webui run build
```

预期：pytest `0 failures`；会话配置新增页面 ESLint 0 errors；全仓 ESLint 不超过任务
9 已冻结的既有基线 `9 errors / 5 warnings`，且不得新增命中；Vite build exit 0。

- [x] **步骤 6：执行完成前代码审查与验证技能**

- 使用 `chinese-code-review` 对实际 diff 做中文自审。
- 使用 `requesting-code-review` 做实现完整性复核。
- 使用 `verification-before-completion` 重新运行步骤 3–5 中能证明最终结论的命令。
- 检查 Prompt 默认模板与 `data/prompts_v2/` runtime 模板内容一致。
- 检查 `core/prompt_v2/variables.py` 和 `template_registry.py`：确认本功能不新增模板
  变量，flow 迁移契约准确。

执行记录（2026-07-13）：

- Task 10 功能回归 `452 passed`，主线程补充关联矩阵 `351 passed`；
- 后端全量测试 `3329 passed, 6 skipped, 0 failed`；
- Ruff、compileall、`git diff --check`、会话配置页面单文件 ESLint 和 Vite build 通过；
- 全仓 ESLint 保持已冻结的 `9 errors / 5 warnings` 基线，本次页面零命中；
- canonical/runtime `flow.json` 与 `main.md` 字节一致，身份模板、QQ 链路和锁文件无差异；
- 暂存区、工作区/索引/历史敏感命中均为零，`nanobot.db` 与 `cc2codex/` 未被跟踪；
- 独立只读复审结论为 `GO`，未发现 Critical、Important 或 Minor。

- [x] **步骤 7：授权后的最终提交检查点（由最终聚合提交完成）**

只有用户明确说“提交”后，按文件指定暂存最终测试或文档修正：

```bash
git add tests/test_prompt_runtime_session_guidance.py tests/test_admin_session_guidance.py tests/test_prompt_flow_session_guidance_migration.py tests/test_group_ingress_recovery.py tests/test_private_chat_route_recovery.py
git commit -m "test(会话指导): 补全跨链路回归验证"
```

如果这些文件已在前序授权提交中包含且最终无新增差异，则跳过空提交。

---

## 最终交付清单

- [x] 所有 session 使用 canonical 配置身份，运行时 session ID 保持不变。
- [x] `private_superuser` 只用于工具策略，指导 identity 始终使用 `private`。
- [x] `ChatStreamConfig` 新字段和两个 migration 均幂等。
- [x] 指导正文校验、摘要和 resolver 只有一套实现。
- [x] 四种 platform/chat type 分支按固定顺序注入唯一 section。
- [x] 空配置不增加 system message。
- [x] 空 guidance 的正文摘要为 `chars=0`、`sha256=""`，不与 section hash 混用。
- [x] 非法配置、数据库异常和无效 flow 均 fail-closed。
- [x] Admin 列表不返回正文，详情仅对已认证 Admin 返回。
- [x] Admin 审计只保存长度和 SHA-256。
- [x] 草稿预览不调用任何模型、不写数据库，并显式报告模型依赖上下文降级。
- [x] Admin preview 复用 live 身份和 ToolPlan；无模型依赖时生成相同 envelope 和
  metrics，无法无模型重放的检索上下文不声称精确一致。
- [x] WebUI 支持发现、新建、编辑、预览、保存、清空和删除整条覆写。
- [x] Prompt flow 自动备份、幂等迁移和 CLI 回滚均验证通过。
- [x] QQbot 端、QQ push 协议和既有运行时 session ID 未修改。
- [x] 全量 pytest 通过；会话配置页面 lint 为 0，全仓 lint 保持既有
  `9 errors / 5 warnings` 基线；WebUI build 通过。
- [x] 未使用 `git add -A` 或 `git add .`，未混入用户已有文件。
