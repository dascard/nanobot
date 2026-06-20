# RAG 过滤约束 fixture 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 强化 RAG `positive_v1` fixture，让 memory、knowledge、sticker 正例同时验证「目标命中」和「同 query decoy 不泄漏」。

**架构：** 保留现有 `positive_v1` preset 和 stable gate，不新增 CLI 参数或生产逻辑。通过 `evals/rag_benchmark/fixtures.py` seed 固定 decoy，并用 `expected.forbidden_candidate_ids`、现有 scorer 与 baseline contract 锁定过滤边界。

**技术栈：** Python、pytest、SQLAlchemy、SQLite、`core.semantic.indexer.upsert_semantic_chunks`、`evals.rag_benchmark.run`、Bash gate 脚本。

---

## 当前状态

- [x] **设计阶段已完成**

设计文档：`docs/superpowers/specs/2026-06-20-rag-filter-constraint-fixture-design.md`

提交：`7339f50 docs(评测): 设计 RAG 过滤约束 fixture`

本计划从设计后的实现阶段开始。除 `7339f50` 外，下面每个阶段完成后都要单独 commit。

## 文件结构

### 修改文件

- `evals/rag_benchmark/fixtures.py`
  - 增加 memory / knowledge / sticker decoy 常量。
  - 扩展三个 positive fixture case 的 `forbidden_candidate_ids`。
  - seed 同 query decoy 数据。

- `tests/test_rag_benchmark.py`
  - 扩展 memory / knowledge / sticker fixture 正例测试。
  - 扩展 CLI fixture gate 和 baseline contract 断言。

- `evals/baselines/rag_benchmark.json`
  - 用 stable gate 真实报告更新，预期 total / positive case 数量不变。

- `docs/evals.md`
  - 说明 RAG stable gate 仍为 9 manual + 4 fixture positive，并补充 decoy 防泄漏语义。

- `docs/todo.md`
  - 记录 P4-5H 验证状态。

- `docs/plan_walkthrough.md`
  - 增加 P4-5H 阶段详情、计划项和真实验证输出。

- `.Codex/plans/rag-filter-constraint-fixture.md`
  - 随实施推进勾选任务，记录红绿验证和提交哈希。

## 子 agent 分配建议

三个 source 的业务逻辑互相独立，但都会编辑 `evals/rag_benchmark/fixtures.py` 和 `tests/test_rag_benchmark.py`。如果使用子 agent，建议只让它们做只读或给出 patch 建议，由主线程合并。直接并行写同一文件容易产生冲突。

推荐执行方式：

- 主线程顺序实现任务 1 到任务 5。
- 如上下文压力变大，可分派 explorer 只读复核某个 source 的过滤实现。
- 不建议让 worker 同时写 `fixtures.py`。

## 任务 1：memory user/session/source decoy

**文件：**
- 修改：`tests/test_rag_benchmark.py`
- 修改：`evals/rag_benchmark/fixtures.py`
- 更新：`.Codex/plans/rag-filter-constraint-fixture.md`

- [ ] **步骤 1：写 memory 红灯测试**

在 `tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case` 的 import 中增加新常量：

```python
from evals.rag_benchmark.fixtures import (
    MEMORY_CANDIDATE_ID,
    MEMORY_CASE_ID,
    MEMORY_OTHER_SESSION_CANDIDATE_ID,
    MEMORY_OTHER_USER_CANDIDATE_ID,
    MEMORY_SESSION_SUMMARY_CANDIDATE_ID,
    build_fixture_db,
)
```

将测试中的硬编码 case id 替换为常量，并追加断言：

```python
decoy_ids = {
    MEMORY_OTHER_USER_CANDIDATE_ID,
    MEMORY_OTHER_SESSION_CANDIDATE_ID,
    MEMORY_SESSION_SUMMARY_CANDIDATE_ID,
}

assert by_case[MEMORY_CASE_ID].expected.candidate_ids == [MEMORY_CANDIDATE_ID]
assert set(by_case[MEMORY_CASE_ID].expected.forbidden_candidate_ids) == decoy_ids
assert result.candidate_ids[0] == MEMORY_CANDIDATE_ID
assert decoy_ids.isdisjoint(result.candidate_ids)
assert score.ok is True
assert score.forbidden_hits == []
assert score.unexpected_source is False
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case \
  -v -p no:cacheprovider
```

