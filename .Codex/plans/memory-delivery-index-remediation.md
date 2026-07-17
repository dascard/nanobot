# 记忆、投递与语义索引后继整改实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 在 `0f0b629` 之上构建可部署后继版本，闭环 QQ push Bearer、Python 3.11、Session Summary 完整继承和 Semantic Index 原子消费。

**架构：** outbound worker 以最小环境读取专用 push Token；Session Summary 通过纯契约层完成 turn 分片、previous lineage 和 coverage CAS；Semantic Index 以带 lease token 的逻辑源 reconcile 完成 replace/delete、重试和 backfill。生产只部署包含全部任务的最终 HEAD，历史清洗和真实投递不在代码实现阶段自动执行。

**技术栈：** Python 3.11、FastAPI、SQLAlchemy、SQLite、aiohttp、pytest、Docker Compose、Prompt Runtime v2。

---

## 文件结构与职责

### 新建文件

- `app/session_memory/llm_contract.py`：Session Summary 分片、预算、manifest、inheritance 和 LLM 结果的纯契约层。
- `.Codex/plans/memory-delivery-index-remediation.md`：本实现计划。
- `docs/superpowers/specs/2026-07-17-memory-delivery-index-remediation-design.md`：已批准设计规格。

### 修改文件

- `Dockerfile`：Python 3.11 运行时。
- `.github/workflows/timing-gate-eval.yml`：CI Python 版本与生产一致。
- `.env.example`：增加空的 `NANOBOT_PUSH_TOKEN` 占位。
- `docker-compose.yml`：只向 outbound worker 传入 push Token。
- `core/outbound_delivery_service.py`：校验并持有脱敏 push Token。
- `core/outbound_transport.py`：发送 Bearer，不记录敏感 header。
- `workers/outbound_delivery_worker.py`：把 worker 配置显式传给 transport。
- `app/session_memory/config.py`：请求/state/安全余量常量。
- `app/session_memory/llm_summarizer.py`：使用纯契约、保存真实模型、执行 finalize permit。
- `app/session_memory/jobs.py`：续租、obsolete 与 owner CAS。
- `workers/session_summary_worker.py`：默认生成唯一 owner。
- `clients/new_api_client.py`：返回本次调用的内部 model/request log metadata。
- `api/admin/session_memory_routes.py`：非 failed retry 返回 409，展示 obsolete 脱敏信息。
- `core/database.py`：Semantic job/item v2 字段。
- `core/schema_migrations.py`：`20260717_semantic_index_reconcile_v2` additive migration。
- `core/semantic/jobs.py`：claim、heartbeat、retry、recover、settle 的 fenced 状态机。
- `workers/semantic_index_worker.py`：事务外 embedding、事务内 reconcile 与 job 结算。
- `core/semantic/adapters.py`：稳定 Recall Card/Session Summary 身份和 canonical 字段。
- `core/semantic/indexer.py`：无内部 commit 的原子 reconcile。
- `core/semantic/backfill.py`：逻辑源 keyset preview/enqueue。
- `core/daily_digest.py`：按逻辑 digest source 入队 replace/delete。
- `app/memory_rag.py`：按稳定 source 聚合，从 `document_id` 返回 summary row ID。
- `api/admin/rag_routes.py`：backfill preview/enqueue 与 semantic job retry。
- `core/prompt_v2/template_registry.py`、`core/prompt_v2/variables.py`：只做一致性核对；除非红测证明契约变化，否则不修改。

### 主要测试文件

- `tests/test_deploy_config.py`
- `tests/test_outbound_transport.py`
- `tests/test_outbound_delivery_worker.py`
- `tests/test_session_memory.py`
- `tests/test_kt_integration.py`
- `tests/test_admin_session_memory_browser.py`
- `tests/test_schema_migrations.py`
- `tests/test_semantic_index_worker.py`
- `tests/test_semantic_adapters.py`
- `tests/test_memory_digest.py`
- `tests/test_memory_query_rag.py`
- `tests/test_semantic_backfill.py`
- `tests/test_rag_debug.py`

## 任务 1：Python 3.11 与 QQ push Bearer

**文件：**

- 修改：`Dockerfile:13`
- 修改：`.github/workflows/timing-gate-eval.yml:31`
- 修改：`.env.example:43-52`
- 修改：`docker-compose.yml:43-68`
- 修改：`core/outbound_delivery_service.py:60-145`
- 修改：`core/outbound_transport.py:380-535`
- 修改：`workers/outbound_delivery_worker.py:48-82`
- 测试：`tests/test_deploy_config.py`
- 测试：`tests/test_outbound_transport.py`
- 测试：`tests/test_outbound_delivery_worker.py`

- [ ] **步骤 1：编写 Python 版本和 Compose allowlist 红测**

在 `tests/test_deploy_config.py` 增加：

```python
def test_runtime_image_uses_python_311():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.11-slim-bullseye" in dockerfile
    assert "FROM python:3.10" not in dockerfile


def test_outbound_worker_receives_only_dedicated_push_token():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    worker = _service_block(compose, "outbound-delivery-worker")
    keys = _environment_keys(worker)
    assert "NANOBOT_PUSH_TOKEN" in keys
    assert "NANOBOT_API_TOKEN" not in keys
    assert "NANOBOT_ADMIN_TOKEN" not in keys
```

同时把 outbound worker 的 expected allowlist 加入 `NANOBOT_PUSH_TOKEN`，并断言 `.env.example` 中该键恰好出现一次且值为空。

- [ ] **步骤 2：运行部署红测并确认失败原因**

运行：

```bash
python -B -m pytest tests/test_deploy_config.py -q -p no:cacheprovider
```

预期：FAIL，指出 Dockerfile 仍为 Python 3.10，Compose/.env example 缺少 `NANOBOT_PUSH_TOKEN`。

- [ ] **步骤 3：编写 Token 校验与 transport header 红测**

在 `tests/test_outbound_delivery_worker.py` 增加：

