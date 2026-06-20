# Nanobot Server 阶段计划 Walkthrough

计划日期：2026-06-17
更新日期：2026-06-20
本轮计划写入日期：2026-06-18
状态校准日期：2026-06-20

本文记录当前长期目标的完整阶段计划，用于继续推进 `docs/todo.md` 中的架构演进路线，并保持每个阶段完成后单独验证、单独提交。2026-06-18 已基于当时工作区、最近提交和 `docs/todo.md` 做过详细校准；2026-06-20 仅修正文档状态漂移，不重写历史执行记录。同日续跑补记：测试 helper 的 `asyncio.Runner` 兼容性问题已随 `cfdd9c2 test(异步): 移除 Runner 测试依赖` 收口，提交前全量回归结果为 `1380 passed, 6 skipped, 139 warnings in 100.75s`，非 vendor Python 代码中无 `asyncio.Runner` 命中。TimingGate scoring 可观测性收尾也已完成：设计提交 `4824036 docs(时机): 设计评分可观测收尾`，计划提交 `2820f7a docs(计划): 记录评分可观测收尾计划`，实现提交 `9d5817c feat(时机): 补齐评分可观测字段`；验证包括红灯 `s_transport_tier` 缺失、绿灯 `1 passed`、相邻回归 `7 passed`、WebUI build 退出码 0、全量回归 `1380 passed, 6 skipped, 139 warnings in 103.22s`。P1-6 已随 `101c457 docs(计划): 同步提示词收口最终状态` 完成文档收口；P1-7「残余同步 IO 审计与收口」已随 `b3d27f5 docs(计划): 同步同步 IO 收口状态` 完成实现、验证和文档归档。P1-8「模型能力校验」也已完成：设计文档已随 `ded7213 docs(模型能力): 设计请求能力校验` 提交，实现计划已随 `d4748d2 docs(计划): 记录模型能力校验计划` 提交；registry 能力归一化和候选硬过滤已随 `388c00f feat(模型能力): 归一化能力并过滤候选` 落地，直接 New API 请求能力推导已随 `d907a98 feat(模型能力): 推导直接请求能力需求` 落地，Bridge 主回复路由能力校验已随 `66fdfd9 feat(桥接): 接入回复模型能力校验` 落地，payload / SDK request 前 guard 与无视觉候选降级已随 `d2a7a1f fix(模型能力): 防止发送不兼容请求` 落地，`model_routing` eval 覆盖已随 `e1d3bef test(评测): 覆盖视觉模型路由` 落地。P2-1「工具配置增加 platform 维度」已完成：只读审计、设计文档和实现计划已完成，设计文档随 `d221180 docs(工具): 设计平台维度配置` 提交，实现计划已写入 `.Codex/plans/tool-platform-scope.md`；后端解析任务已随 `bb7489c feat(工具): 支持平台维度解析` 落地，运行时决策 platform 审计已随 `295e3f7 feat(工具): 记录平台维度决策` 落地，真实入口 platform 透传已随 `73bbe8a feat(消息): 透传客户端平台` 落地，Admin API platform 覆盖和预览已随 `d9a1bae feat(工具): 支持平台覆盖接口` 落地，WebUI 工具页 platform selector 和「指定平台」覆盖入口已随 `2b0e203 feat(工具): 配置平台覆盖` 落地。

P2-2「标准化请求 / 响应信封」的响应信封兼容双写已完成并通过最终验证：只读审计已完成，设计文档已随 `c984036 docs(消息): 设计响应信封标准` 提交，实现计划已写入 `.Codex/plans/message-envelope.md`；任务 1 共享 builder 已随 `147421b feat(消息): 构建响应信封` 提交，任务 2 `/chat` 非流式与 SSE done 信封已随 `57006f3 feat(消息): 返回私聊响应信封` 提交，任务 3 `/group/message` 信封已随 `49b3104 feat(消息): 返回群聊响应信封` 提交，任务 4 push owner 信封适配已随 `fc0eeaf feat(推送): 支持信封推送适配` 提交，任务 5 route push 集成已随 `0c37a30 feat(推送): 接入路由信封推送` 提交，任务 6 响应侧文档和最终验证随 `617aa25 docs(计划): 同步响应信封状态` 收口。P2-2.5「client_meta 边界层校验」设计文档已随 `ce05b35 docs(计划): 设计客户端元信息校验` 提交，`core/client_meta.py` 已随 `d92b632 feat(消息): 校验客户端元信息边界` 接入 `/chat` 与 `/group/message`，把路线项 5 的剩余尾项收口。P2-3「QQ 出站渲染契约」已完成设计、计划、renderer、push、schedule、route 回归、富媒体边界、prompt usage 同步、文档收口和最终验证：设计提交为 `c72ddb3`，计划提交为 `1f4aa69`，实现与测试提交为 `72a9751`、`0c8c590`、`f19b09b`、`f0bfbdf`、`04ff6d3`、`6aea7f8`；文档收口提交为 `docs(计划): 收口 QQ 出站渲染状态`。P2-4「Prompt platform × chat_type 二维适配」已完成设计、计划、核心编排、Bridge / Admin 透传、QQ 模板迁移和集成回归，提交为 `27e632f`、`164b215`、`ca93dc2`、`18d0b0d`、`17a7bd8`、`fe2d81b`。P3-1「SSE 真 token 流式剩余收敛」已完成设计、实现、文档收口和最终验证，提交为 `bca50b8`、`e56a406`、`d8e8703`、`84cb0cb`、`a987d31`、`88268a1`、`a5f705a`、`87f3b40`；最终验证结果为流式定向回归 `23 passed`、API / Bridge 回归 `145 passed`、全量测试 `1311 passed, 6 skipped`。P3-2「私聊 TimingGate 可观测补齐」已完成代码实现和最终验证，提交为 `14b47a5 feat(时机): 持久化私聊评分元信息`；随后 `/models/status` 本地模型回退缺失 import 的独立小修已随 `5c69b7e fix(模型): 修复状态接口本地模型回退` 提交。P3-3「TimingGate 持续评估」已完成三路只读审计、阶段拆分、P3-3A 标注审计复跑入口和 P3-3B 仓库自包含 CI / PR gate。TimingGate `s_bot` live path 收口已完成任务 1：设计提交为 `6463ee8 docs(时机): 设计 s_bot live path 收口`，计划提交为 `1795d04 docs(计划): 记录 s_bot live path 收口计划`，实现提交为 `2fcfad7 fix(时机): 接入其他 bot 软抑制评分`；`current_bot` 自身回声仍保持入口 hard stop，`explicit_bot` / `client_meta` 其他 bot sender 会标记为 `is_other_bot=True` 进入 `GroupRuntime`，`GroupPendingMessage` 透传该字段，`_score_timing()` 聚合 pending 后调用 `decide_timing(is_other_bot=any(m.is_other_bot for m in msgs))`，route 测试已断言 ChatLog meta 中 `s_bot=0.70`。任务 1 定向验证为 `3 passed, 21 warnings in 2.16s`，相邻回归为 `157 passed, 21 warnings in 23.30s`。私聊分类器失败 / 非法输出置信度收口已随 `0763802 fix(时机): 修复私聊分类器失败置信度` 完成，分类器 `invalid output fallback` / `classifier fallback` 会以 `model_confidence=0.0` 进入 shared scoring 的 `rule_fallback`，旧格式兼容仍保留 `0.5` 低置信。P4-1「评测数据集与标注闭环」已完成 expected 契约、候选标注、promote dry-run、离线 CLI、dataset / suite 边界和首个 `capability_model_routing` 能力数据集；P4-2「Admin 标注工作台契约化与 promote 预检 UI」已完成后端 expected contract schema/API、WebUI 契约化标注和 promote 预检流程；P4-3「能力契约评测数据集扩展」已完成 reply / rendering 两个能力数据集、baseline gate 和最终回归；P4-4「RAG baseline 门禁」已完成 RAG benchmark 专用 baseline diff、CLI gate、稳定 baseline、Admin API 和 WebUI 展示；P4-5A「统一评测 PR gate」已完成统一脚本和 CI 接入；P4-5B「周期性复跑与报告归档」已完成 keep-going 脚本、workflow schedule / manual dispatch 和 artifact 归档；P4-5C「RAG manual 样本扩充」已完成；P4-5D「RAG fixture 正例门禁」已完成；P4-5E「RAG knowledge fixture 引用正例门禁」已完成；P4-5F「RAG sticker fixture sendable 正例门禁」已完成；P4-5G「RAG group_memory fixture 正例门禁」已完成；P4-5H「RAG 过滤约束 fixture」已完成。下一阶段转向真实样本运营动作。

## 当前目标

TimingGate「规则信号 + 模型」混合决策主线已经完成阶段性落地，Prompt V2 默认 live 接管、H29 第一刀、P1-5 Prompt legacy 收口、P1-6 旧提示词资产收敛、P1-7 残余同步 IO 审计与 async 热路径隔离、P1-8 模型能力校验，以及 P2-1 工具 platform 维度配置均已完成。当前 `docs/todo.md` 路线项 4 已落地：`ToolOverride(scope_type="platform")`、`RuntimeToolDecision.platform`、真实入口 platform 透传、Admin API 平台覆盖预览和 WebUI 平台覆盖入口都已具备。路线项 5 已完成响应信封兼容双写和 `client_meta` 关键字段边界校验；P2-3「QQ 出站渲染契约」、P2-4「Prompt platform × chat_type 二维适配」、P3-1「SSE 真 token 流式剩余收敛」、P3-2「私聊 TimingGate 可观测补齐」、P3-3A「标注审计复跑入口」、P3-3B「TimingGate CI / PR gate」、P4-1「评测数据集与标注闭环」、P4-2「Admin 标注工作台契约化与 promote 预检 UI」、P4-3「能力契约评测数据集扩展」、P4-4「RAG baseline 门禁」、P4-5A「统一评测 PR gate」、P4-5B「周期性复跑与报告归档」、P4-5C「RAG manual 样本扩充」、P4-5D「RAG fixture 正例门禁」、P4-5E「RAG knowledge fixture 引用正例门禁」、P4-5F「RAG sticker fixture sendable 正例门禁」、P4-5G「RAG group_memory fixture 正例门禁」和 P4-5H「RAG 过滤约束 fixture」均已完成验证。TimingGate `s_bot` live path 偏差已完成代码收口：其他 bot sender 不再被 `bot_sender_no_timing` 统一 hard stop，而是进入 scoring 并触发 `s_bot` soft reject；当前 bot 自身回声仍 hard stop。私聊分类器失败 / 非法输出已收敛到 `model_confidence=0.0` 的规则兜底语义。当前默认下一步是路线项 8 的真实样本运营动作。

## 文档口径

- `docs/todo.md` 是当前架构路线的主参考，但它只记录路线级状态；当它与提交记录、`.Codex/plans/` 任务进度或本文件冲突时，以已提交代码和本文件的当前详细计划为准。本轮已重新核对路线项 5，确认 P2-2 响应信封兼容双写和 P2-2.5 `client_meta` 边界层解析 / 校验均已完成；路线项 5 后续只保留与 P2-3 出站渲染契约相邻的富媒体表达工作。
- 历史待办清单文件目前未跟踪且存在滞后状态，例如仍描述 Prompt V2 默认未启用、TimingGate 阶段仍在中途；后续仅作为历史核对材料，不作为优先级来源。
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
| 阶段 1：前置缺陷修复与稳定性打底 | 已完成 | BridgePool、日志回滚、待办状态同步 |
| 阶段 2：建立 TimingGate scoring 纯函数与 shadow 可观测 | 已完成 | `core/timing_score.py`、ChatLog/Admin/WebUI 调试字段 |
| 阶段 3：普通 ambient 规则短路 | 已完成 | 普通 ambient 确定性规则跳过模型 |
| 阶段 4：模型失败规则兜底 | 已完成 | 模型异常后使用 `rule_fallback` |
| 阶段 5：eval scoring 覆盖 | 已完成 | timing eval 支持 scoring 校验 |
| 阶段 6：`directed_to_other` 软化 | 已完成 | 指向他人从 hard no_reply 降级为抑制信号 |
| 阶段 7：ambient cooldown 软化 | 已完成 | 群聊环境 cooldown 接入 scoring shortcut |
| 阶段 7.5：同步待办进度 | 已完成 | `docs/todo.md` 同步混合决策进度 |
| 阶段 8：私聊接入 shared timing scoring | 已完成 | 私聊规则与分类器统一回灌 shared scoring |
| 阶段 9：timer / legacy cooldown 继续软化 | 已完成 | timer fired 与 legacy cooldown 接入 scoring shortcut |
| 阶段 10：session / platform 级模型层开关 | 已完成 | `enabled` / `rules_only` / `shadow` 策略解析与运行时接入 |
| 阶段 11：真实日志假阳率评估 | 已完成 | 时机信号审计 CLI、shadow mismatch 报告、阈值建议 |
| 阶段 12：timing gate eval 基线与回归门禁 | 已完成 | baseline diff、阈值门禁和 CLI 门禁输出 |
| 阶段 13：TimingGate 文档收尾 | 已完成 | 同步 `docs/todo.md` 与设计文档 |
| 阶段 13.5：TimingGate scoring 最终判定补漏 | 已完成 | 群聊正常模型路径采用 scoring blend 最终动作，旧格式解析降权到 `0.5` |
| 异步测试兼容性收口：移除 `asyncio.Runner` 测试依赖 | 已完成 | `tests/async_helpers.py` 统一复用 `core.async_bridge.run_awaitable_sync()`，提交 `cfdd9c2` |
| TimingGate scoring 可观测性收尾 | 已完成 | WebUI 展示 `conflict_score`、`soft_reject_cap`、`s_transport_tier` 和 wait 子信号，提交 `9d5817c` |
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
| P2-2 | 已完成 | 标准化请求 / 响应信封 | 设计已随 `c984036` 提交，实现计划已写入 `.Codex/plans/message-envelope.md`；共享 builder、`/chat` 非流式、SSE done、`/group/message`、定时任务 push、route push、响应侧文档和最终验证均已完成 | `c984036` / `147421b` / `57006f3` / `49b3104` / `fc0eeaf` / `0c37a30` / `617aa25` |
| P2-2.5 | 已完成 | `client_meta` 边界层校验 | 新增 `core/client_meta.py`，在 `/chat` 和 `/group/message` 入口归一化 `platform`、校验 `chat_type`、裁剪 trace 字段，并保留扩展字段 | `ce05b35` / `feat(消息): 校验客户端元信息边界` |
| P2-3 | 已完成 | QQ 出站渲染契约 | 以响应信封 `messages` 为 canonical 出站内容层，通过集中式 QQ renderer 派生旧 QQbot `message` 字符串 | `c72ddb3` / `1f4aa69` / `72a9751` / `0c8c590` / `f19b09b` / `f0bfbdf` / `04ff6d3` / `6aea7f8` / `docs(计划): 收口 QQ 出站渲染状态` |
| P2-4 | 已完成 | Prompt platform × chat_type 二维适配 | V2 模板按平台和会话类型拆分，QQ 专属约定下沉到 platform 分支；`web × private` 不再注入 QQ 平台模板 | `27e632f` / `164b215` / `ca93dc2` / `18d0b0d` / `17a7bd8` / `fe2d81b` |
| P3-1 | 已完成 | SSE 真 token 流式剩余收敛 | 已完成 `/chat` API delta 合并、`done` 权威测试、Bridge `final.replace`、API final 规范化、bounded queue / progress backpressure、断连 drain 和最终验证 | `bca50b8` / `e56a406` / `d8e8703` / `84cb0cb` / `a987d31` / `88268a1` / `a5f705a` / `87f3b40` / `docs(计划): 完成 SSE 最终验证` |
| P3-2 | 已完成 | 私聊 TimingGate 可观测补齐 | 私聊 `timing_scoring` 已随 user ChatLog、assistant ChatLog 和 ConversationTurn meta 持久化 | `feat(时机): 持久化私聊评分元信息` |
| P3-3A | 已完成 | TimingGate 标注审计复跑 | `timing_signal_audit` 已支持离线 labeled report / sidecar labels 复跑入口 | `feat(评测): 支持时机信号标注复跑` |
| P3-3B | 已完成 | TimingGate CI / PR gate | 已新增稳定 baseline、统一脚本、无 action scoring case 和 CI workflow | `ci(评测): 接入 timing gate 回归门禁` |
| TimingGate s_bot live path 收口 | 已完成 | 其他 bot sender live path 接入 `s_bot` soft reject；`current_bot` 自身回声仍 hard stop | `6463ee8` / `1795d04` / `2fcfad7` / `docs(时机): 收口 s_bot live path 状态` |
| TimingGate 私聊 fallback 置信度收口 | 已完成 | 私聊分类器失败 / 非法输出以 `model_confidence=0.0` 进入 `rule_fallback`；旧格式兼容仍为 `0.5` | `0763802` |
| P4-1 | 已完成 | 评测体系扩展 | expected 契约、候选标注、promote dry-run、离线 CLI、dataset / suite 边界、首个 `capability_model_routing` 能力数据集、文档收口和最终验证均已完成 | `e4fb70a` / `8b892a8` / `4f4cce7` / `b84cbf1` / `7a84084` / `71c3a53` / `a494f3b` / `5a8b601` |
| P4-2 | 已完成 | Admin 标注工作台契约化与 promote 预检 UI | 后端 expected 契约、WebUI 契约化标注、`note` / `expected` 分离、promote dry-run → apply 预检 UI、WebUI build 和全量回归均已完成 | `docs(评测): 设计标注工作台契约` / `feat(评测): 暴露期望契约校验` / `feat(评测): 契约化标注工作台` |
| P4-3 | 已完成 | 能力契约评测数据集扩展 | `capability_reply_contract` 与 `capability_rendering_contract` 数据集、baseline、离线 gate、渲染相邻回归和全量回归均已完成 | `docs(评测): 设计能力数据集扩展` / `docs(计划): 记录能力数据集扩展计划` / `feat(评测): 扩展回复契约数据集` / `feat(评测): 扩展渲染契约数据集` / `docs(评测): 收口能力数据集状态` |
| P4-4 | 已完成 | RAG baseline 门禁 | 为 `evals.rag_benchmark` 增加专用 baseline diff、CLI gate、稳定 baseline、Admin API 和 WebUI 展示 | `docs(评测): 设计 RAG baseline 门禁` / `docs(计划): 记录 RAG baseline 门禁计划` / `feat(评测): 增加 RAG baseline 计算` / `feat(评测): 支持 RAG baseline 门禁` / `test(评测): 固化 RAG baseline` / `feat(评测): 展示 RAG 门禁结果` |
| P4-5A | 已完成 | 统一评测 PR gate | `scripts/run_eval_pr_gate.sh` 串联 TimingGate、capability 和 RAG manual gate，CI workflow 已接入统一入口 | `docs(评测): 设计统一评测门禁` / `docs(计划): 记录统一评测门禁计划` / `ci(评测): 增加统一评测门禁脚本` / `ci(评测): 接入统一评测门禁` / `docs(评测): 收口统一评测门禁状态` |
| P4-5B | 已完成 | 周期性复跑与报告归档 | `scripts/run_eval_periodic.sh` 使用 keep-going 策略复跑稳定 gate，workflow 已支持 schedule / workflow_dispatch 并上传报告 artifact | `docs(评测): 设计周期复跑归档` / `docs(计划): 记录周期复跑计划` / `ci(评测): 增加周期评测脚本` / `ci(评测): 归档周期评测报告` / `docs(评测): 收口周期复跑状态` |
| P4-5C | 已完成 | RAG manual 样本扩充 | manual case 已从 3 扩到 9，baseline 合同测试已收紧，稳定 gate 已通过 | `de97759` / `5511a50` / `2189391` / `93fe947` / `dcf492b` |
| P4-5D | 已完成 | RAG fixture 正例门禁 | 新增固定 memory fixture DB、`manual+fixture` stable gate 和 positive metrics baseline | `6cbce35` / `375b9b3` / `dcf45e5` / `5b967b4` / `8b64ea0` / `docs(评测): 收口 RAG fixture 正例状态` |
| P4-5E | 已完成 | RAG knowledge fixture 引用正例门禁 | `positive_v1` 已扩展为 memory + knowledge 双正例，knowledge case 固定验证 `requires_citation=true` | `d694d53` / `a8ab8b8` / `1d19b95` |
| P4-5F | 已完成 | RAG sticker fixture sendable 正例门禁 | `positive_v1` 已扩展为 memory + knowledge + sticker 三正例，sticker case 固定验证 `requires_sendable=true` | `9008b0e` / `c8016cc` / `1538459` |
| P4-5G | 已完成 | RAG group_memory fixture 正例门禁 | `positive_v1` 已扩展为 memory + knowledge + sticker + group_memory 四正例，group_memory case 固定验证 `requires_group_id=true` | `fa1f387` / `b9e047a` / `7967caf` |
| P4-5H | 已完成 | RAG 过滤约束 fixture | memory / knowledge / sticker 正例已增加同 query decoy 与 forbidden 断言，group_memory 保留跨群 decoy | `7339f50` / `25b24ff` / `eedd21f` / `9a1bb3a` / `bbfc070` / `2e294a2` |

