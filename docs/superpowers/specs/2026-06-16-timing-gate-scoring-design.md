# TimingGate 评分体系设计

> 2026-06-16 · 将群聊 / 私聊的「是否回复」决策从「硬规则级联 + 模型黑箱三态」升级为「信号 → 参与度分数 → 门控短路 → 就绪度判定」的门控-升级（gate-escalation）架构。

---

## 实施状态（2026-06-21）

核心 TimingGate 混合决策主线已完成。已落地 shared timing scoring、群聊 / 私聊统一公式、ambient / legacy / timer cooldown 软化、模型失败规则兜底、session / platform 级模型策略、真实日志信号审计 CLI、scoring 可观测字段补齐（含 WebUI `linger_active` / `linger_reply_count` / `linger_time_remaining` 状态展示），以及 `timing_gate` eval baseline diff / 阈值门禁。

P3-3「持续评估」已完成拆分设计、离线 labeled report / sidecar labels 复跑入口，以及仓库自包含 CI / PR gate。群聊 `s_bot` live path 已接入 scoring 软抑制；私聊分类器失败 / 非法输出已按 `c=0` 进入 `rule_fallback`，旧格式兼容仍保留 `c=0.5`。P4-1 已完成通用 `candidates → labeled` 标注闭环和首个 `capability_model_routing` 能力数据集，P4-2 已完成 Admin 标注工作台契约化与 promote 预检 UI，P4-3 已完成 `capability_reply_contract` / `capability_rendering_contract` per-capability 数据集，P4-4 已完成 RAG baseline 门禁，P4-5A / P4-5B 已完成统一 PR gate 与周期性复跑归档，P4-5C 已完成第一轮 RAG manual 样本扩充，P4-5D / P4-5E / P4-5F / P4-5G 已完成 memory、knowledge、sticker 和 group_memory 四类 fixture-backed positive RAG case，P4-5H 已完成 RAG 过滤约束 fixture。真实样本运营动作 1-10 已完成：TimingGate 信号周期审计、RAG generated → manual 仲裁入口、EvalCandidate 运营规则、候选 reject / defer 仲裁状态、人工仲裁批次审计、EvalCandidate 运营趋势报表、周期运行 manifest、跨 artifact 周期趋势、周期趋势只读调参分析和 TimingSignal 不可变 artifact 加厚均已进入运营闭环。TimingGate 调参提案 record-only 运营链路也已完成，覆盖 run-scoped audit、`final_timing_action` truth、候选参数治理、真实 audit 样本 simulation、Admin 审核 API 和 WebUI 审核入口；该链路只记录人工审核结论，不自动应用参数、不更新 baseline、不改变 gate。

当前剩余工作不再是继续实现旧 scoring 计划，而是进入真实数据运营：使用真实 run-scoped audit、人工 `final_timing_action` truth 和候选参数文件持续生成可审查 proposal，并通过 record-only 审核沉淀人工结论。若本地 artifact 仍是零样本或缺人工 truth，应把 proposal readiness 阻断原因作为运营证据记录，不得伪造人工标注或自动改 gate。

---

## 一、目标与原则

### 1.1 目标

```
除了注入 / 安全拦截外，不再用硬规则一刀切；
所有 timing 信号都转成分数；
规则决定性时直接输出结果（不调模型）；
规则模糊时升级到模型，模型在模糊带内主导；
reply_now / wait / no_reply 可被解释和回溯。
```

### 1.2 设计原则

1. **注入 / 安全硬拦截不变** —— Guardrail 已经独立于 TimingGate，继续 hard stop，本方案不碰它。
2. **信号单通道** —— 每个信号只进一个公式位置，不进两头（杜绝调参耦合）。
3. **门控-升级** —— 分层：规则得分 → 若决定性 → 直接输出（省一次模型）；若模糊 → 升级到模型（模型主导）。
4. **参与度与就绪度解耦** ——「该不该回」和「现在回还是等等」是两道独立判断。
5. **directed_to_other 不再 hard no_reply** —— 降为强抑制信号、仍可被 at_bot / linger 抵消。
6. **模型在模糊带可信且主导** —— λ=0.70，不是少数票。
7. **模型不可用时规则兜底** —— 而非现状的「远端挂 → 全群哑火」(fail→no_reply)。
8. **每条决策可解释** —— 日志回答：为什么 no_reply / wait / continue？规则还是模型主导？哪个信号影响最大？

