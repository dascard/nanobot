# Nanobot Server 阶段计划 Walkthrough

计划日期：2026-06-17
更新日期：2026-06-18
本轮计划写入日期：2026-06-18

本文记录当前长期目标的完整阶段计划，用于继续推进 `docs/todo.md` 中的架构演进路线，并保持每个阶段完成后单独验证、单独提交。本次校准日期为 2026-06-18，基于当前工作区、最近提交和 `docs/todo.md` 重新核对：P1-6 已随 `101c457 docs(计划): 同步提示词收口最终状态` 完成文档收口；P1-7「残余同步 IO 审计与收口」已随 `b3d27f5 docs(计划): 同步同步 IO 收口状态` 完成实现、验证和文档归档。P1-8「模型能力校验」也已完成：设计文档已随 `ded7213 docs(模型能力): 设计请求能力校验` 提交，实现计划已随 `d4748d2 docs(计划): 记录模型能力校验计划` 提交；registry 能力归一化和候选硬过滤已随 `388c00f feat(模型能力): 归一化能力并过滤候选` 落地，直接 New API 请求能力推导已随 `d907a98 feat(模型能力): 推导直接请求能力需求` 落地，Bridge 主回复路由能力校验已随 `66fdfd9 feat(桥接): 接入回复模型能力校验` 落地，payload / SDK request 前 guard 与无视觉候选降级已随 `d2a7a1f fix(模型能力): 防止发送不兼容请求` 落地，`model_routing` eval 覆盖已随 `e1d3bef test(评测): 覆盖视觉模型路由` 落地。P2-1「工具配置增加 platform 维度」已完成：只读审计、设计文档和实现计划已完成，设计文档随 `d221180 docs(工具): 设计平台维度配置` 提交，实现计划已写入 `.Codex/plans/tool-platform-scope.md`；后端解析任务已随 `bb7489c feat(工具): 支持平台维度解析` 落地，运行时决策 platform 审计已随 `295e3f7 feat(工具): 记录平台维度决策` 落地，真实入口 platform 透传已随 `73bbe8a feat(消息): 透传客户端平台` 落地，Admin API platform 覆盖和预览已随 `d9a1bae feat(工具): 支持平台覆盖接口` 落地，WebUI 工具页 platform selector 和「指定平台」覆盖入口已随 `2b0e203 feat(工具): 配置平台覆盖` 落地。当前优先级已切到 P2-2「标准化请求 / 响应信封」：只读审计已完成，设计文档已随 `c984036 docs(消息): 设计响应信封标准` 提交，实现计划已写入 `.Codex/plans/message-envelope.md`，下一步是按接口先行和多 owner 分工执行 TDD 实现。

## 当前目标

TimingGate「规则信号 + 模型」混合决策主线已经完成阶段性落地，Prompt V2 默认 live 接管、H29 第一刀、P1-5 Prompt legacy 收口、P1-6 旧提示词资产收敛、P1-7 残余同步 IO 审计与 async 热路径隔离、P1-8 模型能力校验，以及 P2-1 工具 platform 维度配置均已完成。当前 `docs/todo.md` 路线项 4 已落地：`ToolOverride(scope_type="platform")`、`RuntimeToolDecision.platform`、真实入口 platform 透传、Admin API 平台覆盖预览和 WebUI 平台覆盖入口都已具备。当前执行焦点是 `docs/todo.md` 路线项 5，也就是 P2-2「标准化请求 / 响应信封」；本阶段先采用兼容双写策略统一 `/chat`、流式 done、`/group/message` 和 push 的响应结构，并让私聊路径也返回过滤后的 `reply_meta`。

## 文档口径

- `docs/todo.md` 是当前架构路线的主参考，但它只记录路线级状态；当它与提交记录、`.Codex/plans/` 任务进度或本文件冲突时，以已提交代码和本文件的当前详细计划为准。本轮已重新核对路线项 5，确认 P2-2 设计已提交、实现计划已写入，尚未进入代码实现。
- `docs/TODO_LIST.md` 是历史完成清单，目前未跟踪且存在滞后状态，例如仍描述 Prompt V2 默认未启用、TimingGate 阶段仍在中途；后续仅作为历史核对材料，不作为优先级来源。
- 本文件记录「下一阶段怎么推进」，每次阶段完成后要同步状态并单独提交。

## 执行约束

- 每个阶段先写计划，再按 TDD 执行红灯、绿灯、重构。
- 每个阶段完成后运行定向测试、相关回归和全量测试。
- 每个阶段性改动单独 commit。
- commit 前必须使用中文提交规范，且只暂存本阶段文件。
- 只暂存本阶段文件，不使用 `git add .` 或 `git add -A`。
- 不回滚工作区中与本阶段无关的已有脏文件。
- 所有说明、文档和 commit message 使用中文。

## 进度总览

### 已完成基线

| 阶段 | 状态 | 交付物 |
|------|------|--------|
| 阶段 0：审查 `asyncio.run` 与测试慢速问题 | 已完成 | 代码审查结论与测试性能审查 |
| 阶段 1：前置缺陷修复与稳定性打底 | 已完成 | BridgePool、日志回滚、TODO 状态同步 |
| 阶段 2：建立 TimingGate scoring 纯函数与 shadow 可观测 | 已完成 | `core/timing_score.py`、ChatLog/Admin/WebUI 调试字段 |
| 阶段 3：普通 ambient 规则短路 | 已完成 | 普通 ambient 确定性规则跳过模型 |
| 阶段 4：模型失败规则兜底 | 已完成 | 模型异常后使用 `rule_fallback` |
| 阶段 5：eval scoring 覆盖 | 已完成 | timing eval 支持 scoring 校验 |
| 阶段 6：`directed_to_other` 软化 | 已完成 | 指向他人从 hard no_reply 降级为抑制信号 |
| 阶段 7：ambient cooldown 软化 | 已完成 | 群聊环境 cooldown 接入 scoring shortcut |
| 阶段 7.5：同步 TODO 进度 | 已完成 | `docs/todo.md` 同步混合决策进度 |
| 阶段 8：私聊接入 shared timing scoring | 已完成 | 私聊规则与分类器统一回灌 shared scoring |
| 阶段 9：timer / legacy cooldown 继续软化 | 已完成 | timer fired 与 legacy cooldown 接入 scoring shortcut |
| 阶段 10：session / platform 级模型层开关 | 已完成 | `enabled` / `rules_only` / `shadow` 策略解析与运行时接入 |
| 阶段 11：真实日志假阳率评估 | 已完成 | 时机信号审计 CLI、shadow mismatch 报告、阈值建议 |
| 阶段 12：timing gate eval 基线与回归门禁 | 已完成 | baseline diff、阈值门禁和 CLI 门禁输出 |
| 阶段 13：TimingGate 文档收尾 | 已完成 | 同步 `docs/todo.md` 与设计文档 |
| 流式基础贯通：`Message` 带 `stream` 参数 | 已完成 | API → BridgePool → Bridge → KT `Message` → `BufferedOutput.write_stream()` 已贯通 |
| Agent Step SSE 真流式 | 已完成 | `2369081 feat(agent): 支持 step 流式输出` |
| Prompt V2 默认接管 | 已完成 | 设计、计划、默认 live 接管和文档同步均已落地 |
| P1-4 Prompt Runtime 请求组装提取 | 已完成 | `PromptRuntimeInput` 组装已从 `handle_message()` 提取为可单测边界 |
| P1-5 任务 1：禁用旧版审计回退 | 已完成 | `afc3dd4 refactor(提示词): 禁用旧版审计回退` |
| P1-5 任务 2：默认使用 V2 回复评估 | 已完成 | `99c1803 refactor(评测): 默认使用 V2 回复评估` |
| P1-5 任务 3：旧版管理入口只读化 | 已完成 | `5009034 refactor(提示词): 降级旧版管理入口` |

### 后续优先级

