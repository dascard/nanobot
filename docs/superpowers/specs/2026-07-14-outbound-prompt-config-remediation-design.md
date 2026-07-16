# 出站投递、Prompt 与配置治理整改设计

## 状态

- 日期：2026-07-14
- 状态：已批准，待按实施计划执行
- 设计依据：2026-07-14 运行时、调度、配置和部署审查
- 前置设计：
  - `2026-07-11-private-chat-recovery-and-delivery-design.md`
  - `2026-07-11-research-publication-and-ambiguity-design.md`
  - `2026-07-13-prompt-runtime-contract-remediation-design.md`
- 实施计划：`.codex/plans/outbound-prompt-config-remediation.md`

## 背景

上一轮整改已经建立入站幂等、聊天断连 outbox、主动外呼评估租约、严格 Prompt
flow、请求级 wire tool schema 和最终请求 metrics。但主动生成内容的链路仍没有形成
同级的可恢复提交协议：

- 后台定时任务在 QQ 投递前提交 `last_run_at`，失败后丢失已生成内容；
- 定时任务和主动外呼仍在调度协程中直接发 HTTP 请求，没有共同的持久 outbox、逐次
  attempt 或人工重放；
- QQ push 将全部 HTTP 4xx 压缩成 `False`，5xx 和网络异常压缩成 `None`，上层无法
  区分稳定配置错误、瞬态故障和结果不确定；
- `internal/private` 是真实的主动研究分支，却被 Prompt flow 的平台白名单整体拒绝；
- Prompt 跟踪把同一个 active 路径同时写成 runtime/default，且没有模板来源与基线漂移；
- 通用 Prompt Runtime 初始化只补缺失文件，合法但过时的 runtime override 可以永久
  遮蔽 canonical 修复；
- 记忆折叠 scheduler 与 AI 日报在配置命名上混用 `daily_digest`；
- 分类器真实路由、启动探测、Sentinel loader、敏感设置响应和启动日志存在多个来源；
- Compose worker 获得完整 `.env`，session summary 同时以内置线程和独立容器运行；
- 根测试环境没有声明 `pytest-asyncio` 与 asyncio 模式。

生成、入队、投递、重试、熔断和迁移必须成为服务端可查询、可恢复、可验证的状态机，
不能继续依靠调整调度间隔、吞掉异常或修改 Prompt 正文规避。

## 已核验结论

| 审查项 | 结论 | 本设计处置 |
|---|---|---|
| 投递前写入 `last_run_at` | 确认 | 拆分 attempt/success/status，run 与 outbox 原子提交 |
| 日报与主动外呼没有统一 outbox | 确认 | 新建 source-neutral outbox，不复用入站 claim identity |
| 稳定 4xx 反复生成 | 确认 | 结构化 outcome、同 payload 重试、持久 circuit、生成前 gate |
| 缺少逐次运行与投递记录 | 日报确认，外呼部分确认 | 新建 run/outbox/attempt，保留外呼评估日志 |
| `internal` Prompt 分支缺失 | 确认，实际为平台整体拒绝 | 增加 `internal/private` live branch 和 flow v2 迁移 |
| Prompt 路径失真 | 确认 | 统一 `TemplateResolution` 并持久化完整 map |
| required variables 只查多余变量 | 部分确认 | 补 memory-digest 双模板合同与非空约束 |
| Runtime 只补缺失文件 | 部分确认 | 保留 flow 安全迁移；新增 baseline、audit 和显式迁移 |
| `private_decision` 无模板 | 部分确认 | 当前是显式 code fallback；本轮仅收紧代码边界 |
| `daily_digest` 含义和来源分裂 | 确认 | 改名为 memory-digest scheduler，旧配置做兼容别名 |
| Qwen 探测与运行地址不同 | 确认 | 健康检查读取真实 route resolution，并分类故障 |
| sensitive update 回显原值 | 确认 | 统一 masked/configured 响应合同 |
| reset 返回 registry 默认值 | 确认 | 删除 DB override 后重新读取实际回退值与来源 |
| 启动日志输出敏感配置 | 确认 | 只记录 configured/not configured |
| 失败响应正文无限制写日志 | 确认 | 有界读取、结构化分类和敏感字段脱敏 |
| session summary 双 worker | 确认但有原子 claim 保护 | 增加 embedded/external/disabled 模式 |
| worker 加载完整 `.env` | 确认 | Compose 按服务显式传入所需变量 |
| `.env.example` 过时 | 确认 | 删除 Dify，补当前非敏感配置与迁移说明 |
| 异步测试依赖缺失 | 确认 | 声明 `pytest-asyncio`，根配置固定 strict 模式 |
| Sentinel 默认路径不一致 | 确认 | loader 使用唯一配置解析 |
| 构建版本显示 unknown | 部分确认 | 启动日志复用 env-first resolver；保留 git fallback |

## 目标

1. 每次主动内容生成都有不可变 occurrence key、逐次 generation attempt、生成状态和错误记录。
2. 生成成功后，payload 在同一事务中进入不可变 outbox；网络重试复用 payload，不再次
   调用模型。
3. 每次 HTTP 尝试独立记录响应码、错误分类、耗时和脱敏摘要。
4. 稳定配置型 4xx 打开持久 circuit，在人工关闭或配置 revision 变化前阻止继续生成。
5. 保留已有 `ChatDeliveryOutbox` 的入站 claim 唯一性和断连恢复语义。
6. `internal/private` 进入严格 Prompt flow；`internal/group` 继续 fail closed。
7. Prompt trace 区分 active/default/runtime 路径、来源、hash、基线和漂移状态。
8. 所有真实在线 task 模板都有 required input/output 合同或显式 dormant 声明。
9. runtime 模板升级可 audit、plan、apply、resolve 和 rollback。
10. 配置解析、健康检查、敏感响应、日志和 worker 部署遵循单一来源和最小权限。

## 非目标与固定边界

- 不修改 QQbot、OneBot/NapCat、`/nanobot/push` 请求协议或 CQ renderer。
- 不承诺 exactly-once。QQbot 没有接收端幂等键，响应未知时仍只能提供可审计的
  at-least-once/ambiguous 语义。
- 不修改身份模板、人设、图片提示和其他 Prompt Markdown 正文。
- 本轮不创建或激活 `tasks/private_decision.md`；只明确 code fallback 并收窄异常捕获。
- 不把现有 `ChatDeliveryOutbox` 改成通用表，也不伪造入站 `message_id`。
- 不读取、修改或迁移工作区 `nanobot.db`；测试只使用内存或临时 SQLite。
- 不记录具体超级用户账号、完整 target ID、完整消息正文、token 或密钥。
- 不自动覆盖来源不明的 runtime override；无法证明基线时必须显式迁移。

## 总体架构

```text
调度/外呼评估
  -> OutboundRun 领取生成权
  -> OutboundGenerationAttempt 记录每次模型调用
  -> 事务外调用模型
  -> fenced 事务提交生成结果 + immutable OutboundDeliveryOutbox
  -> 独立 worker 领取投递租约
  -> 事务外 HTTP
  -> fenced 事务结算 Attempt + Outbox + Run + source projection + Circuit
```

