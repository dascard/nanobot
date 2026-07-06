# Web Search 相关性 fallback 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 或在当前会话内按 TDD 执行。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 将 Web Search 默认路由从“第一个非空结果停止”升级为“第一个相关结果停止”，避免明显跑偏的 provider 结果直接进入模型上下文。

**架构：** 新增轻量相关性评分模块，只用 query 与搜索结果的标题、摘要、URL 做启发式判断。`search_enabled_providers()` 在自动 provider 模式下对每个 provider 结果评分，低相关则继续 fallback；显式指定 provider 时不自动换源，但仍返回质量信息给模型和 WebUI。

**技术栈：** Python、pytest、FastAPI admin preview、现有 `core.web_search` provider runtime。

---

## 文件结构

- 创建：`core/web_search/relevance.py`
  - 职责：提取 query token、识别常见跑偏结果、计算 `SearchRelevanceDecision`。
- 修改：`core/web_search/search_runtime.py`
  - 职责：`WebSearchProviderResult` 携带质量字段；自动 fallback 使用相关性门槛。
- 修改：`tests/test_web_search_relevance.py`
  - 职责：覆盖中文天气 query、明显跑偏结果、英文技术 query、空结果。
- 修改：`tests/test_admin_web_search_routes.py`
  - 职责：覆盖 preview 返回质量字段和低相关 fallback。
- 修改：`tests/test_web_search_tool.py`
  - 职责：覆盖发给模型的 `WEB_SEARCH_RESULTS` 包含质量字段。
- 修改：`creatures/nanobot/prompts/skills/web_search/tool.py`
  - 职责：同步工具参数 schema，说明自动模式按“相关结果”停止。
- 修改：`core/tool_schema_preview.py`
  - 职责：同步管理后台/工具预览使用的静态 schema。
- 修改：`prompts.v2.default/tools/web_search/usage.md`
  - 职责：同步默认 Prompt Runtime 工具说明。
- 修改：`data/prompts_v2/tools/web_search/usage.md`
  - 职责：同步当前运行时 Prompt Runtime 工具说明。

## 任务

### 任务 1：相关性评分模块

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_web_search_relevance.py`，覆盖：

```python
def test_weather_query_accepts_weather_domains():
    result = WebSearchResult(
        provider="searxng",
        title="上海天气预报",
        url="https://www.weather.com.cn/weather/101020100.shtml",
        snippet="上海今日天气和未来一周天气预报",
    )
    decision = judge_search_relevance("上海天气", [result])
    assert decision.ok is True
    assert decision.score >= 0.5
```

并添加 Proton VPN 下载页低相关用例。

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_web_search_relevance.py -q`

预期：因 `core.web_search.relevance` 不存在而失败。

- [ ] **步骤 3：实现最少代码**

创建 `core/web_search/relevance.py`，定义：

```python
@dataclass(frozen=True)
class SearchRelevanceDecision:
    ok: bool
    score: float
    reason: str
    matched_terms: list[str]
```

实现 `judge_search_relevance(query, results)`。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_web_search_relevance.py -q`

预期：全部通过。

### 任务 2：自动 fallback 使用相关性门槛

- [ ] **步骤 1：编写失败测试**

在 `tests/test_admin_web_search_routes.py` 或新的 runtime 测试中构造两个 provider：

```python
searxng -> Proton VPN 下载页
brave -> 上海天气预报
```

断言最终返回 `provider_id == "brave"`，并且 first provider 被记录为低相关。

- [ ] **步骤 2：运行测试验证失败**

运行目标测试，预期当前 runtime 返回 `searxng`，测试失败。

- [ ] **步骤 3：修改 runtime**

修改 `WebSearchProviderResult` 添加：

```python
quality: str = "unknown"
quality_score: float = 0.0
quality_reason: str = ""
attempted_providers: list[dict[str, Any]] | None = None
```

在 `search_provider()` 返回前评分；在自动模式下 `quality != "ok"` 时继续 fallback。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_admin_web_search_routes.py tests/test_web_search_relevance.py -q`

预期：全部通过。

### 任务 3：工具输出和 WebUI preview 暴露质量字段

- [ ] **步骤 1：编写失败测试**

修改 `tests/test_web_search_tool.py`，断言模型消息包含：

```text
QUALITY: ok
QUALITY_SCORE:
QUALITY_REASON:
```

修改 preview 测试，断言 JSON 包含 `quality`、`quality_score`、`attempted_providers`。

- [ ] **步骤 2：运行测试验证失败**

运行相关测试，预期缺少质量字段。

- [ ] **步骤 3：补实现**

修改 `format_provider_result_for_model()` 和 `WebSearchProviderResult.to_dict()` 输出质量字段。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_web_search_tool.py tests/test_admin_web_search_routes.py tests/test_web_search_relevance.py -q`

预期：全部通过。

## 验收

- `上海天气` 对 weather/cma/tianqi 结果判为相关。
- `上海天气` 对 Proton VPN 下载页判为低相关。
- 自动 provider 模式遇到低相关结果会继续 fallback。
- 显式 provider 模式仍只调用指定 provider，但返回质量字段。
- WebUI preview JSON 和发送给模型的消息都包含质量字段。
- 工具 schema 与 Prompt Runtime 说明不再宣称“第一个有结果停止”。
- 定向测试和全量测试通过。
