# 模型优先级排序设计

## 背景

`docs/todo.md` 的 E5 指出：当 `model_overrides.json` 或同步后的 registry 中出现 `cost_input_1m = null` 时，模型候选链会在成本过滤或优先级分数计算处抛出 `TypeError`，导致 `get_ordered_candidates()` 整体失败。当前实现里同类风险分散在 3 类路径：

- `NewAPIClient.get_ordered_candidates()`：用 `cost_input_1m` 做预算过滤，再用 `compute_priority_score()` 排序。
- `ModelRegistry.compute_priority_score()`：直接做 `cost / max_cost`。
- `ModelRegistry.select_model()`：旧的 tier 路由、fallback 选择和日志格式化也直接读取成本。

问题本质不是某一处 `None` 判断，而是模型元数据缺少统一的数值归一化边界。

## 目标

- `cost_input_1m` 和 `cost_output_1m` 为 `None`、缺失、非法字符串、负数、`NaN` 或无穷大时，不再让路由链崩溃。
- 优先级排序只依赖归一化后的数值：成本未知视为高成本（默认 `999.0`），因此自然排到低优先级并被预算上限过滤。
- override 合并时不允许 `null` 覆盖已知基础价格；如果基础价格也不可用，则写入未知高成本默认值。
- `get_ordered_candidates()` 明确排序契约：先返回满足 `intel_floor` 的候选，再返回低于门槛的兜底候选；每个分区内部按统一优先级分数升序排列。
- 旧入口 `select_model()` 与新入口共用同一成本归一化逻辑，避免修一处漏一处。

## 非目标

- 不调整权重配置（`router.cost_weight`、`router.intel_weight`、`router.free_bonus`、`router.unstable_penalty`）。
- 不改变熔断器策略，也不处理 E4 的 fire-and-forget 任务问题。
- 不引入结构化 capabilities；视觉能力过滤属于路线项 3，不并入本次。
- 不修改提示词组装、conversation 结构或 `creatures/nanobot/prompt.md`，本次变更不影响 prompt 标记和历史注入语义。

## 方案比较

### 方案 A：调用点逐个 `or 999`

在每个 `m.get("cost_input_1m", 999)` 后面补 `or 999` 或 `is None` 判断。

优点是改动少。缺点是容易继续遗漏，例如日志格式化、fallback 排序、override 合并和旧 tier 路由仍可能出现分叉。

### 方案 B：集中成本归一化（采用）

在 `clients/model_registry.py` 提供 `model_cost_value()` 和 `normalize_model_cost_fields()`：

- 所有排序、过滤、日志格式化使用 `model_cost_value()`。
- registry 写入和 override 合并使用 `normalize_model_cost_fields()`。
- 未知成本统一为 `999.0`，这样预算过滤、排序和日志行为一致。

优点是边界集中，测试覆盖后不容易回退。缺点是需要触及 `new_api_client.py` 和 `model_registry.py` 两个模块。

### 方案 C：用 Pydantic 模型约束 registry 数据

把模型元数据升级为 Pydantic 类型，在入口统一校验。

优点是长期类型边界更清晰。缺点是本次缺陷范围较小，贸然重塑 registry 数据结构会扩大回归面，不符合当前修复目标。

## 详细设计

### 成本归一化

新增常量：

```python
UNKNOWN_MODEL_COST = 999.0
```

新增函数：

```python
def model_cost_value(value: Any, default: float = UNKNOWN_MODEL_COST) -> float:
    ...

def normalize_model_cost_fields(
    model: dict[str, Any],
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ...
```

规则：

- `None`、缺失、无法转成浮点数、非有限数、负数 → `999.0`。
- override 中的 `None` 不覆盖基础模型价格；优先沿用 `fallback` 中的同名字段。
- 合法数字字符串可转为 `float`，提升兼容性。

### 优先级排序

`compute_priority_score()` 保持原公式，但改为使用归一化成本和归一化 intelligence：

```python
cost_norm = model_cost_value(model.get("cost_input_1m")) / max(max_cost, 1e-9)
intel_norm = numeric_intelligence / max(max_intel, 1e-9)
```

`get_ordered_candidates()` 的输出规则：

1. 先过滤 provider、disabled、unstable、avoid tags、exclude、预算上限、熔断禁用。
2. 将候选分成两组：
   - `qualified`：`intelligence >= intel_floor`
   - `fallback`：`intelligence < intel_floor`
3. 两组内部都按 `compute_priority_score()` 升序排序。
4. 返回 `qualified + fallback`。

这样可以保证主链路优先尝试满足复杂度门槛的模型，同时仍保留低门槛兜底候选。

### Registry 写入和 override 合并

- `_apply_model_override()` 在合并后调用 `normalize_model_cost_fields(merged, fallback=base)`。
- `add_or_update_model()` 和 `add_or_update_many()` 入库前归一化成本字段，阻止 `None` 持久化到 registry。
- `_log_all_models()`、fallback 选择、`select_model()` 的过滤和排序均使用 `model_cost_value()`。

## 测试策略

- `tests/test_model_router.py`
  - `compute_priority_score()` 遇到 `cost_input_1m=None` 不抛错，且未知成本比分数相同的已知高成本更低优先级。
  - `get_ordered_candidates()` 在 registry 含 `None` 成本时不崩溃，预算过滤会跳过未知高成本模型，满足 `intel_floor` 的候选优先于低门槛兜底候选。
  - override 中的 `null` 成本不会覆盖基础模型成本。
- `tests/test_model_registry.py`
  - `select_model()` 遇到 `None` 成本不崩溃，预算过滤按未知高成本处理。
  - registry 批量写入会把 `None` 成本归一化为 `999.0`。

## 验收标准

- 目标测试先红后绿。
- `python -B -m pytest tests/test_model_router.py tests/test_model_registry.py -q` 通过。
- `git diff --check` 无输出。
- 不提交 commit，等待用户明确要求。