| 优先级 | 状态 | 阶段 | 目标 | 阶段性提交建议 |
|--------|------|------|------|----------------|
| P1-1 | 已完成 | 提交 Prompt V2 默认接管设计 | 单独提交设计文档，固定本阶段边界和验收清单 | `36874bf docs(提示词): 设计 V2 默认接管方案` |
| P1-2 | 已完成 | 编写 Prompt V2 默认接管实现计划 | 写入 `.Codex/plans/prompt-v2-default-cutover.md`，列出 TDD 步骤、文件、验证命令 | `17b3815 docs(计划): 记录 V2 默认接管计划` |
| P1-3 | 已完成 | Prompt V2 默认 live 接管 | 默认 engine 改为 V2，保留显式 V1 回滚，初始化 `data/prompts_v2`，同步 admin preview 和 reply-test 默认值 | `2be9329` / `8a1e177` / `8a73909` |
| P1-4 | 已完成 | H29 第一刀：提取 Prompt Runtime 请求组装 | 从 `handle_message()` 中抽出 `PromptRuntimeInput` 构造边界，不移动 trace、tool plan、conversation 注入和 audit 异常处理 | `refactor(桥接): 提取提示词运行时组装` |
| P1-5 | 已完成 | Prompt legacy 收口 | live `fallback_v1` 已禁用，评估入口已转 V2，legacy / managed 管理写入口已降级为只读迁移入口 | `afc3dd4` / `99c1803` / `5009034` |
| P1-6 | 已完成 | 删除冗余提示词资产并去版本化 | 旧任务 prompt、V1 live 分支、legacy 管理面、旧资产删除、canonical 命名兼容层和文档最终验证均已完成 | `4fe00bb` / `docs(计划): 同步提示词收口最终状态` |
| P1-7 | 已完成 | 残余同步 IO 审计与收口 | 贴纸 fallback、图片附件预处理和 Direct 工具同步 IO 守卫均已落地，路线项 2 已完成收口 | `8ce5210` / `d96e7cd` / `c7e91a9` / `641d080` / `0489bac` / `b3d27f5` |
| P1-8 | 已完成 | 模型能力校验 | registry、直接 New API、Bridge 主回复、payload guard、无视觉候选降级和 `model_routing` eval 覆盖均已接入 `supports_image` / `supports_tools` / `supports_stream` | `ded7213` / `d4748d2` / `388c00f` / `d907a98` / `66fdfd9` / `d2a7a1f` / `e1d3bef` |
| P2-1 | 已完成 | 工具配置增加 platform 维度 | platform scope 解析、ToolPlan / FinalTools 透传、`RuntimeToolDecision.platform` 迁移、`/tools/decisions` 输出、真实入口到 Bridge 的 platform 透传、Admin API platform 覆盖和预览、WebUI 平台覆盖入口，以及消息字段标准文档收口均已完成 | `d221180` / `7c0fda9` / `bb7489c` / `295e3f7` / `73bbe8a` / `d9a1bae` / `2b0e203` |
| P2-2 | 计划已写入 | 标准化请求 / 响应信封 | 设计已随 `c984036` 提交，实现计划已写入 `.Codex/plans/message-envelope.md`；代码阶段先提交共享 builder，再按 API / 群聊 / push owner 分工推进兼容双写 | `c984036` / `docs(计划): 记录响应信封实现计划` / `refactor(消息): 统一响应信封` |
| P2-3 | 待执行 | QQ 出站渲染契约 | 输出结构化 segments，图片和 HTML 渲染集中在出口层 | `feat(渲染): 定义 QQ 出站消息契约` |
| P2-4 | 待执行 | Prompt platform × chat_type 二维适配 | V2 模板按平台和会话类型拆分，QQ 专属约定下沉到 platform 分支 | `feat(提示词): 支持平台化模板分支` |
| P3-1 | 已部分完成，待继续 | SSE 真 token 流式剩余收敛 | 已贯通 `/chat` 的 `stream` 参数并补齐 `/chat-step` SSE；继续补 chunk 合并窗口、backpressure、工具回合语义和统一信封 | `2369081` / 后续 `refactor(流式): 收敛增量输出契约` |
| P3-2 | 运营项 | TimingGate 持续评估 | 用更多人工标注样本复跑审计，接入外部 CI / PR gate | `ci(评测): 接入 timing gate 回归门禁` |
| P4-1 | 待执行 | 评测体系扩展 | 扩 per-capability 数据集，打通 `candidates → labeled` 标注闭环 | `feat(评测): 扩展能力评测数据集` |

## 当前详细计划：P2-2 标准化请求 / 响应信封

状态：P2-2 设计已提交，代码尚未开始。只读审计已确认 `/chat`、`/chat` SSE done、`/group/message` 和 push / 定时任务出口仍使用不同响应形态；`docs/superpowers/specs/2026-06-18-message-envelope-design.md` 已写入兼容双写方案并随 `c984036` 提交。`.Codex/plans/message-envelope.md` 已改为接口先行、文件 owner 清晰、可在隔离 worktree 中并行执行的实现计划。

目标：

- 对外新增统一响应信封字段：`reply`、`messages`、`reply_meta`、`meta`，群聊同时补 `status`。
- 保留旧字段不破坏调用方：`/chat.answer`、`answer_chunks`、SSE done 的 `answer`、`/group/message.action/reply/reply_meta/generation/reason`、`push_to_qq(target_type, target_id, message) -> bool` 均继续可用。
- 私聊成功路径返回过滤后的 `reply_meta`，不暴露 `_agent_result`、`_no_reply`、`_no_reply_reason` 等内部字段。
- 首版 `messages` 只承载保守的 `text` / `html` 结构，图片、at、reply segments、CQ renderer 和 HTML-to-pic 仍归入 P2-3「QQ 出站渲染契约」。
- `docs/message-field-standard.md` 在实现阶段补响应信封章节，避免只规范入站字段。

只读审计结论：

- `/chat` 非流式成功响应来自 `api/routes.py`，当前只返回 `status`、`user_id`、`answer`、`answer_chunks` 和 `unprocessed_logs`；私聊 `reply_meta` 已被 `_pop_bridge_reply_meta(...)` 取出，但成功路径只用于审计判断，没有进入响应。
- `/chat` SSE done 当前只发送 `{status: "done", answer}`，不带 `reply_meta`、`messages` 或 `meta`。
- `/group/message` continue 响应已经返回 `action`、`reply`、`reply_meta`、`generation` 和 `reason`，但缺少统一 `status`、`messages` 和 `meta`；wait / no_reply 分支也缺少统一空信封字段。
- `push_to_qq` 旧签名被调用方和测试依赖，P2-2 应新增 `push_envelope_to_qq(...)` 适配层，而不是破坏旧 helper。

阶段拆分：

- [x] 核对 `docs/todo.md` 路线项 5，确认 P2-2 是 P2 多平台底座的下一优先级；`docs/TODO_LIST.md` 明显滞后，仅作历史材料。
- [x] 完成只读审计：私聊 / Web 路径、群聊路径、push / 定时任务出口的响应字段差异均已梳理。
- [x] 写入设计文档：`docs/superpowers/specs/2026-06-18-message-envelope-design.md`，明确兼容双写方案、字段映射、P2-2 / P2-3 边界、测试计划和验收标准。提交：`c984036 docs(消息): 设计响应信封标准`。
- [x] 写入实现计划：`.Codex/plans/message-envelope.md`，按接口先行、API / 群聊 / push 文件 owner、主线程集成和阶段提交拆解。
- [ ] 任务 1：新增 `core/message_envelope.py`，覆盖 `messages` 构造、`reply_meta` 过滤、`meta` 组装和信封 builder 的单元测试。
- [ ] 任务 2：API owner 在 `api/routes.py` 接入 `/chat` 非流式和 SSE done 响应信封，保留旧字段并返回过滤后的私聊 `reply_meta`。
- [ ] 任务 3：群聊 owner 在 `app/group_ingress/service.py` 接入 continue / wait / no_reply 响应信封，保留 `action` 调度语义。
- [ ] 任务 4：push owner 在 `core/daily_digest.py` 新增信封适配 helper，旧 `push_to_qq(...)` 签名不变。
- [ ] 任务 5：主线程集成 `api/routes.py` 中手动任务运行和流式断连 push call site，避免与 API / push worker 冲突。
- [ ] 任务 6：同步 `docs/message-field-standard.md`、`docs/todo.md`、本文件和实现计划状态，运行定向与全量验证后单独提交。

下一步验证：

- 计划阶段先运行文档占位词扫描和 `git diff --check`。
- 进入实现后按 TDD 先写失败测试，重点覆盖 `/chat` 非流式、`/chat` SSE done、`/group/message` 三类响应和 `push_envelope_to_qq()`。
- 每个代码阶段运行对应定向测试；阶段收口前运行 `python -B -m pytest tests/ -v -p no:cacheprovider` 全量回归。

建议阶段性提交：

- 设计文档：`docs(消息): 设计响应信封标准`
- 实现计划：`docs(计划): 记录响应信封实现计划`
- builder：`feat(消息): 构建响应信封`
- `/chat`：`feat(消息): 返回私聊响应信封`
- SSE：`feat(流式): 返回完成信封`
- 群聊：`feat(消息): 返回群聊响应信封`
- push：`feat(推送): 支持信封推送适配`
- 文档收口：`docs(计划): 同步响应信封状态`

## 已完成阶段详情：P2-1 工具配置增加 platform 维度