## 已完成阶段详情：P4-5H RAG 过滤约束 fixture

状态：P4-5H 已完成。设计文档为 `docs/superpowers/specs/2026-06-20-rag-filter-constraint-fixture-design.md`，设计提交为 `7339f50 docs(评测): 设计 RAG 过滤约束 fixture`，实现计划为 `.Codex/plans/rag-filter-constraint-fixture.md`，计划提交为 `25b24ff docs(计划): 记录 RAG 过滤约束 fixture 计划`。本阶段复用 `positive_v1` fixture preset，不新增 `constraint_v1` preset，不新增 scorer 字段，不改 gate 脚本参数，不改 Admin / WebUI，不改生产 DB schema。

目标：

- 保持 stable gate 为 9 个 manual constraint case + 4 个 fixture positive case。
- 在 `memory_fixture_positive_001` 中增加跨 user、跨 session、跨 source decoy，验证 forbidden hits 为空。
- 在 `knowledge_fixture_positive_001` 中增加 `trust_level`、`source_type`、`published_after` decoy，验证过滤条件不泄漏。
- 在 `sticker_fixture_positive_001` 中增加其他 stream 与 global decoy，验证 stream scope 和 `include_global=false`。
- 保留 P4-5G 的 group_memory 跨群 decoy。

计划项：

- [x] P4-5H 设计：写入 `docs/superpowers/specs/2026-06-20-rag-filter-constraint-fixture-design.md`。提交：`7339f50 docs(评测): 设计 RAG 过滤约束 fixture`。
- [x] P4-5H 实现计划：写入 `.Codex/plans/rag-filter-constraint-fixture.md`。提交：`25b24ff docs(计划): 记录 RAG 过滤约束 fixture 计划`。
- [x] 任务 1：强化 memory fixture 过滤约束。提交：`eedd21f feat(评测): 强化 memory fixture 过滤约束`。
- [x] 任务 2：强化 knowledge fixture 过滤约束。提交：`9a1bb3a feat(评测): 强化 knowledge fixture 过滤约束`。
- [x] 任务 3：强化 sticker fixture 过滤约束。提交：`bbfc070 feat(评测): 强化 sticker fixture 过滤约束`。
- [x] 任务 4：扩展 CLI fixture gate 与 baseline contract，固化 stable baseline。提交：`2e294a2 test(评测): 固化 RAG 过滤约束 fixture`。
- [x] 任务 5：同步 `docs/evals.md`、`docs/todo.md`、本文件和计划执行记录，完成最终验证与文档收口。

验证结果：

- memory 定向绿灯：`test_rag_benchmark_fixture_db_supports_memory_positive_case` 结果 `1 passed, 1 warning in 0.99s`。
- knowledge 定向绿灯：`test_rag_benchmark_fixture_db_supports_knowledge_positive_case` 结果 `1 passed, 1 warning in 0.97s`；memory + knowledge 组合结果 `2 passed, 1 warning in 1.02s`。
- sticker 定向绿灯：`test_rag_benchmark_fixture_db_supports_sticker_positive_case` 结果 `1 passed, 1 warning in 0.94s`；四个 positive fixture case 组合结果 `4 passed, 1 warning in 1.13s`。
- baseline 合同测试：`test_rag_benchmark_cli_runs_manual_fixture_positive_gate` 与 `test_rag_benchmark_baseline_file_matches_manual_gate_contract` 结果 `2 passed, 1 warning in 1.08s`。
- RAG stable gate：`evals.rag_benchmark.run --fixture positive_v1` 输出 `cases=13 passed=13 failed=0` 和 `Gate passed`。
- RAG 相邻回归：`tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `40 passed, 1 warning in 2.33s`。
- PR gate：`bash scripts/run_eval_pr_gate.sh` 输出评测守卫 `27 passed, 1 warning in 1.82s`，TimingGate、三个 capability gate 和 RAG gate 均输出 `Gate passed`，RAG gate 输出 `cases=13 passed=13 failed=0`。
- 周期性 gate：`bash scripts/run_eval_periodic.sh` 输出评测守卫 `27 passed, 1 warning in 1.71s`，各子 gate 均输出 `Gate passed`，RAG gate 输出 `cases=13 passed=13 failed=0`。
- 全量回归：`python -B -m pytest tests/ -v -p no:cacheprovider` 结果 `1380 passed, 6 skipped, 139 warnings in 104.70s`。

执行边界：

- `positive_v1` 仍是 4 个 positive case，不引入额外 fixture preset。
- decoy 只用于验证过滤边界，不扩大 stable gate 的 positive case 数。
- baseline 文件只保留 `suite`、`provider_mode`、`case_scope`、`metrics`、`failed_cases` 和 `case_scores`，不写入运行态 `cases` / `results` / `scores` / `baseline_diff` / `gate` 字段。
- 下一步转向真实样本运营动作。

## 已完成阶段详情：P4-5G RAG group_memory fixture 正例门禁

状态：P4-5G 已完成。设计文档为 `docs/superpowers/specs/2026-06-20-rag-group-memory-fixture-design.md`，设计提交为 `fa1f387 docs(评测): 设计 group_memory fixture 正例`，实现计划为 `.Codex/plans/rag-group-memory-fixture.md`，计划提交为 `b9e047a docs(计划): 记录 group_memory fixture 计划`。代码与 baseline 已随 `7967caf feat(评测): 增加 group_memory fixture 正例` 落地。本阶段复用 `positive_v1` fixture preset，不新增 gate 脚本参数，不改 Admin / WebUI，不改生产 DB schema，不启用 runtime provider。

目标：

- 将 `positive_v1` 从 memory + knowledge + sticker 三正例扩展为 memory + knowledge + sticker + group_memory 四正例。
- 新增 `group_memory_fixture_positive_001`，固定命中 `group_memory:9201:memory`。
- 通过 `requires_group_id=true` 断言非空 group memory 候选的 `checks.group_filter=true`。
- 同时 seed 跨群 decoy `group_memory:9202:memory`，验证 forbidden check 不泄漏。
- 同步 `evals/baselines/rag_benchmark.json`，使 stable gate 的 `positive_cases=4`，`source:group_memory.positive_cases=1`。

计划项：

- [x] P4-5G 设计：写入 `docs/superpowers/specs/2026-06-20-rag-group-memory-fixture-design.md`。提交：`fa1f387 docs(评测): 设计 group_memory fixture 正例`。
- [x] P4-5G 实现计划：写入 `.Codex/plans/rag-group-memory-fixture.md`。提交：`b9e047a docs(计划): 记录 group_memory fixture 计划`。
- [x] 任务 1：新增 group_memory fixture 红灯测试、CLI fixture gate 和 baseline 合同红灯。
- [x] 任务 2：新增 group_memory fixture seed、`_group_memory_positive_case()` 和跨群 decoy。
- [x] 任务 3：更新 RAG stable baseline，并验证 stable gate。
- [x] 任务 4：运行相邻回归、PR gate、periodic gate、全量测试和 diff 检查，并提交绿色代码阶段。提交：`7967caf feat(评测): 增加 group_memory fixture 正例`。
- [x] 任务 5：同步 `docs/evals.md`、`docs/todo.md`、本文件和计划执行记录，完成文档收口提交。

验证结果：

- 红灯：group_memory fixture 测试失败于 `ImportError: cannot import name 'GROUP_MEMORY_CANDIDATE_ID'`；CLI gate 红灯失败于 `assert 3 == 4`；baseline 合同红灯失败于 `assert 3 == 4`。
- 定向绿灯：`test_rag_benchmark_fixture_db_supports_group_memory_positive_case` 结果 `1 passed, 1 warning in 0.85s`；memory、knowledge、sticker、group_memory 四个 positive fixture case 结果 `4 passed, 1 warning in 1.10s`。
- RAG stable gate：`evals.rag_benchmark.run --fixture positive_v1` 输出 `cases=13 passed=13 failed=0` 和 `Gate passed`。
- RAG 相邻回归：`tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `40 passed, 1 warning in 2.65s`。
- group memory 相邻回归：`tests/test_group_memory_rag.py tests/test_group_memory_injection.py tests/test_semantic_adapters.py::test_group_memory_one_row_one_chunk tests/test_rag_debug.py::test_rag_debug_group_memory_uses_retrieval_service_not_stub` 结果 `16 passed, 21 warnings in 1.99s`。
- PR gate：`bash scripts/run_eval_pr_gate.sh` 输出评测守卫 `27 passed, 1 warning in 1.90s`，TimingGate、三个 capability gate 和 RAG gate 均输出 `Gate passed`，RAG gate 输出 `cases=13 passed=13 failed=0`。
- 周期性 gate：`bash scripts/run_eval_periodic.sh` 输出评测守卫 `27 passed, 1 warning in 1.80s`，各子 gate 均输出 `Gate passed`，RAG gate 输出 `cases=13 passed=13 failed=0`。
- 全量回归：`python -m pytest tests/ -v` 结果 `1380 passed, 6 skipped, 139 warnings in 105.13s`。
- diff 检查：`git diff --check -- tests/test_rag_benchmark.py evals/rag_benchmark/fixtures.py evals/baselines/rag_benchmark.json .Codex/plans/rag-group-memory-fixture.md` 无输出。

执行边界：

- group memory seed 只写 `GroupMemory` 行，不写 semantic index；benchmark adapter 继续通过 `GroupMemoryRetrievalService.select()` 进入真实检索路径。
- `GROUP_MEMORY_GROUP_ID` 使用 `group_` 前缀格式，兼容 `normalize_group_session_id()`。
- 本阶段不实现通用过滤约束 fixture，不修改 `GroupMemoryRetrievalService`，不调整 RAG 阈值。

下一步：

- 过滤约束 fixture 已在 P4-5H 完成；当前后续阶段转向真实样本运营动作。

## 已完成阶段详情：P4-5F RAG sticker fixture sendable 正例门禁

状态：P4-5F 已完成。设计文档为 `docs/superpowers/specs/2026-06-20-rag-fixture-sticker-sendable-design.md`，设计提交为 `9008b0e docs(评测): 设计 sticker fixture 发送门禁`，实现计划为 `.Codex/plans/rag-fixture-sticker-sendable.md`。代码与 baseline 已随 `1538459 feat(评测): 增加 sticker fixture 发送正例` 落地。本阶段复用 `positive_v1` fixture preset，不新增 gate 脚本参数，不改 Admin / WebUI，不改生产 DB schema，不启用 runtime provider。

目标：

- 将 `positive_v1` 从 memory + knowledge 双正例扩展为 memory + knowledge + sticker 三正例。
- 新增 `sticker_fixture_positive_001`，固定命中 `sticker:9101:sticker`。
- 通过 `requires_sendable=true` 断言非空 sticker 候选的 `checks.sendable=true`。
- 同步 `evals/baselines/rag_benchmark.json`，使 stable gate 的 `positive_cases=3`，`source:sticker.positive_cases=1`。

计划项：

- [x] P4-5F 设计：写入 `docs/superpowers/specs/2026-06-20-rag-fixture-sticker-sendable-design.md`。提交：`9008b0e docs(评测): 设计 sticker fixture 发送门禁`。
- [x] P4-5F 实现计划：写入 `.Codex/plans/rag-fixture-sticker-sendable.md`。
- [x] 任务 1：新增 sticker fixture 红灯测试、sendable scorer 守卫、CLI fixture gate 和 baseline 合同红灯。
- [x] 任务 2：新增 sticker fixture seed、`_sticker_positive_case()` 和 semantic index 写入。
- [x] 任务 3：更新 RAG stable baseline，运行 stable gate、相邻回归和全量验证，并提交绿色代码阶段。提交：`1538459 feat(评测): 增加 sticker fixture 发送正例`。
- [x] 任务 4：同步 `docs/evals.md`、`docs/todo.md`、本文件和计划执行记录，完成文档收口提交。