预期：FAIL，原因是新 decoy 常量尚未定义。

- [ ] **步骤 3：实现 memory fixture decoy**

在 `evals/rag_benchmark/fixtures.py` 的 memory 常量附近增加：

```python
MEMORY_OTHER_USER_ID = "rag_fixture_other_user"
MEMORY_OTHER_SESSION_ID = "rag_fixture_other_session"
MEMORY_OTHER_USER_SOURCE_ID = "fixture-memory-decoy-other-user"
MEMORY_OTHER_SESSION_SOURCE_ID = "fixture-memory-decoy-other-session"
MEMORY_SESSION_SUMMARY_SOURCE_ID = "fixture-memory-decoy-session-summary"
MEMORY_OTHER_USER_CANDIDATE_ID = f"memory_digest:{MEMORY_OTHER_USER_SOURCE_ID}:{MEMORY_SOURCE_SUB_ID}"
MEMORY_OTHER_SESSION_CANDIDATE_ID = f"memory_digest:{MEMORY_OTHER_SESSION_SOURCE_ID}:{MEMORY_SOURCE_SUB_ID}"
MEMORY_SESSION_SUMMARY_SOURCE_SUB_ID = "digest:level2"
MEMORY_SESSION_SUMMARY_CANDIDATE_ID = (
    f"session_summary:{MEMORY_SESSION_SUMMARY_SOURCE_ID}:{MEMORY_SESSION_SUMMARY_SOURCE_SUB_ID}"
)
```

在 `_memory_positive_case()` 的 `expected` 中增加：

```python
"forbidden_candidate_ids": [
    MEMORY_OTHER_USER_CANDIDATE_ID,
    MEMORY_OTHER_SESSION_CANDIDATE_ID,
    MEMORY_SESSION_SUMMARY_CANDIDATE_ID,
],
```

把 `seed_positive_fixture_db()` 中单个 memory `SemanticChunk` 扩展为列表：

```python
memory_chunks = [
    SemanticChunk(
        source_type="memory_digest",
        source_id=MEMORY_SOURCE_ID,
        source_sub_id=MEMORY_SOURCE_SUB_ID,
        title="KohakuVQ 端口冲突排查",
        text=text,
        lexical_text=lexical,
        embedding_text=lexical,
        metadata={
            "user_id": MEMORY_USER_ID,
            "session_id": MEMORY_SESSION_ID,
            "fixture": FIXTURE_PRESET,
        },
        visibility="recall",
        quality_score=0.9,
        trust_level="medium",
        source_prior=0.65,
    ),
    SemanticChunk(
        source_type="memory_digest",
        source_id=MEMORY_OTHER_USER_SOURCE_ID,
        source_sub_id=MEMORY_SOURCE_SUB_ID,
        title="KohakuVQ 其他用户端口冲突",
        text=f"{text} 这是其他用户 decoy，不允许被目标用户召回。",
        lexical_text=lexical,
        embedding_text=lexical,
        metadata={
            "user_id": MEMORY_OTHER_USER_ID,
            "session_id": MEMORY_SESSION_ID,
            "fixture": FIXTURE_PRESET,
        },
        visibility="recall",
        quality_score=0.95,
        trust_level="medium",
        source_prior=0.70,
    ),
    SemanticChunk(
        source_type="memory_digest",
        source_id=MEMORY_OTHER_SESSION_SOURCE_ID,
        source_sub_id=MEMORY_SOURCE_SUB_ID,
        title="KohakuVQ 其他会话端口冲突",
        text=f"{text} 这是其他 session decoy，不允许被目标 session 召回。",
        lexical_text=lexical,
        embedding_text=lexical,
        metadata={
            "user_id": MEMORY_USER_ID,
            "session_id": MEMORY_OTHER_SESSION_ID,
            "fixture": FIXTURE_PRESET,
        },
        visibility="recall",
        quality_score=0.95,
        trust_level="medium",
        source_prior=0.70,
    ),
    SemanticChunk(
        source_type="session_summary",
        source_id=MEMORY_SESSION_SUMMARY_SOURCE_ID,
        source_sub_id=MEMORY_SESSION_SUMMARY_SOURCE_SUB_ID,
        title="KohakuVQ session summary decoy",
        text=f"{text} 这是 session_summary decoy，source=digest 时不允许返回。",
        lexical_text=lexical,
        embedding_text=lexical,
        metadata={
            "user_id": MEMORY_USER_ID,
            "session_id": MEMORY_SESSION_ID,
            "fixture": FIXTURE_PRESET,
        },
        visibility="recall",
        quality_score=0.95,
        trust_level="medium",
        source_prior=0.70,
    ),
]
upsert_semantic_chunks(db, memory_chunks, index_version=MEMORY_INDEX_VERSION)
```

