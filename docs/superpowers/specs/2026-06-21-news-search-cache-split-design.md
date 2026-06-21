# news_search 运行时缓存拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍包含
`creatures/nanobot/prompts/skills/news_search/tool.py`。第一刀已经把旧版报告、
评分、价值信号和 layout helper 拆到 `legacy_report.py`，`tool.py` 从原 1835 行
降至 1149 行，但仍同时承担搜索后端、RSS / DDG 接入、运行时缓存、LLM 版式摘要、
AI 日报工具适配和若干兼容 facade。

当前缓存边界集中在 `tool.py` 顶部和 `AiDailyTool._execute()`：

- `NEWS_SEARCH_CACHE_TTL_SECONDS`
- `_NEWS_SEARCH_CACHE`
- `_NEWS_SEARCH_CACHE_LOCK`
- `_news_search_cache_key()`
- `_get_cached_news_result()`
- `_store_cached_news_result()`

测试也直接依赖旧模块符号，例如
`tests/test_tools_package.py::test_ai_daily_tool_reuses_equivalent_daily_query_cache`
和 `tests/test_ai_daily_ingest.py` 会调用 `news_tool._NEWS_SEARCH_CACHE.clear()`。
因此第二刀必须在拆分职责的同时保留旧路径兼容。

## 目标

本阶段采用小步缓存拆分，目标是把运行时缓存状态和缓存 key 计算迁移到独立模块：

1. 新增 `creatures/nanobot/prompts/skills/news_search/runtime_cache.py`。
2. 将缓存状态、TTL 默认值、缓存读写、日报查询识别、日期解析和缓存 key 计算迁移到
   `runtime_cache.py`。
3. `tool.py` 保留旧符号：`NEWS_SEARCH_CACHE_TTL_SECONDS`、`_NEWS_SEARCH_CACHE`、
   `_NEWS_SEARCH_CACHE_LOCK`、`_news_search_cache_key()`、`_get_cached_news_result()`、
   `_store_cached_news_result()`、`_coerce_date()`、`_extract_date()` 和
   `_is_daily_digest_query()`。
4. 旧符号必须指向同一个缓存 dict / lock，或通过薄 wrapper 调用新模块，保证
   `news_tool._NEWS_SEARCH_CACHE.clear()`、`news_tool._get_cached_news_result()` 和
   `AiDailyTool._execute()` 的行为不变。
5. `tool.py` 层 wrapper 继续读取 `tool.NEWS_SEARCH_CACHE_TTL_SECONDS`，保留测试或调试时
   monkeypatch 旧 TTL 符号的行为。
6. `runtime_cache.py` 不导入 `DDGS`、`trafilatura`、`BaseTool`、`NewAPIClient`、
   `run_awaitable_sync` 或 `news_search.tool`，避免轻量缓存模块反向加载运行时工具依赖。

## 非目标

本阶段不做以下事情：

- 不迁移 `AiDailyTool`、`WebTools`、`search_and_extract_news()`、
  `search_and_extract_news_v2()`、`_run_news_daily_pipeline()` 或 RSS / DDG 搜索后端。
- 不改变 `ai_daily` 工具名称、参数、返回 JSON envelope、HTML 兜底或 ingest warning
  语义。
- 不改变缓存 key 版本号 `v2_20260503`、日报 key 形态、普通 query key 形态、TTL 默认值
  `NEWS_SEARCH_CACHE_TTL_SECONDS=300` 或旧的 64 项淘汰触发条件。
- 不把缓存落盘，不引入外部缓存，不新增 DB schema。
- 不改 prompt runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。
- 不新增 `asyncio.run()`，不新增同步函数包 awaitable。

## 方案比较

### 方案 A：新增 `runtime_cache.py` 并保留 `tool.py` facade

将缓存状态和纯缓存 helper 迁移到 `runtime_cache.py`，`tool.py` 只暴露同名旧符号和薄
wrapper。

优点：

- 拆分范围集中，主要是标准库逻辑，风险低。
- 直接减少 `tool.py` 的缓存职责，为搜索后端或 AI 日报适配层的下一刀留出清晰边界。
- 能通过同一 dict / lock 保留现有测试的 `news_tool._NEWS_SEARCH_CACHE.clear()` 入口。
- `runtime_cache.py` 可独立测试，不加载网络搜索和 KT 工具依赖。

缺点：

- 行数收益小于搜索后端拆分，`tool.py` 仍会超过 800 行。
- 需要小心处理 `NEWS_SEARCH_CACHE_TTL_SECONDS` 的旧路径 monkeypatch 语义。