验证结果：

- 红灯：sticker fixture 测试失败于 `ImportError: cannot import name 'STICKER_CANDIDATE_ID'`；CLI gate 红灯失败于 `assert 2 == 3`；baseline 合同红灯失败于 `assert 2 == 3`。
- 定向绿灯：memory、knowledge、sticker 三个 positive fixture case 加 sendable scorer 守卫结果 `4 passed, 1 warning in 1.09s`。
- sticker 相邻回归：`tests/test_sticker_rag.py tests/test_sticker_memory.py tests/test_semantic_adapters.py::test_sticker_chunk_excludes_send_code_and_file_path tests/test_rag_debug.py::test_rag_debug_query_runs_sticker_search` 结果 `23 passed, 21 warnings in 3.26s`。
- RAG stable gate：`evals.rag_benchmark.run --fixture positive_v1` 输出 `cases=12 passed=12 failed=0` 和 `Gate passed`。
- RAG 相邻回归：`tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `39 passed, 1 warning in 2.27s`。
- 全量回归：`python -m pytest tests/ -v -p no:cacheprovider` 结果 `1377 passed, 6 skipped, 139 warnings in 105.89s`。

执行边界：

- sticker seed 使用 `StickerMemory`、`chunk_from_sticker()` 和 `upsert_semantic_chunks()`。
- benchmark candidate sendable 继续由 adapter 根据 `send_code` / `reply_token` 推导。
- 本阶段不实现 group_memory 正例，不修改 `GroupMemoryRetrievalService`，不调整 RAG 阈值。

验证计划：

- 定向 fixture 测试：memory、knowledge、sticker 三个 positive fixture case 加 sendable scorer 守卫。
- sticker 相邻回归：`tests/test_sticker_rag.py`、`tests/test_sticker_memory.py`、`tests/test_semantic_adapters.py::test_sticker_chunk_excludes_send_code_and_file_path`、`tests/test_rag_debug.py::test_rag_debug_query_runs_sticker_search`。
- stable gate：按 `.Codex/plans/rag-fixture-sticker-sendable.md` 的任务 3 命令运行 `evals.rag_benchmark.run --fixture positive_v1`，预期 `cases=12 passed=12 failed=0` 和 `Gate passed`。
- 提交前全量验证：`python -m pytest tests/ -v -p no:cacheprovider`。

## 已完成阶段详情：TimingGate s_bot live path 收口

状态：本阶段已完成设计、计划、任务 1 代码实现和任务 2 文档收口。设计文档为 `docs/superpowers/specs/2026-06-20-timing-gate-sbot-live-path-design.md`，实现计划为 `.Codex/plans/timing-gate-sbot-live-path.md`。本阶段只收口群聊 live ingress 到 TimingGate scoring 的偏差，不调整 TimingGate 阈值，不修改 Prompt Runtime 模板，不涉及 Admin / WebUI、RAG fixture 或生产 DB schema。

目标：

- 保留 `current_bot` 自身回声入口 hard stop，避免自回声进入 TimingGate。
- 让 `explicit_bot` / `client_meta` 其他 bot sender 不再走 `bot_sender_no_timing` hard return，而是进入 `GroupRuntime`。
- 在 `timing_message` 和 `GroupPendingMessage` 中透传 `is_other_bot`。
- `_score_timing()` 使用 `any(m.is_other_bot for m in msgs)` 调用 `decide_timing(is_other_bot=<聚合结果>)`。
- route 级测试覆盖 ChatLog meta 中 `timing_gate.scoring.signals.sub_signals.s_bot == 0.70`。

计划项：

- [x] 设计：提交 `6463ee8 docs(时机): 设计 s_bot live path 收口`。
- [x] 实现计划：提交 `1795d04 docs(计划): 记录 s_bot live path 收口计划`。
- [x] 任务 1：其他 bot sender 进入 runtime 并触发 `s_bot`，提交 `2fcfad7 fix(时机): 接入其他 bot 软抑制评分`。
- [x] 任务 2：文档收口、文档自检和最终验证。

验证记录：

- 任务 1 红灯：route 测试失败于响应仍带 `hard_rule=bot_sender_no_timing`；runtime 测试失败于 `gate_calls == []`，说明 `is_other_bot` 未参与 scoring。
- 任务 1 绿灯：三项定向测试结果 `3 passed, 21 warnings in 2.16s`。
- 任务 1 相邻回归：`tests/test_api.py tests/test_timing_runtime.py tests/test_timing_score.py` 结果 `157 passed, 21 warnings in 23.30s`。
- 任务 2 文档自检：占位符扫描无匹配，U+FFFD 扫描无匹配，`git diff --check` 无输出。
- 任务 2 相邻回归：`tests/test_api.py tests/test_timing_runtime.py tests/test_timing_score.py` 结果 `157 passed, 21 warnings in 23.75s`。
- 任务 2 全量验证：`python -B -m pytest tests/ -v -p no:cacheprovider` 结果 `1372 passed, 6 skipped, 139 warnings in 115.45s`。

提交边界：

- 设计阶段：`6463ee8 docs(时机): 设计 s_bot live path 收口`。
- 计划阶段：`1795d04 docs(计划): 记录 s_bot live path 收口计划`。
- 任务 1：`2fcfad7 fix(时机): 接入其他 bot 软抑制评分`。
- 任务 2：`docs(时机): 收口 s_bot live path 状态`。

## 已完成阶段详情：P4-5E RAG knowledge fixture 引用正例门禁

状态：P4-5E 已完成 knowledge fixture citation 正例。设计文档为 `docs/superpowers/specs/2026-06-20-rag-knowledge-fixture-citation-design.md`，实现计划为 `.Codex/plans/rag-knowledge-fixture-citation.md`。本阶段复用 `positive_v1` fixture preset，不新增 gate 脚本参数，不改 Admin / WebUI，不改生产 DB schema，不启用 runtime provider。

目标：

- 将 `positive_v1` 从 memory 单正例扩展为 memory + knowledge 双正例。
- 新增 `knowledge_fixture_positive_001`，固定命中 `knowledge:9001:chunk:0`。
- 通过 `requires_citation=true` 断言非空 knowledge 候选的 `checks.citation=true`。
- 同步 `evals/baselines/rag_benchmark.json`，使 stable gate 的 `positive_cases=2`，`source:knowledge.positive_cases=1`。

计划项：

- [x] P4-5E 设计：写入 `docs/superpowers/specs/2026-06-20-rag-knowledge-fixture-citation-design.md`。提交：`d694d53 docs(评测): 设计 knowledge fixture 引用门禁`。
- [x] P4-5E 实现计划：写入 `.Codex/plans/rag-knowledge-fixture-citation.md`。提交：`a8ab8b8 docs(计划): 记录 knowledge fixture 引用计划`。
- [x] 任务 1-3：新增红灯测试、knowledge fixture seed、baseline 合同和 stable gate 更新。提交：`1d19b95 feat(评测): 增加 knowledge fixture 引用正例`。
- [x] 任务 4：文档收口，同步 `docs/evals.md`、`docs/todo.md`、本文件和 `.Codex/plans/rag-knowledge-fixture-citation.md`。

验证记录：

- 任务 1 红灯：`test_rag_benchmark_fixture_db_supports_knowledge_positive_case` 失败于 `ImportError: cannot import name 'KNOWLEDGE_CANDIDATE_ID'`；`test_rag_benchmark_cli_runs_manual_fixture_positive_gate` 失败于 `assert 1 == 2`；baseline 合同测试失败于 `KeyError: 'knowledge_fixture_positive_001'`。
- scorer citation 守卫：`test_scorer_fails_requires_citation_when_candidate_lacks_citation` 结果 `1 passed, 1 warning in 0.76s`。
- 任务 2 定向绿灯：memory fixture、knowledge fixture 和 citation 守卫三项结果 `3 passed, 1 warning in 1.29s`。
- citation 相邻回归：`tests/test_knowledge_rag.py::test_knowledge_query_returns_citations`、`tests/test_knowledge_rag.py::test_knowledge_result_without_citation_is_dropped`、`tests/test_rag_debug.py::test_rag_debug_query_runs_knowledge_search_with_citation` 结果 `3 passed, 21 warnings in 1.84s`。
- RAG stable gate：使用本阶段 RAG stable gate 命令运行 `--fixture positive_v1`，输出 `cases=11 passed=11 failed=0` 和 `Gate passed`。
- RAG 相邻回归：`tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `37 passed, 1 warning in 2.78s`。
- 全量回归：`python -m pytest tests/ -v -p no:cacheprovider` 结果 `1374 passed, 6 skipped, 139 warnings in 105.13s`。

提交边界：

- 设计阶段：`d694d53 docs(评测): 设计 knowledge fixture 引用门禁`。
- 实现计划：`a8ab8b8 docs(计划): 记录 knowledge fixture 引用计划`。
- P4-5E 代码与 baseline：`1d19b95 feat(评测): 增加 knowledge fixture 引用正例`。
- P4-5E 文档收口：`docs(评测): 收口 knowledge fixture 引用状态`。

下一步：

- P4-5F sticker fixture sendable 正例已完成，代码与 baseline 提交为 `1538459 feat(评测): 增加 sticker fixture 发送正例`。
- group_memory 正例、过滤约束 fixture 和真实样本运营流程继续保留为后续独立阶段。

## 已完成阶段详情：P4-5D RAG fixture 正例门禁

状态：P4-5D 已完成 fixture-backed positive RAG case。设计文档为 `docs/superpowers/specs/2026-06-20-rag-fixture-positive-case-design.md`，实现计划为 `.Codex/plans/rag-fixture-positive-case.md`。本阶段不改 Admin / WebUI，不启用 runtime provider，不从真实生产 DB 采样；稳定 gate 由 9 个 manual `constraint_only` case 加 1 个固定 memory fixture positive case 组成。

目标：

- 新增 `evals/rag_benchmark/fixtures.py`，构建固定 SQLite fixture DB。
- 用 `memory_fixture_positive_001` 验证 deterministic provider 下能命中 `memory_digest:fixture-memory-positive-001:card:0`。
- 让 `evals.rag_benchmark.run --fixture positive_v1` 先创建 fixture DB，再追加 fixture case 并以只读 runner 执行。
- 将 `scripts/run_eval_pr_gate.sh` 和 `scripts/run_eval_periodic.sh` 的 RAG stable gate 切到 `manual+fixture`，并加入 `--min-hit-at-5 1.0` / `--min-mrr 1.0`。
- 同步 `evals/baselines/rag_benchmark.json`，使 `positive_cases=1`、`hit@5=1.0`、`mrr=1.0`。

计划项：

- [x] P4-5D 设计：写入 `docs/superpowers/specs/2026-06-20-rag-fixture-positive-case-design.md`。提交：`6cbce35 docs(评测): 设计 RAG fixture 正例门禁`。
- [x] P4-5D 实现计划：写入 `.Codex/plans/rag-fixture-positive-case.md`。提交：`375b9b3 docs(计划): 记录 RAG fixture 正例计划`。
- [x] 任务 1：新增 fixture builder、fixture 正例直跑测试和 `overall_fixture` 聚合分组。提交：`dcf45e5 feat(评测): 增加 RAG fixture 正例数据`。
- [x] 任务 2：runner CLI 接入 `--fixture` / `--fixture-db`，并覆盖 `manual+fixture` 报告。提交：`5b967b4 feat(评测): 支持 RAG fixture 门禁入口`。
- [x] 任务 3：baseline 合同、PR gate、周期性 gate 和稳定 baseline 切到 `manual+fixture`。提交：`8b64ea0 test(评测): 固化 RAG fixture 正例门禁`。
- [x] 任务 4：文档收口，同步 `docs/evals.md`、`docs/todo.md`、本文件和 `.Codex/plans/rag-fixture-positive-case.md`。

验证记录：

- 任务 1 红灯：`test_rag_benchmark_fixture_db_supports_memory_positive_case` 失败于 `ModuleNotFoundError: No module named 'evals.rag_benchmark.fixtures'`；`test_rag_aggregate_scores_tracks_fixture_origin` 失败于 `KeyError: 'overall_fixture'`。
- 任务 1 绿灯：两个新增定向测试结果 `2 passed, 1 warning in 0.92s`；`tests/test_rag_benchmark.py` 结果 `15 passed, 1 warning in 1.22s`。
- 任务 2 红灯：`test_rag_benchmark_cli_runs_manual_fixture_positive_gate` 失败于 argparse `unrecognized arguments: --fixture positive_v1 --fixture-db`。
- 任务 2 绿灯：同一测试结果 `1 passed, 1 warning in 0.93s`；`tests/test_rag_benchmark.py` 结果 `16 passed, 1 warning in 1.19s`。
- 任务 3 红灯：baseline 合同测试失败于 `assert 'manual' == 'manual+fixture'`；脚本守卫测试失败于缺少 `--fixture positive_v1`。
- 任务 3 绿灯：脚本守卫结果 `2 passed, 1 warning in 0.75s`；RAG fixture gate 输出 `cases=10 passed=10 failed=0` 和 `Gate passed`；相邻回归 `tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `35 passed, 1 warning in 2.61s`。
- 任务 4 相邻回归：`tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `35 passed, 1 warning in 2.51s`。
- 任务 4 PR gate：`bash scripts/run_eval_pr_gate.sh` 结果为评测守卫 `27 passed, 1 warning in 2.26s`，TimingGate、三个 capability gate 和 RAG gate 均输出 `Gate passed`，RAG gate 输出 `cases=10 passed=10 failed=0`。
- 任务 4 周期性 gate：`bash scripts/run_eval_periodic.sh` 结果为评测守卫 `27 passed, 1 warning in 2.22s`，各子 gate 均输出 `Gate passed`，RAG gate 输出 `cases=10 passed=10 failed=0`。
- 任务 4 文档自检：异常字符扫描无匹配，diff 占位词扫描无匹配，`git diff --check` 无输出。

提交边界：

- 设计阶段：`docs(评测): 设计 RAG fixture 正例门禁`。
- 实现计划：`docs(计划): 记录 RAG fixture 正例计划`。
- P4-5D-1 fixture builder：`feat(评测): 增加 RAG fixture 正例数据`。
- P4-5D-2 runner CLI：`feat(评测): 支持 RAG fixture 门禁入口`。
- P4-5D-3 baseline 和脚本：`test(评测): 固化 RAG fixture 正例门禁`。
- P4-5D-4 文档收口：`docs(评测): 收口 RAG fixture 正例状态`。

下一步：

- knowledge、sticker、group_memory fixture positive case 和过滤约束 fixture 均已完成；当前后续阶段转向真实样本运营动作。

## 已完成阶段详情：P4-5C RAG manual 样本扩充

状态：P4-5C 已完成第一轮 RAG manual 样本扩充。设计文档为 `docs/superpowers/specs/2026-06-18-rag-manual-case-expansion-design.md`，实现计划为 `.Codex/plans/rag-manual-case-expansion.md`。本阶段不改 Admin / WebUI，不纳入 generated case、runtime provider 或真实生产 DB；新增样本仍全部为稳定的 `constraint_only` manual case。

目标：

- 将 RAG manual deterministic gate 的样本从 3 个扩充到 9 个。
- 补强 baseline 合同测试，确保 `evals/baselines/rag_benchmark.json` 的 case id 集合与 enabled manual case 集合一致。
- 同步 `evals/baselines/rag_benchmark.json`，让稳定 RAG gate 保持 `cases=9 passed=9 failed=0`。
- 暂不加入 positive exact 样本；这类样本需要固定 fixture DB，否则会依赖真实 DB candidate id。

计划项：