核心原则是生成与投递解耦、出站 payload 不可变、状态结算有 owner fencing。调度器只负责
生成并入队，worker 不拥有模型调用能力。

## 通用主动出站模型

### `OutboundRun`

`OutboundRun` 表示一次确定的“生成并准备投递”业务运行。它不保存接收端凭据，也不承担
HTTP 重试。

| 字段 | 语义 |
|---|---|
| `id` | 内部主键 |
| `source_type` | `scheduled_task`、`proactive_outreach` 等稳定枚举 |
| `source_id` | 来源对象的内部标识，不进入普通日志 |
| `occurrence_key` | 只由来源身份和不可变触发 occurrence 派生的确定性键 |
| `source_revision` / `task_kind` | 领取时的定义 revision 和任务类型快照，不参与 occurrence 唯一性 |
| `source_snapshot_json` / `source_snapshot_sha256` | 领取时的完整定义快照与完整性 hash，保证来源被编辑或删除后仍可恢复生成 |
| `scheduled_for` / `trigger_type` | 逻辑调度槽与触发方式 |
| `status` | `claimed`、`generating`、`queued`、`delivering`、`succeeded`、`failed`、`blocked`、`ambiguous`、`succeeded_after_ambiguous_replay` |
| `claim_owner` / `claim_token` | 生成权 owner 与 fencing token |
| `claim_expires_at` | 崩溃恢复租约 |
| `attempted_at` | 本轮首次尝试生成时间 |
| `generated_at` | payload 生成完成时间 |
| `succeeded_at` | 已确认投递成功时间 |
| `failure_type` / `failure_summary` | 有界、脱敏的生成或最终投递错误 |
| `created_at` / `updated_at` | 审计时间 |

唯一约束为 `(source_type, source_id, occurrence_key)`。定时任务 occurrence 只由任务 ID 和
规范化 `scheduled_for` 构成；目标、模板和任务 revision 均为快照，不能通过窗口内编辑绕过
唯一约束。手动运行必须提供请求幂等键，主动外呼复用候选的稳定幂等键。租约过期后只能使用
新的 fencing token 恢复，旧 owner 的提交必须失败。

所有出站审计摘要统一限制为 1000 字符；服务层先脱敏再截断，数据库 CHECK 只作为最终防线。
非循环的账本审计关联使用 `NO ACTION` 外键且禁止级联删除，SQLite 生产连接和迁移连接均启用
`PRAGMA foreign_keys=ON`。`run.active_outbox_id` 与 `outbox.run_id` 会形成 SQLite 无法安全排序的
循环外键，前者只保留为 CAS 指针，由状态机验证 active outbox 属于同一 run。来源投影沿用旧表、
只增加关联列与查询索引，不为此重建旧表。

同一 occurrence 领取后再编辑、禁用、删除任务或更换目标，不会创建第二个 run。未开始投递的
旧 outbox 可以按显式规则取消或 supersede；`leased` 和 `ambiguous` 不得自动 supersede，必须
保留未知投递风险。

### `OutboundGenerationAttempt`

每次真实模型调用写入不可变 generation attempt，记录 `run_id`、单调 `attempt_no`、owner/token、
开始/完成时间、结果、模型追踪 ID、内容 hash 和有界错误摘要。调用模型前先持久化 attempt；
恢复 owner 不得覆盖旧 attempt。生成租约过期且没有 outbox 时可以创建下一次 attempt，但每次
模型消耗都可审计。

### `OutboundDeliveryOutbox`

`OutboundDeliveryOutbox` 保存生成完成后的不可变投递事实。

| 字段 | 语义 |
|---|---|
| `id` / `run_id` | outbox 主键与所属运行 |
| `idempotency_key` | 服务端稳定键，供日志、重放和未来接收端幂等使用 |
| `destination_snapshot_json` | 入队时不可变的投递目标快照；来源编辑或删除后仍可恢复 |
| `destination_fingerprint` | 日志和管理端使用的不可逆目标指纹 |
| `target_type` | 私聊、群聊等目标类型快照 |
| `endpoint_key` | 不包含密钥的逻辑出口标识 |
| `payload_json` / `payload_sha256` | 不可变 payload 与完整性 hash |
| `status` | `pending`、`leased`、`retry_wait`、`delivered`、`failed`、`blocked`、`ambiguous`、`cancelled`、`superseded` |
| `lease_owner` / `lease_token` / `lease_expires_at` | worker 投递租约与 fencing |
| `next_attempt_at` / `allocated_attempt_count` | 重试时间与已原子分配的 attempt 序号 |
| `request_started_count` | 已越过 durable request boundary 的真实网络尝试数 |
| `max_attempts` / `retry_deadline_at` | 最大网络尝试次数与最大重试时长 |
| `last_error_type` / `last_error_summary` | 最近一次有界、脱敏错误 |
| `delivered_at` | 已确认接收端成功时间 |
| `cancelled_at` / `cancel_reason_type` | 取消时间与结构化原因 |
| `replay_of_outbox_id` / `replay_sequence` | 人工重放谱系 |
| `created_at` / `updated_at` | 审计时间 |

`idempotency_key` 全局唯一，`(run_id, destination_fingerprint, replay_sequence)` 唯一，其中原始
outbox 的 sequence 为 0。同 key 不同 payload hash 或 destination snapshot 必须硬失败。人工
重放不修改旧记录，而是创建新 key、新 leaf 和相同 payload hash；`ambiguous` 默认不自动重放，
管理员必须明确确认重复投递风险。

run 保存 `active_outbox_id` 和 `has_ambiguous_ancestor`。创建 replay 时必须 CAS
`active_outbox_id`，同一 leaf 即使使用不同 manual request key 也只能产生一个后继。若
ambiguous 后人工重放成功，旧 outbox 与 attempt 保持原状，run/source 进入
`succeeded_after_ambiguous_replay`，不能伪装成无风险的普通成功。

### `OutboundDeliveryAttempt`

每次网络尝试写一条不可变 attempt：领取事务原子递增 `allocated_attempt_count` 并把新值作为
`attempt_no`，随后先持久化 `started`。请求完成后由仍持有相同
lease token 的 worker 结算为 `succeeded`、`transient_failure`、`permanent_failure` 或
`ambiguous`；未越过发送边界的恢复/取消分别结算为 `abandoned_before_send` 或
`cancelled_before_send`。记录包括尝试序号、HTTP 状态码、结果分类、耗时、开始/完成时间和有界
脱敏摘要，不保存响应正文、凭据或完整目标。

attempt 的 `(outbox_id, attempt_no)` 唯一。结算事务同时更新 outbox、run 和来源投影；
worker 丢失租约时不能覆盖新 owner 的状态。attempt 在发起请求前持久更新
`transport_phase=request_started`：该边界之前崩溃可以安全重领；边界之后无法证明未发送时，
一律结算为 `ambiguous`。HTTP 客户端进一步记录 connect/write/read 阶段，但不把推测当事实。
安全重领前，旧 `started` attempt 结算为终态 `abandoned_before_send` 或
`cancelled_before_send`。只有跨越 durable boundary 时才递增 `request_started_count` 并消耗
`max_attempts`；分配序号本身不消耗网络重试预算。

