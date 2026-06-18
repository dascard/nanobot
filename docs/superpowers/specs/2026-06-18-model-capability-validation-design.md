# P1-8 模型能力校验设计

> 2026-06-18 · P1-8 目标是完成 `docs/todo.md` 路线项 3：把模型能力从 tags / 模型名猜测升级为结构化字段，并在请求构造前按 `image` / `tools` / `stream` 能力过滤候选或降级，避免把不兼容 payload 发给错误模型。

---

## 背景

P1-7 已完成路线项 2 的 async IO 收口，核心 LLM 请求复用共享 `aiohttp.ClientSession`，图片、贴纸和 Direct 工具的同步 IO 都有线程边界守卫。下一优先级回到模型路由本身。

当前主回复路由仍只按复杂度、成本、智能分和熔断状态排序。带图请求会在 `NanobotBridge.handle_message()` 内把 `metadata["files"]` 预处理为 `ImagePart(data_url)`，再经 KT `Message.to_dict()` 变成 OpenAI `image_url` content part；但模型候选选择只看纯文本 `raw_query`，不会要求视觉模型。直接 `NewAPIClient.chat_completion()` / `chat_completion_stream()` 也不会根据 messages、tools 或 streaming payload 推导能力需求。

本阶段只处理「模型能不能接这个请求」的问题，不重做多平台消息信封、QQ 出站渲染契约，也不重写 KT provider。单图大小、图片数量上限和 base64 出站禁用是相邻约束，本设计先保留扩展点，第一刀以候选过滤和 payload 前校验为主。

## 当前事实

### 模型记录

模型 registry 当前是普通 dict，没有强 schema。同步路径生成的主要字段包括：

- `id`
- `provider`
- `intelligence`
- `cost_input_1m`
- `cost_output_1m`
- `tier`
- `tags`
- `description`
- `reasoning`

落盘 registry 和 overrides 还可能包含 `context_window`、`enabled`、历史 `cost_input` / `cost_output` 等字段。`clients/data/model_overrides.json` 当前主要覆盖描述、tags、成本、智能分、tier 和上下文窗口，没有 `supports_*` 能力字段。

`clients/new_api_client.py` 的 `_infer_model_profile()` 会从模型名推断 tags：`vision` / `vl` / `omni` 推出 `vision`、`multimodal`，`code` / `coder` 推出 `coding`、`tool_use`。这些 tags 只是排序和展示信号，不是硬能力约束。

### 候选过滤

`NewAPIClient.get_ordered_candidates()` 当前过滤顺序是：

- 排除显式 excluded model。
- 排除 `unstable` tag。
- 排除 `enabled is False`。
- 排除 `avoid_tags`。
- 排除超出 `max_cost` 的模型。
- 排除熔断器禁用模型。
- 按 `intel_floor` 分为 qualified / fallback，再按 `ModelRegistry.compute_priority_score()` 排序。

该函数没有 `required_tags` 或 `required_capabilities` 参数。旧 `ModelRegistry.select_model(required_tags=...)` 是软过滤：只有当前 tier 内存在匹配 tag 的候选时才收窄；它不适合作为「图片必须 vision」的硬能力约束。

### 请求构造

直接 New API 路径由 `_build_payload()` 写入 `stream` 和 `tools`：

- `tools` 非空时写入 `payload["tools"]` 和 `tool_choice=auto`。
- `chat_completion_stream()` 固定构造 `stream=True` payload。
- 非流式 `chat_completion()` 当前传入 `_build_payload(..., stream=False, ...)`，参数列表里的 `stream` 不改变实际 payload。

Bridge / KT 路径更复杂：

- API 的 `req.stream` 控制 SSE 输出队列和 KT user event 的内部 `stream` 标记。
- KT controller 对真实 OpenAI provider 调用本身固定使用 streaming。
- ToolPlan schema 会进入 Prompt Runtime；真实 OpenAI `tools` 只在 KT native tool mode 的 SDK request 或直接 New API 路径出现。

因此 `supports_stream` 不能只按 `/chat?stream=true` 理解；只要真实模型请求使用 streaming，就需要能力匹配或兼容策略。

### 现有测试缺口

现有测试覆盖了：

- `get_ordered_candidates()` 的成本、智能分和 fallback 排序。
- override 的 `cost_input_1m=None` 不覆盖 base cost。
- Bridge 带图请求会通过 `asyncio.to_thread(prepare_image_parts, ...)`。
- API `files` 会进入 Bridge metadata。
- `/chat-step` 会把 tools schema 传给 `NewAPIClient`。
- `chat_completion_stream()` 会记录 `stream=True` 请求。

缺口是：

