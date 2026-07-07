# 主动情感外呼调度重构设计

## 目标

本次重构增强已有 `core/proactive_outreach.py`，修复幂等、调度行增长和崩溃一致性问题，并把触发模型重新划分为四层：心跳、`next_check_at`、冲击和 `max_silence`。系统仍是单用户 superuser 自用工具，不引入 shadow 模式、语义越界闸或禁止话术清单，不修改 `vendor/`。

## 质量缺陷修复

### A1：语义幂等键

`idempotency_key` 不再用当前时间微秒作为种子，而是绑定本次发送响应的调度锚点：

- 普通到期发送：使用当前生效的 `next_check_at` 作为锚点。
- 冲击提前发送：仍使用当前调度行的 `next_check_at`，表示「提前响应该调度点」。
- 强制发送：使用触发 `max_silence` 的 silence anchor，即最近有效发送或最早调度尝试的 `created_at`。

同一个到期点被进程重跑、线程重试或手工再次调用时，会生成同一个 key，唯一约束和投递层幂等才能真正生效。

### A2：单条当前调度行

`pending` 不再作为每次「不发」的历史追加日志。每个 user 只保留一条当前调度行：

- Judge 说不发：更新现有 `pending` 行的 `grounding_json`、`judge_reason`、`next_check_at`、`next_intent` 和语义 key。
- 没有当前 `pending` 行：创建一条新的 `pending` 行。
- 已 `sent` 或 `failed` 的行保留为历史。
- `sending` 表示可能已经推送，不会被更新回 `pending`。

这样保留已发送历史，同时避免「不发」判断无限增长。

### A3：投递状态机

投递状态机改为 `pending → sending → sent/failed`：

- push 前先写入或复用日志行，并提交 `sending`。
- 如果进程在 push 成功后、写 `sent` 前崩溃，重跑时看到 `sending` 会视为可能已投递，不再次 push。
- 同一 `idempotency_key` 的 `sent`、`sending`、`failed` 都不会重复投递；`pending` 会继续推进到 `sending`。

## 四层触发模型

### 1. 心跳

后台线程只是固定节拍器，继续读取 `proactive_outreach.fallback_interval_min` 作为心跳间隔，保持配置兼容。心跳本身不决定是否发送，只调用单步检查函数。

### 2. `next_check_at`

`next_check_at` 是 bot 自定的「最晚下次再考虑」时间，继续由 `_clamp_next_check_at()` 使用 `min_interval_min` 和 `max_check_interval_min` 钳制。到点后进入 Judge；没到点默认跳过。

### 3. 冲击

冲击只在用户活跃时段内生效，且仅用于提前运行 Judge，不直接发送。若 `next_check_at` 未到，每次心跳按概率 `p` 提前考虑一次：

- `p` 在 `surge_min_prob` 和 `surge_max_prob` 之间线性增长。
- 增长依据是距上次用户交互的时长；没有交互记录时按高水位处理。
- 随机源通过参数注入，默认使用 `random.random`，便于测试确定性。
- 安静时段直接跳过，不计算冲击。

### 4. `max_silence`

`max_silence` 是下限保证。超过 `proactive_outreach.max_silence_min` 后，活跃时段内强制生成并发送一次，绕过 Judge，保留现有「防永久沉默」行为。`sending` 视为可能已投递的有效外呼锚点，避免崩溃后重复发送。

## 配置

新增配置项：

- `proactive_outreach.surge_min_prob`：默认 `0.1`，类型 `float`。
- `proactive_outreach.surge_max_prob`：默认 `0.6`，类型 `float`。

保留并沿用现有配置：

- `proactive_outreach.enabled`
- `proactive_outreach.fallback_interval_min`
- `proactive_outreach.min_interval_min`
- `proactive_outreach.max_check_interval_min`
- `proactive_outreach.max_silence_min`

## 测试策略

测试必须覆盖：

- A1：真实调用链同一到期点二次触发，只 push 一次。
- A2：多次 Judge 不发只维护同一条当前 `pending` 行。
- A3：已有 `sending` 记录时不再次 push。
- B：心跳只做节拍；`next_check_at` 未到默认跳过；冲击命中提前跑 Judge；冲击未命中跳过；概率随沉默时长上升；安静时段不冲击；`max_silence` 覆盖 future `next_check_at`。

最终验证使用：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -m pytest tests/ -v
```
