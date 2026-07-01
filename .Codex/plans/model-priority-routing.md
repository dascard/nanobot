# 模型优先级排序实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:test-driven-development 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 修复模型优先级排序在 `cost_input_1m = None` 时崩溃的问题，并明确 `get_ordered_candidates()` 的候选排序契约。

**架构：** 在 `clients/model_registry.py` 集中提供成本归一化 helper，所有模型排序、过滤、fallback、日志格式化和 registry 写入共用该边界。`clients/new_api_client.py` 在 override 合并后调用归一化，避免 `null` 成本污染候选链。

**技术栈：** Python、pytest、现有 `NewAPIClient` / `ModelRegistry`。

---

### 任务 1：红灯测试

**文件：**
- 修改：`tests/test_model_router.py`
- 修改：`tests/test_model_registry.py`

- [ ] **步骤 1：为优先级分数和候选排序写失败测试**

在 `tests/test_model_router.py` 增加 3 个测试：

```python
def test_priority_score_treats_none_cost_as_unknown():
    from clients.model_registry import ModelRegistry

    unknown = {"id": "unknown", "cost_input_1m": None, "intelligence": 8, "tags": []}
    known = {"id": "known", "cost_input_1m": 0.2, "intelligence": 8, "tags": []}

    assert ModelRegistry.compute_priority_score(known) < ModelRegistry.compute_priority_score(unknown)
```

```python
def test_ordered_candidates_handles_none_cost_and_keeps_floor_first(monkeypatch):
    from clients import new_api_client as module
    from clients.new_api_client import NewAPIClient

    class FakeRegistry:
        def get_models_by_provider(self, provider):
            assert provider == "x"
            return [
                {"id": "below-free", "provider": "x", "intelligence": 6, "cost_input_1m": 0.0, "tags": ["free"]},
                {"id": "qualified-known", "provider": "x", "intelligence": 8, "cost_input_1m": 0.2, "tags": []},
                {"id": "qualified-null", "provider": "x", "intelligence": 9, "cost_input_1m": None, "tags": []},
            ]

        def compute_priority_score(self, model):
            from clients.model_registry import ModelRegistry
            return ModelRegistry.compute_priority_score(model)

    monkeypatch.setattr(module, "registry", FakeRegistry())
    monkeypatch.setattr(NewAPIClient, "_failure_tracker", None)
    monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: None)

    client = NewAPIClient(api_key="test", base_url="http://test")
    ids = [
        item["id"]
        for item in client.get_ordered_candidates("x", intel_floor=8, max_cost=1.0)
    ]

    assert ids == ["qualified-known", "below-free"]
```

```python
def test_model_override_null_cost_keeps_base_cost(monkeypatch):
    from clients.new_api_client import NewAPIClient

    monkeypatch.setattr(
        NewAPIClient,
        "_model_overrides_cache",
        {"paid-model": {"cost_input_1m": None, "cost_output_1m": None}},
    )

    client = NewAPIClient(api_key="test", base_url="http://test")
    merged = client._apply_model_override(
        "paid-model",
        {
            "id": "paid-model",
            "tags": ["paid"],
            "cost_input_1m": 0.2,
            "cost_output_1m": 0.8,
            "description": "base",
        },
    )

    assert merged["cost_input_1m"] == 0.2
    assert merged["cost_output_1m"] == 0.8
```

- [ ] **步骤 2：为旧 registry 入口写失败测试**

在 `tests/test_model_registry.py` 增加 2 个测试：

```python
def test_select_model_treats_none_cost_as_unknown_under_budget():
    r = _make_registry([
        {"id": "unknown-cost", "provider": "x", "tier": "fast",
         "intelligence": 10, "cost_input_1m": None, "enabled": True},
        {"id": "ok", "provider": "x", "tier": "fast",
         "intelligence": 6, "cost_input_1m": 0.2, "enabled": True},
    ])

    assert r.select_model("x", tier="fast", max_cost=1.0) == "ok"
```

