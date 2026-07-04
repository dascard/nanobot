# 搜索 API Provider 轻量配置页设计

## 背景

管理后台目前没有配置 Web Search provider 的入口。KT 框架自带的 `web_search` 工具
(`vendor/KohakuTerrarium/.../builtins/tools/web_search.py`)**只支持 DuckDuckGo(ddgs)**,
硬编码 `DDGS()`,不读取任何 api_key / base_url,也没有 provider 概念。

要让 agent 未来能用上 Tavily / Serper 等需要密钥的搜索服务,第一步是先把"配置"这件事
做扎实:管理员能配置 provider 的启用状态、API Key、Base URL,并验证配置可用。本设计只
覆盖这一步(方案 A),不实现搜索编排与 agent 接入。

本文档是对初版实施计划的修订。修订依据是对现有代码库的核查,重点修正三个初版未点破的
硬伤(见 3.2 / 3.3 / 3.4)。

## 目标

- Provider catalog 作为单一事实源:定义支持哪些 provider、需要哪些字段、官方文档链接。
- Admin API:列出 provider 配置状态、保存配置、清空 API Key、测试连接。
- WebUI:新增"搜索 API"页面,支持查看、配置、保存、测试、跳转官方 API 页面。
- 安全:API Key 不回显、不写日志、不进错误响应、不进 audit;Base URL 做基础校验。
- 配置存储复用现有 `SystemSetting` / `SettingsService`,不新增表。

## 非目标

- 不把 provider 接入 agent 的 web_search 工具(下一阶段)。
- 不实现搜索结果归一化、去重、rerank、citation、research mode。
- 不实现多 provider 自动路由、fallback、cooldown、health 趋势。
- 不实现用量统计、预算、账单、搜索日志。
- 不新增密钥加密表或密钥轮换;仅保证脱敏展示与日志安全,不解决 DB 静态加密。
- 不做完整 SSRF denylist;但测试接口只能访问 catalog 对应 endpoint,不支持任意 URL fetch。

## 方案

### 3.1 范围收敛(相对初版的调整)

初版列了 10 个 provider 并要求全部实现 smoke test。核查后判断这与"轻量配置页"定位冲突:
`provider_tests.py` 是最重、最易碎的部分(真实联网、外部 API 会变),且 linkup / you 还需
现查文档。因此本设计**收敛首批可测 provider**,catalog 元数据仍保留全部条目以便前端展示。

- **首批实现 smoke test**:searxng、serper、brave、tavily、ddgs(共 5 个,覆盖自托管 +
  主流 SERP + agent 友好 + 免费 fallback)。
- **catalog 保留但暂不测**:exa、firecrawl、linkup、you、jina。这些 provider 可配置、可
  保存,但 test 接口返回 `error_code: "not_implemented"`,前端显示"暂不支持连接测试"。
- 后续要加测试时,只需在 `provider_tests.py` 补一个分发分支,不改 catalog / settings / 路由。

### 3.2 【硬伤修正】api_key_source 不能用 SettingsService 读

**问题**:`SettingsService.get()`(`core/settings_service.py:82`)透明合并 DB > env > default,
读出来的值**无法区分来源**。而需求要求 GET 返回 `api_key_source: "db"|"env"|null`,并支持
"清空 DB key 后回落到 env"。因此 provider 配置解析**不能走 `settings.get()`**。

**方案**:`core/web_search/provider_settings.py` 自实现三级解析,直接查表 + 读环境变量:

```
def resolve_field(db, provider_id, field) -> (value, source):
    key = f"web_search.providers.{provider_id}.{field}"
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row is not None and row.value is not None:
        return row.value, "db"
    env_val = os.environ.get(env_name_for(provider_id, field))
    if env_val is not None:
        return env_val, "env"
    return catalog_default(provider_id, field), None
```

- `api_key_configured = (source is not None and value != "")`。
- 写入复用现有 `update_setting` 的 upsert 模式(`api/admin_routes.py:597`):无行则新建
  `SystemSetting(key, value)`,有行则改 `row.value`,`commit` 后调 `settings.invalidate()`。
- 清空 DB key = 删除该 `SystemSetting` 行(参考 `reset_setting`,`admin_routes.py:612`),删除后
  source 自然回落到 env(若存在)。
