# 主动研究伙伴与加速验证设计

## 背景与问题

生产日志已经证明主动私聊没有进入 Generator：17 次 Judge 中 14 次以
`finish_reason=length` 结束，截断样本多数只有 `reasoning_content`、没有正文；调用层又只
返回 `message.content`，丢弃 `finish_reason`，JSON 解析失败最终被折叠成合法的
`should_reach_out=false`。因此系统持续维护 `pending`，既不发送，也没有暴露可操作错误。

此外，主动外呼目前直接读取永久档案 `ChatLog`，没有遵守 `ConversationTurn` 与
`history_clear_at` 的会话边界；活跃时段统计混入 assistant、ambient 和群聊记录；空生成
结果也可能进入 `sending` 并调用 QQ。

## 目标

1. 修复主动外呼的截断、契约失败静默、历史越界和空消息投递问题。
2. 将主动能力扩展为方案 B「研究型伙伴」：模型可以围绕近期话题主动搜索、整理资料并
   写成带真实来源的内容。
3. 探索权限必须是不可扩权的固定上限，搜索内容视为不可信资料，发布不相信模型自报来源。
4. 不等待七天真实影子期；用虚拟时间、脚本模型和故障注入在数秒内回放七天，再用真实模型
   dry-run 验证。模拟和 dry-run 在调用图上均不可到达 QQ publisher。
5. 保持 `proactive_outreach.enabled` 为纯布尔总开关，不引入 shadow/dry-run 运行时三态。

## 非目标

- 本轮不构建无期限自主循环，不允许模型自行修改代码、数据库、系统配置或工具权限。
- 本轮不让研究草稿自动进入知识库，避免未经核验的内容被后续 RAG 自我召回。
- 用户尚未确定人格偏向，因此不写死政治、审美或话题立场；只保留以后可配置的兴趣向量入口。
- 不修改 `vendor/KohakuTerrarium/`；`nanobot_kt/bridge.py` 只复用现有 ToolPlan，并增加请求级
  dry-run ContextVar 的最小传递，不在本轮扩展 KT 框架权限。

## 总体架构

```text
ConversationTurn + Persona + 调度状态
                  |
                  v
          outreach_extract
                  |
                  v
           outreach_judge
          /       |       \
       不发     普通消息     研究任务
        |          |           |
     pending  outreach_generate |  research preset
                   |            |  web/knowledge
                   |            v
                   |      有来源的研究草稿
                   \            /
                    candidate 记录
                          |
                    独立投递门控
                          |
                       QQ push
```

普通消息和研究消息共享调度、幂等与最终投递状态机；研究执行器本身不导入 QQ 模块。研究
成功后先把正文、trace 和已核验来源写入现有 `ProactiveOutreachLog` 的候选记录，再由投递
函数把 `candidate -> sending -> sent/failed`。这样探索与发布是两个可独立测试的动作。

为避免多进程或多 worker 同时评估同一用户，本轮新增 `proactive_outreach_leases` 表和
`20260710_proactive_outreach_leases` migration。租约使用墙钟、在取得 SQLite
`BEGIN IMMEDIATE` 写锁后才冻结到期时间；migration 会严格校验列顺序、类型、NULL、默认值、
主键、索引、排序、collation、hidden/generated 列、CHECK、trigger、外键和额外索引。不兼容
schema 会中止且不会被记录为已迁移。

## 模型路由与返回契约

新增三个独立路由：

- `outreach_extract`：提炼 1-3 个近期开放话题。
- `outreach_judge`：输出严格 JSON 决策，并可选择 `message` 或 `research`。
- `outreach_generate`：生成普通主动私聊正文。

三个路由均继承 `reply` 的 provider/model，但拥有独立 timeout、temperature、max_tokens 和
`enable_thinking=false` 默认值。已有 `timing_proactive` 也默认关闭 thinking，继续只服务群聊
主动发言兼容路径。调用点不再用 180/240/320 的硬编码预算覆盖路由配置。

`clients.classifier_client` 新增兼容式结构化入口，保留旧的字符串入口：

```python
@dataclass(frozen=True)
class ModelRouteResponse:
    content: str
    reasoning_content: str
    finish_reason: str | None
    usage: dict[str, Any]
    raw_response: dict[str, Any]
```