- [x] P4-5C 设计：写入 `docs/superpowers/specs/2026-06-18-rag-manual-case-expansion-design.md`。提交：`de97759 docs(评测): 设计 RAG manual 扩样`。
- [x] P4-5C 实现计划：写入 `.Codex/plans/rag-manual-case-expansion.md`。提交：`5511a50 docs(计划): 记录 RAG manual 扩样计划`。
- [x] 任务 1：收紧 RAG baseline 合同测试。提交：`2189391 test(评测): 收紧 RAG baseline 合同`。
- [x] 插入修复：新增样本暴露 `published_after` 对未知发布时间资料放行的问题，修复知识库日期过滤。提交：`93fe947 fix(知识库): 过滤未知发布时间资料`。
- [x] 任务 2：新增 6 个 manual case，并把 RAG baseline 从 3 个 case 更新到 9 个 case。提交：`dcf492b test(评测): 扩充 RAG manual 样本`。
- [x] 任务 3：文档收口，同步 `docs/evals.md`、`docs/todo.md`、本文件和 `.Codex/plans/rag-manual-case-expansion.md`。

验证记录：

- 任务 1 绿灯保护：`tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract` 结果 `1 passed, 1 warning in 0.76s`。
- 任务 2 baseline 合同红灯：新增 6 个 manual case 后，同一测试失败于 `assert 3 == 9`。
- 插入修复红灯：`tests/test_knowledge_rag.py::test_knowledge_query_published_after_excludes_unknown_publish_date` 失败于未知发布时间文档被返回。
- 插入修复绿灯：同一测试结果 `1 passed, 1 warning in 0.53s`；`tests/test_knowledge_rag.py` 结果 `12 passed, 1 warning in 1.54s`。
- 任务 2 RAG 单文件绿灯：`tests/test_rag_benchmark.py` 结果 `13 passed, 1 warning in 1.06s`。
- 任务 2 RAG manual deterministic gate：输出 `cases=9 passed=9 failed=0` 和 `Gate passed`。
- 任务 2 相邻回归：`tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `32 passed, 1 warning in 1.96s`。
- 任务 3 文档自检：占位词扫描无匹配，U+FFFD 扫描通过，`git diff --check` 无输出。
- 任务 3 定向回归：`tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `32 passed, 1 warning in 1.90s`。
- 任务 3 PR gate：`bash scripts/run_eval_pr_gate.sh` 结果为评测守卫 `27 passed, 1 warning in 1.81s`，各子 gate 均输出 `Gate passed`，RAG gate 输出 `cases=9 passed=9 failed=0`。
- 任务 3 周期性 gate：`bash scripts/run_eval_periodic.sh` 结果为评测守卫 `27 passed, 1 warning in 1.75s`，各子 gate 均输出 `Gate passed`，RAG gate 输出 `cases=9 passed=9 failed=0`。
- 任务 3 全量回归：`python -B -m pytest tests/ -v -p no:cacheprovider` 结果 `1367 passed, 6 skipped, 139 warnings in 99.86s`。

提交边界：

- 设计阶段：`docs(评测): 设计 RAG manual 扩样`。
- 实现计划：`docs(计划): 记录 RAG manual 扩样计划`。
- P4-5C-1 baseline 合同守卫：`test(评测): 收紧 RAG baseline 合同`。
- P4-5C 插入修复：`fix(知识库): 过滤未知发布时间资料`。
- P4-5C-2 manual case 与 baseline：`test(评测): 扩充 RAG manual 样本`。
- P4-5C-3 文档收口：`docs(评测): 收口 RAG manual 扩样状态`。

下一步：

- P4-5D 到 P4-5H 的 fixture-backed positive 与过滤约束工作均已完成；当前后续阶段转向真实样本运营动作。

## 已完成阶段详情：P4-5B 周期性评测复跑与报告归档

状态：P4-5B 已完成周期性 keep-going 脚本、workflow schedule / manual dispatch 和 artifact 报告归档。设计文档为 `docs/superpowers/specs/2026-06-18-eval-periodic-reporting-design.md`，实现计划为 `.Codex/plans/eval-periodic-reporting.md`。本阶段不新增 RAG manual case、不更新 baseline、不引入 runtime provider。

目标：

- 新增 `scripts/run_eval_periodic.sh`，复用稳定 gate，但单步失败后继续运行后续 gate。
- `.github/workflows/timing-gate-eval.yml` 保持 PR / push fail-fast，同时为 `schedule` / `workflow_dispatch` 接入周期脚本。
- 使用 `actions/upload-artifact@v4` 在 `if: always()` 条件下上传 `evals/reports/*.json` 和 `tmp/rag_benchmark/reports/*.{json,md}`，保留 14 天。
- 文档化 UTC cron 与北京时间换算、报告读取顺序和失败处理边界。

计划项：

- [x] P4-5B 设计：写入 `docs/superpowers/specs/2026-06-18-eval-periodic-reporting-design.md`。提交：`b9e6f20 docs(评测): 设计周期复跑归档`。
- [x] P4-5B 实现计划：写入 `.Codex/plans/eval-periodic-reporting.md`。提交：`650edb2 docs(计划): 记录周期复跑计划`。
- [x] 任务 1：新增周期性 keep-going 脚本和脚本守卫测试。提交：`8912585 ci(评测): 增加周期评测脚本`。
- [x] 任务 2：workflow 接入 schedule、manual dispatch 和 artifact 上传。提交：`9e80a8b ci(评测): 归档周期评测报告`。
- [x] 任务 3：文档收口，同步 `docs/evals.md`、`docs/todo.md`、本文件和 `.Codex/plans/eval-periodic-reporting.md`。

验证记录：

- 任务 1 红灯：两个周期性脚本测试失败于 `assert script.exists()`。
- 任务 1 绿灯：两个周期性脚本测试结果 `2 passed, 1 warning in 0.73s`。
- 任务 1 脚本验证：`bash scripts/run_eval_periodic.sh` 结果为评测守卫 `24 passed, 1 warning in 1.76s`，所有子 gate 均输出 `Gate passed`。
- 任务 2 红灯：workflow 三个新增测试失败于缺少 `workflow_dispatch`、`actions/upload-artifact@v4` 和 `retention-days: 14`。
- 任务 2 绿灯：workflow 四个定向测试结果 `4 passed, 1 warning in 0.82s`。
- 任务 2 评测守卫组合：`tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py` 结果 `27 passed, 1 warning in 1.75s`。
- 任务 3 文档自检：占位词扫描无匹配，U+FFFD 扫描通过，`git diff --check` 无输出。
- 任务 3 定向回归：`tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py tests/test_rag_benchmark.py` 结果 `40 passed, 1 warning in 2.42s`。
- 任务 3 周期性脚本：`bash scripts/run_eval_periodic.sh` 结果为评测守卫 `27 passed, 1 warning in 1.78s`，各子 gate 均输出 `Gate passed`。
- 任务 3 PR gate：`bash scripts/run_eval_pr_gate.sh` 结果为评测守卫 `27 passed, 1 warning in 1.76s`，各子 gate 均输出 `Gate passed`。
- 任务 3 全量回归：`python -B -m pytest tests/ -v -p no:cacheprovider` 结果 `1366 passed, 6 skipped, 139 warnings in 101.52s`。

提交边界：

- 设计阶段：`docs(评测): 设计周期复跑归档`。
- 实现计划：`docs(计划): 记录周期复跑计划`。
- P4-5B-1 周期性脚本：`ci(评测): 增加周期评测脚本`。
- P4-5B-2 workflow artifact：`ci(评测): 归档周期评测报告`。
- P4-5B-3 文档收口：`docs(评测): 收口周期复跑状态`。

下一步：

- P4-5C 推进 RAG manual 样本扩充。

## 已完成阶段详情：P4-5A 统一评测 PR gate

状态：P4-5A 已完成统一脚本和 CI 接入。设计文档为 `docs/superpowers/specs/2026-06-18-eval-pr-gate-design.md`，实现计划为 `.Codex/plans/eval-pr-gate.md`。本阶段只收敛稳定离线 gate，不新增周期性复跑、不更新 RAG manual case、不调整 baseline。

目标：

- 新增 `scripts/run_eval_pr_gate.sh`，本地和 CI 共用同一个入口。
- 复用 `scripts/run_timing_gate_gate.sh`，并串联 `capability_model_routing`、`capability_reply_contract`、`capability_rendering_contract` 和 RAG manual deterministic gate。
- 将 `.github/workflows/timing-gate-eval.yml` 的 workflow 名称和 job 改为 Eval PR Gate，执行入口改为统一脚本。
- 增加测试守卫，防止脚本或 workflow 漏掉稳定 suite。

计划项：

- [x] P4-5A 设计：写入 `docs/superpowers/specs/2026-06-18-eval-pr-gate-design.md`。提交：`a520fed docs(评测): 设计统一评测门禁`。
- [x] P4-5A 实现计划：写入 `.Codex/plans/eval-pr-gate.md`。提交：`bac2192 docs(计划): 记录统一评测门禁计划`。
- [x] 任务 1：新增统一 gate 脚本和脚本守卫测试。提交：`8aa08db ci(评测): 增加统一评测门禁脚本`。
- [x] 任务 2：CI workflow 接入统一 gate。提交：`d8f5739 ci(评测): 接入统一评测门禁`。
- [x] 任务 3：文档收口，同步 `docs/evals.md`、`docs/todo.md`、本文件和 `.Codex/plans/eval-pr-gate.md`。提交：`docs(评测): 收口统一评测门禁状态`。

验证记录：

- 任务 1 红灯：`tests/test_eval_baseline.py::test_eval_pr_gate_script_runs_stable_suites` 失败于 `assert script.exists()`。
- 任务 1 绿灯：同一测试结果 `1 passed, 1 warning in 0.48s`。
- 任务 1 统一 gate：`bash scripts/run_eval_pr_gate.sh` 结果为评测守卫 `22 passed, 1 warning in 1.53s`，`timing_gate`、`capability_model_routing`、`capability_reply_contract`、`capability_rendering_contract` 和 RAG manual deterministic gate 均输出 `Gate passed`。
- 任务 2 红灯：`tests/test_eval_baseline.py::test_eval_pr_gate_workflow_runs_unified_script` 失败于 workflow 名称仍为 `TimingGate Eval`。
- 任务 2 绿灯：同一测试结果 `1 passed, 1 warning in 0.58s`。
- 任务 2 评测守卫组合：`tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py tests/test_rag_benchmark.py` 结果 `35 passed, 1 warning in 2.21s`。
- 任务 3 文档自检：占位词扫描无匹配，U+FFFD 扫描通过，`git diff --check` 无输出。
- 任务 3 定向回归：`tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py tests/test_rag_benchmark.py` 结果 `35 passed, 1 warning in 2.34s`。
- 任务 3 统一 gate：`bash scripts/run_eval_pr_gate.sh` 结果为评测守卫 `22 passed, 1 warning in 1.77s`，各子 gate 均输出 `Gate passed`。
- 任务 3 全量回归：`python -B -m pytest tests/ -v -p no:cacheprovider` 结果 `1361 passed, 6 skipped, 139 warnings in 100.83s`。

提交边界：

- 设计阶段：`docs(评测): 设计统一评测门禁`。
- 实现计划：`docs(计划): 记录统一评测门禁计划`。
- P4-5A-1 统一 gate 脚本：`ci(评测): 增加统一评测门禁脚本`。
- P4-5A-2 workflow 接入：`ci(评测): 接入统一评测门禁`。
- P4-5A-3 文档收口：`docs(评测): 收口统一评测门禁状态`。

下一步：

- P4-5B 周期性复跑与报告归档已完成。
- P4-5C 推进 RAG manual 样本扩充。

## 已完成阶段详情：P4-4 RAG baseline 门禁

状态：P4-4 已完成。设计文档为 `docs/superpowers/specs/2026-06-18-rag-labeling-baseline-gate-design.md`，实现计划为 `.Codex/plans/rag-baseline-gate.md`。本阶段保留 `evals.rag_benchmark` 专用体系，不并入通用 `EvalCase`；generated case 仍只作为本地 DB 采样候选，仓库稳定 gate 只纳入 manual deterministic case。

目标：

- 新增 RAG benchmark 专用 baseline diff 和 gate 纯函数。
- CLI 支持 `--manual-only`、`--baseline`、`--min-pass-rate`、`--max-new-failures`、`--max-degraded-rate` 和 `--max-unexpected-source-rate`。
- 报告 JSON / Markdown 写入 `provider_mode`、`case_scope`、`case_scores`、`failed_cases`、`baseline_diff` 和 `gate`。
- 固化 `evals/baselines/rag_benchmark.json`，当前覆盖 3 个 manual safe constraint case。
- Admin RAG Benchmark run 支持 gate 参数，响应和 latest report 透传 `baseline_diff` / `gate`。
- WebUI RAG Benchmark 页面展示 `Gate passed` / `Gate failed`、gate errors、`new_failed_cases`、`fixed_cases` 和 `still_failed_cases`。

计划项：

- [x] P4-4 设计：写入 `docs/superpowers/specs/2026-06-18-rag-labeling-baseline-gate-design.md`。提交：`0e06cdb docs(评测): 设计 RAG baseline 门禁`。
- [x] P4-4 实现计划：写入 `.Codex/plans/rag-baseline-gate.md`。提交：`d425828 docs(计划): 记录 RAG baseline 门禁计划`。
- [x] 任务 1：新增 `evals/rag_benchmark/baseline.py` 和纯函数测试。提交：`798ae33 feat(评测): 增加 RAG baseline 计算`。
- [x] 任务 2：CLI gate 与报告输出。提交：`7fe0171 feat(评测): 支持 RAG baseline 门禁`。
- [x] 任务 3：稳定 RAG baseline 文件。提交：`3695027 test(评测): 固化 RAG baseline`。
- [x] 任务 4：Admin API 和 WebUI 展示 gate 结果。提交：`eae32b4 feat(评测): 展示 RAG 门禁结果`。
- [x] 任务 5：文档收口，同步 `docs/evals.md`、`docs/todo.md`、本文件和 `.Codex/plans/rag-baseline-gate.md`。

验证记录：

- 任务 1 红灯：`tests/test_rag_benchmark.py::test_rag_baseline_diff_reports_new_fixed_and_metric_deltas` 失败于 `ModuleNotFoundError: No module named 'evals.rag_benchmark.baseline'`。
- 任务 1 绿灯：同一测试结果 `1 passed, 1 warning in 0.75s`。
- 任务 2 红灯：`tests/test_rag_benchmark.py::test_rag_benchmark_cli_fails_gate_on_new_failure` 失败于 `TypeError: main() takes 0 positional arguments but 1 was given`。
- 任务 2 绿灯：CLI gate 三个定向测试结果 `3 passed, 1 warning in 0.87s`。
- 任务 3 红灯：`tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract` 失败于缺少 `evals/baselines/rag_benchmark.json`。
- 任务 3 绿灯：同一测试结果 `1 passed, 1 warning in 0.74s`；正式 RAG gate 输出 `cases=3 passed=3 failed=0` 和 `Gate passed`；`tests/test_rag_benchmark.py` 结果 `13 passed, 1 warning in 1.07s`。
- 任务 4 Admin 红灯：`tests/test_rag_benchmark_admin.py::test_benchmark_run_returns_gate_when_baseline_requested` 失败于缺少 `BENCHMARK_BASELINE_PATH`。
- 任务 4 Admin 绿灯：同一测试结果 `1 passed, 21 warnings in 1.09s`；`tests/test_rag_benchmark_admin.py` 结果 `12 passed, 21 warnings in 4.88s`。
- 任务 4 WebUI 红灯：`tests/test_rag_benchmark_webui.py` 结果 `1 failed, 3 passed, 1 warning`，失败点为页面源码缺少 `baseline_path`。
- 任务 4 WebUI 绿灯：`tests/test_rag_benchmark_webui.py` 结果 `4 passed, 1 warning in 0.54s`。
- P4-4B 集成回归：`tests/test_rag_benchmark_admin.py tests/test_rag_benchmark_webui.py` 结果 `16 passed, 21 warnings in 5.45s`；`tests/test_rag_benchmark.py tests/test_rag_benchmark_admin.py tests/test_rag_benchmark_webui.py` 结果 `29 passed, 21 warnings in 6.15s`。
- WebUI build：`npm --prefix webui run build` 退出码为 0，保留 Vite 既有 chunk size 和 plugin timing 警告。
- P4-4C 全量回归：`python -B -m pytest tests/ -v -p no:cacheprovider` 结果 `1359 passed, 6 skipped, 139 warnings in 99.40s`。

提交边界：