- 没有结构化 `supports_image` / `supports_tools` / `supports_stream` 归一化测试。
- 没有生产主链路 `NanobotBridge.handle_message(files=...) -> get_ordered_candidates(required_capabilities=...)` 测试。
- 没有直接 New API 根据 messages / tools / stream 推导能力需求的测试。
- 没有手动模型能力不匹配时回退自动路由的测试。
- 没有无视觉候选时禁止发送 `image_url` 的降级测试。
- `model_routing` eval runner 目前只覆盖旧 `ModelRegistry.select_model()`，不能表达 `get_ordered_candidates()` 的硬能力过滤。

## 方案选择

### 方案 A：继续复用 tags / `required_tags`

用 `vision`、`multimodal`、`tool_use` tags 表达能力，并把 `required_tags=["vision"]` 接入候选过滤。

结论：不采用。tags 仍是描述性信号，旧 `required_tags` 语义也是软过滤。把硬约束塞进 tags 会让「能不能发送 payload」和「排序偏好」混在一起，后续很难解释为什么某个模型被过滤。

### 方案 B：新增顶层 `supports_*` 字段，候选过滤做硬约束

模型记录新增顶层布尔字段：

- `supports_image`
- `supports_tools`
- `supports_stream`

模型同步、手动写入和 overrides 都通过同一归一化 helper 处理。候选排序新增 `required_capabilities`，在 per-model loop 内作为硬过滤执行。请求入口先推导所需能力，再选择候选；payload / SDK request 前再做 invariant 校验。

结论：采用。它最贴近现有扁平模型 dict，能直接服务 Bridge 和 New API 主链路，也保留 tags 作为兼容推断来源。

### 方案 C：发失败后按 400 / 422 再换模型

不提前建模能力，等上游返回错误后识别「tools not supported」或「image not supported」，再换下一个模型。

结论：不采用作为主方案。错误文本不稳定，且现有熔断器会把这类 400 记成模型失败，容易误禁可用模型。失败后重试只能作为兜底，不应成为能力路由的主要机制。

## 选定方案

采用「结构化能力字段 + 请求能力推导 + 候选硬过滤 + payload 前安全网」。

第一刀的兼容策略如下：

- `supports_image`：缺失时默认 `False`；若 tags 含 `vision` / `multimodal`，或模型名包含 `vision` / `vl` / `omni`，推断为 `True`。
- `supports_tools`：缺失时默认 `True`，显式 `False` 才排除。原因是当前生产路径已经会给大量模型发送 tools；首版不能因为旧 registry 未补字段而把工具请求过滤空。
- `supports_stream`：缺失时默认 `True`，显式 `False` 才排除。原因是 KT provider 当前固定 streaming，若缺失直接当 `False` 会让主回复路径大面积无候选。
- overrides 支持顶层字段和嵌套 `capabilities` 输入，但归一化输出统一写到顶层 `supports_*`。
- override 中 `supports_*: null` 不覆盖 base；如果 base 也没有，按上面的兼容默认和推断规则处理。

## 数据模型

### 模型记录字段

模型记录新增字段：

```json
{
  "id": "qwen/qwen-vl-plus",
  "provider": "newapi",
  "tier": "smart",
  "tags": ["general", "vision", "multimodal"],
  "supports_image": true,
  "supports_tools": true,
  "supports_stream": true
}
```

overrides 允许两种输入：

```json
{
  "qwen/qwen-vl-plus": {
    "supports_image": true,
    "supports_tools": true,
    "supports_stream": true
  },
  "legacy/text-only": {
    "capabilities": {
      "image": false,
      "tools": false,
      "stream": true
    }
  }
}
```

归一化后不要求保留嵌套 `capabilities`，避免和 `clients/classifier_client.py` 展示用 `capabilities` list 混淆。

### Helper 边界

建议在 `clients/model_registry.py` 增加集中 helper：

- `normalize_model_capability_fields(model, fallback=None)`
- `normalize_model_record(model, fallback=None)`
- `model_supports_capabilities(model, required_capabilities)`

调用点：

- `clients/new_api_client.py::_infer_model_profile()`
- `clients/new_api_client.py::_apply_model_override()`
- `clients/model_registry.py::add_or_update_model()`
- `clients/model_registry.py::add_or_update_many()`

`add_or_update_many()` 的变更检测要纳入 `context_window` 和 `supports_*`，否则能力变化写入后日志仍显示 unchanged，排查困难。

## 请求能力推导

### 通用 helper

建议新增请求能力推导 helper，位置可以放在 `clients/new_api_client.py` 或独立模块：

- `messages_have_image_url(messages)`：递归检查 OpenAI messages content 是否包含 `{"type": "image_url"}`。
- `required_capabilities_for_request(messages, tools=None, stream=False)`：返回 `{"supports_image": True}` 等字段。
- `capability_requirement_reason(...)`：用于日志和 trace meta，说明是 image、tools 还是 stream 触发过滤。

### 直接 New API 路径

`chat_completion()`：

