# AI 日报插件架构重构

## 目标

稳定、快、少 bug、输出可控、方便维护。

默认链路：

```
RSS/官方源 → NewsItem → 去重/评分/分类 → Digest JSON → render_html() → 图片
```

深度模式：

```
Web Search → 正文抽取 → EvidenceCard → LLM → validate → render_html()
```

## 目录结构

```
news_daily/
├── tool.py          # Bot 工具入口，只负责参数解析、路由、缓存
├── config.py        # 源配置、TTL、模式开关
├── schema.py        # NewsItem / NewsDigest / SourceConfig
├── cache.py         # TTL cache / stale cache
├── sources/         # RSS/Atom/curated/web_search providers
├── pipeline/        # collect/normalize/dedup/rank/classify/digest/summarize/research
├── render.py        # 复用现有 HTML 模板
└── image.py         # HTML → 图片
```

## 三种模式

| 模式 | 数据源 | LLM | 目标耗时 |
|------|--------|-----|---------|
| fast | RSS官方+Juya | 不调 | 2-8s |
| quality | 官方+策展 | 仅摘要 | 5-15s |
| research | Web Search | 深度 | 15-60s |

## 迁移计划

1. 数据源层（base/rss/official/curated + schema + config）
2. fast 模式（collect/normalize/dedup/rank/digest + render_html）
3. quality 模式（light evidence + LLM summary）
4. 旧 v2 改 research（保留 EvidenceCard，去默认搜）
5. 图片输出（html_to_image）