- 设计阶段：`docs(评测): 设计 RAG baseline 门禁`。
- 实现计划：`docs(计划): 记录 RAG baseline 门禁计划`。
- P4-4A-1 baseline 纯函数：`feat(评测): 增加 RAG baseline 计算`。
- P4-4A-2 CLI gate 与报告输出：`feat(评测): 支持 RAG baseline 门禁`。
- P4-4A-3 稳定 baseline：`test(评测): 固化 RAG baseline`。
- P4-4B Admin / WebUI 展示：`feat(评测): 展示 RAG 门禁结果`。
- P4-4C 文档收口：`docs(评测): 收口 RAG 门禁状态`。

下一步：

- P4-5A 统一 PR gate 和 P4-5B 周期性复跑与报告归档已完成。
- P4-5C 继续推进 RAG manual 样本扩充。

## 已完成阶段详情：P4-3 能力契约评测数据集扩展

状态：P4-3 已完成。P4-3A Reply Contract 数据集、P4-3B Rendering Contract runner / 数据集、两个能力数据集 baseline gate、渲染相邻回归和全量回归均已完成。P4-3 采用「数据集优先 + 最小 runner」方案，设计文档为 `docs/superpowers/specs/2026-06-18-capability-contract-eval-datasets-design.md`，实现计划为 `.Codex/plans/capability-contract-eval-datasets.md`。本阶段未做 RAG 标注闭环、未接入更多 suite PR gate、未修改生产 QQ renderer 或真实 QQ push。

目标：

- 新增 `capability_reply_contract` dataset 和 baseline，复用现有 `reply_contract` / `group_reply` runner 与 scorer。
- 新增 `capability_rendering_contract` dataset 和 baseline，新增离线 `rendering_contract` runner。
- 保持 dataset 与 suite 分层：dataset 负责目录和门禁维度，case 内 `suite` 负责选择 runner。
- 让两个能力数据集都能通过 baseline gate 独立验收。
- 让 Admin expected contract 暴露 `rendering_contract` suite preset，继续只提交 scorer 可评分字段。

计划项：

- [x] 只读审计文档边界：确认 P4-3 聚焦 reply / rendering 能力数据集，RAG 和更多 suite PR gate 分别留给 P4-4 / P4-5。
- [x] 只读审计 evals 代码结构：确认 `load_cases()` 按 dataset 目录加载，`run_case()` 按 case 内 `suite` 分发 runner。
- [x] 方案选择：采用 reply 复用现有 runner、rendering 新增最小离线 runner 的方案。
- [x] 设计文档：写入 `docs/superpowers/specs/2026-06-18-capability-contract-eval-datasets-design.md` 并完成规格自检。
- [x] 实现计划：写入 `.Codex/plans/capability-contract-eval-datasets.md`，明确 TDD 步骤、文件清单、验证顺序和子 agent 协作边界。
- [x] P4-3A：新增 `capability_reply_contract` cases、baseline、测试和文档。
- [x] P4-3B：新增 `rendering_contract` runner、expected preset、`capability_rendering_contract` cases、baseline、测试和文档。
- [x] P4-3C：运行两个能力数据集 gate、相关回归和全量测试，更新 walkthrough / todo 状态并提交。

子 agent 分配：

- Reply dataset agent：只读检查 `group_reply_runner`、现有 regression reply cases 和 scorer 字段，输出 reply contract case 清单与 expected 字段建议。
- Rendering runner agent：只读检查 `core/qq_outbound_renderer.py`、`tests/test_qq_outbound_renderer.py` 和 `tests/test_push_envelope.py`，输出 runner 输入输出契约与渲染 case 清单。
- Baseline / docs agent：只读检查 `evals/run.py`、`evals/baseline.py`、`docs/evals.md` 和已有 baseline 文件，输出 baseline 文件结构与运行命令。

验证计划：

- 设计阶段：设计文档占位词扫描、U+FFFD 扫描、`git diff --check`。
- P4-3A：红灯 `tests/test_eval_baseline.py::test_capability_reply_contract_dataset_uses_reply_runner` 失败于 `cases` 为空；绿灯同一测试 `1 passed, 1 warning in 0.83s`；`python -B -m evals.run --suite capability_reply_contract --baseline evals/baselines/capability_reply_contract.json --min-pass-rate 1.0 --max-new-failures 0` 输出 `total=3 passed=3 failed=0` 和 `Gate passed`。
- P4-3B：红灯 `tests/test_eval_candidate_contract.py::test_rendering_contract_expected_preset_uses_scoreable_fields tests/test_eval_baseline.py::test_capability_rendering_contract_dataset_runs_offline` 失败于缺少 `rendering_contract` preset 和数据集为空；绿灯同一集合 `2 passed, 1 warning in 0.89s`；渲染相邻回归 `tests/test_qq_outbound_renderer.py tests/test_push_envelope.py` 结果 `17 passed, 1 warning in 1.03s`；`python -B -m evals.run --suite capability_rendering_contract --baseline evals/baselines/capability_rendering_contract.json --min-pass-rate 1.0 --max-new-failures 0` 输出 `total=5 passed=5 failed=0` 和 `Gate passed`。
- P4-3C：定向评测回归 `tests/test_eval_candidate_contract.py tests/test_eval_baseline.py` 结果 `34 passed, 21 warnings in 3.10s`；reply gate 输出 `total=3 passed=3 failed=0` 和 `Gate passed`；rendering gate 输出 `total=5 passed=5 failed=0` 和 `Gate passed`；渲染相邻回归 `tests/test_qq_outbound_renderer.py tests/test_push_envelope.py` 结果 `17 passed, 1 warning in 0.72s`；全量回归 `python -B -m pytest tests/ -v -p no:cacheprovider` 结果 `1353 passed, 6 skipped, 139 warnings in 99.23s`。

提交边界：

- 设计阶段：`docs(评测): 设计能力数据集扩展`。
- 实现计划：`docs(计划): 记录能力数据集扩展计划`。
- P4-3A Reply Contract 数据集：`feat(评测): 扩展回复契约数据集`。
- P4-3B Rendering Contract 数据集：`feat(评测): 扩展渲染契约数据集`。
- P4-3C 收口：`docs(评测): 收口能力数据集状态`。

## 已完成阶段详情：P4-2 Admin 标注工作台契约化与 promote 预检 UI

状态：P4-2 已完成。P4-2A 后端契约已完成红灯、实现、定向回归和提交前全量验证；P4-2B WebUI 工作台已完成 label modal 契约化、promote modal 两阶段预检、WebUI build、候选闭环回归和全量回归。设计文档为 `docs/superpowers/specs/2026-06-18-admin-eval-workbench-contract-design.md`；实现计划为 `.Codex/plans/admin-eval-workbench-contract.md`。本阶段不重复 P4-1 的 store / CLI / runner，不新增 RAG benchmark，也不扩更多 suite gate。

目标：

- 后端暴露 canonical expected contract，包含 `scoreable_keys`、`field_schema`、`suite_presets` 和 `deprecated_keys`。
- `validate_expected_contract()` 从 key 白名单升级到类型 / 枚举校验，拒绝 `"false"`、`123`、非法 `timing_action` 和旧 UI 字段。
- Admin label API 保留 `expected_json` 兼容，但拒绝 `expected` 与 `expected_json` 内容冲突。
- Promote apply 响应与 dry-run 对齐，返回 `dry_run=false`、`case_id`、`suite`、`target_dataset` 和真实 `path`。
- WebUI 标注表单从 `/evals/expected-contract` 读取契约，只提交 scorer 会读取的 expected 字段；人工解释写入 `note`。
- WebUI promote 改为 modal，两阶段执行 dry-run → apply，用户必须确认 `target_dataset` 和 `path` 后才写正式 case。

已完成计划项：

- [x] 三路只读审计：前端表单、后端契约 / API、测试 / 文档边界均已完成。
- [x] 设计文档：`docs/superpowers/specs/2026-06-18-admin-eval-workbench-contract-design.md`。
- [x] 实现计划：`.Codex/plans/admin-eval-workbench-contract.md`。
- [x] 子 agent 分工：后端契约 agent、WebUI 工作台 agent、验证 agent。

实现阶段拆分：

- [x] P4-2A-1：新增 expected contract endpoint，返回 `scoreable_keys`、`field_schema`、`suite_presets` 和 `deprecated_keys`。
- [x] P4-2A-2：收紧 expected 类型 / 枚举校验，并拒绝 `expected` / `expected_json` 冲突。
- [x] P4-2A-3：对齐 promote apply 响应字段，保持 dry-run / apply 契约一致。
- [x] P4-2B-1：WebUI label modal 加载 expected contract，移除旧 expected 字段，`note` 与 `expected` 分离。
- [x] P4-2B-2：WebUI promote modal 支持 `target_dataset`、dry-run 预检、预检结果展示和 apply 二次确认。
- [x] P4-2B-3：运行 WebUI 静态测试、候选闭环回归和 `npm --prefix webui run build`。
- [x] P4-2 收口：同步 `docs/evals.md`、`docs/todo.md`、本文件和最终验证记录，并完成本轮全量回归。

子 agent 分配：

- 后端契约 agent：负责 `evals/expected_contract.py`、`api/admin_routes.py`、`tests/test_eval_candidate_contract.py`，提交 P4-2A。
- WebUI 工作台 agent：负责 `webui/src/features/evals/EvalsPage.jsx`、`tests/test_webui_admin_redesign.py`，依赖 P4-2A 的 endpoint 契约，提交 P4-2B。
- 验证 agent：只读审查最终 diff，确认未误改 P4-1 store / CLI / runner，并核对验证输出；主线程决定是否采纳结论。

验证计划：

- 计划阶段：文档占位词扫描、U+FFFD 扫描、`git diff --check`。
- P4-2A：`tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py`。
- P4-2B：`tests/test_webui_admin_redesign.py` 和 `npm --prefix webui run build`。
- 最终回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider`。

P4-2A 验证记录：

- 红灯：新增 endpoint、类型 / 枚举、label 冲突和 promote apply 响应用例后运行新增集合，结果 `8 failed, 2 passed, 21 warnings in 7.02s`；失败点为 endpoint 404、类型校验未触发、冲突请求返回 200、apply 响应缺 `dry_run`。
- 绿灯：同一新增集合复跑，结果 `10 passed, 21 warnings in 1.82s`。
- 后端定向回归：`tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py`，结果 `24 passed, 21 warnings in 3.30s`。
- 全量回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider`，结果 `1349 passed, 6 skipped, 139 warnings in 103.57s`。

P4-2B 验证记录：

- 红灯：WebUI 工作台新增静态守卫后，`tests/test_webui_admin_redesign.py` 结果为 `2 failed, 16 passed, 1 warning`；失败点为未加载 expected contract 和 promote 仍直接裸 `POST /promote`。
- 绿灯：`tests/test_webui_admin_redesign.py` 结果为 `18 passed, 1 warning in 0.70s`。
- WebUI build：`npm --prefix webui run build` 退出码为 0，构建耗时 `9.76s`；保留 Vite 既有 chunk size 和 plugin timing 警告。
- 候选闭环回归：`tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py`，结果 `24 passed, 21 warnings in 3.29s`。
- 全量回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider`，结果 `1350 passed, 6 skipped, 139 warnings in 96.82s`。

提交边界：

- 计划阶段：`docs(评测): 设计标注工作台契约`。
- P4-2A 后端契约：`feat(评测): 暴露期望契约校验`。
- P4-2B WebUI 工作台：`feat(评测): 契约化标注工作台`。
- 文档收口：随 P4-2B 提交同步阶段状态；若全量验证后仍需单独调整，再使用 `docs(评测): 收口标注工作台状态`。

## 已完成阶段详情：P3-2 私聊 TimingGate 可观测补齐

状态：P3-2 已完成 TDD 红灯、代码实现、定向回归和文档收口。实现计划为 `.Codex/plans/private-timing-scoring-meta.md`，提交建议为 `feat(时机): 持久化私聊评分元信息`。

目标：

- 私聊 `PrivateDecision.timing_scoring` 已计算后，随本轮对话一起持久化到 ChatLog meta。
- user ChatLog、assistant ChatLog 和 ConversationTurn meta 都写入同一个 `timing_gate` 片段，便于调试私聊 action、reason、effort、runtime_preset 与 scoring 明细。
- 群聊 timing 事件保持原链路，不把私聊 helper 接入群聊路径。

已完成任务：

- [x] 写入 P3-2 实现计划：`.Codex/plans/private-timing-scoring-meta.md`。
- [x] 新增私聊成功回复回归测试，断言 user / assistant ChatLog meta 均包含 `timing_gate.scoring`。
- [x] 新增私聊 `no_reply` 回归测试，断言静默路径也持久化 `timing_gate`。
- [x] 新增 `_private_timing_meta()`，把 `PrivateDecision` 转成稳定 meta 片段。
- [x] 扩展 `_persist_chat_turn()`，支持 `timing_meta` keyword-only 参数并合并到 user ChatLog、assistant ChatLog 和 ConversationTurn。
- [x] 在私聊 `no_reply`、`casual_template`、guardrail `silent`、stream 成功 / 错误 / prompt audit、非流式成功 / 错误 / prompt audit 路径传入私聊 timing meta。
- [x] 同步 `docs/todo.md`、本文件和实现计划状态。

验证记录：

- 红灯：`python -m pytest tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta -v`，结果 `2 failed, 21 warnings`；失败点为 `KeyError: 'timing_gate'`。
- 绿灯：同一命令初次结果 `2 passed, 21 warnings in 1.60s`；提交前复跑结果 `2 passed, 21 warnings in 1.63s`。
- 定向回归：`python -m pytest tests/test_private_timing.py tests/test_api.py -k "private_timing or private_buffer or proxy_chat_persists_private_timing_scoring_meta or proxy_chat_no_reply_persists_private_timing_scoring_meta" -v`，初次结果 `12 passed, 73 deselected, 21 warnings in 1.87s`；提交前复跑结果 `12 passed, 73 deselected, 21 warnings in 2.40s`。
- 相邻回归：`python -m pytest tests/test_api.py tests/test_chat_response_envelope.py tests/test_streaming_response_envelope.py -v`，初次结果 `87 passed, 21 warnings in 20.24s`；提交前复跑结果 `87 passed, 21 warnings in 20.07s`。
- 提交前轻量检查：文档占位词扫描无输出；U+FFFD 扫描无输出；本阶段文件 `git diff --check` 无输出。
- 全量回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider`，结果 `1313 passed, 6 skipped, 139 warnings in 101.70s`。

下一步：

- 进入 P4-1「评测体系扩展」设计 / 计划。

## 当前详细计划：P3-3 TimingGate 持续评估

状态：已完成三路只读审计、设计文档和实现计划。设计文档为 `docs/superpowers/specs/2026-06-18-timing-gate-continuous-eval-design.md`；实现计划为 `.Codex/plans/timing-gate-continuous-eval.md`。P3-3 不做通用 candidates 产品化闭环，该闭环留到 P4-1。

审计结论：

- `timing_signal_audit` 现有 CLI 能从 DB 抽样并聚合样本内已有 `label` 字段，但缺少读取 labeled report 或 sidecar labels 后复跑的入口。
- `evals.run` 已支持 `--baseline`、`--min-pass-rate`、`--max-new-failures`，但仓库缺稳定 baseline、统一脚本和 CI workflow。
- 当前正式 `evals/cases/timing_gate/*.json` 全部带 `input.action`，主要验证 action 回放；需要新增无 action case 覆盖 `decide_timing()` scoring 路径。
- 当前没有 `.github`、`.gitlab-ci.yml`、`pytest.ini`、根级 `pyproject.toml` 或 `Makefile`；首个 CI gate 应保持轻量，并显式设置 `NANOBOT_ADMIN_TOKEN`，避免 `config.py` 在 CI 中写 `.env`。

P3-3A 目标：

- [x] 收敛只读审计结论，明确真实标注复跑缺口。
- [x] 写入设计文档：`docs/superpowers/specs/2026-06-18-timing-gate-continuous-eval-design.md`。
- [x] 写入实现计划：`.Codex/plans/timing-gate-continuous-eval.md`。
- [x] 在 `core/eval_sampling/timing_signal_audit.py` 增加纯函数 label merge helper。
- [x] 在 `evals/timing_signal_audit.py` 增加 `--input-report` / `--labels` 离线复跑模式。
- [x] 在 `tests/test_timing_signal_audit.py` 覆盖 sidecar 合并、labeled report 复跑和建议状态。
- [x] 同步 `docs/todo.md`、本文件和设计文档状态。
- [x] 运行最终检查并提交 `feat(评测): 支持时机信号标注复跑`。