状态：P2-1 已完成。`docs/todo.md` 路线项 4 的工具 platform 维度已形成运行和配置闭环：现有工具裁剪已有 `chat_type`、`group`、`user`、`runtime_preset` 和后端 `platform` override 解析，真实入口已把 `client_meta.platform` 传到 Bridge / ToolPlan；Admin API 已支持 platform override、effective preview、tools preview 和 platform targets，并随 `d9a1bae feat(工具): 支持平台覆盖接口` 提交。WebUI 工具页已支持预览 platform，并可对「指定平台」写入工具覆盖，随 `2b0e203 feat(工具): 配置平台覆盖` 提交。下一优先级切到 P2-2「标准化请求 / 响应信封」。

目标：

- 工具解析支持 platform scope，默认兼容 QQ 主通道，未来可表达 `qq`、`web`、`synergy` 等平台级工具策略。
- `ToolOverride` 优先复用现有泛化结构，新增 `scope_type="platform"`、`scope_id="<platform>"`，不为 P2-1 首版引入 platform + group/user 复合 scope。
- 解析顺序固定为 `chat_type -> platform -> group -> user`，并保持 `runtime_preset=none`、`force_enabled`、群聊 `force_disabled_group` 这些硬约束不可被 platform override 绕过。
- 运行时审计记录 platform，让 `/tools/decisions` 能解释平台策略导致的工具启停结果。
- 真实入口显式透传 platform：私聊 `/chat` 从 `client_meta.platform` 读取，群聊入口把已解析的 platform 继续传给 bridge，保留旧 QQ 流量的 `qq` fallback。

只读审计结论：

- 当前真实链路是 `NanobotBridge.handle_message()` → `build_tool_plan()` → `resolve_effective_tools()` → `record_runtime_tool_decision()`；出口侧 `resolve_final_tools()` 也复用 `resolve_effective_tools()`。
- `resolve_effective_tools()` 现有合并顺序为：`TOOL_METADATA` 默认值 → 初始硬约束 → `runtime_preset` → DB `ToolOverride` 覆盖 → 最终硬约束兜底。DB 覆盖只支持 `chat_type`、`group`、`user`，排序为 `chat_type -> group -> user`。
- `ToolOverride` 字段是 `tool_name`、`scope_type`、`scope_id`、`enabled`、`reason`、`created_at`、`updated_at`，唯一约束是 `(tool_name, scope_type, scope_id)`；该结构已直接承载 `scope_type="platform"`。
- `RuntimeToolDecision` 已新增 `platform` 列并补旧库迁移；平台策略生效后可以从运行记录解释来源。
- 群聊入口已经把 `client_meta.platform` 传给 TimingGate，但 `_continue_to_bridge` 的 `bridge_meta` 没有带 platform；只改工具解析函数会导致真实生成阶段仍拿不到平台。
- Admin API 已支持 `PUT /tools/{tool}/override` 写入 platform scope，`/tools/effective?platform=web` 应用平台覆盖，`/tools?platform=web` 展示平台覆盖状态，`/tools/targets?scope_type=platform` 返回内置平台并合并已有覆盖；WebUI 工具配置页已接入 platform selector 和「指定平台」覆盖对象。
- `PromptRuntimeInput` 当前没有 platform；P2-1 首版不做 prompt 模板 platform 化，避免把路线项 4 扩成路线项 9。

阶段拆分：

- [x] 已从 `docs/todo.md` 确认 P1 收敛去债完成后，下一优先级是 P2 多平台底座，首项为路线项 4「工具配置增加 platform 维度」。
- [x] 已完成后端运行时只读审计：工具解析顺序、`ToolOverride` scope 结构、`RuntimeToolDecision` 审计字段、Bridge / final tools 调用链均已梳理。
- [x] 已完成 Admin / WebUI 只读审计：`ToolOverrideBody`、`/tools`、`/tools/effective`、`/tools/decisions`、`/tools/targets` 和 `webui/src/features/tools/ToolsPage.jsx` 的 platform 缺口已梳理。
- [x] 写入设计文档：`docs/superpowers/specs/2026-06-18-tool-platform-scope-design.md`，明确推荐方案、非目标、迁移策略、Admin/API 口径和测试计划。提交：`d221180 docs(工具): 设计平台维度配置`。
- [x] 写入实现计划：`.Codex/plans/tool-platform-scope.md`，按 TDD 拆分红灯、绿灯、重构和阶段提交。
- [x] 任务 1：后端解析函数接入 platform 参数，覆盖 `resolve_effective_tools()`、`build_tool_plan()`、`resolve_final_tools()` 的 precedence 测试。提交：`bb7489c feat(工具): 支持平台维度解析`。
- [x] 任务 2：`RuntimeToolDecision` 增加 `platform` 字段、迁移和 `/tools/decisions` 输出，并补写入测试。提交：`295e3f7 feat(工具): 记录平台维度决策`。
- [x] 任务 3：真实入口透传 platform 到 Bridge / ToolPlan，覆盖 `/chat`、群聊 `_continue_to_bridge` 和 Bridge ToolPlan / decision 记录路径。提交：`73bbe8a feat(消息): 透传客户端平台`。
- [x] 任务 4：Admin API 支持 platform override / effective preview / tools preview，`PUT /tools/{tool}/override` 允许 `scope_type="platform"`。提交：`d9a1bae feat(工具): 支持平台覆盖接口`。
- [x] 任务 5：WebUI 工具配置页补最小 platform selector 和指定平台覆盖入口。提交：`2b0e203 feat(工具): 配置平台覆盖`。
- [x] 任务 6：同步 `docs/todo.md`、`docs/message-field-standard.md`、本文件和实现计划状态，运行定向与全量验证后单独提交。

最新验证记录：

- `bb7489c` 提交前红灯：`tests/test_tool_plan.py -k "platform_override or pass_platform"` 先失败于 `resolve_effective_tools()` / `build_tool_plan()` 不接受 `platform` 参数。
- `bb7489c` 提交前绿灯：新增 platform 解析定向 `3 passed, 8 deselected, 1 warning`；工具计划相关回归 `22 passed, 1 warning`；全量测试 `1238 passed, 6 skipped, 113 warnings in 83.38s`。
- `295e3f7` 提交前红灯：`record_runtime_tool_decision()` 不接受 `platform`，旧 `runtime_tool_decisions` 表缺少 `platform` 列。
- `295e3f7` 提交前绿灯：任务 2 定向 `3 passed, 21 warnings`；工具 / 迁移 / Admin 回归 `26 passed, 119 warnings`；全量测试 `1240 passed, 6 skipped, 139 warnings in 89.78s`。
- 任务 3 红灯：`tests/test_api.py -k "client_platform_to_bridge"` 先失败于 `/chat` 和群聊 Bridge metadata 缺少 `platform`；`tests/test_kt_framework.py -k "platform_to_tool_plan"` 先失败于 ToolPlan 参数 `platform=None`。
- 任务 3 绿灯：API platform 透传定向 `2 passed, 76 deselected, 21 warnings`；Bridge platform 透传定向 `1 passed, 55 deselected, 1 warning`；入口 / Bridge 回归 `134 passed, 21 warnings in 35.87s`；全量测试 `1243 passed, 6 skipped, 139 warnings in 86.72s`。
- `d9a1bae` 任务 4 红灯：`tests/test_admin_api.py::TestToolAdmin -k "platform"` 先失败于 Admin API 拒绝 `scope_type="platform"`，新增 2 个 platform 测试失败、既有 `/tools/decisions` platform 测试通过。
- `d9a1bae` 任务 4 绿灯：platform 定向 `3 passed, 7 deselected, 1 warning`；完整 `TestToolAdmin` 回归 `10 passed, 1 warning`；Admin / ToolPlan 相关回归 `86 passed, 1 warning`；全量测试 `1245 passed, 6 skipped, 139 warnings in 85.33s`。
- 任务 5 红灯：`tests/test_webui_admin_redesign.py -k "tools_page_exposes_platform"` 先失败于缺少 `tool-platform-select`。
- 任务 5 绿灯：同一定向测试 `1 passed, 15 deselected, 1 warning`；WebUI 静态回归 `21 passed, 1 warning`；`npm run build` 通过，Vite 仅提示大 chunk 和 `rolldown:vite-resolve` 插件耗时 warning；全量测试 `1246 passed, 6 skipped, 139 warnings in 84.80s`。
- 任务 6 文档扫描：过时占位词扫描无输出；`git diff --check -- docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/tool-platform-scope.md` 无输出。
- 任务 6 P2-1 定向回归：`tests/test_tool_plan.py tests/test_admin_api.py::TestToolAdmin tests/test_schema_migrations.py tests/test_api.py tests/test_kt_framework.py tests/test_webui_admin_redesign.py tests/test_webui_app_split.py`，结果 `183 passed, 139 warnings in 41.61s`。
- 任务 6 全量回归：`tests/`，结果 `1246 passed, 6 skipped, 139 warnings in 85.67s`。

后续测试与验证：