### `OutboundDeliveryCircuit`

跨进程阻止稳定失败继续消耗模型需要持久 circuit，不能只依赖 worker 内存。

| 字段 | 语义 |
|---|---|
| `scope_type` | `endpoint`、`destination` 或 `payload_contract` |
| `scope_fingerprint` | 与 scope 类型匹配的不可逆 hash |
| `config_revision` | 本次请求实际使用的持久非敏感配置 revision |
| `status` | `closed`、`open` |
| `reason_type` | 结构化错误类型 |
| `opened_at` / `updated_at` | 状态时间 |
| `opened_by_attempt_id` | 打开 circuit 的审计来源 |

出口鉴权或路由错误打开 endpoint scope，对所有来源生效；目标不存在或被拒绝只打开
endpoint+destination scope；可证明与固定 envelope/schema revision 有关的结构错误打开
payload-contract scope；单个正文长度、内容或目标数据导致的 400/413/422 只终止该 outbox，不
污染共享 circuit。瞬态故障只使用 outbox 的次数/时间预算，不建立第二套 transient circuit。
配置 revision 变化视为新的 closed scope，不复用旧 circuit；同一 revision 的稳定 circuit 只能
人工关闭。

生成前和 worker 领取前都必须检查适用 circuit。`open` 时 producer 只登记 blocked run，worker
把已排队记录保持为 blocked/hold；producer 在 circuit 确认 closed 前不得调用模型。

配置 revision 不从 URL、token 或密钥 hash 推导。QQ 出口使用持久单调版本或显式
`NANOBOT_QQ_PUSH_CONFIG_REVISION`；相关 Admin 设置更新时只递增该版本，无关设置不改变它。
环境配置变化但未提升 revision 时保持旧 circuit 阻断，要求管理员显式 reset，不能静默绕过。
outbox、delivery attempt 和 probe 都快照实际使用的 revision；旧 attempt 的 401 只能打开旧
revision circuit，不能污染并发轮换后的新配置。

### `OutboundDeliveryControl`

每个 producer `source_type` 有且仅有一行持久控制记录，保存 `mode`、`cutover_epoch`、
owner/version 和更新时间。mode 固定为：

```text
legacy_direct
outbox_hold
outbox_active
outbox_draining
```

首次迁移为 `scheduled_task` 和 `proactive_outreach` 分别创建一行 `legacy_direct` 控制记录，初始
`cutover_epoch=0`、`protocol_version=1`、`writer_version=0`。`effective_from` 记录初始化时间；
只有后续切换 CAS 才要求它绑定严格未来的 occurrence 边界，不能用会随时间失真的数据库 CHECK
表达“始终位于未来”。

旧 producer 和新 producer 在同一 DB 事务中读取并 CAS 对应 source control，确保每个 source 在
一个 epoch 只有一个写者。`outbox_hold` 阻止旧直推、允许新 producer 入队但 worker 不发送，
用于安全切换和回滚准备。所有 run/outbox 记录领取时的 `cutover_epoch`。

从 `outbox_active` 回滚必须先 CAS 到 `outbox_draining`：旧/new producer 都停止创建 occurrence，
worker 只消费切换时已存在且 epoch 匹配的 `pending/retry_wait/leased`。存在 `ambiguous` 时拒绝
自动切回；队列 drain 或显式取消到安全终态后，才能以未来 `effective_from` 进入新 epoch 的
`legacy_direct`。`outbox_hold` 不承担 drain。旧 worker 不得消费新 epoch，旧 direct producer 也
不得绕过 control。

legacy direct 不是账本外旁路：兼容 producer 也必须先领取同一个 `OutboundRun` occurrence，记录
`delivery_mode=legacy_direct` 和结果，再执行直推。occurrence 唯一约束不包含 epoch，因此同一
cron slot 无论前向切换或回滚都不能再次生成。control 记录 `effective_from` 和协议版本；切换只
能绑定严格未来 slot。

上线分两阶段：先把具备 control/occurrence 协议的兼容代码部署到全部实例并保持
`legacy_direct`，确认旧二进制全部退出、旧 writer lease 过期后，才允许设置未来 cutover。
混合协议版本期间禁止 CAS；单实例 Compose 通过先完整重建 server、再运行 cutover 命令实现该
barrier。

### 数据库硬约束

状态转换由代码表驱动，未知状态或未列出的逆向转换一律拒绝。数据库至少建立
`(status, next_attempt_at)` due index、`(status, lease_expires_at)` lease index、run/source 索引和
circuit scope/revision 唯一索引。`leased` 必须同时具有非空 owner/token/expiry，其他状态必须
清空租约；terminal 状态不得再次领取；`delivered` 必须具有 `delivered_at`；attempt number 和
generation attempt number 均从 1 单调递增。迁移器对同名畸形表、缺失 CHECK 或错误唯一索引
fail closed，不能只依赖 ORM 声明。

## 生成与投递事务

1. 调度器计算不可变 `occurrence_key`，在短事务内校验 delivery control、检查适用 circuit，
   再插入或领取 `OutboundRun`。任务 revision、目标和定义只在首次领取时快照。
2. 已打开 circuit 时将 run 标为 `blocked` 并结束；稳定 circuit 关闭前不得生成。
3. 在短事务内原子分配并持久化 `OutboundGenerationAttempt(started)`，再在事务外调用模型。
   生成失败只 fenced 结算 generation attempt 和 run，不创建 outbox。
4. owner 使用 generation token 在一个事务中结算 attempt、写入生成结果摘要和不可变 outbox，
   并将 run 改为
   `queued`。只有这一步成功后才认为内容已持久保存。
5. 独立 worker 先校验 delivery control、circuit 和重试预算，再领取到期 outbox；领取事务原子
   分配 attempt number 并写 `started` attempt，随后在事务外调用 HTTP。
6. worker 根据结果分类，在一个 fenced 事务中结算 attempt、outbox、run、来源投影和
   circuit。
7. 瞬态故障只修改 `next_attempt_at`，下一次仍发送同一 payload；任何网络重试都不得调用
   模型重新生成。

生成租约过期且尚未生成 payload 时可以重新生成；一旦 outbox 存在，恢复路径只能继续投递
该 payload。若无法证明请求是否到达接收端，则进入 `ambiguous`，禁止静默自动重放。

## HTTP 结果分类

| 类别 | 条件 | 动作 |
|---|---|---|
| 成功 | HTTP 2xx 且响应满足出口合同 | outbox/run 成功，更新来源成功时间 |
| 瞬态 | 408、425、429、500、502、503、504、明确发生在发送前的连接/DNS 故障 | full-jitter 退避；429 优先采用有界 `Retry-After` |
| 稳定 endpoint | 401、403、endpoint 404/405、415、501、505 | 终止 outbox，打开 endpoint circuit |
| 稳定 destination | 接收端明确报告目标不存在、拒收或已删除 | 终止 outbox，打开 destination circuit |
| 单 payload 失败 | 400、413、422，且错误只与本 payload 有关 | 终止 outbox，不打开共享 circuit |
| 不确定 | 已发送后读超时、连接重置、worker 租约过期且无法证明结果 | 标记 `ambiguous`，等待人工处置 |