P3-3B 目标：

- [x] 新增 `evals/baselines/timing_gate.json`，避免依赖被忽略或覆盖的 `evals/reports/latest.json`。
- [x] 新增至少 2 个无 `input.action` 的正式 `timing_gate` scoring case。
- [x] 补充 `tests/test_eval_baseline.py` gate 成功路径和异常配置守卫。
- [x] 补充 `tests/test_timing_gate_prompt_policy.py`，要求正式 suite 含 scoring case。
- [x] 新增 `scripts/run_timing_gate_gate.sh`，统一本地和 CI 命令。
- [x] 新增 `.github/workflows/timing-gate-eval.yml`，运行轻量 pytest 和 timing gate eval gate。
- [x] 保持 `evals/run.py` 现有报告写入行为，本阶段 workflow 不启用 `git diff --exit-code`，`evals/reports/*.json` 继续被 `.gitignore` 忽略。
- [x] 新增 `docs/evals.md`，记录 baseline 更新规则和失败处理。
- [x] 运行最终检查并提交 `ci(评测): 接入 timing gate 回归门禁`。

验证计划：

- P3-3 文档阶段：`git diff --check`、文档占位词扫描、U+FFFD 扫描。
- P3-3A：`python -m pytest tests/test_timing_signal_audit.py tests/test_timing_gate_prompt_policy.py -v`。
- P3-3B：`python -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py -v`、`bash scripts/run_timing_gate_gate.sh`、全量 `python -B -m pytest tests/ -v -p no:cacheprovider`。

P3-3A 验证记录：

- 红灯：`python -m pytest tests/test_timing_signal_audit.py::test_merge_timing_signal_labels_overrides_by_log_id_and_signal tests/test_timing_signal_audit.py::test_run_labeled_audit_merges_jsonl_labels_and_writes_report -v`，结果 `2 failed, 1 warning in 5.90s`；失败点为缺少 `merge_timing_signal_labels` 和 `run_labeled_audit`。
- 绿灯：同一新增测试命令结果 `2 passed, 1 warning in 0.71s`。
- P3-3A 文件回归：`python -m pytest tests/test_timing_signal_audit.py -v`，结果 `5 passed, 1 warning in 0.86s`。
- CLI 入口调整后相邻回归：`python -m pytest tests/test_timing_signal_audit.py tests/test_timing_gate_prompt_policy.py -v`，结果 `12 passed, 1 warning in 1.45s`。
- 全量回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider`，结果 `1316 passed, 6 skipped, 139 warnings in 99.05s`。

P3-3B 验证记录：

- 红灯：`python -m pytest tests/test_eval_baseline.py::test_evaluate_gate_requires_baseline_for_new_failure_limit tests/test_eval_baseline.py::test_evaluate_gate_fails_for_baseline_suite_mismatch tests/test_eval_baseline.py::test_eval_run_cli_returns_success_when_gate_passes tests/test_eval_baseline.py::test_timing_gate_gate_script_uses_stable_baseline tests/test_eval_baseline.py::test_timing_gate_workflow_runs_gate_script tests/test_timing_gate_prompt_policy.py::test_timing_gate_eval_suite_contains_rule_scoring_cases -v`，结果 `3 failed, 3 passed, 1 warning in 6.31s`；失败点为缺少门禁脚本、workflow 和正式 scoring case。
- 新增红灯集合绿灯：同一命令结果 `6 passed, 1 warning in 1.00s`。
- TimingGate suite 守卫回归：`python -m pytest tests/test_timing_gate_prompt_policy.py -v`，结果 `8 passed, 1 warning in 1.02s`。
- Eval baseline 回归：`python -m pytest tests/test_eval_baseline.py -v`，结果 `10 passed, 1 warning in 1.15s`。
- 门禁脚本：`bash scripts/run_timing_gate_gate.sh`，结果 `Suite: timing_gate total=18 passed=18 failed=0 pass_rate=100.0%`，`Gate passed`。
- 定向组合：`python -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py -v && bash scripts/run_timing_gate_gate.sh`，结果 `18 passed, 1 warning in 1.67s`，随后 `Gate passed`。
- 全量回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider`，结果 `1322 passed, 6 skipped, 139 warnings in 100.88s`。

提交边界：

- 文档 / 计划阶段单独提交：`docs(评测): 设计 TimingGate 持续评估`。
- P3-3A 单独提交：`feat(评测): 支持时机信号标注复跑`。
- P3-3B 单独提交：`ci(评测): 接入 timing gate 回归门禁`。

## 当前详细计划：P4-1 评测数据集与标注闭环

状态：已完成。设计文档已随 `e4fb70a docs(评测): 设计标注闭环` 提交；实现计划已随 `8b892a8 docs(计划): 记录标注闭环计划` 提交。P4-1 第一阶段采用契约优先方案，已完成 expected 契约、候选标注、晋升安全、离线 CLI、首个能力数据集、文档收口和最终验证。

目标：

- 修复 WebUI / Admin 标注字段错配，避免 expected 被静默写成空对象。
- 禁止空 expected、`needs_label=true` 和不可评分 expected 进入 labeled / promoted 状态。
- 补齐历史未评分 expected key 的测试与 scorer 覆盖。
- 增加 promote dry-run、`target_dataset` 和来源 `meta`。
- 增加离线 candidates export / import-labels / promote 命令。
- 新增 `capability_model_routing` 数据集和 baseline，固定 dataset / suite 两层语义。

阶段拆分：

- [x] 设计文档：`docs/superpowers/specs/2026-06-18-eval-dataset-labeling-design.md`。提交：`e4fb70a docs(评测): 设计标注闭环`。
- [x] 实现计划：`.Codex/plans/eval-dataset-labeling.md`。提交：`8b892a8 docs(计划): 记录标注闭环计划`。
- [x] 任务 1：Expected 契约与历史未评分 key。提交：`4f4cce7 fix(评测): 校验可评分期望字段`。
- [x] 任务 2：候选标注契约修复。提交：`b84cbf1 fix(评测): 修复候选标注契约`。
- [x] 任务 3：Promote dry-run 与 dataset 目标。提交：`7a84084 feat(评测): 支持候选晋升预检`。
- [x] 任务 4：离线 candidates CLI。提交：`71c3a53 feat(评测): 增加候选标注命令`。
- [x] 任务 5：首个 per-capability 数据集。提交：`a494f3b test(评测): 增加模型路由能力数据集`。
- [x] 任务 6：文档收口。提交：`docs(评测): 收口标注闭环状态`。
- [x] 任务 7：最终验证与交接。提交：`docs(计划): 完成标注闭环验证`。

验证计划：

- 计划阶段：`git diff --check`、占位词扫描和 U+FFFD 扫描。
- 定向回归：`tests/test_eval_candidate_contract.py`、`tests/test_eval_candidates_cli.py`、`tests/test_eval_baseline.py`、`tests/test_timing_gate_prompt_policy.py`。
- 门禁：`bash scripts/run_timing_gate_gate.sh` 和 `python -B -m evals.run --suite capability_model_routing --baseline evals/baselines/capability_model_routing.json --min-pass-rate 1.0 --max-new-failures 0`。
- 最终回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider`。

已完成验证摘要：

- 任务 1：新增 expected 契约红灯覆盖后通过 `tests/test_eval_candidate_contract.py`，`regression` eval 结果为 `total=11 passed=11 failed=0`，全量回归为 `1323 passed, 6 skipped`。
- 任务 2：候选标注契约和 WebUI 静态守卫均通过，定向回归为 `6 passed, 4 deselected`，全量回归为 `1327 passed, 6 skipped`。
- 任务 3：promote dry-run / `target_dataset` / Admin dry-run 覆盖通过，定向回归为 `10 passed`，全量回归为 `1329 passed, 6 skipped`。
- 任务 4：候选 CLI 覆盖 export、import-labels、promote dry-run / apply 和 CLI main，定向回归为 `7 passed`，全量回归为 `1337 passed, 6 skipped`。
- 任务 5：`capability_model_routing` suite 和 baseline gate 均通过，`tests/test_eval_baseline.py` 为 `11 passed`，全量回归为 `1338 passed, 6 skipped`。
- 任务 6：文档占位词扫描、U+FFFD 扫描和 `git diff --check` 均无错误输出；评测定向组合为 `33 passed, 21 warnings`，TimingGate 与 `capability_model_routing` gate 均输出 `Gate passed`，全量回归为 `1338 passed, 6 skipped, 139 warnings`。
- 任务 7：候选闭环定向回归为 `14 passed, 21 warnings`；eval 相关回归为 `19 passed, 1 warning`；TimingGate 与 `capability_model_routing` gate 均输出 `Gate passed`；WebUI 静态回归为 `17 passed, 1 warning`；全量回归为 `1338 passed, 6 skipped, 139 warnings`。

## 已完成阶段详情：P3-1 SSE 真 token 流式剩余收敛

状态：P3-1 已完成设计、计划、核心实现、文档收口和最终验证。设计文档为 `docs/superpowers/specs/2026-06-18-streaming-sse-convergence-design.md`，已随 `bca50b8 docs(流式): 设计 SSE 收敛方案` 提交；实现计划为 `.Codex/plans/streaming-sse-convergence.md`，已随 `e56a406 docs(计划): 记录 SSE 收敛计划` 提交。

目标：

- `/chat` SSE 在 API 层规范化内部队列事件，连续 `delta.text` 在当前可用队列窗口内合并。
- `done.answer` / `done.reply` 继续作为最终业务权威结果，草稿 `delta` 只用于提前展示。
- Bridge 在最终 response 确定后发送 `final.replace` 收敛事件，帮助前端替换草稿展示区。
- `/chat` stream queue 设置上限；文本 delta / final 走自然 backpressure，progress 满队列可丢弃，error 仍保留排队。
- 客户端断连后后台 runner 继续完成并 push 最终结果，同时 drain bounded queue，避免无人消费 SSE 事件导致 runner 卡住。
- `docs/message-field-standard.md` 记录 `/chat` SSE 事件、`/chat-step delta.content` 差异、图片 token 展开边界和 `done` 权威语义。

已完成任务：

- [x] 设计文档：明确 API adapter、Bridge final/replace、bounded queue、事件契约和子 agent 分工。提交：`bca50b8 docs(流式): 设计 SSE 收敛方案`。
- [x] 实现计划：写入 `.Codex/plans/streaming-sse-convergence.md`，按 API、Bridge、queue、文档和最终验证拆分任务。提交：`e56a406 docs(计划): 记录 SSE 收敛计划`。
- [x] 任务 1：`/chat` API delta adapter 与连续合并。提交：`d8e8703 refactor(流式): 合并聊天增量事件`。
- [x] 任务 2：固化 `done` 权威和 `/chat-step delta.content` 字段差异。提交：`84cb0cb test(流式): 固化完成信封权威性`。
- [x] 任务 3：Bridge / `BufferedOutput` 输出 `final.replace` 收敛事件。提交：`a987d31 feat(流式): 发送最终收敛事件`。
- [x] 任务 4：API 层规范化 final 事件，补齐 `replace` 和 `source` 默认值。提交：`88268a1 refactor(流式): 规范化最终收敛事件`。
- [x] 任务 5：限制 `/chat` stream queue 增长，progress 满队列丢弃，断连后台 drain bounded queue。提交：`a5f705a perf(流式): 限制聊天流队列增长`。
- [x] 任务 6：同步消息字段标准、路线项 6、本文件和实现计划状态。提交：`docs(流式): 收口 SSE 收敛状态`。
- [x] 任务 7：运行最终格式检查、流式相关回归、API / Bridge 相关回归和全量测试。

验证记录：

- 任务 1 红灯：`python -m pytest tests/test_streaming_api.py -v`，结果 `3 failed, 2 passed, 21 warnings in 7.72s`；失败点为连续 delta 仍逐个下发。
- 任务 1 绿灯：`python -m pytest tests/test_streaming_api.py -v`，结果 `5 passed, 21 warnings in 2.29s`。
- 任务 1 API 回归：`python -m pytest tests/test_streaming_api.py tests/test_streaming_response_envelope.py tests/test_chat_response_envelope.py -v`，结果 `10 passed, 21 warnings in 4.76s`。
- 任务 2 保护测试：`python -m pytest tests/test_streaming_response_envelope.py tests/test_agent_step_api.py::test_chat_step_stream_emits_final_answer_deltas -v`，结果 `3 passed, 21 warnings in 1.89s`。
- 任务 3 红灯：`python -m pytest tests/test_streaming_output.py tests/test_streaming_bridge.py::test_bridge_handle_message_streams_controller_text_deltas -v`，结果 `2 failed, 1 passed, 1 warning in 6.11s`；失败点为 `write_final` 缺失和 Bridge 队列无 final 事件。
- 任务 3 绿灯：`python -m pytest tests/test_streaming_output.py tests/test_streaming_bridge.py -v`，结果 `6 passed, 1 warning in 1.21s`。
- 任务 4 红灯：`python -m pytest tests/test_streaming_api.py::test_stream_chat_normalizes_final_replace_before_done -v`，结果 `1 failed, 21 warnings in 6.43s`；失败点为 final 事件缺少 `replace/source`。
- 任务 4 绿灯：`python -m pytest tests/test_streaming_api.py -v`，结果 `6 passed, 21 warnings in 2.68s`。
- 任务 4 API 回归：`python -m pytest tests/test_streaming_api.py tests/test_streaming_response_envelope.py -v`，结果 `8 passed, 21 warnings in 3.79s`。
- 任务 5 红灯：`python -m pytest tests/test_streaming_api.py::test_stream_chat_uses_bounded_stream_queue tests/test_streaming_output.py::test_buffered_output_drops_progress_when_stream_queue_is_full tests/test_streaming_output.py::test_buffered_output_keeps_error_when_stream_queue_is_full tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner -v`，结果 `2 failed, 2 passed, 21 warnings in 6.70s`；失败点为 `/chat` 队列无上限和 progress 满队列仍延迟入队。
- 任务 5 绿灯：同一命令结果 `4 passed, 21 warnings in 1.37s`。
- 任务 5 流式 / 断连回归：`python -m pytest tests/test_streaming_api.py tests/test_streaming_output.py tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send -v`，结果 `15 passed, 21 warnings in 3.38s`。
- 任务 6 文档扫描：`rg -n "T[O]DO|待[定]|后续[实]现|类似[任]务|添加[适]当|为上[述]" docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/streaming-sse-convergence.md`，结果无输出，退出码 1。
- 任务 6 乱码检查：4 个文档文件均不包含 U+FFFD replacement character，退出码 0。
- 任务 6 文档格式检查：`git diff --check -- docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/streaming-sse-convergence.md`，结果无输出，退出码 0。
- 任务 6 P3-1 定向回归：`python -m pytest tests/test_streaming_api.py tests/test_streaming_response_envelope.py tests/test_agent_step_api.py tests/test_streaming_output.py tests/test_streaming_bridge.py -v`，结果 `23 passed, 21 warnings in 6.50s`。
- 任务 7 格式检查：`git diff --check`，结果无输出，退出码 0。
- 任务 7 流式相关回归：`python -m pytest tests/test_streaming_api.py tests/test_streaming_response_envelope.py tests/test_agent_step_api.py tests/test_streaming_output.py tests/test_streaming_bridge.py -v`，结果 `23 passed, 21 warnings in 6.73s`。
- 任务 7 API / Bridge 相关回归：`python -m pytest tests/test_api.py tests/test_chat_response_envelope.py tests/test_api_push_envelope.py tests/test_kt_framework.py tests/test_streaming_bridge.py -v`，结果 `145 passed, 21 warnings in 40.76s`。
- 任务 7 全量回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider`，结果 `1311 passed, 6 skipped, 139 warnings in 99.87s`。

下一步：

- P3-2 已补齐私聊 `timing_scoring` 持久化到 ChatLog meta 的可观测缺口。
- 之后推进 TimingGate 外部 CI / PR gate、真实标注样本复跑调参，以及 P4 评测体系扩展。

## 已完成阶段详情：P2-3 QQ 出站渲染契约