---

## 二、信号体系（按回答的问题归类）

### 2.1 参与度信号 —— 回答「该不该理」

#### 显式指向 d₀

```
d₀: 这条消息有多明确地在对 bot 说？
```

| 来源 | d₀ |
|------|-----|
| reply_to_bot | 1.00 |
| at_bot | 0.95 |
| bot_name_mentioned | 0.75 |
| direct_call | 0.65 |
| private chat | 0.75（私聊天然有指向性）|
| group ambient | 0 |

> 多条件命中最高的那条。回复 bot > @bot > 提名字 > 命令触发。

#### 余韵 ℓ（linger）

用于解决「被 @ 后 cooldown 打断后续对话」的体验问题。Bot 被召唤后，后续几句即使不再 @bot，也很可能仍在和 bot 对话。

```python
LINGER_TIMEOUT_SECONDS   = 180   #  3 分钟后余韵消退
LINGER_MAX_MESSAGES      =   3   # 最多在 3 条内有效
LINGER_BASE_WEIGHT       = 0.70  # 基础权重（注意：不是采样概率，是确定性乘子）
LINGER_MIN_INTERVAL_SECS =  10   # 余韵内的最小回复间隔
```

激活条件：

```python
if is_at_bot or is_reply_to_bot or bot_name_mentioned or direct_call:
    state.activate_linger(sender_id, trigger_reason)
```

设 t = 距激活的秒数，k = 已回复次数，q ∈ {1.00(同用户), 0.65(其他用户)}：

```
A_t = clip(1 − t / 180)          # 时间衰减
A_k = clip(1 − k / 3)            # 次数额度衰减
ℓ   = clip(0.70 × (0.55+0.45·A_t) × (0.60+0.40·A_k) × q)
```

> ℓ 在激活瞬间 ≈0.70(同用户连续对话) / ≈0.46(跨用户)，随时间/次数/非同一用户衰减到 0。

#### 总指向 d

```
d = max(d₀, ℓ)
```

用 `max` 是语义干净的：ℓ 和 d₀ 回答同一个问题（「这条有多像在对 bot 说」），显式信号永远优先（d₀ 不会被 ℓ 拖累），余韵仅在显式信号已消失时补足。

---

#### 抑制信号 s

用 noisy-or 聚合（同质弱信号可叠加但不会线性爆炸）：

| 子信号 | 条件 | 值 |
|--------|------|-----|
| s_ack | 短确认/语气词：嗯、哦、ok、收到、好的、哈哈等 | 0.85 |
| s_transport | **按内容类型分档**（单条取命中最高档，不累加）：secret/token/blob=0.95，纯URL=0.75，纯代码块=0.65，长文本dump无问句=0.55 | 见右 |
| s_other | 明确指向其他人且未指向 bot（群聊 @ 别人、回复别人）| 0.75 |
| s_bot | 发送者是其他 bot | 0.70 |

> **图片/文件不进 s_transport**——纯图片/文件只进 w_file（就绪度 wait），不在参与度阶段压制。这修复了「@bot + 图片」被误判为纯传输导致 NO_REPLY 的 bug。

```
s = 1 − (1−s_ack)(1−s_transport)(1−s_other)(1−s_bot)
```

> s_other=0.75 是关键软化点：现状 `directed_to_other` 是 hard no_reply，新方案只给 0.75 的抑制。配合 conflict 升级机制（§Stage 2），「d 和 s 同时存在」时强制调模型而非规则裁死。
>
> s_ack 设 0.85 而非 0.95：需要给 `@bot + 弱确认` 场景留出进入模糊带的空间（E_rule=0.185 vs θ_at=0.30，落入模糊带让模型裁量），同时纯 ambient ack 仍被压到 E_rule=0（d=0 时无对抗力）。