删除原来的单个 `chunk = SemanticChunk(...)` 与对应 `upsert_semantic_chunks(db, [chunk], ...)`。

- [ ] **步骤 4：运行 memory 绿灯**

运行：

```bash
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case \
  -v -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 5：提交 memory 阶段**

运行：

```bash
git add evals/rag_benchmark/fixtures.py tests/test_rag_benchmark.py .Codex/plans/rag-filter-constraint-fixture.md
git commit -m "feat(评测): 强化 memory fixture 过滤约束"
```

提交前只允许暂存上述 3 个文件。

## 任务 2：knowledge trust/source/date decoy

**文件：**
- 修改：`tests/test_rag_benchmark.py`
- 修改：`evals/rag_benchmark/fixtures.py`
- 更新：`.Codex/plans/rag-filter-constraint-fixture.md`

- [ ] **步骤 1：写 knowledge 红灯测试**

在 `test_rag_benchmark_fixture_db_supports_knowledge_positive_case` 的 import 中增加：

```python
KNOWLEDGE_LOW_TRUST_CANDIDATE_ID,
KNOWLEDGE_OLD_PUBLISHED_CANDIDATE_ID,
KNOWLEDGE_WRONG_SOURCE_CANDIDATE_ID,
```

追加断言：

```python
knowledge_decoys = {
    KNOWLEDGE_LOW_TRUST_CANDIDATE_ID,
    KNOWLEDGE_WRONG_SOURCE_CANDIDATE_ID,
    KNOWLEDGE_OLD_PUBLISHED_CANDIDATE_ID,
}

assert by_case[KNOWLEDGE_CASE_ID].filters["min_trust_level"] == "high"
assert by_case[KNOWLEDGE_CASE_ID].filters["source_type"] == "manual_file"
assert by_case[KNOWLEDGE_CASE_ID].filters["published_after"] == "2026-01-01"
assert set(by_case[KNOWLEDGE_CASE_ID].expected.forbidden_candidate_ids) == knowledge_decoys
assert knowledge_decoys.isdisjoint(knowledge_result.candidate_ids)
assert knowledge_score.forbidden_hits == []
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_knowledge_positive_case \
  -v -p no:cacheprovider