- `tests/test_tool_plan.py`：platform override 启停工具、`chat_type < platform < group < user` precedence、platform 放开 lightweight 禁用工具、`runtime_preset=none` 不可被 override 放开。
- `tests/test_admin_api.py`：platform override 写入、`/tools/effective?platform=web` 生效、`/tools` 预览展示平台覆盖、`/tools/decisions` 返回 platform。
- 群入口 / Bridge 测试：`client_meta.platform` 不只进入 TimingGate，也进入 bridge metadata 并最终影响 `build_tool_plan()`。
- 迁移验证：已有库通过 `core/schema_migrations.py` 补 `runtime_tool_decisions.platform` 列，避免线上旧库写入失败。
- 收尾验证：相关定向回归、`python -B -m pytest tests/ -v -p no:cacheprovider` 全量回归、`git diff --check`、文档过时措辞扫描。

风险与边界：

- `platform=""` 不应匹配空 `scope_id` 脏数据；函数默认空值用于兼容直接调用方，真实运行入口负责显式给出 `qq` fallback。
- 复用 `scope_type="platform"` 只能表达平台全局策略，不能表达「某平台下某群 / 某用户」的组合策略；组合策略不纳入 P2-1 首版。
- platform override 不能绕过 `force_enabled`、群聊 `force_disabled_group` 和 `runtime_preset=none`。
- Prompt Runtime platform 模板拆分属于 P2-4，不在本阶段实现。

建议阶段性提交：

- 已完成：`d221180 docs(工具): 设计平台维度配置`
- 本次计划同步：`docs(计划): 记录工具平台配置计划`
- 已完成：`bb7489c feat(工具): 支持平台维度解析`
- 已完成：`295e3f7 feat(工具): 记录平台维度决策`
- 已完成：`73bbe8a feat(消息): 透传客户端平台`
- 已完成：`d9a1bae feat(工具): 支持平台覆盖接口`
- 已完成：`2b0e203 feat(工具): 配置平台覆盖`
- 文档收口：`docs(计划): 同步工具平台配置状态`

## 已完成阶段详情：P1-8 模型能力校验

状态：P1-8 已完成。`docs/todo.md` 路线项 3 已从早期「尚未接入能力校验」口径同步为已落地口径；后续只保留 base64 data URL、图片数量 / 大小上限、platform 出站契约等相邻路线项。`docs/TODO_LIST.md` 仍是历史清单，不能作为当前优先级来源。

目标：

- 把模型能力从 tags / 模型 ID 猜测升级为模型记录顶层结构化字段，至少包括 `supports_image`、`supports_tools` 和 `supports_stream`。（已完成 registry / override / 候选硬过滤）
- 在候选模型排序前生成请求能力需求：messages 含 `image_url` 时要求 `supports_image`，传入 tools 时要求 `supports_tools`，真实 streaming 请求要求 `supports_stream`。（已完成直接 New API 路径和 Bridge 主回复路由）
- 主回复路由、`NewAPIClient.chat_completion()`、`chat_completion_stream()` 和 KT SDK request 边界都不能绕过能力过滤。（直接 New API、Bridge 和 payload guard 已完成）
- 手动指定回复模型也要校验能力；不满足时记录原因并回退自动路由，而不是盲发 payload。
- 当没有可用视觉模型时，降级为纯文本说明或明确错误，禁止把 `image_url` 发给纯文本模型。

只读审计结论：

- 当前模型记录是普通 dict，没有强 schema；已存在字段主要是 `id`、`provider`、`intelligence`、`cost_input_1m`、`cost_output_1m`、`tier`、`tags`、`description`、`reasoning`、`context_window` 和 `enabled`。
- 现有 `tags` 中的 `vision` / `multimodal` / `tool_use` 只能作为兼容推断来源；P1-8 不复用 `required_tags` 承载硬能力约束，因为旧 `ModelRegistry.select_model(required_tags=...)` 是软过滤语义。
- 能力字段采用顶层布尔字段，并兼容 overrides 中的嵌套 `capabilities` 输入；`supports_image` 缺失默认 false 或由 vision tag / 模型名推断，`supports_tools` 和 `supports_stream` 首版应保持兼容默认，至少先硬排除显式 false。
- Bridge 带图主链路的数据流是 `metadata["files"]` → `prepare_image_parts()` → `ImagePart(data_url)` → KT `Message.to_dict()` → OpenAI `image_url` content part；当前模型选择发生在图片 event content 构造之后，因此候选过滤前已经能拿到 `has_image`。
- KT controller 的生产 LLM 调用本身固定 streaming；`/chat?stream=true` 只控制 SSE 输出队列和用户 event stream 标记。因此 `supports_stream` 校验不能只覆盖直接 `NewAPIClient.chat_completion_stream()`，也要覆盖 Bridge / KT provider 的真实请求。
- ToolPlan schema 会进入 Prompt Runtime；真实 OpenAI `tools` 只在直接 New API 路径或 KT native tool mode 的 SDK request 中出现。P1-8 要先保证候选过滤，再在 payload / SDK request 构造前加安全网。
- 测试优先级以生产路径为准：先覆盖 `NanobotBridge.handle_message(files=...) -> get_ordered_candidates(required_capabilities=...)`，再覆盖 registry 归一化、直接 New API、stream / tools payload 和 eval runner。

阶段拆分：

- [x] 已确认 P1-7 已完成：定向测试 `186 passed, 20 warnings`，全量测试 `1222 passed, 6 skipped, 113 warnings`，并随 `b3d27f5` 归档。
- [x] 已从 `docs/todo.md` 确认下一优先级为路线项 3「请求构造按模型能力校验」。
- [x] 已完成 P1-8 只读审计：模型 registry / overrides、Bridge 图片和 stream 数据流、现有测试覆盖缺口均已梳理。
- [x] 写入设计文档：`docs/superpowers/specs/2026-06-18-model-capability-validation-design.md`，覆盖能力字段、过滤策略、降级策略、手动模型策略和测试计划。提交：`ded7213 docs(模型能力): 设计请求能力校验`。
- [x] 写入实现计划：`.Codex/plans/model-capability-validation.md`，按 TDD 拆分红灯、绿灯、重构和阶段提交。
- [x] 任务 1-3：完成 registry 能力归一化、override `null` fallback、`get_ordered_candidates(required_capabilities=...)` 硬过滤和相关测试。提交：`388c00f feat(模型能力): 归一化能力并过滤候选`。说明：Bridge 带图候选要求已顺延到任务 5，不再归入任务 1。
- [x] 任务 4：直接 New API 请求已从 messages / tools / stream 自动推导能力需求，`chat_completion()` 和 `chat_completion_stream()` 均会把 `required_capabilities` 传给候选排序，手动模型能力不匹配会返回错误。提交：`d907a98 feat(模型能力): 推导直接请求能力需求`。
- [x] 任务 5：让 `NanobotBridge` 主回复路由消费 `files`、ToolPlan schema 和 KT 固定 streaming 请求事实，手动模型同样执行能力校验。提交：`66fdfd9 feat(桥接): 接入回复模型能力校验`。
- [x] 任务 6：实现 payload / SDK request 前安全网和无视觉候选时的降级策略，确保纯文本模型不会收到 `image_url` payload。提交：`d2a7a1f fix(模型能力): 防止发送不兼容请求`。
- [x] 任务 7：扩展 `model_routing` eval case / runner，覆盖带图请求必须选 vision 候选，保留现有 regression case 的发现路径。提交：`e1d3bef test(评测): 覆盖视觉模型路由`。
- [x] 任务 8：同步 `docs/todo.md`、本文件、`.Codex/plans/model-capability-validation.md` 和相关 Prompt Runtime 文档口径，并运行定向与全量验证。

最新验证记录：