**⚠️ 信号提取器是 single point of failure**。s_ack / s_transport / w_marker 的值取决于关键词 / 规则检测的准确率——一次假阳（把「好的，再帮我查下X」判为纯 ack）会丢失一条回复。s_transport 的 tier 判定同样关键：把「@bot + URL」误判为 blob(s=0.95) vs 正确判为 URL(s=0.75)，影响 E_rule 达 0.17。阶段二实现时信号提取器必须先独立跑 shadow、用真实群聊日志统计假阳率；验收标准见 §八。
>
> **s_ack 提取必须排除非纯确认**：含请求词（帮我/查下/怎么/继续/总结/看看）、问号、URL、代码块、文件 中任意一项 → 不算纯 ack。

---

### 2.2 就绪度信号 —— 回答「现在回还是等」

就绪度**不参与** E_rule（不推高 / 压低回复分），只在「决定参与」之后区分 reply_now 还是 wait。

#### wait marker w

| 子信号 | 条件 | 值 |
|--------|------|-----|
| w_marker | 明确待续词：等下、稍等、我发图、我发代码、还有、下一段 | 1.00 |
| w_file | 只有图片 / 文件，没有文字 | 0.45 |
| w_incomplete | 明显半截结尾：以 ：/:/， 等结尾 | 0.35 |

```
w = 1 − (1−w_marker)(1−w_file)(1−w_incomplete)
```

#### 最小回复间隔 I

```
I = 1  当 距上次 bot 回复 < min_interval 且 linger 已激活
    0  否则
```

min_interval 的来源按优先级：linger 余韵内为 `LINGER_MIN_INTERVAL_SECS`(10s)，否则按群 / 全局配置。

---

## 三、计算流程（5 阶段）

```
Stage 0  安全 / 注入拦截  → hard stop（现有 Guardrail，不变）
Stage 1  规则参与度 E_rule  → 单通道：d 推高、s 压低
Stage 2  冲突升级 + 门控    → κ=min(d,s)≥0.35 → 强制模型；
                             否则 → |E_rule−θ|≥m 短路 / <m 调模型
Stage 3  模型升级（模糊/冲突）→ λ=c×0.875，模型主导，失败回退规则侧
Stage 4  就绪度判定        → 仅「参与」→ wait/reply_now（w + min_interval）
```

---

### Stage 1 — 规则参与度（单通道）

```
E_rule = clip( β₀ + β_d·d − β_s·s )

β₀ = 0.10    基础底分（纯 ambient 无信号时略高于 0）
β_d = 0.85   指向 bot 是最重要的正向信号
β_s = 0.85   抑制信号显著压低参与度
```

**系数解读（β_d=β_s=0.85，对称设计）**：
- `@bot` 单独(d=0.95,s=0) → E=0.91 ✓ 强烈参与
- 纯 ambient(d=0,s=0) → E=0.10 ✓ 极低参与度
- 纯 `嗯` ambient(d=0,s=0.85) → E=clip(0.10−0.723)=0 ✓ 不出声
- `@bot 嗯`(d=0.95,s=0.85) → E=clip(0.10+0.808−0.723)=0.185 → 落入模糊带 + κ=0.85≥0.35 冲突升级，交给模型裁量
- `@bot` + 图片(d=0.95,s=0,w_file=0.45) → E=0.91 → 跳过模型、参与 → Stage4 w≥0.4 → WAIT 5s ✓ 不再被误杀
- `@bot` + URL(d=0.95,s=0.75) → E=0.27, κ=0.75≥0.35 → 冲突升级，交给模型裁量
- `@bot` + blob(d=0.95,s=0.95) → E=0.10 < θ−m 但 κ=0.95≥0.35 → 冲突升级，交给模型裁量

> 两个 β 相等时调参直觉最好：d 和 s 等重对抗、β₀ 控制 ambient 基线。

---

### Stage 2 — 门控短路（规则决定性）

按本条消息的**显式**指向 d₀ 选择决策边界 θ（d₀ 是即时的、不受 linger 影响——linger 已经有衰减，不应再拉低阈值）：

| 场景 | d₀ | θ | 含义 |
|------|-----|---|------|
| 私聊 | 0.75(私聊) | 0.40 | 私聊天然可参与，门槛低 |
| 群 at_bot / reply_to_bot | ≥0.90 | 0.30 | 被点名了，门槛极低 |
| 群 direct_call / bot_name | [0.65, 0.90) | 0.42 | 可能在对 bot 说 |
| 群 ambient | [0, 0.65) | 0.62 | 无人点名，需要很强信号才理 |