### 方案 B：直接拆搜索后端到 `legacy_search.py`

迁移 RSS、Juya、DDG、trafilatura、`WebTools` 和 `search_and_extract_news*()`。

优点：

- 行数收益更大。
- 搜索职责能从工具适配层中明显分离。

缺点：

- 旧测试大量 patch `news_search.tool._urlopen`、`DDGS`、`trafilatura.fetch_url`、
  `WebTools.search` 和 `_run_news_daily_pipeline()`，直接迁移容易破坏 monkeypatch
  语义。
- 搜索后端与 `_summarize_news_layout()`、RSS 日期过滤、fallback HTML 仍有较多交叉。

### 方案 C：拆 AI 日报工具适配层

迁移 `AiDailyTool`、结果 envelope、兜底 HTML 和 ingest 逻辑。

优点：

- 可以让 `tool.py` 更接近纯 legacy 搜索 facade。
- 对 KT 工具适配层边界更清晰。

缺点：

- `AiDailyTool._execute()` 当前连接缓存、pipeline、fallback、ingest 和
  `build_reply_tool_result()`，迁移前需要更完整的工具契约测试。
- 对运行路径的影响大于缓存拆分。

推荐采用方案 A。方案 B / C 留给缓存边界稳定后的独立阶段，避免第二刀同时移动网络、
工具注册和运行时状态。

## 模块边界

### `runtime_cache.py`

职责：

- 读取 `NEWS_SEARCH_CACHE_TTL_SECONDS` 环境默认值。
- 持有 `_NEWS_SEARCH_CACHE` 和 `_NEWS_SEARCH_CACHE_LOCK`。
- 提供 `_coerce_date()`、`_extract_date()` 和 `_is_daily_digest_query()`。
- 提供 `_news_search_cache_key()`，保持当前 key 版本和 key 形态。
- 提供 `_get_cached_news_result()` 和 `_store_cached_news_result()`，保持 TTL 过期和旧淘汰逻辑。

依赖限制：

- 只允许依赖 `os`、`re`、`threading`、`time`、`datetime` 和 `typing` 等标准库。
- 不得导入 `tool.py` 或任何会加载网络 / KT 工具运行时的模块。

建议接口形态：

```python
NEWS_SEARCH_CACHE_TTL_SECONDS = int(os.environ.get("NEWS_SEARCH_CACHE_TTL_SECONDS", "300"))
NEWS_SEARCH_CACHE_MAX_ENTRIES = 64
_NEWS_SEARCH_CACHE: dict[tuple[Any, ...], tuple[float, str]] = {}
_NEWS_SEARCH_CACHE_LOCK = threading.Lock()

def _extract_date(query: str, *, now: datetime | None = None) -> str | None:
    ...

def _news_search_cache_key(
    query: str,
    max_results: int,
    mode: str = "fast",
    user_id: str = "",
    session_id: str = "",
    *,
    now: datetime | None = None,
    date_extractor: Callable[[str], str | None] | None = None,
    daily_digest_detector: Callable[[str], bool] | None = None,
) -> tuple[Any, ...]:
    ...

def _get_cached_news_result(
    key: tuple[Any, ...],
    *,
    ttl_seconds: int | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> str | None:
    ...

def _store_cached_news_result(
    key: tuple[Any, ...],
    output: str,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    ...
```

`user_id` 和 `session_id` 继续保留在签名里但不进入 key，保持现有跨 session 等价日报
缓存行为。

### `tool.py`

职责：

- 从 `runtime_cache.py` 导入同一个缓存 dict / lock。
- 保留旧函数名 wrapper，供旧调用方和测试 monkeypatch 使用。
- `AiDailyTool._execute()` 继续调用旧函数名，不直接感知新模块。

建议 facade：

```python
from . import runtime_cache as _runtime_cache

NEWS_SEARCH_CACHE_TTL_SECONDS = _runtime_cache.NEWS_SEARCH_CACHE_TTL_SECONDS
_NEWS_SEARCH_CACHE = _runtime_cache._NEWS_SEARCH_CACHE
_NEWS_SEARCH_CACHE_LOCK = _runtime_cache._NEWS_SEARCH_CACHE_LOCK
_coerce_date = _runtime_cache._coerce_date
_extract_date = _runtime_cache._extract_date
_is_daily_digest_query = _runtime_cache._is_daily_digest_query

def _news_search_cache_key(
    query: str,
    max_results: int,
    mode: str = "fast",
    user_id: str = "",
    session_id: str = "",
) -> tuple[Any, ...]:
    return _runtime_cache._news_search_cache_key(
        query,
        max_results,
        mode=mode,
        user_id=user_id,
        session_id=session_id,
        date_extractor=_extract_date,
        daily_digest_detector=_is_daily_digest_query,
    )

def _get_cached_news_result(key: tuple[Any, ...]) -> str | None:
    return _runtime_cache._get_cached_news_result(
        key,
        ttl_seconds=NEWS_SEARCH_CACHE_TTL_SECONDS,
    )

def _store_cached_news_result(key: tuple[Any, ...], output: str) -> None:
    _runtime_cache._store_cached_news_result(key, output)
```

