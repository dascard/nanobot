---
name: 群聊日报工具 V2
version: 1
kind: tool
tool_name: group_analysis
description: group_analysis 工具的使用边界。
---
## group_analysis 工具边界

用于分析群聊消息并生成群日报、话题总结、活跃用户称号、群聊金句、活跃度分布和聊天质量锐评。

### 何时使用

- 用户要求总结群聊、分析某个群、生成群日报、查看某群近期讨论、复盘群里今天聊了什么时直接使用 `group_analysis`。
- 用户说“这个群”“本群”时，`group_id` 使用当前运行上下文中的群 ID。
- 用户说具体群号、`group_` 前缀 ID、session_id、stream_id 或群名时，`group_id` 直接传该值。
- 只知道群名时也直接传群名，不要为了查群号先调用 `sql_analysis`。

### 参数填写

- `group_id` 必填，是被分析的群，不是最终回复要发送到的会话。
- `instructions` 可选，只写用户额外分析要求，例如“只看最近讨论 AI 的部分”“偏技术总结”“找活跃用户”。
- `window_hours` 可选，默认 24；传 `0` 表示不限制历史范围。用户说“最近 2 小时/最近 3 天”时换算后填写。

### 工具职责

`group_analysis` 会自行完成群解析、消息读取、清洗去重、统计聚合、LLM 分支分析和 HTML 日报渲染。

内部 LLM 分支使用同目录下这些 V2 模板，而不是代码里的旧硬编码文本：

- `tools/group_analysis/system`：共用 system prompt。
- `tools/group_analysis/topics`：话题总结。
- `tools/group_analysis/titles`：活跃用户称号。
- `tools/group_analysis/quotes`：群聊金句。
- `tools/group_analysis/quality`：聊天质量锐评。

它的报告结构包括：

- 群级统计：消息数、参与人数、总字数、表情统计、活跃时段。
- 活跃度分布：按小时展示群聊活跃轨迹。
- 话题总结：提炼主要话题、贡献者和简短细节。
- 活跃用户称号：给活跃成员生成称号、理由和可选 MBTI 风格标签。
- 群聊金句：提取少量有代表性的原话。
- 聊天质量锐评：从内容密度、互动性、信息量和氛围等维度评价。

### 输出约束

- 工具返回的是可直接发送的 HTML 日报。拿到结果后不要再自行改写、总结或压缩。
- 不要逐条暴露隐私消息；输出应聚合、去重、保留必要匿名化。
- 如果工具返回“匹配到多个群”“未找到群”“消息不足”之类错误，直接把工具结果作为回复基础，不要编造报告。
- 不要把工具内部 SQL、缓存命中、LLM 分支过程、源消息 ID 列表写给用户。
- 不要把 `group_analysis` 当作普通历史查询工具；查上一句、查某条记录、查表结构仍使用 `sql_analysis`。