QQ push 客户端返回结构化 `DeliveryOutcome`，至少包含 `category`、`error_type`、
`status_code`、`retry_after_seconds`、`duration_ms` 和 `safe_summary`。兼容调用方可以在一个
发布周期内通过显式适配器得到旧三态值，但新出站链路不得使用布尔值推断错误类别。

响应正文在流式读取层达到固定字节上限后立即停止，不能先调用无限制 `response.text()`。正文
只用于提取白名单结构化错误码，任意 `detail`、未知字段、原始键值和截断片段均不得进入
`DeliveryOutcome` 或日志；非空正文只记录固定省略标记。异常对象文本同样不得进入诊断信息，
只按结构化 `error_type` 映射固定安全摘要。结构化日志只包含错误类别、状态码、出口逻辑名、
payload hash 前缀和内部记录 ID。

每个 outbox 同时受 `max_attempts` 和 `retry_deadline_at` 限制。超过任一预算后进入
`failed/retry_exhausted`，只能通过人工重放创建新 leaf；不能无限退避。默认退避使用有上限的
full jitter，稳定 5xx 不进入无限重试。

## 来源接入

### 定时任务与 AI 日报

`ScheduledTask` 增加 `last_attempt_at`、`last_success_at`、`delivery_status`、`last_run_id` 和
有界 `last_error_summary`。调度器触发时只更新 attempt/run 状态；只有 worker 已确认投递
成功，才更新 `last_success_at`。

旧 `last_run_at` 在一个兼容周期内保留为已弃用的 `last_attempt_at` 投影，维持原有“最近尝试”
语义，但不再参与成功判断。迁移不能把历史 `last_run_at` 当成成功证据：已有记录将其复制到
`last_attempt_at`，同时初始化为 `delivery_status=legacy_unknown`、`last_success_at=NULL`。
下一次真实成功只更新 `last_success_at` 和成功状态；兼容字段继续随 attempt 更新。

同一任务的 occurrence key 只由任务 ID 和规范化计划触发时间决定。目标会话、prompt、任务类型
和定义 revision 只做不可变快照；进程重启、重复 tick 或同一窗口内编辑任务都会命中同一运行，
不重复生成。编辑/禁用后，只有 `pending/retry_wait` 可 fenced 取消；`leased/ambiguous` 保持人工
处置。API 和工具的成功判断只读取 `last_success_at/delivery_status`，不得把兼容
`last_run_at` 当成功或去重依据。

### 主动外呼

现有评估租约、候选 CAS 和 `ProactiveOutreachLog` 保留，负责“是否应当外呼”的决策审计。
候选 fenced CAS、唯一 run 建立、`outbound_run_id` 关联和来源状态投影必须在同一事务中完成；
任何一侧都不能提前提交。内容生成成功后，发布责任转交给通用 outbox。worker 结算来源投影时
同时校验 candidate version/fence，过期结算不得覆盖新候选，最终状态为 `sent`、`failed`、
`blocked`、`ambiguous` 或 `sent_after_ambiguous_replay`。

旧的 `sending` 和 `ambiguous` 记录迁移为确定的 `legacy_ambiguous_hold`，不自动创建 run/outbox
或可发送 payload；管理端显示来源、时间和风险，并要求人工 resolve。迁移重复执行不得改变该
状态。不能假设未发送并自动重放。
外呼 circuit 打开后，评估可以被记录为跳过，但不得调用研究或聊天模型生成正文。

history clear 与通用 outbox 使用 fenced 取消协议：`pending/retry_wait` 可以取消；已经领取但尚未
越过 `request_started` 边界的记录由 owner 安全取消；越过该边界或已 ambiguous 的记录保留为
ambiguous 审计；delivered 永久保留事实。claim、HTTP 和 settlement 三个竞态点均不得把取消
后的旧内容重新标为普通成功。

### Worker 与人工操作

新增独立 outbound delivery worker；它只读取已持久化 payload，不加载模型客户端。worker
使用短租约、稳定 worker ID、有界批次、指数退避和 owner fencing。领取、期限终结与过期租约
恢复都必须显式限定 `endpoint_key`；QQ worker 不得改写其他 transport 的 outbox、attempt 或 run。
HTTP 返回后重新读取完成时刻，再以该时刻校验租约和 retry deadline、计算退避并写入结算时间。

QQ 出口当前只接受 `qq-envelope-v1`：目标快照必须包含一致的 `target_type/target_id`，payload
必须是 canonical JSON 且 SHA-256 匹配，并能由既有 QQ renderer 从 `messages` 或 `reply` 渲染出
非空正文。合同损坏在 request boundary 前以 `payload_contract` 永久失败结算并打开对应 circuit，
不得把历史 `content` 字段当作隐式兼容别名。

管理 API 至少支持：按状态和来源查看计数、查看单条 run/outbox/attempt 的脱敏审计信息、
关闭 circuit、重试可安全重试的失败、确认后重放 ambiguous，以及取消未投递记录。API 不
返回完整 target、完整 payload、响应正文或凭据。所有人工动作写审计事件。

## Prompt Runtime 治理

### Live 分支与 flow 迁移

支持的 live 分支固定为：

```text
qq/group
qq/private
web/group
web/private
internal/private
```

`internal/group` 和未知平台继续 fail closed。`internal/private` 经过 `base_contract` 和私聊
节点，接受主动研究等内部调用所需的 runtime context，但不获得 QQ 平台节点。

canonical、内置默认和 runtime 的 `chat/flow.json` 使用可验证的 flow schema version 升级。
安全迁移只补充可证明未冲突的核心边；如果 runtime 修改了同一核心条件或节点，启动严格审计
失败并要求显式迁移，不静默覆盖自定义字段。flow v2 的 `plan/apply` 必须与 canonical 合同同一
任务交付；只有 runtime 已显式 apply 并通过五分支 strict audit 后，才能启用
`internal/private` live matrix。真实 `build_prompt_runtime` 和 Bridge 研究链路必须证明
`platform=internal` 最终进入编译器，不能只用 fake bridge 测试。

本次窄迁移只接受已经包含规范 `session_guidance` 节点及关系的当前 v1 基线，允许的语义变化
仅为顶层版本号和唯一 `base_contract -> private_policy` 核心边。更旧且缺少该节点的 Flow 必须
先完成上一阶段迁移；本次 `plan` 在创建计划文件前明确拒绝并保持 runtime、计划目录和备份目录
不变。跨任意历史版本的组合迁移由后续 baseline/三方合并治理提供，不能在本迁移中静默猜测。