- `d907a98` 提交前定向回归：`tests/test_llm_request_tracing.py tests/test_final_tools.py tests/test_model_router.py`，结果 `59 passed, 1 warning in 2.14s`。
- `d907a98` 提交前全量回归：`tests/`，结果 `1228 passed, 6 skipped, 113 warnings in 84.26s`。
- `66fdfd9` 提交前新增任务 5 定向：`tests/test_kt_framework.py::TestNanobotBridge::test_handle_message_with_files_requests_vision_candidates`、`tests/test_kt_framework.py::TestNanobotBridge::test_reply_model_lacking_required_capability_falls_back_to_auto`、`tests/test_streaming_bridge.py::test_bridge_handle_message_streams_controller_text_deltas`，结果 `3 passed, 1 warning in 2.89s`。
- `66fdfd9` 提交前 Bridge 回归：`tests/test_kt_framework.py tests/test_streaming_bridge.py`，结果 `58 passed, 1 warning in 18.26s`。
- `66fdfd9` 提交前 P1-8 相关回归：`tests/test_model_registry.py tests/test_model_router.py tests/test_llm_request_tracing.py tests/test_final_tools.py tests/test_kt_framework.py tests/test_streaming_bridge.py`，结果 `126 passed, 1 warning in 19.58s`。
- `66fdfd9` 提交前全量回归：`tests/`，结果 `1230 passed, 6 skipped, 113 warnings in 88.28s`。
- `d2a7a1f` 提交前任务 6 红灯：`tests/test_final_tools.py -k "required_capabilities_when_model_lacks_support"` 先失败于 `_build_payload()` 不支持 `model_info`；`tests/test_kt_framework.py -k "degrades_to_text_without_vision_candidate"` 先失败于只路由一次且未降级。
- `d2a7a1f` 提交前任务 6 绿灯：New API payload guard 定向 `3 passed, 8 deselected, 1 warning in 0.36s`；Bridge 无视觉候选降级定向 `1 passed, 54 deselected, 1 warning in 2.09s`。
- `d2a7a1f` 提交前任务 6 相关回归：`tests/test_kt_framework.py tests/test_final_tools.py tests/test_llm_request_tracing.py`，结果 `88 passed, 1 warning in 19.26s`。
- `d2a7a1f` 提交前 P1-8 相关回归：`tests/test_model_registry.py tests/test_model_router.py tests/test_llm_request_tracing.py tests/test_final_tools.py tests/test_kt_framework.py tests/test_streaming_bridge.py`，结果 `130 passed, 1 warning in 20.40s`。
- `d2a7a1f` 提交前全量回归：`tests/`，结果 `1234 passed, 6 skipped, 113 warnings in 88.05s`。
- `e1d3bef` 提交前任务 7 红灯：新增 `regression_model_routing_vision_required_001` 后，`python -m evals.run --suite model_routing` 失败，结果 `total=3 passed=2 failed=1`，失败原因是旧 runner 选择了 `text-cheap`。
- `e1d3bef` 提交前任务 7 绿灯：`python -m evals.run --suite model_routing`，结果 `total=3 passed=3 failed=0`；`tests/test_eval_baseline.py`，结果 `5 passed, 1 warning in 0.84s`。
- `e1d3bef` 提交前任务 7 相关回归：`tests/test_eval_baseline.py tests/test_model_router.py`，结果 `34 passed, 1 warning in 1.23s`。
- `e1d3bef` 提交前 P1-8 相关回归：`tests/test_model_registry.py tests/test_model_router.py tests/test_llm_request_tracing.py tests/test_final_tools.py tests/test_kt_framework.py tests/test_streaming_bridge.py tests/test_eval_baseline.py`，结果 `135 passed, 1 warning in 21.05s`。
- `e1d3bef` 提交前全量回归：`tests/`，结果 `1235 passed, 6 skipped, 113 warnings in 87.72s`。

建议阶段性提交：

- 已完成：`ded7213 docs(模型能力): 设计请求能力校验`
- 已完成：`d4748d2 docs(计划): 记录模型能力校验计划`
- 已完成：`388c00f feat(模型能力): 归一化能力并过滤候选`
- 已完成：`d907a98 feat(模型能力): 推导直接请求能力需求`
- 已完成：`66fdfd9 feat(桥接): 接入回复模型能力校验`
- 已完成：`d2a7a1f fix(模型能力): 防止发送不兼容请求`
- 已完成：`e1d3bef test(评测): 覆盖视觉模型路由`
- 本次文档收口：`docs(计划): 同步模型能力校验状态`

## 已完成阶段详情：P1-7 残余同步 IO 审计与收口

状态：已完成。P1-7 不做全仓 `urllib` / `requests` 异步化，目标是确认同步 IO 是否仍会直接落在 async 热路径，并只修已经确认的风险点。

设计文档：`docs/superpowers/specs/2026-06-18-sync-io-audit-design.md`

实现计划：`.Codex/plans/sync-io-audit.md`

当前结论：

- [x] 共享 `aiohttp.ClientSession` 已随 `4550aca` 和 `2bf4ee7` 落地，核心 LLM 请求不再逐请求创建 session。
- [x] P1-7 只读审计已完成，并随 `8ce5210` 写入设计文档。
- [x] `nanobot_kt/image_pipeline.py` 主链路已由 `NanobotBridge.handle_message()` 使用 `await asyncio.to_thread(prepare_image_parts, ...)` 卸载。
- [x] 私聊 / 群聊图片预缓存通过 Starlette `BackgroundTasks` 执行同步函数，不直接阻塞 ASGI event loop。
- [x] `image_generation`、`image_summary` 和当前注册的 `ai_daily` 工具入口已在 `_execute()` 内使用 `asyncio.to_thread()` 包住同步 HTTP / 抓取逻辑。
- [x] `daily_digest_scheduler()` 的 `time.sleep()` 运行在独立 daemon thread 中，不属于 ASGI event loop 阻塞。
- [x] `core/compaction.py` 当前从同步 `/context` endpoint 调用，风险是占用 worker，不是 event loop 阻塞。
- [x] 已确认必修风险：`app/group_ingress/helpers.py` 的 `register_group_stickers_from_message()` 在 `background_tasks is None` 时会直接调用 `cache_sticker_preview()`，可能从 `GroupIngressService.handle()` 的 async 路径进入。
- [x] 已写入 P1-7 实现计划，拆为 4 个阶段性提交：贴纸 fallback 收口、图片附件守卫、Direct 工具守卫、路线文档收尾。
- [x] 任务 1：按 TDD 修复贴纸预览 `background_tasks=None` fallback，确保 async service 不直接执行同步 DNS / urllib / 文件 IO / 图片解码。提交：`c7e91a9 fix(贴纸): 隔离预览缓存同步 IO`。
- [x] 任务 2：补图片附件 `to_thread` 回归守卫。提交：`641d080 test(图片): 守卫附件预处理线程卸载`。
- [x] 任务 3：补 `image_generation`、`image_summary`、`ai_daily` Direct 工具 `to_thread` 回归守卫。提交：`0489bac test(工具): 守卫同步调用线程卸载`。
- [x] 任务 4：同步 `docs/todo.md`、本文件和 `.Codex/plans/sync-io-audit.md` 的最终状态，并运行定向与全量验证。

验证记录：

- 任务 1 红灯：`test_group_message_sticker_preview_without_background_tasks_uses_to_thread` 失败，调用顺序第一项为 `direct_cache`。
- 任务 1 绿灯：新增测试 `1 passed, 1 warning`；贴纸入口回归 `6 passed, 70 deselected, 20 warnings`；`tests/test_sticker_memory.py` `10 passed, 1 warning`。
- 任务 2 红灯：临时直接调用 `prepare_image_parts(...)` 后，`test_handle_message_uses_multimodal_event_for_files` 失败于 `assert to_thread_calls`。
- 任务 2 绿灯：守卫单测 `1 passed, 1 warning`；图片相关回归 `59 passed, 1 warning`。
- 任务 3 红灯：临时把 3 个 Direct 工具改为直接同步调用后，3 个新增守卫全部失败，均未记录到 `asyncio.to_thread`。
- 任务 3 绿灯：新增守卫 `3 passed, 1 warning`；Direct 工具回归 `41 passed, 1 warning`。
- 引用审计：`cache_sticker_preview(` 只剩后台 job、同步 admin / public endpoint；async service 使用 `await asyncio.to_thread(cache_sticker_preview_bg, sticker_id)`。
- P1-7 定向测试：`186 passed, 20 warnings in 35.26s`。
- P1-7 全量测试：`1222 passed, 6 skipped, 113 warnings in 86.76s`。

## 已完成阶段详情：P1-6 删除冗余提示词资产并去版本化

状态：已完成。P1-6 任务 1-6 已完成：旧管理入口已由后端 410 catch-all 接管，WebUI 旧 route / 旧页面组件已删除，旧任务 prompt 已迁移到 V2 task template，V1 live 分支已封存，旧资产删除、禁止项扫描、相关回归、WebUI 构建和全量测试均已通过，并已随 `597a514` 单独提交归档。任务 7「建立无版本 canonical prompt 命名兼容层」已完成红灯、绿灯实现、相关回归、WebUI 构建、禁止项扫描和全量测试，并已随 `4fe00bb` 单独提交归档。任务 8 已把 `docs/todo.md`、本文件、P1-6 设计文档和实现计划同步到当前事实，并通过最终引用守卫和回归验证。