- messages 含 `image_url` 时要求 `supports_image`。
- `tools` 非空时要求 `supports_tools`。
- 当前非流式 payload 固定 `stream=False`，不因函数签名中的 `stream` 参数要求 `supports_stream`；若后续真正允许非流式函数发 streaming payload，再同步调整。

`chat_completion_stream()`：

- 固定要求 `supports_stream`。
- messages 含 `image_url` 时要求 `supports_image`。
- `tools` 非空时要求 `supports_tools`。
- 候选不应只取第一项后失败返回；如果第一个能力匹配模型发生网络 / 429 / 5xx，后续计划可继续沿用现有流式语义。P1-8 第一刀至少要确保选中的第一个候选满足能力。

手动模型：

- 若 `manual_model` disabled，保持现有 disabled error / fallback 语义。
- 若 `manual_model` 存在但不满足 required capabilities，直接返回明确错误（直接 New API）或在 Bridge 主回复里回退自动路由。
- 若 registry 查不到 `manual_model`，第一刀按兼容策略允许继续，但必须在日志中标记「缺少模型能力元数据」。后续可改成更严格策略。

### Bridge 主回复路径

Bridge 能在候选选择前得到：

- `files` / `image_parts`：决定是否要求 `supports_image`。
- ToolPlan / `prompt_build.tool_schemas`：决定是否要求 `supports_tools`。
- KT provider 固定 streaming：决定是否要求 `supports_stream`，至少排除显式 `supports_stream=False` 的模型。

主回复路由调用：

```python
candidates = route_client.get_ordered_candidates(
    provider=_route_registry_provider,
    intel_floor=reply_intel_floor,
    max_cost=REPLY_MODEL_MAX_COST,
    required_capabilities={
        "supports_image": has_image,
        "supports_tools": has_tool_schemas,
        "supports_stream": True,
    },
)
```

`required_capabilities` 中值为 false 的字段可以在 helper 内删除，避免日志噪声。

手动回复模型：

- 配置了 `model.reply` 或 `LLM_MODEL_REPLY` 时，先查 registry。
- 模型 disabled：保持现有逻辑，回退自动路由。
- 模型能力不满足：记录 warning，回退自动路由，不直接构造伪 candidate。
- registry 查不到模型：按兼容策略允许手动模型继续，但记录 warning；如果请求带图，建议回退自动路由，因为未知模型不能证明支持 `image_url`。

## 降级策略

### 图片请求

首选策略是换到支持 `supports_image` 的候选。若没有任何视觉候选：

- 不把 `ImagePart` / `image_url` 发送给纯文本模型。
- 把 event content 降级为纯文本，并附加明确说明：当前没有可用视觉模型，图片内容未被读取。
- 记录 trace / 日志字段，标记 `image_downgraded_no_vision_candidate=true`。

降级文本只用于让 bot 回应「当前没有可用视觉模型，图片内容未被读取」或处理用户文本部分，不假装理解图片。若请求是纯图片且没有文本，最终回复应明确说明暂时无法处理图片。

### tools 请求

第一刀不静默剥离主回复路径的 tools。原因是 reply contract、工具调用和 Prompt Runtime 约定依赖工具 schema。若没有支持 tools 的候选：

- 直接 New API 可返回明确错误。
- Bridge 主回复可回退到无工具文本模式的策略留作后续；本阶段先选择能力匹配候选，避免 silent stripping 改变行为。

如果业务明确传入可选 tools，后续可引入 `tools_required=False` 的请求级标记。

### stream 请求

直接 `chat_completion_stream()` 没有支持 stream 的候选时，返回明确错误事件，而不是把 streaming payload 发给不支持模型。

Bridge / KT 路径因 provider 固定 streaming，候选过滤至少要排除显式 `supports_stream=False`。如果所有候选都显式不支持 stream，返回内部错误或降级到后续非 streaming provider 路径；P1-8 第一刀先不重写 KT provider，因此不能声称已经支持完整非 streaming 降级。

## 实现边界

### 第一阶段：设计与计划

- 写入本设计文档。
- 写入 `.Codex/plans/model-capability-validation.md`。
- 每个文档阶段独立验证、独立提交。

### 第二阶段：能力归一化

目标文件：

- `clients/model_registry.py`
- `clients/new_api_client.py`
- `clients/data/model_overrides.json`（如需补显式能力样例）
- `tests/test_model_registry.py`
- `tests/test_model_router.py`

预期行为：

- 模型同步输出带 `supports_*`。
- overrides 顶层和嵌套 `capabilities` 都能生效。
- override `null` 不覆盖 base。
- registry 更新检测能识别能力字段变化。

### 第三阶段：候选硬过滤

目标文件：

- `clients/new_api_client.py`
- `clients/model_registry.py`
- `tests/test_model_router.py`

预期行为：