```python
def test_worker_config_requires_dedicated_push_token():
    with pytest.raises(ValueError, match="NANOBOT_PUSH_TOKEN 未配置"):
        OutboundWorkerConfig.from_env({
            "QQBOT_PUSH_URL": "http://10.60.42.158:8082/nanobot/push",
            "QQBOT_PUSH_TIMEOUT": "180",
            "NANOBOT_QQ_PUSH_CONFIG_REVISION": "test-v1",
            "NANOBOT_OUTBOUND_LEASE_SECONDS": "240",
        })


def test_worker_config_repr_never_contains_push_token():
    token = "push-secret-for-test"
    config = OutboundWorkerConfig.from_env({
        "QQBOT_PUSH_URL": "http://10.60.42.158:8082/nanobot/push",
        "QQBOT_PUSH_TIMEOUT": "180",
        "NANOBOT_QQ_PUSH_CONFIG_REVISION": "test-v1",
        "NANOBOT_OUTBOUND_LEASE_SECONDS": "240",
        "NANOBOT_PUSH_TOKEN": token,
    })
    assert token not in repr(config)
```

在 `tests/test_outbound_transport.py` 增加：

```python
@pytest.mark.asyncio
async def test_qq_push_sends_dedicated_bearer_header():
    outcome, session = await _deliver(
        _FakeResponse(200),
        push_token="push-token-for-test",
    )
    assert outcome.category == "success"
    _, kwargs = session.calls[0]
    assert kwargs["headers"] == {
        "Authorization": "Bearer push-token-for-test",
    }
```

修改测试 helper，使每次调用显式传入测试 token；再增加控制字符 token 被拒绝、异常文本不含 token 的测试。

- [ ] **步骤 4：运行投递红测并确认 header/配置缺失**

运行：

```bash
python -B -m pytest tests/test_outbound_transport.py tests/test_outbound_delivery_worker.py -q -p no:cacheprovider
```

预期：FAIL，函数签名没有 `push_token`，配置对象没有专用字段。

- [ ] **步骤 5：实现最小 Token 配置与 Bearer**

在 `core/outbound_transport.py` 增加：

```python
class QQPushConfigurationError(ValueError):
    """QQ push 配置无效，异常不得包含配置原值。"""


def resolve_qq_push_token(environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    token = str(source.get("NANOBOT_PUSH_TOKEN") or "").strip()
    if not token:
        raise QQPushConfigurationError("NANOBOT_PUSH_TOKEN 未配置")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in token):
        raise QQPushConfigurationError("NANOBOT_PUSH_TOKEN 包含非法控制字符")
    return token
```

给 `deliver_qq_push_with_session()` 增加必填 `push_token: str`，校验后调用：

```python
async with session.post(
    push_url,
    headers={"Authorization": f"Bearer {token}"},
    json={
        "target_type": target_type,
        "target_id": target_id,
        "message": message,
    },
    timeout=aiohttp.ClientTimeout(total=normalized_timeout),
    allow_redirects=False,
) as response:
```

给 `OutboundWorkerConfig` 增加 `push_token: str = field(repr=False)`，`from_env()` 使用 resolver；worker transport 显式传 `config.push_token`。不加入 URL scheme/IP 限制。

- [ ] **步骤 6：升级镜像和最小环境**

把 Dockerfile 改为 `python:3.11-slim-bullseye`，workflow 改为 Python 3.11；`.env.example` 增加 `NANOBOT_PUSH_TOKEN=`；Compose outbound environment 增加：

```yaml
      NANOBOT_PUSH_TOKEN: ${NANOBOT_PUSH_TOKEN:-}
```

- [ ] **步骤 7：运行任务 1 聚焦回归**

运行：

```bash
python -B -m pytest tests/test_deploy_config.py tests/test_outbound_transport.py tests/test_outbound_delivery_worker.py -q -p no:cacheprovider
```

预期：全部 PASS。

- [ ] **步骤 8：提交任务 1**

```bash
git add Dockerfile .github/workflows/timing-gate-eval.yml .env.example docker-compose.yml core/outbound_delivery_service.py core/outbound_transport.py workers/outbound_delivery_worker.py tests/test_deploy_config.py tests/test_outbound_transport.py tests/test_outbound_delivery_worker.py docs/superpowers/specs/2026-07-17-memory-delivery-index-remediation-design.md .Codex/plans/memory-delivery-index-remediation.md
git commit -m "fix(投递): 正式化推送鉴权并升级运行时"
```

## 任务 2：Session Summary 分片、预算与继承门禁

**文件：**

- 创建：`app/session_memory/llm_contract.py`
- 修改：`app/session_memory/config.py`
- 修改：`app/session_memory/llm_summarizer.py:65-240`
- 测试：`tests/test_session_memory.py`

- [ ] **步骤 1：编写 turn 完整分片红测**

在 `tests/test_session_memory.py` 增加：

```python
def test_single_oversized_turn_is_fragmented_without_character_loss():
    content = "第一段\n" + "甲" * 13000 + "\n最后一段"
    fragments = fragment_summary_turn(
        SessionSummaryTurnSnapshot(
            id=91,
            role="user",
            content=content,
            created_at=None,
            meta_json="{}",
        ),
        max_fragment_chars=4000,
    )
    assert len(fragments) > 1
    assert "".join(item.content for item in fragments) == content
    assert [item.fragment_index for item in fragments] == list(range(len(fragments)))
    assert all(item.fragment_count == len(fragments) for item in fragments)
```

再增加 manifest hash、原顺序和分片 hash 完整性的断言。

- [ ] **步骤 2：运行分片红测**

运行：

```bash
python -B -m pytest tests/test_session_memory.py -k "oversized_turn or fragment_manifest" -q -p no:cacheprovider
```

预期：FAIL，`llm_contract` 和分片 API 尚不存在。

- [ ] **步骤 3：实现不可变 fragment 与 manifest**

在新模块定义：

```python
@dataclass(frozen=True, slots=True)
class TurnFragment:
    turn_id: int
    role: str
    fragment_index: int
    fragment_count: int
    content: str
    sanitized_sha256: str
    fragment_sha256: str


@dataclass(frozen=True, slots=True)
class TurnCoverageManifest:
    ordered_turn_ids: tuple[int, ...]
    turn_hashes: tuple[str, ...]
    fragment_hashes: tuple[str, ...]
```

`fragment_summary_turn()` 先调用非截断 sanitizer，再按换行和硬边界切片。`build_coverage_manifest()` 保留输入顺序；重复或无效 turn ID 直接抛 `ValueError`。

- [ ] **步骤 4：编写完整 messages 预算红测**

测试必须构造 4000 字符 previous state、多个 fragment 和固定 system prompt，断言每批：

```python
assert request_char_count(batch.messages) <= 12000
assert tuple(hash_ for batch in batches for hash_ in batch.fragment_hashes) == manifest.fragment_hashes
```

并断言预算不足时抛 `summary_request_budget_exceeded`，不返回截断内容。

- [ ] **步骤 5：实现预算切批**