- **env 变量命名**:`WEB_SEARCH_<PROVIDER_ID_UPPER>_{ENABLED,API_KEY,BASE_URL}`。当前所有
  provider id 无连字符,直接大写即可;解析时仍统一做 `id.upper().replace("-", "_")` 以防未来新增。

### 3.3 【硬伤修正】30 条 setting 会污染现有系统设置页

**问题**:现有 `GET /settings`(`api/admin_routes.py:543`)遍历**全部** `SETTING_DEFS` 无条件返回,
**没有 category 过滤**。一旦注册 5~10 provider × 3 字段 = 15~30 条 `web_search.providers.*`,
它们会全部冒进通用"系统设置"页(api_key 显示 `****`),造成噪音。

**方案**:两步隔离,二选一但都要做第一步:

1. **给这批 SettingDef 用独立 category `"web_search"`**(现有 category 无此值,新增一个)。
2. **在 `GET /settings` 增加可选 `?exclude_category=web_search` 或默认排除 `web_search`**。
   为最小改动且不破坏现有前端,采用:`list_settings` 默认**跳过 `category == "web_search"`** 的条目
   (这批设置专属"搜索 API"页,不应出现在通用设置页)。改动仅在遍历处加一行 `if defn.category
   == "web_search": continue`。

> 决策:采用"默认排除"。理由:web_search 配置有专属页面和专属 admin API,通用设置页展示它们
> 既冗余又有误操作风险(通用页的 PUT 不做 base_url/clear_api_key 校验)。

### 3.4 【硬伤修正】与 KT web_search 工具的对接断层

**问题**:KT 的 `web_search` 工具只认 ddgs,不读 api_key/base_url。本阶段配的需要 key 的
provider,下一阶段**无法被现有 KT 工具直接消费**。

**方案(本阶段只定契约,不实现接入)**:

- 本设计确立的 setting key 命名 `web_search.providers.<id>.{enabled,api_key,base_url}` 即为
  下一阶段的**消费契约**。下一阶段的接入方(改 KT 工具或在 server 侧包新工具)从这些 key 读配置。
- catalog 的 `capabilities` 字段(如 `["search"]` / `["crawl","extract"]`)为下一阶段路由选择
  预留语义,本阶段仅存储与展示,不参与逻辑。
- 本文档"后续"节记录接入方式待定,提醒实现者本阶段 schema 必须稳定,避免下一阶段返工。

### 3.5 Provider Catalog 规格

catalog 顺序稳定,前端按此顺序展示。`testable` 标记本阶段是否实现 smoke test(3.1 收敛)。

| id | 名称 | api_key | base_url | testable | docs_url |
|----|------|---------|----------|----------|----------|
| searxng | SearXNG | 否 | 必需 | ✓ | https://docs.searxng.org/dev/search_api.html |
| serper | Serper | 必需 | 可选 | ✓ | https://serper.dev/signup |
| brave | Brave Search | 必需 | 可选 | ✓ | https://brave.com/search/api/ |
| tavily | Tavily | 必需 | 可选 | ✓ | https://docs.tavily.com/documentation/quickstart |
| ddgs | DuckDuckGo | 否 | 否 | ✓ | https://pypi.org/project/duckduckgo-search/ |
| exa | Exa | 必需 | 可选 | ✗ | https://exa.ai/docs/reference/search-api-guide |
| firecrawl | Firecrawl | 必需 | 可选 | ✗ | https://docs.firecrawl.dev/api-reference/introduction |
| linkup | Linkup | 必需 | 可选 | ✗ | https://docs.linkup.so/pages/documentation/platform/authentication |
| you | You.com | 必需 | 可选 | ✗ | https://you.com/docs/administration/api-keys |
| jina | Jina Reader | 可选 | 可选 | ✗ | https://jina.ai/reader/ |

> **消除初版矛盾**:初版表格说 serper/brave/exa/tavily base_url "可选" 但 metadata 示例
> `supports_base_url: False`。本设计统一裁定:凡"可选"即 `supports_base_url = True`
> (允许覆盖默认 endpoint,留空用 default_base_url)。ddgs 为本地库,`supports_base_url = False`。

catalog item 字段(dataclass,frozen):

```
{
  "id": "serper",
  "name": "Serper",
  "description": "Google SERP API provider",
  "capabilities": ["search"],
  "requires_api_key": True,
  "supports_base_url": True,
  "default_base_url": "https://google.serper.dev",
  "docs_url": "https://serper.dev/signup",
  "enabled_by_default": False,
  "testable": True,
}
```