```

预期：FAIL，原因是新 decoy 常量尚未定义，或 filters 仍是旧值。

- [ ] **步骤 3：实现 knowledge fixture decoy**

在 knowledge 常量附近增加：

```python
KNOWLEDGE_LOW_TRUST_DOCUMENT_ID = 9002
KNOWLEDGE_WRONG_SOURCE_DOCUMENT_ID = 9003
KNOWLEDGE_OLD_PUBLISHED_DOCUMENT_ID = 9004
KNOWLEDGE_LOW_TRUST_CANDIDATE_ID = f"knowledge:{KNOWLEDGE_LOW_TRUST_DOCUMENT_ID}:{KNOWLEDGE_CHUNK_ID}"
KNOWLEDGE_WRONG_SOURCE_CANDIDATE_ID = f"knowledge:{KNOWLEDGE_WRONG_SOURCE_DOCUMENT_ID}:{KNOWLEDGE_CHUNK_ID}"
KNOWLEDGE_OLD_PUBLISHED_CANDIDATE_ID = f"knowledge:{KNOWLEDGE_OLD_PUBLISHED_DOCUMENT_ID}:{KNOWLEDGE_CHUNK_ID}"
```

调整 `_knowledge_positive_case()`：

```python
filters={
    "min_trust_level": "high",
    "source_type": "manual_file",
    "published_after": "2026-01-01",
},
expected={
    "candidate_ids": [KNOWLEDGE_CANDIDATE_ID],
    "forbidden_candidate_ids": [
        KNOWLEDGE_LOW_TRUST_CANDIDATE_ID,
        KNOWLEDGE_WRONG_SOURCE_CANDIDATE_ID,
        KNOWLEDGE_OLD_PUBLISHED_CANDIDATE_ID,
    ],
    "hit_at": 5,
    "expected_source_type": "knowledge",
    "requires_citation": True,
},
```

把 `_seed_knowledge_positive_fixture()` 改成 helper 方式，创建 target + 3 个 decoy。建议实现一个局部 helper：

```python
def add_knowledge_doc(
    *,
    document_id: int,
    title: str,
    text: str,
    document_kind: str,
    trust_level: str,
    published_at: str,
) -> None:
    document = KnowledgeDocument(
        id=document_id,
        document_kind=document_kind,
        title=title,
        published_at=published_at,
        status="active",
        trust_level=trust_level,
        created_by="fixture",
        updated_by="fixture",
        latest_seen=now,
        meta_json=json.dumps({"fixture": FIXTURE_PRESET}, ensure_ascii=False, sort_keys=True),
        created_at=now,
        updated_at=now,
    )
    db.add(document)
    db.flush()
    citation = {
        "document_id": str(document_id),
        "chunk_id": KNOWLEDGE_CHUNK_ID,
        "title": title,
        "trust_level": trust_level,
        "published_at": published_at,
    }
    chunk = KnowledgeChunk(
        document_id=document_id,
        chunk_id=KNOWLEDGE_CHUNK_ID,
        order_index=0,
        title=title,
        text=text,
        citation_json=json.dumps(citation, ensure_ascii=False, sort_keys=True),
        status="active",
        trust_level=trust_level,
        meta_json=json.dumps({"fixture": FIXTURE_PRESET}, ensure_ascii=False, sort_keys=True),
        created_at=now,
        updated_at=now,
    )
    db.add(chunk)
    db.flush()
    semantic_chunks.append(chunk_from_knowledge_chunk(chunk, document=document))
```

调用顺序：

```python
semantic_chunks: list[SemanticChunk] = []
add_knowledge_doc(
    document_id=KNOWLEDGE_DOCUMENT_ID,
    title="RAG 引用门禁说明",
    text="RAG 引用门禁要求 knowledge 检索返回项必须携带 citation。固定 fixture 用于验证 high trust 正例。",
    document_kind="manual_file",
    trust_level="high",
    published_at="2026-06-20",
)
add_knowledge_doc(
    document_id=KNOWLEDGE_LOW_TRUST_DOCUMENT_ID,
    title="RAG 低信任 decoy",
    text="RAG 引用门禁 decoy：低 trust 文档不应通过 high trust 过滤。",
    document_kind="manual_file",
    trust_level="low",
    published_at="2026-06-20",
)
add_knowledge_doc(
    document_id=KNOWLEDGE_WRONG_SOURCE_DOCUMENT_ID,
    title="RAG 错误来源 decoy",
    text="RAG 引用门禁 decoy：ai_daily 来源不应通过 manual_file 过滤。",
    document_kind="ai_daily",
    trust_level="high",
    published_at="2026-06-20",
)
add_knowledge_doc(
    document_id=KNOWLEDGE_OLD_PUBLISHED_DOCUMENT_ID,
    title="RAG 旧发布时间 decoy",
    text="RAG 引用门禁 decoy：旧发布时间不应通过 published_after 过滤。",
    document_kind="manual_file",
    trust_level="high",
    published_at="2025-01-01",
)
upsert_semantic_chunks(db, semantic_chunks, index_version=KNOWLEDGE_INDEX_VERSION)
```

- [ ] **步骤 4：运行 knowledge 绿灯**

运行：

```bash
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_knowledge_positive_case \
  -v -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 5：提交 knowledge 阶段**

运行：

```bash
git add evals/rag_benchmark/fixtures.py tests/test_rag_benchmark.py .Codex/plans/rag-filter-constraint-fixture.md
git commit -m "feat(评测): 强化 knowledge fixture 过滤约束"
```

提交前只允许暂存上述 3 个文件。

## 任务 3：sticker stream/global decoy

**文件：**
- 修改：`tests/test_rag_benchmark.py`
- 修改：`evals/rag_benchmark/fixtures.py`
- 更新：`.Codex/plans/rag-filter-constraint-fixture.md`

- [ ] **步骤 1：写 sticker 红灯测试**