在 `config.py` 固定：

```python
SESSION_SUMMARY_LLM_MAX_REQUEST_CHARS = 12000
SESSION_SUMMARY_LLM_MAX_STATE_CHARS = 4000
SESSION_SUMMARY_LLM_REQUEST_SAFETY_CHARS = 512
```

契约层以最终 messages 计算字符总量；放不下的最后一个完整 fragment 移到下一批。删除 `_format_turn_for_llm()` 的 `max_chars=12000` 切尾和 `_chunk_source_turns()` 的单 turn 超预算漏洞。

- [ ] **步骤 6：编写 inheritance 门禁红测**

覆盖以下实际 payload：

```python
previous = {
    "summary": "用户在部署记忆链路",
    "open_threads": ["完成语义索引重建"],
    "decisions": ["先 dry-run"],
    "important_user_requests": ["不得删除 chat_logs"],
    "resolved_items": [],
    "artifacts": ["审计报告"],
    "participants": [],
    "keywords": ["语义索引"],
}
```

分别测试：每个 obligation 恰好映射一次；`resolved` 指向 `resolved_items`；未知、重复、缺失、越界 target 均抛 `summary_inheritance_invalid`；legacy 文本 obligation 必须映射到 `summary`。

- [ ] **步骤 7：实现 canonical state 和 inheritance gate**

定义：

```python
@dataclass(frozen=True, slots=True)
class SummaryObligation:
    source_id: str
    field: str
    normalized_text: str


@dataclass(frozen=True, slots=True)
class InheritanceAudit:
    obligation_count: int
    carried_count: int
    updated_count: int
    resolved_count: int
    state_sha256: str
```

`canonical_previous_state()` 优先解析 `summary_json`；超出 4000 字符抛 `summary_state_budget_exceeded`。`validate_inheritance()` 返回审计对象，业务 payload 保存前移除 `inheritance`。

- [ ] **步骤 8：集成逐批 previous state**

`PreparedSessionSummaryJob` 改为保存 manifest、batch contracts 和 previous canonical state。每批成功输出经 gate 后成为下一批 previous；不得使用 `summary_text[:1800]`。finalize 重新加载 turns 并比较 manifest。

- [ ] **步骤 9：运行任务 2 聚焦回归**

运行：

```bash
python -B -m pytest tests/test_session_memory.py -k "fragment or budget or inheritance or previous_state or manifest" -q -p no:cacheprovider
```

预期：全部 PASS。

- [ ] **步骤 10：提交任务 2**

```bash
git add app/session_memory/llm_contract.py app/session_memory/config.py app/session_memory/llm_summarizer.py tests/test_session_memory.py
git commit -m "fix(会话摘要): 保证分片覆盖与摘要继承"
```

## 任务 3：Session Summary 真实模型、续租与 coverage CAS

**文件：**

- 修改：`clients/new_api_client.py:900-960`
- 修改：`app/session_memory/llm_contract.py`
- 修改：`app/session_memory/llm_summarizer.py:220-760`
- 修改：`app/session_memory/jobs.py:1-170`
- 修改：`workers/session_summary_worker.py:1-180`
- 修改：`api/admin/session_memory_routes.py:420-455`
- 测试：`tests/test_kt_integration.py`
- 测试：`tests/test_session_memory.py`
- 测试：`tests/test_admin_session_memory_browser.py`

- [ ] **步骤 1：编写真实模型 metadata 红测**

在 `tests/test_kt_integration.py` 的 NewAPI fake response 测试中断言：

```python
assert result["_nanobot_model_id"] == "resolved-model"
assert result["_nanobot_requested_model"] == "requested-model"
assert result["_nanobot_request_log_id"] == 123
```

在 Session 测试中让调用返回 provider `model="actual-provider-model"`，随后 monkeypatch 路由为另一个模型，断言保存行仍为 `actual-provider-model` 且 request log ID 为本次调用 ID。

- [ ] **步骤 2：运行模型追踪红测**

运行：

```bash
python -B -m pytest tests/test_kt_integration.py tests/test_session_memory.py -k "request_log_id or response_model or route_change" -q -p no:cacheprovider
```

预期：FAIL，当前默认 summarizer 只返回字符串，保存阶段重新解析路由。

- [ ] **步骤 3：实现 `SessionSummaryLLMResult`**

在契约层定义：

```python
@dataclass(frozen=True, slots=True)
class SessionSummaryLLMResult:
    content: object
    model: str
    requested_model: str
    request_log_id: int | None
```

NewAPI 成功响应附加 requested model 与 log ID；默认 summarizer 返回该数据类。实际模型顺序为 `response["model"]`、`_nanobot_model_id`、`unknown`。删除 `_resolved_session_summary_model()`。自定义 `str`/`dict` 自动包装为 `custom_summarizer`。

- [ ] **步骤 4：编写乱序 finalize 红测**

构造同一 session 的 coverage 80 和 96 两个 job。先完成 96，再完成 80，断言：

```python
assert older_job.status == "obsolete"
assert active_summary.covered_until_turn_id == 96
assert db_session.query(SemanticIndexJob).filter_by(source_type="session_summary").count() == 1
```

再覆盖相同 coverage 的自身 fallback 可替换、其他 active summary 不可替换、失去 owner 不能 finalize。

- [ ] **步骤 5：实现 finalize permit 与 obsolete**

在 `jobs.py` 增加：

```python
@dataclass(frozen=True, slots=True)
class FinalizePermit:
    decision: Literal["promote", "obsolete", "lost_lease"]
    blocking_summary_id: int | None
    reason: str
```

`acquire_summary_finalize_permit()` 先 CAS 刷新当前 running owner 的 `locked_at`，再比较 active coverage。obsolete 只更新 job/meta；promote 保持 summary、semantic job、job done 单事务。

- [ ] **步骤 6：实现续租和唯一 owner**

增加 `renew_summary_job_lease(db, job_id, owner) -> bool`，条件为 `id + status=running + locked_by=owner`。每个 LLM batch 后续租，失败即停止。worker 未显式传 owner 时使用：

```python
def default_worker_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"
```

CLI 显式 `--owner` 仍优先。

- [ ] **步骤 7：收紧 Admin retry**

`retry_session_summary_job()` 对非 failed 抛专用冲突异常；路由映射为 409。obsolete meta 只返回 blocking summary ID、coverage 和 reason，不返回正文。

- [ ] **步骤 8：运行任务 3 聚焦回归**

运行：