### 3.6 配置存储规则

setting key:`web_search.providers.<id>.{enabled,api_key,base_url}`。

**读取**(见 3.2 三级解析):DB 行 > env > catalog 默认。enabled 默认取 `enabled_by_default`,
base_url 默认取 `default_base_url`,api_key 默认空。

**写入**:

- `enabled`:保存 bool(存为字符串 `"true"/"false"`,与现有 `_cast` bool 解析一致)。
- `base_url`:仅当 `supports_base_url=True` 才接受;否则传非空值 → 422。
  传**空字符串**语义为"重置":删除已存 DB 行,读取时回落到 catalog 默认(实现见
  `provider_settings.py` `update_provider_config` 的 else 分支)。
- `api_key`:非空字符串 = 替换密钥;为空或缺字段 = 不修改已有密钥。
- `clear_api_key=True` = 删除 DB 密钥行;与非空 `api_key` 同时出现 → 422。

**脱敏**:GET 永不含 api_key;含 `api_key_configured` 与 `api_key_source`。api_key 的
SettingDef 标记 `sensitive=True`。

> 注:`list_settings` 调 `all_values()` 会把 api_key 明文 load 进 SettingsService 内存缓存,
> 但 3.3 的 continue 使其不进任何响应,且 `provider_settings` 走独立 DB 解析、不依赖该缓存读 key。
> 当前无泄露路径;静态加密留待后续。

### 3.7 SettingDef 循环生成

30 条手写字面量既啰嗦又易漂移。在 `config_registry.py` 末尾对 catalog 循环生成注入
`SETTING_DEFS`(catalog 是单一事实源):

```
from core.web_search.provider_catalog import PROVIDER_CATALOG
for pid, item in PROVIDER_CATALOG.items():
    base = f"web_search.providers.{pid}"
    SETTING_DEFS[f"{base}.enabled"] = SettingDef(..., value_type="bool",
        category="web_search", default=item.enabled_by_default)
    SETTING_DEFS[f"{base}.api_key"] = SettingDef(..., value_type="str",
        category="web_search", sensitive=True, default="")
    if item.supports_base_url:
        SETTING_DEFS[f"{base}.base_url"] = SettingDef(..., value_type="str",
            category="web_search", default=item.default_base_url)
```

> 注意循环导入:`config_registry` 目前无外部依赖。若 `provider_catalog` 反向 import
> `config_registry` 会成环。约定 catalog **不 import** config_registry(catalog 只放纯数据)。

### 3.8 文件结构

创建:

- `core/web_search/__init__.py` — 模块声明。
- `core/web_search/provider_catalog.py` — catalog 元数据 + `list/get/is_known` 查询,纯数据,不 import config_registry。
- `core/web_search/provider_settings.py` — `ProviderResolvedConfig` + 三级解析 + upsert/clear。
- `core/web_search/provider_tests.py` — 首批 5 个 provider 的 smoke test + 脱敏 + 分发。
- `api/admin/web_search_routes.py` — 3 个 admin endpoint,复用 `api/admin/common.py`。
- `tests/test_admin_web_search_routes.py` — 契约、脱敏、来源、清空、错误路径测试。
- `webui/src/features/web-search/WebSearchPage.jsx` — 配置页。
- `webui/src/features/web-search/api.js` — API client,复用 `webui/src/api/client.js` 的 `api` 实例。

修改:

- `api/admin_routes.py` — include `web_search_routes.router`(参考 `model_routes` 的 include 模式)。
- `api/admin_routes.py` 的 `list_settings` — 默认排除 `category == "web_search"`(3.3)。
- `core/config_registry.py` — 循环生成注册 web_search settings(3.7)。
- `webui/src/App.jsx` — 增加 `/web-search` route + 导入页面。
- `webui/src/App.jsx` 的 `NAV` 数组 — 增加"搜索 API"导航项(位置靠近模型/API 配置区)。

> **前端无 providerMeta.js / 无 test.jsx**:核查确认 `webui/package.json` 无 vitest/jest/
> testing-library,无任何 `*.test.jsx`。本阶段**不新增前端测试框架**,前端仅以 `npm run build`
> 验证编译。UI 层常量直接内联在页面,不单开 providerMeta.js。

