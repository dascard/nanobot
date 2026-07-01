# Session 摘要浏览修复设计

## 背景

线上摘要浏览已经具备近期摘要与长期摘要入口，但实际数据存在四类问题：

- 长期摘要 v2 数据的 `preview` 顶层为空，Web 默认显示 `-`，必须点击展开才可读。
- 旧数据同时存在 `group_123` 与 `123` 两种 session_id，列表重复。
- 测试、烟测和本地 repl session 默认混入管理视图。
- 新生成的长期摘要会把 sender 前缀、纯图片占位和命令类短消息带进主题词。

## 目标

让 Web 管理端默认展示可读、去重、按类型过滤后的摘要列表，并改善后续摘要生成质量。

## 方案

### 后端浏览服务

`AdminSessionMemoryBrowser` 作为管理浏览层，不改变底层数据：

- 将裸数字旧群 session 与 `group_` 前缀 session 归并到规范 `group_<id>`。
- 详情查询支持 session alias，因此打开 `group_123` 时能看到旧 `123` 的摘要。
- 默认过滤测试/烟测 session，保留 `include_system_sessions=true` 作为审计入口。
- `kind=recent|long|all` 控制列表只展示当前 tab 有数据的 session。
- 对 memory_digests v2，从 `meta_json.preview.brief`、`keywords`、`recall_cards`、`long_summary` 派生 preview；`include_content=true` 时若 `content` 为空，从 v2 meta 重新渲染 level 内容。

### 前端展示

`SessionSummaryBrowser` 按 tab 请求对应 kind：

- 近期摘要只列出 `summary_count > 0` 的 session。
- 长期摘要只列出 `digest_count > 0` 的 session。
- 长期摘要列表显示 `latest_digest_preview`，不再复用近期摘要字段。

### 摘要生成质量

`MemoryDigestBuilder` 在后续生成 v2 摘要时：

- 去掉群聊 ambient 内容里的重复 `[sender]:` 前缀。
- 纯图片占位、签到/钓鱼等命令按正文判断，不被 sender 前缀绕过。
- 关键词提取避免把常见空泛词当主题。

## 不做

- 不迁移或重写已有历史摘要。
- 不把所有 ambient 写入 `conversation_turns`。
- 不改变 RAG 检索语义。

## 验证

- 后端单测覆盖 v2 preview 派生、session alias 归并、系统 session 过滤。
- 摘要构建单测覆盖 sender 前缀剥离和图片占位过滤。
- Web 源码测试覆盖按 tab 传 `kind` 和长期摘要 preview 字段。
