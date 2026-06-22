# Nanobot Server 阶段计划 Walkthrough

计划日期：2026-06-17
更新日期：2026-06-21
本轮计划写入日期：2026-06-18
状态校准日期：2026-06-21

当前推进焦点：TimingGate proposal 运营链路已进入只读复核和运营闭环，代码迭代优先级已转回 P3 超大文件拆分；`api/admin_routes.py` 已降至 632 行并移出 >800 行清单，普通 `api/routes.py` 已完成 history / log 路由拆分并降至 2134 行，本文件后续阶段记录以 `api/routes.py` 的模块边界收敛为主。

本文记录当前长期目标的完整阶段计划，用于继续推进 `docs/todo.md` 中的架构演进路线，并保持每个阶段完成后单独验证、单独提交。2026-06-18 已基于当时工作区、最近提交和 `docs/todo.md` 做过详细校准；2026-06-20 仅修正文档状态漂移，不重写历史执行记录。同日续跑补记：测试 helper 的 `asyncio.Runner` 兼容性问题已随 `cfdd9c2 test(异步): 移除 Runner 测试依赖` 收口，提交前全量回归结果为 `1380 passed, 6 skipped, 139 warnings in 100.75s`，非 vendor Python 代码中无 `asyncio.Runner` 命中。TimingGate scoring 可观测性收尾也已完成：设计提交 `4824036 docs(时机): 设计评分可观测收尾`，计划提交 `2820f7a docs(计划): 记录评分可观测收尾计划`，实现提交 `9d5817c feat(时机): 补齐评分可观测字段`；验证包括红灯 `s_transport_tier` 缺失、绿灯 `1 passed`、相邻回归 `7 passed`、WebUI build 退出码 0、全量回归 `1380 passed, 6 skipped, 139 warnings in 103.22s`。P1-6 已随 `101c457 docs(计划): 同步提示词收口最终状态` 完成文档收口；P1-7「残余同步 IO 审计与收口」已随 `b3d27f5 docs(计划): 同步同步 IO 收口状态` 完成实现、验证和文档归档。P1-8「模型能力校验」也已完成：设计文档已随 `ded7213 docs(模型能力): 设计请求能力校验` 提交，实现计划已随 `d4748d2 docs(计划): 记录模型能力校验计划` 提交；registry 能力归一化和候选硬过滤已随 `388c00f feat(模型能力): 归一化能力并过滤候选` 落地，直接 New API 请求能力推导已随 `d907a98 feat(模型能力): 推导直接请求能力需求` 落地，Bridge 主回复路由能力校验已随 `66fdfd9 feat(桥接): 接入回复模型能力校验` 落地，payload / SDK request 前 guard 与无视觉候选降级已随 `d2a7a1f fix(模型能力): 防止发送不兼容请求` 落地，`model_routing` eval 覆盖已随 `e1d3bef test(评测): 覆盖视觉模型路由` 落地。P2-1「工具配置增加 platform 维度」已完成：只读审计、设计文档和实现计划已完成，设计文档随 `d221180 docs(工具): 设计平台维度配置` 提交，实现计划已写入 `.Codex/plans/tool-platform-scope.md`；后端解析任务已随 `bb7489c feat(工具): 支持平台维度解析` 落地，运行时决策 platform 审计已随 `295e3f7 feat(工具): 记录平台维度决策` 落地，真实入口 platform 透传已随 `73bbe8a feat(消息): 透传客户端平台` 落地，Admin API platform 覆盖和预览已随 `d9a1bae feat(工具): 支持平台覆盖接口` 落地，WebUI 工具页 platform selector 和「指定平台」覆盖入口已随 `2b0e203 feat(工具): 配置平台覆盖` 落地。

同日真实样本运营第一步已完成：TimingGate 信号周期审计设计提交 `8c7a563 docs(评测): 设计时机信号周期审计`，计划提交 `979639e docs(计划): 记录时机信号周期审计计划`，实现提交 `0980f22 ci(评测): 接入时机信号周期审计`。本阶段新增 `scripts/run_timing_signal_audit_periodic.sh`，接入 `scripts/run_eval_periodic.sh` keep-going 流程，并在 `docs/evals.md` 记录 `TIMING_SIGNAL_AUDIT_DB`、skipped 报告和 artifact 归档语义；验证包括红灯 `2 failed`、中间红灯 `1 failed, 1 passed`、绿灯 `2 passed`、相邻回归 `24 passed`、周期脚本全部 gate 通过，以及全量回归 `1382 passed, 6 skipped, 139 warnings in 107.45s`。

同日真实样本运营第二步已完成代码与 UI 落地：RAG generated → manual 仲裁入口设计提交 `e0d537d docs(评测): 设计 RAG 样本仲裁入口`，计划提交 `bd51e31 docs(计划): 记录 RAG 样本仲裁计划`，后端接口提交 `6567c99 feat(评测): 支持 RAG 样本提升接口`，WebUI 入口提交 `7dfdce7 feat(评测): 增加 RAG 样本仲裁入口`。本阶段新增 `POST /api/v1/admin/rag/benchmark/cases/{case_id}/promote-manual`，支持 dry-run/apply、stale 阻断、目标冲突阻断、覆盖备份和 `promote_rag_benchmark_generated_case` 审计；WebUI generated case 详情页已提供「提升为 Manual」二阶段确认流程。验证包括后端红灯 `3 failed`、后端绿灯 `3 passed`、后端相邻回归 `15 passed`、WebUI 红灯 `1 failed, 4 passed`、WebUI 绿灯 `5 passed`、定向组合 `20 passed`、RAG 相邻回归 `40 passed`、WebUI build 退出码 0，以及全量回归 `1386 passed, 6 skipped, 139 warnings in 103.04s`。

同日真实样本运营第三步已完成代码落地：EvalCandidate 运营规则设计提交 `8dc41f5 docs(评测): 设计候选运营规则`，计划提交 `aed5333 docs(计划): 记录候选运营规则计划`，后端 readiness / summary 和状态约束提交 `cbcc399 feat(评测): 增加候选晋升资格`，批量 preflight 与 CLI 聚合提交 `37ab830 feat(评测): 支持候选批量预检`，WebUI 运营预检提交 `376bffe feat(评测): 展示候选运营预检`。本阶段让候选列表返回 readiness 与 summary，阻止不可运行 suite 晋升，禁止 PATCH 直接写 `labeled` / `promoted`，并提供只读批量 preflight；CLI dry-run 输出 ready / blocked 聚合，apply 遇到 blocked 批次不做部分写入。

同日真实样本运营第四步已完成代码落地：EvalCandidate 候选仲裁状态设计提交 `d53ba55 docs(评测): 设计候选仲裁状态`，计划提交 `343ccef docs(计划): 记录候选仲裁计划`，后端状态机与 Admin API 提交 `7859ac8 feat(评测): 增加候选仲裁状态`，CLI / WebUI 入口提交 `cf9bddc feat(评测): 增加候选仲裁入口`。本阶段在不改 DB schema 的前提下，为通用候选队列增加 `rejected`、`deferred` 和 `reopen` 运营动作；动作必须走专用端点，记录统一 Admin audit detail，WebUI 支持单条暂缓、拒绝和复开，CLI 可按新状态导出。

同日真实样本运营第五步已完成代码落地：EvalCandidate 人工仲裁批次审计设计提交 `f95e67e docs(评测): 设计候选批次审计`，计划提交 `ba79917 docs(计划): 记录候选批次审计计划`，后端批次审计 API 提交 `c5eded7 feat(评测): 增加候选批次审计接口`，CLI / WebUI 只读入口提交 `97b0ab8 feat(评测): 增加候选批次审计入口`。本阶段新增 record-only 批次审计：dry-run 只生成快照，apply 只写一条 `AdminAuditLog`，不批量修改候选状态；CLI 可导出批次审计 JSON，WebUI 支持当前页「批次审计」只读弹窗。

同日真实样本运营第六步已完成代码落地：EvalCandidate 运营趋势报表设计提交 `2341da8 docs(评测): 设计运营趋势报表`，计划提交 `ced6f26 docs(计划): 记录候选趋势计划`，后端趋势 API 提交 `0f2f89e feat(评测): 增加候选趋势接口`，CLI 导出提交 `752513d feat(评测): 增加候选趋势导出`，WebUI 入口提交 `2f63ec7 feat(评测): 展示候选趋势报表`。本阶段新增只读 `GET /api/v1/admin/evals/candidates/trend` 与 `python -m evals.candidates trend`，按 `EvalCandidate.created_at` 分桶展示当前状态、readiness 和阻断原因；WebUI 提供「趋势报表」tab，不做调参或批量状态变更。

同日周期运行 manifest 阶段已完成代码落地：设计与计划提交 `7e17125 docs(评测): 设计周期运行清单`，manifest helper 提交 `a4660c1 feat(评测): 构建周期运行清单`，周期脚本接入提交 `f459acc ci(评测): 输出周期运行清单`。本阶段新增 `evals.periodic_manifest`，让 `scripts/run_eval_periodic.sh` 在 keep-going 周期复跑结束前写出 `periodic_manifest_latest.json`、`YYYY-MM-DD-periodic_manifest.json` 和 `runs/<run_id>/manifest.json`，索引通用 eval、RAG benchmark 和 TimingGate signal audit 的步骤状态、报告路径和摘要指标；第一版不新增 Admin API、WebUI 或调参逻辑。

同日跨 artifact 周期趋势阶段已完成代码落地：设计提交 `bd676f5 docs(评测): 设计周期趋势报表`，计划提交 `bf4fb0a docs(计划): 记录周期趋势计划`，趋势聚合提交 `9073262 feat(评测): 聚合周期趋势报表`，CLI 导出提交 `9aa3d9c feat(评测): 导出周期趋势报表`。本阶段新增 `evals.artifact_trends`，从 periodic manifest 生成只读 `artifact_trends_latest.json`，聚合 run、eval suite、RAG benchmark 和 TimingSignal audit 的跨 run 趋势；第一版不新增 Admin API、WebUI、gate 或调参逻辑。

同日周期趋势只读调参分析阶段已完成代码落地：设计提交 `4c5be89 docs(评测): 设计周期调参分析`，计划提交 `21edcc1 docs(计划): 记录周期调参分析计划`，分析骨架提交 `8dc6198 feat(评测): 建立调参分析骨架`，TimingSignal 证据分析提交 `fa5cab4 feat(评测): 分析时机信号证据`，趋势复核建议提交 `1a33b51 feat(评测): 生成趋势复核建议`，CLI 导出提交 `a9656a2 feat(评测): 导出调参分析报告`。本阶段新增 `evals.tuning_analysis`，输出只读 `tuning_analysis_latest.json`，把趋势退化和 raw audit 证据转成复核、补标注、补 artifact 或暂不调整建议；第一版不自动 apply 参数、不更新 baseline、不改变 gate。验证包括 readiness 红灯 `3 failed`、任务 2 红灯 `2 failed`、任务 3 红灯 `2 failed`、任务 4 红灯 `2 failed`、调参分析定向绿灯 `9 passed`、相邻回归 `18 passed`、CLI smoke 退出码 0，以及全量回归 `1425 passed, 6 skipped, 139 warnings in 106.14s`。

同日 TimingSignal 不可变 artifact 加厚阶段已完成代码落地：设计提交 `712cb0f docs(评测): 设计时机信号不可变报告`，计划提交 `59d7e60 docs(计划): 记录时机信号不可变报告计划`，审计脚本复制提交 `ca2a90c fix(评测): 复制时机信号审计报告`，周期入口索引提交 `df78dfd ci(评测): 索引时机信号不可变报告`，workflow 归档提交 `bad632b ci(评测): 归档时机信号运行报告`，运行级报告忽略规则提交 `95c88fe chore(评测): 忽略运行级评测报告`。本阶段让周期审计同轮写出 latest、dated 和 run-scoped 三类报告，manifest 优先索引 run-scoped TimingSignal audit，workflow artifact 归档 `evals/reports/runs/**/timing_signal_audit.json`，并避免本地 smoke 生成的运行级报告污染 git 状态；第一版不生成可执行调参 proposal、不更新 baseline、不改变 gate。

同日剩余项审计与文档口径同步阶段已启动：三路只读子 agent 分别核对 `docs/todo.md` 剩余项、TimingGate scoring 设计实现差距和 AGENTS / 工作区风险。审计结论是 AGENTS 任务前置输出和子 agent 分派规则已落地，TimingSignal 不可变 artifact 阶段提交链完整；`docs/todo.md` 和 TimingGate 设计文档存在状态口径漂移，需要把“已完成主链路”“待人工确认的调参提案”“仍未完成的 H29 / H30 / 多平台演进债”拆清楚。本阶段为纯文档收口，不修改代码、不自动调参、不更新 baseline、不改变 gate。

同日 TimingGate WebUI 余韵状态补齐阶段已完成代码落地：`GroupRuntime` 的 `timing_scoring.signals` 增加 `linger_active`、`linger_reply_count` 和 `linger_time_remaining`，WebUI TimingGate 详情页在「信号分解」中展示这三个状态。验证包括后端红灯 `KeyError: 'linger_active'`、WebUI 红灯字段缺失、红绿组合 `2 passed`、相邻回归 `84 passed, 1 warning in 2.01s`，以及 WebUI build 退出码 0；本阶段不改 scoring 公式、不改 gate 阈值、不自动调参。

2026-06-21 TimingGate 可审核调参提案第一版已完成只读链路：设计提交 `4dcb849`，计划提交 `42857f2`，report 骨架提交 `0d3469d`，CLI / 候选参数提交 `6d17d2e`，what-if 模拟提交 `32c104b`，TimingSignal 证据字段提交 `b52a13e`，Admin 只读 API 提交 `04a026e`，WebUI 只读展示和 dist 产物提交 `fbb4cdb`。本阶段新增 `evals.timing_tuning_proposal`，输出 `timing_tuning_proposal_latest.json`；Admin endpoint 为 `GET /api/v1/admin/evals/timing-tuning/proposal`；WebUI「Eval 评测」新增「调参提案」tab。验证包括 proposal 定向、TimingGate 相邻、Admin 相邻、WebUI 静态测试、`npm --prefix webui run build` 以及全量回归；最终全量为 `1446 passed, 6 skipped, 139 warnings in 107.54s`。第一版只读，不自动应用参数、不更新 baseline、不改变 PR gate 或周期 gate。

同日 TimingGate `directed_to_other` prompt 语义补漏已完成实现：计划提交 `82ca651`，实现提交随本阶段最终提交收口。内嵌 `TIMING_GATE_PROMPT` 与默认 Prompt Runtime 模板已改为“仅指向其他人默认 no_reply；同时指向 bot、回复 bot 或处于 bot 对话余韵冲突时结合上下文裁量”；`timing_gate` eval 新增 `@bot + @其他人` 与 `directed_to_other + linger` 两个 model-assisted conflict case，baseline 从 18 更新到 20。阶段验证包括红灯 `2 failed, 7 passed`、prompt policy 绿灯 `9 passed`、TimingGate gate `total=20 passed=20`、Timing 相邻回归 `94 passed`、eval baseline 组合 `34 passed`，以及最终全量 `1447 passed, 6 skipped, 139 warnings in 108.22s`。

2026-06-21 TimingGate 调参提案运营链路计划已启动：设计文档已写入 `docs/superpowers/specs/2026-06-21-timing-tuning-operations-design.md`，设计提交为 `4f7d13a docs(时机): 设计调参提案运营链路`；实现计划已写入 `.Codex/plans/timing-tuning-operations.md`。本阶段目标是把第一版只读 proposal 接入真实 run-scoped audit、`final_timing_action` 人工 truth、候选参数治理和 record-only 人工审核状态；仍不自动应用参数、不更新 baseline、不改变 PR gate 或周期 gate。任务 1 已完成：TimingSignal audit 现在公开 `FINAL_TIMING_ACTIONS` 与 truth 校验 helper，sidecar JSONL 合并会保留 `final_timing_action`，`run_audit()` / `run_labeled_audit()` 与周期脚本 skipped 报告都会写入 `source.run_id`；验证结果为红灯 `4 failed`、目标绿灯 `5 passed`、任务文件回归 `13 passed`、全量回归 `1451 passed, 6 skipped, 139 warnings in 113.43s`。任务 2 已完成：proposal CLI 默认读取候选参数路径并支持 `--run-id`，TimingSignal audit 输入收紧为 run-scoped artifact，显式 latest 会被拒绝，报告 readiness 会阻断 run mismatch、非法 `final_timing_action`、重复/缺失候选 ID 和空 `param_diff`，候选输出透传 `expected_effect` 与 `evidence_refs`；验证结果为红灯 `4 failed, 7 passed`、proposal 绿灯 `11 passed`、相邻回归 `25 passed`、全量回归 `1455 passed, 6 skipped, 139 warnings in 107.18s`。任务 3 已完成：TimingScore simulation 现在从 `timing_input` 重放真实 TimingSignal audit 样本，输出 `sources`、`source_type`、`log_id` 和 `signal_name`，缺少 `timing_input` 的 truth 样本会被跳过并让 proposal readiness 记录 `missing_replay_input`；proposal CLI 已把 audit samples 注入 simulation。验证结果为红灯 `4 failed, 13 passed`、任务绿灯 `17 passed, 1 warning in 1.84s`、全量回归 `1458 passed, 6 skipped, 139 warnings in 109.57s`。任务 4 已完成：Admin 新增 record-only proposal review GET / POST，按 report SHA256 记录和读取 `review_timing_tuning_proposal` 审计日志，decision 使用白名单校验，接口不应用参数、不更新 baseline、不写配置；验证结果为红灯 `3 failed, 3 passed`、任务绿灯 `7 passed, 21 warnings in 3.02s`、全量回归 `1461 passed, 6 skipped, 139 warnings in 108.71s`。任务 5 已完成：WebUI 调参提案页加载 `/evals/timing-tuning/proposal/review` 展示 proposal SHA256 与最新审核状态，并通过 `/evals/timing-tuning/proposal/reviews` 提供 record-only 审核表单；`approved_for_manual_experiment` 仅表示进入人工实验，不代表生产参数已变更，页面仍不提供应用参数、更新 baseline 或写入配置入口。验证结果为红灯 `1 failed, 1 warning`、定向绿灯 `1 passed, 1 warning in 0.75s`、WebUI 静态回归 `23 passed, 1 warning in 1.06s`、WebUI build 退出码 0、全量回归 `1461 passed, 6 skipped, 139 warnings in 112.60s`。

计划列表：

- [x] 设计：固定真实运营链路边界、输入合同、人工 truth sidecar、候选参数治理、review API / UI 语义和子 agent 分工。提交：`4f7d13a`。
- [x] 任务 1：TimingSignal audit 支持 `source.run_id` 与 `final_timing_action` 合同。
- [x] 任务 2：proposal 收紧 run-scoped 输入与候选参数治理。
- [x] 任务 3：simulation 标识真实 audit 样本来源并守住 `timing_input`。
- [x] 任务 4：Admin 增加 record-only proposal review API。
- [x] 任务 5：WebUI 展示审核状态并提供记录型审核入口。
- [x] 任务 6：同步 `docs/evals.md`、`docs/todo.md`、本 walkthrough 和计划勾选，完成最终验证。

任务 6 文档收口已完成：`docs/evals.md` 已补充 TimingGate 调参提案运营小节、run-scoped audit / `final_timing_action` / record-only review / baseline 边界；`docs/todo.md` 已同步路线项 8 和路线项 10 的完成状态与下一步运营口径；本文件和 `.Codex/plans/timing-tuning-operations.md` 已同步任务 6 勾选和验证结果。验证结果为文档红旗词扫描无输出、旧口径扫描无输出、`git diff --check` 无输出；TimingGate gate `total=20 passed=20 failed=0` 且 `Gate passed`；周期脚本 eval guard `34 passed, 1 warning`，各子 gate 均通过，RAG `cases=13 passed=13 failed=0`，TimingSignal audit 写出 latest、dated 和 run-scoped 报告；WebUI build 退出码 0，仅有 Vite 警告；全量回归 `1461 passed, 6 skipped, 139 warnings in 109.55s`。

2026-06-21 TimingGate 真实数据 proposal 运营复核阶段已完成第一轮证据收口：三路只读审计确认当前实现入口实际位于 `evals.timing_signal_audit`、`evals.timing_tuning_proposal` 和 `core/eval_sampling/timing_signal_audit.py`；本地 `data/nanobot.db` 有 `chat_logs=47931`、`ambient=47187`、`timing_gate=31582`，但没有 `scoring`、`sub_signals`、`final_timing_action` 或 `run_id` 字段命中，因此现有真实库不能合法生成非零 TimingSignal audit，也不能伪造 truth 或用旧 runtime 字段反推 `timing_input`。本阶段同时修复 `scripts/run_eval_periodic.sh` 未导出 `PERIODIC_RUN_ID` 导致 run-scoped audit `source.run_id` 为空的问题；红灯为 `test_eval_periodic_script_writes_manifest` 缺少 `export PERIODIC_RUN_ID`，绿灯为同一测试 `1 passed`，相邻回归 `tests/test_eval_baseline.py tests/test_timing_signal_audit_periodic.py tests/test_timing_signal_audit.py tests/test_timing_tuning_proposal.py` 结果 `50 passed, 1 warning in 3.36s`。`docs/evals.md` 已补充 run-scoped audit 命令、候选参数最小格式和当前 readiness 阻断证据；本阶段仍不自动应用参数、不更新 baseline、不改变 PR gate 或周期 gate。

P2-2「标准化请求 / 响应信封」的响应信封兼容双写已完成并通过最终验证：只读审计已完成，设计文档已随 `c984036 docs(消息): 设计响应信封标准` 提交，实现计划已写入 `.Codex/plans/message-envelope.md`；任务 1 共享 builder 已随 `147421b feat(消息): 构建响应信封` 提交，任务 2 `/chat` 非流式与 SSE done 信封已随 `57006f3 feat(消息): 返回私聊响应信封` 提交，任务 3 `/group/message` 信封已随 `49b3104 feat(消息): 返回群聊响应信封` 提交，任务 4 push owner 信封适配已随 `fc0eeaf feat(推送): 支持信封推送适配` 提交，任务 5 route push 集成已随 `0c37a30 feat(推送): 接入路由信封推送` 提交，任务 6 响应侧文档和最终验证随 `617aa25 docs(计划): 同步响应信封状态` 收口。P2-2.5「client_meta 边界层校验」设计文档已随 `ce05b35 docs(计划): 设计客户端元信息校验` 提交，`core/client_meta.py` 已随 `d92b632 feat(消息): 校验客户端元信息边界` 接入 `/chat` 与 `/group/message`，把路线项 5 的剩余尾项收口。P2-3「QQ 出站渲染契约」已完成设计、计划、renderer、push、schedule、route 回归、富媒体边界、prompt usage 同步、文档收口和最终验证：设计提交为 `c72ddb3`，计划提交为 `1f4aa69`，实现与测试提交为 `72a9751`、`0c8c590`、`f19b09b`、`f0bfbdf`、`04ff6d3`、`6aea7f8`；文档收口提交为 `docs(计划): 收口 QQ 出站渲染状态`。P2-4「Prompt platform × chat_type 二维适配」已完成设计、计划、核心编排、Bridge / Admin 透传、QQ 模板迁移和集成回归，提交为 `27e632f`、`164b215`、`ca93dc2`、`18d0b0d`、`17a7bd8`、`fe2d81b`。P3-1「SSE 真 token 流式剩余收敛」已完成设计、实现、文档收口和最终验证，提交为 `bca50b8`、`e56a406`、`d8e8703`、`84cb0cb`、`a987d31`、`88268a1`、`a5f705a`、`87f3b40`；最终验证结果为流式定向回归 `23 passed`、API / Bridge 回归 `145 passed`、全量测试 `1311 passed, 6 skipped`。P3-2「私聊 TimingGate 可观测补齐」已完成代码实现和最终验证，提交为 `14b47a5 feat(时机): 持久化私聊评分元信息`；随后 `/models/status` 本地模型回退缺失 import 的独立小修已随 `5c69b7e fix(模型): 修复状态接口本地模型回退` 提交。P3-3「TimingGate 持续评估」已完成三路只读审计、阶段拆分、P3-3A 标注审计复跑入口和 P3-3B 仓库自包含 CI / PR gate。TimingGate `s_bot` live path 收口已完成任务 1：设计提交为 `6463ee8 docs(时机): 设计 s_bot live path 收口`，计划提交为 `1795d04 docs(计划): 记录 s_bot live path 收口计划`，实现提交为 `2fcfad7 fix(时机): 接入其他 bot 软抑制评分`；`current_bot` 自身回声仍保持入口 hard stop，`explicit_bot` / `client_meta` 其他 bot sender 会标记为 `is_other_bot=True` 进入 `GroupRuntime`，`GroupPendingMessage` 透传该字段，`_score_timing()` 聚合 pending 后调用 `decide_timing(is_other_bot=any(m.is_other_bot for m in msgs))`，route 测试已断言 ChatLog meta 中 `s_bot=0.70`。任务 1 定向验证为 `3 passed, 21 warnings in 2.16s`，相邻回归为 `157 passed, 21 warnings in 23.30s`。私聊分类器失败 / 非法输出置信度收口已随 `0763802 fix(时机): 修复私聊分类器失败置信度` 完成，分类器 `invalid output fallback` / `classifier fallback` 会以 `model_confidence=0.0` 进入 shared scoring 的 `rule_fallback`，旧格式兼容仍保留 `0.5` 低置信。P4-1「评测数据集与标注闭环」已完成 expected 契约、候选标注、promote dry-run、离线 CLI、dataset / suite 边界和首个 `capability_model_routing` 能力数据集；P4-2「Admin 标注工作台契约化与 promote 预检 UI」已完成后端 expected contract schema/API、WebUI 契约化标注和 promote 预检流程；P4-3「能力契约评测数据集扩展」已完成 reply / rendering 两个能力数据集、baseline gate 和最终回归；P4-4「RAG baseline 门禁」已完成 RAG benchmark 专用 baseline diff、CLI gate、稳定 baseline、Admin API 和 WebUI 展示；P4-5A「统一评测 PR gate」已完成统一脚本和 CI 接入；P4-5B「周期性复跑与报告归档」已完成 keep-going 脚本、workflow schedule / manual dispatch 和 artifact 归档；P4-5C「RAG manual 样本扩充」已完成；P4-5D「RAG fixture 正例门禁」已完成；P4-5E「RAG knowledge fixture 引用正例门禁」已完成；P4-5F「RAG sticker fixture sendable 正例门禁」已完成；P4-5G「RAG group_memory fixture 正例门禁」已完成；P4-5H「RAG 过滤约束 fixture」已完成。真实样本运营 1-10 和 TimingGate 调参提案 record-only 审核运营链路均已完成，下一阶段是用真实 run-scoped artifact、action truth 和候选参数持续生成可审查报告，并沉淀人工审核结论。