### 3.9 Admin API 契约

所有 endpoint 挂在 `/api/v1/admin/web-search` 下,鉴权用 `verify_admin`,审计用 `audit`。

**GET `/web-search/providers`** → `{ "providers": [ {catalog 字段 + enabled, base_url,
api_key_configured, api_key_source, testable, last_test: null} ] }`。`last_test` 本阶段不持久化,固定 null。

**PUT `/web-search/providers/{provider_id}`**
请求:`{ enabled?, api_key?, clear_api_key?, base_url? }`
响应:`{ "provider": { id, enabled, base_url, api_key_configured, api_key_source } }`
错误:未知 provider→404;不支持 base_url 却传非空→422;base_url scheme 非 http/https→422;
clear_api_key=true 且 api_key 非空→422。
audit **只记** `api_key_changed: bool` / `clear_api_key: bool`,**绝不记** api_key 明文;
请求 body 不得原样进 audit detail。

**POST `/web-search/providers/{provider_id}/test`**
请求:`{ query?: str }`(默认 `"nanobot"`)
成功:`{ ok: true, provider_id, duration_ms, message, sample_count }`
失败:`{ ok: false, provider_id, duration_ms, message, error_code }`
HTTP 状态约定:配置缺失/认证失败/provider 响应失败 → **200 + ok:false**(便于前端展示);
未知 provider / 请求格式错误 / admin auth 失败 → 对应 HTTP 错误码。
message 必须脱敏,不含 api_key、Authorization/X-API-KEY/X-Subscription-Token、带 token 的 URL。

### 3.10 Provider Smoke Test 策略

公共规则:超时 8s;UA `Nanobot-WebSearchConfig/1.0`;最多 3 次重定向;结构化结果不抛含密钥异常;
外部 HTTP 调用只在 `provider_tests.py`,路由层不拼 provider 细节。

**aiohttp 会话**:每次 `test` **新建 `async with aiohttp.ClientSession()`**,**绝不复用全局单例**
——避免重蹈 `clients/new_api_client.py:132` 修过的跨事件循环 bug(commit 29b291b)。

统一入口:`async def test_provider(provider_id, config: ProviderResolvedConfig, query) -> ProviderTestResult`。

前置短路(不发 HTTP):

- `requires_api_key` 但缺 key → 直接 `ok:false, error_code:"missing_api_key"`。
- `testable=False` → 直接 `ok:false, error_code:"not_implemented"`。
- base_url 非法在 routes/settings 阶段已拒,不进测试函数。

首批 5 个 provider 最小测试:

- searxng:`GET {base_url}/search?q=<query>&format=json`,2xx 且 JSON 含 `results` list。
- serper:`POST {base_url}/search`,header `X-API-KEY`,body `{"q":query,"num":3}`,2xx 且含 `organic`。
- brave:`GET https://api.search.brave.com/res/v1/web/search`,header `X-Subscription-Token`,
  params `q,count=3`,2xx 且含 `web.results`。
- tavily:`POST https://api.tavily.com/search`,body 含 `api_key` 与 `query`,2xx 且含 `results`。
- ddgs:import `ddgs` 或 `duckduckgo_search`,执行 `text(query, max_results=3)` 返回 list;
  依赖缺失 → `error_code:"dependency_missing"`,不自动安装。

脱敏函数(测试异常/message 返回前统一过一遍):替换 api_key 子串为 `***`;移除
Authorization / X-API-KEY / X-Subscription-Token 明文;URL query 中疑似 token 参数替换为 `***`。

**status/body 判断顺序**:HTTP provider(searxng/serper/brave/tavily)必须**先判 status,
非 2xx 用 `_error_body_snippet` 读 body 片段(200 字符,非 JSON 也不抛)再返回对应 error_code,
2xx 才解析 JSON**。若先解析 JSON,401/403/429 遇到网关返回的 HTML body 会被 `_json_response`
抛成 `ValueError` → 顶层 catch 成 `provider_bad_response`,使 auth/rate_limit 错误码失真。
错误 message 只取 body 片段,不回显 provider 完整响应体。

## 接口约定(错误码)

`missing_api_key` / `invalid_base_url` / `provider_auth_failed` / `provider_rate_limited` /
`provider_timeout` / `provider_bad_response` / `dependency_missing` /
`provider_capability_unavailable` / `not_implemented` / `unknown_provider`。