`plan` 记录采用封闭字段集和严格 JSON 类型，plan ID 绑定迁移类型、runtime 绝对路径、源/目标
字节摘要、源/目标版本和变更状态，不保存 Flow 正文。`apply` 在共享写锁内重新读取 runtime：
当前摘要等于源摘要时重新派生目标并精确备份、原子替换；等于目标摘要时重新解析并验证 v2
合同和规范序列化字节，随后以 `already_applied` 幂等返回；其他摘要一律视为 stale 并零写入。
同一计划的顺序重放和并发双 apply 最多产生一次 runtime 写入和一份备份。计划 ID 路径穿越、
符号链接计划目录和符号链接备份目录均在 runtime 写入前拒绝。

### 模板来源与追踪

统一解析器返回 `TemplateResolution`：

```text
template_key
active_source
active_path
runtime_path
default_path
active_sha256
runtime_sha256
default_sha256
baseline_version
drift_status
```

不存在的路径使用 `null`，不能用 active 路径伪装 runtime/default。`PromptPlan.debug` 按
flow node 保存完整 resolution map；`AgentRun` 和 `PromptRenderLog` 持久化同一结构化 map，
路径标量兼容字段只代表 base contract。`prompt_source` 取 `runtime`、`default`、`mixed` 或
`built_in`。既有 `prompt_sha256` 继续表示最终 `messages + tools` 请求信封，base contract 的原始
文件摘要从 resolution map 的 `active_sha256` 读取，禁止复用或改义。旧 `template_paths` 在一个
兼容周期内保留，新增代码只读取 resolution map。路径 hash、baseline hash 和 drift 比较的输入
域统一为包含 frontmatter 的原始文件字节，不能复用渲染正文 hash。

### 输入合同

`TaskContract` 同时声明：

- `required_variables`：模板正文必须引用的变量；
- `required_call_values`：调用方必须提供且不得为 `None` 的变量；
- `non_empty_call_values`：调用方必须提供非空字符串或非空集合的变量；
- 输出 schema、解析失败策略和允许的定向重试次数。

在线启用前，registry 启动审计验证模板引用、输出 parser 和 invocation manifest 一致；动态
调用值无法在启动期证明，必须由所有 active task 共用的 invocation wrapper 在每次 render 前
校验。manifest 快照测试确保每个 live task 都通过该 wrapper，不能绕过合同直接渲染。

记忆摘要的 system 与 user 模板纳入合同；user 模板要求 `date`、`session_id`、`source_id`、
`source_type`、`source_range`、`message_count` 和 `digest_source`，其中会话、来源、范围和摘要正文
不得为空。当前 canonical/runtime 正文已引用这些变量，本轮只增加代码合同和调用路径验证，
不修改 Prompt Markdown。

invocation wrapper 返回带类型的失败。关键动态值缺失或为空时不得调用 LLM；调用方保留未完成/
可重试处理状态，并返回现有确定性摘要或显式失败，不能把日志标记为已完成。模板文件缺失、
模板合同错误、动态输入错误和模型输出错误分别记录，不能统一吞掉再用 fallback prompt 调模型。

`private_decision` 本轮继续标为 `code_fallback_only`。只捕获“模板不存在”这一预期分支；
模板读取错误、渲染错误、模型网络错误和 parser 错误分别记录并按各自策略处理，不能被一个
宽泛异常静默吞掉。`clients/classifier_client.py` 中任务渲染和模型调用的宽捕获都属于整改范围；
只有业务边界的唯一 owner 可以把预期网络故障或非法模型输出转换为结构化降级，`TypeError` 等
编程错误必须传播。

### Runtime 基线与显式迁移

模板根目录之外保存基线清单和只读 content-addressed blob store。清单记录模板 key、canonical
version、上次安装的 canonical hash、对应 baseline blob hash、当前 canonical hash、runtime
hash、修改来源和最近迁移记录；blob 按完整 SHA-256 命名并在读取时校验内容。漂移状态固定为：

```text
in_sync
upgrade_available
local_override
diverged
runtime_missing
untracked_legacy
invalid
```

状态判定以经过完整性校验的 baseline 原始字节 `B`、runtime 原始字节 `R` 和当前 canonical
原始字节 `C` 为唯一输入，优先级固定如下：manifest/blob/模板合同损坏先判 `invalid`；没有
baseline 且存在 runtime 判 `untracked_legacy`；runtime 缺失判 `runtime_missing`；`R == C` 判
`in_sync`；`R == B != C` 判 `upgrade_available`；`C == B != R` 判 `local_override`；其余已接管
差异判 `diverged`。所有摘要覆盖含 frontmatter、换行风格在内的原始字节。`built_in` 只用于
非治理内置模板来源，不属于上述七态。

启动只执行 audit 和缺失文件的安全 provision：发现 drift 时输出结构化告警；关键 flow 或
active task 为 `invalid` 时 fail closed。启动不得对已有 runtime 做三方合并或覆盖。

既有部署首次接管必须显式执行：无 baseline 且 runtime 原始字节等于 canonical 时，允许
`adopt-in-sync` 写入基线；两者不等时保持 `untracked_legacy`，直到管理员选择 keep-runtime、
use-default 或提供 merged file。启动不得自动“猜测”旧基线。

显式 CLI 提供 `audit`、`plan`、`apply --plan-id`、`resolve` 和 `rollback`：三方比较使用已校验
baseline blob 中的旧 canonical、当前 runtime 与当前 canonical；plan ID 绑定所有输入 hash，
apply 前重新校验，持锁、备份并原子替换。baseline blob 缺失、hash 损坏或存在冲突时零写入，
由管理员选择保留 runtime、采用 default 或提供已合并文件。

跨文件 apply 使用可恢复 journal，至少有 `prepared`、`files_installed`、`state_committed` 状态；
文件备份、目标 hash、manifest 变更和迁移谱系都写入 journal。进程在任一崩溃点恢复时，只能在
hash 完全匹配 plan 时完成 state commit。journal 为每个文件分别记录 before/after 存在性、完整
hash、CAS blob 和安装进度；恢复前先验证所有文件与 manifest 当前值都属于各自的
`{before, after}` 集合。出现第三种字节时必须零写入、保留 journal 并转人工恢复，禁止用备份
覆盖崩溃后的管理员修改。rollback 作为新的反向 journal，同时恢复 runtime 文件、baseline
manifest 和活动 lineage，并保留原迁移及回滚的不可变操作审计。

所有 live compile/preview 在一次完整编译期间持有全局共享治理锁；CLI apply、启动 provision、
Admin 模板 create/save/delete/reset 和 flow save 持有同一锁的排他模式。迁移中途退出后，读取方
发现 pending journal 必须 fail closed；启动和相同 plan 的后续 apply 可以在重新验证
before/after 后完成恢复。首次缺失文件 provision 同样先写 journal，不能在“runtime 已写、基线
未写”的崩溃窗口里靠字节相等自动认领历史文件。

plan ID 除 base/runtime/canonical/target hash 外，还绑定操作类型、merged 源路径与 hash、规范绝对
根、排序后的模板集合、manifest 原始字节 hash/revision 和 lineage head。apply 持排他锁后重读
并逐项验证；任何输入变化都在写 journal 前拒绝。baseline blob 使用完整 SHA-256 文件名和
write-once 安装，同名损坏对象不得被 adopt、provision 或 apply 静默覆盖。

## 配置、安全与部署