状态：P2-3 已完成实现、文档收口和最终验证。设计文档为 `docs/superpowers/specs/2026-06-18-qq-outbound-rendering-contract-design.md`，已随 `c72ddb3 docs(渲染): 设计 QQ 出站契约` 提交；实现计划为 `.Codex/plans/qq-outbound-rendering-contract.md`，已随 `1f4aa69 docs(计划): 同步 QQ 出站渲染计划` 提交。本阶段采用保守兼容方案：响应信封里的 `messages` 是 canonical 出站内容层，`reply` 是 fallback；`core/qq_outbound_renderer.py` 负责把 `envelope/messages/reply_meta` 渲染成 QQbot 旧 `message` 字符串，`push_to_qq(target_type, target_id, message) -> bool` 旧签名保持不变。

目标：

- 新增集中式 QQ 出站 renderer，并输出内部 `QQOutboundRenderResult`，包含 `message`、`messages`、`reply_meta` 和 `warnings`。
- `push_envelope_to_qq()` 改为调用 renderer，不再依赖会忽略图片的 `envelope_to_message()`。
- `[generated_image:<id>]` 在有公开 URL 时渲染为 `[CQ:image,file=<url>]`；无公开 URL 时保留短 token，且不出现 `base64://`。
- `[sticker:<id>]` 继续作为兼容输入，renderer 层可识别并展开；`ReplyTool` 首轮仍可保留现有提前展开行为。
- `creatures/nanobot/prompts/skills/schedule_task/tool.py` 的 `action == "run"` 分支改走响应信封和 `push_envelope_to_qq()`，不再直连旧 push。
- prompt 工具说明同步短 token 与出口 renderer 职责，两个物理根目录保持同一口径。

阶段拆分：

- [x] 完成只读审计：QQ push / transport 出口、reply 工具、sticker、generated image、响应信封和群聊 / 私聊出口均已由子 agent 分域审计。
- [x] 写入设计文档：`docs/superpowers/specs/2026-06-18-qq-outbound-rendering-contract-design.md`。提交：`c72ddb3 docs(渲染): 设计 QQ 出站契约`。
- [x] 写入实现计划：`.Codex/plans/qq-outbound-rendering-contract.md`，按 renderer、push、schedule、route、富媒体边界、prompt 文档和最终收口拆分任务，并写明子 agent 文件所有权。
- [x] 任务 1：新增 `core/qq_outbound_renderer.py` 和 `tests/test_qq_outbound_renderer.py`。提交：`72a9751 feat(渲染): 添加 QQ 出站渲染器`。
- [x] 任务 2：让 `core/daily_digest.push_envelope_to_qq()` 使用 renderer。提交：`f19b09b feat(推送): 使用 QQ 出站渲染器`。
- [x] 任务 3：修复 schedule task `action == "run"` 绕过旧 push 的问题。提交：`f0bfbdf fix(定时任务): 统一运行推送出口`。
- [x] 任务 4：固化 route push call site 走响应信封和 renderer 的回归测试。提交：`04ff6d3 docs(计划): 同步渲染出口任务状态` 记录状态，既有 route 回归通过。
- [x] 任务 5：保护富媒体响应信封边界，确保 image / HTML 不被旧文本 helper 静默丢弃或破坏。提交：`0c8c590 test(渲染): 保护富媒体信封边界`。
- [x] 任务 6：同步 `reply`、`sticker_search`、`image_generation` 工具 usage 文档。提交：`6aea7f8 docs(提示词): 说明出站渲染职责`。
- [x] 任务 7：同步 `docs/message-field-standard.md`、`docs/todo.md`、本文件和实现计划状态，运行定向与全量验证后提交。

最新验证记录：