```bash
python -B -m pytest tests/test_kt_integration.py tests/test_session_memory.py tests/test_admin_session_memory_browser.py -q -p no:cacheprovider
```

预期：全部 PASS。

- [ ] **步骤 9：提交任务 3**

```bash
git add clients/new_api_client.py app/session_memory/llm_contract.py app/session_memory/llm_summarizer.py app/session_memory/jobs.py workers/session_summary_worker.py api/admin/session_memory_routes.py tests/test_kt_integration.py tests/test_session_memory.py tests/test_admin_session_memory_browser.py
git commit -m "fix(会话摘要): 记录真实模型并阻止覆盖倒退"
```

## 任务 4：Semantic job v2 migration 与 lease fencing

**文件：**

- 修改：`core/database.py:1595-1665`
- 修改：`core/schema_migrations.py`
- 修改：`core/semantic/jobs.py`
- 修改：`workers/semantic_index_worker.py`
- 测试：`tests/test_schema_migrations.py`
- 测试：`tests/test_semantic_index_worker.py`

- [ ] **步骤 1：编写 migration 红测**

从旧 semantic 表 schema 启动迁移，断言新增列：

```python
expected_job_columns = {
    "lease_token",
    "lease_expires_at",
    "attempt_count",
    "manual_retry_count",
    "source_revision",
    "meta_json",
}
assert expected_job_columns <= job_columns
assert "source_revision" in item_columns
```

插入旧 running 和不可达 retry row，迁移后分别断言 pending、旧锁清空、retry 历史保留。重复运行 migration 后数据不再变化。

- [ ] **步骤 2：运行 migration 红测**

运行：

```bash
python -B -m pytest tests/test_schema_migrations.py -k "semantic_index_reconcile_v2" -q -p no:cacheprovider
```

预期：FAIL，migration ID 和字段不存在。

- [ ] **步骤 3：实现 additive migration 与 ORM 字段**

新增 migration ID `20260717_semantic_index_reconcile_v2`。ORM 字段与设计规格完全一致；migration 使用方言安全的列存在性检查。创建：

```text
idx_semantic_job_claim_v2(status, next_retry_at, id)
idx_semantic_job_lease_v2(status, lease_expires_at, id)
idx_semantic_job_source_revision_v2(source_type, source_id, index_version, source_revision, status)
idx_semantic_item_source_revision_v2(source_type, source_id, source_revision, status)
```

- [ ] **步骤 4：编写 claim/retry/fencing 红测**

覆盖：

```python
first = claim_next_job(db_session, worker_id="worker-a", lease_seconds=60, now=NOW)
assert first.status == "running"
assert first.lease_token
assert first.attempt_count == 1

fail_result = fail_job(
    db_session,
    job_id=first.id,
    lease_token=first.lease_token,
    error="temporary",
    retryable=True,
    now=NOW,
)
assert fail_result.status == "pending"
assert fail_result.retry_count == 1
```

再让 A lease 过期、B 重领，断言 A 的 heartbeat/finish/fail 均返回 lease lost，B 的锁与状态不变。

- [ ] **步骤 5：运行 job 红测**

运行：

```bash
python -B -m pytest tests/test_semantic_index_worker.py -k "retry or lease or heartbeat or recover" -q -p no:cacheprovider
```

预期：FAIL，当前任务没有 token/expiry，失败状态不可领取。

- [ ] **步骤 6：实现 fenced 状态机**

定义不可变 handle：

```python
@dataclass(frozen=True, slots=True)
class SemanticJobLease:
    job_id: int
    worker_id: str
    lease_token: str
    lease_expires_at: datetime
    source_revision: str
    attempt_count: int
```

claim 每次生成 `secrets.token_hex(32)`；heartbeat、recover、finish、fail 均用条件 UPDATE。retryable fail 写 `pending + next_retry_at`，预算耗尽才写 `failed + finished_at`。超时 recover 增加 retry_count，不允许无限循环。

- [ ] **步骤 7：让 worker 使用统一 fail/settle**

删除异常路径直接 `finish_job(status="failed")` 的调用。embedding 或 adapter 外部工作完成后先续租；旧 lease 丢失只记录 `lease_lost`，不得提交副作用。

- [ ] **步骤 8：运行任务 4 聚焦回归**

运行：

```bash
python -B -m pytest tests/test_schema_migrations.py tests/test_semantic_index_worker.py -q -p no:cacheprovider
```

预期：全部 PASS。

- [ ] **步骤 9：提交任务 4**

```bash
git add core/database.py core/schema_migrations.py core/semantic/jobs.py workers/semantic_index_worker.py tests/test_schema_migrations.py tests/test_semantic_index_worker.py
git commit -m "fix(语义索引): 增加任务租约与可达重试"
```

## 任务 5：稳定 adapter 身份与原子 reconcile

**文件：**

- 修改：`core/semantic/adapters.py`
- 修改：`core/semantic/indexer.py`
- 修改：`workers/semantic_index_worker.py`
- 修改：`core/daily_digest.py`
- 修改：`app/session_memory/llm_summarizer.py`
- 修改：`app/memory_rag.py`
- 测试：`tests/test_semantic_adapters.py`
- 测试：`tests/test_semantic_index_worker.py`
- 测试：`tests/test_memory_digest.py`
- 测试：`tests/test_memory_query_rag.py`

- [ ] **步骤 1：编写稳定 Recall Card ID 红测**

构造同一 digest source 的两张 card，重排顺序后断言：

```python
first = chunks_from_memory_digest(digest_rows)
second = chunks_from_memory_digest(list(reversed(digest_rows)))
assert {item.source_sub_id for item in first} == {item.source_sub_id for item in second}
assert len({item.source_sub_id for item in first if item.source_sub_id.startswith("card:")}) == 2
assert "card:0" not in {item.source_sub_id for item in first}
```

关键词和 importance 改变不换 ID；正文、类型或 evidence 改变必须换 ID。

- [ ] **步骤 2：编写 canonical Session Summary adapter 红测**

payload 同时包含 canonical 和 legacy alias，断言 `important_user_requests`、`resolved_items` 各生成一组 chunk，legacy 不重复；两个 row revision 共用 `source_id=session_id`，但 `document_id` 不同。

- [ ] **步骤 3：运行 adapter 红测**

运行：

```bash
python -B -m pytest tests/test_semantic_adapters.py -q -p no:cacheprovider
```

预期：FAIL，Recall Card 仍为 `card:0`，Session Summary 使用物理 row ID 和旧字段名。

