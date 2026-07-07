# 主动情感外呼设计

## 目标

为 nanobot 增加单用户自用的主动情感外呼能力：在没有 incoming 消息触发时，后台调度器可自主判断是否向唯一 superuser 发送 QQ 私聊 DM。该能力与已有群聊 ambient 主动发言正交，不复用群聊 `timing_gate.proactive.*` 语义。

## 框架校准

本功能面向单用户自用工具，用户、开发者和风险承担者是同一个人。因此不引入 shadow/dry-run 三态，不引入语义越界闸，不做防情感依赖逻辑，也不在 prompt 中写禁止话术清单。`proactive_outreach.enabled` 是纯布尔开关。

必须落地的工程约束有三条：

- 幂等：用 `idempotency_key` 唯一约束和投递状态机防止热重启、多线程或多进程重复发送同一条消息。
- 防永久沉默：Judge 给出的 `next_check_at` 受上下界钳制，超过 `max_silence_min` 未真实发送时由兜底心跳强制生成并发送一条，记录 `forced=true`。
- 活跃时段：从该用户历史 `chat_logs.created_at` 小时分布推断清醒窗口；数据不足时使用 08:00-23:00 保守默认，安静时段跳过普通发送。

## 架构

核心链路由独立模块承载，避免污染群聊 runtime：

1. `build_outreach_grounding(user_id)` 从 `personas` 和最近 `chat_logs` 组装语义记忆、近期对话、上次意图和时序状态。
2. `judge_outreach(grounding)` 使用 `timing_proactive` route 输出 `should_reach_out/reason/next_check_at/next_intent`，并对 `next_check_at` 做 `min_interval_min` 与 `max_check_interval_min` 钳制。
3. `generate_outreach_message(grounding, reason)` 使用 `reply` route 生成 2-5 句 DM 正文，prompt 只做正面示范，允许表达自身状态。
4. `deliver_outreach(user_id, message, idempotency_key, ...)` 先创建 `pending` 记录，再复用 daily digest 的 QQ push 方式发送 DM，成功转 `sent`，失败转 `failed`；重复 key 不再次 push。
5. `proactive_outreach_scheduler(stop_event)` 按 fallback 心跳和 DB 中最近 `next_check_at` 唤醒；`enabled=False` 时不启动线程。

## 数据

新增 `proactive_outreach_log`：

- `id` INTEGER PK
- `user_id` TEXT
- `idempotency_key` TEXT UNIQUE
- `grounding_json` TEXT
- `judge_should` BOOLEAN
- `judge_reason` TEXT
- `next_check_at` DATETIME
- `next_intent` TEXT
- `message` TEXT
- `status` TEXT
- `forced` BOOLEAN DEFAULT 0
- `created_at` DATETIME

## 配置

新增 `proactive_outreach.*` 设置：

- `enabled`: bool, 默认 False
- `fallback_interval_min`: int, 默认 120
- `min_interval_min`: int, 默认 30
- `max_check_interval_min`: int, 默认 1440
- `max_silence_min`: int, 默认 2880

## Prompt

Judge prompt 只强调主动、具体、扎根上下文和自定下次时间。Generator prompt 只强调自然、温暖、可表达自身状态、结尾不必催回复。不得添加 shadow、越界检测、黑名单或禁止清单。

## 测试与验收

每个实现步骤先写失败测试再实现。验收证据包括：

- 全量 `python -m pytest tests/ -v` 0 failures。
- `enabled=False` 时 scheduler 不启动。
- 重复 `idempotency_key` 不重发。
- Judge 无限推迟时 48h 地板强制发送并记录 `forced=true`。
- 安静时段跳过普通发送。
- `vendor/` 无任何本功能改动。
- prompt 无禁止清单且允许表达自身状态。