- [x] 重新核对 `docs/todo.md` 路线项 1，确认 P1-6 是 P1-5 之后的下一优先级。
- [x] 核对 `docs/TODO_LIST.md`，确认它记录了更旧的路线状态，仅作历史核对，不作为优先级来源。
- [x] 标记 P1-5 Prompt legacy 收口已完成，并把本文件的当前执行焦点切到 P1-6。
- [x] 写入 P1-6 设计文档：`docs/superpowers/specs/2026-06-17-prompt-v1-asset-removal-design.md`。
- [x] 写入 P1-6 实现计划：`.Codex/plans/prompt-v1-asset-removal.md`。
- [x] 只读清点仍依赖旧 prompt 的 live 引用：后台任务 prompt、旧运行时模块、旧模板目录和相关 admin / WebUI 路由均已纳入迁移或删除范围。
- [x] 新增 V2 task template 渲染边界：`classifier_legacy` 和 `memory_extract` 已具备 V2 task 模板、变量白名单和渲染 helper。
- [x] 迁移分类器任务 prompt 调用方：`clients/classifier_client.py` 的 `timing_gate/private_decision/classifier_legacy` 已改用 V2 task 模板，旧 `core.prompt_runtime` 不再参与分类器路由渲染。
- [x] 迁移记忆抽取任务 prompt 调用方：`core/legacy_adapter.py` 的 `memory_extract` 已改用 V2 task 模板，旧 `core.prompt_runtime` 不再参与记忆抽取渲染。
- [x] 收敛运行时入口：live 发送路径已忽略 `v1` override 并统一进入 V2 canonical runtime，`build_prompt_runtime()` 不再调用 `PromptAssembler`。
- [x] 修正旧主链路测试口径：`tests/test_kt_framework.py` 不再断言 V1 手工 `<runtime_context>` 注入，改为验证 bridge 传给 V2 prompt runtime 的结构化输入。
- [x] 提交 P1-6 任务 4：修正旧测试后全量测试已重新通过，并随本阶段单独 commit 归档。
- [x] 处理管理面迁移出口：旧入口下线测试已改写并完成红灯验证；后端旧 managed / legacy route 已删除并补最小 410 catch-all；WebUI `/prompt-legacy`、`/prompts` 和旧页面组件已删除；任务 5 定向回归、WebUI 构建和全量测试已通过，随本阶段单独提交归档。
- [x] 删除冗余资产：删除不再被 live 读取的 V1 / legacy 模块、模板目录和构建脚本，保留测试夹具中确有必要的最小样本。
- [x] 任务 7 范围校准：本阶段先做 engine、配置、env、admin API、WebUI 主入口和 live tracing 输出的 canonical 化；暂不物理重命名 `prompts.v2.default`、`data/prompts_v2`、`core.prompt_v2` 包名和 `prompt_v2_audit_failed` 等历史兼容字段。
- [x] 任务 7 红灯测试：新增 manifest、registry env、admin canonical route、WebUI 主入口和 reply-test canonical engine 断言，并确认当前旧实现失败。
- [x] 任务 7 绿灯实现：把 live 输出规范化为 `prompt` / `Prompt Runtime`，新增 `/prompt/*` admin alias 与 `/prompt-templates` WebUI 主路由，保留 `/prompt-v2/*` 和旧 variant 兼容读取。
- [x] 任务 7 验证：运行任务 7 定向测试、prompt runtime 相关回归、WebUI 构建、禁止项扫描和全量测试。
- [x] 去版本化命名收尾：确认 live 主路径不再输出 `v2` 作为 engine / mode / 用户可见文案；旧配置只保留兼容读取，不再作为 live 主路径。
- [x] 任务 7 阶段提交：按文件显式暂存并以 `4fe00bb refactor(提示词): 建立无版本运行时命名` 单独提交。
- [x] 同步提示词说明：当前态文档已从旧单文件 prompt / 旧 builder 口径改为 canonical Prompt Runtime 模板目录；历史规格中的旧引用保留为历史上下文。
- [x] 任务 8 验证收尾：运行 P1-6 定向测试、prompt runtime / reply admin 回归、引用守卫、WebUI 构建和全量测试，并按阶段单独 commit。

验证记录：

- P1-6 任务 1 红灯：`2 failed, 1 warning`，失败点为 `core.prompt_v2.task_templates` 模块不存在。
- P1-6 任务 1 绿灯：`2 passed, 1 warning`。
- P1-6 任务 1 Prompt V2 回归：`24 passed, 1 warning`。
- P1-6 任务 2 红灯：`1 failed, 1 warning`，失败点为分类器 payload 仍使用旧 `legacy system`。
- P1-6 任务 2 绿灯：`1 passed, 1 warning`。
- P1-6 任务 2 分类器与时机策略回归：`34 passed, 1 warning`。
- P1-6 任务 3 红灯：`1 failed, 1 warning`，失败点为记忆抽取 query 仍使用旧候选抽取 prompt。
- P1-6 任务 3 绿灯：`1 passed, 1 warning`。
- P1-6 任务 3 evolution / memory 回归：`27 passed, 1 warning`。
- P1-6 任务 4 红灯：`4 failed, 1 warning`，失败点覆盖 settings / metadata `v1` 仍可进入 live 分支、`PromptAssembler` 仍被调用。
- P1-6 任务 4 绿灯：`4 passed, 1 warning`。
- P1-6 任务 4 bridge / streaming 回归：`15 passed, 1 warning`。
- P1-6 任务 4 首次全量：`1 failed, 1243 passed, 6 skipped, 113 warnings`，失败点为旧 `test_kt_framework` 仍断言 V1 手工 `<runtime_context>` 注入。
- P1-6 任务 4 旧测试修正：`1 passed, 1 warning`。
- P1-6 任务 4 bridge / streaming / KT 回归：`67 passed, 1 warning`。
- P1-6 任务 4 修正后全量：`1244 passed, 6 skipped, 113 warnings`。
- P1-6 任务 5 红灯：`9 failed, 17 passed, 20 warnings`，失败点覆盖旧 GET 路由仍返回 200、WebUI 仍暴露 `/prompt-legacy` / `/prompts` 直达路由和旧组件 import / export。
- P1-6 任务 5 定向：`27 passed, 20 warnings`。
- P1-6 任务 5 相关回归：`47 passed, 20 warnings`。
- P1-6 任务 5 WebUI 构建：`npm run build` 通过。
- P1-6 任务 5 首次全量：`1 failed, 1251 passed, 6 skipped, 113 warnings`，失败点为 `tests/test_token_utils.py` 仍导入已随旧 `/prompt` 管理页删除的 `_prompt_metrics()`。
- P1-6 任务 5 修正旧测试后全量：`1252 passed, 6 skipped, 113 warnings`。
- P1-6 任务 6 守卫红灯：`3 failed, 1 warning`，失败点覆盖旧模块 / 旧目录仍存在和 `config.yaml` 仍引用 `prompt.md`。
- P1-6 任务 6 守卫绿灯：`3 passed, 1 warning`。
- P1-6 任务 6 首轮定向：`1 failed, 53 passed, 1 warning`，失败点为 V2 `timing_gate` task 模板仍是占位内容。
- P1-6 任务 6 timing gate 修正后定向：`7 passed, 1 warning`。
- P1-6 任务 6 主定向：`54 passed, 1 warning`。
- P1-6 任务 6 相关回归：`104 passed, 20 warnings`。
- P1-6 任务 6 生产禁止项扫描：无命中；测试中的命中仅为守卫断言和隔离断言。
- P1-6 任务 6 WebUI 构建：`npm run build` 通过，Vite 仅提示大 chunk 与插件耗时 warning。
- P1-6 任务 6 全量测试：`1214 passed, 6 skipped, 113 warnings in 80.22s`。
- P1-6 任务 7 范围校准：保留 `prompts.v2.default`、`data/prompts_v2`、`data/prompts_v2_history`、`core.prompt_v2`、`/prompt-v2/*`、`/prompt-v2-templates`、`v2_code_retry` / `v2_prompt_only` 和 `prompt_v2_audit_failed` 作为兼容边界；canonical 主路径使用 `prompt`、`Prompt Runtime`、`/prompt/*` 和 `/prompt-templates`。
- P1-6 任务 7 红灯：`15 failed, 1 passed, 20 warnings`，失败点覆盖 manifest 仍是 `v2` active engine、config 默认仍为 `v2`、registry env 仍优先 `NANOBOT_PROMPT_V2_*`、`/prompt/templates` 被旧 catch-all 返回 410、WebUI 主入口和 reply-test 仍输出 `v2`。
- P1-6 任务 7 红灯集合绿灯：`16 passed, 20 warnings`。
- P1-6 任务 7 相关回归：`126 passed, 20 warnings`。
- P1-6 任务 7 WebUI 构建：`npm run build` 通过，Vite 仅提示大 chunk 与插件耗时 warning。
- P1-6 任务 7 禁止项扫描：主路径未再输出 `Prompt Runtime V2`、`engine: 'v2'`、`/prompt-v2/templates` 等；命中仅剩兼容 route、兼容输入测试和负向断言。
- P1-6 任务 7 全量测试：`1219 passed, 6 skipped, 113 warnings in 81.05s`。
- P1-6 任务 8 文档检查：`git diff --check` 无输出。
- P1-6 任务 8 引用守卫：`core.prompt_runtime|PromptAssembler|legacy_prompt_runtime|build_nanobot_prompt|system_prompt_file: prompt.md` 仅命中 `tests/test_prompt_legacy_removal.py` 和 `tests/test_prompt_v2.py` 的守卫 / 负向断言，live 源码无命中。
- P1-6 任务 8 prompt / manifest / bridge 定向：`71 passed, 1 warning in 4.00s`。
- P1-6 任务 8 reply admin / trace / legacy readonly / WebUI 回归：`42 passed, 20 warnings in 9.36s`。
- P1-6 任务 8 API / streaming 回归：`79 passed, 20 warnings in 14.91s`。
- P1-6 任务 8 WebUI 构建：`npm run build` 通过，Vite 仅提示大 chunk 与 `rolldown:vite-resolve` 插件耗时 warning。
- P1-6 任务 8 全量测试：`1219 passed, 6 skipped, 113 warnings in 82.12s`。
- P1-5 任务 3 红灯：`13 failed, 3 passed, 20 warnings`，失败点覆盖旧写接口、V1 preview、legacy GET 副作用和 WebUI 只读断言。
- P1-5 任务 3 绿灯：`16 passed, 20 warnings`。
- P1-5 任务 3 定向：`25 passed, 20 warnings`。
- P1-5 相关回归：`70 passed, 20 warnings`。
- WebUI 构建：`npm run build` 通过。
- P1-5 收口全量测试：`1238 passed, 6 skipped, 113 warnings`。