在 `test_rag_benchmark_fixture_db_supports_sticker_positive_case` 的 import 中增加：

```python
STICKER_GLOBAL_CANDIDATE_ID,
STICKER_OTHER_STREAM_CANDIDATE_ID,
```

追加断言：

```python
sticker_decoys = {STICKER_OTHER_STREAM_CANDIDATE_ID, STICKER_GLOBAL_CANDIDATE_ID}

assert set(by_case[STICKER_CASE_ID].expected.forbidden_candidate_ids) == sticker_decoys
assert sticker_decoys.isdisjoint(sticker_result.candidate_ids)
assert sticker_score.forbidden_hits == []
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_sticker_positive_case \
  -v -p no:cacheprovider
```

预期：FAIL，原因是新 decoy 常量尚未定义。

- [ ] **步骤 3：实现 sticker fixture decoy**

在 sticker 常量附近增加：

```python
STICKER_OTHER_STREAM_ID = 9102
STICKER_GLOBAL_ID = 9103
STICKER_OTHER_STREAM_CHAT_STREAM_ID = "group:rag-fixture-sticker-other"
STICKER_OTHER_STREAM_CANDIDATE_ID = f"sticker:{STICKER_OTHER_STREAM_ID}:sticker"
STICKER_GLOBAL_CANDIDATE_ID = f"sticker:{STICKER_GLOBAL_ID}:sticker"
```

调整 `_sticker_positive_case()` 的 expected：

```python
"forbidden_candidate_ids": [
    STICKER_OTHER_STREAM_CANDIDATE_ID,
    STICKER_GLOBAL_CANDIDATE_ID,
],
```

把 `_seed_sticker_positive_fixture()` 改成 helper 方式，创建 target + 2 个 decoy。建议实现一个局部 helper：

```python
def add_sticker(
    *,
    sticker_id: int,
    chat_stream_id: str,
    sticker_hash: str,
    name: str,
    description: str,
) -> None:
    sticker = StickerMemory(
        id=sticker_id,
        chat_stream_id=normalize_sticker_stream_id(chat_stream_id=chat_stream_id),
        sticker_hash=sticker_hash,
        file_ref=f"https://example.com/{sticker_hash}.png",
        send_code=f"[CQ:image,file=https://example.com/{sticker_hash}.png]",
        name=name,
        description=description,
        tags_json=json.dumps(["开心", "拍桌", "表情包"], ensure_ascii=False),
        emotions_json=json.dumps(["happy"], ensure_ascii=False),
        source_type="fixture",
        source_count=1,
        status="active",
        usage_count=0,
        first_seen=now,
        last_seen=now,
        meta_json=json.dumps({"fixture": FIXTURE_PRESET}, ensure_ascii=False, sort_keys=True),
        preview_status="pending",
        content_hash=sticker_hash,
        dedupe_status="unique",
        describe_status="ok",
        described_at=now,
        created_at=now,
    )
    db.add(sticker)
    db.flush()
    semantic_chunk = chunk_from_sticker(sticker)
    assert semantic_chunk is not None
    semantic_chunks.append(semantic_chunk)
```

调用顺序：

```python
semantic_chunks: list[SemanticChunk] = []
add_sticker(
    sticker_id=STICKER_ID,
    chat_stream_id=STICKER_CHAT_STREAM_ID,
    sticker_hash="fixture-sticker-positive-001",
    name="开心拍桌",
    description="开心拍桌表情包，适合表达高兴、赞同和突然兴奋。",
)
add_sticker(
    sticker_id=STICKER_OTHER_STREAM_ID,
    chat_stream_id=STICKER_OTHER_STREAM_CHAT_STREAM_ID,
    sticker_hash="fixture-sticker-decoy-other-stream-001",
    name="开心拍桌其他群",
    description="开心拍桌表情包 decoy：其他 stream 不应在目标 stream 查询中返回。",
)
add_sticker(
    sticker_id=STICKER_GLOBAL_ID,
    chat_stream_id="global",
    sticker_hash="fixture-sticker-decoy-global-001",
    name="开心拍桌全局",
    description="开心拍桌表情包 decoy：include_global=false 时全局表情不应返回。",
)
upsert_semantic_chunks(db, semantic_chunks, index_version=STICKER_INDEX_VERSION)
```

- [ ] **步骤 4：运行 sticker 绿灯**