- 设计文档占位词扫描：`docs/superpowers/specs/2026-06-18-qq-outbound-rendering-contract-design.md`，结果无输出，退出码 0。
- 设计文档格式检查：`git diff --check -- docs/superpowers/specs/2026-06-18-qq-outbound-rendering-contract-design.md`，结果无输出，退出码 0。
- renderer 红灯：`tests/test_qq_outbound_renderer.py` 先失败于 `ModuleNotFoundError: No module named 'core.qq_outbound_renderer'`。
- renderer 绿灯：`tests/test_qq_outbound_renderer.py`，结果 `9 passed, 1 warning`；补充审查建议后为 `11 passed, 1 warning`。
- renderer 相邻回归：`tests/test_qq_outbound_renderer.py tests/test_message_envelope.py tests/test_sticker_tool.py tests/test_image_generation_tool.py`，结果 `40 passed, 1 warning`。
- 富媒体边界回归：`tests/test_message_envelope.py tests/test_group_response_envelope.py tests/test_qq_outbound_renderer.py`，结果 `23 passed, 1 warning`。
- push 回归：`tests/test_push_envelope.py tests/test_daily_digest.py`，结果 `17 passed, 1 warning`。
- schedule 回归：`tests/test_schedule_task_tool.py tests/test_push_envelope.py tests/test_daily_digest.py`，结果 `19 passed, 1 warning`。
- route / API 回归：`tests/test_api_push_envelope.py tests/test_chat_response_envelope.py tests/test_streaming_response_envelope.py tests/test_group_response_envelope.py tests/test_api.py tests/test_streaming_api.py`，结果 `94 passed, 21 warnings in 22.28s`。
- prompt 文档扫描：6 个 usage 文件均包含 `出口 renderer`、`reply_token`、`[generated_image:`；三个 prompt 根目录 diff 无输出；`git diff --check HEAD^ HEAD -- <6 个 usage 文件>` 无输出。
- 任务 7 文档扫描：`docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/qq-outbound-rendering-contract.md`，结果无输出，退出码 0。
- 任务 7 文档格式检查：`git diff --check -- docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/qq-outbound-rendering-contract.md`，结果无输出，退出码 0。
- 任务 7 P2-3 定向回归：`tests/test_qq_outbound_renderer.py tests/test_push_envelope.py tests/test_schedule_task_tool.py tests/test_api_push_envelope.py tests/test_message_envelope.py tests/test_group_response_envelope.py tests/test_chat_response_envelope.py tests/test_streaming_response_envelope.py`，结果 `38 passed, 21 warnings in 5.02s`。
- 任务 7 全量回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider`，结果 `1288 passed, 6 skipped, 139 warnings in 87.69s`。

后续事项：

- 历史执行记录：P2-4「Prompt platform × chat_type 二维适配」完成后曾转入 P3-1「SSE 真 token 流式剩余收敛」；P3-1 当前已完成，当前下一步以文末「下一步」章节为准。

## 已完成阶段详情：P2-4 Prompt platform × chat_type 二维适配

状态：P2-4 已完成实现、集成回归和本轮文档收口。设计文档为 `docs/superpowers/specs/2026-06-18-prompt-platform-chat-type-design.md`，已随 `27e632f docs(提示词): 设计平台化提示词分支` 提交；实现计划为 `.Codex/plans/prompt-platform-chat-type.md`，已随 `164b215 docs(计划): 记录平台化提示词计划` 提交。本阶段保留 `chat_type ∈ {group, private}` 表达会话语义，新增 `platform` 表达客户端平台；Prompt Runtime 按 `platform × chat_type` 过滤 flow，QQ 私有规则下沉到 `chat/platform/qq/*` 平台分支。

目标：

- `platform` 从 Bridge metadata 进入 `PromptRuntimeInput`、`PromptCompileRequest`、`PromptPlan`、`debug` 和 `<runtime_context>`。
- flow 节点和边支持 `platforms` 条件，并拒绝 `chat_types × platforms` 条件重叠的歧义出边。
- QQ 平台模板拆分为 `chat/platform/qq/common.md` 和 `chat/platform/qq/group.md`，公共群聊 / 私聊模板不再写死 QQ 私有措辞。
- `qq × group` 注入通用群聊模板、QQ common 模板和 QQ 群聊模板；`qq × private` 注入通用私聊模板和 QQ common 模板；`web × private` 不注入 QQ 平台模板。
- Admin effective-preview 支持 `platform`，并让 ToolPlan 与 PromptCompileRequest 使用同一个平台值。

已完成任务：

- [x] 设计文档：明确 platform 与 chat_type 的职责边界、flow 条件规则和模板迁移策略。提交：`27e632f docs(提示词): 设计平台化提示词分支`。
- [x] 实现计划：写入 `.Codex/plans/prompt-platform-chat-type.md`，拆分任务 1 到任务 5 的文件 owner、验证命令和提交边界。提交：`164b215 docs(计划): 记录平台化提示词计划`。
- [x] 任务 1：Prompt Runtime core 支持 platform 维度，覆盖 schema、variables、context、flow、compiler 和二维冲突测试。提交：`ca93dc2 feat(提示词): 支持平台化编排条件`。
- [x] 任务 2：Bridge 和 Admin 预览透传 platform，覆盖 PromptRuntimeInput、PromptCompileRequest、ToolPlan 和 effective-preview 响应。提交：`18d0b0d feat(提示词): 透传提示词平台上下文`。
- [x] 任务 3：迁移 flow 和提示词模板，新增 QQ 平台模板，同步 `prompts.v2.default` 与 `data/prompts_v2`，并清理工具 usage 中的 QQ / OneBot / CQ 私有表述。提交：`17a7bd8 feat(提示词): 拆分 QQ 平台模板`。
- [x] 任务 4：补平台化提示词集成回归，覆盖默认 QQ、Web 私聊和 Admin 真实预览链路。提交：`fe2d81b test(提示词): 覆盖平台化提示词链路`。
- [x] 任务 5：同步 `docs/todo.md`、本文件和实现计划状态，运行文档扫描、定向回归、Prompt Runtime 完整回归与全量测试后单独提交。

验证记录：

- 任务 2 定向：`tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_prompt_v2_template_admin.py -k "platform"`，结果 `4 passed, 73 deselected, 1 warning`。
- 任务 2 相关完整回归：`tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_prompt_v2_template_admin.py`，结果 `77 passed, 1 warning`。
- 任务 2 全量回归：`tests/`，结果 `1297 passed, 6 skipped, 139 warnings`。
- 任务 3 模板一致性：`diff -qr prompts.v2.default data/prompts_v2`，结果无输出。
- 任务 3 平台模板定向：`tests/test_prompt_v2.py tests/test_prompt_v2_template_registry.py -k "default_flow_selects_qq_platform_templates or default_flow_skips_qq_templates or platform_templates_are_addressable or platform_words_are_isolated or tool_usage_avoids_platform_private_message_codes"`，结果 `5 passed, 33 deselected, 1 warning`。
- 任务 3 Prompt Runtime / registry 回归：`tests/test_prompt_v2.py tests/test_prompt_v2_template_registry.py`，结果 `38 passed, 1 warning`。
- 任务 3 全量回归：`tests/`，结果 `1302 passed, 6 skipped, 139 warnings`。
- 任务 4 最小集成断言：`tests/test_prompt_v2.py tests/test_prompt_v2_template_admin.py -k "default_flow_selects_qq_platform_templates or default_flow_skips_qq_templates or templates_can_be_edited_from_admin"`，结果 `3 passed, 30 deselected, 1 warning`。
- 任务 4 平台定向回归：`tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_admin_api.py tests/test_prompt_v2_template_admin.py -k "platform or prompt_v2"`，结果 `50 passed, 127 deselected, 1 warning`。
- 任务 4 Prompt Runtime 完整回归：`tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_admin_api.py tests/test_prompt_v2_template_registry.py tests/test_prompt_v2_template_admin.py`，结果 `190 passed, 1 warning`。
- 任务 5 文档扫描：`.Codex/plans/prompt-platform-chat-type.md docs/todo.md docs/plan_walkthrough.md`，结果 `scan ok`；`git diff --check` 无输出；`diff -qr prompts.v2.default data/prompts_v2` 无输出。
- 任务 5 Prompt Runtime 完整回归：`tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_admin_api.py tests/test_prompt_v2_template_registry.py tests/test_prompt_v2_template_admin.py`，结果 `190 passed, 1 warning`。
- 任务 5 全量回归：`tests/`，结果 `1302 passed, 6 skipped, 139 warnings in 99.24s`。

剩余边界：

- 工具模板 selector 暂不按平台拆分；现阶段只清理工具 usage 的平台私有措辞。
- TimingGate task 模板的平台化仍由 TimingGate 路线独立推进。

## 已完成阶段详情：P2-2.5 `client_meta` 边界层校验

状态：P2-2.5 已完成实现并通过最终验证。设计文档为 `docs/superpowers/specs/2026-06-18-client-meta-boundary-validation-design.md`，实现计划为 `.Codex/plans/client-meta-boundary-validation.md`。本阶段新增 `core/client_meta.py` 纯 helper，route 层把 helper 校验错误转换为 HTTP 400；`/chat` 和 `/group/message` 均会在业务处理前归一化 `req.client_meta`，避免冲突 `chat_type` 或非法 trace 字段继续进入 Bridge、TimingGate 和 ambient log。

目标：

- `platform` 缺省为 `qq`，传入时执行 `strip().lower()` 并要求匹配 `^[a-z][a-z0-9_-]{0,31}$`。
- `/chat` 与 `/group/message` 校验 `client_meta.chat_type` 与入口事实一致，冲突时返回 400。
- `trace.request_id`、`trace.correlation_id`、`trace.source` 必须是字符串，服务端会裁剪到 128 字符。
- `/chat` 将归一化后的 `trace.request_id` 投影到响应信封 `meta.request_id`。
- 群聊 ambient log 保存归一化后的 `client_meta`，并继续保留 `stickers`、`raw`、`business` 等扩展字段。

阶段拆分：

- [x] 写入设计文档：`docs/superpowers/specs/2026-06-18-client-meta-boundary-validation-design.md`。提交：`ce05b35 docs(计划): 设计客户端元信息校验`。
- [x] 写入实现计划：`.Codex/plans/client-meta-boundary-validation.md`。
- [x] 任务 1：新增 helper 红灯测试 `tests/test_client_meta.py`。
- [x] 任务 2：实现 `core/client_meta.py`，覆盖 platform 默认值、平台归一化、`chat_type` 冲突、trace 字符串校验和扩展字段保留。
- [x] 任务 3：补 `/chat` 与 `/group/message` API 红灯测试，覆盖响应 `meta.request_id`、冲突 `chat_type` 400、ambient log 归一化。
- [x] 任务 4：在 `api/routes.py` 接入 route 层 `client_meta` 归一化和错误转换。
- [x] 任务 5：同步 `docs/message-field-standard.md`、`docs/todo.md`、本文件和实现计划状态，并在最终验证后提交。

最新验证记录：

- helper 红灯：`tests/test_client_meta.py` 先失败于 `ModuleNotFoundError: No module named 'core.client_meta'`，结果 `5 failed, 1 warning`。
- helper 绿灯：`tests/test_client_meta.py`，结果 `5 passed, 1 warning in 0.60s`。
- API 红灯：`tests/test_chat_response_envelope.py tests/test_group_response_envelope.py`，结果 `4 failed, 5 passed, 21 warnings`；失败点为响应 `meta.request_id` 缺失、冲突 `chat_type` 未返回 400、群聊 ambient log 中 `client_meta` 未归一。
- API 绿灯：`tests/test_client_meta.py tests/test_chat_response_envelope.py tests/test_group_response_envelope.py`，结果 `14 passed, 21 warnings in 2.70s`。
- 文档占位词扫描：`docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/client-meta-boundary-validation.md docs/superpowers/specs/2026-06-18-client-meta-boundary-validation-design.md`，结果无输出，退出码 0。
- 文档格式检查：`git diff --check -- docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/client-meta-boundary-validation.md docs/superpowers/specs/2026-06-18-client-meta-boundary-validation-design.md`，结果无输出，退出码 0。
- P2-2.5 定向回归：`tests/test_client_meta.py tests/test_chat_response_envelope.py tests/test_group_response_envelope.py tests/test_api.py::test_proxy_chat_passes_client_platform_to_bridge tests/test_api.py::test_group_message_passes_client_platform_to_timing_gate tests/test_api.py::test_group_message_passes_client_platform_to_bridge`，结果 `17 passed, 21 warnings in 3.29s`。
- 全量回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider`，结果 `1272 passed, 6 skipped, 139 warnings in 86.68s`。

后续事项：

- P2-3 继续处理 QQ 出站渲染契约，采用响应信封 `messages` + 集中 renderer 的方案，收敛图片、表情、@、引用和 HTML 出口层职责。
- TimingGate 真实日志标注 / CI 接入属于运营延续项，不抢占 P2 执行顺序。

## 当前详细计划：P2-2 标准化请求 / 响应信封

状态：P2-2 响应信封兼容双写已完成并通过最终验证。只读审计已确认 `/chat`、`/chat` SSE done、`/group/message` 和 push / 定时任务出口的旧响应差异；`docs/superpowers/specs/2026-06-18-message-envelope-design.md` 已写入兼容双写方案并随 `c984036` 提交。`.Codex/plans/message-envelope.md` 已改为接口先行、文件 owner 清晰、可在隔离 worktree 中并行执行的实现计划。任务 1 已新增 `core/message_envelope.py` 共享 builder 并随 `147421b` 提交；任务 2 已让 `/chat` 非流式和 SSE done 返回 `reply`、`messages`、过滤后的 `reply_meta` 与 `meta`，同时保留旧字段；任务 3 已让 `/group/message` 的 continue / wait / no_reply 返回标准信封，并保留 `action` 调度语义；任务 4 已新增 `push_envelope_to_qq()`，定时任务推送会先构造标准信封，再通过适配层派生旧 QQbot `message`；任务 5 已让 `api/routes.py` 中手动定时任务运行和流式断连后台 push 也改用 `push_envelope_to_qq()`，并保留图片 token `allow_base64=False` 展开边界；任务 6 已把响应信封标准、兼容双写字段、P2-2 / P2-3 边界和最终验证记录写入文档。

目标：

- 对外新增统一响应信封字段：`reply`、`messages`、`reply_meta`、`meta`，群聊同时补 `status`。
- 保留旧字段不破坏调用方：`/chat.answer`、`answer_chunks`、SSE done 的 `answer`、`/group/message.action/reply/reply_meta/generation/reason`、`push_to_qq(target_type, target_id, message) -> bool` 均继续可用。
- 私聊成功路径返回过滤后的 `reply_meta`，不暴露 `_agent_result`、`_no_reply`、`_no_reply_reason` 等内部字段。
- 首版 `messages` 只承载保守的 `text` / `html` 结构，图片、at、reply segments、CQ renderer 和 HTML-to-pic 仍归入 P2-3「QQ 出站渲染契约」。
- `docs/message-field-standard.md` 在实现阶段补响应信封章节，避免只规范入站字段。

只读审计结论：

- `/chat` 非流式成功响应已接入兼容信封：返回 `reply`、`messages`、过滤后的 `reply_meta` 和 `meta`，并保留 `status`、`user_id`、`answer`、`answer_chunks` 和 `unprocessed_logs`。
- `/chat` SSE done 已接入兼容信封：继续发送 `status="done"` 和 `answer`，同时新增 `reply`、`messages`、过滤后的 `reply_meta` 和 `meta`；`progress`、`delta`、`heartbeat`、`error` 事件结构未变。
- `/group/message` continue / wait / no_reply 响应已接入兼容信封：新增 `status`、`messages`、过滤后的 `reply_meta` 和 `meta`，并保留 `action`、`reply`、`generation`、`reason`、`delay_seconds`、`diagnostics` 等旧字段。
- `push_to_qq` 旧签名被调用方和测试依赖，P2-2 应新增 `push_envelope_to_qq(envelope)` 适配层，而不是破坏旧 helper。

阶段拆分：

- [x] 核对 `docs/todo.md` 路线项 5，确认 P2-2 是 P2 多平台底座的下一优先级；历史待办清单文件明显滞后，仅作历史材料。
- [x] 完成只读审计：私聊 / Web 路径、群聊路径、push / 定时任务出口的响应字段差异均已梳理。
- [x] 写入设计文档：`docs/superpowers/specs/2026-06-18-message-envelope-design.md`，明确兼容双写方案、字段映射、P2-2 / P2-3 边界、测试计划和验收标准。提交：`c984036 docs(消息): 设计响应信封标准`。
- [x] 写入实现计划：`.Codex/plans/message-envelope.md`，按接口先行、API / 群聊 / push 文件 owner、主线程集成和阶段提交拆解。
- [x] 任务 1：新增 `core/message_envelope.py`，覆盖 `messages` 构造、`reply_meta` 过滤、`meta` 组装和信封 builder 的单元测试。提交：`147421b feat(消息): 构建响应信封`。
- [x] 任务 2：API owner 在 `api/routes.py` 接入 `/chat` 非流式和 SSE done 响应信封，保留旧字段并返回过滤后的私聊 `reply_meta`。
- [x] 任务 3：群聊 owner 在 `app/group_ingress/service.py` 接入 continue / wait / no_reply 响应信封，保留 `action` 调度语义。
- [x] 任务 4：push owner 在 `core/daily_digest.py` 新增信封适配 helper，旧 `push_to_qq(target_type, target_id, message)` 签名不变。
- [x] 任务 5：主线程集成 `api/routes.py` 中手动任务运行和流式断连 push call site，避免与 API / push worker 冲突。
- [x] 任务 6：同步 `docs/message-field-standard.md`、`docs/todo.md`、本文件和实现计划状态，运行定向与全量验证后单独提交。

最新验证记录：

- 任务 4 红灯：`tests/test_push_envelope.py` 先失败于 `AttributeError: module 'core.daily_digest' has no attribute 'push_envelope_to_qq'`，结果 `3 failed, 1 passed, 1 warning`。
- 任务 4 绿灯和回归：`tests/test_push_envelope.py tests/test_daily_digest.py`，结果 `15 passed, 1 warning in 1.07s`。
- 任务 4 全量回归：`tests/`，结果 `1261 passed, 6 skipped, 139 warnings in 86.06s`。
- 任务 5 红灯：`tests/test_api_push_envelope.py` 先失败于 route 仍调用旧 `push_to_qq()`，结果 `2 failed, 21 warnings`。
- 任务 5 绿灯和回归：`tests/test_api_push_envelope.py` 加两个流式断连回归，结果 `4 passed, 21 warnings in 1.27s`。
- 任务 5 全量回归：`tests/`，结果 `1263 passed, 6 skipped, 139 warnings in 85.19s`。
- 任务 6 文档占位词扫描：`docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/message-envelope.md`，结果无输出，退出码 0。
- 任务 6 文档格式检查：`git diff --check -- docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/message-envelope.md`，结果无输出，退出码 0。
- 任务 6 P2-2 定向回归：`tests/test_message_envelope.py tests/test_chat_response_envelope.py tests/test_streaming_response_envelope.py tests/test_group_response_envelope.py tests/test_push_envelope.py tests/test_api_push_envelope.py tests/test_api.py tests/test_streaming_api.py tests/test_daily_digest.py tests/test_reply_contract.py tests/test_bridge_integration.py`，结果 `130 passed, 21 warnings in 23.94s`。
- 任务 6 全量回归：`python -B -m pytest tests/ -v -p no:cacheprovider`，结果 `1263 passed, 6 skipped, 139 warnings in 90.13s`。

后续事项：

- P2-2.5 已收口 `client_meta` 边界层解析 / 校验，`platform`、`chat_type` 和 `trace` 关键字段均已有运行时校验。
- P2-3 继续处理 QQ 出站渲染契约，采用响应信封 `messages` + 集中 renderer 的方案，收敛图片、表情、@、引用和 HTML 出口层职责。

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

状态：P1-8 已完成。`docs/todo.md` 路线项 3 已从早期「尚未接入能力校验」口径同步为已落地口径；后续只保留 base64 data URL、图片数量 / 大小上限、platform 出站契约等相邻路线项。历史待办清单文件仍是历史清单，不能作为当前优先级来源。

目标：

- 把模型能力从 tags / 模型 ID 猜测升级为模型记录顶层结构化字段，至少包括 `supports_image`、`supports_tools` 和 `supports_stream`。（已完成 registry / override / 候选硬过滤）
- 在候选模型排序前生成请求能力需求：messages 含 `image_url` 时要求 `supports_image`，传入 tools 时要求 `supports_tools`，真实 streaming 请求要求 `supports_stream`。（已完成直接 New API 路径和 Bridge 主回复路由）
- 主回复路由、`NewAPIClient.chat_completion()`、`chat_completion_stream()` 和 KT SDK request 边界都不能绕过能力过滤。（直接 New API、Bridge 和 payload guard 已完成）
- 手动指定回复模型也要校验能力；不满足时记录原因并回退自动路由，而不是盲发 payload。
- 当没有可用视觉模型时，降级为纯文本说明或明确错误，禁止把 `image_url` 发给纯文本模型。

只读审计结论：

- 当前模型记录是普通 dict，没有强 schema；已存在字段主要是 `id`、`provider`、`intelligence`、`cost_input_1m`、`cost_output_1m`、`tier`、`tags`、`description`、`reasoning`、`context_window` 和 `enabled`。
- 现有 `tags` 中的 `vision` / `multimodal` / `tool_use` 只能作为兼容推断来源；P1-8 不复用 `required_tags` 承载硬能力约束，因为旧 `ModelRegistry.select_model(required_tags=<标签>)` 是软过滤语义。
- 能力字段采用顶层布尔字段，并兼容 overrides 中的嵌套 `capabilities` 输入；`supports_image` 缺失默认 false 或由 vision tag / 模型名推断，`supports_tools` 和 `supports_stream` 首版应保持兼容默认，至少先硬排除显式 false。
- Bridge 带图主链路的数据流是 `metadata["files"]` → `prepare_image_parts()` → `ImagePart(data_url)` → KT `Message.to_dict()` → OpenAI `image_url` content part；当前模型选择发生在图片 event content 构造之后，因此候选过滤前已经能拿到 `has_image`。
- KT controller 的生产 LLM 调用本身固定 streaming；`/chat?stream=true` 只控制 SSE 输出队列和用户 event stream 标记。因此 `supports_stream` 校验不能只覆盖直接 `NewAPIClient.chat_completion_stream()`，也要覆盖 Bridge / KT provider 的真实请求。
- ToolPlan schema 会进入 Prompt Runtime；真实 OpenAI `tools` 只在直接 New API 路径或 KT native tool mode 的 SDK request 中出现。P1-8 要先保证候选过滤，再在 payload / SDK request 构造前加安全网。
- 测试优先级以生产路径为准：先覆盖 `NanobotBridge.handle_message(files=<文件列表>) -> get_ordered_candidates(required_capabilities=<需求>)`，再覆盖 registry 归一化、直接 New API、stream / tools payload 和 eval runner。

阶段拆分：

- [x] 已确认 P1-7 已完成：定向测试 `186 passed, 20 warnings`，全量测试 `1222 passed, 6 skipped, 113 warnings`，并随 `b3d27f5` 归档。
- [x] 已从 `docs/todo.md` 确认下一优先级为路线项 3「请求构造按模型能力校验」。
- [x] 已完成 P1-8 只读审计：模型 registry / overrides、Bridge 图片和 stream 数据流、现有测试覆盖缺口均已梳理。
- [x] 写入设计文档：`docs/superpowers/specs/2026-06-18-model-capability-validation-design.md`，覆盖能力字段、过滤策略、降级策略、手动模型策略和测试计划。提交：`ded7213 docs(模型能力): 设计请求能力校验`。
- [x] 写入实现计划：`.Codex/plans/model-capability-validation.md`，按 TDD 拆分红灯、绿灯、重构和阶段提交。
- [x] 任务 1-3：完成 registry 能力归一化、override `null` fallback、`get_ordered_candidates(required_capabilities=<需求>)` 硬过滤和相关测试。提交：`388c00f feat(模型能力): 归一化能力并过滤候选`。说明：Bridge 带图候选要求已顺延到任务 5，不再归入任务 1。
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
- [x] `nanobot_kt/image_pipeline.py` 主链路已由 `NanobotBridge.handle_message()` 使用 `await asyncio.to_thread(prepare_image_parts, *args)` 卸载。
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
- 任务 2 红灯：临时直接调用 `prepare_image_parts(parts)` 后，`test_handle_message_uses_multimodal_event_for_files` 失败于 `assert to_thread_calls`。
- 任务 2 绿灯：守卫单测 `1 passed, 1 warning`；图片相关回归 `59 passed, 1 warning`。
- 任务 3 红灯：临时把 3 个 Direct 工具改为直接同步调用后，3 个新增守卫全部失败，均未记录到 `asyncio.to_thread`。
- 任务 3 绿灯：新增守卫 `3 passed, 1 warning`；Direct 工具回归 `41 passed, 1 warning`。
- 引用审计：`cache_sticker_preview(` 只剩后台 job、同步 admin / public endpoint；async service 使用 `await asyncio.to_thread(cache_sticker_preview_bg, sticker_id)`。
- P1-7 定向测试：`186 passed, 20 warnings in 35.26s`。
- P1-7 全量测试：`1222 passed, 6 skipped, 113 warnings in 86.76s`。

## 已完成阶段详情：P1-6 删除冗余提示词资产并去版本化

状态：已完成。P1-6 任务 1-6 已完成：旧管理入口已由后端 410 catch-all 接管，WebUI 旧 route / 旧页面组件已删除，旧任务 prompt 已迁移到 V2 task template，V1 live 分支已封存，旧资产删除、禁止项扫描、相关回归、WebUI 构建和全量测试均已通过，并已随 `597a514` 单独提交归档。任务 7「建立无版本 canonical prompt 命名兼容层」已完成红灯、绿灯实现、相关回归、WebUI 构建、禁止项扫描和全量测试，并已随 `4fe00bb` 单独提交归档。任务 8 已把 `docs/todo.md`、本文件、P1-6 设计文档和实现计划同步到当前事实，并通过最终引用守卫和回归验证。

- [x] 重新核对 `docs/todo.md` 路线项 1，确认 P1-6 是 P1-5 之后的下一优先级。
- [x] 核对历史待办清单文件，确认它记录了更旧的路线状态，仅作历史核对，不作为优先级来源。
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

已完成 BridgePool 在途请求等待、日志保存失败回滚、相关待办状态同步等前置修复。

相关提交：

- `95683ed fix(BridgePool): 停止前等待在途请求完成`
- `91d5f75 fix(记忆): 保存日志失败时回滚事务`
- `3a4ce44`：同步缺陷修复状态

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

### 阶段 7.5：同步待办进度

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

### 阶段 13.5：TimingGate scoring 最终判定补漏

状态：已完成。

目标：收口 2026-06-18 复审发现的 TimingGate 规格偏差，确保群聊 enabled 正常模型返回路径也以 shared scoring 的 `E_final >= theta` 判定为准，而不是继续采用模型 raw `action`；同时把 TimingGate 输出解析质量回灌到 `TimingModelHint.confidence`。

已完成：

- JSON 解析结果携带 `parse_quality="json"` 与 `model_confidence=0.8`。
- 旧格式 `是/否,数字` 解析结果携带 `parse_quality="legacy"` 与 `model_confidence=0.5`。
- 非法输出和网络错误携带低置信 `model_confidence=0.0`。
- 群聊 `_apply_gate_result()` 在正常模型返回、模型失败和 timer 路径均使用 `_score_timing()` 的最终 `action/delay_seconds/reason`。
- `wait.delay_seconds` 上限从 30 秒收敛到设计要求的 15 秒。
- 补充红灯回归：低置信 legacy `continue` 在 `directed_to_other + linger` 冲突场景下应被 scoring blend 判为 `no_reply`。

验证结果：

- 红灯验证：新增解析质量 / delay 上限 / scoring blend 用例在实现前失败。
- 定向回归：`tests/test_timing_gate.py tests/test_timing_runtime.py`，结果 `76 passed, 1 warning`。
- TimingGate 相关回归：`tests/test_timing_gate.py tests/test_timing_score.py tests/test_timing_runtime.py tests/test_timing_gate_prompt_policy.py tests/test_timing_signal_audit.py tests/test_private_timing.py`，结果 `110 passed, 1 warning`。
- CLI 门禁：`scripts/run_timing_gate_gate.sh`，结果 `Suite: timing_gate total=18 passed=18 failed=0`，`Gate passed`。

## 下一步

TimingGate `s_bot` live path 收口、私聊 fallback 置信度收口、P4-5E knowledge fixture citation 正例、P4-5F sticker fixture sendable 正例、P4-5G group_memory fixture 正例和 P4-5H RAG 过滤约束 fixture 均已完成。默认下一步是路线项 8 的真实样本运营动作。

TimingGate 真实日志标注、周期复跑报告调参和更多真实样本仲裁属于后续延续项，不抢占当前默认执行顺序。Prompt V2、P2-4、P3-1、P4-5D、P4-5E、P4-5F、P4-5G 和 P4-5H 均已完成，历史章节中保留的旧阶段说明仅作为执行记录，不再作为下一步来源。