### 分类器路由与健康检查

运行客户端、启动探测和管理端探测共同调用一个 route resolver，解析 provider、base URL、
model 和启用状态。共享 `async probe_model_route()` 是唯一网络探测实现；lifespan 必须 await
异步 startup check，管理端也直接 await 同一函数，禁止在事件循环内 `asyncio.run`、同步阻塞或
复制第二套 probe。探测必须使用实际最终地址，不能再维护独立默认值。

探测返回固定状态：`not_configured`、`provider_disabled`、`timeout`、
`connection_refused`、`dns_error`、`auth_failed`、`client_error`、`server_error`、
`invalid_models_response`、`model_not_ready`、`network_error`。日志只记录分类和逻辑 route，
不输出带凭据 URL。

### 设置读取与敏感响应

`SettingsService.get_resolved()` 返回值及来源 `database`、`environment`、`legacy_database`、
`legacy_environment` 或 `default`。删除数据库 override 后必须使缓存失效，并重新走完整解析
链；reset 响应展示实际回退来源，而不是 registry 默认值。

GET、PUT 和 reset 共用一个设置响应序列化器。`sensitive=True` 时固定返回：

```json
{
  "value": null,
  "display_value": "****",
  "configured": true,
  "source": "environment"
}
```

未配置时 `configured=false`，仍不返回原值。启动日志和网络检查同样只输出“已配置/未配置”。

### 调度配置命名

记忆折叠使用以下 canonical 设置：

```text
memory_digest.scheduler_enabled
memory_digest.schedule_hour
MEMORY_DIGEST_SCHEDULER_ENABLED
MEMORY_DIGEST_SCHEDULE_HOUR
```

默认时刻保持当前记忆折叠语义的 4 点。`daily_digest.enabled/hour` 和 `DAILY_DIGEST_*` 只作为
一个发布周期的低优先级兼容别名，并输出一次弃用告警；AI 新闻日报使用独立命名，不再共享
这些键。兼容解析优先级固定为 `canonical DB > canonical env > legacy DB > legacy env > default`；
legacy DB 行不被启动过程覆盖或删除，重复解析/迁移幂等。scheduler 每轮通过
`SettingsService` 读取解析后的值，使管理端更新无需重启生效。

### Worker、路径与构建信息

- `NANOBOT_SESSION_SUMMARY_WORKER_MODE` 支持 `embedded`、`external`、`disabled`；裸进程默认
  `embedded`，Compose server 设置为 `external`，只由独立 worker 运行。
- Compose server 可以继续加载完整运行环境；各 worker 删除 `env_file` 并使用以下显式变量
  白名单，测试按集合精确比较：
  - outbound：`DATABASE_URL`、`LOG_DIR`、`LOG_LEVEL`、`QQBOT_PUSH_URL`、
    `QQBOT_PUSH_TIMEOUT`、`NANOBOT_QQ_PUSH_CONFIG_REVISION`、
    `NANOBOT_OUTBOUND_BATCH_SIZE`、`NANOBOT_OUTBOUND_LEASE_SECONDS`、
    `NANOBOT_OUTBOUND_POLL_INTERVAL`；
  - session-summary：`DATABASE_URL`、`LOG_DIR`、`LOG_LEVEL`、`NEW_API_BASE_URL`、
    `NEW_API_KEY`、`NEW_API_TIMEOUT`、`NEW_API_MAX_RETRIES`、`LLM_MODEL_SESSION_SUMMARY`、
    `LLM_MODEL_FAST`、`CLASSIFIER_API_URL`、
    `NANOBOT_PROMPT_DEFAULT_DIR`、`NANOBOT_PROMPT_RUNTIME_DIR`；
  - semantic-index：`DATABASE_URL`、`LOG_DIR`、`LOG_LEVEL`、`SEMANTIC_INDEX_ENABLED`、
    `RAG_EMBEDDING_PROVIDER`。
  每个 worker 的实际环境 key 必须是对应 allowlist 的子集且包含其必需集合；共同禁止
  `NANOBOT_ADMIN_TOKEN`、`NANOBOT_API_TOKEN`、`NANOBOT_SUPER_USER_IDS`、图片公开 token 和不在
  allowlist 的 provider key。
- `max_attempts/retry_deadline_at` 是 producer 创建 outbox 时写入的不可变预算。相应默认配置应在
  定时任务和主动外呼接入阶段由 producer 读取并快照；不得注入消费 worker 或在投递时覆盖已有
  outbox。未接入 producer 前不对外暴露无效配置。
- session-summary 的模型首选专用 `LLM_MODEL_SESSION_SUMMARY`；`LLM_MODEL_FAST` 仅保留一个发布
  周期作为兼容兜底。`SEMANTIC_INDEX_ENABLED=0` 时 semantic-index worker 必须在领取任务前停止
  消费。outbound worker 的 Compose 服务和白名单随真实 worker 在任务 11 交付，不提前创建空壳服务。
- Sentinel 唯一默认目录为 `./sentinel`，配置对象和 loader 共同调用同一 resolver。
- 构建信息 resolver 优先读取镜像注入的 commit、branch、build time，再回退到本地 Git；启动
  日志、健康接口和管理端复用同一结果。
- `.env.example` 删除全部 Dify key，补齐调度、分类器、Prompt Runtime、QQ push URL、图片公开
  地址、worker mode 和 Compose `GIT_*` 构建输入的固定 required key set；所有敏感示例值保持
  为空。示例不得写入空的 `NANOBOT_GIT_*` 运行时变量，避免覆盖镜像内已注入的构建元数据。
- 开发/测试依赖显式声明 `pytest-asyncio`，根 `pytest.ini` 设置 `asyncio_mode = strict`；静态部署
  测试必须读取这两个文件验证，不能依赖当前机器恰好已经安装插件。

## 迁移与兼容策略

1. 先上线新表、结构化 push outcome、安全日志和设置响应；delivery control 初始化为
   `legacy_direct`，兼容 legacy producer 先领取共享 occurrence 后仍直推，新 worker 不领取。
2. backfill 只建立 `legacy_unknown` / `legacy_ambiguous_hold` 状态，不制造历史成功事实或历史
   payload。
3. 完整重建所有 server 实例，确认只剩支持 control 协议的 writer、旧 writer lease 已过期；
   cutover 绑定严格未来 `effective_from`。混合版本或当前 slot 不允许切换。
4. 对单个 source 执行 CAS：`legacy_direct -> outbox_hold`。旧 producer 此时停止直推，新
   producer 可以生成并入队，worker 仍 hold；核对 occurrence、目标快照和队列后再 CAS 到
   `outbox_active`。
5. 先切定时任务并观察 drain，再用独立 cutover epoch 切主动外呼。每个 source 同一 epoch 只
   有一个 producer；禁止保留“失败时回退 direct push”的旁路。
6. 回滚先 `outbox_active -> outbox_draining`，同时停止新旧 producer；worker 只处理切换时已有
   且 epoch 匹配的记录。等待 `leased` 结算并 drain 或显式取消 `pending/retry_wait`；存在
   `ambiguous` 或不可证明的 started attempt 时拒绝自动切回。队列满足验证条件后才能以未来
   `effective_from` 进入新 epoch 的 `legacy_direct`。
