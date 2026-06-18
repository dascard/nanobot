# P1-8 模型能力校验实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为模型 registry 增加结构化能力字段，并在请求构造前按 `supports_image` / `supports_tools` / `supports_stream` 过滤或降级，避免把不兼容 payload 发给错误模型。

**架构：** 模型能力统一归一到模型记录顶层 `supports_*` 字段，候选排序通过 `required_capabilities` 做硬过滤。请求入口先从 messages、tools 和真实 streaming payload 推导能力需求；Bridge 主回复和直接 New API 路径共享同一套能力判断，payload / SDK request 前再做防绕过 guard。

**技术栈：** Python 3.12/3.13、pytest、aiohttp、FastAPI、KohakuTerrarium OpenAI provider、in-memory SQLite。

---

## 文件结构

- 修改：`clients/model_registry.py`
  - 新增模型能力归一化 helper。
  - 合并成本归一化和能力归一化入口。
  - `add_or_update_model()` / `add_or_update_many()` 写入前归一化 `supports_*`。
  - registry 更新检测纳入 `context_window` 和 `supports_*`。
- 修改：`clients/new_api_client.py`
  - `_infer_model_profile()` 输出基础 `supports_*`。
  - `_apply_model_override()` 支持顶层 `supports_*` 和嵌套 `capabilities`，并防止 `null` 覆盖 base。
  - 新增 messages / tools / stream 能力需求推导。
  - `get_ordered_candidates()` 支持 `required_capabilities` 硬过滤。
  - `chat_completion()` 和 `chat_completion_stream()` 传入能力需求。
- 修改：`nanobot_kt/bridge.py`
  - 主回复路由根据 `files` / ToolPlan / KT streaming 传 `required_capabilities`。
  - 手动回复模型能力不匹配时回退自动路由。
  - 无视觉候选时不发送 `ImagePart` / `image_url`。
- 修改：`evals/runners/model_routing_runner.py`
  - 支持 case 中的 `required_capabilities` / `has_image`。
- 创建：`evals/cases/regression/regression_model_routing_vision_required_001.json`
  - 覆盖带图请求必须选 vision 候选。
- 修改：`tests/test_model_registry.py`
  - 覆盖能力默认值、推断、override `null` fallback、能力变化检测。
- 修改：`tests/test_model_router.py`
  - 覆盖 `required_capabilities` 硬过滤和排序分区不变。
- 修改：`tests/test_llm_request_tracing.py`
  - 覆盖直接 `chat_completion()` / `chat_completion_stream()` 传能力需求。
- 修改：`tests/test_final_tools.py`
  - 覆盖 payload guard 不向已知不支持 tools 的模型发送 tools。
- 修改：`tests/test_kt_framework.py`
  - 覆盖 Bridge 带图请求要求 vision 候选、手动模型能力不匹配回退、无视觉候选降级。
- 修改：`tests/test_streaming_bridge.py`
  - 覆盖 Bridge 不选择显式 `supports_stream=False` 的候选。
- 修改：`docs/todo.md`
  - P1-8 完成后同步路线项 3 状态。
- 修改：`docs/plan_walkthrough.md`
  - 同步 P1-8 任务、验证和提交记录。

## 当前事实

- 设计文档：`docs/superpowers/specs/2026-06-18-model-capability-validation-design.md`，提交 `ded7213 docs(模型能力): 设计请求能力校验`。
- P1-7 已完成，路线项 2 全量验证记录为 `1222 passed, 6 skipped, 113 warnings`。
- `NewAPIClient.get_ordered_candidates()` 当前没有 `required_capabilities`。
- `ModelRegistry.select_model(required_tags=...)` 是软过滤，不承载本阶段硬能力约束。
- Bridge 带图请求已能在模型路由前知道 `files` / `image_parts`，但没有把 `has_image` 传给候选过滤。
- KT provider 对真实模型请求固定 streaming，因此 Bridge 主回复至少要排除显式 `supports_stream=False` 的候选。