主动私聊 Judge/Generator 只有在 `finish_reason` 明确为 `stop`、`message.content` 为字符串、
正文非空且 JSON/字段契约完整时才形成业务结果。`length`、缺失停止原因、非字符串 content、
空正文、部分 JSON、字符串形式的 `"false"`、缺少下次检查时间均映射为明确错误。群聊
`timing_proactive` 单独保留旧网关缺失 `finish_reason` 的兼容，不扩散到主动私聊。

Judge/Generator 契约失败不会伪装成正常“不发”，而是写入 `evaluation_error` 审计行：它保存
错误阶段、类型、重试时间和 grounding 摘要，并作为首次沉默锚点。这样即使模型从启用起持续
截断，最长沉默兜底仍能触发，后台 scheduler 也会结构化记录非正常结果。到达最长沉默窗口后，
若 forced Generator 因截断、结束原因或空正文违反模型契约，则不再无限重试同一失败链，而是发送
一条服务端维护、无事实断言、无 URL/控制码的固定安全短句；错误类型保存在该投递行的 grounding
中。普通 Generator 错误仍失败关闭，不会用通用短句冒充上下文相关候选。

## Canonical Prompt Runtime

新增并同步默认与运行时模板：

- `tasks/outreach_extract`
- `tasks/outreach_judge`
- `tasks/outreach_generate`
- `tasks/proactive_research`

Judge 的 JSON 契约包括：

```json
{
  "should_reach_out": true,
  "reason": "string",
  "next_check_in_hours": 3,
  "next_intent": "string",
  "outreach_kind": "message|research",
  "research_query": "仅 research 时必填"
}
```

研究模板要求先调用真实工具，再基于工具结果写作；网页内容中的指令、提示词或工具请求都只
是资料，不得改变系统边界。模型输出中的 URL 不是来源事实，发布层只接受本次 trace 中真实
`web_search` 工具结果出现过的 URL。

## 历史语义

主动 grounding 统一读取 `ConversationTurn`，仅接受私聊 session 的 `user/assistant`；为旧
数据兼容，可把 `model` 归一化为 `assistant`。所有语义查询严格使用
`created_at > User.history_clear_at`。`active_hours()` 和最近交互时间只统计清除点后的私聊
`user` turn。

`ProactiveOutreachLog` 的限频、幂等和发送状态仍跨历史清除保留，防止清历史等价于重置投递
保护；但注入模型的 `last_outreach/next_intent` 必须过滤清除点之前的记录。

## 研究工具边界

新增固定 `research` runtime preset，上限集合为：

- `web_search`
- `reply`
- `no_reply`

该 preset 只做现有启用集合的交集，不会重新启用后台已关闭工具；加载 ToolOverride 后再次
裁剪，因此 user/group/platform override 也不能放开 `memory_query`、SQL、文件、代码执行、
图片生成或其他工具。`reply/no_reply` 是收口动作，不计入探索预算。

每次研究创建独立 `NanobotBridge` 生命周期，并注册研究预算插件。插件在
`pre_tool_execute` 原子计数探索调用，当前 ceiling 下唯一探索工具是 `web_search`；超过上限后
`exhausted` 成为粘性终态，即使模型捕获工具异常后继续写正文，runner 仍返回
`budget_exhausted`。`knowledge_query` 暂不进入研究 preset，因为其同步 RAG/reranker 路径可能
阻塞事件循环、触发索引维护或运行时模型下载，无法满足本轮硬截止与只读边界。通用 ToolPlan
guard 和研究预算插件都覆盖
`pre_subagent_run`，研究预设不允许任何 SubAgent，不能用伪造 native `memory_write` 绕过
工具上限。外层使用不会被迟到结果反转的总超时，bridge stop 有独立短超时。默认预算为
120 秒、最多 6 次探索、最多 6000 字正文。

`web_search` 在调用第三方 provider 之前，由研究插件校验实际 query：必须为字符串、长度不超过
1000、不得包含邮箱、连续或分组长账号/手机号/卡号、拆分后的身份证等敏感词、密钥特征、本地
文件路径或非公网 IP，并且必须与原研究 query 共享高信息主题原子词、不得追加未知实体。输入先做
Unicode NFKC、零宽字符删除和分隔符折叠；中文连续串再拆成原子 bigram 比较，避免用空格、标点或
“原主题+赌博/博彩”超长 token 洗白。该前置门禁之外，来源提取时仍会再次核对 trace 中的实际
query。