7. 旧 push 三态适配器、`last_run_at` attempt 投影、旧调度配置和 `template_paths` 保留一个发布
   周期，使用处写弃用指标。
8. Prompt flow schema 先升级 canonical/built-in，再用显式安全迁移更新未冲突 runtime；冲突
   环境拒绝启动 live branch。
9. Compose 先设置 worker mode，再移除 server 内置 summary worker，防止升级窗口双运行。
10. 新审计表、generation/delivery attempt、ambiguous 和 replay lineage 永不因回滚删除或覆盖。

## 实施顺序

```text
安全日志、敏感响应与配置解析
  -> Prompt internal 分支、来源追踪与 task 合同
  -> Prompt baseline audit/迁移 CLI
  -> 通用出站数据模型与状态机
  -> 结构化 push outcome 和 worker
  -> 定时任务接入
  -> 主动外呼接入
  -> 管理查询、circuit 与人工重放
  -> Compose、依赖、示例配置和全链路验收
```

前两段先关闭泄露和确定性错误；通用 outbox 随后按完整状态机落地，避免在旧布尔 push 合同上
构建新的持久化语义。

## 测试策略

### 单元测试

- occurrence key 唯一性、generation/delivery lease fencing、目标/payload 快照不可变和 attempt
  唯一性；
- HTTP 分类矩阵、`Retry-After` 上限、有界脱敏摘要和 circuit revision；
- `last_attempt_at` 与 `last_success_at` 分离，失败不推进成功时间；
- sensitive GET/PUT/reset 永不包含原值，reset 正确体现环境回退；
- classifier route 在运行、启动和管理探测中逐字一致；
- `internal/private` strict compile 成功，`internal/group` 失败；
- active/runtime/default 路径和 hash 在 default、runtime、mixed 场景准确；
- required/non-empty 变量、输出 parser、runtime drift 状态和迁移 plan hash 校验；
- worker mode、Sentinel 路径和 build metadata 的单一来源。

### 状态机与并发测试

- 两个 scheduler 同时领取同一 occurrence，只有一个获得生成权；同一窗口编辑任务仍不二次
  生成；
- 旧生成 owner、旧投递 worker 和过期 lease 都无法结算；
- 每次模型调用都有独立 generation attempt；worker 在 durable `request_started` 前后崩溃的
  安全重试/ambiguous 边界可证明；
- 5xx/网络故障重试发送相同 payload hash，模型调用次数保持 1；
- endpoint 401 与 destination missing 打开不同 scope，单 payload 413 不污染共享 circuit；
- producer 与 worker 均遵守稳定 circuit；配置 revision 变化使用新的 closed scope，旧 attempt
  只按自身 revision 结算；
- 超过最大次数或截止时间进入 exhausted，不无限重试；
- ambiguous 默认不重放，人工确认后的 replay 保留 lineage，并聚合为带风险的成功状态；
- legacy/outbox 共用 occurrence；`legacy_direct -> outbox_hold -> outbox_active` 和
  `outbox_active -> outbox_draining -> legacy_direct` 在并发窗口内无双投，混合旧协议实例存在时
  拒绝切换。

### 集成与迁移测试

- 临时 SQLite 从旧 schema 升级，新字段不伪造成功时间；
- 定时任务生成、入队、失败重试、成功投影完整闭环；
- 主动外呼候选、评估日志、run/outbox 和最终状态可追溯；
- 旧三态 push 调用方在兼容期保持原行为，新 worker 只使用结构化结果；
- runtime 模板 in-sync 自动审计、local override 保留、diverged 零写入、rollback 恢复备份；
- Compose 配置只能启动一个 session summary worker；每个独立 worker 的环境均不含无关敏感
  变量。

## 任务 8 落地证据

通用出站持久层已经按本设计建立六张独立账本表，并给 `ScheduledTask` 与
`ProactiveOutreachLog` 增加兼容投影。ORM 建表与旧库迁移路径使用同一份冻结合同，严格核对列
顺序、类型、可空性、默认值、具名 CHECK、索引、外键和孤儿记录；发现同名畸形对象时迁移
fail closed，失败不会记录迁移版本。

数据库层额外固化了以下边界：

- 28 个 `DATETIME` 字段只接受 SQLAlchemy 可稳定往返的 UTC naive SQLite 文本格式；包含
  年月日、闰年、时分秒、微秒、NUL 隐藏尾部和 SQLite CHECK 三值逻辑的 `30,054` 个性质样例
  与 Python `datetime` 判定完全一致；
- run、generation、outbox、attempt、circuit 与 control 的状态、租约、fencing token、配置
  revision、网络预算和终态事实均由具名 CHECK 约束；
- `ScheduledTask.last_error_summary` 在 ORM 与旧库迁移两条路径都限制为 1000 字符；历史
  `last_run_at` 只回填 `last_attempt_at`，不伪造成功时间；
- 旧 proactive `sending/ambiguous` 迁移为 `legacy_ambiguous_hold`，迁移不创建 run/outbox，
  不触发任何网络调用；文件数据库升级前只创建一次快照。

新鲜验证结果为：任务文件 `133 passed`，schema/migration 联合矩阵 `204 passed`，全量
`3699 passed, 6 skipped, 0 failed`。两轮独立只读审查均给出
`0 Critical / 0 Important / 0 Minor / GO`；Ruff、`compileall`、`git diff --check`、Prompt
Markdown/QQ renderer 边界和敏感信息扫描均通过。任务 8 只完成持久层与迁移合同，生产者、
worker、HTTP 分类和 `last_run_at` 调用方切换仍按后续任务实施。

## 任务 12 落地证据

定时任务已从“生成后直接推送”切换为“领取 occurrence、冻结输入、生成并持久入队”。cron
occurrence 只由任务 ID 和规范 UTC 槽组成；手动执行要求调用方提供幂等键，持久身份只保存其
SHA-256 派生值。producer 在共享来源控制锁内读取最新任务，并用同一 cron helper 按上海时区
复核计划槽，因此任务更新成功后不会再登记旧 cron。正文生成发生在数据库事务外，成功与失败
均由 claim token、generation attempt 和有效租约 fenced 结算。

崩溃恢复分成两条互斥路径：没有任何 outbox 的过期 generation run 只使用首次来源快照、投递
合同、occurrence 和 revision 恢复；已有 `legacy_direct` leaf 则由专用兼容 drain 发送原始
payload，普通 worker 继续只领取 `outbox` 模式。兼容 drain 支持 pending、到期 retry_wait 和
blocked，持有 writer lease 与 cutover fence，单 leaf 普通异常只写安全诊断并继续批次；
`CancelledError` 不会被吞。任务更新、停用或删除会在同一来源锁内终结可安全取消的 blocked
run，已经进入可能投递区间的记录仍按不可撤销状态报告。