```python
def test_add_or_update_many_normalizes_none_cost(monkeypatch):
    r = _make_registry([])
    monkeypatch.setattr(r, "save_registry", lambda: None)

    assert r.add_or_update_many([
        {"id": "m", "provider": "x", "tier": "fast",
         "intelligence": 5, "cost_input_1m": None, "cost_output_1m": None}
    ]) == 1

    saved = r.data["models"][0]
    assert saved["cost_input_1m"] == 999.0
    assert saved["cost_output_1m"] == 999.0
```

- [ ] **步骤 3：运行红灯测试**

运行：

```bash
python -B -m pytest tests/test_model_router.py::TestPriorityScore tests/test_model_registry.py::TestSelectModel -q
```

预期：新增测试失败，失败原因分别是 `None` 成本参与比较或除法、override 保留了 `None`、registry 写入未归一化。

### 任务 2：实现成本归一化边界

**文件：**
- 修改：`clients/model_registry.py`

- [ ] **步骤 1：新增成本归一化 helper**

在模块顶部增加：

```python
import math
```

并新增：

```python
UNKNOWN_MODEL_COST = 999.0


def model_cost_value(value: Any, default: float = UNKNOWN_MODEL_COST) -> float:
    try:
        cost = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(cost) or cost < 0:
        return float(default)
    return cost


def normalize_model_cost_fields(
    model: Dict[str, Any],
    *,
    fallback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = dict(model)
    for field in ("cost_input_1m", "cost_output_1m"):
        value = normalized.get(field)
        if value is None and fallback is not None:
            value = fallback.get(field)
        normalized[field] = model_cost_value(value)
    return normalized
```

- [ ] **步骤 2：接入优先级分数和日志**

把 `compute_priority_score()` 中的成本计算改为 `model_cost_value(model.get("cost_input_1m"))`，并让 `_log_all_models()` 的 `cost` 格式化使用同一 helper。

- [ ] **步骤 3：接入 `select_model()`**

把 `_score()`、`max_cost` 过滤、跨层免费检查、fallback 排序和 fallback warning 中的成本读取都改为 `model_cost_value(...)`。

- [ ] **步骤 4：接入 registry 写入**

在 `add_or_update_model()` 和 `add_or_update_many()` 入库前调用 `normalize_model_cost_fields()`。

### 任务 3：接入 NewAPI 候选链

**文件：**
- 修改：`clients/new_api_client.py`

- [ ] **步骤 1：导入 helper**

把导入改为：

```python
from clients.model_registry import registry, model_cost_value, normalize_model_cost_fields
```

- [ ] **步骤 2：归一化 override 合并结果**

在 `_apply_model_override()` 中：

- 无 override 时返回 `normalize_model_cost_fields(base)`。
- 有 override 时合并后返回 `normalize_model_cost_fields(merged, fallback=base)`。

- [ ] **步骤 3：修正候选过滤和排序契约**

在 `get_ordered_candidates()` 中：

- 预算过滤使用 `model_cost_value(m.get("cost_input_1m"))`。
- 将候选拆成 `qualified` 和 `fallback`，两组分别按 `registry.compute_priority_score()` 排序后返回。

- [ ] **步骤 4：修正 fallback 排序**

把 `_resolve_model()` 和 `resolve_model()` 的 fallback `sort(key=lambda m: m.get("cost_input_1m", 999))` 改为使用 `model_cost_value()`。

### 任务 4：验证

**文件：**
- 无新增

- [ ] **步骤 1：运行目标测试**

运行：

```bash
python -B -m pytest tests/test_model_router.py::TestPriorityScore tests/test_model_registry.py::TestSelectModel -q
```

预期：PASS。

- [ ] **步骤 2：运行相关模型路由测试**

运行：

```bash
python -B -m pytest tests/test_model_router.py tests/test_model_registry.py -q
```

预期：PASS。

- [ ] **步骤 3：运行 diff 检查**

运行：

```bash
git diff --check
```

预期：无输出。

- [ ] **步骤 4：检查工作区**

运行：

```bash
git status --short
```

预期：只新增或修改本任务相关文件，其他既有未提交文件保持不变。