## 当前目标

TimingGate「规则信号 + 模型」混合决策主线已经完成阶段性落地，Prompt V2 默认 live 接管、H29 第一刀、P1-5 Prompt legacy 收口、P1-6 旧提示词资产收敛、P1-7 残余同步 IO 审计与 async 热路径隔离、P1-8 模型能力校验，以及 P2-1 工具 platform 维度配置均已完成。当前 `docs/todo.md` 路线项 4 已落地：`ToolOverride(scope_type="platform")`、`RuntimeToolDecision.platform`、真实入口 platform 透传、Admin API 平台覆盖预览和 WebUI 平台覆盖入口都已具备。路线项 5 已完成响应信封兼容双写和 `client_meta` 关键字段边界校验；P2-3「QQ 出站渲染契约」、P2-4「Prompt platform × chat_type 二维适配」、P3-1「SSE 真 token 流式剩余收敛」、P3-2「私聊 TimingGate 可观测补齐」、P3-3A「标注审计复跑入口」、P3-3B「TimingGate CI / PR gate」、P4-1「评测数据集与标注闭环」、P4-2「Admin 标注工作台契约化与 promote 预检 UI」、P4-3「能力契约评测数据集扩展」、P4-4「RAG baseline 门禁」、P4-5A「统一评测 PR gate」、P4-5B「周期性复跑与报告归档」、P4-5C「RAG manual 样本扩充」、P4-5D「RAG fixture 正例门禁」、P4-5E「RAG knowledge fixture 引用正例门禁」、P4-5F「RAG sticker fixture sendable 正例门禁」、P4-5G「RAG group_memory fixture 正例门禁」、P4-5H「RAG 过滤约束 fixture」、真实样本运营第一步「TimingGate 信号周期审计」、第二步「RAG generated → manual 仲裁入口」、第三步「EvalCandidate 运营规则」、第四步「候选 reject / defer 仲裁状态」、第五步「人工仲裁批次审计」、第六步「运营趋势报表」、第七步「周期运行 manifest」、第八步「跨 artifact 周期趋势」、第九步「周期趋势只读调参分析」、第十步「TimingSignal 不可变 artifact 加厚」和 TimingGate 调参提案 record-only 审核运营链路均已完成代码落地。TimingGate `s_bot` live path 偏差已完成代码收口：其他 bot sender 不再被 `bot_sender_no_timing` 统一 hard stop，而是进入 scoring 并触发 `s_bot` soft reject；当前 bot 自身回声仍 hard stop。私聊分类器失败 / 非法输出已收敛到 `model_confidence=0.0` 的规则兜底语义；`directed_to_other` prompt 也已补齐纯指向他人与冲突裁量的区别。默认下一步是用真实 run-scoped audit、final action truth 和候选参数文件持续生成 proposal，并通过 record-only 审核沉淀人工结论。

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
| TimingGate 信号周期审计 | 已完成 | 周期评测接入 `scripts/run_timing_signal_audit_periodic.sh`，缺库写 skipped 报告，提交 `0980f22` |
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
| 真实样本运营 1 | 已完成 | TimingGate 信号周期审计周期化 | 周期脚本额外产出 TimingGate signal audit 报告，缺少真实 DB 时写 `source.mode=skipped` 空报告并退出 0 | `8c7a563` / `979639e` / `0980f22` |
| 真实样本运营 2 | 已完成 | RAG generated → manual 仲裁入口 | 单条 generated case 支持 dry-run/apply 提升为 manual case，WebUI 提供二阶段确认，不自动更新 baseline | `e0d537d` / `bd51e31` / `6567c99` / `7dfdce7` |
| 真实样本运营 3 | 已完成 | EvalCandidate 运营规则 | 通用候选队列支持 readiness、summary、批量 preflight、CLI 聚合 dry-run 和 WebUI 当前页预检 | `8dc41f5` / `aed5333` / `cbcc399` / `37ab830` / `376bffe` |
| 真实样本运营 4 | 已完成 | EvalCandidate 候选仲裁状态 | 通用候选队列支持 `rejected`、`deferred` 和 `reopen`，Admin audit 记录原因码，WebUI 支持单条暂缓 / 拒绝 / 复开 | `d53ba55` / `343ccef` / `7859ac8` / `cf9bddc` |
| 真实样本运营 5 | 已完成 | EvalCandidate 人工仲裁批次审计 | 通用候选队列支持 record-only 批次审计，Admin apply 写单条审计日志，CLI / WebUI 提供只读批次快照 | `f95e67e` / `ba79917` / `c5eded7` / `97b0ab8` |
| 真实样本运营 6 | 已完成 | EvalCandidate 运营趋势报表 | 按创建日期分桶展示当前候选状态、readiness 和阻断原因，只读不调参 | `2341da8` / `ced6f26` / `0f2f89e` / `752513d` / `2f63ec7` |
| 真实样本运营 7 | 已完成 | 周期运行 manifest | 周期复跑写出 manifest，索引通用 eval、RAG benchmark 和 TimingGate signal audit 的步骤状态、报告路径和摘要指标 | `7e17125` / `a4660c1` / `f459acc` |
| 真实样本运营 8 | 已完成 | 跨 artifact 周期趋势 | 基于 periodic manifest 聚合 run、eval、RAG 和 TimingSignal 趋势，只读不调参 | `bd676f5` / `bf4fb0a` / `9073262` / `9aa3d9c` |
| 真实样本运营 9 | 已完成 | 周期趋势只读调参分析 | 基于趋势报告、raw TimingSignal audit 和 manifest 输出复核、补标注、补 artifact 或暂不调整建议，只读不自动调参 | `4c5be89` / `21edcc1` / `8dc6198` / `fa5cab4` / `1a33b51` / `a9656a2` |
| 真实样本运营 10 | 已完成 | TimingSignal 不可变 artifact 加厚 | 周期审计同轮写出 latest、dated 和 run-scoped 报告，manifest 优先索引 run-scoped，workflow artifact 归档运行级报告，本地忽略运行级产物 | `712cb0f` / `59d7e60` / `ca2a90c` / `df78dfd` / `bad632b` / `95c88fe` |
| TimingGate 可审核调参提案 | 已完成 | 只读 proposal report、离线 what-if 模拟、TimingSignal 证据字段、Admin API、WebUI 展示 | 不自动应用参数、不更新 baseline、不改变 gate；最终全量回归 `1446 passed, 6 skipped, 139 warnings` | `4dcb849` / `42857f2` / `0d3469d` / `6d17d2e` / `32c104b` / `b52a13e` / `04a026e` / `fbb4cdb` |
| TimingGate 调参提案运营链路 | 已完成 | run-scoped audit、`final_timing_action` truth、候选参数治理、真实样本 simulation、record-only 审核 API 和 WebUI 入口 | 只记录人工审核结论，不自动应用参数、不更新 baseline、不改变 gate | `4f7d13a` / `90d850d` / `b818102` / `baca3d2` / `32628ee` / `69595c7` |
| TimingGate 指向他人 prompt 语义补漏 | 已完成 | 内嵌 prompt 与默认模板区分纯 directed no_reply 和 `@bot` / 回复 bot / 余韵冲突裁量；新增 paired eval case 并更新 baseline 到 20 | 不改 scoring 公式、不自动调参、不改变 PR gate 或周期 gate | `82ca651` / 本阶段实现提交 |

## 已完成阶段详情：TimingGate 可审核调参提案

状态：已完成第一版只读链路。设计文档为 `docs/superpowers/specs/2026-06-21-timing-gate-tuning-proposal-design.md`，设计提交为 `4dcb849 docs(时机): 设计可审核调参提案`；实现计划为 `.Codex/plans/timing-gate-tuning-proposal.md`，计划提交为 `42857f2 docs(计划): 记录调参提案实现计划`。核心 report 骨架已随 `0d3469d feat(评测): 建立调参提案报告` 落地，CLI 与候选参数校验已随 `6d17d2e feat(评测): 导出调参提案报告` 落地，离线 what-if 模拟已随 `32c104b feat(时机门控): 支持候选参数模拟` 落地，TimingSignal 证据字段已随 `b52a13e feat(评测): 加厚时机信号提案证据` 落地，Admin 只读 API 已随 `04a026e feat(评测): 提供调参提案只读接口` 落地，WebUI 只读展示和 dist 产物已随 `fbb4cdb feat(评测): 展示调参提案状态` 落地。

交付物：

- `evals.timing_tuning_proposal`：输出 `evals/reports/timing_tuning_proposal_latest.json`，报告包含 `readiness`、`candidate_sets`、`parameters`、`simulation`、`validation_plan` 和 `blocked_actions`。
- `evals.timing_score_simulation`：基于 eval case 和显式候选参数 diff 做离线 what-if 模拟，不改变 live `decide_timing()` 默认行为。
- `GET /api/v1/admin/evals/timing-tuning/proposal`：只读读取 proposal 报告；缺报告返回可解释的 `exists=false` 状态。
- WebUI「Eval 评测」新增「调参提案」tab，只展示 readiness、blocking reasons、候选组、模拟翻转和 blocked actions。

验证结果：

- 任务 4 全量：`1442 passed, 6 skipped, 139 warnings in 107.66s`。
- 任务 5 全量：`1445 passed, 6 skipped, 139 warnings in 108.98s`。
- 任务 6 WebUI build：`npm --prefix webui run build` 退出码 0，仅有 Vite chunk size / plugin timing 警告。
- 任务 6 全量：`1446 passed, 6 skipped, 139 warnings in 107.54s`。

执行边界：

- 不自动应用参数，不更新 `evals/baselines/timing_gate.json`。
- 不修改 `core/timing_score.py` 默认参数，不改变 PR gate 或周期 gate。
- Admin API 和 WebUI 均不提供「应用参数」或「更新 baseline」入口。
- `ready=false` 是合法输出，用于表达 artifact、truth、候选参数或 baseline 证据不足。

## 已完成阶段详情：TimingGate 信号周期审计

状态：已完成。设计文档为 `docs/superpowers/specs/2026-06-20-timing-signal-audit-periodic-design.md`，设计提交为 `8c7a563 docs(评测): 设计时机信号周期审计`；实现计划为 `.Codex/plans/timing-signal-audit-periodic.md`，计划提交为 `979639e docs(计划): 记录时机信号周期审计计划`；实现提交为 `0980f22 ci(评测): 接入时机信号周期审计`。

目标：

- 新增 `scripts/run_timing_signal_audit_periodic.sh`，复用现有 `evals.timing_signal_audit` CLI。
- 通过 `TIMING_SIGNAL_AUDIT_DB`、`TIMING_SIGNAL_AUDIT_OUT`、`TIMING_SIGNAL_AUDIT_LIMIT`、`TIMING_SIGNAL_AUDIT_AFTER_ID` 和 `TIMING_SIGNAL_AUDIT_SIGNALS` 控制周期审计。
- 缺少真实 SQLite DB 时写出 `source.mode=skipped`、`source.reason=db_not_found` 的空报告并退出 0。
- 将审计脚本接入 `scripts/run_eval_periodic.sh` keep-going 流程，让现有 `evals/reports/*.json` artifact 规则归档报告。

验证结果：

- 红灯：`tests/test_timing_signal_audit_periodic.py` 初次运行结果 `2 failed, 1 warning`，分别失败于脚本不存在和周期入口未引用脚本。
- 中间红灯：新增脚本后结果 `1 failed, 1 passed, 1 warning`，缺库 skipped 行为已通过，周期入口仍未接入。
- 定向绿灯：`python -B -m pytest tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider` 结果 `2 passed, 1 warning in 0.70s`。
- 相邻回归：`tests/test_timing_signal_audit.py tests/test_eval_baseline.py` 结果 `24 passed, 1 warning in 1.67s`。
- 独立脚本缺库验证：`TIMING_SIGNAL_AUDIT_DB=tmp/missing-timing-audit.db TIMING_SIGNAL_AUDIT_OUT=tmp/timing_signal_audit_latest.json bash scripts/run_timing_signal_audit_periodic.sh` 输出 skipped 报告，退出码为 0。
- 周期脚本：`bash scripts/run_eval_periodic.sh` 中 eval guard、TimingGate、三个 capability gate、RAG gate 和 `timing signal audit` 均通过；当前环境默认 `data/nanobot.db` 存在，审计输出 `samples=0 mismatch=0 out=evals/reports/timing_signal_audit_latest.json`。
- 全量回归：`python -B -m pytest tests/ -q -p no:cacheprovider` 结果 `1382 passed, 6 skipped, 139 warnings in 107.45s`。

执行边界：

- 不修改 TimingGate scoring 公式、阈值或 runtime 决策。
- 不新增生产 DB schema。
- 不把真实样本审计纳入 PR fail-fast gate。
- 不实现人工仲裁、标注 UI 或候选队列统一。

## 已完成阶段详情：TimingSignal 不可变 Artifact 加厚

状态：已完成代码落地并通过最终验证。设计文档为 `docs/superpowers/specs/2026-06-20-timing-signal-immutable-artifacts-design.md`，设计提交为 `712cb0f docs(评测): 设计时机信号不可变报告`；实现计划为 `.Codex/plans/timing-signal-immutable-artifacts.md`，计划提交为 `59d7e60 docs(计划): 记录时机信号不可变报告计划`。审计脚本复制已随 `ca2a90c fix(评测): 复制时机信号审计报告` 落地，周期入口索引已随 `df78dfd ci(评测): 索引时机信号不可变报告` 落地，workflow 归档已随 `bad632b ci(评测): 归档时机信号运行报告` 落地，运行级报告忽略规则已随 `95c88fe chore(评测): 忽略运行级评测报告` 落地。

目标：

- 保留 `evals/reports/timing_signal_audit_latest.json` 作为兼容入口。
- 同轮写出 `evals/reports/YYYY-MM-DD-timing_signal_audit.json`。
- 同轮写出 `evals/reports/runs/<run_id>/timing_signal_audit.json`。
- 周期 manifest 的 TimingSignal step 按 run-scoped、dated、latest 顺序索引报告。
- workflow artifact 上传 `evals/reports/runs/**/timing_signal_audit.json`。
- 本地忽略 `evals/reports/runs/`，避免周期 smoke 产物污染 git 状态。

验证结果：

- 审计脚本红灯：`tests/test_timing_signal_audit_periodic.py::test_timing_signal_audit_periodic_script_skips_missing_db` 结果 `1 failed, 1 warning in 6.18s`，失败点为额外 dated / run-scoped 输出不存在。
- 审计脚本绿灯：同一单条测试 `1 passed, 1 warning in 0.84s`；文件回归 `tests/test_timing_signal_audit_periodic.py` 结果 `3 passed, 1 warning in 0.74s`。
- 周期入口红灯：`test_eval_periodic_script_indexes_immutable_timing_signal_audit_reports` 结果 `1 failed, 1 warning in 6.03s`，失败点为缺少三类 TimingSignal audit 路径变量。
- 周期入口绿灯：同一单条测试 `1 passed, 1 warning in 0.84s`；相邻回归 `tests/test_eval_baseline.py tests/test_timing_signal_audit_periodic.py` 结果 `26 passed, 1 warning in 1.83s`。
- Workflow 红灯：`test_eval_workflow_uploads_run_scoped_timing_signal_audit` 结果 `1 failed, 1 warning in 6.05s`，失败点为 artifact glob 缺少 run-scoped TimingSignal audit。
- Workflow 绿灯：同一单条测试 `1 passed, 1 warning in 0.48s`；`tests/test_eval_baseline.py` 结果 `24 passed, 1 warning in 1.00s`。
- 忽略规则红灯：`test_eval_run_scoped_reports_are_gitignored` 结果 `1 failed, 1 warning in 6.22s`，失败点为 `.gitignore` 缺少 `evals/reports/runs/`。
- 忽略规则绿灯：同一单条测试 `1 passed, 1 warning in 0.52s`；`tests/test_eval_baseline.py` 结果 `25 passed, 1 warning in 1.04s`。
- 文档扫描：红旗词扫描无输出，U+FFFD 扫描无输出，`git diff --check` 无输出。
- 定向回归：`tests/test_timing_signal_audit_periodic.py tests/test_eval_baseline.py tests/test_periodic_tuning_analysis.py tests/test_eval_artifact_trends.py` 结果 `42 passed, 1 warning in 2.65s`。
- 周期脚本 smoke：`TIMING_SIGNAL_AUDIT_DB=tmp/missing-timing-audit.db PERIODIC_RUN_ID=immutable_artifact_smoke bash scripts/run_eval_periodic.sh` 退出码 0；内部 eval guard `32 passed, 1 warning in 1.90s`；所有子 gate 通过；TimingSignal audit 写出 latest、dated 和 run-scoped 三类 skipped 报告。
- Smoke JSON 校验：三类 TimingSignal audit payload 完全一致，`source.mode=skipped`；run-scoped manifest 的 TimingSignal step `report_paths` 顺序为 run-scoped、dated、latest。
- 全量回归：`python -B -m pytest tests/ -q -p no:cacheprovider` 结果 `1431 passed, 6 skipped, 139 warnings in 106.38s`。

执行边界：

- 不自动调整 TimingGate 参数。
- 不生成可执行调参 proposal。
- 不更新 baseline。
- 不改变 PR gate 或周期 gate 的通过条件。

## 已完成阶段详情：RAG generated → manual 仲裁入口

状态：已完成。设计文档为 `docs/superpowers/specs/2026-06-20-rag-generated-manual-promotion-design.md`，设计提交为 `e0d537d docs(评测): 设计 RAG 样本仲裁入口`；实现计划为 `.Codex/plans/rag-generated-manual-promotion.md`，计划提交为 `bd51e31 docs(计划): 记录 RAG 样本仲裁计划`。后端 API 已随 `6567c99 feat(评测): 支持 RAG 样本提升接口` 落地，WebUI 入口已随 `7dfdce7 feat(评测): 增加 RAG 样本仲裁入口` 落地。

目标：

- 新增 `POST /api/v1/admin/rag/benchmark/cases/{case_id}/promote-manual`。
- 支持 dry-run 预检目标 `target_case_id`、目标 path 和转换后的 manual case JSON。
- apply 时写入 manual case，覆盖已有 manual 时先写 backup。
- generated case stale 时返回 `409`，manual 源调用返回 `409`，unsafe target id 返回 `400`。
- apply 写入 `promote_rag_benchmark_generated_case` audit。
- WebUI generated case 详情页提供「提升为 Manual」入口，先 dry-run，再确认 apply。
- 不自动更新 `evals/baselines/rag_benchmark.json`，不提交 `tmp/rag_benchmark/generated/*`。

计划项：

- [x] 设计：写入 `docs/superpowers/specs/2026-06-20-rag-generated-manual-promotion-design.md`。提交：`e0d537d docs(评测): 设计 RAG 样本仲裁入口`。
- [x] 实现计划：写入 `.Codex/plans/rag-generated-manual-promotion.md`。提交：`bd51e31 docs(计划): 记录 RAG 样本仲裁计划`。
- [x] 后端 API 与测试：新增 request model、case 查找、stale 校验、转换、写入和 audit。提交：`6567c99 feat(评测): 支持 RAG 样本提升接口`。
- [x] WebUI 仲裁入口：generated case 详情页 dry-run/apply 二阶段 UI、stale/manual 不可写禁用提示和静态守卫。提交：`7dfdce7 feat(评测): 增加 RAG 样本仲裁入口`。
- [x] 文档收口：同步 `docs/evals.md`、本计划和 walkthrough，完成最终验证。

验证结果：