- [ ] **步骤 4：实现稳定 identity**

新增纯函数：

```python
def canonical_recall_card_id(
    *,
    digest_source_id: str,
    card_type: str,
    text: str,
    evidence_log_ids: Sequence[int],
) -> str:
    identity = {
        "v": 1,
        "digest_source_id": digest_source_id,
        "type": canonical_card_type(card_type),
        "text": normalize_identity_text(text),
        "evidence_log_ids": sorted({int(item) for item in evidence_log_ids if int(item) > 0}),
    }
    return "rc_" + stable_hash(identity)[:24]
```

同一步增加 `normalize_identity_text()`（NFKC、合并连续空白）和 `canonical_card_type()`（只接受 MemoryDigest schema 的 canonical 枚举，非法值抛 `ValueError`）。MemoryDigest loader 按逻辑 source 聚合 L0/L1/L2。Session Summary source ID 改为 session ID，metadata 增加 document ID、coverage、revision 和 stable hash。

- [ ] **步骤 5：编写 reconcile 回滚与 stale 删除红测**

先索引旧 chunks，再 replace 为少一个 sub-ID 的新 revision。断言旧 sub-ID `status=deleted`、`deleted_at` 非空、FTS 无 row；新 expected 全 active。故障注入 FTS INSERT 抛错后，断言旧 rows/FTS 和 job running 状态全部保持原值。

- [ ] **步骤 6：实现无内部 commit 的 reconcile**

移除 `upsert_semantic_chunks()` 的无条件 `db.commit()`，增加 `commit: bool = True` 兼容参数。实现：

```python
def reconcile_semantic_source(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    source_revision: str,
    index_version: str,
    expected_chunks: list[SemanticChunk],
    delete_source_ids: Sequence[str],
    lease: SemanticJobLease,
) -> list[SemanticIndexItem]:
    assert_semantic_job_lease(db, lease)
    soft_delete_existing_source_rows(db, source_type, source_id, delete_source_ids)
    rows = upsert_semantic_chunks(
        db,
        expected_chunks,
        index_version=index_version,
        commit=False,
    )
    settle_semantic_job(db, lease, status="done", commit=False)
    return rows
```

同一步在 `core/semantic/jobs.py` 实现 `assert_semantic_job_lease()` 与 `settle_semantic_job(commit=False)`，在 `core/semantic/indexer.py` 实现 `soft_delete_existing_source_rows()`；所有 helper 只 flush。worker 成功路径最后一次 commit，异常统一 rollback。

- [ ] **步骤 7：让 producer 原子入队 replace/delete**

Session Summary 入队使用 `source_id=session_id`、新 summary revision 和 document ID meta。MemoryDigest 每个逻辑 source 只入队一个 replace；force rebuild 把旧 source ID 写入 `delete_source_ids`。空 expected 集合必须执行 delete。

- [ ] **步骤 8：兼容 Memory RAG 展开**

聚合仍按稳定 source ID，`summary_id` 优先读取 metadata 的 `document_id`；只有 metadata 缺失且旧 source ID 为数字时回退。

- [ ] **步骤 9：运行任务 5 聚焦回归**

运行：

```bash
python -B -m pytest tests/test_semantic_adapters.py tests/test_semantic_index_worker.py tests/test_memory_digest.py tests/test_memory_query_rag.py -q -p no:cacheprovider
```

预期：全部 PASS。

- [ ] **步骤 10：提交任务 5**

```bash
git add core/semantic/adapters.py core/semantic/indexer.py workers/semantic_index_worker.py core/daily_digest.py app/session_memory/llm_summarizer.py app/memory_rag.py tests/test_semantic_adapters.py tests/test_semantic_index_worker.py tests/test_memory_digest.py tests/test_memory_query_rag.py
git commit -m "fix(语义索引): 原子替换逻辑源索引"
```

## 任务 6：Cursor backfill 与 Admin retry

**文件：**

- 修改：`core/semantic/backfill.py`
- 修改：`api/admin/rag_routes.py`
- 修改：`core/semantic/jobs.py`
- 测试：`tests/test_semantic_backfill.py`
- 测试：`tests/test_rag_debug.py`

- [ ] **步骤 1：编写 keyset preview 红测**

创建超过一页的多个逻辑源，断言循环 cursor 后无遗漏/重复；第一页开始后新增高于 high-water 的 source 不进入本轮。cursor 的 index version 或 adapter manifest 改变时抛验证错误。

响应精确断言：

```python
assert set(result) == {
    "scanned",
    "current",
    "missing",
    "stale",
    "orphan",
    "enqueued",
    "next_cursor",
    "done",
    "reasons",
}
```

- [ ] **步骤 2：编写 missing/stale/orphan 红测**

分别构造无 item、部分 chunk 缺失、旧 version、source hash 不同和已归档业务源，断言互斥分类；embedding incomplete 只出现在独立 reason，不改变 lexical current 判定。

- [ ] **步骤 3：运行 backfill 红测**

运行：

```bash
python -B -m pytest tests/test_semantic_backfill.py -q -p no:cacheprovider
```

预期：FAIL，现有实现固定读取第一页并按全局 item 总数判断。

- [ ] **步骤 4：实现 opaque cursor 与逻辑源扫描**

定义：

```python
@dataclass(frozen=True, slots=True)
class SemanticBackfillCursor:
    version: int
    source_type: str
    after_anchor: int
    high_water: int
    target_index_version: str
    adapter_manifest: str
```

使用 canonical JSON + base64url 编解码，拒绝未知字段/版本。固定扫描顺序为 memory_digest、session_summary、group_memory、sticker、knowledge、orphan_sweep。preview 不调用写 schema helper，不 commit。

- [ ] **步骤 5：编写 Admin retry 红测**

`POST /api/v1/admin/rag/index-jobs/{id}/retry` 覆盖 404、409、422、200。两个相同 `expected_updated_at` 的请求一成功一冲突；成功审计与状态同事务，不返回 lease token。有效 running lease 和所有不可 retry 终态返回 409。

- [ ] **步骤 6：实现 Admin retry CAS**

请求模型：

```python
class SemanticIndexJobRetryRequest(BaseModel):
    expected_status: Literal["failed", "running"]
    expected_updated_at: datetime
    reason: str = Field(min_length=1, max_length=500)
```

核心 CAS 保留 retry_count/max_retry/source_revision，增加 manual_retry_count，清 lease/lock/finished_at，`next_retry_at=now`。running 只允许 lease 已过期。

