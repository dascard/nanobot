# Web Search Provider Priority 设计

## 背景

当前 `web_search` 自动模式已经由 runtime 统一选择 provider，模型不可见 `provider` 参数。自动模式仍按 catalog 固定顺序尝试启用 provider，管理员无法表达“Brave 优先、SearXNG fallback、Tavily 兜底”这类策略。

## 目标

- 为每个 provider 增加可配置 `priority`，数值越小越优先。
- 自动搜索候选池只包含声明了 `search` capability 的 provider。
- 默认 priority 保持现有 catalog 顺序，避免升级后行为突变。
- WebUI 可以查看和编辑 priority。
- usage 表继续按真实调用积累数据，为后续健康度惩罚打基础。

## 非目标

- 不做关键词分类。
- 不做 query 类型路由。
- 不做多 provider 并行搜索。
- 不做健康度自动降权。
- 不改变模型工具 schema；模型仍只传 `query` 和 `limit`。

## 设计

### 配置模型

新增 setting：

```text
web_search.providers.<id>.priority
```

来源解析仍遵循 DB > env > catalog default：

```text
WEB_SEARCH_<PROVIDER>_PRIORITY
```

`ProviderResolvedConfig` 新增 `priority: int`，`public_dict()` 返回该字段。

### 默认优先级

Catalog 增加 `default_priority`，默认值按现有顺序递增：

```text
searxng=100
serper=200
brave=300
tavily=400
ddgs=500
...
```

这样不配置 priority 时，行为与当前固定 catalog 顺序一致。

### Runtime 排序

自动模式候选池：

```text
catalog item capabilities 包含 "search"
且 provider config enabled=True
```

排序规则：

```text
(priority, catalog_index, provider_id)
```

显式 provider 仍用于 WebUI 调试；如果 provider 不支持 `search`，返回 `provider_capability_unsupported`。

### WebUI

Provider 卡片展示 priority。配置弹窗新增“优先级”数字输入，保存时随 `enabled/base_url/api_key` 一起提交。

## 验收

- 默认 provider 顺序不变。
- 配置 Brave priority 小于 SearXNG 后，自动搜索先调用 Brave。
- 只有具备 `search` capability 的 provider 会进入自动候选池。
- Provider 配置 API 返回并保存 `priority`。
- WebUI 页面包含 priority 展示和编辑入口。
- 不引入关键词分类或多 provider 并行。