## 测试策略

新增 `tests/test_news_search_runtime_cache.py`，覆盖以下契约：

1. `runtime_cache.py` 可轻量导入，不加载 `news_search.tool`、`duckduckgo_search`、
   `trafilatura` 或 `kohakuterrarium.modules.tool.base`。
2. `_news_search_cache_key()` 保持日报 key 与普通 query key 的当前形态：
   - `2026年5月1日 AI 日报` → `("v2_20260503", "daily_ai", "2026-05-01", 8, "quality")`
   - `  GPT-5   NEWS  ` → `("v2_20260503", "query", "gpt-5 news", 3, "fast")`
3. `tool.py` facade 与 `runtime_cache.py` 共享同一个 `_NEWS_SEARCH_CACHE` 和
   `_NEWS_SEARCH_CACHE_LOCK`。
4. 通过 `tool.NEWS_SEARCH_CACHE_TTL_SECONDS` monkeypatch TTL 时，旧
   `_get_cached_news_result()` wrapper 仍按旧路径 TTL 生效。
5. 现有 AI 日报缓存复用测试继续通过：
   `tests/test_tools_package.py::test_ai_daily_tool_reuses_equivalent_daily_query_cache`。

相邻回归：

```bash
python -m pytest tests/test_news_search_runtime_cache.py -v
python -m pytest tests/test_tools_package.py::test_ai_daily_tool_reuses_equivalent_daily_query_cache -v
python -m pytest tests/test_ai_daily_ingest.py -v
python -m pytest tests/test_ai_daily_tool_and_sources.py -v
python -m pytest tests/test_news_search_legacy_report.py -v
python -m pytest tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard -v
```

最终提交前仍运行：

```bash
git diff --check
python -m pytest tests/ -v
```

## 风险与约束

- 缓存 dict 必须是同一个对象；否则旧测试和调试脚本清理 `news_tool._NEWS_SEARCH_CACHE`
  不能影响实际运行缓存。
- `tool.py` 的 TTL wrapper 必须读取旧模块变量，避免测试或运维临时 monkeypatch
  `news_tool.NEWS_SEARCH_CACHE_TTL_SECONDS` 后失效。
- `runtime_cache.py` 不能反向导入 `tool.py`，否则会抵消拆分收益并重新加载 heavy 依赖。
- `AiDailyTool._execute()` 暂不改调用路径，减少工具运行时风险。
- 不处理搜索后端 monkeypatch 迁移；该主题需要单独设计搜索 facade 或依赖注入策略。

## 子 agent 分工建议

下一阶段代码实现可拆成两个互不冲突的子任务：

- **测试子 agent（只写 `tests/test_news_search_runtime_cache.py`）：** 按本设计编写红灯测试，
  不修改生产代码。
- **实现主线程（写 `runtime_cache.py` 与 `tool.py`）：** 迁移缓存状态和 wrapper，保持旧路径契约。
- **文档子 agent（只读或只写文档）：** 在代码验证通过后同步 `.Codex/plans/`、
  `docs/todo.md` 和 `docs/plan_walkthrough.md`。

生产代码文件存在依赖顺序，建议由主线程串行编辑，避免多个 agent 同时修改 `tool.py`。

## 验收标准

- `runtime_cache.py` 存在，且轻量导入测试证明不会加载运行时工具依赖。
- `tool.py` 中缓存相关真实状态不再本地定义，旧符号仍可导入和调用。
- `AiDailyTool` 缓存命中行为不变，同一等价请求第二次不重复调用 pipeline。
- `tests/test_news_search_runtime_cache.py`、AI 日报缓存相邻回归、旧报告回归、
  `asyncio.run` 策略测试和全量 `python -m pytest tests/ -v` 均通过。
- 本阶段不新增 `asyncio.run()`，不新增同步函数包 awaitable。
