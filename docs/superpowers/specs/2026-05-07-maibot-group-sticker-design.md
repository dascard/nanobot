# Maibot 群聊能力补齐与表情包系统设计

## 背景

上一阶段已经把 Nanobot 的群聊入口、TimingGate、`group_recent_context`、群长期记忆和回复模型路由向 Maibot 靠拢。本阶段继续补齐群聊侧的可运行能力，重点是：

- 审查并固化群聊入口统一性。
- 将 Maibot 的 talk_value、轻量 recent context、表达/黑话持续学习适配到现有 FastAPI + KT bridge 架构。
- 增加表情包系统，提供存储、检索、发送引用和工具接入。

Maibot 的表情包系统核心能力是：表情哈希/文件保存、描述与标签、禁用/注册状态、按情绪或语义选择、发送后更新使用记录。Nanobot 当前没有原生消息组件，QQbot 推送只接受文本，因此本次采用 QQ/OneBot 兼容的 CQ 图片码作为发送载体。

## 目标

1. 群聊消息入口继续以 `/api/v1/group/message` 为唯一主入口，普通消息、@bot、回复 bot、叫 bot 名字都进入 GroupRuntime。
2. TimingGate 除 pending 外接收 `<timing_recent_context>`，用于判断“那这个怎么办”这类依赖前文的短句。
3. GroupRuntime 使用规范化 group_id，避免 `123` 和 `group_123` 产生两个 state。
4. 群聊发言频率引入 `talk_value`，普通 ambient 消息按群活跃度动态决定是否触发 gate。
5. 表达/黑话学习后台任务持续扫描 ambient 消息，不再只依赖 group_analysis 后沉淀。
6. 增加 `StickerMemory` 表，支持表情包哈希、群/全局作用域、URL 或 CQ file、描述、标签、情绪、启用状态和使用统计。
7. 群聊图片/表情包自动注册：QQbot 侧把图片段、表情段和文件引用传入 `/group/message`，服务端按 hash 去重入库。
8. 复用现有 Qwen 视觉能力为未描述的表情包后台生成描述、标签和情绪，不新增多模态模型。
9. 增加 `sticker_search` 工具：模型按关键词/情绪搜索表情包，拿到可直接放进 `reply(content)` 的 CQ 图片码。
10. 增加管理 API：注册、搜索、禁用表情包，供 QQbot 或后台管理面板接入。
11. 允许同步修改 QQbot 仓库：入站兼容群图片/表情，出站解析服务端返回的 CQ 图片码。

## 非目标

- 不照搬 Maibot 的完整图片文件替换策略和存储淘汰策略；本阶段先做 hash 去重、启停和使用统计。
- 不引入新的向量数据库或新的多模态模型；视觉描述复用当前 Qwen/image_summary 通道。
- 不把 KT reply 工具改成多消息组件输出。
- 不实现表情包 Web 管理台；只提供 HTTP API 和工具接入。

## 架构

### 群聊运行时

`GroupRuntime` 负责状态机：

- `group_id` 统一规范化为 `group_<raw>`。
- `talk_value` 存在 `ChatStreamConfig`，默认 `0.5`。普通 ambient 需要累计到 `round(1/talk_value)` 条 pending 才进入 TimingGate。
- 直接触发类（@bot、回复 bot、叫名字、命令）跳过 talk_value gate。
- timer 回调不能绕过 talk_value gate。

`api.routes.group_message()` 在当前消息入库前构造 `build_timing_recent_context()`，避免当前消息同时出现在 recent 和 pending 里。

### 持续学习

`core.expression_learner` 每 10 分钟扫描最近 15 分钟群聊 ambient：

- 剥离 `[用户名]:` 前缀。
- 重复短句进入 `ExpressionMemory`。
- “X 就是 Y / X 的意思是 Y / X=Y”进入 `JargonMemory`。
- Python upsert 状态机负责置信度和 active/candidate 状态。

### 表情包系统

新增 `StickerMemory`：

- `sticker_hash`：稳定去重键，优先使用 QQbot 传入 hash；否则由 URL/file/caption 生成。
- `chat_stream_id`：`qq:<group_id>:group` 或 `global`。
- `file_ref`：可发送引用，支持 URL、本地 file、OneBot file ID。
- `send_code`：缓存的 `[CQ:image,file=...]`，为空时由 `file_ref` 生成。
- `description/tags/emotions`：检索字段。
- `status`：`active/candidate/disabled`。
- `usage_count/last_used`：选择后更新。

自动注册：

- `/api/v1/group/message` 接收 `files` 和 `client_meta.stickers`。
- 图片/表情包消息即使没有文本，也进入统一群聊入口，用 `[图片]` 或 `[表情包]` 占位写入 ambient log，并进入 TimingGate。
- 服务端按 `chat_stream_id + sticker_hash` upsert；已有记录只刷新 `last_seen` 和补充缺失字段。
- 未带描述的表情包加入后台描述任务，调用现有 Qwen 视觉接口生成 description/tags/emotions。
- Qwen 失败不阻塞群消息处理，记录为 candidate，等待后续显式注册或下一次补全。

工具 `sticker_search`：

- 入参：`query`、`group_id`、`limit`、`prefer_global`。
- 先查当前群，再查 global。
- 输出候选及 `send_code`，明确“把 send_code 放入 reply(content) 即可发送表情包”。

QQbot 适配：

- 群聊 lurker 不再丢弃纯图片/表情消息。
- 提取 `image`、`mface`、`face` 等可用消息段，传 `files` 和 `client_meta` 到 Nanobot。
- 出站 `_send_answer()` 检测纯 CQ 图片码并转成 OneBot `MessageSegment.image()` 发送。

管理 API：

- `POST /api/v1/stickers/register`
- `GET /api/v1/stickers/search`
- `POST /api/v1/stickers/{sticker_id}/disable`

## 风险与缓解

- QQbot 不解析 CQ 图片码：本阶段同步修改 QQbot `_send_answer()`，并把输出格式集中在 `core.sticker_memory.build_sticker_send_code()`，后续可切换。
- 模型滥发表情包：prompt 中要求只在斗图、玩梗、用户要求或语气明显适合时使用。
- 群里普通图片被误收为表情包：优先依赖 QQbot 传入的 segment 类型；无法区分时先以 `candidate` 入库，搜索工具默认只返回 `active`。

## 验收

- `StickerMemory` 表可创建，注册同 hash 表情包会更新而非重复插入。
- 搜索命中 description/tags/emotions，并按群内表情优先于 global。
- 群里纯图片/表情消息不会被 QQbot 丢弃，会进入 `/group/message` 并自动注册。
- 未带描述的表情包会触发 Qwen 后台描述补全，失败不影响消息链路。
- `sticker_search` 工具返回 CQ 图片码和候选元信息。
- prompt 包含 `sticker_search` 路由和克制使用规则。
- QQbot 能把纯 CQ 图片码回复转成图片发送。
- TimingGate recent context、talk_value gate、持续学习测试通过。
- 全量 pytest 通过，提交使用中文 Conventional Commit。