## 任务 1：模型能力归一化红灯测试

**文件：**
- 修改：`tests/test_model_registry.py`
- 修改：`tests/test_model_router.py`

- [ ] **步骤 1：编写 registry 能力归一化失败测试**

在 `tests/test_model_registry.py` 中新增测试，锁定默认值和推断规则：

```python
def test_add_or_update_many_normalizes_capability_defaults(tmp_path, monkeypatch):
    from clients.model_registry import ModelRegistry

    reg = ModelRegistry()
    reg.data = {"models": [], "last_updated": "never"}
    reg.save_registry = lambda: None

    reg.add_or_update_many([
        {
            "id": "qwen/qwen-vl-plus",
            "provider": "newapi",
            "tier": "smart",
            "intelligence": 7,
            "cost_input_1m": None,
            "cost_output_1m": None,
            "tags": ["vision", "multimodal"],
        },
        {
            "id": "legacy/text-only",
            "provider": "newapi",
            "tier": "fast",
            "intelligence": 5,
            "cost_input_1m": 0.1,
            "cost_output_1m": 0.2,
            "tags": ["general"],
        },
    ])

    vision = reg.get_model_info("qwen/qwen-vl-plus")
    text = reg.get_model_info("legacy/text-only")
    assert vision["supports_image"] is True
    assert vision["supports_tools"] is True
    assert vision["supports_stream"] is True
    assert text["supports_image"] is False
    assert text["supports_tools"] is True
    assert text["supports_stream"] is True
```

- [ ] **步骤 2：编写 override `null` fallback 失败测试**

在 `tests/test_model_router.py` 中新增测试，覆盖 override 不抹掉 base：

```python
def test_model_override_null_capability_keeps_base_capability(monkeypatch):
    from clients.new_api_client import NewAPIClient

    client = NewAPIClient(api_key="k", base_url="http://example.test")
    monkeypatch.setattr(
        NewAPIClient,
        "_load_model_overrides",
        classmethod(lambda cls: {"vision-model": {"supports_image": None}}),
    )

    result = client._apply_model_override(
        "vision-model",
        {
            "id": "vision-model",
            "provider": "newapi",
            "tags": ["vision"],
            "supports_image": True,
            "supports_tools": True,
            "supports_stream": True,
            "cost_input_1m": 0.0,
            "cost_output_1m": 0.0,
        },
    )
    assert result["supports_image"] is True
```

- [ ] **步骤 3：运行红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_model_registry.py::test_add_or_update_many_normalizes_capability_defaults tests/test_model_router.py::test_model_override_null_capability_keeps_base_capability -q -p no:cacheprovider
```

预期：FAIL。当前代码没有 `supports_*` 归一化，测试会在字段缺失或 `None` 覆盖处失败。

- [ ] **步骤 4：提交红灯测试**

```bash
git add tests/test_model_registry.py tests/test_model_router.py
git commit -m "test(模型能力): 覆盖能力归一化红灯"
```

## 任务 2：实现模型能力归一化

**文件：**
- 修改：`clients/model_registry.py`
- 修改：`clients/new_api_client.py`
- 测试：`tests/test_model_registry.py`
- 测试：`tests/test_model_router.py`

- [ ] **步骤 1：在 registry 中新增能力 helper**

在 `clients/model_registry.py` 的成本归一化 helper 附近新增：

```python
CAPABILITY_FIELDS = ("supports_image", "supports_tools", "supports_stream")


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return None


def _model_tags(model: Dict[str, Any]) -> list[str]:
    tags = model.get("tags") or []
    if not isinstance(tags, list):
        return []
    return [str(t).lower() for t in tags]


def _infer_supports_image(model: Dict[str, Any]) -> bool:
    tags = set(_model_tags(model))
    mid = str(model.get("id") or "").lower()
    return bool({"vision", "multimodal"} & tags) or any(
        marker in mid for marker in ("vision", "vl", "omni")
    )