> θ 只按 d₀ 选档（不按 ℓ 分档）。linger 余韵的得分已通过 ℓ→d→E_rule 进入分数，不应再重复降阈值。（这是上一版评审 P5 的修正。）

**冲突强制升级**（在短路判定之前）：

```
κ = min(d, s)

if κ ≥ 0.35:
    → 强制调模型（stage = "model_assisted_conflict"）
    # 双方都有实质信号时，规则不应裁死
    # 覆盖：directed_to_other + linger、@bot + @别人、bot_name + ack ……
```

κ=0.35 恰好卡在「双方都有 meaningful 信号」的底线——纯 ambient/纯 @bot/纯 ack/纯 directed_to_other（κ 都是 0）不触发；同时存在 d≥0.35 和 s≥0.35 才触发。

冲突升级**之后**才是短路判定（只对 κ<0.35 的无冲突消息生效）：

决定性边距 m（推荐 0.18）：

```
E_rule ≥ θ + m    → 参与（跳过模型）
E_rule ≤ θ − m    → no_reply（跳过模型）
|E_rule − θ| < m  → 模糊带 → 调模型
```

**m 的含义与调整**：
- m 越小 → 更信任规则、省更多模型调用；但误判也更多
- m 越大 → 更多交给模型裁定；更稳但成本高
- 推荐 m=0.18 起步 → 模糊带宽 0.36，约为 θ 跨度的 1/3
- m=0 等价于「纯规则决策」，m=+∞ 等价于「总是调模型」

---

### Stage 3 — 模型升级（仅模糊带）

**设计决策**：模糊带内 λ=0.70（非 0.45），因为：
1. 用户说「模型的判断没那么不可靠，多数时候能正确判断」——那模型就要能翻盘
2. 模糊带恰好是规则最不自信区域，该让模型主导
3. **只有模糊带才调模型**（否则链没到这就短路了）—— λ=0.70 不会浪费任何一次模型调用

#### 模型先验 Mₑ(a)

模型输出 `action ∈ {no_reply, wait, reply_now, continue}` 映射为参与度先验：

```
Mₑ(no_reply) = 0.05
Mₑ(wait)     = 0.55
Mₑ(reply_now / continue) = 0.95
```

> 注意：模型 `wait` 现在同时影响参与度（参与但不推满）和 Stage 4 就绪度。

#### 模型置信度 c 与模型权重 λ

模型**不自我评分**（LLM 自报置信本就不可靠）。c 是系统侧对模型调用质量的度量，**直接调制模型权重** λ：

```
合法 JSON + 合法 action + reason 非空   → c = 0.80
fallback parse（旧格式兼容）             → c = 0.50
模型失败 / 超时 / 非法输出               → c = 0

λ = c × 0.875
```

换算（0.875 = 0.70/0.80，保证正常解析时 λ=0.70）：

| c | λ | 模型影响力 |
|---|----|----------|
| 0.80（正常解析）| 0.70 | 正常——跟原设计一致 |
| 0.50（旧格式兼容）| 0.438 | 打折——仍能翻盘但需更强 E_rule 配合 |
| 0（失败/超时）| 0 | 跳过模型，E_final = E_rule |

#### 合成

```
E_final = (1−λ) × E_rule + λ × Mₑ(a)

参与 ⟺ E_final ≥ θ
```

模糊带的两端算例验证：

| 算例 | E_rule | θ | c | λ | 模型说 | E_final | 结果 | 验证 |
|------|--------|---|----|----|--------|---------|------|------|
| 地板 + 正常模型 reply | 0.44 | 0.62(ambient) | 0.80 | 0.70 | reply(M=0.95) | 0.30×0.44+0.70×0.95=0.80 | 参与 ✅ | 正常解析，模型全额拉回 |
| 地板 + 弱解析 reply | 0.44 | 0.62(ambient) | 0.50 | 0.438 | reply(M=0.95) | 0.562×0.44+0.438×0.95=0.66 | 参与 ✅ | 旧格式兼容仍能翻盘 |
| 天花板 + 正常模型 no_reply | 0.80 | 0.42(bot_name) | 0.80 | 0.70 | no_reply(M=0.05) | 0.30×0.80+0.70×0.05=0.28 | no_reply ✅ | 正常解析，模型全额拒绝 |
| 天花板 + 弱解析 no_reply | 0.80 | 0.42(bot_name) | 0.50 | 0.438 | no_reply(M=0.05) | 0.562×0.80+0.438×0.05=0.47 | 参与 ⚠️ | 旧格式下模型无力拒绝，规则说了算 |