P1-6 验收重点：

- 默认 live 路径不再构造、加载或回退到 V1 / legacy prompt。
- 后台任务 prompt 已进入 V2 task template 管理，不再依赖旧 fragment / legacy prompt key。
- 删除资产前有 `rg` 级别的引用清点，确认剩余引用只属于历史兼容、测试夹具或迁移说明。
- 去版本化命名不破坏现有历史 trace / eval 报告读取。
- admin / WebUI 对已删除旧能力的展示是只读、迁移说明或明确下线，不再暴露可写入口。

## 已完成阶段详情：P1-4 Prompt Runtime 请求组装提取

计划文件：`.Codex/plans/prompt-runtime-request-extraction.md`

### 阶段 A：提交实现计划

状态：已完成。

- [x] 核对 `docs/todo.md`，确认下一步仍属于 P1 路线项 1 与 H29 拆分。
- [x] 只读梳理 `handle_message()` 中 Prompt Runtime 请求组装输入，确认第一刀边界。
- [x] 只读审查现有 prompt/runtime 测试覆盖，确认新增单元测试位置和回归命令。
- [x] 创建 `.Codex/plans/prompt-runtime-request-extraction.md`，记录 TDD 步骤、影响文件和验证命令。
- [x] 运行文档 diff 检查。
- [x] 提交：`docs(计划): 记录提示词运行时组装提取计划`

### 阶段 B：TDD 红灯

状态：已完成。

- [x] 在 `tests/test_bridge_prompt_v2.py` 新增 `_build_prompt_runtime_input` 单元测试。
- [x] 覆盖 V2 默认组装：prompt key、fallback V1 prompt mode、身份字段、上下文字段、tool schema、trace 字段和 audit policy。
- [x] 覆盖 V1 override 组装：`group_chat/private_chat` key、显式 prompt mode、空画像 fallback。
- [x] 覆盖 `source_message_ids` 清洗和 tool schema 读取失败降级。
- [x] 运行新增测试并确认先失败：`3 failed, 1 warning`，失败原因为 `PromptRuntimeAssemblyContext` 不存在。

### 阶段 C：最小实现

状态：已完成。

- [x] 在 `nanobot_kt/bridge.py` 增加最小组装边界，保持 bridge 私有方法，不把 bridge metadata 解析下沉到 `prompt_runtime.py`。
- [x] 构造并返回 `PromptRuntimeInput`，保持现有字段语义不变。
- [x] 当时对 V2 仍传入 V1 fallback prompt mode，作为旧审计回退策略的兼容边界；P1-5 任务 1 后已固定为 `v2`。
- [x] 保持 v2 的 `chat_group/chat_private` 与 v1 的 `group_chat/private_chat` 不混用。
- [x] 未移动 `build_prompt_runtime()` 调用、`PromptRuntimeAuditFailure` 处理、`RunTracer.update_prompt_source()`、`apply_prompt_messages()` 和 `create_user_event()`。

### 阶段 D：验证与提交

状态：已完成。

- [x] 运行新增测试，确认红灯转绿：`3 passed, 1 warning`。
- [x] 运行 `tests/test_bridge_prompt_v2.py` 和 `tests/test_streaming_bridge.py`：`14 passed, 1 warning`。
- [x] 运行 prompt runtime 相关回归：`28 passed, 20 warnings`。
- [x] 提交前运行全量测试：`1221 passed, 6 skipped, 113 warnings in 84.27s`。
- [x] 单独提交实现：`refactor(桥接): 提取提示词运行时组装`

## 已完成阶段详情：Prompt V2 默认接管

### 阶段 A：提交设计文档

状态：已完成。

- [x] 只暂存 `docs/superpowers/specs/2026-06-17-prompt-v2-default-cutover-design.md`
- [x] 运行 `git diff --check -- docs/superpowers/specs/2026-06-17-prompt-v2-default-cutover-design.md`
- [x] 提交：`36874bf docs(提示词): 设计 V2 默认接管方案`

### 阶段 B：编写实现计划

状态：已完成。

- [x] 创建 `.Codex/plans/prompt-v2-default-cutover.md`
- [x] 覆盖文件职责：`core/config_registry.py`、`nanobot_kt/bridge.py`、`bootstrap/prompt_runtime.py`、`api/admin_routes.py`、相关测试文件
- [x] 明确 TDD 验收：默认走 V2、显式 V1 可回滚、非法 engine 回落 V2、admin / reply-test 默认值改为 V2、V2 runtime 初始化不覆盖已有修改
- [x] 运行计划占位符扫描和 `git diff --check`
- [x] 提交：`17b3815 docs(计划): 记录 V2 默认接管计划`

### 阶段 C：TDD 红灯

状态：已完成。

- [x] 更新 `tests/test_bridge_prompt_v2.py`，验证无 override 默认走 V2、metadata override 为 V1 时仍可回滚、非法 engine 回落 V2
- [x] 更新 `tests/test_prompt_manifest.py`，验证 manifest active engine 与 config registry 默认值一致
- [x] 更新 `tests/test_prompt_v2_template_registry.py`，验证 V2 runtime 初始化复制缺失模板且不覆盖已有文件
- [x] 更新 `tests/test_prompt_runtime_bootstrap.py`，验证启动初始化 V2 runtime 和 V1 显式回滚 warning
- [x] 更新 `tests/test_prompt_trace_admin.py`，验证 effective preview 默认 engine 为 V2
- [x] 更新 `tests/test_reply_admin.py`，验证 reply-test 默认 prompt engine / variant 使用 V2
- [x] 已按 TDD 确认新增测试先失败，再由实现变绿

### 阶段 D：最小实现

状态：已完成。

- [x] `core/config_registry.py`：`prompt_runtime.engine` 默认值改为 `v2`
- [x] `nanobot_kt/bridge.py`：engine fallback 从 V1 改为 V2，显式 V1 override 仍生效
- [x] `core/prompt_v2/template_registry.py` / `bootstrap/prompt_runtime.py`：启动时初始化 `data/prompts_v2`，已有 runtime 修改不覆盖
- [x] `api/admin_routes.py`：effective preview 和 reply-test 默认值切到 V2
- [x] 启动诊断：有效 engine 仍为 V1 时记录显式回滚 warning

### 阶段 E：验证与提交

状态：已完成。

- [x] 运行定向测试：`32 passed, 20 warnings`
- [x] 运行全量测试：`1206 passed, 6 skipped, 113 warnings`
- [x] 插队修复运行时错误：`22e0b79 fix(调度): 兼容无 Runner 的定时任务`
- [x] 同步 `docs/todo.md` 和本文件状态
- [x] 提交文档收尾：本次 `docs(计划): 同步阶段进度`

## 阶段清单

### 阶段 0：审查 `asyncio.run` 与测试慢速问题

状态：已完成。

已确认生产代码不在 `main` 以外违规使用 `asyncio.run`；测试套件没有发现由明显 bug 导致的异常拖慢。

### 阶段 1：前置缺陷修复与稳定性打底

状态：已完成。

已完成 BridgePool 在途请求等待、日志保存失败回滚、相关 TODO 状态同步等前置修复。

相关提交：

