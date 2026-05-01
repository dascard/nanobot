# Group Analysis 对齐外部实现设计

## 背景

本地 `group_analysis` 已经复刻了外部项目的三路并发分析主干：

- 话题分析
- 用户称号
- 金句提取

但与 `SXP-Simon/astrbot_plugin_qq_group_daily_analysis` 相比，仍有几个明显缺口：

1. **消息清洗偏弱**
   - 只过滤了部分 game bot 命令、纯符号、纯 URL
   - 没有统一过滤 slash 命令、bot mention 指令、技术噪音

2. **统计层过薄**
   - 只有用户级基础统计
   - 缺少全局统计概览：消息数、参与人数、总字数、平均长度、最活跃时段

3. **报告结构过于简化**
   - 目前只有话题/活跃用户/金句
   - 外部实现还有明显的“统计概览”日报头部
   - 用户称号支持更丰富的字段（如 MBTI）

4. **参数未充分利用**
   - 本地 `instructions` 参数未真正参与筛选逻辑

## 边界

本次只对齐“工具级分析逻辑”，不引入以下内容：

- webui
- 图片模板系统
- 多平台适配器
- 自动调度/增量分析
- 群文件/群相册上传

## 目标

在保持当前 KT 工具架构不变的前提下，把 `group_analysis` 向外部实现靠拢为：

1. 更完整的消息清洗
2. 更丰富的统计摘要
3. 更结构化的 Markdown 报告
4. 支持轻量的时间窗口指令筛选
5. 补上平台无关的“聊天质量锐评”和“活跃度分布”表达
6. 修复 QQbot 侧“所有消息均自动撤回”的回归，只让临时进度消息自动撤回

## 方案

### 1. 清洗规则对齐

新增过滤：

- slash 命令：`/xxx`
- bot mention + slash 命令
- 纯 @mention 消息
- 过短文本
- 技术性噪音文本

保留当前已有的 game bot 过滤。

### 2. 统计层补强

新增 `_compute_group_statistics()`：

- `message_count`
- `participant_count`
- `total_characters`
- `average_message_length`
- `most_active_period`
- `hourly_counts`
- `emoji_count`

用户统计补充：

- `emoji_like_count` 不单独入用户统计，但群级统计会基于纯文本做轻量 emoji 估算
- 继续保留 `reply_ratio`、`night_ratio`

### 3. 输出结构对齐

报告改为：

- 标题
- 统计概览
- 活跃度分布
- 活跃用户速览
- 话题总结
- 活跃用户称号（表格，增加 MBTI 列）
- 聊天质量锐评
- 金句
- 页脚（生成时间）

### 4. 指令支持

让 `instructions` 支持简单时间过滤：

- `最近2小时`
- `最近6小时`
- `最近12小时`
- `最近1天`

不做复杂自然语言解析，只做高价值规则。

### 5. 聊天质量锐评

增加第四路 LLM 分析，输出结构化 JSON：

- `title`
- `subtitle`
- `dimensions[]`
- `summary`

本地报告只保留文本化结果，不引入外部项目的图片卡片模板。

### 6. QQbot 自动撤回边界

`send_target_message()` 默认不自动撤回。

只有以下两类消息显式设置 `retract_seconds`：

- SSE 过程中的临时进度消息
- 未来明确声明为临时消息的调用方

最终回复、Markdown 渲染图片、普通 push 消息默认保留。

## 验证

1. 单测验证清洗规则。
2. 单测验证统计函数输出（含 `emoji_count` / `hourly_counts`）。
3. 单测验证 Markdown 报告包含活跃度分布、统计概览、MBTI 列、聊天质量锐评。
4. 工具级测试验证 `_execute()` 仍然可正常返回富 Markdown 结果。
5. QQbot 单测验证默认不自动撤回，显式临时消息才撤回。