> 算例 4 暴露了一个合理的不对称：低质量解析时模型说 no_reply 比说 reply 更难奏效（因为 Mₑ(no_reply)=0.05 极低，λ 打六折后基本拉不动 E_final）。这是设计的取舍——宁可在旧格式兼容时多参与一次（比漏回安全），也不为了对称去抬高 Mₑ(no_reply)。

#### 路由软否决（仅 s_bot，默认轻量）

s_other 不设上限——冲突场景已由 Stage 2 的 κ 升级机制接管（d 和 s 同时存在 → 强制调模型，模型能阅读完整消息文本做出正确判断）。只对 s_bot 保留软否决：**其他 bot 的消息无论如何不过低门槛**。

```
if s_bot > 0:
    E_final = min(E_final,  1 − γ × s_bot)

γ = 0.80
```

效果：s_bot=0.70 → 硬上限 0.44 → ambient(θ=0.62) 永远过不了；但 at_bot(θ=0.30) 仍可过（被 @ 的 bot 即使发送者是 bot 也该理）。s_other 不再设上限，冲突场景交给模型裁量。

#### 模型不可用 — 规则兜底

```
若 c=0（模型失败/超时）：
  跳过 Stage 3 的 blend，直接 E_final = E_rule
  用 θ 判定：E_rule ≥ θ → 参与，否则 no_reply
```

效果：远端 llama-server 挂掉 ≠ 全群哑火。确定 @bot 仍然参与；ambient 仍然 no_reply；只有模糊带的会丢掉一次模型辅助判断（仍是规则侧判定），比当前 `TimingGate.judge` 异常直接 `no_reply` 强。

---

### Stage 4 — 就绪度判定（仅当参与）

与参与度解耦：**已决定参与后，才判断是现在说还是等**。

按优先级判定（第一条命中即停）：

1. **min_interval 命中** (I=1) → **WAIT**，delay = ⌈δ − g⌉  秒（等到间隔期满）
2. **w ≥ 0.8**（强 marker：等下/我发图）→ **WAIT**，delay ≈ 8
3. **w ≥ 0.4**（弱信号：半截/纯文件）→ **WAIT**，delay ≈ 5
4. **模型(若调了说 wait)** → **WAIT**，delay = 5
5. **否则** → **REPLY_NOW** / **CONTINUE**

```
delay 统一 clip[3, 15]
```

> 注意：现状 delay clip 是 [3, 30]，新方案收窄到 [3,15]。若线上确有需要等更长的场景(如「我等下贴配置，大概半分钟后」)，可在 Stage 4 优先表顶部加一条 `w ≥ 0.95 且 w_marker 为长时态 → delay=25`。

---

## 四、行为算例