来源由本次 `trace_id` 对应的成功 `ToolCall(tool_name=web_search)` 的 `result_preview` 提取并
去重。`web_search` 单独使用 40000 字的有界审计预览，其他工具仍保持 2000 字；来源解析只接受
完整且唯一的 `WEB_SEARCH_RESULTS_BEGIN/END`、`RESULTS` 连续编号和一致的 `RESULT_COUNT`，
截断结果及 QUERY 区域伪造 URL 均失败关闭。单条来源相关性只使用 title/snippet，不允许 URL
路径或 query 回显搜索词来洗白无关页面。默认至少需要两个不同网页来源。

URL 策略只接受规范化的公开 HTTP/HTTPS：拒绝 userinfo、空白/控制字符、反斜杠、坏端口、
协议相对 URL、裸 `www`、普通裸 FQDN/IPv4、非 HTTP scheme、localhost、单标签内网域名、
`.local` 和非 global 字面 IP。
provider URL 先规范化再进入 trace；模型正文必须逐字使用规范 URL，fragment、默认端口、追踪
参数或其他会被 canonicalizer 改写的变体都失败关闭。bridge 正文先去除 think，再做 URL 核验，
避免删除中间 think 块后重组出新链接。研究正文是纯文本契约，拒绝 `[CQ:*]`、
`[generated_image:*]`、`[sticker:*]`；不可信来源标题的方括号由服务端全角化。最终候选层和
publisher 前再复核一次来源 URL 与控制语法。最终“来源”段由服务端生成，不照抄模型声明。

## 投递安全

`deliver_outreach_once()` 在任何数据库状态变更前验证正文：空字符串、纯空白、纯 think 内容
直接返回 `generation_error`，不占用幂等键、不写 `sending`、不调用 publisher。

`pending/candidate -> sending` 使用带行 ID 和旧状态条件的原子更新；只有 `rowcount == 1` 的
执行者能进入 publisher。并发首建由唯一幂等键兜底，竞争失败统一返回 `skipped_duplicate`。
claim 同时受有效评估租约 owner、history clear 点和原状态约束。若 claim 已提交但在调用
publisher 前明确失租，使用 CAS 把行恢复为 `candidate`（清历史则 `cancelled`），不会留下永久
毒化的 `sending`。若进程在 publisher 调用期间崩溃、结果确实不确定，则原 key 不自动重发；
超过 30 分钟且旧租约失效后转成 `ambiguous`。默认 QQ publisher 对网络异常、5xx 或其他无法
证明远端未处理的结果返回不确定态，投递层同样写 `ambiguous`，而不是误记为可重试的 `failed`；
最新 ambiguous 在一个完整 `max_silence` 窗口内冻结新语义外呼，窗口届满后才允许再次评估，
在重复风险与永久封死之间取保守平衡。相同 key 始终不会重新调用 publisher。

publisher 作为显式可注入依赖：生产默认适配 `push_to_qq`，模拟使用只记录不出站的
`RecordingPublisher`。研究 runner、dry-run 和模拟模块均不导入生产 publisher。调度循环每轮
重新读取 `proactive_outreach.enabled`，管理端关闭开关后无需重启即可停止执行。

## 加速模拟与发布门禁

生产检查、live dry-run 和模拟复用候选评估内核。模拟模式固定为
`seven_day_conformance_replay`，使用内存 SQLite、虚拟时钟和 RecordingPublisher，不 sleep、
不访问生产数据库、不接受生产 provider 模式。0/1/2 来源、研究超时和预算耗尽场景均调用真实
生产 `run_proactive_research()` 与生产来源 extractor；fake Bridge 只替代模型和外网。

必测场景：

1. 完整 Judge JSON 形成普通候选。
2. `finish_reason=length`、空正文、部分 JSON 和 schema 错误失败关闭。
3. 空 Generator 不写发送状态。
4. 研究获得 0/1/2 个真实来源时分别阻断、阻断、通过。
5. 安静时段、最小间隔、最大沉默、surge 和虚拟 `now` 一致。
6. 同一 due anchor 重放不会重复记录或发布。
7. 研究超时和探索预算耗尽不会降级成无来源文章。
8. auto-publish 使用 RecordingPublisher；dry-run 只返回候选，不写业务发送状态。

