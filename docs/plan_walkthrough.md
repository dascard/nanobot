# Nanobot Server 阶段计划 Walkthrough

计划日期：2026-06-17
更新日期：2026-06-18
本轮计划写入日期：2026-06-18

本文记录当前长期目标的完整阶段计划，用于继续推进 `docs/todo.md` 中的架构演进路线，并保持每个阶段完成后单独验证、单独提交。本次校准日期为 2026-06-18，基于当前工作区、最近提交和 `docs/todo.md` 重新核对：P1-6 任务 1-6 已随 `597a514 refactor(提示词): 删除旧版提示词资产` 归档；任务 7「建立无版本 canonical prompt 命名兼容层」已随 `4fe00bb refactor(提示词): 建立无版本运行时命名` 归档。当前进入任务 8：文档同步与最终验证。

## 当前目标

TimingGate「规则信号 + 模型」混合决策主线已经完成阶段性落地，Prompt V2 默认 live 接管、H29 第一刀、P1-5 Prompt legacy 收口，以及 P1-6 的任务模板迁移、V1 live 分支封存、旧管理面下线、旧资产删除、无版本命名兼容层和文档最终验证均已完成。下一步回到 `docs/todo.md` 的后续路线：P1-7 残余同步 IO 审计、P1-8 模型能力校验，以及 P2 的 platform 维度底座。

## 文档口径

- `docs/todo.md` 是当前架构路线的主参考，但它只记录路线级状态；当它与提交记录、`.Codex/plans/` 任务进度或本文件冲突时，以已提交代码和本文件的当前详细计划为准。
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
| P1-7 | 已部分完成，待继续 | 连接池复用与残余同步 IO 审计 | 共享 `aiohttp.ClientSession` 已落地；继续审计 compaction / image / sticker 同步 IO | `4550aca` / `2bf4ee7` |
| P1-8 | 待执行 | 模型能力校验 | 为模型配置补 `supports_image` / `supports_tools` / `supports_stream`，请求构造前按能力过滤和降级 | `feat(路由): 按模型能力校验请求` |
| P2-1 | 待执行 | 工具配置增加 platform 维度 | 工具解析支持 platform scope，运行时审计带 platform | `feat(工具): 支持平台维度配置` |
| P2-2 | 待执行 | 标准化请求 / 响应信封 | `/chat`、流式 done、`/group/message`、push 共享响应结构，私聊也返回 `reply_meta` | `refactor(消息): 统一响应信封` |
| P2-3 | 待执行 | QQ 出站渲染契约 | 输出结构化 segments，图片和 HTML 渲染集中在出口层 | `feat(渲染): 定义 QQ 出站消息契约` |
| P2-4 | 待执行 | Prompt platform × chat_type 二维适配 | V2 模板按平台和会话类型拆分，QQ 专属约定下沉到 platform 分支 | `feat(提示词): 支持平台化模板分支` |
| P3-1 | 已部分完成，待继续 | SSE 真 token 流式剩余收敛 | 已贯通 `/chat` 的 `stream` 参数并补齐 `/chat-step` SSE；继续补 chunk 合并窗口、backpressure、工具回合语义和统一信封 | `2369081` / 后续 `refactor(流式): 收敛增量输出契约` |
| P3-2 | 运营项 | TimingGate 持续评估 | 用更多人工标注样本复跑审计，接入外部 CI / PR gate | `ci(评测): 接入 timing gate 回归门禁` |
| P4-1 | 待执行 | 评测体系扩展 | 扩 per-capability 数据集，打通 `candidates → labeled` 标注闭环 | `feat(评测): 扩展能力评测数据集` |

## 当前详细计划：P1-6 删除冗余提示词资产并去版本化

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

P1-6 已完成收口。`docs/todo.md` 中的后续路线仍可作为优先级参考：优先看 P1-7 残余同步 IO 审计、P1-8 模型能力校验，以及 P2 的 platform 维度底座；TimingGate 真实日志标注 / CI 接入属于后续运营项，不抢占 P1 后续实现顺序。