| # | 输入 | d | s | κ | θ | 升级/短路 | 模型? | 最终 |
|---|------|---|---|---|----|----------|-------|------|
| 1 | `@bot 帮我查X` | 0.95 | 0 | 0 | 0.30 | E≥θ+m → 跳过 | ❌ | **CONTINUE** |
| 2 | 纯 `嗯` ambient | 0 | 0.85 | 0 | 0.62 | E≤θ−m → 跳过 | ❌ | **NO_REPLY** |
| 3 | ambient 闲聊 | 0 | 0 | 0 | 0.62 | E≤θ−m → 跳过 | ❌ | **NO_REPLY** |
| 4 | `@bot 等下我发图` | 0.95 | 0 | 0 | 0.30 | E≥θ+m → 跳过→w≥0.8 | ❌ | **WAIT 8s** |
| 5 | bot_name + ack | 0.75 | 0.85 | **0.75** | 0.42 | **冲突→模型** | ✅ | 模型裁 |
| 6 | linger followup(t=0) | 0.70 | 0 | 0 | 0.62 | 模糊带→模型 | ✅ | 模型裁 |
| 7 | `@bot` + `@张三` | 0.95 | 0.75 | **0.75** | 0.30 | **冲突→模型**(无软否决) | ✅ | 模型裁 |
| 8 | 纯 directed_to_other | 0 | 0.75 | 0 | 0.62 | E≤θ−m → 跳过 | ❌ | **NO_REPLY** |
| 9 | directed_to_other + linger | 0.55 | 0.75 | **0.55** | 0.62 | **冲突→模型** ← 修复 | ✅ | 模型裁 |
| 10a | `@bot` + blob | 0.95 | 0.95 | **0.95** | 0.30 | **冲突→模型** | ✅ | 模型→NO_REPLY |
| 10b | `@bot` + URL | 0.95 | 0.75 | **0.75** | 0.30 | **冲突→模型** | ✅ | 模型裁 |
| 10c | `@bot` + 图片 | 0.95 | 0 | 0 | 0.30 | E≥θ+m → 跳过→w≥0.4 | ❌ | **WAIT 5s** |
| 11 | bot_name + 半截 | 0.75 | 0 | 0 | 0.42 | E≥θ+m → 跳过→w≥0.4 | ❌ | **WAIT 5s** |
| 12 | 私聊 `查下X` | 0.75 | 0 | 0 | 0.40 | E≥θ+m → 跳过 | ❌ | **REPLY_NOW** |
| 13 | s_bot + ambient | 0 | 0.70 | 0 | 0.62 | E≤θ−m → 跳过 | ❌ | **NO_REPLY** |
| 14 | s_bot + `@bot` | 0.95 | 0.70 | **0.70** | 0.30 | **冲突→模型**+软否决(cap 0.44) | ✅ | 过 θ=0.30 ✓ |

算例验证：
- 1, 3, 4, 8, 10c, 11, 12 共 **7/14 = 50% 跳过模型**——纯指向/纯抑制/纯 ambient 直接定，不改本意。
- 例 9 从 NO_REPLY→模型（核心修复）：directed_to_other+linger 不再被规则裁死。
- 例 10a/10b/10c 展示 transport 拆分的核心价值：`@bot+图片` 不再被 s_transport 误杀（WAIT 5s 而非 NO_REPLY），`@bot+URL` 和 `@bot+blob` 区分对待（URL 可参与/blod 仍压制）。
- 例 7 不再有 s_other 软否决上限——模型在冲突升级后可真正翻盘。
- 例 14 证明 s_bot 软否决仍有效：其他 bot 被 @ 后可通过 θ=0.30 的低门槛（因被点名了该理），但 ambient 下永远过不了 θ=0.62。

---

## 五、参数表

| 参数 | 值 | 敏感度 | 说明 |
|------|-----|--------|------|
| β₀ | 0.10 | 低 | ambient 基线，0.05–0.15 都可 |
| β_d | 0.85 | 中 | 指向 bot 权重，调大 = 更信 @ |
| β_s | 0.85 | 中 | 抑制权重，调大 = 更沉默 |
| θ_private | 0.40 | 低 | 私聊门槛 |
| θ_at/reply_to_bot(d₀≥0.9) | 0.30 | 低 | 被点名门槛极低 |
| θ_direct_call/bot_name(d₀∈[0.65,0.9)) | 0.42 | 中 | 可能在对 bot 说 |
| θ_ambient(d₀<0.65) | 0.62 | 中 | 无人点名，最保守 |
| m | 0.18 | **高** | 决定性边距，直接决定短路率 |
| λ | c×0.875 | **高** | 模糊带模型权重（由 c 调制，非独立参数）；c=0.80→λ=0.70 |
| γ | 0.80 | 低 | 软否决强度，0=不启用 |
| s_ack | 0.85 | **高** | 确认/语气词抑制值；0.85 给 @bot+弱ack 留模糊带空间 |
| s_transport | 分档 0.55–0.95 | **高** | blob=0.95; URL=0.75; codeblock=0.65; dump=0.55; 图片/文件=0 |
| s_other | 0.75 | 中 | directed_to_other 已经不是 hard no_reply |
| s_bot | 0.70 | 中 | 其他 bot 发言；唯一保留软否决上限 |
| κ 阈值 | 0.35 | **高** | min(d,s)≥0.35 → 冲突强制升级，直接调模型 |
| γ | 0.80 | 低 | 仅对 s_bot 生效，s_other 不设上限 |
| LINGER_TIMEOUT_SECS | 180 | 低 | 余韵时长 |
| LINGER_MAX_MESSAGES | 3 | 低 | 余韵消息上限 |
| LINGER_BASE_WEIGHT | 0.70 | 中 | 余韵权重 |
| LINGER_MIN_INTERVAL_SECS | 10 | 低 | 余韵内最小间隔 |
| delay clip | [3, 15] | 低 | wait 延迟范围 |

