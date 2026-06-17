# RAG recency 评分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 knowledge、memory、sticker 三类 RAG 的最终评分使用真实 recency 分数，替代写死的 `0.5`。

**架构：** 在 `core.semantic.scoring` 中提供共享 `recency_score()`，各 RAG 服务按自己的业务对象选择时间戳并复用该函数。最终评分权重保持不变，只替换 `recency` 组件取值，并在 `score_breakdown` 暴露分数。

**技术栈：** Python、pytest、SQLAlchemy in-memory SQLite、现有 RAG 服务与语义评分工具。

---

### 任务 1：共享 recency 评分函数

**文件：**
- 修改：`core/semantic/scoring.py`
- 测试：`tests/test_semantic_scoring.py`

- [ ] **步骤 1：编写失败的测试**

```python
from datetime import datetime, timedelta


def test_recency_score_decays_from_latest_to_old():
    from core.semantic.scoring import recency_score

    now = datetime(2026, 6, 17, 12, 0, 0)

    latest = recency_score(now, now=now, half_life_days=30)
    old = recency_score(now - timedelta(days=90), now=now, half_life_days=30)

    assert latest == 1.0
    assert 0.05 <= old < 0.2
    assert latest > old


def test_recency_score_missing_and_future_timestamps_are_stable():
    from core.semantic.scoring import recency_score

    now = datetime(2026, 6, 17, 12, 0, 0)

    assert recency_score(None, now=now) == 0.5
    assert recency_score(now + timedelta(days=1), now=now) == 1.0
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -B -m pytest tests/test_semantic_scoring.py -q -p no:cacheprovider`
预期：`ImportError` 或 `AttributeError`，提示 `recency_score` 不存在。

- [ ] **步骤 3：实现最少代码**

在 `core.semantic.scoring` 中新增 `recency_score(*timestamps, now=None, half_life_days=90.0, floor=0.05, default=0.5)`，过滤空值，取最新时间戳，并按半衰期公式返回 `round(score, 12)`。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -B -m pytest tests/test_semantic_scoring.py -q -p no:cacheprovider`
预期：新增测试通过。

### 任务 2：三类 RAG 服务接入 recency

**文件：**
- 修改：`core/knowledge_rag.py`
- 修改：`core/memory_rag.py`
- 修改：`core/sticker_rag.py`
- 测试：`tests/test_knowledge_rag.py`
- 测试：`tests/test_memory_query_rag.py`
- 测试：`tests/test_sticker_rag.py`

- [ ] **步骤 1：编写失败的 RAG 回归测试**

在三个测试文件中分别构造新旧候选，保持 query、reranker 或 lexical 条件相同，断言新候选的 `score_breakdown["recency"]` 大于旧候选，并且最终分数排序体现 recency 差异。

- [ ] **步骤 2：运行测试验证失败**

运行：
`python -B -m pytest tests/test_knowledge_rag.py tests/test_memory_query_rag.py tests/test_sticker_rag.py -q -p no:cacheprovider`

预期：新测试失败，原因是 `score_breakdown` 没有 `recency` 或 recency 恒为 `0.5`。

- [ ] **步骤 3：接入实现**

三处服务都从 `core.semantic.scoring` 引入 `recency_score`。新增私有方法计算候选 recency，并在 `_final_score()` 中替换常量 `0.5`。`_result_item()` 或 `_card_dict()` 的 `score_breakdown` 加入 `recency`。

- [ ] **步骤 4：运行相关测试验证通过**

运行：
`python -B -m pytest tests/test_knowledge_rag.py tests/test_memory_query_rag.py tests/test_sticker_rag.py -q -p no:cacheprovider --durations=20`

预期：相关 RAG 测试全部通过。

### 任务 3：阶段验证与提交

**文件：**
- 修改：`core/semantic/scoring.py`
- 修改：`core/knowledge_rag.py`
- 修改：`core/memory_rag.py`
- 修改：`core/sticker_rag.py`
- 修改：`tests/test_semantic_scoring.py`
- 修改：`tests/test_knowledge_rag.py`
- 修改：`tests/test_memory_query_rag.py`
- 修改：`tests/test_sticker_rag.py`
- 新增：`docs/superpowers/specs/2026-06-17-rag-recency-scoring-design.md`
- 新增：`.Codex/plans/rag-recency-scoring.md`

- [ ] **步骤 1：完整测试**

运行：`unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/ -v -p no:cacheprovider --durations=20`
预期：`0 failures`。

- [ ] **步骤 2：检查 diff**

运行：
`git diff --check -- core/semantic/scoring.py core/knowledge_rag.py core/memory_rag.py core/sticker_rag.py tests/test_semantic_scoring.py tests/test_knowledge_rag.py tests/test_memory_query_rag.py tests/test_sticker_rag.py docs/superpowers/specs/2026-06-17-rag-recency-scoring-design.md .Codex/plans/rag-recency-scoring.md`

预期：无输出。

- [ ] **步骤 3：显式暂存并提交**

运行：
`git add core/semantic/scoring.py core/knowledge_rag.py core/memory_rag.py core/sticker_rag.py tests/test_semantic_scoring.py tests/test_knowledge_rag.py tests/test_memory_query_rag.py tests/test_sticker_rag.py docs/superpowers/specs/2026-06-17-rag-recency-scoring-design.md .Codex/plans/rag-recency-scoring.md`

提交信息：
`refactor(RAG): 启用真实 recency 评分`