- `get_ordered_candidates(..., required_capabilities=...)` 在模型进入 candidates 前做硬过滤。
- 能力过滤不改变 `intel_floor` 的 qualified / fallback 分区语义。
- 没有能力匹配候选时返回空列表，并在日志或诊断字段里能看到 required capabilities。

### 第四阶段：直接 New API 防绕过

目标文件：

- `clients/new_api_client.py`
- `tests/test_llm_request_tracing.py`
- `tests/test_final_tools.py`

预期行为：

- `chat_completion()` 根据 image / tools 传 `required_capabilities`。
- `chat_completion_stream()` 根据 image / tools / stream 传 `required_capabilities`。
- `_build_payload()` 或调用前 guard 不允许把明显不匹配的 tools / image / stream payload 发给已知不支持的模型。

### 第五阶段：Bridge 主回复接入

目标文件：

- `nanobot_kt/bridge.py`
- `tests/test_kt_framework.py`
- `tests/test_streaming_bridge.py`

预期行为：

- 带 `metadata["files"]` 的请求必须传 `supports_image` 能力需求。
- 有 ToolPlan schema 的主回复请求必须传 `supports_tools` 能力需求。
- KT 固定 streaming 请求必须传 `supports_stream` 能力需求或至少排除显式 false。
- 手动回复模型能力不满足时回退自动路由。
- 无视觉候选时不发送 `image_url`。

### 第六阶段：eval 与文档收口

目标文件：

- `evals/runners/model_routing_runner.py`
- `evals/cases/regression/regression_model_routing_vision_required_001.json`
- `docs/todo.md`
- `docs/plan_walkthrough.md`

预期行为：

- `model_routing` eval 能表达 `has_image` / `required_capabilities`。
- 带图请求必须选择 vision 候选。
- 不新增 `evals/cases/model_routing/` 目录，避免改变现有 regression 目录下 model_routing case 的发现路径；若后续迁移目录，需要一次性迁移现有两条 case。

## 验收标准

- [ ] 模型 registry 记录有顶层 `supports_image`、`supports_tools`、`supports_stream`。
- [ ] overrides 顶层能力字段和嵌套 `capabilities` 输入都能归一到顶层 `supports_*`。
- [ ] override 中 `supports_*: null` 不会抹掉 base 能力。
- [ ] `get_ordered_candidates(required_capabilities=...)` 是硬过滤，不复用旧 `required_tags` 软过滤。
- [ ] 带图 Bridge 主回复请求会要求 `supports_image` 候选。
- [ ] 直接 `chat_completion()` 的 image / tools 请求会要求对应能力。
- [ ] 直接 `chat_completion_stream()` 会要求 `supports_stream`，并同时处理 image / tools。
- [ ] 手动回复模型能力不匹配时不会绕过过滤。
- [ ] 无视觉候选时不会把 `image_url` 发给纯文本模型。
- [ ] `model_routing` eval 覆盖带图请求必须选 vision 模型。
- [ ] 文档同步 `docs/todo.md`、`docs/plan_walkthrough.md` 和相关 Prompt Runtime 描述。

## 测试计划

优先红灯测试：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_model_registry.py -k "capability" -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_model_router.py -k "capability or ordered_candidates" -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_kt_framework.py -k "files and candidates" -q -p no:cacheprovider
```

实现后定向回归：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_model_registry.py tests/test_model_router.py tests/test_llm_request_tracing.py tests/test_final_tools.py tests/test_kt_framework.py tests/test_streaming_bridge.py -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest evals -q -p no:cacheprovider
```

最终验证：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

## 风险与控制

- **旧 registry 能力缺失导致无候选。** 控制：`supports_tools` 和 `supports_stream` 首版缺失默认 true，只硬排除显式 false；`supports_image` 缺失默认 false 并从 vision tags / 模型名推断。
- **静默剥离 tools 改变回复合同。** 控制：第一刀不默认剥 tools，优先换能力匹配模型；可选工具剥离留给后续请求级标记。
- **图片降级假装读图。** 控制：无视觉候选时只发文本说明，不发送 `image_url`，不让模型推测图片内容。
- **手动模型绕过过滤。** 控制：手动模型同样校验能力；Bridge 主回复可回退自动路由，直接 New API 返回明确错误。
- **stream 语义混乱。** 控制：区分 API SSE stream、KT event stream 和真实 provider streaming；能力校验以真实 provider payload 为准。
- **eval 目录迁移漏 case。** 控制：首版新增 regression case，不新增 `evals/cases/model_routing/` 目录。

## 后续

P1-8 完成后，P1 收敛去债主线的「提示词唯一运行时、连接池 / 同步 IO、请求能力校验」三项地基完成。下一优先级进入 P2 platform 维度底座：工具配置 platform scope、标准化请求 / 响应信封、QQ 出站渲染契约和 Prompt platform × chat_type 适配。