> 「敏感度」标 `高` 的参数建议先用 shadow 模式日志观察 E_rule/E_final 分布至少一轮后再调。

---

## 六、重构计划

### 阶段一：数据结构（不改行为）

新增 `core/timing_score.py`：

```python
@dataclass
class TimingSignals:
    explicit_direct_score: float   # d₀
    linger_score: float            # ℓ
    direct_score: float            # d = max(d₀, ℓ)
    wait_signal: float             # w
    suppress_score: float          # s
    sub_signals: dict              # 明细：s_ack/s_transport/s_other/s_bot 及 w_i

@dataclass
class TimingModelHint:
    action: str                    # no_reply | wait | reply_now | continue
    confidence: float              # c（系统侧度量，非模型自报）
    raw: str
    reason: str

@dataclass
class TimingDecision:
    action: str                    # no_reply | wait | reply_now | continue
    stage: str                     # "hard_stop" | "rule_shortcut" | "model_assisted" | "model_assisted_conflict" | "rule_fallback"
    participation_score: float     # E_rule
    final_score: float             # E_final（仅在 model_assisted 时有意义）
    theta: float                   # 决策边界
    low_threshold: float           # θ − m
    high_threshold: float          # θ + m
    delay_seconds: int | None
    model_used: bool
    model_action: str
    model_confidence: float
    model_weight: float            # λ（仅在 model_assisted 时有意义）
    signals: TimingSignals
    reason: str
```

先只产出结构、写日志，不替换旧 action。

### 阶段二：实现规则层 (Stage 1 + 2 + 4)

`core/timing_score.py` 实现：
- `extract_signals()` — d₀ / ℓ / w / s 提取
- `compute_rule_score()` — Stage 1
- `select_theta()` — 按 d₀ 选档
- `classify_rule()` — Stage 2 门控短路 + Stage 4 就绪度（不含模型）

此阶段即可用 `evals/cases/timing_gate/*` 跑基准。

### 阶段三：接入模型 (Stage 3)

- `compute_model_prior()` — 模型输出 → Mₑ(a)
- `compute_confidence()` — c
- `compute_final_score()` — blend
- `apply_soft_reject()` — 可选软否决
- 模型失败回退规则侧

### 阶段四：LingerState

在 `GroupChatState` 新增：

```python
linger_active_until: float = 0.0
linger_reply_count: int = 0
linger_source_user_id: str = ""
linger_started_by: str = ""
linger_last_reply_ts: float = 0.0
```

触发：`is_at_bot or is_reply_to_bot or bot_name_mentioned or direct_call` → `activate_linger()`。

更新：回复成功后 `linger_reply_count += 1`、`linger_last_reply_ts = now`。

### 阶段五：Shadow 对比

保留当前 `TimingGate.judge` 输出：

```python
old_action = current_timing_gate_result  # 现状 Qwen 直接三态
new_decision = timing_score_decision     # 新方案 TimingDecision
```

日志同时输出两侧供比，至少观察一轮再切主逻辑。

### 阶段六：替换

群聊 Runtime 中 `force_next_continue`、`_should_cooldown`、`directed_to_other` hard rule 等降级为信号：
- `directed_to_other` → suppress_score（已量化）
- `cooldown` → min_interval
- `recent_bot_followup` → linger
- `force_next_continue` → 设 d₀=1.0（等同 reply_to_bot 级别的指向），仍完整走 Stage 1–4 所有安全阀（Guardrail/min_interval/w/marker/模型仍可拒绝）

私聊也接入同一套 `extract_signals → compute_rule_score → classify_rule`，私聊天然 d₀=0.75、s_other=0、ℓ=0、I=0，输入更简单但公式不变。

