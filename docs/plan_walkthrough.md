# Nanobot Server 阶段计划 Walkthrough

更新日期：2026-06-17

本文记录当前任务的完整阶段计划，用于继续推进 `docs/todo.md` 中 TimingGate 混合决策路线，并保持每个阶段完成后单独验证、单独提交。

## 当前目标

优先完成 TimingGate「规则信号 + 模型」混合决策的剩余落地工作。当前重点是让私聊和群聊共享同一套 scoring 公式，并在后续阶段继续处理 cooldown 兼容路径、配置开关、真实日志评估和 eval 门禁。

## 执行约束

- 每个阶段先写计划，再按 TDD 执行红灯、绿灯、重构。
- 每个阶段完成后运行定向测试、相关回归和全量测试。
- 每个阶段性改动单独 commit。
- 只暂存本阶段文件，不使用 `git add .` 或 `git add -A`。
- 不回滚工作区中与本阶段无关的已有脏文件。
- 所有说明、文档和 commit message 使用中文。

## 进度总览

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
| 阶段 11：真实日志假阳率评估 | 待开始 | 抽样脚本、shadow 对比、阈值建议 |
| 阶段 12：timing gate eval 基线与回归门禁 | 待开始 | baseline diff 与阈值门禁 |
| 阶段 13：文档收尾 | 待开始 | 同步 `docs/todo.md` 与设计文档 |

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

状态：待开始。

目标：用真实 ChatLog 抽样评估 `s_ack`、`s_transport`、`w_marker` 的假阳性，并输出 shadow 对比结果。

验收标准：

- 有可复跑脚本或 eval runner
- 记录样本量、误判类型和建议阈值
- 不凭感觉直接调参

### 阶段 12：timing gate eval 基线与回归门禁

状态：待开始。

目标：把现有 timing eval 从手动运行升级为基线对比和回归门禁。

验收标准：

- 新增 baseline diff
- 支持阈值失败机制
- 核心 suite 可纳入提交前或 CI 验证流程

### 阶段 13：文档收尾

状态：待开始。

目标：所有代码阶段完成后，同步 `docs/todo.md` 和相关设计文档，确保文档与真实代码状态一致。

验收标准：

- `docs/todo.md` 不再描述已过时状态
- 剩余限制明确写出
- 文档变更单独提交

## 下一步

继续阶段 11：真实日志假阳率评估。下一阶段仍按计划文件、TDD 红绿、回归验证和阶段提交推进。