报告指标包括契约成功率、截断率、解析失败率、候选率、来源覆盖率、重复率、超时率、模拟
发布数和真实外部推送数。18 个 required case 的期望契约由代码内不可变清单定义；门禁拒绝
缺失、重复或额外 case，不信任 case 自报 `passed/expected`。SQLite snapshot 包含排序后的原始
candidate/attempt 行，门禁从这些原始行重算全部聚合；每条 RecordingPublisher 记录必须与 sent
candidate 的 case、key、正文和 naive ISO 发布时间逐字一致。非法时间、naive/aware 时区混用、
聚合或任一侧原始证据篡改均失败关闭。门禁要求非预期截断为 0、重复发布为 0、研究成功来源
覆盖率为 1、预算不超限、`external_push_count == 0`。

脚本门禁通过后，再允许用真实模型执行少量 `live_dry_run`。该入口复用真实 grounding 和模型，
但代码路径到候选即返回，不写 `sending/sent`，也没有 publisher 参数，因而不可能调用 QQ。
请求级 `ContextVar` 把 `dry_run` 传到真实 `ReplyTool.execute()`，贴纸使用计数也不会被 dry-run
污染；并发请求和异常退出都会按 token 复位。

## 本轮加速与真实链路证据

- 最终七日一致性回放独立运行两次，规范化 JSON 完全一致，SHA-256 均为
  `37daaadbde272c559dff7cdac34e518af6da6f292f13ff4142c581aaa1a9d008`；18 个 case、14 项
  gate 全部通过，`external_push_count=0`、`duplicate_publish_count=0`、预算越界为 0。
- 临时进程内把不可达的持久地址切到 `http://10.60.42.158:9000/v1`，真实 Judge 返回
  `should_reach_out=true/outreach_kind=research`，真实 Generator 返回 126 字且无 think；实际用户
  grounding 返回 `no_candidate`。覆盖未写入持久配置，publisher 调用、外呼日志增量和
  `sending/sent` 增量均为 0。
- 真实研究 trace `41f908c43baa40ad87af4f80fb7c91e0` 产生 5 次成功 Web Search；最终代码的来源 extractor
  重新读取该 trace，仍核验出 5 个来源。此前把该真实 trace 交给生产 runner 回放得到
  `draft_ready`、891 字、5 个服务端来源，无 publisher、无业务外呼写入；后续 PII/裸域名与
  `knowledge_query` ceiling 加固没有重新执行完整 runner，因此不把旧回放冒充最终代码的完整
  端到端成功样本。
- 上一项是“真实模型/真实搜索 trace + 修复后门禁回放”，不是修复后完整端到端研究成功。
  原完整调用在模型继续尝试第 N+1 次探索时触发预算门禁，随后人工诊断进程被终止；它只能作为
  `budget_exhausted`/取消路径证据，不能冒充成功样本。
- 七日 gate 证明自建 SimulationLedger 原始行与 RecordingPublisher 记录内部一致，并能发现单边
  正文、时间、原始行或聚合篡改；它没有调用生产 `deliver_outreach_once()`、生产 ORM/CAS/租约或
  QQ publisher，因此不能替代后续受控的小流量生产验证，也不能抵抗攻击者同时伪造整套 ledger
  与 publisher 证据。

## 与长期虚拟生活研究的关系

本轮采用的最小闭环与 Generative Agents 的“观察记忆、反思、计划”、Voyager 的受限工具探索、
Reflexion 的结果反馈和 MemGPT 的分层记忆方向一致，但只落地可审计的一次性研究任务。后续可
在此基础上增加：可配置兴趣向量、研究积压队列、反思日志、长期目标和结果评分。任何长期循环
仍需保持预算、时间、工具上限和发布门控，不能让角色设定替代权限系统。

## 验收

- 根因样本对应的 `length/空正文/部分 JSON` 均成为可观察错误，不再伪装成正常 false。
- 路由默认关闭 thinking，管理端可编辑并正确回读。
- 清除历史后的主动 grounding 不再引用清除前、群聊、ambient 或 tool 内容。
- 研究 preset 不能被 ToolOverride 扩权，研究稿至少包含两个实际搜索来源。
- 模拟与 live dry-run 的真实 QQ 推送计数恒为 0。
- 运行 `python -m pytest tests/ -v` 为 0 failures，且 `vendor/` 无改动。