- [ ] **步骤 7：实现 backfill preview/enqueue 路由**

新增：

```text
POST /api/v1/admin/rag/index-backfill/preview
POST /api/v1/admin/rag/index-backfill/enqueue
```

preview 只读；enqueue 对 missing/stale/orphan 分别创建 replace/delete job，并写 Admin audit。响应不包含业务正文。

- [ ] **步骤 8：运行任务 6 聚焦回归**

运行：

```bash
python -B -m pytest tests/test_semantic_backfill.py tests/test_rag_debug.py -q -p no:cacheprovider
```

预期：全部 PASS。

- [ ] **步骤 9：提交任务 6**

```bash
git add core/semantic/backfill.py core/semantic/jobs.py api/admin/rag_routes.py tests/test_semantic_backfill.py tests/test_rag_debug.py
git commit -m "fix(索引维护): 增加游标回填与任务重试"
```

## 任务 7：Prompt Runtime 契约与旧失败回归

**文件：**

- 核对：`prompts.v2.default/chat/*`
- 核对：`prompts.v2.default/tasks/*`
- 核对：`prompts.v2.default/tools/memory_query/usage.md`
- 核对：`data/prompts_v2/`
- 核对：`core/prompt_v2/variables.py`
- 核对：`core/prompt_v2/template_registry.py`
- 测试：`tests/test_prompt_runtime_request_contract.py`
- 测试：`tests/test_prompt_v2_template_admin.py`
- 测试：旧基线 22 个失败用例所在文件

- [ ] **步骤 1：运行 canonical Runtime audit**

在临时目录完整复制 `prompts.v2.default/` 作为隔离 Runtime，设置临时 HOME/LOG_DIR，不使用仓库或生产 Runtime。运行正式 audit，记录每个模板的 resolution/source/hash；预期无缺失变量、未知标记或工具合同错误。

- [ ] **步骤 2：证明本次不改变聊天 Prompt 契约**

运行：

```bash
python -B -m pytest tests/test_prompt_runtime_request_contract.py tests/test_prompt_v2_template_admin.py tests/test_prompt_v2_tool_template_integration.py -q -p no:cacheprovider
```

预期：全部 PASS。若仅 Session Summary 内部 `inheritance` 变化，canonical/runtime Markdown 均保持无 diff；不得为追求 `in_sync` 直接覆盖已有生产 Runtime。

- [ ] **步骤 3：在 Python 3.11 镜像中重跑旧基线失败文件**

运行：

```bash
python -B -m pytest tests/test_api_chat_route_runner_split.py tests/test_async_bridge.py tests/test_group_message_idempotency.py tests/test_kt_framework.py tests/test_prompt_v2_template_migration.py tests/test_timing_runtime.py tests/test_tools_package.py -v -p no:cacheprovider
```

预期：Python 3.10 缺失 `Task.cancelling()`/`asyncio.Runner` 导致的 21 项失败全部通过；news freshness 用例也必须通过。若 freshness 用例仍失败，只允许修正为显式 UTC aware 比较并补固定时钟测试，不允许全局设置 `TZ=UTC` 掩盖问题。

- [ ] **步骤 4：运行摘要与索引联合事务故障注入**

运行：

```bash
python -B -m pytest tests/test_session_memory.py tests/test_semantic_index_worker.py -k "rollback or lost_lease or obsolete or superseded" -v -p no:cacheprovider
```

预期：全部 PASS，且每个异常后数据库没有半完成状态。

- [ ] **步骤 5：提交仅由红测证明必要的 Runtime/UTC 修正**

如果步骤 1–4 没有产生代码变更，不创建空提交。如果产生变更，只添加实际修改文件：

```bash
git add core/prompt_v2/variables.py core/prompt_v2/template_registry.py creatures/nanobot/prompts/skills/news_search/search_backend.py tests/test_prompt_runtime_request_contract.py tests/test_prompt_v2_template_admin.py tests/test_tools_package.py
git commit -m "fix(运行时): 收敛提示词与时区回归"
```

## 任务 8：完整验证、审查与可部署提交

**文件：**

- 检查：本分支全部修改文件
- 更新：设计文档和本计划中的实际验证结果，不写生产秘密

- [ ] **步骤 1：运行格式与静态验证**

运行：

```bash
git diff --check
python -m compileall api app clients core workers
ruff check api app clients core workers tests
```

预期：全部退出码 0。

- [ ] **步骤 2：构建 Python 3.11 测试镜像**

构建时只传 Git 版本 build args，不传生产 Token。容器内运行：

```bash
python --version
python -c "import asyncio; assert hasattr(asyncio, 'Runner'); loop=asyncio.new_event_loop(); task=loop.create_task(asyncio.sleep(0)); assert hasattr(task, 'cancelling'); task.cancel(); loop.run_until_complete(asyncio.gather(task, return_exceptions=True)); loop.close()"
```

预期：Python 3.11.x，探针退出码 0。

- [ ] **步骤 3：运行完整测试**

在临时 Prompt Runtime、临时 HOME/LOG_DIR、临时 SQLite 下运行：