```

- [ ] **步骤 2：新增归一化函数**

继续在 `clients/model_registry.py` 中新增：

```python
def normalize_model_capability_fields(
    model: Dict[str, Any],
    *,
    fallback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = dict(model)
    nested = normalized.get("capabilities")
    nested_map = nested if isinstance(nested, dict) else {}

    for field in CAPABILITY_FIELDS:
        short = field.removeprefix("supports_")
        raw = normalized.get(field)
        if raw is None:
            raw = nested_map.get(short)
        if raw is None and fallback is not None:
            raw = fallback.get(field)

        value = _coerce_optional_bool(raw)
        if value is None:
            if field == "supports_image":
                value = _infer_supports_image(normalized)
            else:
                value = True
        normalized[field] = value

    return normalized


def normalize_model_record(
    model: Dict[str, Any],
    *,
    fallback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = normalize_model_cost_fields(model, fallback=fallback)
    return normalize_model_capability_fields(normalized, fallback=fallback)
```

- [ ] **步骤 3：替换写入归一化调用点**

在 `clients/model_registry.py` 中把写入前的 `normalize_model_cost_fields(...)` 改成 `normalize_model_record(...)`：

```python
def add_or_update_model(self, model_data: Dict[str, Any]):
    model_data = normalize_model_record(model_data)
```

在 `add_or_update_many()` 中：

```python
for raw_model in models:
    old = models_list[index[model_id]] if model_id in index else None
    m = normalize_model_record(raw_model, fallback=old)
```

实际实现时先取 `model_id`，再读取 `old`，避免在 `model_id` 未验证前查 index。

- [ ] **步骤 4：扩展变更检测**

在 `add_or_update_many()` 的 `changed` 判断中纳入：

```python
old.get("context_window") != m.get("context_window") or
old.get("supports_image") != m.get("supports_image") or
old.get("supports_tools") != m.get("supports_tools") or
old.get("supports_stream") != m.get("supports_stream")
```

- [ ] **步骤 5：让 NewAPIClient 使用统一归一化**

在 `clients/new_api_client.py` 导入并使用 `normalize_model_record`：

```python
from clients.model_registry import normalize_model_record
```

把 `_apply_model_override()` 里的基础归一化和返回归一化改为：

```python
base = normalize_model_record(base)
...
return normalize_model_record(merged, fallback=base)
```

在 `_infer_model_profile()` 返回 dict 时加入基础能力：

```python
"supports_image": "vision" in tags_list or "multimodal" in tags_list,
"supports_tools": True,
"supports_stream": True,
```

- [ ] **步骤 6：运行绿灯和相关回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_model_registry.py tests/test_model_router.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 7：提交任务 2**

```bash
git add clients/model_registry.py clients/new_api_client.py tests/test_model_registry.py tests/test_model_router.py
git commit -m "feat(模型能力): 归一化模型能力字段"
```

## 任务 3：候选排序支持 `required_capabilities`

**文件：**
- 修改：`clients/model_registry.py`
- 修改：`clients/new_api_client.py`
- 测试：`tests/test_model_router.py`

- [ ] **步骤 1：编写候选硬过滤红灯测试**

在 `tests/test_model_router.py` 中新增：

```python
def test_ordered_candidates_filters_required_capabilities_before_intel_fallback(monkeypatch):
    from clients.new_api_client import NewAPIClient
    from clients.model_registry import registry

    registry.data = {
        "models": [
            {
                "id": "smart-text",
                "provider": "newapi",
                "tier": "smart",
                "intelligence": 9,
                "cost_input_1m": 0.01,
                "tags": ["general"],
                "supports_tools": False,
                "supports_stream": True,
                "supports_image": False,
                "enabled": True,
            },
            {
                "id": "fast-tool",
                "provider": "newapi",
                "tier": "fast",
                "intelligence": 4,
                "cost_input_1m": 0.02,
                "tags": ["tool_use"],
                "supports_tools": True,
                "supports_stream": True,
                "supports_image": False,
                "enabled": True,
            },
        ]
    }

    client = NewAPIClient(api_key="k", base_url="http://example.test")
    candidates = client.get_ordered_candidates(
        provider="newapi",
        intel_floor=8,
        required_capabilities={"supports_tools": True},
    )
    assert [m["id"] for m in candidates] == ["fast-tool"]
```

- [ ] **步骤 2：运行红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_model_router.py::test_ordered_candidates_filters_required_capabilities_before_intel_fallback -q -p no:cacheprovider
```

预期：FAIL。当前 `get_ordered_candidates()` 不接受 `required_capabilities`。

- [ ] **步骤 3：实现能力匹配 helper**

在 `clients/model_registry.py` 中新增：

```python
def model_supports_capabilities(
    model: Dict[str, Any],
    required_capabilities: Optional[Dict[str, bool]] = None,
) -> bool:
    if not required_capabilities:
        return True
    normalized = normalize_model_capability_fields(model)
    for field, required in required_capabilities.items():
        if not required:
            continue
        if normalized.get(field) is not True:
            return False
    return True
```

- [ ] **步骤 4：扩展候选接口**

在 `clients/new_api_client.py::get_ordered_candidates()` 增加参数：

```python
required_capabilities: Optional[Dict[str, bool]] = None,
```

在 per-model loop 中 `enabled` / `avoid_tags` 之后、成本过滤之前加入：

```python
if not model_supports_capabilities(m, required_capabilities):
    continue
```

同时在日志中记录 required capabilities：

```python
required_capabilities = {
    key: value for key, value in (required_capabilities or {}).items() if value
}
```

- [ ] **步骤 5：运行绿灯和排序回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_model_router.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 6：提交任务 3**

```bash
git add clients/model_registry.py clients/new_api_client.py tests/test_model_router.py
git commit -m "feat(模型路由): 按能力过滤候选"
```

## 任务 4：直接 New API 请求推导能力需求

**文件：**
- 修改：`clients/new_api_client.py`
- 测试：`tests/test_llm_request_tracing.py`
- 测试：`tests/test_final_tools.py`

- [ ] **步骤 1：编写 `chat_completion()` tools 能力红灯测试**

在 `tests/test_llm_request_tracing.py` 中新增测试，mock `get_ordered_candidates` 捕获 kwargs：

```python
@pytest.mark.asyncio
async def test_chat_completion_with_tools_requests_tool_capable_candidates(monkeypatch):
    from clients.new_api_client import NewAPIClient

    captured = {}

    def fake_candidates(self, **kwargs):
        captured.update(kwargs)
        return [{"id": "tool-model", "supports_tools": True, "cost_input_1m": 0.0, "intelligence": 7}]

    monkeypatch.setattr(NewAPIClient, "sync_models_to_registry", AsyncMock())
    monkeypatch.setattr(NewAPIClient, "get_ordered_candidates", fake_candidates)
    monkeypatch.setattr(NewAPIClient, "_request_session", fake_success_session_context)

    client = NewAPIClient(api_key="k", base_url="http://example.test")
    await client.chat_completion(
        [{"role": "user", "content": "查一下"}],
        tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
    )
    assert captured["required_capabilities"]["supports_tools"] is True
```

`fake_success_session_context` 按现有测试中的 fake session 写法复用，不引入真实网络。

- [ ] **步骤 2：编写 stream 能力红灯测试**

在同文件中新增：

```python
@pytest.mark.asyncio
async def test_chat_completion_stream_requests_stream_capable_candidates(monkeypatch):
    captured = {}

    def fake_candidates(self, **kwargs):
        captured.update(kwargs)
        return [{"id": "stream-model", "supports_stream": True, "cost_input_1m": 0.0, "intelligence": 7}]

    monkeypatch.setattr(NewAPIClient, "sync_models_to_registry", AsyncMock())
    monkeypatch.setattr(NewAPIClient, "get_ordered_candidates", fake_candidates)
    monkeypatch.setattr(NewAPIClient, "_request_session", fake_stream_session_context)

    client = NewAPIClient(api_key="k", base_url="http://example.test")
    chunks = [
        chunk async for chunk in client.chat_completion_stream(
            [{"role": "user", "content": "hello"}],
        )
    ]
    assert captured["required_capabilities"]["supports_stream"] is True
    assert chunks
```

- [ ] **步骤 3：运行红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_llm_request_tracing.py -k "capable_candidates or required_capabilities" -q -p no:cacheprovider
```

预期：FAIL。当前直接 New API 不传 `required_capabilities`。

- [ ] **步骤 4：实现请求能力推导 helper**

在 `clients/new_api_client.py` 中新增：

```python
def messages_have_image_url(messages: List[Dict[str, Any]]) -> bool:
    for message in messages or []:
        content = message.get("content")
        parts = content if isinstance(content, list) else []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "image_url":
                return True
    return False


def required_capabilities_for_request(
    messages: List[Dict[str, Any]],
    *,
    tools: Optional[List[Dict[str, Any]]] = None,
    stream: bool = False,
) -> Dict[str, bool]:
    required: Dict[str, bool] = {}
    if messages_have_image_url(messages):
        required["supports_image"] = True
    if tools:
        required["supports_tools"] = True
    if stream:
        required["supports_stream"] = True
    return required
```

- [ ] **步骤 5：接入候选调用**

在 `chat_completion()` 的自动候选路径中传入：

```python
required_capabilities = required_capabilities_for_request(messages, tools=tools, stream=False)
candidates = self.get_ordered_candidates(
    provider=self.registry_provider,
    intel_floor=intel_floor,
    required_capabilities=required_capabilities,
)
```

在 `chat_completion_stream()` 中传入 `stream=True`。

- [ ] **步骤 6：手动模型能力不匹配直接返回错误**

在直接 New API 手动模型路径查到 registry info 时加入：

```python
if info and not model_supports_capabilities(info, required_capabilities):
    return {"error": f"Model lacks required capabilities: {manual_model}"}
```

stream 版本使用：

```python
yield {"error": f"Model lacks required capabilities: {manual_model}"}
return
```

- [ ] **步骤 7：运行绿灯和相关回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_llm_request_tracing.py tests/test_final_tools.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 8：提交任务 4**

```bash
git add clients/new_api_client.py tests/test_llm_request_tracing.py tests/test_final_tools.py
git commit -m "feat(模型能力): 推导直接请求能力需求"
```

## 任务 5：Bridge 主回复路由接入能力校验

**文件：**
- 修改：`nanobot_kt/bridge.py`
- 测试：`tests/test_kt_framework.py`
- 测试：`tests/test_streaming_bridge.py`

- [ ] **步骤 1：编写带图候选能力红灯测试**

在 `tests/test_kt_framework.py` 的多模态测试附近新增：

```python
@pytest.mark.asyncio
async def test_handle_message_with_files_requests_vision_candidates(monkeypatch):
    captured = {}

    def fake_get_ordered_candidates(self, **kwargs):
        captured.update(kwargs)
        return [{
            "id": "vision-model",
            "supports_image": True,
            "supports_tools": True,
            "supports_stream": True,
            "intelligence": 7,
            "cost_input_1m": 0.0,
            "context_window": 128000,
        }]

    monkeypatch.setattr("clients.new_api_client.NewAPIClient.get_ordered_candidates", fake_get_ordered_candidates)
    # 复用现有 bridge fake agent / fake process_event 构造。
    # 调用 handle_message(..., metadata={"files": [{"url": "http://example/img.jpg"}], "stream": False})
    assert captured["required_capabilities"]["supports_image"] is True
```

- [ ] **步骤 2：编写 stream 显式 false 排除测试**

在 `tests/test_streaming_bridge.py` 中新增测试，registry candidates 包含 `supports_stream=False` 和 `supports_stream=True`，断言最终进入 FakeLLM 的模型不是显式 false 的那个。

```python
assert selected_model == "stream-capable-model"
```

- [ ] **步骤 3：运行红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_kt_framework.py -k "vision_candidates" -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_streaming_bridge.py -k "stream_capability" -q -p no:cacheprovider
```

预期：FAIL。当前 Bridge 不传 `required_capabilities`。

- [ ] **步骤 4：实现 Bridge 能力需求构造**

在 `nanobot_kt/bridge.py` 图片 event content 构造后、调用 `get_ordered_candidates()` 前计算：

```python
has_image = bool(files)
has_tool_schemas = bool(getattr(prompt_build, "tool_schemas", None))
required_capabilities = {
    "supports_image": has_image,
    "supports_tools": has_tool_schemas,
    "supports_stream": True,
}
required_capabilities = {k: v for k, v in required_capabilities.items() if v}
```

若 `prompt_build` 实际字段名不是 `tool_schemas`，以当前 `PromptRuntimeInput` 中传给 Prompt Runtime 的 schema 字段为准。

- [ ] **步骤 5：自动路由传能力需求**

在 Bridge 调用中加入：

```python
candidates = route_client.get_ordered_candidates(
    provider=_route_registry_provider,
    intel_floor=reply_intel_floor,
    max_cost=REPLY_MODEL_MAX_COST,
    required_capabilities=required_capabilities,
)
```

- [ ] **步骤 6：手动回复模型也校验能力**

在 `manual_reply_model` disabled 检查后加入：

```python
if manual_reply_model and info and not model_supports_capabilities(info, required_capabilities):
    logger.warning(
        "[ReplyModel] configured model lacks capabilities: %s required=%s, falling back to auto",
        manual_reply_model,
        required_capabilities,
    )
    manual_reply_model = ""
```

如果 `info is None` 且 `has_image` 为 true，也回退自动路由并记录 warning。

- [ ] **步骤 7：运行绿灯和 Bridge 回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_kt_framework.py tests/test_streaming_bridge.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 8：提交任务 5**

```bash
git add nanobot_kt/bridge.py tests/test_kt_framework.py tests/test_streaming_bridge.py
git commit -m "feat(桥接): 接入回复模型能力校验"
```

## 任务 6：无视觉候选降级和 payload 前 guard

**文件：**
- 修改：`clients/new_api_client.py`
- 修改：`nanobot_kt/bridge.py`
- 测试：`tests/test_kt_framework.py`
- 测试：`tests/test_final_tools.py`

- [ ] **步骤 1：编写无视觉候选降级红灯测试**

在 `tests/test_kt_framework.py` 新增：

```python
@pytest.mark.asyncio
async def test_handle_message_with_files_does_not_send_image_url_without_vision_candidate(monkeypatch):
    sent_events = []

    def fake_get_ordered_candidates(self, **kwargs):
        return []

    async def fake_process_event(agent, event):
        sent_events.append(event)
        return None

    # 构造带 files 请求，mock prepare_image_parts 返回 ImagePart。
    # 调用 handle_message。
    assert sent_events
    assert "image_url" not in str(sent_events[-1].content)
```

- [ ] **步骤 2：编写 tools guard 红灯测试**

在 `tests/test_final_tools.py` 新增针对 `_build_payload()` 的 guard 测试：

```python
def test_new_api_payload_rejects_tools_when_model_lacks_support():
    client = NewAPIClient(api_key="k", base_url="http://example.test")
    with pytest.raises(ValueError, match="supports_tools"):
        client._build_payload(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
            temperature=0.7,
            stream=False,
            model="text-only",
            model_info={"supports_tools": False},
        )
```

如果不想扩展 `_build_payload()` 参数，可把 guard 放在调用前，并把测试改为覆盖公开方法返回明确错误。

- [ ] **步骤 3：运行红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_kt_framework.py -k "without_vision_candidate" -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_final_tools.py -k "lacks_support" -q -p no:cacheprovider
```

预期：FAIL。当前 fallback 会构造普通候选，payload guard 也不存在。

- [ ] **步骤 4：实现 Bridge 图片降级**

在 Bridge 候选为空且 `has_image` 为 true 时，不使用普通 fallback 模型发送 `event_content`。构造纯文本降级内容：

```python
if has_image and not candidates:
    degraded_content = (
        f"{prompt_build.event_content}\n\n"
        "[系统提示：当前没有可用视觉模型，图片内容未被读取。请不要推测图片内容。]"
    )
    event_content = degraded_content
    required_capabilities.pop("supports_image", None)
    candidates = route_client.get_ordered_candidates(
        provider=_route_registry_provider,
        intel_floor=reply_intel_floor,
        max_cost=REPLY_MODEL_MAX_COST,
        required_capabilities=required_capabilities,
    )
```

如果降级后仍无候选，再走现有 fallback，但 fallback event content 必须是纯文本。

- [ ] **步骤 5：实现 payload 前 guard**

在 `clients/new_api_client.py` 增加 model info 参数或调用前检查。推荐新增 helper：

```python
def validate_payload_capabilities(
    *,
    model_info: Optional[Dict[str, Any]],
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    stream: bool,
) -> None:
    if not model_info:
        return
    required = required_capabilities_for_request(messages, tools=tools, stream=stream)
    if not model_supports_capabilities(model_info, required):
        raise ValueError(f"model lacks required capabilities: {required}")
```

在 `_build_payload()` 前调用，或把 `model_info` 传进 `_build_payload()` 后校验。

- [ ] **步骤 6：运行绿灯和相关回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_kt_framework.py tests/test_final_tools.py tests/test_llm_request_tracing.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 7：提交任务 6**

```bash
git add clients/new_api_client.py nanobot_kt/bridge.py tests/test_kt_framework.py tests/test_final_tools.py tests/test_llm_request_tracing.py
git commit -m "fix(模型能力): 防止发送不兼容请求"
```

## 任务 7：扩展 model_routing eval

**文件：**
- 修改：`evals/runners/model_routing_runner.py`
- 创建：`evals/cases/regression/regression_model_routing_vision_required_001.json`
- 测试：`tests/test_eval_baseline.py` 或新增 eval runner 测试

- [ ] **步骤 1：新增 regression case**

创建 `evals/cases/regression/regression_model_routing_vision_required_001.json`：

```json
{
  "id": "regression_model_routing_vision_required_001",
  "suite": "model_routing",
  "input": {
    "provider": "newapi",
    "tier": "smart",
    "has_image": true,
    "models": [
      {
        "id": "text-cheap",
        "provider": "newapi",
        "tier": "smart",
        "intelligence": 9,
        "cost_input_1m": 0.001,
        "tags": ["general"],
        "supports_image": false,
        "supports_tools": true,
        "supports_stream": true,
        "enabled": true
      },
      {
        "id": "vision-model",
        "provider": "newapi",
        "tier": "smart",
        "intelligence": 7,
        "cost_input_1m": 0.02,
        "tags": ["vision", "multimodal"],
        "supports_image": true,
        "supports_tools": true,
        "supports_stream": true,
        "enabled": true
      }
    ]
  },
  "expected": {
    "model_used": "vision-model",
    "must_not_use": ["text-cheap"]
  }
}
```

- [ ] **步骤 2：运行红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_baseline.py -q -p no:cacheprovider
python -m evals.run --suite model_routing
```

预期：model_routing case 失败或 runner 无法消费 `has_image`。

- [ ] **步骤 3：扩展 runner**

在 `evals/runners/model_routing_runner.py` 中：

```python
required_capabilities = {}
if case.input.get("has_image"):
    required_capabilities["supports_image"] = True
required_capabilities.update(case.input.get("required_capabilities") or {})
```

优先使用 `NewAPIClient.get_ordered_candidates()` 或 registry helper，而不是旧 `select_model(required_tags=...)`。

- [ ] **步骤 4：运行绿灯**

运行：

```bash
python -m evals.run --suite model_routing
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_baseline.py -q -p no:cacheprovider
```

预期：PASS，且新 case 使用 `vision-model`。

- [ ] **步骤 5：提交任务 7**

```bash
git add evals/runners/model_routing_runner.py evals/cases/regression/regression_model_routing_vision_required_001.json tests/test_eval_baseline.py
git commit -m "test(评测): 覆盖视觉模型路由"
```

只暂存实际修改过的文件。

## 任务 8：文档收口与全量验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`docs/superpowers/specs/2026-06-18-model-capability-validation-design.md`（若实现中策略变化）

- [ ] **步骤 1：同步 `docs/todo.md` 路线项 3**

把路线项 3 的现状改为已落地，记录：

- 模型记录已有 `supports_*`。
- 主回复和直接 New API 均按 `required_capabilities` 过滤。
- 无视觉候选不会发送 `image_url`。
- `model_routing` eval 已覆盖带图请求。

- [ ] **步骤 2：同步 `docs/plan_walkthrough.md`**

在 P1-8 当前计划中标记已完成任务和提交号，下一优先级切到 P2 platform 维度底座。

- [ ] **步骤 3：检查 Prompt Runtime 文档口径**

运行：

```bash
rg -n "image_url|图片|视觉|stream|tools|capabilit|模型能力" data/prompts_v2 creatures/nanobot docs -g "*.md"
```

若 Prompt Runtime 文档描述了图片或工具发送行为，确认它没有声称纯文本模型能读取图片，也没有过时地描述旧 prompt 单文件路径。

- [ ] **步骤 4：运行最终验证**

运行：

```bash
rg -n "待[定]|后续实[现]|类似任[务]|添加适[当]|为上[述]" docs/plan_walkthrough.md docs/superpowers/specs/2026-06-18-model-capability-validation-design.md .Codex/plans/model-capability-validation.md
LC_ALL=C rg -n $'\xef\xbf\xbd' docs/plan_walkthrough.md docs/superpowers/specs/2026-06-18-model-capability-validation-design.md .Codex/plans/model-capability-validation.md
git diff --check -- docs/todo.md docs/plan_walkthrough.md docs/superpowers/specs/2026-06-18-model-capability-validation-design.md .Codex/plans/model-capability-validation.md
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_model_registry.py tests/test_model_router.py tests/test_llm_request_tracing.py tests/test_final_tools.py tests/test_kt_framework.py tests/test_streaming_bridge.py -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
python -m evals.run --suite model_routing
```

预期：

- 占位文本扫描无新问题。
- `git diff --check` 无输出。
- 定向测试通过。
- 全量测试通过。
- model_routing eval 通过。

- [ ] **步骤 5：提交任务 8**

```bash
git add docs/todo.md docs/plan_walkthrough.md docs/superpowers/specs/2026-06-18-model-capability-validation-design.md .Codex/plans/model-capability-validation.md
git commit -m "docs(计划): 同步模型能力校验状态"
```

只暂存实际修改过的文件。

## 最终验收清单

- [ ] `clients/data/models.json` 或同步后的 registry 记录能看到顶层 `supports_image`、`supports_tools`、`supports_stream`。
- [ ] `get_ordered_candidates(required_capabilities={"supports_image": True})` 不返回纯文本模型。
- [ ] Bridge 带图主回复请求传入 `supports_image` 能力需求。
- [ ] 直接 `chat_completion()` 的 tools 请求传入 `supports_tools` 能力需求。
- [ ] 直接 `chat_completion_stream()` 传入 `supports_stream` 能力需求。
- [ ] 手动回复模型能力不匹配时不会直接绕过过滤。
- [ ] 无视觉候选时不会把 `image_url` 发给纯文本模型。
- [ ] `python -m evals.run --suite model_routing` 覆盖带图路由 case 并通过。
- [ ] 全量测试 `tests/` 通过。