前端映射:missing_api_key→"请先配置 API Key";provider_auth_failed→"认证失败,请检查 API Key";
provider_rate_limited→"Provider 限流,请稍后重试";dependency_missing→"服务端缺少依赖";
not_implemented→"暂不支持连接测试";其余显示后端 message。

## 测试策略

后端(TDD,红-绿-重构),用 in-memory SQLite + monkeypatch env + monkeypatch HTTP:

- `test_list_providers_requires_admin`
- `test_list_providers_returns_catalog_and_config_state`
- `test_update_provider_saves_enabled_and_base_url`
- `test_update_provider_replaces_api_key_without_echoing_secret`
- `test_clear_api_key_removes_db_secret`
- `test_env_api_key_reported_configured_source_env_without_value`
- `test_db_key_overrides_env_source_db`
- `test_clear_db_key_falls_back_to_env_source`
- `test_unknown_provider_returns_404`
- `test_base_url_rejected_when_not_supported`(ddgs 传 base_url → 422)
- `test_invalid_base_url_scheme_rejected`
- `test_clear_and_set_api_key_conflict_rejected`
- `test_test_provider_missing_key_returns_ok_false_without_http_call`
- `test_test_provider_not_implemented_returns_ok_false`
- `test_test_provider_masks_secret_in_error_message`
- `test_web_search_settings_excluded_from_generic_settings_list`(验证 3.3 隔离)
- 全程断言响应 JSON 不含测试密钥字符串。

前端:无测试框架,`cd webui && npm run build` 编译通过即可。

## 验证计划

1. `python -m pytest tests/test_admin_web_search_routes.py -v` → 0 failures。
2. `python -m pytest tests/ -v` → 0 failures(确认未破坏现有 settings/admin 测试)。
3. 启动服务,`GET /api/v1/admin/web-search/providers` 返回 10 provider,顺序稳定。
4. `GET /api/v1/admin/settings` **不含** `web_search.providers.*`(验证隔离)。
5. `cd webui && npm run build` 成功。
6. 手动:配 tavily key → test 成功;清空 key → 状态回落;非法 base_url → 422。

> 遵循 CLAUDE.md:验证命令必须实际运行并确认输出,通过 verification-before-completion 后才声称完成。

## Prompt Runtime 核查

本阶段不改 `enriched_query` 组装、历史注入、conversation 结构,不接入 agent 工具,
因此**不涉及** `creatures/nanobot/prompt.md` 的标记或行为描述。下一阶段接入 agent 时需重新核查。

## 风险和缓解

- **DB 明文存 api_key**:本阶段只保证脱敏展示/日志安全,不做静态加密。缓解:sensitive 标记 +
  GET/audit/error 全链路脱敏;静态加密列入后续。
- **外部 API 变更导致 smoke test 失效**:首批收敛到 5 个稳定 provider;测试全部 monkeypatch
  HTTP,CI 不真实联网,只有手动验证真连。
- **循环导入**:catalog 不 import config_registry(3.7 约定)。
- **设置页隔离改动影响现有前端**:`list_settings` 只加一行 continue,现有 category 不受影响;
  加回归测试 `test_web_search_settings_excluded_from_generic_settings_list`。

## 提交计划(中文规范,禁止 git add -A/.)

1. `feat(搜索配置): 添加搜索供应商配置接口` — catalog、settings 注册、settings 页隔离、
   provider_settings 解析、admin routes、后端契约测试。
2. `feat(搜索配置): 添加供应商连接测试与脱敏` — provider_tests 首批 5 个 + 脱敏 + 错误码测试。
3. `feat(管理后台): 添加搜索 API 配置页面` — WebUI route、页面、api client、导航、构建验证。

每次显式列文件提交,例如:
`git add core/web_search/provider_catalog.py core/web_search/provider_settings.py ...`

## 后续(下一阶段)

- 将 catalog + `web_search.providers.*` 配置接入 agent:改造 KT `web_search` 工具或在 server
  侧包装新工具,从本阶段确立的 setting key 读配置(3.4 契约)。
- 补齐 exa/firecrawl/linkup/you/jina 的 smoke test(catalog 已含元数据,只需加分发分支)。
- 搜索结果归一化、多 provider 路由/fallback、用量与预算、api_key 静态加密。