```bash
python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：0 failed；允许的 skip 必须逐项与现有 6 个基线 skip 对照，不得新增未知 skip。

- [ ] **步骤 4：运行敏感信息扫描**

检查 diff、测试日志和新增 fixture：不得出现生产 Token、API Key、Cookie、密码、完整 Authorization header 或生产消息正文。只允许测试 sentinel 值。

- [ ] **步骤 5：请求中文代码审查**

使用 `requesting-code-review` 和 `chinese-code-review`，至少覆盖：

- push secret 最小权限与日志脱敏；
- Session Summary manifest/inheritance/CAS；
- semantic migration/fencing/reconcile；
- backfill preview 零写入；
- Prompt Runtime 无意外变化。

所有 Critical/Important 问题先补红测再修复，随后重跑相关回归。

- [ ] **步骤 6：按中文提交规范整理最终提交**

检查每个任务提交只包含列出的文件，禁止 `git add -A` 或 `git add .`。运行：

```bash
git status --short
git log --oneline --decorate 0f0b629..HEAD
git diff --stat 0f0b629..HEAD
```

预期：工作区干净，提交拓扑线性，所有 subject 符合中文规范。

- [ ] **步骤 7：生成部署前证据包**

记录：最终 commit、镜像 ID、Python 版本、测试数量、Prompt audit、migration ID、预期服务重建列表和回滚要求。不得包含任何凭据原文。

- [ ] **步骤 8：进入生产部署阶段**

生产阶段重新读取 AGENTS/deploy 文件并执行：备份 → 清除 shell 继承 Token → 停旧 semantic worker → migration → 重建四服务 → HTTP/DB/容器闭环。真实日报和生产归档/全量索引重建仍遵循原专项授权边界。

## 任务 9：把 legacy compatibility drain 移交 outbound worker

**文件：**

- 修改：`core/outbound_delivery.py`
- 修改：`core/outbound_delivery_service.py`
- 修改：`core/scheduled_task_outbound.py`
- 修改：`core/daily_digest.py`
- 修改：`core/proactive_outreach.py`
- 修改：`workers/outbound_delivery_worker.py`
- 测试：`tests/test_scheduled_task_outbound.py`
- 测试：`tests/test_proactive_outbound_delivery.py`
- 测试：`tests/test_proactive_outreach.py`
- 测试：`tests/test_daily_digest.py`
- 测试：`tests/test_outbound_delivery_worker.py`

- [ ] **步骤 1：编写 server 与 producer 红测**

把 daily/proactive scheduler 的旧 drain 断言改为禁止调用；把
`OutboundWorkerConfig.from_env()` 替换为一旦调用就失败的测试替身，并清空
`NANOBOT_PUSH_TOKEN`。scheduled producer 在 `legacy_direct` 且未传
`legacy_transport` 时，断言只创建一个 pending outbox、零 delivery attempt；proactive producer
未传 `publisher` 时执行同样断言。显式 transport/publisher 的原同步兼容测试继续保留。

- [ ] **步骤 2：运行 server 与 producer 红测**

运行：

```bash
timeout 180s python -B -m pytest \
  tests/test_scheduled_task_outbound.py \
  tests/test_proactive_outbound_delivery.py \
  tests/test_proactive_outreach.py \
  tests/test_daily_digest.py \
  -k "legacy or scheduler or run_scheduled_tasks" -q -p no:cacheprovider
```

预期：FAIL，失败点分别证明默认 producer 仍读取 worker 凭据并直推、两个 server 主循环仍执行
legacy drain。

- [ ] **步骤 3：编写 worker lane、停止和 fencing 红测**

在 worker 测试中注入三个 lane，断言普通、scheduled legacy、proactive legacy 使用同一个
transport/config 且各自收到有界 `limit`；scheduled lane 抛异常时 proactive lane 仍执行；预置
或中途设置 stop 后不再调用下一次 source-specific claim。新增 stale writer 测试：快照后改变
control 的 owner/token/version，调用兼容 claim，断言返回空、transport 零调用、outbox 保持
pending。重复轮询已 delivered leaf，断言不会产生第二个 attempt。

- [ ] **步骤 4：运行 worker 与 fencing 红测**

运行：

```bash
timeout 180s python -B -m pytest \
  tests/test_outbound_delivery_worker.py \
  tests/test_scheduled_task_outbound.py \
  tests/test_proactive_outbound_delivery.py \
  -k "legacy or lane or stop or writer" -q -p no:cacheprovider
```

预期：FAIL，worker 尚未执行 compatibility lane，旧读取也没有 writer version/有效 lease 的
事务内复核入口。

- [ ] **步骤 5：实现最小职责迁移**

给 legacy claim 增加可选的 live writer snapshot CAS：显式 producer 路径维持现有
acquire/renew；worker 优先接受 control 中仍有效的 owner/token/protocol/version，并在 source
写锁事务内复核，不续租、不回显 token。若存在到期 leaf 但原 writer 已过期，worker 使用
进程内 source-scoped takeover identity 走现有 writer acquire/rebind CAS；活动 writer 不得被
抢占。两条 source-specific drain 在每次 claim 前检查 stop，并把 snapshot 或 takeover 合同传给
现有 `deliver_legacy_outbound_once()`。

scheduled/proactive producer 只有显式 transport/publisher 才同步投递；daily/proactive scheduler
删除 drain 和 worker poll 配置读取。outbound worker 新增三个 lane 的单周期编排，每 lane 独立
使用 `batch_size`，共用已解析 config/transport，异常按 lane 隔离。

- [ ] **步骤 6：运行聚焦绿灯**

运行：

```bash
timeout 300s python -B -m pytest \
  tests/test_scheduled_task_outbound.py \
  tests/test_proactive_outbound_delivery.py \
  tests/test_proactive_outreach.py \
  tests/test_daily_digest.py \
  tests/test_outbound_delivery_worker.py \
  -q -p no:cacheprovider
```

预期：全部 PASS；普通 worker 原入口仍不领取 `legacy_direct`。

- [ ] **步骤 7：运行原回归、Compose 与静态验证**

所有命令使用 OS `timeout`。重跑既有四组 758 项回归、真实 Compose sentinel 渲染、
`compileall`、目标 Ruff、`git diff --check` 和敏感信息扫描。预期 0 failures，server 渲染环境仍
没有有效 push Token，outbound worker 仍获得 sentinel。

- [ ] **步骤 8：追加提交并复审**

只按文件显式暂存，不使用 `git add -A` 或 `git add .`。提交信息：

```text
fix(投递): 将遗留兼容消费移交出站 worker
```

提交后交回原质量审查者，Critical/Important 必须清零；Minor 只记录已明确排除的 revision 重复。

## 任务 10：最终审查补充门禁

**文件：**

- 修改：`core/database.py`
- 修改：`workers/semantic_index_worker.py`
- 修改：`app/session_memory/rolling_summary.py`
- 修改：`core/semantic/jobs.py`
- 修改：`core/semantic/backfill.py`
- 按红测需要修改：`core/context_builder.py`
- 测试：`tests/test_database.py`
- 测试：`tests/test_semantic_index_worker.py`
- 测试：`tests/test_session_memory.py`

- [x] **步骤 1：编写并验证三个失败测试**

测试分别覆盖：注释开头的 `TextClause` DML 不得被 clean release 回滚；普通
`ValueError` 正文不得进入 semantic job；`mark-clear` 已提交后，持有旧 detached turns 的
rollup 不得复活摘要或任务。

运行：

```bash
python -B -m pytest \
  tests/test_database.py::test_release_clean_transaction_refuses_commented_text_dml \
  tests/test_semantic_index_worker.py::test_permanent_worker_error_never_persists_value_error_text \
  tests/test_session_memory.py::test_history_clear_fences_stale_inflight_rollup \
  -v -p no:cacheprovider