- 后端红灯：新增 promote 测试初次运行结果 `3 failed, 21 warnings in 7.25s`，失败原因是接口未实现，返回 `405 Method Not Allowed`。
- 后端绿灯：同一新增测试命令结果 `3 passed, 21 warnings in 2.15s`。
- 后端相邻回归：`tests/test_rag_benchmark_admin.py` 结果 `15 passed, 21 warnings in 6.58s`。
- WebUI 红灯：worker 运行 `tests/test_rag_benchmark_webui.py -v` 结果 `1 failed, 4 passed, 1 warning`，失败点是缺少「提升为 Manual」。
- WebUI 静态绿灯：`tests/test_rag_benchmark_webui.py` 结果 `5 passed, 1 warning in 0.74s`。
- 实现阶段定向验证：`tests/test_rag_benchmark_admin.py tests/test_rag_benchmark_webui.py` 结果 `20 passed, 21 warnings in 7.10s`。
- RAG 相邻回归：`tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `40 passed, 1 warning in 2.26s`。
- WebUI build：`npm --prefix webui run build` 退出码 0，仅有现有 Vite chunk size 和 plugin timing warning。
- 全量回归：`python -B -m pytest tests/ -q -p no:cacheprovider` 结果 `1386 passed, 6 skipped, 139 warnings in 103.04s`。

执行边界：

- 不修改 RAG sampler、runner、scoring、fixtures、baseline 或 gate 脚本。
- Promote 只创建 manual case，不代表样本已进入稳定 baseline。
- 后续若要批量仲裁、reject/defer、队列摘要或趋势统计，另起阶段处理。
- 下一步优先推进通用 EvalCandidate 候选晋升资格、队列摘要和批量预检规则。

## 已完成阶段详情：EvalCandidate 运营规则

状态：已完成代码落地。设计文档为 `docs/superpowers/specs/2026-06-20-eval-candidate-operations-design.md`，设计提交为 `8dc41f5 docs(评测): 设计候选运营规则`；实现计划为 `.Codex/plans/eval-candidate-operations.md`，计划提交为 `aed5333 docs(计划): 记录候选运营规则计划`。后端 readiness / summary 和状态约束已随 `cbcc399 feat(评测): 增加候选晋升资格` 落地，批量 preflight 与 CLI 聚合 dry-run 已随 `37ab830 feat(评测): 支持候选批量预检` 落地，WebUI 当前页预检已随 `376bffe feat(评测): 展示候选运营预检` 落地。

目标：

- 每条 `EvalCandidate` 返回 `readiness`，解释是否可晋升以及阻断原因。
- `GET /api/v1/admin/evals/candidates` 返回队列 `summary`。
- 新增 `POST /api/v1/admin/evals/candidates/preflight`，支持只读批量预检 ready / blocked。
- CLI `python -m evals.candidates promote --dry-run` 输出 ready / blocked 聚合结果。
- PATCH 不能直接写入 `labeled` 或 `promoted`，标注和晋升必须走专用接口。
- 不可运行 suite（例如 `error`）不能晋升为正式 eval case。

计划项：

- [x] 设计：写入 `docs/superpowers/specs/2026-06-20-eval-candidate-operations-design.md`。提交：`8dc41f5 docs(评测): 设计候选运营规则`。
- [x] 实现计划：写入 `.Codex/plans/eval-candidate-operations.md`。提交：`aed5333 docs(计划): 记录候选运营规则计划`。
- [x] 后端 readiness / summary / 状态约束：新增派生 readiness、summary、priority 排序和状态机约束。提交：`cbcc399 feat(评测): 增加候选晋升资格`。
- [x] 批量 preflight / CLI：新增只读 preflight API，CLI dry-run 聚合 ready / blocked，apply 遇 blocked 不做部分写入。提交：`37ab830 feat(评测): 支持候选批量预检`。
- [x] WebUI 运营预检：候选页展示 summary、资格列、blocked 原因、详情 readiness 和「预检当前页」。提交：`376bffe feat(评测): 展示候选运营预检`。
- [x] 文档收口：同步 `docs/evals.md`、本文件和实现计划，完成最终验证。

验证结果：

- 后端 readiness 红灯：新增 4 个测试初次运行结果 `4 failed, 21 warnings`，失败点分别是缺少 `candidate_readiness`、列表缺少 `summary`、PATCH 仍返回 200、`error` suite 可晋升。
- 后端 readiness 绿灯：同一命令结果 `4 passed, 21 warnings in 1.76s`。
- 后端相邻回归：`python -B -m pytest tests/test_eval_candidate_contract.py -q -p no:cacheprovider` 结果 `25 passed, 21 warnings in 3.47s`。
- Preflight 红灯：`test_eval_candidates_preflight_returns_ready_and_blocked_items` 初次运行结果 `1 failed, 21 warnings`，失败点是 `405 Method Not Allowed`。
- Preflight 绿灯：同一测试结果 `1 passed, 21 warnings in 1.21s`。
- 后端 + CLI 集成：`python -B -m pytest tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py -q -p no:cacheprovider` 结果 `32 passed, 21 warnings in 4.20s`。
- WebUI 静态测试：`python -B -m pytest tests/test_webui_admin_redesign.py -q -p no:cacheprovider` 结果 `19 passed, 1 warning in 0.93s`。
- WebUI build：`npm --prefix webui run build` 退出码 0，仅有现有 Vite chunk size 和 plugin timing warning。

执行边界：

- 不修改 `EvalCandidate` 数据库 schema。
- 不实现批量 apply。
- 不强制 `target_dataset == suite`，仅校验 dataset 名称安全和目标文件冲突。
- 不重写 WebUI 标注表单；未定制 suite 继续使用高级 JSON 标注模式。

## 已完成阶段详情：EvalCandidate 候选仲裁状态

状态：已完成代码落地。设计文档为 `docs/superpowers/specs/2026-06-20-eval-candidate-triage-design.md`，设计提交为 `d53ba55 docs(评测): 设计候选仲裁状态`；实现计划为 `.Codex/plans/eval-candidate-triage.md`，计划提交为 `343ccef docs(计划): 记录候选仲裁计划`。后端状态机与 Admin API 已随 `7859ac8 feat(评测): 增加候选仲裁状态` 落地，CLI / WebUI 仲裁入口已随 `cf9bddc feat(评测): 增加候选仲裁入口` 落地。

目标：

- 新增 `rejected` 与 `deferred` 两个通用 `EvalCandidate` 运营状态。
- 新增 `POST /api/v1/admin/evals/candidates/{case_id}/reject`、`/defer` 和 `/reopen`。
- 每个动作写入统一 Admin audit detail：`before_status`、`after_status`、`reason_code`、`note` 和 `defer_until`。
- 收紧 `label_candidate()` 与 `ignore_candidate()` 的来源状态，避免绕过显式状态机。
- WebUI 候选页支持状态筛选、单条「暂缓」「拒绝」「复开」和原因码 modal。
- CLI 保持现有 `export --status` 形态，可导出 `deferred` 与 `rejected` 候选。

计划项：

- [x] 设计：写入 `docs/superpowers/specs/2026-06-20-eval-candidate-triage-design.md`。提交：`d53ba55 docs(评测): 设计候选仲裁状态`。
- [x] 实现计划：写入 `.Codex/plans/eval-candidate-triage.md`。提交：`343ccef docs(计划): 记录候选仲裁计划`。
- [x] 后端状态机与 Admin API：新增 store triage 函数、显式动作端点和审计 payload。提交：`7859ac8 feat(评测): 增加候选仲裁状态`。
- [x] CLI / WebUI 入口：新增新状态导出守卫、WebUI 单条仲裁入口和 dist bundle。提交：`cf9bddc feat(评测): 增加候选仲裁入口`。
- [x] 文档收口：同步 `docs/evals.md`、`docs/todo.md`、本文件和实现计划，完成最终验证。

验证结果：

- 后端红灯：新增 3 个测试初次运行结果 `3 failed, 21 warnings in 6.45s`，失败点为缺少 `reject_candidate` / `defer_candidate` / `reopen_candidate` 和 API 返回 `405 Method Not Allowed`。
- 后端定向绿灯：同一命令结果 `3 passed, 21 warnings in 1.29s`。
- 后端相邻回归：`python -B -m pytest tests/test_eval_candidate_contract.py -q -p no:cacheprovider` 结果 `29 passed, 21 warnings in 3.92s`。
- CLI / WebUI 红灯：新增定向测试初次运行结果 `1 failed, 1 passed, 1 warning in 6.23s`，失败点为 WebUI 缺少 `deferred` / `rejected` 状态筛选和仲裁入口。
- CLI / WebUI 定向绿灯：同一命令结果 `2 passed, 1 warning in 0.94s`。
- CLI / WebUI 相邻回归：`python -B -m pytest tests/test_eval_candidates_cli.py tests/test_webui_admin_redesign.py -q -p no:cacheprovider` 结果 `27 passed, 1 warning in 1.75s`。
- WebUI build：`npm --prefix webui run build` 退出码 0，仅有现有 Vite chunk size 和 plugin timing warning。
- 最终组合回归：`python -B -m pytest tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py tests/test_webui_admin_redesign.py -q -p no:cacheprovider` 结果 `56 passed, 21 warnings in 5.89s`。
- 全量回归：`python -B -m pytest tests/ -q -p no:cacheprovider` 结果 `1399 passed, 6 skipped, 139 warnings in 106.06s`。

执行边界：

- 不修改 `EvalCandidate` 数据库 schema。
- 不实现批量 reject / defer / reopen。
- 不修改 readiness、summary、preflight 或 promote 规则。
- 不把 RAG generated / manual case 并入通用 `EvalCandidate`。
- 不自动更新 baseline，不在本阶段做趋势报表或 TimingGate 阈值调参。
- 后续真实样本趋势报表、周期运行 manifest、跨 artifact 周期趋势、周期趋势只读调参分析和 TimingSignal 不可变 artifact 加厚均已完成；TimingGate 调参提案 record-only 审核运营链路也已完成，后续重点转为真实报告生成和人工复核运营。

## 已完成阶段详情：EvalCandidate 人工仲裁批次审计

状态：已完成代码落地。设计文档为 `docs/superpowers/specs/2026-06-20-eval-candidate-batch-audit-design.md`，设计提交为 `f95e67e docs(评测): 设计候选批次审计`；实现计划为 `.Codex/plans/eval-candidate-batch-audit.md`，计划提交为 `ba79917 docs(计划): 记录候选批次审计计划`。后端批次审计 API 已随 `c5eded7 feat(评测): 增加候选批次审计接口` 落地，CLI / WebUI 只读入口已随 `97b0ab8 feat(评测): 增加候选批次审计入口` 落地。

目标：

- 新增 `POST /api/v1/admin/evals/candidates/batch-audit`。
- `dry_run=true` 只生成候选批次快照，不写审计日志，不修改候选状态。
- `dry_run=false` 重新生成计划，`ok=true` 时写入一条 `AdminAuditLog`，`action=audit_eval_candidate_batch`。
- 批次审计 detail 记录 `filters`、`batch_note`、`counts`、`items` 和 readiness 阻断原因。
- CLI 新增 `python -m evals.candidates audit`，可导出只读 JSON 报告。
- WebUI 候选页新增「批次审计」弹窗，复用当前页 preflight 展示 `counts`、`top_blocking_reasons` 和 `items`。

计划项：

- [x] 设计：写入 `docs/superpowers/specs/2026-06-20-eval-candidate-batch-audit-design.md`。提交：`f95e67e docs(评测): 设计候选批次审计`。
- [x] 实现计划：写入 `.Codex/plans/eval-candidate-batch-audit.md`。提交：`ba79917 docs(计划): 记录候选批次审计计划`。
- [x] 后端批次审计 API：新增 store plan / record 函数、Admin API 和审计落库测试。提交：`c5eded7 feat(评测): 增加候选批次审计接口`。
- [x] CLI / WebUI 入口：新增 CLI `audit` 子命令、WebUI 当前页只读弹窗和 dist bundle。提交：`97b0ab8 feat(评测): 增加候选批次审计入口`。
- [x] 文档收口：同步 `docs/evals.md`、`docs/todo.md`、本文件和实现计划，完成最终验证。

验证结果：

- 后端红灯：新增 3 个测试初次运行结果 `3 failed, 21 warnings in 7.00s`，失败点为 `/batch-audit` 返回 `405 Method Not Allowed`。
- 后端定向绿灯：同一命令结果 `3 passed, 21 warnings in 1.89s`。
- 后端相邻回归：`python -B -m pytest tests/test_eval_candidate_contract.py -q -p no:cacheprovider` 结果 `32 passed, 21 warnings in 4.86s`。
- CLI / WebUI 红灯：新增定向测试初次运行结果 `2 failed, 1 warning in 6.39s`，失败点为 CLI 缺少 `audit` 子命令、WebUI 缺少「批次审计」入口。
- CLI / WebUI 定向绿灯：同一命令结果 `2 passed, 1 warning in 0.87s`。
- CLI / WebUI 相邻回归：`python -B -m pytest tests/test_eval_candidates_cli.py tests/test_webui_admin_redesign.py -q -p no:cacheprovider` 结果 `29 passed, 1 warning in 1.66s`。
- WebUI build：`npm --prefix webui run build` 退出码 0，仅有现有 Vite chunk size 和 plugin timing warning。
- 最终组合回归：`python -B -m pytest tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py tests/test_webui_admin_redesign.py -q -p no:cacheprovider` 结果 `61 passed, 21 warnings in 6.24s`。
- 全量回归：`python -B -m pytest tests/ -q -p no:cacheprovider` 结果 `1404 passed, 6 skipped, 139 warnings in 109.54s`。

执行边界：

- 不修改 `EvalCandidate` 数据库 schema。
- 不实现批量 reject / defer / reopen / label / promote。
- 审计 decision 只是人工结论，不触发状态流转。
- 不自动更新 baseline，不在本阶段做趋势报表或 TimingGate 阈值调参。
- 不把 RAG generated / manual case 并入通用 `EvalCandidate`。
- 后续真实样本趋势报表、周期运行 manifest、跨 artifact 周期趋势、周期趋势只读调参分析和 TimingSignal 不可变 artifact 加厚均已完成；TimingGate 调参提案 record-only 审核运营链路也已完成，后续重点转为真实报告生成和人工复核运营。

## 已完成阶段详情：EvalCandidate 运营趋势报表

状态：已完成代码落地。设计文档为 `docs/superpowers/specs/2026-06-20-eval-operations-trend-report-design.md`，设计提交为 `2341da8 docs(评测): 设计运营趋势报表`；实现计划为 `.Codex/plans/eval-operations-trend-report.md`，计划提交为 `ced6f26 docs(计划): 记录候选趋势计划`。后端趋势 API 已随 `0f2f89e feat(评测): 增加候选趋势接口` 落地，CLI 趋势导出已随 `752513d feat(评测): 增加候选趋势导出` 落地，WebUI 趋势报表入口已随 `2f63ec7 feat(评测): 展示候选趋势报表` 落地。

目标：

- 新增 `candidate_trend_report()`，按 `EvalCandidate.created_at` 做日粒度分桶。
- 新增 `GET /api/v1/admin/evals/candidates/trend`，支持 `days`、`suite`、`status`、`source` 和 `target_dataset`。
- 新增 `python -m evals.candidates trend`，可导出只读 JSON 报告。
- WebUI「Eval 评测」页新增「趋势报表」tab，展示 summary、日期桶和完整 payload。
- 明确趋势报表是“按创建日期分桶 + 当前状态快照”，不代表历史状态迁移。

计划项：

- [x] 设计：写入 `docs/superpowers/specs/2026-06-20-eval-operations-trend-report-design.md`。提交：`2341da8 docs(评测): 设计运营趋势报表`。
- [x] 实现计划：写入 `.Codex/plans/eval-operations-trend-report.md`。提交：`ced6f26 docs(计划): 记录候选趋势计划`。
- [x] 后端趋势聚合与 Admin API：新增 store 聚合、只读 API 和契约测试。提交：`0f2f89e feat(评测): 增加候选趋势接口`。
- [x] CLI 趋势导出：新增 `trend` 子命令和只读导出测试。提交：`752513d feat(评测): 增加候选趋势导出`。
- [x] WebUI 趋势报表入口：新增「趋势报表」tab、静态守卫和 dist bundle。提交：`2f63ec7 feat(评测): 展示候选趋势报表`。
- [x] 文档收口：同步 `docs/evals.md`、`docs/todo.md`、本文件和实现计划，完成最终验证。

验证结果：

- 后端红灯：新增 2 个测试初次运行结果 `2 failed, 21 warnings in 6.19s`，失败点为缺少 `candidate_trend_report` 和 `/trend` 被动态路由吞成 `candidate not found`。
- 后端定向绿灯：同一命令结果 `2 passed, 21 warnings in 1.27s`。
- 后端相邻回归：`python -B -m pytest tests/test_eval_candidate_contract.py -q -p no:cacheprovider` 结果 `34 passed, 21 warnings in 5.10s`。
- CLI 红灯：新增定向测试初次运行结果 `1 failed, 1 warning in 6.04s`，失败点为 `invalid choice: 'trend'`。
- CLI 绿灯：同一命令结果 `1 passed, 1 warning in 0.87s`。
- CLI 相邻回归：`python -B -m pytest tests/test_eval_candidates_cli.py -q -p no:cacheprovider` 结果 `9 passed, 1 warning in 1.12s`。
- WebUI 红灯：新增静态测试初次运行结果 `1 failed, 1 warning in 6.10s`，失败点为缺少「趋势报表」。
- WebUI 绿灯：同一命令结果 `1 passed, 1 warning in 0.80s`。
- WebUI 相邻回归：`python -B -m pytest tests/test_webui_admin_redesign.py -q -p no:cacheprovider` 结果 `22 passed, 1 warning in 0.97s`。
- WebUI build：`npm --prefix webui run build` 退出码 0，仅有现有 Vite chunk size 和 plugin timing warning。
- 最终组合回归：`python -B -m pytest tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py tests/test_webui_admin_redesign.py -q -p no:cacheprovider` 结果 `65 passed, 21 warnings in 6.52s`。
- 全量回归：`python -B -m pytest tests/ -q -p no:cacheprovider` 结果 `1408 passed, 6 skipped, 139 warnings in 108.30s`。

执行边界：

- 不修改 `EvalCandidate` 数据库 schema。
- 不写 `AdminAuditLog`，不修改候选状态。
- 不实现批量 reject / defer / reopen / label / promote。
- 不自动更新 baseline，不做 TimingGate、RAG 或 capability gate 阈值调参。
- 不把 RAG generated / manual case 并入通用 `EvalCandidate`。
- 跨 artifact 趋势另起设计，基于周期运行 manifest 再解析通用 eval、RAG benchmark 和 TimingSignal JSON。

## 已完成阶段详情：周期运行 manifest

状态：已完成代码落地。设计文档为 `docs/superpowers/specs/2026-06-20-eval-periodic-manifest-design.md`，实现计划为 `.Codex/plans/eval-periodic-manifest.md`，设计与计划提交为 `7e17125 docs(评测): 设计周期运行清单`。manifest helper 已随 `a4660c1 feat(评测): 构建周期运行清单` 落地，周期脚本接入已随 `f459acc ci(评测): 输出周期运行清单` 落地。

目标：

- 新增 `evals.periodic_manifest`，从步骤 JSONL 和报告文件构建周期运行 manifest。
- `scripts/run_eval_periodic.sh` 保持 keep-going 语义，但为每个步骤记录 `kind`、`suite`、退出码、baseline 和报告路径。
- 周期脚本结束前写出 `evals/reports/periodic_manifest_latest.json`、`evals/reports/YYYY-MM-DD-periodic_manifest.json` 和 `evals/reports/runs/<run_id>/manifest.json`。
- manifest 索引通用 eval、RAG benchmark 和 TimingGate signal audit 的步骤状态、报告路径和摘要指标。
- workflow artifact 上传 manifest 文件，便于按一次周期运行回溯报告。

计划项：

- [x] 设计与计划：写入 `docs/superpowers/specs/2026-06-20-eval-periodic-manifest-design.md` 和 `.Codex/plans/eval-periodic-manifest.md`。提交：`7e17125 docs(评测): 设计周期运行清单`。
- [x] Manifest helper：新增 `evals/periodic_manifest.py` 和 helper 契约测试。提交：`a4660c1 feat(评测): 构建周期运行清单`。
- [x] 周期脚本接入：`scripts/run_eval_periodic.sh` 写 steps JSONL 并调用 manifest helper。提交：`f459acc ci(评测): 输出周期运行清单`。
- [x] Workflow 与文档收口：artifact 追加 manifest glob，同步 `docs/evals.md`、`docs/todo.md`、本文件和实现计划。

验证结果：

- Helper 红灯：新增测试初次运行结果 `1 failed, 1 warning in 5.88s`，失败点为缺少 `evals.periodic_manifest`。
- Helper 绿灯：同一命令结果 `1 passed, 1 warning in 0.83s`。
- Helper 相邻回归：`python -B -m pytest tests/test_eval_baseline.py -q -p no:cacheprovider` 结果 `20 passed, 1 warning in 1.23s`。
- 周期脚本红灯：新增 2 个测试初次运行结果 `2 failed, 1 warning in 5.92s`，失败点为缺少 `PERIODIC_RUN_ID`、`record_step` 和 TimingSignal 报告路径。
- 周期脚本绿灯：同一命令结果 `2 passed, 1 warning in 0.81s`。
- 周期脚本验证：`bash scripts/run_eval_periodic.sh` 退出码 0，内部 eval guard `29 passed, 1 warning in 1.84s`，所有 gate passed，manifest 断言输出 `20260620_204359_local passed 7`。
- 周期脚本相邻回归：`python -B -m pytest tests/test_eval_baseline.py tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider` 结果 `24 passed, 1 warning in 1.71s`。
- Workflow 红灯：新增测试初次运行结果 `1 failed, 1 warning in 5.98s`，失败点为 workflow 缺少 `periodic_manifest` artifact glob。
- Workflow 绿灯：同一命令结果 `1 passed, 1 warning in 0.77s`。
- 最终定向回归：`python -B -m pytest tests/test_eval_baseline.py tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider` 结果 `25 passed, 1 warning in 2.21s`。
- 最终周期脚本：`bash scripts/run_eval_periodic.sh` 退出码 0，内部 eval guard `30 passed, 1 warning in 1.86s`，所有 gate passed，并写出 `periodic_manifest=evals/reports/periodic_manifest_latest.json`。
- 全量回归：`python -B -m pytest tests/ -q -p no:cacheprovider` 结果 `1412 passed, 6 skipped, 139 warnings in 109.97s`。

执行边界：

- 不改 `run_eval_pr_gate.sh` 的 fail-fast 语义。
- 不修改 `evals/baselines/*.json`。
- 不做 TimingGate、RAG 或 capability gate 阈值调参。
- 不新增 Admin API 或 WebUI 页面。
- 不解析历史 artifact 做趋势图。
- 不批量重写既有历史报告。

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
- 真实样本运营 1-10 已完成；TimingGate 调参提案 record-only 审核运营链路也已完成，后续重点转为真实报告生成和人工复核运营。

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
- 任务 2 文档自检：模板词扫描无匹配，U+FFFD 扫描无匹配，`git diff --check` 无输出。
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
- 任务 4 文档自检：异常字符扫描无匹配，diff 模板词扫描无匹配，`git diff --check` 无输出。

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
- 任务 3 文档自检：模板词扫描无匹配，U+FFFD 扫描通过，`git diff --check` 无输出。
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
- 任务 3 文档自检：模板词扫描无匹配，U+FFFD 扫描通过，`git diff --check` 无输出。
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
- 任务 3 文档自检：模板词扫描无匹配，U+FFFD 扫描通过，`git diff --check` 无输出。
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

- 设计阶段：设计文档模板词扫描、U+FFFD 扫描、`git diff --check`。
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

- 计划阶段：文档模板词扫描、U+FFFD 扫描、`git diff --check`。
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
- 提交前轻量检查：文档模板词扫描无输出；U+FFFD 扫描无输出；本阶段文件 `git diff --check` 无输出。
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

- P3-3 文档阶段：`git diff --check`、文档模板词扫描、U+FFFD 扫描。
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

- 计划阶段：`git diff --check`、模板词扫描和 U+FFFD 扫描。
- 定向回归：`tests/test_eval_candidate_contract.py`、`tests/test_eval_candidates_cli.py`、`tests/test_eval_baseline.py`、`tests/test_timing_gate_prompt_policy.py`。
- 门禁：`bash scripts/run_timing_gate_gate.sh` 和 `python -B -m evals.run --suite capability_model_routing --baseline evals/baselines/capability_model_routing.json --min-pass-rate 1.0 --max-new-failures 0`。
- 最终回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider`。

已完成验证摘要：

- 任务 1：新增 expected 契约红灯覆盖后通过 `tests/test_eval_candidate_contract.py`，`regression` eval 结果为 `total=11 passed=11 failed=0`，全量回归为 `1323 passed, 6 skipped`。
- 任务 2：候选标注契约和 WebUI 静态守卫均通过，定向回归为 `6 passed, 4 deselected`，全量回归为 `1327 passed, 6 skipped`。
- 任务 3：promote dry-run / `target_dataset` / Admin dry-run 覆盖通过，定向回归为 `10 passed`，全量回归为 `1329 passed, 6 skipped`。
- 任务 4：候选 CLI 覆盖 export、import-labels、promote dry-run / apply 和 CLI main，定向回归为 `7 passed`，全量回归为 `1337 passed, 6 skipped`。
- 任务 5：`capability_model_routing` suite 和 baseline gate 均通过，`tests/test_eval_baseline.py` 为 `11 passed`，全量回归为 `1338 passed, 6 skipped`。
- 任务 6：文档模板词扫描、U+FFFD 扫描和 `git diff --check` 均无错误输出；评测定向组合为 `33 passed, 21 warnings`，TimingGate 与 `capability_model_routing` gate 均输出 `Gate passed`，全量回归为 `1338 passed, 6 skipped, 139 warnings`。
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

- 设计文档模板词扫描：`docs/superpowers/specs/2026-06-18-qq-outbound-rendering-contract-design.md`，结果无输出，退出码 0。
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
- 文档模板词扫描：`docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/client-meta-boundary-validation.md docs/superpowers/specs/2026-06-18-client-meta-boundary-validation-design.md`，结果无输出，退出码 0。
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
- 任务 6 文档模板词扫描：`docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/message-envelope.md`，结果无输出，退出码 0。
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
- 任务 6 文档扫描：过时模板词扫描无输出；`git diff --check -- docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/tool-platform-scope.md` 无输出。
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
- P1-6 任务 6 首轮定向：`1 failed, 53 passed, 1 warning`，失败点为 V2 `timing_gate` task 模板仍是模板内容。
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
- [x] 运行计划模板词扫描和 `git diff --check`
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

TimingGate `s_bot` live path 收口、私聊 fallback 置信度收口、P4-5E knowledge fixture citation 正例、P4-5F sticker fixture sendable 正例、P4-5G group_memory fixture 正例、P4-5H RAG 过滤约束 fixture、真实样本运营 1-10，以及 TimingGate 调参提案 record-only 审核运营链路均已完成。默认下一步是用真实 run-scoped audit、final action truth 和候选参数文件持续生成 proposal，并通过 record-only 审核沉淀人工结论。

TimingGate 真实日志标注和周期复跑报告调参属于延续项，不抢占当前默认执行顺序。Prompt V2、P2-4、P3-1、P4-5D、P4-5E、P4-5F、P4-5G、P4-5H、真实样本运营 1-10 和 TimingGate 调参提案 record-only 审核运营链路均已完成，历史章节中保留的旧阶段说明仅作为执行记录，不再作为下一步来源。

## 2026-06-21 H29 handle_message 拆分计划

状态：H29 第一轮拆分已完成。`NanobotBridge.handle_message()` 的外部签名、`metadata` 开放 dict、`stream_queue` 侧通道、空字符串语义和 `pop_last_reply_meta(session_id)` 弹出式语义保持不变；内部已按 request 准备、模型循环、reply contract 和 trace finalizer 拆出私有边界。

已完成：

- [x] 读取 `docs/todo.md` H29 条目，确认目标是拆分 `nanobot_kt/bridge.py:866-1947` 的巨型 `handle_message()`。
- [x] 分派 3 个只读 explorer，分别审计职责切片、测试覆盖和外部调用契约。
- [x] 汇总子 agent 结论，选择「模块内小步抽 helper」方案，避免第一阶段破坏 `nanobot_kt.bridge.NewAPIClient`、`registry`、`AsyncOpenAI` 等 monkeypatch 路径。
- [x] 写入设计文档：`docs/superpowers/specs/2026-06-21-h29-handle-message-refactor-design.md`。
- [x] 设计阶段验证：模板词扫描无输出，`git diff --check` 退出码 0，`python -m pytest tests/ -v` 结果 `1461 passed, 6 skipped, 139 warnings in 108.74s`。
- [x] 设计阶段提交：`e6cd2b5 docs(桥接): 设计消息处理拆分`。
- [x] 写入实现计划：`.Codex/plans/h29-handle-message-refactor.md`，提交 `a9f0dbb docs(计划): 记录消息处理拆分计划`。

H29 计划列表：

- [x] 阶段 0：只读审计、方案选择和设计文档。
- [x] 阶段 1：抽低风险 helper，包括 output 初始化、event payload 和 runtime tool state 边界。提交 `e65575c refactor(桥接): 抽取消息准备辅助函数`；验证为定向 `8 passed, 1 warning`、相邻 `36 passed, 1 warning`、全量 `1464 passed, 6 skipped, 139 warnings in 108.57s`。
- [x] 阶段 2：补模型 retry 回归，并拆 `_run_model_loop()`。提交 `1da43fb refactor(桥接): 拆分回复模型重试循环`；验证为模型门禁 `10 passed, 1 warning`、相邻 `98 passed, 1 warning`、全量 `1465 passed, 6 skipped, 139 warnings in 110.41s`。
- [x] 阶段 3：补 structured `no_reply` 回归，并拆 `_check_reply_contract()`。提交 `786e707 refactor(桥接): 拆分回复合同检查`；验证为 reply contract 门禁 `9 passed, 1 warning`、Bridge 相邻 `57 passed, 1 warning`、全量 `1466 passed, 6 skipped, 139 warnings in 112.65s`。
- [x] 阶段 4：补 trace cleanup 幂等回归，并收敛 `BridgeTraceFinalizer`。提交 `1612158 refactor(桥接): 收敛运行追踪收尾`；验证为 trace 与 stream 回归 `15 passed, 21 warnings`、Bridge 相邻 `67 passed, 1 warning`、全量 `1467 passed, 6 skipped, 139 warnings in 114.10s`。
- [x] 阶段 5：同步 `docs/todo.md`、`docs/plan_walkthrough.md` 和计划状态。验证为文档扫描无输出、文档格式检查无输出、H29 定向回归 `80 passed, 1 warning in 21.03s`、最终全量 `1467 passed, 6 skipped, 139 warnings in 111.26s`。

执行约束：

- `NanobotBridge.handle_message()` 和 `NanobotBridgePool.handle_message()` 的签名、默认值、keyword-only 参数和返回值保持不变。
- `metadata` 继续作为开放 dict 使用，`files` 不提升为顶层参数。
- `stream_queue` 仍是侧通道，不能替代最终字符串返回。
- 空字符串返回继续表达 no-reply、suppressed、audit-failure 或 empty。
- `pop_last_reply_meta(session_id)` 继续在 `handle_message()` 返回后可用，并保持弹出式语义。
- 不新增 `asyncio.run()`，不新增同步函数包 awaitable。
- 每个阶段完成后先运行指定定向回归和 `python -m pytest tests/ -v`，再按文件显式暂存并提交。

子 agent 分工结论：

- H29 生产代码集中在 `nanobot_kt/bridge.py`，主线程串行持有生产改动，避免同文件冲突。
- 测试和文档适合分派给子 agent；本轮实际执行中，前置只读 explorer 已用于职责切片、测试覆盖和外部契约审计。

下一步：

H29 第一轮拆分已无未完成任务。默认回到 `docs/todo.md` 的剩余 P3/P4 项，优先级较高的是 H30 RAG query 拆分和超大文件职责拆分；如果继续沿 Bridge 深化，建议先基于新的 helper 边界做二次体积削减计划，而不是扩大本轮提交范围。

## 2026-06-21 H30 RAG query 拆分计划

状态：H30 第一轮拆分已完成。`KnowledgeRagService.query()` 和 `MemoryRagService.query()` 的 public signature、result envelope、`stats`、`debug_trace`、degraded 语义、RAG benchmark adapter 和 Admin debug 消费契约保持不变；内部已按 recall、filter、rerank、gate 和 result 阶段拆出模块内私有边界。第一轮不抽跨模块 RAG base，避免 knowledge citation / document filter 与 memory parent grouping 过早绑定。

已完成：

- [x] 读取 `docs/todo.md` H30 条目，确认当前 HIGH 项是 `core/knowledge_rag.py` 与 `core/memory_rag.py` 中 `query()` 巨型流程拆分。
- [x] 分派只读 explorer 审计 H30 范围、测试覆盖、public contract 与 TimingGate 余留风险。
- [x] 收口 TimingGate cooldown fallback delay 补漏，提交 `69f2248 fix(TimingGate): 裁剪冷却回退等待时长`；验证为 `tests/test_timing_runtime.py` 63 passed、TimingGate 相关回归 124 passed、全量 `1469 passed, 6 skipped, 139 warnings in 115.50s`。
- [x] 写入设计文档：`docs/superpowers/specs/2026-06-21-h30-rag-query-refactor-design.md`，提交 `417f09b docs(检索): 设计 RAG 查询拆分`。
- [x] 写入实现计划：`.Codex/plans/h30-rag-query-refactor.md`，提交 `9d4a58b docs(计划): 记录 RAG 查询拆分计划`。
- [x] 任务 1：补 query contract characterization tests，提交 `c319b4f test(检索): 锁定 RAG 查询契约`。
- [x] 任务 2：拆分 `KnowledgeRagService.query()`，提交 `ba512f6 refactor(检索): 拆分知识查询流程`。
- [x] 任务 3：拆分 `MemoryRagService.query()`，提交 `5391274 refactor(检索): 拆分记忆查询流程`。
- [x] 任务 4：同步 `docs/todo.md`、`docs/plan_walkthrough.md` 和计划状态。

H30 计划列表：

- [x] 阶段 0：只读审计、方案选择和设计文档。
- [x] 阶段 0.5：实现计划写入 `.Codex/plans/h30-rag-query-refactor.md` 并同步 walkthrough。
- [x] 阶段 1：补 query contract characterization tests，覆盖 knowledge / memory 的 `stats`、`debug_trace`、`score_breakdown`、degraded、recall 越界和 skip reason。
- [x] 阶段 2：拆分 `KnowledgeRagService.query()`，保持 public signature、result envelope、citation 过滤和 benchmark / Admin debug 消费契约不变。
- [x] 阶段 3：拆分 `MemoryRagService.query()`，保持 source filter、parent grouping、reranker budget、weak fallback skip reason 和 degraded 语义不变。
- [x] 阶段 4：同步 `docs/todo.md`、`docs/plan_walkthrough.md` 和计划状态，记录提交号与验证结果。

执行约束：

- 不新增 `asyncio.run()`，不新增同步函数包 awaitable。
- 第一轮只在 `core/knowledge_rag.py` 和 `core/memory_rag.py` 内抽私有 dataclass/helper。
- 生产代码拆分由主线程串行持有，避免跨 agent 合并同类语义改动。
- 每个阶段完成后运行定向回归和 `python -m pytest tests/ -v`，再按文件显式暂存并提交。

验证记录：

- 任务 1 目标用例：`10 passed, 1 warning in 1.67s`；RAG 相邻回归：`67 passed, 21 warnings in 8.14s`；全量回归：`1477 passed, 6 skipped, 139 warnings in 108.80s`。
- 任务 2 knowledge 定向：`17 passed, 21 warnings in 2.70s`；RAG 相邻回归：`67 passed, 21 warnings in 8.06s`；提交前全量回归：`1477 passed, 6 skipped, 139 warnings in 111.94s`。
- 任务 3 memory 定向：`19 passed, 21 warnings in 2.44s`；RAG 相邻回归：`67 passed, 21 warnings in 7.95s`；提交前全量回归：`1477 passed, 6 skipped, 139 warnings in 114.59s`。

下一步：

默认回到 `docs/todo.md` 的剩余 P3/P4 项。H30 公共 recall helper 可在两个模块稳定运行后单独评估；不作为第一轮拆分阻塞项。

## 2026-06-21 Context Builder 第一刀拆分

状态：第一刀实现完成。`core/context_builder.py` 已保留真实上下文构造和兼容 facade，deprecated 群聊上下文实现已迁移到 `core/context_legacy.py`。

已完成：

- [x] 补充 `core.context_legacy` 模块边界红灯测试，确认新模块不存在时失败。
- [x] 新增 `core/context_legacy.py`，迁移 `build_group_recent_context()`、`_lookup_evidence_snippets()`、`build_group_profile_context()` 和 `_evidence_for()`。
- [x] 将 `core/context_builder.py` 的 deprecated 群聊 context 入口收敛为 wrapper，保持旧导入路径可用。
- [x] 同步 `.Codex/plans/context-builder-split.md`、`docs/todo.md` 和本 walkthrough。

验证：

- 红灯：`python -m pytest tests/test_group_memory.py::test_legacy_context_module_exports_group_context_builders -v` -> `1 failed, 1 warning`，失败原因为 `core.context_legacy` 不存在。
- 绿灯：同一测试 -> `1 passed, 1 warning`。
- 定向兼容：`tests/test_group_memory.py::test_legacy_context_module_exports_group_context_builders`、`tests/test_group_memory.py::TestBuildProfile::test_profile_includes_relationships_in_context`、`tests/test_group_memory.py::TestGroupRecentContext::test_recent_context_uses_maibot_message_prefix`、`tests/test_token_utils.py::test_remaining_token_estimators_share_same_formula` -> `4 passed, 1 warning`。
- 行数：`core/context_builder.py` 782 行，`core/context_legacy.py` 170 行。
- `asyncio.run` 约束：`tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard` -> `1 passed, 1 warning`。
- 全量：`python -m pytest tests/ -v` -> `1478 passed, 6 skipped, 139 warnings in 108.80s`。

后续：继续按规格中的排序拆 `api/admin_routes.py` DB Browser。

## 2026-06-21 Admin DB Browser 第一刀拆分

状态：第一刀实现完成。只读 DB Browser 的三条路由已迁移到
`api/admin/db_browser_routes.py`，`api/admin_routes.py` 保留顶层 include 和
旧符号兼容导出。`/db/backup`、`/db/vacuum` 及其他 admin 子域仍留在旧文件。

已完成：

- [x] 补充路由迁出、旧导入兼容、legacy token monkeypatch 和路由不重复注册测试。
- [x] 新增 `api/admin/db_browser_routes.py`，迁移 `DbQuery`、DB Browser 表策略、
  SQL guard、序列化 helper 和三条只读 Browser 路由。
- [x] `api/admin_routes.py` include 新 router，并通过 re-export 保持旧导入路径可用。
- [x] 删除旧文件中的只读 DB Browser 真实实现块，保留 `/db/backup` 和 `/db/vacuum`。
- [x] 同步 `.Codex/plans/admin-db-browser-split.md`、`docs/todo.md` 和本 walkthrough。

验证：

- 红灯：新增路由迁出和旧导入兼容测试在生产迁移前运行 -> `2 failed, 1 warning`。
- 绿灯首次运行：生产迁移后边界测试 -> `2 failed, 2 passed, 21 warnings`；
  原因是测试 helper 未展开 FastAPI `_IncludedRouter`。
- 绿灯修正：递归展开 `api.admin_routes.router` 后，新增边界测试 ->
  `4 passed, 21 warnings`。
- DB Browser 回归：`tests/test_admin_db_browser.py -v` ->
  `14 passed, 21 warnings in 4.30s`。
- Admin auth 回归：`tests/test_admin_api.py::TestAuth -v` ->
  `5 passed, 1 warning in 1.36s`。
- Private block 联动：`tests/test_admin_api.py::TestBlockRule`
  与 `tests/test_admin_api.py::TestPrivateBlockFlow::test_blocked_user_chat_writes_log_with_files`
  -> `3 passed, 1 warning in 1.16s`。
- WebUI DB 页面：
  `tests/test_admin_web_debug.py::test_db_page_contains_grouped_search_pagination_and_preview_ui -v`
  -> `1 passed, 1 warning in 0.72s`。
- `asyncio.run` 约束：
  `tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard -v`
  -> `1 passed, 1 warning in 1.79s`。
- 行数：`api/admin_routes.py` 5535 行，`api/admin/db_browser_routes.py` 374 行。
- 全量：`python -m pytest tests/ -v` ->
  `1482 passed, 6 skipped, 139 warnings in 105.96s`。
- 提交：`65060a2 refactor(管理端): 拆分 DB Browser 路由`。

后续：继续按超大文件拆分排序处理 `news_search/tool.py` 或下一段 admin 子域。

## 2026-06-21 news_search/tool.py 第一刀拆分

状态：第一刀实现完成。旧版新闻报告、评分、价值信号和 layout fallback / HTML
渲染 helper 已迁移到 `creatures/nanobot/prompts/skills/news_search/legacy_report.py`；
`tool.py` 继续保留搜索后端、缓存、`_summarize_news_layout()`、`WebTools`、
`search_and_extract_news*()` 和 `AiDailyTool` facade。

已完成：

- [x] 读取 `docs/todo.md` 的 P3 超大文件拆分条目，确认
  `creatures/nanobot/prompts/skills/news_search/tool.py` 仍在未完成列表中。
- [x] 分派并收口三路只读子 agent：
  - 职责边界审计：建议 `legacy_report.py` 作为第一刀，保持 `tool.py` facade、
    旧路径导入兼容和顶层运行入口 monkeypatch 兼容。
  - 测试覆盖审计：梳理 `tests/test_tools_package.py`、`tests/test_ai_daily_tool_and_sources.py`、
    `tests/test_ai_daily_ingest.py`、`tests/test_news_daily_pipeline.py`、KT/Bridge/schema 相邻回归。
  - 文档优先级审计：确认 TimingGate 不阻塞，下一阶段回到 P3 超大文件拆分。
- [x] 主线程复核 `tool.py` 关键边界：报告/评分/layout helper 集中在 70-884 行；
  `WebTools`、RSS/DDG、`search_and_extract_news_v2()`、`AiDailyTool` 和缓存暂不迁移。
- [x] 写入设计文档：
  `docs/superpowers/specs/2026-06-21-news-search-tool-split-design.md`。
- [x] 写入实现计划：`.Codex/plans/news-search-tool-split.md`。
- [x] 更新 `docs/todo.md`，记录 `news_search/tool.py` 第一刀已进入设计/计划阶段。
- [x] 补 `tests/test_news_search_legacy_report.py` 红灯测试，锁住新模块轻量导入与
  `tool.py` re-export 兼容。
- [x] 新增 `legacy_report.py`，迁移旧版新闻报告、评分、价值信号和 layout / HTML helper。
- [x] `tool.py` 显式 re-export 迁移后的 helper，保持旧导入路径可用。
- [x] 审查反馈修复：抽出轻量 `core.json_utils.json_repair()`，避免
  `_parse_news_layout_payload()` 通过 `core.legacy_adapter` 反向加载 `news_search.tool`
  及 runtime tool 依赖。
- [x] 审查反馈修复：收窄兼容说明，明确 `tool.py` re-export 保证导入兼容；
  迁移后的报告内部 helper 如需 monkeypatch，应使用 `legacy_report` 路径。

计划列表：

- [x] 阶段 0：只读审计、方案比较和边界选择。
- [x] 阶段 0.5：写入设计文档和实现计划，记录日期与当前阶段状态。
- [x] 阶段 1：补 `tests/test_news_search_legacy_report.py` 红灯测试，锁住新模块轻量导入与
  `tool.py` re-export 兼容。
- [x] 阶段 2：新增 `news_search/legacy_report.py`，迁移旧版新闻报告、评分、价值信号和
  layout fallback / HTML 渲染 helper；`tool.py` 显式 re-export。
- [x] 阶段 3：运行新模块、legacy HTML、搜索/AI 日报相邻回归、`asyncio.run` 策略测试和
  全量 `python -m pytest tests/ -v`。
- [x] 阶段 4：同步 `.Codex/plans/news-search-tool-split.md`、`docs/todo.md` 和本 walkthrough，
  记录行数变化、验证结果和提交号。
- [x] 阶段 5：处理审查反馈，隔离 parser 轻量依赖、补回归测试并更新兼容边界文档。

执行约束：

- 不迁移 `AiDailyTool`、`WebTools`、RSS/DDG 搜索后端、缓存和 `_run_news_daily_pipeline()`。
- 不改变 `search_and_extract_news()`、`search_and_extract_news_v2()`、`AiDailyTool` 的签名。
- 不恢复 `NewsSearchTool`，不重新暴露 `news_search` 工具名。
- 不新增 `asyncio.run()`，不新增同步函数包 awaitable。
- 不改 prompt runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。
- 旧 `tool.py` 路径保证导入兼容和顶层运行入口 patch 兼容；迁移后的报告内部 helper
  若要 monkeypatch 内部依赖，使用 `legacy_report` 路径。
- 每个阶段完成后运行定向回归和 `python -m pytest tests/ -v`，再按文件显式暂存并提交。

验证记录：

- 红灯：新增两个 `legacy_report` 测试在生产迁移前运行 ->
  `2 failed, 1 warning in 5.63s`；失败原因为 `legacy_report` 模块不存在。
- 绿灯：`tests/test_news_search_legacy_report.py -v` ->
  `2 passed, 1 warning in 0.78s`。
- legacy HTML / layout 定向回归 ->
  `4 passed, 1 warning in 0.57s`。
- 搜索与 AI 日报相邻回归 ->
  `9 passed, 1 warning in 0.73s`。
- `asyncio.run` 约束：
  `tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard -v` ->
  `1 passed, 1 warning in 1.74s`。
- `git diff --check`：无输出，退出码为 0。
- 行数：`tool.py` 1149 行，`legacy_report.py` 723 行。
- 全量：`python -m pytest tests/ -v` ->
  `1484 passed, 6 skipped, 139 warnings in 107.20s`。
- 提交：本阶段提交为 `5493955 refactor(新闻搜索): 拆分旧版报告渲染`。

审查修复验证记录：

- 红灯：新增 parser 轻量依赖测试在修复前失败，失败时显示 `news_search.tool`、
  `duckduckgo_search`、`trafilatura` 和 `BaseTool` 均被加载。
- 绿灯：parser 轻量依赖单测 ->
  `1 passed, 1 warning in 0.87s`。
- 新模块测试：`tests/test_news_search_legacy_report.py -v` ->
  `3 passed, 1 warning in 0.82s`。
- 旧 API 兼容：`tests/test_audit_fixes.py::TestEvolutionUtils -v` ->
  `5 passed, 1 warning in 0.46s`。
- news / AI Daily 相邻回归 ->
  `13 passed, 1 warning in 1.15s`。
- `asyncio.run` 约束：
  `tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard -v` ->
  `1 passed, 1 warning in 2.02s`。
- `git diff --check`：无输出，退出码为 0。
- 全量：`python -m pytest tests/ -v` ->
  `1485 passed, 6 skipped, 139 warnings in 116.15s`。
- 提交信息：`fix(新闻搜索): 隔离报告解析轻量依赖`。

后续：

完成 `legacy_report.py` 第一刀后，继续评估 `news_search/tool.py` 的缓存、搜索后端或
AI 日报适配层拆分；`cache.py` 可作为低风险小步候选，但不替代当前第一刀的超大文件治理收益。

## 2026-06-21 news_search/tool.py 运行时缓存拆分

状态：第二刀实现完成。运行时缓存状态、缓存 key 计算、日期解析和日报查询识别已拆到
`creatures/nanobot/prompts/skills/news_search/runtime_cache.py`；`tool.py` 保留旧缓存符号、
同一 dict / lock 和薄 wrapper，`AiDailyTool._execute()` 继续调用旧函数名。搜索后端、
`WebTools`、`AiDailyTool`、`_run_news_daily_pipeline()` 和 `_summarize_news_layout()`
仍留在旧文件，避免同时移动网络依赖和 KT 工具适配层。

设计文档：
`docs/superpowers/specs/2026-06-21-news-search-cache-split-design.md`。

实现计划：
`.Codex/plans/news-search-cache-split.md`。

只读审计结论：

- `docs/todo.md` 剩余硬项主要是「超大文件 >800 行拆分」「静默吞异常补日志」
  和「ruff 批量清理」，其中 `news_search/tool.py` 第二刀后仍有 1110 行。
- `news_search/tool.py` 的缓存边界集中在顶部状态、`_news_search_cache_key()`、
  `_get_cached_news_result()`、`_store_cached_news_result()` 和 `AiDailyTool._execute()`。
- 现有测试会直接清理 `news_tool._NEWS_SEARCH_CACHE`，因此新模块必须与旧符号共享同一个
  dict / lock，不能只复制状态。
- 搜索后端拆分涉及 `_urlopen`、`DDGS`、`trafilatura`、RSS 和 `WebTools` 的旧路径
  monkeypatch，风险高于缓存拆分。

计划列表：

- [x] 阶段 0：确认待办、第一刀收口状态和缓存调用边界。
- [x] 阶段 0.5：写入缓存拆分设计文档，明确 `runtime_cache.py` 与 `tool.py` facade。
- [x] 阶段 0.6：写入 `.Codex/plans/news-search-cache-split.md`，列出 TDD 红灯、
  实现、验证和阶段性提交步骤。
- [x] 阶段 1：新增 `tests/test_news_search_runtime_cache.py` 红灯测试，覆盖轻量导入、
  key 形态、共享 dict / lock 和旧 TTL monkeypatch。
- [x] 阶段 2：新增 `news_search/runtime_cache.py`，迁移缓存状态、日期解析、日报识别、
  缓存 key 和缓存读写。
- [x] 阶段 3：将 `tool.py` 缓存相关真实实现收敛为旧符号 facade，`AiDailyTool._execute()`
  继续调用旧函数名。
- [x] 阶段 4：运行运行时缓存测试、AI 日报相邻回归、旧报告回归、`asyncio.run`
  策略测试和全量 `python -m pytest tests/ -v`。
- [x] 阶段 5：同步 `.Codex/plans/news-search-cache-split.md`、`docs/todo.md` 和本
  walkthrough，记录验证结果、行数变化和提交号。

验证记录：

- 红灯：`python -m pytest tests/test_news_search_runtime_cache.py -v` ->
  `3 failed, 1 warning in 5.47s`，失败原因为 `runtime_cache` 模块不存在。
- 语法检查：`python -m compileall creatures/nanobot/prompts/skills/news_search -q`
  无输出，退出码为 0。
- 绿灯：`tests/test_news_search_runtime_cache.py -v` ->
  `3 passed, 1 warning in 0.78s`。
- 旧日期与缓存兼容：`test_extract_date_accepts_chinese_date_and_today` 与
  `test_ai_daily_tool_reuses_equivalent_daily_query_cache` ->
  `2 passed, 1 warning in 0.62s`。
- AI 日报相邻回归：`tests/test_ai_daily_ingest.py -v` ->
  `7 passed, 1 warning in 1.04s`；`tests/test_ai_daily_tool_and_sources.py -v` ->
  `14 passed, 1 warning in 1.03s`。
- 旧报告相邻回归：`tests/test_news_search_legacy_report.py -v` ->
  `3 passed, 1 warning in 0.73s`。
- `asyncio.run` 约束：
  `tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard -v` ->
  `1 passed, 1 warning in 1.68s`。
- `git diff --check`：无输出，退出码为 0。
- 全量：`python -m pytest tests/ -v` ->
  `1488 passed, 6 skipped, 139 warnings in 105.92s`。
- 行数：`tool.py` 1110 行，`runtime_cache.py` 122 行，
  `tests/test_news_search_runtime_cache.py` 66 行。
- 实现提交：`f4bdfec refactor(新闻搜索): 拆分运行时缓存`。

执行约束：

- 不迁移 `AiDailyTool`、`WebTools`、RSS/DDG 搜索后端、`_run_news_daily_pipeline()` 或
  `_summarize_news_layout()`。
- 不改变缓存 key 版本号 `v2_20260503`、日报 key 形态、普通 query key 形态、TTL 默认值
  或旧淘汰触发条件。
- `tool.py` 必须保留 `NEWS_SEARCH_CACHE_TTL_SECONDS`、`_NEWS_SEARCH_CACHE`、
  `_NEWS_SEARCH_CACHE_LOCK`、`_news_search_cache_key()`、`_get_cached_news_result()` 和
  `_store_cached_news_result()`。
- `runtime_cache.py` 不得导入 `news_search.tool`、`DDGS`、`trafilatura`、`BaseTool` 或
  `run_awaitable_sync`。
- 不新增 `asyncio.run()`，不新增同步函数包 awaitable。
- 不改 prompt runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。

下一步：

后续已执行搜索后端拆分，详见下一节。AI 日报工具适配层仍可按维护性继续评估，但
`news_search/tool.py` 已不再阻塞 >800 行拆分子项。

## 2026-06-21 news_search/tool.py 搜索后端拆分

状态：第三刀实现完成。RSS、Juya、DDG、query 判定、stale 过滤、domain diversity
rerank 和 trafilatura 正文提取已拆到
`creatures/nanobot/prompts/skills/news_search/search_backend.py`；`tool.py` 保留旧符号、
旧 monkeypatch 入口和 `WebTools` facade，调用新模块时显式注入 `DDGS`、`trafilatura`、
`_fetch_multi_rss()`、`_fetch_juya_rss()` 和 `_urlopen()`。

设计文档：
`docs/superpowers/specs/2026-06-21-news-search-backend-split-design.md`。

实现计划：
`.Codex/plans/news-search-backend-split.md`。

阶段提交：

- 设计提交：`4ba9d95 docs(新闻搜索): 设计搜索后端拆分`。
- 计划提交：`536305d docs(计划): 记录搜索后端拆分计划`。
- 实现提交：`009af79 refactor(新闻搜索): 拆分搜索后端`。

已完成：

- [x] 新增 `tests/test_news_search_backend_split.py`，锁定新模块入口、旧
  `tool.WebTools.search` monkeypatch、旧 `_urlopen` monkeypatch 和旧 trafilatura
  monkeypatch 兼容。
- [x] 新增 `search_backend.py`，迁移代理感知 `_urlopen()`、`_ddgs_kwargs()`、RSS/Juya
  抓取、DDG 聚合搜索、query variants、去重、排序和正文提取。
- [x] 将 `tool.py` 中搜索后端真实实现收敛为 facade，保留 `DDGS`、`trafilatura`、
  `NEWS_SEARCH_DDG_ENABLED`、`RSS_*`、`JUYA_RSS_URL` 和同名 helper wrapper。
- [x] `WebTools.search()` 改为接收 `search_backend.search()` 返回的
  `(results, last_error)`，继续维护 `WebTools.last_error` 旧语义。
- [x] `WebTools.extract_web_content()` 改为调用
  `search_backend.extract_web_content(url, trafilatura_module=trafilatura)`，保留旧路径
  patch 兼容。
- [x] 同步 `.Codex/plans/news-search-backend-split.md`、`docs/todo.md` 和本 walkthrough。

验证记录：

- 红灯：`tests/test_news_search_backend_split.py -v` 在生产迁移前运行 ->
  `1 failed, 3 passed, 1 warning in 5.75s`；失败原因是
  `ImportError: cannot import name 'search_backend'`。
- 绿灯：`tests/test_news_search_backend_split.py -v` ->
  `4 passed, 1 warning in 0.68s`。
- 搜索后端定向回归：`tests/test_tools_package.py` 中 7 个 WebTools / RSS / Juya / DDG
  兼容用例 -> `7 passed, 1 warning in 0.64s`。
- 相邻兼容回归：legacy report、runtime cache、AI 日报 ingest/source、KT
  `TestAiDailyTool` 和 `asyncio.run` 策略测试 ->
  `30 passed, 1 warning in 4.16s`。
- 语法检查：`python -m compileall creatures/nanobot/prompts/skills/news_search -q`
  无输出，退出码为 0。
- 格式检查：`git diff --check` 无输出，退出码为 0。
- 全量：`python -m pytest tests/ -v` ->
  `1492 passed, 6 skipped, 139 warnings in 110.25s`。
- 行数：`tool.py` 798 行，`search_backend.py` 482 行，
  `tests/test_news_search_backend_split.py` 105 行。

执行约束：

- 不迁移 `AiDailyTool`、`search_and_extract_news*()`、`_run_news_daily_pipeline()` 或
  `_summarize_news_layout()`。
- 不改变 `WebTools.search()` / `WebTools.extract_web_content()` public signature。
- 不破坏旧路径 monkeypatch：`news_tool.DDGS`、`news_tool.trafilatura`、
  `news_tool._fetch_multi_rss()`、`news_tool._fetch_juya_rss()`、`news_tool._urlopen()`。
- 不新增 `asyncio.run()`，不新增同步函数包 awaitable。
- 不改 prompt runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。

下一步：

`news_search/tool.py` 已降至 800 行以下，P3 超大文件队列应优先回到仍超过 800 行的
`api/routes.py`、`core/group_runtime/runtime.py` 或 `core/persona_preprocess.py`。若继续做
新闻搜索域内治理，优先评估 V2 evidence bridge 或 AI 日报适配层拆分，但这属于后续
维护性优化，不再是 >800 行硬阻塞项。

## 2026-06-21 GroupRuntime 状态与评分拆分

状态：实现与文档收口已完成。`core/group_runtime/runtime.py` 已从 1385 行降至
722 行，群运行时常量、状态模型、pending helper 和 scoring 私有方法分别拆到
`core/group_runtime/constants.py`、`core/group_runtime/state.py` 与
`core/group_runtime/scoring.py`；`runtime.py` 继续承载主状态机、模型调用、
timing context 构造、快照和全局单例。

设计文档：
`docs/superpowers/specs/2026-06-21-group-runtime-state-scoring-split-design.md`。

实现计划：
`.Codex/plans/group-runtime-state-scoring-split.md`。

阶段提交：

- 设计提交：`b4ae8a5 docs(群运行时): 设计状态与评分拆分`。
- 计划提交：`4d6614d docs(计划): 记录群运行时拆分计划`。
- 测试稳定性提交：`4cbab07 test(画像预处理): 固定正交向量 mock`。
- 实现提交：`0018d02 refactor(群运行时): 拆分状态与评分逻辑`。

已完成：

- [x] 新增 `tests/test_group_runtime_split_compat.py`，锁定旧导入路径、pending payload、
  directed 优先级、helper 边界和 `_score_timing()` 入参映射。
- [x] 新增 `constants.py`，迁移群运行时常量和 trigger 集合。
- [x] 新增 `state.py`，迁移 `GroupPendingMessage`、`GroupChatState`、pending payload
  helper、scoring 信号 helper、wait delay clip 和 model confidence 解析。
- [x] 新增 `scoring.py`，以 `GroupRuntimeScoringMixin` 承载 timing scoring、policy、
  cooldown、shadow scoring 和 recent follow-up 私有逻辑。
- [x] `GroupRuntime` 改为继承 `GroupRuntimeScoringMixin`，旧
  `core.group_runtime.runtime` 与 `core.timing_runtime` 导入路径继续兼容。
- [x] `runtime.py` 行数降至 722 行，已移出 P3 超大文件 >800 行清单。

验证记录：

- 行为基线：`tests/test_group_runtime_split_compat.py -v` ->
  `5 passed, 1 warning in 0.76s`。
- 状态拆分定向：split compat、group runtime ids、`TestGateState`、
  `TestGroupPendingMessageDirected`、`TestShouldSuppressDirected` ->
  `26 passed, 1 warning in 1.24s`。
- Scoring 定向：split compat、`tests/test_timing_runtime.py`、
  `tests/test_timing_score.py`、group runtime ids ->
  `89 passed, 1 warning in 2.35s`。
- 相邻回归：群响应 envelope、group message structured、API timing gate 和 KT
  note bot replied 相关测试 ->
  `32 passed, 21 warnings in 11.04s`。
- 语法检查：`python -m compileall core/group_runtime -q` 无输出，退出码为 0。
- 格式检查：`git diff --check` 无输出，退出码为 0。
- `asyncio.run` 约束：本次拆分范围内扫描无匹配。
- 全量：`python -m pytest tests/ -v` ->
  `1497 passed, 6 skipped, 139 warnings in 119.42s`。
- 文档收口提交前验证：`git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/group-runtime-state-scoring-split.md`
  与残留扫描脚本均无输出；随后全量 `python -m pytest tests/ -v` ->
  `1497 passed, 6 skipped, 139 warnings in 111.55s`。
- 行数：`runtime.py` 722 行，`constants.py` 19 行，`state.py` 397 行，
  `scoring.py` 330 行，`tests/test_group_runtime_split_compat.py` 137 行。

执行约束：

- 不迁移 `process_message()`、`handle_timer_fired()`、`_apply_gate_result()`、
  `_call_gate()`、`_build_timing_context()`、快照或全局单例。
- 不改变群聊主状态机、timing policy 语义、pending payload 格式或旧导入路径。
- 不新增 `asyncio.run()`，不新增同步函数包 awaitable。
- 不改 prompt runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。

下一步：

P3 超大文件队列现在应优先继续拆 `api/routes.py`，其次是
`core/persona_preprocess.py`。如果转向低风险质量项，则处理「静默吞异常补日志」更适合
小步推进。

## 2026-06-21 Best-effort 吞异常补日志

状态：实现、验证、实现阶段提交与文档收口已完成。该阶段聚焦 `docs/todo.md`
中 P3 的 best-effort fallback 可观测性，不改变任何 fallback 返回值或业务流程。

设计文档：
`docs/superpowers/specs/2026-06-21-best-effort-debug-logging-design.md`。

实现计划：
`.Codex/plans/best-effort-debug-logging.md`。

阶段提交：

- 设计提交：`4074b60 docs(日志): 设计 best-effort 调试日志`。
- 计划提交：`e210d7e docs(计划): 记录 best-effort 日志计划`。
- 实现提交：`ab08701 fix(日志): 补齐 best-effort 调试记录`。

已完成：

- [x] `core/prompts/manager.py` 的 `PromptTracer.record_render()` fallback 补
  `nanobot.prompt_manager` 的 `debug` 日志。
- [x] `core/context_legacy.py` 的 deprecated 群画像 fallback 补
  `nanobot.context_legacy` 的 `debug` 日志。
- [x] `api/admin/system_routes.py` 的 git 探测 fallback 补 `nanobot.admin`
  的 `debug` 日志。
- [x] `app/group_ingress/helpers.py` 的 `safe_meta()` 与
  `get_group_talk_value()` fallback 补 `nanobot.group_ingress` 的 `debug`
  日志。
- [x] `app/memory_digest/builder.py` 的 `_safe_meta()` fallback 补
  `nanobot.memory_digest.builder` 的 `debug` 日志。
- [x] `core/legacy_adapter.py::SQLiteMemory.save_log()` 已有 rollback +
  `logger.exception` + 回归测试，本阶段仅复验旧行为，未改业务逻辑。
- [x] 新增和扩展 caplog 测试，覆盖 trace、deprecated 群画像、git 探测、
  group ingress helper 与 memory digest meta fallback。
- [x] 同步 `docs/todo.md` 和 `.Codex/plans/best-effort-debug-logging.md`。

验证记录：

- 红灯：实现前运行新增日志测试 -> `6 failed, 1 warning in 6.62s`；失败点均为
  缺少预期 `debug` 日志。
- 绿灯：新增日志测试 -> `6 passed, 1 warning in 1.52s`。
- 相邻回归：prompt manager、group memory、admin auth、group ingress helper、
  memory digest builder 和 H11 `save_log` 回归 -> `35 passed, 1 warning in 3.24s`。
- 语法检查：`python -m compileall core/prompts/manager.py core/context_legacy.py api/admin/system_routes.py app/group_ingress/helpers.py app/memory_digest/builder.py -q`
  无输出，退出码为 0。
- 静默吞异常扫描：目标文件中
  `except Exception:\s*(pass|return \{\}|return None|return ""|return 0\.5)`
  无匹配。
- 格式检查：目标实现与测试文件 `git diff --check` 无输出。
- 全量：`python -m pytest tests/ -v` ->
  `1503 passed, 6 skipped, 139 warnings in 112.76s`。
- 提交后检查：`git show --stat --oneline -1` 确认实现提交包含 10 个预期文件，
  目标代码与测试文件提交后干净。
- 文档门禁：`git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/best-effort-debug-logging.md`
  与残留扫描脚本均无输出。
- 文档提交前全量：`python -m pytest tests/ -v` ->
  `1503 passed, 6 skipped, 139 warnings in 118.42s`。

执行约束：

- 日志级别统一为 `debug`，避免把可容错 fallback 升级成生产噪声。
- 不记录 prompt 正文、用户输入、完整 `meta_json`、文件 URL 或群记忆 evidence。
- 不改变 `/version`、群画像、meta 解析、talk value fallback 或 memory digest 构建结果。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。
- 不改 prompt runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。

下一步：

`docs/todo.md` 当前剩余硬项主要是「超大文件 >800 行拆分」和「ruff 批量清理」。
若继续做结构性治理，优先拆 `api/routes.py`，其次是 `core/persona_preprocess.py`；
若转向低风险质量收尾，则可以先做 ruff 批量清理。

## 2026-06-21 API Routes 群消息 Helper 去重

状态：实现、验证和实现阶段提交已完成。`api/routes.py` 中与
`app.group_ingress.helpers` 重复的群消息 helper 实现已删除，旧 underscore 私有
helper 名称保留为兼容别名；`api/routes.py` 从 3434 行降至 2822 行，仍超过
800 行，后续可继续拆其他路由职责。

设计文档：
`docs/superpowers/specs/2026-06-21-api-routes-group-helper-split-design.md`。

实现计划：
`.Codex/plans/api-routes-group-helper-split.md`。

阶段提交：

- 设计提交：`0fd2da8 docs(路由): 设计群消息 helper 去重`。
- 计划提交：`3a75ec4 docs(计划): 记录群消息 helper 去重计划`。
- 实现提交：`c822177 refactor(路由): 收敛群消息 helper 实现`。

已完成：

- [x] 新增 `tests/test_api_routes_group_helper_facade.py`，锁定 `api.routes`
  旧 underscore helper 指向 `app.group_ingress.helpers`。
- [x] 新增行数守卫，要求 `api/routes.py` 低于 3000 行。
- [x] `api/routes.py` 导入 `app.group_ingress.helpers` 并绑定旧私有 helper
  兼容别名。
- [x] 删除 route-local 的群回复持久化、复读检测、no-reply 日志 helper。
- [x] 删除结构化群消息 helper、重复常量、sticker preview 背景缓存 helper 和
  agent result / trigger reason helper 的 route-local 实现。
- [x] 保留 `GroupMessageRequest`、`group_message()`、`GroupTimingRequest` 与
  `group_timing_timer()` 的路由边界。

验证记录：

- 红灯：`tests/test_api_routes_group_helper_facade.py -q` ->
  `2 failed, 1 warning in 5.44s`；失败点为旧 helper 仍是本地函数，
  且 `api/routes.py` 仍为 3434 行。
- 绿灯：`tests/test_api_routes_group_helper_facade.py -q` ->
  `2 passed, 1 warning in 0.60s`。
- 旧私有导入与 facade 定向回归：新增 facade 测试、group bridge reply 持久化、
  duplicate reply 检测和 reply admin agent result 用例 ->
  `7 passed, 1 warning in 0.93s`。
- 相邻回归：`tests/test_api.py`、group / chat response envelope、push envelope、
  streaming API 和 streaming response envelope ->
  `102 passed, 21 warnings in 23.17s`。
- 语法检查：`python -m compileall api/routes.py app/group_ingress/helpers.py -q`
  无输出，退出码为 0。
- 重复定义检查：目标 helper 定义 `rg` 无匹配，退出码为 1。
- 行数检查：`wc -l api/routes.py` -> `2822 api/routes.py`。
- 格式检查：`git diff --check -- api/routes.py tests/test_api_routes_group_helper_facade.py`
  无输出。
- 全量：`python -m pytest tests/ -v` ->
  `1505 passed, 6 skipped, 139 warnings in 107.44s`。

执行约束：

- 不迁移 `GroupMessageRequest`、`group_message()`、`GroupTimingRequest` 或
  `group_timing_timer()`。
- 不改变 `/group/message` 主流程、不改变 `/chat` 主流程。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。
- 不改 prompt runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime
  输入。

下一步：

`api/routes.py` 仍超过 800 行，P3 超大文件队列可继续拆路由中与 `/chat`、群 timing
timer 或 sticker 端点相关的独立职责；若转向较小范围，`core/persona_preprocess.py`
第一刀也适合作为下一阶段。

## 2026-06-21 Persona 候选 Prompt 拆分

状态：实现、验证和实现阶段提交已完成。`core/persona_preprocess.py` 已从 857 行降至
773 行，候选提取 prompt、用户日志过滤、候选日志格式化和候选 prompt 构造已迁移到
`core/persona_candidate_prompt.py`；旧 `core.persona_preprocess` 同名符号继续作为
facade 导入，外部导入路径兼容。

设计文档：
`docs/superpowers/specs/2026-06-21-persona-candidate-prompt-split-design.md`。

实现计划：
`.Codex/plans/persona-candidate-prompt-split.md`。

阶段提交：

- 设计提交：`785bf26 docs(画像): 设计候选 prompt 拆分`。
- 计划提交：`a7ebd3e docs(计划): 记录候选 prompt 拆分计划`。
- 实现提交：`3e6d878 refactor(画像): 拆分候选 prompt helper`。

已完成：

- [x] 新增 `tests/test_persona_candidate_prompt_split.py`，锁定旧模块候选 prompt 符号
  指向 `core.persona_candidate_prompt`。
- [x] 新增行数守卫，要求 `core/persona_preprocess.py` 低于 800 行。
- [x] 新增 `core/persona_candidate_prompt.py`，承载候选提取 prompt 和日志格式化纯函数。
- [x] `core/persona_preprocess.py` 删除本地 prompt 常量和三个纯函数实现，并从新模块
  导入同名符号保留兼容。
- [x] 保留 `PersonaStateMachine`、embedding / NLI 懒加载、DB 写入和 monkeypatch 契约。

验证记录：

- 红灯：`tests/test_persona_candidate_prompt_split.py -q` ->
  `2 failed, 1 warning in 5.79s`；失败点为新模块不存在且旧文件仍为 857 行。
- 绿灯：`tests/test_persona_candidate_prompt_split.py -q` ->
  `2 passed, 1 warning in 0.67s`。
- Prompt 定向回归：新增 split 测试与 `tests/test_persona_preprocess.py::TestBuildPrompt` ->
  `4 passed, 1 warning in 0.65s`。
- 相邻回归：`tests/test_persona_preprocess.py -m "not slow"` 与
  `tests/test_admin_api.py -k "persona_update_fact_rejects_duplicate"` ->
  `2 passed, 111 deselected, 1 warning in 1.48s`。
- 语法检查：`python -m compileall core/persona_preprocess.py core/persona_candidate_prompt.py -q`
  无输出，退出码为 0。
- 行数检查：`persona_preprocess.py` 773 行，`persona_candidate_prompt.py` 97 行，
  `tests/test_persona_candidate_prompt_split.py` 20 行。
- 格式检查：`git diff --check -- core/persona_preprocess.py core/persona_candidate_prompt.py tests/test_persona_candidate_prompt_split.py`
  无输出。
- 全量：`python -m pytest tests/ -v` ->
  `1507 passed, 6 skipped, 139 warnings in 107.24s`。

执行约束：

- 不移动 `PersonaStateMachine`、`embed_text()`、`_get_embedder()`、`_get_nli()`、
  `_EMBEDDER_MODEL`、`_NLI_MODEL`、`content_hash()`、blob helper 或置信度 helper。
- 不改变画像候选 prompt 文案、JSON schema 示例、证据 ID 校验或状态机决策语义。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。
- 不改 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。

下一步：

当时 P3 超大文件队列只剩 `api/admin_routes.py` 和 `api/routes.py` 两个硬项；如果先做
低风险质量收尾，则可以进入「ruff 批量清理」。

## 2026-06-21 Admin Sticker 路由拆分

状态：实现、验证和实现阶段提交准备已完成。
`api/admin_routes.py` 已拆出 Sticker / Generated Images 管理边界到
`api/admin/sticker_routes.py`；旧 `api.admin_routes` 继续 include 新 router，并
re-export 迁移后的 request model、endpoint 和 `_sticker_dict()`，保持旧导入路径、
HTTP 路径、审计动作和 admin token monkeypatch 兼容。`api/admin_routes.py` 从
5535 行降至 4979 行，新模块 `api/admin/sticker_routes.py` 为 614 行。

设计文档：
`docs/superpowers/specs/2026-06-21-admin-sticker-routes-split-design.md`。

实现计划：
`.Codex/plans/admin-sticker-routes-split.md`。

阶段提交：

- 设计提交：`ab17cb4 docs(管理端): 设计贴纸路由拆分`。
- 计划提交：`3162055 docs(计划): 记录贴纸路由拆分计划`。
- 实现提交：`26f6112 refactor(管理端): 拆分贴纸管理路由`。

已完成：

- [x] 新增 `tests/test_admin_sticker_routes_split.py`，锁定路由来源、旧导入
  facade、legacy token monkeypatch 和 method + path 级别的重复注册。
- [x] 新增 `api/admin/sticker_routes.py`，承载 Sticker CRUD、Generated Images、
  duplicate groups、near duplicate 治理、预览重试、phash / dedupe backfill 和批量删除。
- [x] 新模块使用 `api.admin.common.verify_admin` 与 `audit_request`，不反向导入
  `api.admin_routes`，不新增 `asyncio.run()` 或同步 awaitable 包装。
- [x] `api/admin_routes.py` include `sticker_router`，并 re-export 迁移符号；
  `group_detail()` 继续调用同名 `_sticker_dict()`。
- [x] 保留父模块 `_safe_json()`、`_iso()`、`verify_admin()`、`_audit_request()`、
  `NANOBOT_ADMIN_TOKEN`、`StickerMemory` 和 `get_db` 等其他 admin 子域仍需依赖的符号。

验证记录：

- 红灯：新增 split 目标测试 ->
  `2 failed, 1 warning`；失败点为 endpoint module 仍是 `api.admin_routes`，
  且 `api.admin.sticker_routes` 尚不存在。
- 绿灯：`tests/test_admin_sticker_routes_split.py -q` ->
  `4 passed, 21 warnings in 1.30s`。
- sticker 行为回归：新增 split 测试、`TestGeneratedImagesAdmin` 和
  `TestStickerCRUD` -> `19 passed, 21 warnings in 2.00s`。
- 鉴权与 asyncio 策略回归：`TestAuth` 与 `tests/test_asyncio_run_policy.py` ->
  `9 passed, 1 warning in 2.33s`。
- WebUI duplicate 静态回归：`tests/test_webui_admin_redesign.py -k "sticker_duplicate"` ->
  `2 passed, 21 deselected, 1 warning in 0.44s`。
- 语法检查：`python -m compileall api/admin_routes.py api/admin/sticker_routes.py -q`
  无输出，退出码为 0。
- 行数检查：`api/admin_routes.py` 4979 行，`api/admin/sticker_routes.py` 614 行，
  `tests/test_admin_sticker_routes_split.py` 119 行。
- 格式检查：`git diff --check -- api/admin_routes.py api/admin/sticker_routes.py tests/test_admin_sticker_routes_split.py`
  无输出。
- 全量：`python -m pytest tests/ -v` ->
  `1511 passed, 6 skipped, 139 warnings in 109.17s`。

执行约束：

- 不拆普通 `api/routes.py`。
- 不迁移 admin 认证、审计 helper、`/overview`、`/groups`、群记忆、TimingGate、
  配置、模型、工具、reply/eval、eval 工作台、日志 viewer 或 settings。
- 不改变 DB schema、response shape、状态码、审计 action、预览缓存行为或
  duplicate canonical 语义。
- 不改 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。

下一步：

P3 超大文件队列仍剩 `api/admin_routes.py` 和 `api/routes.py`。如果继续沿管理端拆分，
下一刀可优先考虑 `group_memory` 或 trace / observability 只读边界；如果切回普通 API，
可优先拆公开 media / sticker 端点，但要先设计 `api.routes.verify_token` monkeypatch 兼容。

## 2026-06-21 Admin Group Memory 路由拆分

状态：实现、定向验证和实现阶段提交准备已完成。`api/admin_routes.py` 已拆出
Group Memory 管理端路由到 `api/admin/group_memory_routes.py`；旧
`api.admin_routes` 继续 include 新 router，并 re-export 迁移后的 request model、
helper 和 endpoint，保持旧导入兼容。`api/admin_routes.py` 从 4979 行降至
4731 行，新模块 `api/admin/group_memory_routes.py` 为 281 行。

设计文档：
`docs/superpowers/specs/2026-06-21-admin-group-memory-routes-split-design.md`。

实现计划：
`.Codex/plans/admin-group-memory-routes-split.md`。

阶段提交：

- 设计提交：`0388314 docs(管理端): 设计群记忆路由拆分`。
- 计划提交：`0f43e62 docs(计划): 记录群记忆路由拆分计划`。
- 实现提交：`925c110 refactor(管理端): 拆分群记忆路由`。

已完成：

- [x] 新增 `tests/test_admin_group_memory_routes_split.py`，覆盖 endpoint module、
  legacy import、token monkeypatch、重复注册和 group detail catch-all 顺序。
- [x] 在 `tests/test_admin_api.py::TestObservabilityAPI` 补 legacy list 路由行为回归。
- [x] 新增 `api/admin/group_memory_routes.py`，迁移 Group Memory request model、
  helper 和 8 个路由；新模块使用 `api.admin.common.verify_admin` 和
  `audit_request`，不反向导入 `api.admin_routes`。
- [x] `api/admin_routes.py` include `group_memory_router`，并 re-export 迁移符号；
  `/groups/{group_id:path}/memories` 系列路由注册顺序早于本地
  `/groups/{group_id:path}` catch-all。

验证记录：

- 红灯：split 目标测试 ->
  `2 failed, 1 warning in 5.66s`；失败点为 endpoint module 仍是
  `api.admin_routes`，且 `api.admin.group_memory_routes` 尚不存在。
- 绿灯：`tests/test_admin_group_memory_routes_split.py -q` ->
  `5 passed, 21 warnings in 1.31s`。
- Group Memory 行为回归：split 测试与 `TestObservabilityAPI` ->
  `14 passed, 21 warnings in 1.71s`。
- 鉴权与 asyncio 策略回归：`TestAuth` 与 `tests/test_asyncio_run_policy.py` ->
  `9 passed, 1 warning in 2.49s`。
- 语法检查：`python -m compileall api/admin_routes.py api/admin/group_memory_routes.py -q`
  无输出，退出码为 0。
- 格式检查：`git diff --check -- api/admin_routes.py api/admin/group_memory_routes.py tests/test_admin_group_memory_routes_split.py tests/test_admin_api.py`
  无输出，退出码为 0。
- 反向导入与旧 helper 精确扫描：token 级扫描确认新模块没有导入
  `api.admin_routes`，没有使用 `_audit_request`、`_raw_group_id`、
  `_group_session_id` 或 `_group_stream_id`，也没有新增 `asyncio.run()` 或
  `run_awaitable_sync()`。
- 行数检查：`api/admin_routes.py` 4731 行，`api/admin/group_memory_routes.py`
  281 行，`tests/test_admin_group_memory_routes_split.py` 111 行。
- 全量：`python -m pytest tests/ -v` ->
  `1517 passed, 6 skipped, 139 warnings in 107.94s`。
- 文档收口提交前复跑：`python -m pytest tests/ -v` ->
  `1517 passed, 6 skipped, 139 warnings in 108.71s`。

执行约束：

- 不拆普通 `api/routes.py`。
- 不迁移 admin 认证、审计 helper、`/overview`、`/groups`、`group_detail()`、
  TimingGate、配置、模型、工具、reply/eval、eval 工作台、日志 viewer 或 settings。
- 不改变 DB schema、HTTP 路径、response shape、状态码、审计 action、token
  monkeypatch 或 group detail catch-all 兼容顺序。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列仍剩 `api/admin_routes.py` 和 `api/routes.py`。继续沿管理端拆分时，
下一刀可考虑 trace / observability 只读边界；切普通 API 前应先设计 `verify_token`
共享兼容层。

## 2026-06-21 Admin Observability 路由拆分

状态：实现、验证、实现阶段提交和文档收口验证已完成。`api/admin_routes.py`
已拆出 trace、工具调用、LLM API 日志、audit log、日志 viewer 和前端错误上报路由到
`api/admin/trace_routes.py` 与 `api/admin/log_routes.py`；旧 `api.admin_routes`
继续 include 新 router，并 re-export 迁移后的 endpoint、request model 和 helper，
保持旧导入路径、HTTP 路径、admin token monkeypatch、日志读取语义和 audit log 过滤兼容。
`api/admin_routes.py` 从 4731 行降至 4303 行。

设计文档：
`docs/superpowers/specs/2026-06-21-admin-observability-routes-split-design.md`。

实现计划：
`.Codex/plans/admin-observability-routes-split.md`。

阶段提交：

- 设计提交：`a305e28 docs(管理端): 设计观测路由拆分`。
- 计划提交：`91da500 docs(计划): 记录观测路由拆分计划`。
- 实现提交：`12f1548 refactor(管理端): 拆分观测路由`。

已完成：

- [x] 新增 `tests/test_admin_observability_routes_split.py`，锁定 endpoint module、
  legacy import、token monkeypatch、重复注册和 `/logs/{name}` 动态路由顺序。
- [x] 新增 `api/admin/trace_routes.py`，承载 AgentRun、ToolCall 和 LLM API log
  查询接口。
- [x] 新增 `api/admin/log_routes.py`，承载 AdminAuditLog、日志 viewer 和
  frontend error 上报接口。
- [x] `api/admin_routes.py` include `trace_router` 与 `log_router`，并 re-export
  迁移符号。
- [x] 新模块无反向导入 `api.admin_routes`，未新增 `asyncio.run()` 或
  `run_awaitable_sync()`。

验证记录：

- 红灯：`tests/test_admin_observability_routes_split.py -q` ->
  `4 failed, 2 passed, 21 warnings in 6.66s`；失败点为 endpoint module 仍是
  `api.admin_routes`、新模块不存在，以及 `/logs/frontend-error` 顺序落后于
  `/logs/{name}`。
- 绿灯：`tests/test_admin_observability_routes_split.py -q` ->
  `6 passed, 21 warnings in 1.36s`。
- 行为回归：`tests/test_prompt_trace_admin.py tests/test_admin_logs_viewer.py tests/test_admin_api.py::TestObservabilityAPI -q`
  -> `14 passed, 21 warnings in 3.65s`。
- 鉴权与 asyncio 策略回归：`tests/test_admin_api.py::TestAuth tests/test_asyncio_run_policy.py -q`
  -> `9 passed, 1 warning in 2.56s`。
- Audit 烟测：`tests/test_admin_api.py::TestToolAdmin::test_tools_have_separate_superuser_private_default_template -q`
  -> `1 passed, 1 warning in 1.14s`。
- 静态检查：`python -m compileall api/admin_routes.py api/admin/trace_routes.py api/admin/log_routes.py -q`
  无输出；`git diff --check -- api/admin_routes.py api/admin/trace_routes.py api/admin/log_routes.py tests/test_admin_observability_routes_split.py`
  无输出；`rg -n "asyncio\.run|run_awaitable_sync|from api\.admin_routes|import api\.admin_routes" api/admin/trace_routes.py api/admin/log_routes.py`
  无输出。
- 行数检查：`api/admin_routes.py` 4303 行，`api/admin/trace_routes.py` 274 行，
  `api/admin/log_routes.py` 218 行，`tests/test_admin_observability_routes_split.py`
  144 行。
- 全量：`python -m pytest tests/ -v` ->
  `1523 passed, 6 skipped, 139 warnings in 108.28s`。
- 文档收口提交前复跑：`git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-observability-routes-split.md`
  无输出；`python -m pytest tests/ -v` ->
  `1523 passed, 6 skipped, 139 warnings in 110.56s`。

执行约束：

- 不拆普通 `api/routes.py`。
- 不迁移模型、工具、reply/eval、eval 工作台、`/db/backup` 或 `/db/vacuum`。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime
  输入。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列仍剩 `api/admin_routes.py` 4303 行、`api/routes.py` 2822 行。继续沿
管理端拆分时，下一刀可考虑 Tools 或 Models；推荐先 Tools，Models 需要单独设计配置写入、
Prompt Runtime 间接调用和本地模型加载边界。

## 2026-06-21 Admin Tools 路由拆分

状态：实现、验证和实现阶段提交已完成。`api/admin_routes.py` 已拆出
Admin Tools 管理端路由到 `api/admin/tool_routes.py`；旧 `api.admin_routes`
继续 include 新 router，并 re-export 迁移后的 request model、helper 和 endpoint，
保持旧导入路径、HTTP 路径、admin token monkeypatch、audit action/detail、runtime
preset、生效预览、schema override 和工具覆盖语义兼容。`api/admin_routes.py`
从 4303 行降至 3761 行，新模块 `api/admin/tool_routes.py` 为 601 行。

设计文档：
`docs/superpowers/specs/2026-06-21-admin-tool-routes-split-design.md`。

实现计划：
`.Codex/plans/admin-tool-routes-split.md`。

阶段提交：

- 设计提交：`1b5d58b docs(管理端): 设计工具路由拆分`。
- 计划提交：`0871bb9 docs(计划): 记录工具路由拆分计划`。
- 实现提交：`75f0089 refactor(管理端): 拆分工具路由`。

已完成：

- [x] 新增 `tests/test_admin_tool_routes_split.py`，锁定 endpoint module、
  legacy import、token monkeypatch、重复注册、静态路径顺序和反向导入 / awaitable
  扫描。
- [x] 新增 `api/admin/tool_routes.py`，承载 Tools request model、helper 和
  10 个 `/tools*` 路由。
- [x] `api/admin_routes.py` include `tool_router`，并 re-export 迁移符号。
- [x] 新模块使用 `api.admin.common.verify_admin`、`audit()` 和 `client_ip()`；
  不反向导入 `api.admin_routes`。
- [x] 红灯测试未单独提交；按项目提交门禁，失败状态只作为 TDD 证据记录，
  绿灯后与实现一起提交。

验证记录：

- 红灯：`tests/test_admin_tool_routes_split.py -q` ->
  `4 failed, 2 passed, 21 warnings in 7.55s`；失败点为 endpoint module 仍是
  `api.admin_routes`、新模块不存在，以及旧 `/tools/effective` 顺序落后于动态
  `/{tool_name}` 系列。
- 绿灯：`tests/test_admin_tool_routes_split.py -q` ->
  `6 passed, 21 warnings in 2.17s`。
- 工具行为回归：`tests/test_admin_api.py::TestToolAdmin tests/test_tool_plan.py tests/test_tool_schema_config.py tests/test_final_tools.py -q`
  -> `36 passed, 1 warning in 7.09s`。
- 鉴权与 asyncio 策略回归：`tests/test_admin_api.py::TestAuth tests/test_asyncio_run_policy.py -q`
  -> `9 passed, 1 warning in 2.57s`。
- 静态检查：`python -m compileall api/admin_routes.py api/admin/tool_routes.py -q`
  无输出；`git diff --check -- api/admin_routes.py api/admin/tool_routes.py tests/test_admin_tool_routes_split.py .Codex/plans/admin-tool-routes-split.md`
  无输出；`rg -n "from api\.admin_routes|import api\.admin_routes|asyncio\.run|run_awaitable_sync" api/admin/tool_routes.py`
  无输出。
- 行数检查：`api/admin_routes.py` 3761 行，`api/admin/tool_routes.py` 601 行，
  `tests/test_admin_tool_routes_split.py` 136 行。
- 全量：`python -m pytest tests/ -v` ->
  `1529 passed, 6 skipped, 139 warnings in 109.75s`。

执行约束：

- 不拆普通 `api/routes.py`。
- 不迁移 Admin Models、reply/eval、eval 工作台、settings、`/db/backup` 或
  `/db/vacuum`。
- 不重构 `core.runtime_tool_service`，不改变工具默认值、runtime preset、生效预览、
  schema override、platform override 或 runtime tool decision 语义。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime
  输入。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列仍剩 `api/admin_routes.py` 3761 行、`api/routes.py` 2822 行。继续沿
管理端拆分时，下一刀可考虑 Models，但需要单独设计 provider 凭据、route test、
Prompt Runtime 间接调用和本地模型加载边界；切普通 API 前应先设计 `verify_token`
共享兼容层。

## 2026-06-21 Admin Models 路由拆分

状态：设计、计划、实现、验证和实现阶段提交已完成。`api/admin_routes.py` 已拆出
Admin Models 管理端路由到 `api/admin/model_routes.py`；旧 `api.admin_routes`
继续 include 新 router，并 re-export 迁移后的 request model、常量、helper 和
endpoint，保持旧导入路径、HTTP 路径、admin token monkeypatch、审计语义、
provider/catalog/route test、本地组件测试、TimingGate 稳定性测试和模型健康检查
行为兼容。`/model-replies` 仍留在 `api.admin_routes`，作为回复日志观测边界。
`api/admin_routes.py` 从 3761 行降至 2647 行，新模块
`api/admin/model_routes.py` 为 1178 行。

设计文档：
`docs/superpowers/specs/2026-06-21-admin-model-routes-split-design.md`。

实现计划：
`.Codex/plans/admin-model-routes-split.md`。

阶段提交：

- 设计提交：`08be4d6 docs(管理端): 设计模型路由拆分`。
- 计划提交：`f5ed550 docs(计划): 记录模型路由拆分计划`。
- 实现提交：`c2966c7 refactor(管理端): 拆分模型路由`。

已完成：

- [x] 新增 `tests/test_admin_model_routes_split.py`，锁定 19 个模型管理 endpoint
  的 endpoint module、legacy import、token monkeypatch、重复注册、`/model-replies`
  父模块归属和反向导入 / awaitable 扫描。
- [x] 新增 `api/admin/model_routes.py`，承载模型状态、chat test、legacy catalog、
  provider 管理、provider catalog、legacy stage route、canonical route 编辑、
  route test、resolved route、available models、本地组件测试 / 预热、TimingGate
  稳定性测试和模型健康检查。
- [x] `api/admin_routes.py` include `model_router`，并 re-export 迁移符号。
- [x] 新模块使用 `api.admin.common.verify_admin`、`audit()`、`audit_request()` 和
  `client_ip()`；不反向导入 `api.admin_routes`。
- [x] 红灯测试未单独提交；按项目提交门禁，失败状态只作为 TDD 证据记录，
  绿灯后与实现一起提交。

验证记录：

- 红灯：`tests/test_admin_model_routes_split.py -q` ->
  `3 failed, 3 passed, 21 warnings in 6.63s`；失败点为 endpoint module 仍是
  `api.admin_routes`、`api.admin.model_routes` 尚不存在，以及
  `api/admin/model_routes.py` 文件不存在。
- 绿灯：`tests/test_admin_model_routes_split.py -q` ->
  `6 passed, 21 warnings in 1.24s`。
- 模型行为回归：
  `tests/test_admin_api.py::TestModelCatalog tests/test_admin_api.py::TestModelRoutes tests/test_admin_api.py::TestModelHealthCheck tests/test_admin_api.py::TestModelRouteV2 -q`
  -> `22 passed, 1 warning in 1.95s`。
- 拆分兼容回归：
  `tests/test_admin_model_routes_split.py tests/test_admin_tool_routes_split.py tests/test_admin_sticker_routes_split.py tests/test_admin_group_memory_routes_split.py tests/test_admin_observability_routes_split.py tests/test_admin_db_browser.py -q`
  -> `41 passed, 21 warnings in 8.18s`。
- 鉴权与 asyncio 策略回归：
  `tests/test_admin_api.py::TestAuth tests/test_asyncio_run_policy.py tests/test_admin_model_routes_split.py::test_admin_model_routes_do_not_import_parent_admin_routes_or_sync_awaitable -q`
  -> `10 passed, 1 warning in 2.63s`。
- 静态检查：`python -m compileall api/admin_routes.py api/admin/model_routes.py -q`
  无输出；`git diff --check -- api/admin_routes.py api/admin/model_routes.py tests/test_admin_model_routes_split.py .Codex/plans/admin-model-routes-split.md`
  无输出；`rg -n "from api\.admin_routes|import api\.admin_routes|asyncio\.run|run_awaitable_sync" api/admin/model_routes.py`
  无输出，退出码为 1。
- 行数检查：`api/admin_routes.py` 2647 行，`api/admin/model_routes.py` 1178 行，
  `tests/test_admin_model_routes_split.py` 159 行。
- 独立只读审查：子 agent 核对三文件后未发现 `[必须修复]` 或 `[建议修改]` 问题。
- 全量：`python -m pytest tests/ -v` ->
  `1535 passed, 6 skipped, 139 warnings in 109.68s`。
- 文档收口提交前复跑：`git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-model-routes-split.md`
  无输出；`python -m pytest tests/ -v` ->
  `1535 passed, 6 skipped, 139 warnings in 109.35s`。

执行约束：

- 不拆普通 `api/routes.py`。
- 不迁移 `/model-replies`、reply/eval、eval 工作台、settings、`/db/backup` 或
  `/db/vacuum`。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime
  输入。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列仍剩 `api/admin_routes.py` 2647 行、`api/routes.py` 2822 行。继续沿
管理端拆分时，下一刀可考虑 Settings / Reply Eval / Eval Workbench 等更小边界；
切普通 API 前应先设计 `verify_token` 共享兼容层。

## 2026-06-21 Admin Reply Eval 路由拆分

状态：设计、计划、实现、验证和实现阶段提交已完成。`api/admin_routes.py` 已拆出
Reply 手动测试与 Reply Eval 管理端路由到 `api/admin/reply_routes.py`；旧
`api.admin_routes` 继续 include 新 router，并 re-export 迁移后的 request model、
helper 和 endpoint，保持旧导入路径、HTTP 路径、admin token monkeypatch、
Prompt Runtime metadata、评测 metrics、traffic 聚合和 `/reply-eval/runs`
静态路由顺序兼容。`/model-replies`、`/evals/*`、`/settings/*` 和 `/db/*`
仍留在父模块或既有子模块，不进入本阶段。`api/admin_routes.py` 从 2647 行降至
1935 行，新模块 `api/admin/reply_routes.py` 为 754 行。

设计文档：
`docs/superpowers/specs/2026-06-21-admin-reply-routes-split-design.md`。

实现计划：
`.Codex/plans/admin-reply-routes-split.md`。

阶段提交：

- 设计提交：`73f6f81 docs(管理端): 设计回复评测路由拆分`。
- 计划提交：`2996862 docs(计划): 记录回复评测路由拆分计划`。
- 实现提交：`fb5186d refactor(管理端): 拆分回复评测路由`。

已完成：

- [x] 新增 `tests/test_admin_reply_routes_split.py`，锁定 11 个 Reply route 的
  endpoint module、legacy import、token monkeypatch、重复注册、`/reply-eval/runs`
  静态路由顺序、协程边界和反向导入 / awaitable 扫描。
- [x] 新增 `api/admin/reply_routes.py`，承载 `ReplyTestRunRequest`、Reply Eval case /
  run request model、Reply contract 聚合 helper、手动测试 endpoint、case CRUD、
  generated preview / save、eval run、traffic 聚合和 run 查询。
- [x] `api/admin_routes.py` include `reply_router`，并 re-export 迁移符号。
- [x] 新模块使用 `api.admin.common.verify_admin` 和 `core.database.get_db`；不反向导入
  `api.admin_routes`。
- [x] 红灯测试未单独提交；按项目提交门禁，失败状态只作为 TDD 证据记录，
  绿灯后与实现一起提交。

验证记录：

- 红灯：`tests/test_admin_reply_routes_split.py -q` ->
  `4 failed, 3 passed, 21 warnings in 6.30s`；失败点为 endpoint module 仍是
  `api.admin_routes`、`api.admin.reply_routes` 尚不存在，以及
  `api/admin/reply_routes.py` 文件不存在。
- 绿灯：`tests/test_admin_reply_routes_split.py -q` ->
  `7 passed, 21 warnings in 1.27s`。
- Reply 行为回归：
  `tests/test_reply_admin.py tests/test_admin_model_routes_split.py::test_model_replies_stays_in_parent_admin_routes -q`
  -> `15 passed, 21 warnings in 2.78s`。
- 拆分兼容回归：
  `tests/test_admin_reply_routes_split.py tests/test_admin_model_routes_split.py tests/test_admin_tool_routes_split.py tests/test_admin_sticker_routes_split.py tests/test_admin_group_memory_routes_split.py tests/test_admin_observability_routes_split.py tests/test_admin_db_browser.py -q`
  -> `48 passed, 21 warnings in 9.43s`。
- 鉴权与 asyncio 策略回归：
  `tests/test_admin_api.py::TestAuth tests/test_asyncio_run_policy.py tests/test_admin_reply_routes_split.py::test_admin_reply_routes_do_not_import_parent_admin_routes_or_sync_awaitable -q`
  -> `10 passed, 1 warning in 2.57s`。
- 静态检查：`python -m compileall api/admin_routes.py api/admin/reply_routes.py -q`
  无输出；`git diff --check -- api/admin_routes.py api/admin/reply_routes.py tests/test_admin_reply_routes_split.py .Codex/plans/admin-reply-routes-split.md`
  无输出；`rg -n "from api\.admin_routes|import api\.admin_routes|asyncio\.run|run_awaitable_sync" api/admin/reply_routes.py`
  无输出，退出码为 1。
- 行数检查：`api/admin_routes.py` 1935 行，`api/admin/reply_routes.py` 754 行，
  `tests/test_admin_reply_routes_split.py` 151 行。
- 全量：`python -m pytest tests/ -v` ->
  `1542 passed, 6 skipped, 139 warnings in 112.96s`。

执行约束：

- 不拆普通 `api/routes.py`。
- 不迁移 `/model-replies`、`/evals/*`、`/settings/*`、`/db/*`、`/db/backup` 或
  `/db/vacuum`。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime
  输入。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列仍剩 `api/admin_routes.py` 1935 行、`api/routes.py` 2822 行。继续沿
管理端拆分时，下一刀可考虑 Eval Workbench、Runtime / Overview 或 Settings；
切普通 API 前应先设计 `verify_token` 共享兼容层。

## 2026-06-21 Admin Eval Workbench 路由拆分

状态：设计、计划、实现、验证和实现阶段提交已完成。`api/admin_routes.py` 已拆出
Eval Workbench 管理端路由到 `api/admin/eval_routes.py`；旧 `api.admin_routes`
继续 include 新 router，并 re-export 迁移后的 request model、常量、helper 和
endpoint，保持旧导入路径、HTTP 路径、admin token monkeypatch、
`TIMING_TUNING_PROPOSAL_REPORT` 父模块 monkeypatch、candidate 静态路由顺序和
`/evals/runs` 静态路由顺序兼容。`/model-replies`、Runtime / Overview、
`/settings/*`、Configs、Prompt effective preview、Block / ContentBlock 和 DB 运维
仍留在父模块或既有子模块，不进入本阶段。`api/admin_routes.py` 从 1935 行降至
1390 行，新模块 `api/admin/eval_routes.py` 为 614 行。

设计文档：
`docs/superpowers/specs/2026-06-21-admin-eval-routes-split-design.md`。

实现计划：
`.Codex/plans/admin-eval-routes-split.md`。

阶段提交：

- 设计提交：`febd9f6 docs(管理端): 设计评测工作台路由拆分`。
- 计划提交：`7c94a00 docs(计划): 记录评测工作台路由拆分计划`。
- 实现提交：`c2f042b refactor(管理端): 拆分评测工作台路由`。

已完成：

- [x] 新增 `tests/test_admin_eval_routes_split.py`，锁定 21 个 Eval Workbench route
  的 endpoint module、legacy import、token monkeypatch、proposal report monkeypatch、
  重复注册、candidate 静态路由顺序、runs 静态路由顺序、协程边界和反向导入 /
  awaitable 扫描。
- [x] 新增 `api/admin/eval_routes.py`，承载 expected contract、TimingGate 调参提案、
  proposal review、candidate list / preflight / batch audit / trend / get / patch /
  label / triage / promote、sample run / status、suite run 和 run 查询。
- [x] `api/admin_routes.py` include `eval_router`，并 re-export 迁移符号。
- [x] 新模块使用 `api.admin.common.verify_admin`、`audit_request()` 和 `client_ip()`；
  不反向导入 `api.admin_routes`。
- [x] 红灯测试未单独提交；按项目提交门禁，失败状态只作为 TDD 证据记录，
  绿灯后与实现一起提交。

验证记录：

- 红灯：`tests/test_admin_eval_routes_split.py -q` ->
  `4 failed, 5 passed, 21 warnings in 6.89s`；失败点为 endpoint module 仍是
  `api.admin_routes`、`api.admin.eval_routes` 尚不存在，以及
  `api/admin/eval_routes.py` 文件不存在。
- 绿灯：`tests/test_admin_eval_routes_split.py -q` ->
  `9 passed, 21 warnings in 1.53s`。
- Eval / Timing proposal 行为回归：
  `tests/test_eval_candidate_contract.py tests/test_timing_tuning_proposal_admin.py -q`
  -> `40 passed, 21 warnings in 7.05s`。
- WebUI 与 asyncio 策略回归：
  `tests/test_webui_admin_redesign.py tests/test_asyncio_run_policy.py -q`
  -> `26 passed, 1 warning in 1.87s`。
- 静态检查：`python -m compileall api/admin_routes.py api/admin/eval_routes.py -q`
  无输出；`git diff --check -- api/admin_routes.py api/admin/eval_routes.py tests/test_admin_eval_routes_split.py .Codex/plans/admin-eval-routes-split.md docs/superpowers/specs/2026-06-21-admin-eval-routes-split-design.md`
  无输出；`rg -n "from api\.admin_routes|import api\.admin_routes|asyncio\.run|run_awaitable_sync" api/admin/eval_routes.py`
  无输出，退出码为 1。
- 行数检查：`api/admin_routes.py` 1390 行，`api/admin/eval_routes.py` 614 行，
  `tests/test_admin_eval_routes_split.py` 194 行。
- 全量：`python -m pytest tests/ -v` ->
  `1551 passed, 6 skipped, 139 warnings in 112.38s`。

执行约束：

- 不拆普通 `api/routes.py`。
- 不抽 `verify_token` common auth。
- 不迁移 `/model-replies`、Runtime / Overview、`/settings/*`、Configs、
  Prompt effective preview、Block / ContentBlock、DB backup / vacuum。
- 不改变 eval storage、scorer、runner、dataset 文件格式或 WebUI 页面。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime
  输入。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列仍剩 `api/admin_routes.py` 1390 行、`api/routes.py` 2822 行。继续沿
管理端拆分时，下一刀可考虑 Runtime / Overview 或 Settings；切普通 API 前应先设计
`verify_token` 共享兼容层。

## 2026-06-21 Admin Runtime / Overview 路由拆分

状态：设计、计划、红灯测试、实现、验证和实现阶段提交已完成。`api/admin_routes.py`
已拆出 Runtime / Overview 管理端路由到 `api/admin/runtime_routes.py`；旧
`api.admin_routes` 继续 include 新 router，并 re-export 迁移后的 request model、
Runtime 专属 helper 和 endpoint，保持旧导入路径、HTTP 路径、admin token
monkeypatch、Group Memory 子路由先于 `/groups/{group_id:path}` catch-all 的顺序、
overview / groups / TimingGate events response shape 和 `timing_gate_test()` 协程边界。
Settings、Configs、Prompt effective preview、Block / ContentBlock、DB backup /
vacuum、`/model-replies` 和普通 `api/routes.py` 不进入本阶段。`api/admin_routes.py`
从 1390 行降至 1009 行，新模块 `api/admin/runtime_routes.py` 为 462 行。

设计文档：
`docs/superpowers/specs/2026-06-21-admin-runtime-routes-split-design.md`。

实现计划：
`.Codex/plans/admin-runtime-routes-split.md`。

阶段提交：

- 设计提交：`1c2745c docs(管理端): 设计运行态路由拆分`。
- 计划提交：`0ac3fa7 docs(计划): 记录运行态路由拆分计划`。
- 红灯测试提交：`d394766 test(管理端): 锁定运行态路由拆分契约`。
- 实现提交：`d6a05bf refactor(管理端): 拆分运行态路由`。

已完成：

- [x] 新增 `tests/test_admin_runtime_routes_split.py`，锁定 5 个 Runtime / Overview
  route 的 endpoint module、legacy import、token monkeypatch、重复注册、
  `/groups` 静态路由顺序、Group Memory 子路由顺序、协程边界、Pydantic 约束和
  反向导入 / awaitable 扫描。
- [x] 新增 `api/admin/runtime_routes.py`，承载 `TimingGateTestRequest`、Runtime
  snapshot、TimingGate event / stats helper、overview、groups list / detail、
  TimingGate events 和 TimingGate 手测 endpoint。
- [x] `api/admin_routes.py` include `runtime_router`，并 re-export 迁移符号。
- [x] 新模块使用 `api.admin.common.verify_admin` 和 `core.database.get_db`；不反向导入
  `api.admin_routes`。
- [x] `group_memory_router` 继续早于 `runtime_router` include，避免
  `/groups/{group_id:path}` catch-all 吞掉 Group Memory 子路由。
- [x] 红灯测试单独提交，符合用户「每完成一个阶段性改动都 commit 一次」的要求。

验证记录：

- 红灯：`tests/test_admin_runtime_routes_split.py -q` ->
  `5 failed, 4 passed, 21 warnings in 6.57s`；失败点为 endpoint module 仍是
  `api.admin_routes`、`api.admin.runtime_routes` 尚不存在，以及
  `api/admin/runtime_routes.py` 文件不存在。
- 绿灯：`tests/test_admin_runtime_routes_split.py -q` ->
  `9 passed, 21 warnings in 1.15s`。
- 管理端行为 / 顺序 / asyncio 策略回归：
  `tests/test_admin_api.py tests/test_admin_group_memory_routes_split.py tests/test_asyncio_run_policy.py -q`
  -> `86 passed, 21 warnings in 8.88s`。
- 静态检查：`python -m compileall api/admin_routes.py api/admin/runtime_routes.py`
  无输出；`git diff --check` 无输出；
  `rg -n "from api\.admin_routes|import api\.admin_routes|asyncio\.run|run_awaitable_sync" api/admin/runtime_routes.py`
  无命中，退出码为 1。
- 行数检查：`api/admin_routes.py` 1009 行，`api/admin/runtime_routes.py` 462 行，
  `tests/test_admin_runtime_routes_split.py` 143 行。
- 全量：`python -m pytest tests/ -v` ->
  `1560 passed, 6 skipped, 139 warnings in 111.58s`。

执行约束：

- 不拆普通 `api/routes.py`。
- 不抽 `verify_token` common auth。
- 不迁移 Settings、Configs、Prompt effective preview、Block / ContentBlock、
  DB backup / vacuum 或 `/model-replies`。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime
  输入。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列仍剩 `api/admin_routes.py` 1009 行、`api/routes.py` 2822 行。继续沿
管理端拆分时，下一刀可考虑 Settings；切普通 API 前应先设计 `verify_token` 共享兼容层。

## 2026-06-21 Admin Chat Config 路由拆分

状态：设计、计划、红灯测试、实现、验证和实现阶段提交已完成。`api/admin_routes.py`
已拆出 Block / ContentBlock / ChatStreamConfig 管理端路由到
`api/admin/chat_config_routes.py`；旧 `api.admin_routes` 继续 include 新 router，并
re-export 迁移后的 request model、helper 和 15 个 endpoint，保持旧导入路径、HTTP
路径、admin token monkeypatch、Block / ContentBlock / Config response shape、audit
action/detail、`/configs` 静态路由顺序和 `/block-rules/test` 静态路由顺序。
Prompt effective preview、`/model-replies`、DB backup / vacuum、Settings 和普通
`api/routes.py` 不进入本阶段。`api/admin_routes.py` 从 1009 行降至 632 行，新模块
`api/admin/chat_config_routes.py` 为 396 行。

设计文档：
`docs/superpowers/specs/2026-06-21-admin-chat-config-routes-split-design.md`。

实现计划：
`.Codex/plans/admin-chat-config-routes-split.md`。

阶段提交：

- 设计提交：`c3f2f7c docs(管理端): 设计聊天配置路由拆分`。
- 计划提交：`94606ec docs(计划): 记录聊天配置路由拆分计划`。
- 红灯测试提交：`ec9ef63 test(管理端): 锁定聊天配置路由拆分契约`。
- 实现提交：`06d8aa6 refactor(管理端): 拆分聊天配置路由`。

已完成：

- [x] 新增 `tests/test_admin_chat_config_routes_split.py`，锁定 15 个 Chat Config route
  的 endpoint module、legacy import、token monkeypatch、重复注册、`/configs` 静态
  路由顺序、`/block-rules/test` 静态路由顺序和反向导入 / awaitable 扫描。
- [x] 新增 `api/admin/chat_config_routes.py`，承载 BlockRule、ContentBlockRule、
  ChatStreamConfig 的 request model、response helper、chat stream 列表、配置默认值、
  effective configs 合并和 CRUD endpoint。
- [x] `api/admin_routes.py` include `chat_config_router`，并 re-export 迁移符号。
- [x] 新模块使用 `api.admin.common.verify_admin`、`audit()`、`audit_request()` 和
  `client_ip()`；不反向导入 `api.admin_routes`。
- [x] `/block-rules/test` 已移动到动态 `/block-rules/{rule_id}` 前，避免被动态路由吞掉。
- [x] 红灯测试单独提交，符合用户「每完成一个阶段性改动都 commit 一次」的要求。

验证记录：

- 红灯：`tests/test_admin_chat_config_routes_split.py -q` ->
  `4 failed, 3 passed, 21 warnings in 6.32s`；失败点为 endpoint module 仍是
  `api.admin_routes`、`api.admin.chat_config_routes` 尚不存在、`/block-rules/test`
  仍排在动态路由后，以及 `api/admin/chat_config_routes.py` 文件不存在。
- 绿灯：`tests/test_admin_chat_config_routes_split.py -q` ->
  `7 passed, 21 warnings in 1.19s`。
- 行为与相邻回归：
  `tests/test_admin_api.py::TestBlockRule`、
  `tests/test_admin_api.py::TestPrivateBlockFlow::test_blocked_user_chat_writes_log_with_files`、
  `tests/test_api.py::test_effective_configs_*`、
  `tests/test_admin_runtime_routes_split.py`、
  `tests/test_admin_group_memory_routes_split.py`、
  `tests/test_admin_tool_routes_split.py`、
  `tests/test_asyncio_run_policy.py` ->
  `30 passed, 21 warnings in 7.53s`。
- 静态检查：`python -B -m compileall api/admin_routes.py api/admin/chat_config_routes.py`
  成功；`git diff --check -- api/admin_routes.py api/admin/chat_config_routes.py` 无输出；
  `rg -n "from api\.admin_routes|import api\.admin_routes|asyncio\.run|run_awaitable_sync" api/admin/chat_config_routes.py`
  无命中，退出码为 1。
- 行数检查：`api/admin_routes.py` 632 行，`api/admin/chat_config_routes.py` 396 行，
  `tests/test_admin_chat_config_routes_split.py` 160 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1567 passed, 6 skipped, 139 warnings in 115.24s`。

执行约束：

- 不拆普通 `api/routes.py`。
- 不抽 `verify_token` common auth。
- 不迁移 Prompt effective preview、`/model-replies`、DB backup / vacuum 或 Settings。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime
  输入。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列当前只剩 `api/routes.py` 2822 行。继续拆普通 API 前，应先设计
`verify_token` 共享兼容层并明确 `/chat`、`/group/message`、搜索 / 回忆等端点的模块边界。

## 2026-06-21 普通 API Tasks 路由拆分

状态：设计、计划、红灯测试、鉴权兼容层、任务路由拆分、验证和实现阶段提交均已完成。
本阶段切回普通 `api/routes.py`，但没有拆 `/chat` 或 `/group/message` 主链路。已先把普通
API `verify_token` 下沉到 `api/common_auth.py`，保持 `api.routes.verify_token` 与 common
auth 同一函数对象，再把低耦合 `/tasks*` 定时任务路由拆到 `api/task_routes.py`。
旧 `api.routes.NANOBOT_API_TOKEN` monkeypatch、`app.dependency_overrides[routes.verify_token]`、
`/tasks*` HTTP 契约、push envelope 行为、`run_scheduled_task_now()` 协程边界和旧导入路径
均保持兼容。`api/routes.py` 从 2822 行降至 2712 行，新模块 `api/task_routes.py` 为
169 行。

设计文档：
`docs/superpowers/specs/2026-06-21-api-task-routes-split-design.md`。

实现计划：
`.Codex/plans/api-task-routes-split.md`。

阶段提交：

- 设计提交：`835ebfd docs(普通API): 设计任务路由拆分`。
- 计划提交：`16d3809 docs(计划): 记录任务路由拆分计划`。
- 红灯测试提交：`55c114d test(普通API): 锁定任务路由拆分契约`。
- 鉴权兼容层提交：`3266ea1 refactor(普通API): 抽出鉴权兼容层`。
- 任务路由拆分提交：`57f90fd refactor(普通API): 拆分任务路由`。
- 文档收口提交：随本次 `docs(计划): 收口任务路由拆分` 完成。

计划列表：

- [x] 只读审计 `api/routes.py` 路由 / helper 分组、旧 monkeypatch 和 route order 风险。
- [x] 只读审计现有 admin split 测试模板与普通 API 鉴权兼容要求。
- [x] 设计 common auth + `/tasks*` 第一刀拆分方案。
- [x] 写入设计文档并完成全量验证后提交。
- [x] 写入实现计划并提交。
- [x] 补普通 API common auth 与 task route split 红灯测试并提交。
- [x] 抽出 `api/common_auth.py`，保持 `api.routes.verify_token` 对象身份与旧 token
  monkeypatch 兼容，并提交。
- [x] 拆出 `api/task_routes.py`，保持 `/tasks*` HTTP 契约、push envelope 行为、
  `run_scheduled_task_now()` 协程边界和旧导入兼容，并提交。
- [x] 更新 `docs/todo.md`、本 walkthrough 和计划执行记录，完成最终验证后提交文档收口。

设计阶段验证记录：

- 文档自检：模板残留和乱码扫描无命中。
- 空白检查：`git diff --check -- docs/superpowers/specs/2026-06-21-api-task-routes-split-design.md`
  无输出。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1567 passed, 6 skipped, 139 warnings in 112.06s`。

实现阶段验证记录：

- 红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_task_routes_split.py`
  -> `6 failed, 4 passed, 21 warnings in 6.37s`；失败点为 `api.common_auth`
  尚不存在、`api.task_routes` 尚不存在、`/tasks*` endpoint module 仍为 `api.routes`，
  以及 `api/task_routes.py` 文件不存在。
- 鉴权绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_task_routes_split.py::test_api_verify_token_is_shared_common_auth_object tests/test_api_task_routes_split.py::test_api_common_auth_uses_legacy_api_routes_token_monkeypatch tests/test_api.py::test_api_auth_no_token_configured_returns_503 tests/test_api.py::test_api_auth_missing_or_wrong_token_returns_401 tests/test_api.py::test_api_auth_accepts_valid_bearer_token`
  -> `5 passed, 1 warning in 0.77s`。
- 鉴权后保留拆分红灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_task_routes_split.py`
  -> `4 failed, 6 passed, 21 warnings in 6.47s`；剩余失败均指向 `/tasks*`
  尚未迁移到 `api.task_routes`。
- Split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_task_routes_split.py`
  -> `10 passed, 21 warnings in 1.11s`。
- 行为与相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_push_envelope.py::test_run_scheduled_task_now_uses_push_envelope tests/test_schedule_task_tool.py tests/test_api.py::test_api_auth_no_token_configured_returns_503 tests/test_api.py::test_api_auth_missing_or_wrong_token_returns_401 tests/test_api.py::test_api_auth_accepts_valid_bearer_token tests/test_asyncio_run_policy.py`
  -> `9 passed, 21 warnings in 3.13s`。
- 静态检查：`python -B -m compileall api/routes.py api/common_auth.py api/task_routes.py`
  成功；`rg -n "from api\.routes|import api\.routes|asyncio\.run|run_awaitable_sync" api/task_routes.py`
  无命中，退出码为 1；`git diff --check -- api/routes.py api/common_auth.py api/task_routes.py tests/test_api_task_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 2712 行，`api/task_routes.py` 169 行，
  `tests/test_api_task_routes_split.py` 180 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1577 passed, 6 skipped, 139 warnings in 112.05s`。

执行约束：

- 不拆 `/chat`。
- 不拆 `/group/message`。
- 不拆 history、context、log、sticker/media、group timing、search/render、agent step、
  evolution、memory 或 model 路由。
- 不迁移 `/health`。
- 不修改 `server.py`。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query`、conversation 结构、
  Prompt Runtime 输入或工具输出契约。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列当前仍只剩 `api/routes.py`，行数为 2712。下一刀建议优先从
evolution、memory 或 models 路由中选择低耦合边界；继续避开 `/chat` 与
`/group/message` 主链路，除非先完成更细的设计文档和红灯契约。

## 2026-06-21 普通 API Memory 路由拆分

状态：设计、计划、红灯测试、记忆路由拆分、验证和实现阶段提交均已完成。本阶段继续
拆普通 `api/routes.py`，但没有触碰 `/chat` 或 `/group/message` 主链路。已把
`/memory/digests`、`/memory/digests/run` 和 `/memory/recall` 迁移到
`api/memory_routes.py`；旧 `api.routes` 继续 re-export `MemoryDigestRunRequest`、
memory endpoint 和 legacy helper。`_safe_meta` 保留在父模块，因为聊天落库路径仍使用它。
`/memory*` HTTP 契约、日期过滤 HTTP 400、AI daily tool log 召回、旧 token
monkeypatch、dependency override 和禁止同步 awaitable 桥的约束均保持兼容。
`api/routes.py` 从 2712 行降至 2523 行，新模块 `api/memory_routes.py` 为 216 行。

设计文档：
`docs/superpowers/specs/2026-06-21-api-memory-routes-split-design.md`。

实现计划：
`.Codex/plans/api-memory-routes-split.md`。

阶段提交：

- 设计提交：`e322638 docs(普通API): 设计记忆路由拆分`。
- 计划提交：`6657a78 docs(计划): 记录记忆路由拆分计划`。
- 红灯测试提交：`0052793 test(普通API): 锁定记忆路由拆分契约`。
- 记忆路由拆分提交：`9536b18 refactor(普通API): 拆分记忆路由`。
- 文档收口提交：随本次 `docs(计划): 收口记忆路由拆分` 完成。

计划列表：

- [x] 只读审计 memory、models、evolution 三个候选边界和旧导入 / monkeypatch 风险。
- [x] 选择 memory 作为本阶段拆分目标，记录 `models` 风险最低但行数收益较小的取舍。
- [x] 写入设计文档并提交。
- [x] 写入实现计划并提交。
- [x] 补普通 API memory route split 红灯测试并提交。
- [x] 拆出 `api/memory_routes.py`，保持 `/memory*` HTTP 契约、旧导入兼容和
  `_safe_meta` 父模块边界，并提交。
- [x] 更新 `docs/todo.md`、本 walkthrough 和计划执行记录，完成最终验证后提交文档收口。

验证记录：

- 红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_memory_routes_split.py`
  -> `3 failed, 4 passed, 21 warnings in 6.87s`；失败点为 `/memory*` endpoint module
  仍是 `api.routes`、`api.memory_routes` 尚不存在，以及 `api/memory_routes.py`
  文件不存在。
- Split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_memory_routes_split.py`
  -> `7 passed, 21 warnings in 1.08s`。
- Memory 行为回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_memory_digest.py`
  -> `32 passed, 21 warnings in 2.51s`。
- 相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_asyncio_run_policy.py tests/test_api_task_routes_split.py`
  -> `13 passed, 21 warnings in 2.65s`。
- 静态检查：`python -B -m compileall api/routes.py api/memory_routes.py` 成功；
  `rg -n "from api\.routes|import api\.routes|asyncio\.run|run_awaitable_sync" api/memory_routes.py`
  无命中，退出码为 1；`git diff --check -- api/routes.py api/memory_routes.py tests/test_api_memory_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 2523 行，`api/memory_routes.py` 216 行，
  `tests/test_api_memory_routes_split.py` 139 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1584 passed, 6 skipped, 139 warnings in 114.10s`。

执行约束：

- 不拆 `/chat`。
- 不拆 `/group/message`。
- 不拆 history、context、log、sticker/media、group timing、search/render、agent step、
  evolution route-only、models 或 `/health`。
- 不修改 `server.py`。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query`、conversation 结构、
  Prompt Runtime 输入或工具输出契约。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列当前仍只剩 `api/routes.py`，行数为 2523。下一刀可优先拆
`models` 路由以获得最低风险的小步进展，或拆 evolution route-only 但必须保留父模块
`init_legacy_memory`、`memory` 和 `/chat` / `/log` 自动触发 `evolution_task` 的导入边界。
继续避开 `/chat` 与 `/group/message` 主链路，除非先完成更细的设计文档和红灯契约。

## 2026-06-21 普通 API Models 路由拆分

状态：设计、计划、红灯测试、模型路由拆分、验证和实现阶段提交均已完成。本阶段继续
拆普通 `api/routes.py`，但没有触碰 `/chat` 或 `/group/message` 主链路。已把
`/models/list` 和 `/models/sync` 迁移到 `api/model_routes.py`；旧 `api.routes`
继续 re-export `ModelSyncRequest`、`list_models()` 和 `sync_models()`。`/models*`
HTTP 契约、provider / tier 过滤、缺少 `NEW_API_KEY` 的 400 响应、`force` 透传、
旧 token monkeypatch 和 `sync_models()` 协程边界均保持兼容。`api/routes.py` 从
2523 行降至 2484 行，新模块 `api/model_routes.py` 为 57 行。

设计文档：
`docs/superpowers/specs/2026-06-21-api-model-routes-split-design.md`。

实现计划：
`.Codex/plans/api-model-routes-split.md`。

阶段提交：

- 设计提交：`7887681 docs(普通API): 设计模型路由拆分`。
- 计划提交：`44d344d docs(计划): 记录模型路由拆分计划`。
- 红灯测试提交：`6e5291f test(普通API): 锁定模型路由拆分契约`。
- 模型路由拆分提交：`6e1a2d4 refactor(普通API): 拆分模型路由`。
- 文档收口提交：随本次 `docs(计划): 收口模型路由拆分` 完成。

计划列表：

- [x] 只读审计 models 和 evolution 两个候选边界，确认 models 是当前最低风险下一刀。
- [x] 写入设计文档并提交。
- [x] 写入实现计划并提交。
- [x] 补普通 API model route split 红灯测试并提交。
- [x] 更新 memory split 测试中仍留在父模块的尾部路由列表。
- [x] 拆出 `api/model_routes.py`，保持 `/models*` HTTP 契约、旧导入兼容、旧 token
  monkeypatch 和 `sync_models()` 协程边界，并提交。
- [x] 更新 `docs/todo.md`、本 walkthrough 和计划执行记录，完成最终验证后提交文档收口。

验证记录：

- 红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_model_routes_split.py tests/test_api_memory_routes_split.py`
  -> `6 failed, 11 passed, 21 warnings in 7.79s`；失败点为 `api.model_routes`
  尚不存在、models endpoint module 仍是 `api.routes`，以及 `api/model_routes.py`
  文件不存在。
- Split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_model_routes_split.py tests/test_api_memory_routes_split.py`
  -> `17 passed, 21 warnings in 2.71s`。
- 相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_asyncio_run_policy.py tests/test_api_task_routes_split.py`
  -> `13 passed, 21 warnings in 2.61s`。
- 静态检查：`python -B -m compileall api/routes.py api/model_routes.py` 成功；
  `rg -n "from api\.routes|import api\.routes|asyncio\.run|run_awaitable_sync" api/model_routes.py`
  无命中，退出码为 1；`git diff --check -- api/routes.py api/model_routes.py tests/test_api_model_routes_split.py tests/test_api_memory_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 2484 行，`api/model_routes.py` 57 行，
  `tests/test_api_model_routes_split.py` 189 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1594 passed, 6 skipped, 139 warnings in 114.82s`。

执行约束：

- 不拆 `/chat`。
- 不拆 `/group/message`。
- 不拆 history、context、log、sticker/media、group timing、search/render、agent step、
  evolution route-only 或 `/health`。
- 不修改 `server.py`。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query`、conversation 结构、
  Prompt Runtime 输入或工具输出契约。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列当前仍只剩 `api/routes.py`，行数为 2484。下一刀可优先拆
evolution route-only，但必须保留父模块 `init_legacy_memory`、`memory` 和 `/chat` /
`/log` 自动触发 `evolution_task` 的导入边界；也可以继续寻找 stickers/media、
history/context/log、agent-step/search/render 等更大但低耦合的尾部边界。继续避开
`/chat` 与 `/group/message` 主链路，除非先完成更细的设计文档和红灯契约。

## 2026-06-21 普通 API Evolution 路由拆分

状态：设计、计划、红灯测试、进化路由拆分、验证和实现阶段提交均已完成。本阶段继续
拆普通 `api/routes.py`，但没有迁移 `/chat`、`/group/message`、`/log` 或 legacy
memory 初始化链路。已把手动 `/evolution/trigger` HTTP 层迁移到
`api/evolution_routes.py`；旧 `api.routes` 继续 re-export
`EvolutionTriggerRequest` 和 `trigger_evolution()`。手动触发的 path、method、鉴权、
请求体、response shape、旧 token monkeypatch 和同步 `BackgroundTasks.add_task()`
排队边界均保持兼容。父模块继续保留 `evolution_task`、`EVOLUTION_THRESHOLD`、
`SQLiteMemory`、`memory`、`init_legacy_memory()`、`_persist_chat_turn()` 和 `/health`，
供 `/chat` / `/log` 自动触发与启动生命周期继续使用。`api/routes.py` 从 2484 行降至
2469 行，新模块 `api/evolution_routes.py` 为 33 行。

设计文档：
`docs/superpowers/specs/2026-06-21-api-evolution-routes-split-design.md`。

实现计划：
`.Codex/plans/api-evolution-routes-split.md`。

阶段提交：

- 设计提交：`a82e342 docs(普通API): 设计进化路由拆分`。
- 设计勘误提交：`99581b7 docs(普通API): 修正进化路由验证引用`。
- 计划提交：`1e95c88 docs(计划): 记录进化路由拆分计划`。
- 红灯测试提交：`68b5c72 test(普通API): 锁定进化路由拆分契约`。
- 进化路由拆分提交：`d29a462 refactor(普通API): 拆分进化路由`。
- 文档收口提交：随本次 `docs(计划): 收口进化路由拆分` 完成。

计划列表：

- [x] 审计下一刀普通 API 拆分候选并选择 evolution route-only 边界。
- [x] 写入设计文档并提交。
- [x] 写入实现计划并提交。
- [x] 补普通 API evolution route split 红灯测试并提交。
- [x] 拆出 `api/evolution_routes.py`，保持旧导入兼容、旧 token monkeypatch、
  同步后台排队边界和父模块自动触发边界，并提交。
- [x] 更新 `docs/todo.md`、本 walkthrough 和计划执行记录，完成最终验证后提交文档收口。

验证记录：

- 红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py`
  -> `5 failed, 19 passed, 21 warnings in 8.90s`；失败点为 `/evolution/trigger`
  endpoint module 仍是 `api.routes`、`api.evolution_routes` 尚不存在，以及
  `api/evolution_routes.py` 文件不存在。
- Split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py`
  -> `24 passed, 21 warnings in 3.27s`。
- 相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_task_routes_split.py tests/test_asyncio_run_policy.py tests/test_audit_fixes.py::TestLazyControllerInit::test_legacy_memory_init_exists`
  -> `14 passed, 21 warnings in 2.53s`。
- 静态检查：`python -B -m compileall api/routes.py api/evolution_routes.py` 成功；
  `rg -n "from api\.routes|import api\.routes|asyncio\.run|run_awaitable_sync" api/evolution_routes.py`
  无命中，退出码为 1；`git diff --check -- api/routes.py api/evolution_routes.py tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 2469 行，`api/evolution_routes.py` 33 行，
  `tests/test_api_evolution_routes_split.py` 140 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1601 passed, 6 skipped, 139 warnings in 115.11s`。

执行约束：

- 不拆 `/chat`。
- 不拆 `/group/message`。
- 不拆 `/log`、`/log_ambient`、history、context、sticker/media、group timing、
  search/render、agent step 或 `/health`。
- 不迁移 `init_legacy_memory()`、`memory`、`SQLiteMemory`、`EVOLUTION_THRESHOLD`
  或自动 evolution 触发路径。
- 不修改 `server.py` 或 `bootstrap/lifespan.py`。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query`、conversation 结构、
  Prompt Runtime 输入或工具输出契约。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列当前仍只剩 `api/routes.py`，行数为 2469。下一刀建议优先审计
stickers/media、history/context/log、agent-step/search/render 等更大但低耦合边界；
继续避开 `/chat` 与 `/group/message` 主链路，除非先完成更细的设计文档和红灯契约。

## 2026-06-21 普通 API History / Log 路由拆分

状态：设计、计划、红灯测试、history / log 路由拆分、验证和实现阶段提交均已完成。
本阶段继续拆普通 `api/routes.py`，但没有迁移 `/chat` 或 `/group/message` 主链路。
已把 `/chat/mark-clear`、`/chat/history-summary`、`/chat/compact-history`、`/context`、
`/log`、`/log_ambient` 和 `/search_logs` HTTP 层迁移到
`api/history_log_routes.py`；旧 `api.routes` 继续 re-export `LogRequest`、
`AmbientLogRequest` 和 7 个 endpoint。旧 token monkeypatch、dependency override、
`/log` 同步 `BackgroundTasks.add_task()` evolution 排队边界、SQLite locked retry、
`/log_ambient` 的 `group_*` session / `ambient` role / `processed=1` 合同，以及
`/search_logs` 的 limit / context_size / LIKE 转义和同 session 上下文展开语义均保持兼容。
父模块继续保留 `_persist_chat_turn()`、`_safe_meta()`、`init_legacy_memory()`、`memory`、
`evolution_task`、`EVOLUTION_THRESHOLD`、`/chat`、`/group/message` 和 `/health`。
`api/routes.py` 从 2469 行降至 2134 行，新模块 `api/history_log_routes.py` 为 367 行。

设计文档：
`docs/superpowers/specs/2026-06-21-api-history-log-routes-split-design.md`。

实现计划：
`.Codex/plans/api-history-log-routes-split.md`。

阶段提交：

- 设计提交：`6f93c94 docs(普通API): 设计历史日志路由拆分`。
- 计划提交：`f321d12 docs(计划): 记录历史日志路由拆分计划`。
- 红灯测试提交：`360b099 test(普通API): 锁定历史日志路由拆分契约`。
- 路由拆分提交：`e6aa5f1 refactor(普通API): 拆分历史日志路由`。
- 文档收口提交：随本次 `docs(计划): 收口历史日志路由拆分` 完成。

计划列表：

- [x] 并行审计 stickers/media、history/context/log 与 agent-step/search/render 候选。
- [x] 选择 `history_log_routes` 作为下一刀边界，并写入设计文档。
- [x] 写入实现计划并提交。
- [x] 补普通 API history / log route split 红灯测试并提交。
- [x] 拆出 `api/history_log_routes.py`，保持旧导入兼容、旧 token monkeypatch、
  手动日志 evolution 排队边界和父模块聊天主链路边界，并提交。
- [x] 更新 `docs/todo.md`、本 walkthrough 和计划执行记录，完成最终验证后提交文档收口。

验证记录：

- 红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_history_log_routes_split.py`
  -> `5 failed, 4 passed, 21 warnings in 6.46s`；失败点为 7 个 history / log endpoint
  仍注册在 `api.routes`、`api.history_log_routes` 尚不存在，以及
  `api/history_log_routes.py` 文件不存在。
- Split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_history_log_routes_split.py`
  -> `9 passed, 21 warnings in 1.22s`。
- 相邻 split / SQLite retry 回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_history_log_routes_split.py tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py tests/test_api_task_routes_split.py tests/test_tracing_sqlite_retry.py`
  -> `51 passed, 21 warnings in 5.61s`。
- 主 API 行为回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api.py`
  -> `81 passed, 21 warnings in 16.29s`。
- asyncio 策略回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_asyncio_run_policy.py`
  -> `3 passed, 1 warning in 1.70s`。
- 静态检查：`python -B -m py_compile api/routes.py api/history_log_routes.py tests/test_api_history_log_routes_split.py`
  成功；`api/history_log_routes.py` 无 `from api.routes`、`import api.routes`、
  `asyncio.run` 或 `run_awaitable_sync`；`git diff --check -- api/routes.py api/history_log_routes.py tests/test_api_history_log_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 2134 行，`api/history_log_routes.py` 367 行，
  `tests/test_api_history_log_routes_split.py` 208 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1610 passed, 6 skipped, 139 warnings in 119.08s`。

执行约束：

- 不拆 `/chat`。
- 不拆 `/group/message`。
- 不迁移 `_persist_chat_turn()`、`_safe_meta()`、`init_legacy_memory()`、`memory`、
  `evolution_task` 或 `EVOLUTION_THRESHOLD`。
- 不修改 `server.py` 或 `bootstrap/lifespan.py`。
- 不改变 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约或
  message envelope。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列当前仍只剩 `api/routes.py`，行数为 2134。下一刀可优先拆 media /
stickers 路由，或拆 `chat-step` / `render` 这类 route-only 边界；继续避开 `/chat` 与
`/group/message` 主链路，除非先完成更细的设计文档和红灯契约。

## 2026-06-22 普通 API Sticker / Media 路由拆分

状态：设计、计划、红灯测试、sticker / media 路由拆分、验证和实现阶段提交均已完成。
本阶段继续拆普通 `api/routes.py`，但没有迁移 `/chat` 或 `/group/message` 主链路。
选择 sticker / media 的原因是它覆盖独立 HTTP 边界，能明显削减父模块行数，同时
避免触碰聊天落库、Prompt Runtime、message envelope 和群聊入口。已把
`/stickers/register`、`/stickers/search`、`/stickers/{sticker_id}/image`、
`/generated-images/{image_id}/image` 和 `/stickers/{sticker_id}/disable` 迁移到
`api/sticker_media_routes.py`；旧 `api.routes` 继续 re-export
`StickerRegisterRequest` 和 5 个 endpoint。普通 API token monkeypatch、公开图片
环境 token、collection route 先于动态 sticker route、duplicate canonical 跳转、
active 状态判断、cache fallback、生成图片缺失 404 和禁用 sticker 404 语义均保持兼容。
父模块继续保留 `/chat`、`/group/message`、聊天图片 helper、群聊 sticker facade、
`init_legacy_memory()`、`memory`、`evolution_task` 和 `/health`。`api/routes.py`
从 2134 行降至 1975 行，新模块 `api/sticker_media_routes.py` 为 185 行。

设计文档：
`docs/superpowers/specs/2026-06-22-api-sticker-media-routes-split-design.md`。

实现计划：
`.Codex/plans/api-sticker-media-routes-split.md`。

阶段提交：

- 设计提交：`0b02d3e docs(普通API): 设计贴纸媒体路由拆分`。
- 计划提交：`9493c0c docs(计划): 记录贴纸媒体路由拆分计划`。
- 红灯测试提交：`6ded608 test(普通API): 锁定贴纸媒体路由拆分契约`。
- 路由拆分提交：`8d3acbc refactor(普通API): 拆分贴纸媒体路由`。
- 文档收口提交：随本次 `docs(计划): 收口贴纸媒体路由拆分` 完成。

计划列表：

- [x] 审计 media / stickers 与 chat-step / render 候选，选择 sticker / media
  作为下一刀 route-only 边界。
- [x] 写入设计文档并提交。
- [x] 写入实现计划并提交。
- [x] 补普通 API sticker / media route split 红灯测试并提交。
- [x] 拆出 `api/sticker_media_routes.py`，保持旧导入兼容、旧 token monkeypatch、
  公开图片环境 token 边界、route 顺序和父模块聊天主链路边界，并提交。
- [x] 更新 `docs/todo.md`、本 walkthrough 和计划执行记录，完成最终验证后提交文档收口。

验证记录：

- 红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_sticker_media_routes_split.py`
  -> `3 failed, 7 passed, 21 warnings in 7.04s`；失败点为 5 个 sticker / media endpoint
  仍注册在 `api.routes`、`api.sticker_media_routes` 尚不可导入，以及
  `api/sticker_media_routes.py` 文件不存在。
- Split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_sticker_media_routes_split.py`
  -> `10 passed, 21 warnings in 1.66s`。
- sticker / generated image / push renderer 行为回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api.py::test_sticker_register_search_and_disable_api tests/test_api.py::test_public_sticker_image_returns_cached_file tests/test_api.py::test_sticker_register_auto_describe_adds_background_task tests/test_sticker_memory.py tests/test_sticker_rag.py tests/test_sticker_tool.py tests/test_image_generation_tool.py tests/test_push_envelope.py tests/test_qq_outbound_renderer.py`
  -> `67 passed, 21 warnings in 6.05s`。
- 普通 API split 相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_sticker_media_routes_split.py tests/test_api_history_log_routes_split.py tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py tests/test_api_task_routes_split.py tests/test_asyncio_run_policy.py`
  -> `56 passed, 21 warnings in 7.67s`。
- 静态检查：`python -B -m py_compile api/routes.py api/sticker_media_routes.py tests/test_api_sticker_media_routes_split.py`
  成功；`api/sticker_media_routes.py` 无 `from api.routes`、`import api.routes`、
  `asyncio.run` 或 `run_awaitable_sync`；`git diff --check -- api/routes.py api/sticker_media_routes.py tests/test_api_sticker_media_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 1975 行，`api/sticker_media_routes.py` 185 行，
  `tests/test_api_sticker_media_routes_split.py` 182 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1620 passed, 6 skipped, 139 warnings in 116.71s`。
- 文档收口定向回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_sticker_media_routes_split.py tests/test_api.py::test_sticker_register_search_and_disable_api tests/test_api.py::test_public_sticker_image_returns_cached_file tests/test_api.py::test_sticker_register_auto_describe_adds_background_task tests/test_asyncio_run_policy.py`
  -> `16 passed, 21 warnings in 3.87s`。

执行约束：

- 不拆 `/chat`。
- 不拆 `/group/message`。
- 不迁移 `_persist_chat_turn()`、`_safe_meta()`、聊天图片 helper、群聊 sticker facade、
  `init_legacy_memory()`、`memory` 或 `evolution_task`。
- 不修改 `server.py` 或 `bootstrap/lifespan.py`。
- 不改变 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约或
  message envelope。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列当前仍只剩 `api/routes.py`，行数为 1975。下一刀可拆 `chat-step` /
`render` 这类小型 route-only 边界，或继续审计更低风险的独立 HTTP 子域；继续避开
`/chat` 与 `/group/message` 主链路，除非先完成更细的设计文档和红灯契约。

## 2026-06-22 普通 API Agent Step / Render 路由拆分

状态：设计、设计勘误、计划、红灯测试、Agent Step / Render 路由拆分、验证和
实现阶段提交均已完成。本阶段继续拆普通 `api/routes.py`，但没有迁移 `/chat` 或
`/group/message` 主链路。选择 Agent Step / Render 的原因是它是小型 route-only
边界，能在保持风险可控的前提下继续削减父模块，并验证 `/chat-step` 的 SSE 语义、
旧导入兼容和普通 API token monkeypatch 不被拆分破坏。已把 `/render` 与
`/chat-step` HTTP 层迁移到 `api/agent_step_routes.py`；旧 `api.routes` 继续
re-export `AgentStepRequest`、agent-step 执行 / 序列化对象和 2 个 endpoint。
`/render` 无鉴权 deprecated 响应、`/chat-step` 普通 API token 鉴权、
`Accept: text/event-stream` 与 body `stream=true` 两种 SSE 触发、SSE 首事件，以及
`/render` -> `/chat-step` -> `/chat` 路由顺序均保持兼容。父模块继续保留 `/chat`、
`/group/message`、group timing、`update_group_name()`、聊天落库、Prompt Runtime、
message envelope 和 `/health`。`api/routes.py` 从 1975 行降至 1954 行，新模块
`api/agent_step_routes.py` 为 42 行。

设计文档：
`docs/superpowers/specs/2026-06-22-api-agent-step-routes-split-design.md`。

实现计划：
`.Codex/plans/api-agent-step-routes-split.md`。

阶段提交：

- 设计提交：`66a4b83 docs(普通API): 设计 Agent Step 路由拆分`。
- 设计勘误提交：`db83dfb docs(普通API): 修正 Agent Step 路由顺序设计`。
- 计划提交：`51940fb docs(计划): 记录 Agent Step 路由拆分计划`。
- 红灯测试提交：`eaab6ba test(普通API): 锁定 Agent Step 路由拆分契约`。
- 路由拆分提交：`eb6981f refactor(普通API): 拆分 Agent Step 路由`。
- 文档收口提交：随本次 `docs(计划): 收口 Agent Step 路由拆分` 完成。

计划列表：

- [x] 核对当前 todo 进度与工作区状态。
- [x] 审计 `api/routes.py` 剩余可拆边界并选定 Agent Step / Render route-only 小刀。
- [x] 写入设计文档并提交。
- [x] 修正设计中的 route order 与旧导入兼容清单并提交。
- [x] 写入实现计划并提交。
- [x] 补普通 API Agent Step route split 红灯测试并提交。
- [x] 拆出 `api/agent_step_routes.py`，保持旧导入兼容、旧 token monkeypatch、
  SSE 触发、route 顺序和父模块聊天主链路边界，并提交。
- [x] 更新 `docs/todo.md`、本 walkthrough 和计划执行记录，完成最终验证后提交文档收口。

验证记录：

- 红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_agent_step_routes_split.py`
  -> `4 failed, 7 passed, 21 warnings in 7.14s`；失败点为 `/render` 与
  `/chat-step` endpoint 仍注册在 `api.routes`、`api.agent_step_routes` 尚不可导入，
  以及 `api/agent_step_routes.py` 文件不存在。
- Split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_agent_step_routes_split.py`
  -> `11 passed, 21 warnings in 1.83s`；文档收口前复验为
  `11 passed, 21 warnings in 1.94s`。
- Agent Step 行为回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_agent_step_api.py`
  -> `6 passed, 21 warnings in 2.44s`。
- 普通 API split 相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_agent_step_routes_split.py tests/test_api_sticker_media_routes_split.py tests/test_api_history_log_routes_split.py tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py tests/test_api_task_routes_split.py tests/test_asyncio_run_policy.py`
  -> `67 passed, 21 warnings in 8.94s`。
- `/chat` 流式相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api.py::test_stream_chat_passes_stream_flag_to_bridge tests/test_streaming_api.py tests/test_streaming_response_envelope.py`
  -> `10 passed, 21 warnings in 4.84s`。
- 静态检查：`python -B -m py_compile api/routes.py api/agent_step_routes.py tests/test_api_agent_step_routes_split.py`
  成功；`api/agent_step_routes.py` 无 `from api.routes`、`import api.routes`、
  `asyncio.run` 或 `run_awaitable_sync`；`git diff --check -- api/routes.py api/agent_step_routes.py tests/test_api_agent_step_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 1954 行，`api/agent_step_routes.py` 42 行，
  `tests/test_api_agent_step_routes_split.py` 218 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1631 passed, 6 skipped, 139 warnings in 121.40s`。
- 文档收口定向回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_agent_step_routes_split.py tests/test_agent_step_api.py tests/test_asyncio_run_policy.py`
  -> `20 passed, 21 warnings in 5.66s`。

执行约束：

- 不拆 `/chat`。
- 不拆 `/group/message`。
- 不迁移 group timing、`update_group_name()`、`_persist_chat_turn()`、`_safe_meta()`、
  `init_legacy_memory()`、`memory` 或 `evolution_task`。
- 不修改 `server.py` 或 `bootstrap/lifespan.py`。
- 不改变 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约或
  message envelope。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列当前仍只剩 `api/routes.py`，行数为 1954。下一刀可以审计 group
utility / legacy timing route，但该边界会接触 bridge、history context、runtime state、
群回复持久化，风险高于本阶段；也可以继续寻找更低风险的 route-only 子域。继续避开
`/chat` 与 `/group/message` 主链路，除非先完成更细的设计文档和红灯契约。

## 2026-06-22 普通 API Group Utility / Legacy Timing 路由拆分

状态：设计、计划、红灯测试、路由拆分、验证和实现阶段提交均已完成。本阶段继续拆
普通 `api/routes.py`，迁移 `/update_group_name`、`/group_timing` 与
`/group_timing/timer` 到 `api/group_utility_routes.py`，但没有迁移 `/chat` 或
`/group/message` 主链路。选择该边界的原因是 Agent Step / Render 拆分后，剩余可拆
小路由主要集中在 group utility / legacy timing；它比只拆 `update_group_name()` 的行数
收益更高，又比直接拆 `/chat` 或 `/group/message` 风险可控。旧 `api.routes` 继续
re-export `UpdateGroupNameRequest`、`GroupTimingRequest`、`GroupTimingTimerRequest`、
`_build_group_timing_context()`、`update_group_name()`、`group_timing_deprecated()` 和
`group_timing_timer()`。普通 API token monkeypatch、`api.routes.get_bridge`
monkeypatch、group user id normalization、timer recent context、bridge 前事务释放、
HTML 回复不截断、重复回复抑制、群回复持久化和 route order 均保持兼容。父模块继续保留
`/chat`、`/group/message`、`/health`、聊天落库、Prompt Runtime、message envelope 和私聊
multimodal helper。`api/routes.py` 从 1954 行降至 1754 行，新模块
`api/group_utility_routes.py` 为 283 行。

设计文档：
`docs/superpowers/specs/2026-06-22-api-group-utility-routes-split-design.md`。

实现计划：
`.Codex/plans/api-group-utility-routes-split.md`。

阶段提交：

- 设计提交：`d7a68f0 docs(普通API): 设计群工具路由拆分`。
- 计划提交：`f8f1ac0 docs(计划): 记录群工具路由拆分计划`。
- 红灯测试提交：`9ae67ea test(普通API): 锁定群工具路由拆分契约`。
- 路由拆分提交：`e6d4be5 refactor(普通API): 拆分群工具路由`。
- 文档收口提交：随本次 `docs(计划): 收口群工具路由拆分` 完成。

计划列表：

- [x] 核对当前 todo 进度与工作区状态。
- [x] 审计 `api/routes.py` 剩余可拆边界并选定 group utility / legacy timing。
- [x] 写入设计文档并提交。
- [x] 写入实现计划并提交。
- [x] 补普通 API group utility route split 红灯测试并提交。
- [x] 拆出 `api/group_utility_routes.py`，保持旧导入兼容、旧 token monkeypatch、
  `get_bridge` monkeypatch、route 顺序和父模块聊天主链路边界，并提交。
- [x] 更新 `docs/todo.md`、本 walkthrough 和计划执行记录，完成最终验证后提交文档收口。

验证记录：

- 红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py`
  -> `7 failed, 13 passed, 21 warnings in 8.33s`；失败点为 group utility endpoint
  仍注册在 `api.routes`、`api.group_utility_routes` 尚不可导入、
  `api/group_utility_routes.py` 文件不存在，以及 Agent Step 边界测试已期待
  `group_timing_timer` re-export 到新模块。
- Split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py`
  -> `20 passed, 21 warnings in 2.88s`。
- timing 行为回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_timing_gate.py::TestRouteContext::test_group_timing_context_sanitizes_pending_messages tests/test_api.py::test_group_timer_returns_full_html_reply_without_truncation tests/test_api.py::test_group_message_returns_full_html_reply_without_truncation tests/test_api_routes_group_helper_facade.py`
  -> `5 passed, 1 warning in 1.51s`。
- 普通 API split 相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_sticker_media_routes_split.py tests/test_api_history_log_routes_split.py tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py tests/test_api_task_routes_split.py tests/test_asyncio_run_policy.py`
  -> `76 passed, 21 warnings in 10.01s`。
- 静态检查：`python -B -m py_compile api/routes.py api/group_utility_routes.py tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py`
  成功；`api/group_utility_routes.py` 无 `from api.routes`、`import api.routes`、
  `asyncio.run` 或 `run_awaitable_sync`；`git diff --check -- api/routes.py api/group_utility_routes.py tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 1754 行，`api/group_utility_routes.py` 283 行，
  `tests/test_api_group_utility_routes_split.py` 211 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1640 passed, 6 skipped, 139 warnings in 120.42s`。
- 文档收口定向回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_group_utility_routes_split.py tests/test_api.py::test_group_timer_returns_full_html_reply_without_truncation tests/test_asyncio_run_policy.py`
  -> `13 passed, 21 warnings in 2.73s`。

执行约束：

- 不拆 `/chat`。
- 不拆 `/group/message`。
- 不迁移 `ChatProxyRequest`、`GroupMessageRequest`、OneBot segment model、
  `_persist_chat_turn()`、`_safe_meta()`、私聊缓冲、guardrail、流式响应、
  Prompt Runtime 输入组装、message envelope 或私聊 multimodal helper。
- 不修改 `server.py` 或 `bootstrap/lifespan.py`。
- 不改变 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约或
  message envelope。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列当前仍只剩 `api/routes.py`，行数为 1754。剩余显式路由为
`/group/message`、`/chat` 和 `/health`；`/health` 收益很低且承担父模块哨兵作用，
`/chat` 与 `/group/message` 是主链路，继续拆分前需要重新设计更细的边界和红灯契约。

## 2026-06-22 普通 API Group Message 路由拆分

状态：设计、计划、红灯测试、路由拆分、验证和实现阶段提交均已完成。本阶段继续拆
普通 `api/routes.py`，迁移 `/group/message`、`OneBotMessageSegmentPayload`、
`GroupMessageRequest` 和 `group_message()` 到 `api/group_message_routes.py`，但没有
迁移 `/chat`、`/health`、聊天落库、Prompt Runtime、message envelope、私聊
multimodal helper 或 group ingress helper facade。选择该边界的原因是 group utility /
legacy timing 拆分后，父模块剩余显式路由只剩 `/group/message`、`/chat` 和 `/health`；
`/health` 收益极低，直接拆 `/chat` 风险最高，而 `/group/message` 的业务主体已经在
`GroupIngressService`，HTTP shell 相对薄。旧 `api.routes` 继续 re-export
`OneBotMessageSegmentPayload`、`GroupMessageRequest` 和 `group_message()`。普通 API
token monkeypatch、`api.routes.get_bridge` monkeypatch、`client_meta` 群聊边界校验、
route order、响应信封、TimingGate / Bridge metadata 和 helper facade identity 均保持兼容。
`api/routes.py` 从 1754 行降至 1709 行，新模块 `api/group_message_routes.py` 为 88 行。

设计文档：
`docs/superpowers/specs/2026-06-22-api-group-message-routes-split-design.md`。

实现计划：
`.Codex/plans/api-group-message-routes-split.md`。

阶段提交：

- 设计提交：`1ccbf33 docs(普通API): 设计群消息路由拆分`。
- 计划提交：`60d69d6 docs(计划): 记录群消息路由拆分计划`。
- 红灯测试提交：`2035f7c test(普通API): 锁定群消息路由拆分契约`。
- 路由拆分提交：`d69808b refactor(普通API): 拆分群消息路由`。
- 文档收口提交：随本次 `docs(计划): 收口群消息路由拆分` 完成。

计划列表：

- [x] 审计 `/health`、`/group/message` 与 `/chat` 的收益 / 风险比，并选定
  group message route-only 边界。
- [x] 写入设计文档并提交。
- [x] 写入实现计划并提交。
- [x] 补普通 API group message route split 红灯测试并提交。
- [x] 拆出 `api/group_message_routes.py`，保持旧导入兼容、旧 token monkeypatch、
  `get_bridge` monkeypatch、client meta 400、route 顺序和父模块聊天主链路边界，并提交。
- [x] 更新 `docs/todo.md`、本 walkthrough 和计划执行记录，完成最终验证后提交文档收口。

验证记录：

- 红灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_sticker_media_routes_split.py`
  -> `8 failed, 24 passed, 21 warnings in 9.27s`；失败点为 `/group/message`
  endpoint 仍注册在 `api.routes`、`api.group_message_routes` 尚不可导入、
  `api/group_message_routes.py` 文件不存在，以及相邻 split 测试已期待
  `routes.group_message` re-export 到新模块。
- Split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_sticker_media_routes_split.py tests/test_api_group_utility_routes_split.py`
  -> `41 passed, 21 warnings in 4.69s`。
- 群消息行为回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api.py::test_group_message_ambient_enters_timing_gate tests/test_api.py::test_group_message_passes_client_platform_to_timing_gate tests/test_api.py::test_group_message_passes_client_platform_to_bridge tests/test_api.py::test_group_message_returns_full_html_reply_without_truncation tests/test_api.py::test_group_message_prompt_v2_audit_failure_is_no_send tests/test_group_response_envelope.py tests/test_api_routes_group_helper_facade.py`
  -> `13 passed, 1 warning in 2.18s`。
- 普通 API split 相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_group_message_routes_split.py tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_sticker_media_routes_split.py tests/test_api_history_log_routes_split.py tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py tests/test_api_task_routes_split.py tests/test_asyncio_run_policy.py`
  -> `87 passed, 21 warnings in 10.78s`。
- 静态检查：`python -B -m py_compile api/routes.py api/group_message_routes.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_sticker_media_routes_split.py`
  成功；`api/group_message_routes.py` 无 `from api.routes`、`import api.routes`、
  `asyncio.run` 或 `run_awaitable_sync`；`git diff --check -- api/routes.py api/group_message_routes.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_sticker_media_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 1709 行，`api/group_message_routes.py` 88 行，
  `tests/test_api_group_message_routes_split.py` 262 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1651 passed, 6 skipped, 139 warnings in 122.43s`。

执行约束：

- 不拆 `/chat`。
- 不拆 `/health`。
- 不迁移 `ChatProxyRequest`、`proxy_chat()`、`_persist_chat_turn()`、`_safe_meta()`、
  私聊缓冲、guardrail、流式响应、Prompt Runtime 输入组装、message envelope 或私聊
  multimodal helper。
- 不迁移 group ingress helper facade。
- 不修改 `GroupIngressService` 业务语义。
- 不改变 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约或
  message envelope。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列当前仍只剩 `api/routes.py`，行数为 1709。剩余显式路由为 `/chat` 和
`/health`；`/health` 只有极少行数且承担父模块哨兵作用，不优先拆。下一刀建议先设计
chat helper / contract / persistence 抽取，把 `/chat` 主链路的私聊缓冲、SSE、guardrail、
Prompt Runtime metadata、落库和断连 push 逐步拆成更小边界，再考虑迁移完整 `/chat`
endpoint。

## 2026-06-22 普通 API Chat Helper 拆分

状态：设计、计划、红灯测试、helper 拆分、验证和实现阶段提交均已完成。本阶段继续拆
普通 `api/routes.py`，新增 `api/chat_content_helpers.py` 与
`api/chat_response_contract.py`，迁移聊天内容 helper 和响应契约 helper 的实现逻辑。
`api.routes` 继续保留旧下划线 wrapper，`/chat` 路由本体、`ChatProxyRequest`、
私聊缓冲、聊天落库、Prompt Runtime 输入组装、bridge/SSE runner、`get_bridge` /
`get_guardrail` monkeypatch、`CHAT_STREAM_QUEUE_MAXSIZE` 和 `/health` 均保持在父模块。
选择该边界的原因是完整搬迁 `/chat` 会同时触达私聊缓冲、SSE、断连后台 push、落库幂等和
大量旧父模块 monkeypatch；而内容 helper 与 response contract 基本是纯函数，可先降低
`api/routes.py` 的职责密度。`api/routes.py` 从 1709 行降至 1604 行；
`api/chat_content_helpers.py` 为 76 行，`api/chat_response_contract.py` 为 163 行。

设计文档：
`docs/superpowers/specs/2026-06-22-api-chat-helper-split-design.md`。

实现计划：
`.Codex/plans/api-chat-helper-split.md`。

阶段提交：

- 设计提交：`7063415 docs(普通API): 设计聊天助手拆分`。
- 计划提交：`6cfd183 docs(计划): 记录聊天助手拆分计划`。
- 红灯测试提交：`b313580 test(普通API): 锁定聊天助手拆分契约`。
- Helper 拆分提交：`dd34229 refactor(普通API): 拆分聊天助手契约`。
- 文档收口提交：随本次 `docs(计划): 收口聊天助手拆分` 完成。

计划列表：

- [x] 汇总 `/chat` 拆分审计结论并确定 chat content / response contract 边界。
- [x] 写入设计文档并提交。
- [x] 写入实现计划并提交。
- [x] 补普通 API chat helper split 红灯测试并提交。
- [x] 新增 `api/chat_content_helpers.py` 与 `api/chat_response_contract.py`，把父模块
  helper 改为 wrapper，保持旧 `__module__`、SSE 格式、response envelope 和图片归档契约，并提交。
- [x] 更新 `docs/todo.md`、本 walkthrough 和计划执行记录，完成最终验证后提交文档收口。

验证记录：

- 首次红灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_chat_helpers_split.py tests/test_api_sticker_media_routes_split.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py`
  -> `8 failed, 35 passed, 21 warnings in 10.08s`；其中 7 个失败来自新模块缺失，
  另 1 个失败暴露测试误用顶层 `request_id`，已按真实契约改为
  `client_meta.trace.request_id`。
- 修正后红灯：同一命令 ->
  `7 failed, 36 passed, 21 warnings in 10.03s`；失败点集中在
  `api/chat_content_helpers.py`、`api/chat_response_contract.py` 不存在或无法导入。
- Helper split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_chat_helpers_split.py`
  -> `8 passed, 1 warning in 0.89s`。
- 普通 API split 相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_chat_helpers_split.py tests/test_api_sticker_media_routes_split.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_history_log_routes_split.py tests/test_api_memory_routes_split.py tests/test_asyncio_run_policy.py`
  -> `62 passed, 21 warnings in 7.57s`。
- `/chat` 流式与信封回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_streaming_api.py tests/test_streaming_response_envelope.py tests/test_api_push_envelope.py tests/test_chat_response_envelope.py tests/test_message_envelope.py`
  -> `21 passed, 21 warnings in 7.21s`。
- `/chat` 私聊缓冲和持久化关键 nodeid：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api.py::test_proxy_chat_kt_error_does_not_echo_internal_detail tests/test_api.py::test_stream_chat_emits_progress_and_done_events tests/test_api.py::test_stream_chat_passes_stream_flag_to_bridge tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta tests/test_api.py::test_private_buffer_refreshes_window_and_persists_merged_messages tests/test_api.py::test_private_buffer_merges_files_for_final_bridge_request`
  -> `11 passed, 21 warnings in 2.86s`。
- 静态检查：`python -B -m py_compile api/routes.py api/chat_content_helpers.py api/chat_response_contract.py tests/test_api_chat_helpers_split.py`
  成功；`api/chat_content_helpers.py` 与 `api/chat_response_contract.py` 无
  `from api.routes`、`import api.routes`、`asyncio.run` 或 `run_awaitable_sync`；
  `git diff --check -- api/routes.py api/chat_content_helpers.py api/chat_response_contract.py tests/test_api_chat_helpers_split.py tests/test_api_sticker_media_routes_split.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 1604 行，`api/chat_content_helpers.py` 76 行，
  `api/chat_response_contract.py` 163 行，`tests/test_api_chat_helpers_split.py` 144 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1662 passed, 6 skipped, 139 warnings in 125.10s`。
- 文档收口定向回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_chat_helpers_split.py tests/test_streaming_response_envelope.py tests/test_asyncio_run_policy.py`
  -> `13 passed, 21 warnings in 3.66s`。

执行约束：

- 不拆 `/chat` 路由本体。
- 不拆 `/health`。
- 不迁移 `ChatProxyRequest`、`proxy_chat()`、`_private_buffers`、`_persist_chat_turn()`、
  `_safe_meta()`、`get_bridge`、`get_guardrail` 或 `CHAT_STREAM_QUEUE_MAXSIZE`。
- 不改变 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约或
  message envelope。
- 不改变 ChatLog 完整图片归档和 ConversationTurn 图片摘要边界。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。

下一步：

P3 超大文件队列当前仍只剩 `api/routes.py`，行数为 1604。剩余显式路由为 `/chat` 和
`/health`；`/health` 收益很低且承担父模块哨兵作用，不优先拆。下一候选边界应继续围绕
`/chat` 主链路做细粒度设计：优先评估聊天落库 writer、私聊缓冲状态机或 streaming
finalizer / push envelope 构造，继续保留父模块 monkeypatch facade，避免一次性迁移完整
`proxy_chat()`。