运行：

```bash
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_sticker_positive_case \
  -v -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 5：提交 sticker 阶段**

运行：

```bash
git add evals/rag_benchmark/fixtures.py tests/test_rag_benchmark.py .Codex/plans/rag-filter-constraint-fixture.md
git commit -m "feat(评测): 强化 sticker fixture 过滤约束"
```

提交前只允许暂存上述 3 个文件。

## 任务 4：CLI gate、baseline contract 和 stable baseline

**文件：**
- 修改：`tests/test_rag_benchmark.py`
- 修改：`evals/baselines/rag_benchmark.json`
- 更新：`.Codex/plans/rag-filter-constraint-fixture.md`

- [ ] **步骤 1：扩展 CLI fixture gate 断言**

在 `test_rag_benchmark_cli_runs_manual_fixture_positive_gate` 的 `scores` 断言后补充：

```python
assert scores["memory_fixture_positive_001"].get("forbidden_hits", []) == []
assert scores["knowledge_fixture_positive_001"].get("forbidden_hits", []) == []
assert scores["sticker_fixture_positive_001"].get("forbidden_hits", []) == []
```

保留：

```python
assert report["metrics"]["overall"]["positive_cases"] == 4
assert report["metrics"]["overall"]["hit@5"] == 1.0
assert report["metrics"]["overall"]["mrr"] == 1.0
```

- [ ] **步骤 2：扩展 baseline contract 断言**

在 `test_rag_benchmark_baseline_file_matches_manual_gate_contract` 中补充：

```python
assert fixture_score.get("forbidden_hits", []) == []
assert knowledge_fixture_score.get("forbidden_hits", []) == []
assert sticker_fixture_score.get("forbidden_hits", []) == []
```

并保持：

```python
assert baseline["metrics"]["overall"]["positive_cases"] == 4
assert set(baseline_scores) == stable_case_ids
```

- [ ] **步骤 3：运行合同测试验证失败或通过**

运行：

```bash
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate \
  tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract \
  -v -p no:cacheprovider
```

预期：如果 baseline 已含空 `forbidden_hits`，测试可能直接 PASS；如果真实报告统计变化导致 baseline 不一致，先记录输出，再进入步骤 4 更新 baseline。

- [ ] **步骤 4：运行 stable gate**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 NANOBOT_TESTING=1 DATABASE_URL=sqlite:///:memory: \
python -B -m evals.rag_benchmark.run \
  --manual evals/cases/rag_benchmark/manual \
  --generated tmp/rag_benchmark/empty \
  --provider-mode deterministic \
  --manual-only \
  --fixture positive_v1 \
  --fixture-db tmp/rag_benchmark/fixtures/positive_v1.db \
  --baseline evals/baselines/rag_benchmark.json \
  --min-pass-rate 1.0 \
  --min-hit-at-5 1.0 \
  --min-mrr 1.0 \
  --max-new-failures 0 \
  --max-degraded-rate 0.0 \
  --max-unexpected-source-rate 0.0
```

预期：

```text
cases=13 passed=13 failed=0
Gate passed
```

- [ ] **步骤 5：更新 baseline**

如果 `tmp/rag_benchmark/reports/latest.json` 里的 stable report 与 `evals/baselines/rag_benchmark.json` 有差异，使用真实报告更新 baseline。

不要手写伪造指标。可以使用格式化命令：

```bash
python -m json.tool tmp/rag_benchmark/reports/latest.json > /tmp/rag_benchmark_latest.json
```

然后用编辑工具把 `/tmp/rag_benchmark_latest.json` 内容同步到 `evals/baselines/rag_benchmark.json`。若只存在 latency 噪声，仍以真实 gate 输出为准。

- [ ] **步骤 6：运行相邻回归**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider
```

预期：全部 PASS。

- [ ] **步骤 7：提交 baseline 阶段**

运行：

```bash
git add tests/test_rag_benchmark.py evals/baselines/rag_benchmark.json .Codex/plans/rag-filter-constraint-fixture.md
git commit -m "test(评测): 固化 RAG 过滤约束 fixture"
```

提交前只允许暂存上述 3 个文件。

## 任务 5：文档收口和最终验证

**文件：**
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/rag-filter-constraint-fixture.md`

- [ ] **步骤 1：同步 `docs/evals.md`**