- `95683ed fix(BridgePool): 停止前等待在途请求完成`
- `91d5f75 fix(记忆): 保存日志失败时回滚事务`
- `3a4ce44 docs(TODO): 同步缺陷修复状态`

### 阶段 2：建立 TimingGate scoring 纯函数与 shadow 可观测

状态：已完成。

已新增 `core/timing_score.py`，覆盖 `d0`、`linger`、`s_ack`、`s_transport`、`s_other`、`w_*`、规则分数、冲突升级、模型融合和 `rule_fallback`。ChatLog、Admin 和 WebUI 已能透出 scoring 调试字段。

### 阶段 3：普通 ambient 规则短路

状态：已完成。

普通 ambient 路径在调用模型前先执行 scoring。纯 ambient、纯确认等确定性场景可以跳过模型。

相关提交：

- `40f0ce6 feat(时机门控): 接管普通规则短路`

### 阶段 4：模型失败规则兜底

状态：已完成。

模型失败、超时或解析失败时，使用规则侧 `rule_fallback` 决策，不再让远端模型异常导致全群哑火。

相关提交：

- `fc53b99 fix(时机门控): 模型失败时使用规则兜底`

### 阶段 5：eval scoring 覆盖

状态：已完成。

`timing_gate_runner` 在 case 缺少旧式 `input.action` 时会执行 `decide_timing()`，scorer 支持递归校验 `expected.scoring`。

相关提交：

- `5e5c14f test(时机门控): 让评测覆盖规则评分`

### 阶段 6：`directed_to_other` 软化

状态：已完成。

`directed_to_other` 已从 hard no_reply 降级为 `s_other` 抑制信号。独自成立时规则侧 no_reply，和 linger 等正向信号冲突时升级到模型。

相关提交：

- `99cb17b refactor(时机门控): 软化指向他人规则`

### 阶段 7：ambient cooldown 软化

状态：已完成。

`trigger_reason="ambient"` 的 cooldown 分支已接入 scoring shortcut，避免继续保留不透明 hard wait。

相关提交：

- `9bbf945 refactor(时机门控): 软化群聊环境冷却`

### 阶段 7.5：同步 TODO 进度

状态：已完成。

`docs/todo.md` 已同步当前 TimingGate 混合决策的已完成项和剩余项。

相关提交：

- `397d029 docs(时机门控): 同步混合决策进度`

### 阶段 8：私聊接入 shared timing scoring

状态：已完成。

目标：私聊不再使用独立规则加 Qwen 黑箱三态。规则明确时调用 `decide_timing(is_private=True)` 直接短路；冲突或模糊时，将 `PrivateDecisionClassifier` 结果转换成 `TimingModelHint`，再回灌统一 scoring 公式。

已完成：

- 已写计划文件：`.Codex/plans/timing-gate-scoring-phase8-private.md`
- 已写并验证红灯：私聊任务请求应跳过分类器并携带 `timing_scoring`
- 已写并验证红灯：私聊 URL 冲突应调用分类器并回灌 scoring
- 已修复私聊纯图片 shared scoring 判定 `wait` 时保留 `effort=short` 和 `runtime_preset=lightweight`
- 已运行私聊定向、TimingGate 回归和全量测试

相关提交：

- `cda08e3 refactor(时机门控): 私聊接入共享评分`

### 阶段 9：timer / legacy cooldown 继续软化

状态：已完成。

目标：处理仍保留兼容 hard wait 的 timer path 和 `trigger_reason=""` legacy cooldown，尽量纳入 scoring 或 min interval 语义。

已完成：

- 已写计划文件：`.Codex/plans/timing-gate-scoring-phase9-cooldown.md`
- 已将 legacy 空 `trigger_reason` cooldown 接入 scoring shortcut
- 已将 timer fired cooldown 接入 scoring shortcut
- 已保留 scoring 不可用或非短路时的旧 hard wait fallback
- 已确认 timer 不绕过 talk_value gate、generation mismatch 和 direct bypass 语义
- 已运行 `TestGroupRuntime`、TimingGate 回归和全量测试

相关提交：

- `b2d5adf refactor(时机门控): 软化计时冷却路径`

### 阶段 10：session / platform 级模型层开关

状态：已完成。

目标：允许按 session 或 platform 控制 TimingGate 是否启用模型辅助、是否只用规则、是否只做 shadow。

已完成：

- 已写计划文件：`.Codex/plans/timing-gate-scoring-phase10-model-policy.md`
- 已新增 `core/timing_model_policy.py`，按 session > platform > default 解析策略
- 已注册 `timing_gate.model_policy.default`、`timing_gate.model_policy.platforms`、`timing_gate.model_policy.sessions`
- 已在群聊消息路径和 timer 路径接入 `enabled`、`rules_only`、`shadow`
- 已从 `/group/message` 的 `client_meta.platform` 透传 platform，默认 `qq`
- 已运行 Phase 10 定向、TimingGate 回归和全量测试

验收标准：

- 默认配置向后兼容
- 单测覆盖 session override、platform override、默认策略和 alias 归一化
- 响应调试字段能解释当前开关模式与来源

相关提交：

- `452f20b feat(时机门控): 添加模型层策略开关`

### 阶段 11：真实日志假阳率评估

状态：已完成。

目标：用真实 ChatLog 抽样评估 `s_ack`、`s_transport`、`w_marker` 的假阳性，并输出 shadow 对比结果。

已完成：

- 已写计划文件：`.Codex/plans/timing-gate-scoring-phase11-log-audit.md`
- 已新增 `core/eval_sampling/timing_signal_audit.py`，从 `ChatLog.meta_json.timing_gate.scoring` 抽取信号样本
- 已新增 `evals/timing_signal_audit.py`，支持从运行 DB 生成 JSON 审计报告
- 报告包含人工标注假阳率、`runtime_action` 与 `scoring_action` 的 shadow mismatch 统计
- 已运行阶段 11 定向、TimingGate 回归和全量测试

验收标准：

- 有可复跑脚本或 eval runner
- 记录样本量、误判类型和建议阈值
- 不凭感觉直接调参

相关提交：

- `efb04a0 feat(评测): 添加时机信号日志审计`

### 阶段 12：timing gate eval 基线与回归门禁

状态：已完成。

目标：把现有 timing eval 从手动运行升级为基线对比和回归门禁。

已完成：

- 已写计划文件：`.Codex/plans/timing-gate-scoring-phase12-eval-baseline.md`
- 已新增 `evals/baseline.py`，支持读取 baseline report、计算新增失败、修复失败、仍失败和 pass rate delta
- 已扩展 `SuiteReport`，可选携带 `baseline_diff` 和 `gate`
- 已扩展 `evals.run.run_suite()`，支持 `baseline_path`、`min_pass_rate`、`max_new_failures`
- 已扩展 CLI 参数：`--baseline`、`--min-pass-rate`、`--max-new-failures`
- 已运行阶段 12 定向、TimingGate 回归、CLI 门禁和全量测试

验收标准：

- 新增 baseline diff
- 支持阈值失败机制
- 核心 suite 可纳入提交前或 CI 验证流程

验证结果：

- 阶段定向：`11 passed, 1 warning`
- TimingGate 回归：`81 passed, 1 warning`
- CLI 门禁：`Gate passed`
- 全量测试：`1198 passed, 6 skipped, 113 warnings`

相关提交：

- `6dd126c docs(计划): 记录评测门禁计划`

### 阶段 13：文档收尾

状态：已完成。

目标：所有代码阶段完成后，同步 `docs/todo.md` 和相关设计文档，确保文档与真实代码状态一致。

已完成：

- 已同步 `docs/todo.md` 路线项 10，标记私聊 shared scoring、timer / legacy cooldown 软化、模型策略开关、真实日志审计和 eval 门禁已落地
- 已同步 `docs/todo.md` 路线项 8，记录 baseline diff / 阈值门禁已完成，CI 接入和标注闭环作为后续运营项
- 已同步 `docs/superpowers/specs/2026-06-16-timing-gate-scoring-design.md`，增加 2026-06-17 实施状态并更新验收清单

验收标准：

- `docs/todo.md` 不再描述已过时状态
- 剩余限制明确写出
- 文档变更单独提交

## 下一步

P2-2「标准化请求 / 响应信封」已完成只读审计，设计文档已随 `c984036` 提交，实现计划已写入 `.Codex/plans/message-envelope.md`。代码阶段先做统一信封 builder，再按 API owner、群聊 owner、push owner 拆分互不干扰的写入面；`api/routes.py` 的 push call site 由主线程或 API owner 单独集成，避免多个 worker 并行修改同一文件。P2-3 的出站 segments / CQ renderer / HTML-to-pic 不在本阶段展开。TimingGate 真实日志标注 / CI 接入属于运营延续项，不抢占 P2 执行顺序。