### 阶段七：WebUI 调试字段

TimingGate 调试页展示（至少）：

```
action, stage, participation_score, final_score, θ, low_threshold, high_threshold
d₀, ℓ, d, w, s, κ
s_ack, s_transport (tier), s_transport_raw, s_other, s_bot  ← 子信号+分档来源
w_marker, w_file, w_incomplete
model_used, model_action, model_confidence, model_weight
soft_reject_cap (s_bot 上限)
linger_active, linger_reply_count, linger_time_remaining
reason
```

否则后面调参调不出。

---

## 七、与现有代码的映射

| 现有代码 | 映射到新方案 |
|----------|-------------|
| `TimingGate.judge` (classifier_client.py:1215) | Stage 3 — 仅模糊带调用 |
| `_parse_output` (classifier_client.py:1163) | 拆为 `parse_model_output() → TimingModelHint` |
| `_should_cooldown` / `force_next_continue` | 降级为信号，不硬 return |
| `should_suppress_directed_to_other` | 删除 hard check，改 s_other=0.75 + κ 冲突升级机制（d 和 s 同时存在时强制调模型） |
| `_DIRECT_TRIGGERS` / `_COOLDOWN_BYPASS_TRIGGERS` | 保留用作 d₀ 和 linger 激活源 |
| `PrivateDecisionClassifier.classify` | 同公式，输入简化（d₀=0.75, θ=0.40, s_other=0） |
| `delay_seconds` (当前 clip 3-30) | clip [3,15]，待线上观察后确定最终范围 |
| `evals/cases/timing_gate/*` | 直接作为 Stage 2 实现的红测基线 |

---

## 八、验收标准

- [x] 安全 / 注入拦截仍然 hard stop，不被评分体系绕开
- [x] `@bot` 消息确定性参与（跳过模型），不受 cooldown 阻断
- [x] 纯 ambient 闲聊确定性 no_reply（跳过模型），不浪费模型调用
- [x] linger 余韵内同用户 followup 正常参与（可调模型），不被 s_other 或 cooldown 一刀切
- [x] `directed_to_other` 不再是 hard no_reply，但独自成立时规则侧足够压低
- [x] 模型在 `|E_rule−θ| < m` 或 `κ ≥ 0.35`（冲突升级）时调用；其余跳过
- [x] 模型失败/超时后规则侧独立判定，不会全群哑火
- [x] 每条决策日志含 stage、各信号分解值、E_rule/E_final、θ、模型参与信息
- [x] 现有 `evals/cases/timing_gate/*` 通过（至少不回归旧行为逻辑）
- [x] Shadow 模式与模型策略开关已落地，可用 `shadow` 策略和日志审计报告继续对比新旧决策
- [x] 私聊与群聊共享同一套信号提取和公式，仅输入不同
- [x] WebUI 调试页能独立查看每个子信号的贡献
- [x] 信号提取器（s_ack / s_transport / w_marker）已有真实 ChatLog 审计 CLI，可输出假阳率、shadow mismatch 和阈值建议
- [x] `@bot + 弱确认`（如「@bot 好的，帮我查下X」）不被 s_ack 误判为纯 ack——确认有模型兜底路径
- [x] `directed_to_other + linger` 不再被规则裁死——经 κ 冲突升级进入模型
- [x] `@bot + 图片` 不再被 s_transport 误判——E_rule≥θ+m 跳过模型、WAIT 而不是 NO_REPLY
- [x] s_transport 分档判定正确：blob(0.95) vs URL(0.75) vs codeblock(0.65) vs dump(0.55) vs 图片/文件(0)
- [x] s_ack 提取排除非纯确认：含请求词/问号/URL/代码/文件 → 不算纯 ack
- [x] s_other 无软否决上限——仅 s_bot 保留软否决
- [x] `force_next_continue` 设 d₀=1.0（非 E_rule=1.0），仍经完整 Stage 1–4 安全阀

补充说明：真实日志的「假阳率可接受」仍依赖持续人工标注样本，而不是一次性代码完成项；当前已交付可复跑审计和门禁能力。

---

_设计：2026-06-16 · 基于真实代码现状核实 + 五轮迭代（含逐场景推演复审 + 四改联动验证）。_