API 和模型工具的手动运行都要求幂等键，只返回 run/outbox 的安全状态，不返回目标或原始键；
legacy 兼容投递也必须先持久化 outbox 和 durable request boundary 后才执行 HTTP。生成超时与
普通异常分别记为 `generation_timeout` 和 `generation_error`，Bridge 清理后重新抛出，日志只
记录 task ID 与异常类型。

验证结果：Task 12 矩阵 `111 passed`，状态机/worker/定时任务 `182 passed`，出站 schema
`134 passed`，transport `608 passed`，工具 overlay `33 passed`，Bridge Prompt `20 passed`；
可写且排除数据库、凭据和运行文件的验证副本全量为
`4515 passed, 6 skipped, 0 failed`。两轮独立只读复审最终为
`0 Critical / 0 Important / 0 Minor / GO`。

## 验收标准

1. QQ push 失败时，已生成内容仍可从 outbox 恢复；网络重试不会重新生成。
2. 稳定 endpoint/destination 错误只阻断正确 scope，单 payload 错误不误封其他目标；producer
   和 worker 均不绕过 circuit。
3. 每次模型调用和网络 attempt 都有独立持久记录，普通成功、失败、ambiguous 和
   ambiguous 后重放成功不会互相冒充。
4. 定时任务失败不更新成功时间；历史 `last_run_at` 不被迁移为成功。
5. 五个 live Prompt 分支均通过 strict audit，`internal/group` 和未知平台的拒绝分支均有测试。
6. 任一 Prompt trace 都能定位实际 active 文件，并准确区分 runtime/default/mixed。
7. 启动审计能发现模板变量、parser 和 invocation manifest 不匹配；每次 render 能拒绝缺失或
   空的动态调用值。
8. runtime drift 在启动时可见，显式迁移使用完整 baseline blob，支持计划校验、损坏/冲突零
   写入和回滚。
9. 敏感设置、启动日志和失败日志中没有原值、凭据 URL 或无限响应正文。
10. 分类器、调度、Sentinel、worker mode 和构建信息均只有一个解析入口。
11. canonical/runtime Prompt Markdown 正文无意外差异，QQ 协议与 renderer 无差异。
12. 切换和回滚使用持久 delivery control 与 cutover epoch，升级窗口不存在 direct/outbox
    双写或 worker 越界消费。
13. 定向测试、`python -m pytest tests/ -v`、Ruff、`compileall`、`git diff --check` 和敏感
    信息扫描全部通过。

## 任务 14 第二阶段代码审查整改合同（2026-07-15）

本节收窄任务 14 中文代码质量审查发现的三个阻塞项。范围仅包含通用出站核心状态机、
Admin 出站路由和对应测试；不修改 Prompt Runtime、Prompt 模板、QQ push 协议或 renderer。

### Admin control transition 后释放临时 writer

Admin 仍以进程内随机 owner/token 获取临时 writer lease，并使用该身份完成合法 control
transition，但成功转换不能把 900 秒临时 lease 留给真实 producer。核心层新增独立的
`release_delivery_writer()` 状态机，合同如下：

- 输入包含 `Session`、`source_type`、当前 owner/token、protocol version、预期 writer version
  和显式 `now`；函数只 flush，不 commit 或 rollback；
- 在与 acquire/transition 相同的 source control 写锁下，校验 owner、token、protocol、有效 lease
  和 `expected_writer_version`；任一事实失效都抛 `OutboundFencingError`；
- 成功 CAS 清空 `writer_owner`、`writer_token`、`writer_lease_expires_at`，同时将
  `writer_version` 加一并更新 `updated_at`；返回 release 后的最终 writer version；
- Admin 原子流程固定为 acquire（版本 `V+1`）→ transition（版本 `V+2`）→ release（版本
  `V+3`）→ 写审计 → 单次 commit。release 或审计失败时，acquire、transition 和 release 全部
  rollback；
- Admin 响应和审计只返回 release 后最终 writer version，不返回 owner、token 或 lease 时间；
  commit 后不同身份的真实 producer 可立即 acquire/claim，无需等待临时 lease 到期。

不采用路由直接修改 ORM 字段，因为那会绕开 source lock 与 fencing；也不把 Admin 专属释放语义
折叠进通用 transition，以免改变非 Admin 调用者的 lease 生命周期。

### Legacy proactive resolve 的安全可发现性

新增 `GET /api/v1/admin/outbound-delivery/legacy-proactive`。查询只选择
`ProactiveOutreachLog.status == "legacy_ambiguous_hold"`，按 `created_at DESC, id DESC` 稳定
排序；分页合同为 `page=1`、`limit=50`，`page >= 1`、`1 <= limit <= 200`。

外层响应严格只有 `total`、`items`、`page`、`limit`。每个 item 严格只有：

- `id`
- `source_type`，固定为 `proactive_outreach`
- `status`
- `created_at`
- `source_revision`

`source_revision` 继续由冻结的 legacy 来源事实计算，是仅用于乐观并发控制的 opaque SHA；列表
不得返回或嵌入 `user_id`、message、grounding、idempotency key、目标、payload 或其他原始来源
事实。POST resolve 继续要求客户端回传列表中的 `created_at + source_revision`，核心 CAS 和
`cancel_without_replay` 唯一 resolution 均保持不变。

不取消 revision CAS，也不扩宽现有 proactive 管理响应，避免管理客户端无法证明观察版本或让
旧接口意外增加敏感字段。

### Admin 动作统一可替换 UTC 时钟

Admin 出站路由定义无参数 `_utc_now()`，返回 UTC naive `datetime`。replay 和 control
transition 都在请求入口读取该时钟，并把值作为显式 `now` 传给核心状态机；核心状态机默认时钟
合同不变。

Admin 出站测试 fixture 将路由时钟固定为测试常量 `NOW`。replay deadline 始终使用
`NOW + 1 day`，不得改成依赖测试执行日的动态 deadline；transition boundary 同样从固定 `NOW`
推导。聚焦回归通过替换核心 `_utc_naive` 证明：未显式传 `now` 时核心会看到 `NOW + 2 days`
并拒绝 deadline，而路由显式传入固定 `NOW` 后 replay 成功。

测试 fixture 必须显式关闭 `TestClient`，并在关闭客户端后清除 dependency override、删除临时
schema、释放 engine；不改变应用生产 lifespan。

### 错误、事务与验证合同

- release fencing、安全状态冲突继续走 Admin 固定 409 文案；参数错误走固定 422；未知错误或
  审计失败走固定 500，任何响应都不拼接底层异常正文；
- legacy GET 是只读脱敏查询，不写审计；resolve 仍在状态变更与审计同一事务中提交；
- 问题 A 先以 Admin transition 后真实 producer 无法立即 claim 的集成测试红灯，再补核心 stale
  CAS/成功 release 测试；
- 问题 B 先把 resolve 集成测试改为从 GET 获取 revision，确认 GET 缺失红灯；
- 问题 C 先以核心未来时钟、路由固定时钟的聚焦 replay 测试确认 route 未传 `now` 红灯；
- 三项分别绿灯后运行 Admin 出站、核心出站、Admin API、Admin proactive outreach 回归，以及
  目标文件 Ruff、目标模块 `compileall` 和 `git diff --check`；0 failures 才能声明完成。