```

隔离 Python 3.11 容器中的已确认结果：`3 failed`，且均因目标门禁缺失而失败。

- [x] **步骤 2：让原始 TextClause 写识别失败闭合**

`_orm_execute_may_write()` 对原始文本采用只读 allowlist：无分号，跳过连续前导行/块注释后，
仅首 token 精确为 `SELECT` 时允许 clean release；CTE、PRAGMA、EXPLAIN、多语句和未知文本
全部失败闭合为可能写入。运行数据库事务测试，确认纯 SELECT 和 ORM 只读查询仍可释放，
注释 DML、bulk DML、flush 和 nested commit/rollback 的写标记仍正确。

- [x] **步骤 3：禁止 semantic worker 持久化普通异常正文**

`_safe_worker_error()` 对普通异常始终返回 `f"{prefix}:{type(exc).__name__}"`。已有 embedding
错误码继续由 embedding 校验函数显式返回，不经过普通异常正文放行。运行 permanent、retryable、
embedding 错误脱敏测试。

- [x] **步骤 4：实现 history-clear 与 rollup 的事务 fence**

在 `maybe_rollup_session_summary()` 的非 dry-run 写路径中使用 `begin_nested()`：

1. 对 User 或 pending turn scope 做 no-op UPDATE，取得写序列化点；
2. 在锁内重新读取 `history_clear_at`，与调用方捕获的 clear point 比较；
3. 对全部唯一 pending turn ID 做带 `session_id`、必要时带 `user_id` 的 no-op UPDATE，并要求
   affected 数量完全一致；
4. 在锁内重新调用 `get_best_session_summary()` 完成 coverage CAS；
5. 只有 fence 全部成立时，才保存 fallback、SessionSummaryJob 和 SemanticIndexJob；
6. fence 失败通过内部异常回滚 savepoint，返回 `summary=None`、`requires_commit=False` 和稳定
   `skipped_reason`，其中清除点变化或旧 turns 消失统一为 `history_clear_changed`。

若上下文构建发现 `history_clear_changed`，不得继续注入调用前加载的旧 active summary。

事务型 semantic job 入队使用 `commit=False` 时不得运行 schema DDL；rolling summary 的
savepoint 回归证明隐式 `ensure_semantic_schema()` 会在 StaticPool/SQLite 下提交同一底层连接并
破坏 savepoint。维护型 backfill 在扫描和入队前显式准备 schema。

- [x] **步骤 5：运行补充门禁聚焦回归**

```bash
python -B -m pytest tests/test_database.py tests/test_semantic_index_worker.py \
  tests/test_session_memory.py -q -p no:cacheprovider
```

预期：全部 PASS；随后重跑原 365 项聚焦组合、Prompt Runtime 458 项和完整测试。

实际验证（2026-07-17，Python 3.11 隔离容器、`--network none`、空生产凭据）：

- 事务/semantic/session 三文件：`132 passed`；
- 记忆、日报、迁移、semantic、RAG 聚焦组合：`309 passed`；
- Prompt Runtime、KT Bridge 与工具合同扩大组合：`580 passed`；
- scheduled/proactive/outbound 投递链：`207 passed`；
- 首轮完整测试：`4878 passed, 6 skipped, 1 failed`，唯一失败证明原始
  `text("SELECT 1")` 的 clean release 合同被过度保守策略破坏；
- 增加只读 allowlist 红测并修复后，最终完整测试：
  `4892 passed, 6 skipped, 0 failed`；
- `git diff --check` 与 `compileall api app clients core workers tests` 退出码均为 0；
- 两轮最终中文审查均为 Critical 0、Important 0；指出的反向 clear/rollup 时序与
  TextClause allowlist 边界两个 Minor 均已补充测试；
- Ruff：测试镜像与宿主均未安装，明确标记为尚未执行。

## 任务 11：生产响应继承审计兼容

**文件：**

- 修改：`app/session_memory/llm_contract.py`
- 修改：`app/session_memory/llm_summarizer.py`
- 修改：`tests/test_session_memory.py`
- 修改：`docs/superpowers/specs/2026-07-17-memory-delivery-index-remediation-design.md`
- 修改：`.Codex/plans/memory-delivery-index-remediation.md`

- [x] **步骤 1：补充生产形态红测**

构造两个 `decisions` obligation 被模型合并到唯一输出项的脱敏 fixture：模型仍把两项
标为 `carried`，目标索引分别为 0、1。断言规范化后两项均为 `updated` 且共享索引 0，
严格 inheritance gate 通过，同时原 payload 不被修改。补充 Prompt 对 0-based 索引和
多来源合并映射的断言。

- [x] **步骤 2：证明旧代码红灯**

运行新增测试，预期因规范化入口尚不存在或原严格门禁拒绝合并后索引而失败。

- [x] **步骤 3：实现纯函数保守规范化**

只修复已知唯一 source、同字段、唯一非空目标且索引仍位于原 obligation 序号范围的
合并元数据；有效目标上的错误 `carried` 改为 `updated`。未知、重复、跨字段、多目标
歧义、负索引、超出原序号范围和空目标保持失败闭合。编排层在完整响应预算检查之后、
严格 `validate_inheritance()` 之前调用该纯函数，并在批次 trace 中记录脱敏的
`normalized_count`。

- [x] **步骤 4：运行负向边界与真实响应只读重放**

验证多目标、任意大索引和跨字段仍失败；在禁网、只读挂载中重放 LLM 日志 24882，
只输出结构计数，不输出请求、响应或召回正文。

- [x] **步骤 5：运行完整验证并版本化提交**

执行 `tests/test_session_memory.py`、全量隔离 pytest、`compileall`、`git diff --check`
和中文代码/安全审查。仅在 0 failures 后按文件精确暂存并提交，不包含生产配置或数据。

实际验证（2026-07-18，Python 3.11 隔离容器、`--network none`、空生产凭据）：

- 新增红测：`5 failed`，失败原因分别为规范化入口和 Prompt 说明缺失；
- 规范化、负向边界、Prompt 与编排 trace：`11 passed`；
- `tests/test_session_memory.py`：`122 passed`；
- LLM 日志 24882 禁网只读重放：5 个 obligation 通过，运行时规范化 2 项，业务字段不变；
- 完整测试：`4922 passed, 6 skipped, 0 failed`；
- 安全审查与事务审查均为 Critical 0、Important 0、Minor 0；
- 另修正两个只在 Asia/Shanghai 暴露的测试 fixture UTC 语义，生产调用链审计未发现本地
  naive 时间进入 chat delivery 或 outbound run 账本。