更新 RAG benchmark 小节：

- stable gate 仍为 `9 manual + 4 fixture positive`。
- `positive_v1` fixture 已从「四 source 正例」强化为「正例命中 + decoy 不泄漏」。
- memory 覆盖 user/session/source decoy。
- knowledge 覆盖 trust/source/date decoy。
- sticker 覆盖 stream/global decoy。
- group_memory 保留跨群 decoy。

- [ ] **步骤 2：同步 `docs/todo.md`**

在 P4 路线项 8 的 P4-5G 后增加 P4-5H 验证状态：

```markdown
- **P4-5H 验证状态（2026-06-20）**：RAG `positive_v1` fixture 已强化过滤约束：memory 正例新增跨 user、跨 session、跨 source decoy；knowledge 正例新增 trust、source_type、published_after decoy；sticker 正例新增其他 stream 与 global decoy；group_memory 保留跨群 decoy。RAG stable gate 输出 `cases=13 passed=13 failed=0` 和 `Gate passed`；相邻回归、PR gate、周期性 gate 和全量回归结果按本阶段真实输出记录。
```

同时把「下一阶段」更新为真实样本运营动作或下一条路线，不再把过滤约束 fixture 作为未完成项。

- [ ] **步骤 3：同步 `docs/plan_walkthrough.md`**

在进度总览增加：

```markdown
| P4-5H | 已完成 | RAG 过滤约束 fixture | memory / knowledge / sticker 正例已增加同 query decoy 与 forbidden 断言，group_memory 保留跨群 decoy | `7339f50` |
```

实现提交产生后，在同一单元格追加真实提交哈希。

新增「已完成阶段详情：P4-5H RAG 过滤约束 fixture」小节，记录：

- 目标。
- 计划项。
- 红灯输出。
- 绿灯输出。
- stable gate 输出。
- 相邻回归、PR gate、周期性 gate、全量回归输出。
- 提交边界。

- [ ] **步骤 4：运行文档自检**

运行：

```bash
rg -n "TO""DO|TB""D|待""定|占""位|xx""x|FIX""ME" \
  .Codex/plans/rag-filter-constraint-fixture.md \
  docs/evals.md docs/todo.md docs/plan_walkthrough.md
rg -n "\x{FFFD}" \
  .Codex/plans/rag-filter-constraint-fixture.md \
  docs/evals.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- \
  .Codex/plans/rag-filter-constraint-fixture.md \
  docs/evals.md docs/todo.md docs/plan_walkthrough.md
```

预期：三条命令均无问题输出。`rg` 无匹配时退出码为 1，这是可接受结果。

- [ ] **步骤 5：运行 PR gate**

运行：

```bash
bash scripts/run_eval_pr_gate.sh
```

预期：TimingGate、三个 capability gate 和 RAG gate 均输出 `Gate passed`，RAG gate 输出 `cases=13 passed=13 failed=0`。

- [ ] **步骤 6：运行周期性 gate**

运行：

```bash
bash scripts/run_eval_periodic.sh
```

预期：各子 gate 均完成，RAG gate 输出 `cases=13 passed=13 failed=0`。

- [ ] **步骤 7：运行全量测试**

运行：

```bash
python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：0 failures。

- [ ] **步骤 8：提交文档收口**

运行：

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-filter-constraint-fixture.md
git commit -m "docs(评测): 收口 RAG 过滤约束 fixture 状态"
```

提交前只允许暂存上述 4 个文件。

## 最终完成标准

- `positive_v1` fixture 仍为 4 个 positive case。
- memory / knowledge / sticker / group_memory 四个 fixture 正例全部 PASS。
- memory、knowledge、sticker、group_memory fixture score 的 `forbidden_hits` 均为空。
- RAG stable gate 输出 `cases=13 passed=13 failed=0` 和 `Gate passed`。
- `tests/test_rag_benchmark.py tests/test_eval_baseline.py` 全部通过。
- `bash scripts/run_eval_pr_gate.sh` 通过。
- `bash scripts/run_eval_periodic.sh` 通过。
- `python -B -m pytest tests/ -v -p no:cacheprovider` 通过且 0 failures。
- `docs/evals.md`、`docs/todo.md`、`docs/plan_walkthrough.md` 和本计划记录真实验证输出。
- 每个阶段改动已按本计划单独 commit。
