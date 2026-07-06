# 群聊偶尔主动发言设计

## 背景

用真实生产库(`data/nanobot.db`,16 万条 ambient 决策)验证发现:**bot 在群里从不凭规则冷启动主动开口**。

- 全量 continue 仅 0.39%;新链路(2026-06-18 起,带 scoring)里 **ambient 触发 + 纯规则产生的 continue = 0 条**。
- 数学必然:纯 ambient(无 @/reply/点名/linger)时 `direct_score≈0`,`rule_score = 0.10 + 0.85·direct − 0.85·suppress ≈ 0.10`,恒 ≤ `low = θ−0.18`,在 `decide_timing` 里被 **rule_shortcut 直接判 no_reply**,LLM 从未被咨询。
- 同一个短路,也是模型层空转的根因:新链路 32k 决策 99.6% 走 rule_shortcut,LLM 实际只被调用 64 次、其中改变最终结论 **0 次**——因为它只被叫去仲裁"分数冲突"(它的劣势场),而"这条闲聊值不值得主动接"这类**语义判断**(它的优势场)被短路挡在门外。

产品方向已确认:**bot 应当能偶尔主动发言(无需被 @),但只是偶尔。** 这是对"非召唤即沉默"原设计的有意反转。

## 目标

- 冷启动 ambient 消息在满足节流的前提下,可由 LLM 做**语义裁判**决定"值不值得主动接一句"。
- 主动发言严格受限于硬预算(每群每 N 分钟 ≤ 1 次),默认倾向沉默。
- 主动发言成功后进入**余韵(linger)模式**,让 bot 更可能就该话题继续跟进。
- 复用现有节流设施(talk_value / activity_factor / cooldown)作安全刹车,不新造节流。
- 让当前空转的 LLM 层在它擅长的语义场景真正起作用。

## 非目标

- 不改 `decide_timing` 纯函数的评分契约(主动逻辑作为 runtime 编排层的**附加旁路**,不污染评分)。
- 不做关键词匹配式的"命中词就插话"(CLAUDE.md 明令警惕的 naive 方案,必然误触发)。
- 不新增 shadow 代码开关——**协议层静默即天然影子**(见下)。
- 不改历史注入 / conversation 结构 / enriched_query 组装。

## 影子机制:协议层静默 = codebase 外的天然影子

关键约束(已确认):**发言许可在协议层,不在本 codebase。** 代码里所有群都跑完整时机链路并把决策持久化进 `chat_logs.meta_json.timing_gate`,只是"未获许可的群"在协议层不把 continue 落地成真实消息。

因此:
- **代码里不加任何 shadow/policy 分支**——所有群计算相同的主动决策、记相同的日志。
- 主动发言逻辑上线后,只在协议层"已许可发言"的 1~2 个群真正发出;其余群的主动决策照算照记但不发。
- **验证主动质量 = 读静默群的 `timing_gate` 日志**:观察这些群里"本会主动发言"的决策是否靠谱、是否过频,达标后再由协议层逐步放开更多群。

这天然实现了"shadow-first 上线",且零额外工程。

## 方案

### 触发链路(在 runtime 编排层附加旁路)

冷 ambient 现有流向:
```
冷 ambient → talk_value 门(累积器,攒够 N 条才放行)
          → _score_timing → rule_score≈0.10 ≤ low →【rule_shortcut → no_reply】← 阻塞点
```

新增旁路——**仅当上述短路给出 no_reply 时**尝试主动:
```
ambient scoring shortcut 得到 action==no_reply
  且 trigger==ambient 且 无 bot 信号 且 无 active linger（纯冷启动）
  且 通过主动节流前置检查（下）
     → 调用 LLM 主动裁判 judge_proactive(context)
        → should_speak=True  → continue + activate_linger(sender,"proactive") + 消费预算
        → should_speak=False → no_reply（同原行为）
  否则 → no_reply（同原行为）
```

设计要点:
- **纯附加**:不改动现有任何路径的结论,只给冷 ambient 的 no_reply 增加一个受严格限制的逃逸口。
- **省成本**:LLM 只在通过所有前置节流后才被调用(下),不是每条冷 ambient 都问模型。
- **并发安全**:主动 LLM 调用是 async,期间新消息到达会改变 generation;返回后复用现有 generation 校验,mismatch 则丢弃(与现有 gate 一致)。

### 主动节流前置检查(调 LLM 之前,全部为廉价规则)

1. 当前决策为 no_reply(规则本不想回)。
2. trigger_reason == "ambient" 且无 bot 指向信号 且 `_active_linger_score(state) <= 0`(纯冷启动;若 linger 已激活,走现有 linger 路径,不重复)。
3. **活跃度落在"金发姑娘带"**:群不死(最近有消息,如 `msg_5m >= 活跃下限`)且不刷屏(`msg_1m < 活跃上限`)。死群没人听、刷屏群插嘴是噪音。
4. **硬预算可用(滑动窗口计数)**:维护 `state.proactive_ts_window: list[float]`,剪掉 `PROACTIVE_WINDOW_SEC`(默认 1800s=30min)之外的时间戳,要求剩余 `len < PROACTIVE_MAX_PER_WINDOW`(默认 2)。即"每群每 30 分钟最多 2 次主动"。
5. 不在回复 cooldown 内。

全部通过才调 `judge_proactive`。

### LLM 主动裁判(新 prompt,独立于 continue/wait/no_reply 门)

- **新增独立 route_key `timing_proactive`**(而非复用 `timing_gate`)。现有模型路由基建已支持每个 route_key 独立配置模型:`call_model_route` 从 `settings.get("model.route.<key>")` 读 provider_id/model/base_url,`admin models_status` 遍历 `ROUTE_METADATA` 自动生成后台配置项。因此只需在 `core/route_metadata.py` 的 `ROUTE_METADATA` 注册 `"timing_proactive": {"type": "classifier", "label": "主动发言裁判"}`,后台"模型配置"页即自动出现该路由,可随意切换模型/base_url/key、单独限流——满足"向能随意配置模型设计"。
- 复用 `call_model_route(route_key="timing_proactive", ...)`,`max_tokens` 保持小(~80)。
- 新 prompt,语义为:"你是群成员,这条群聊消息值不值得你**主动**接一句?默认倾向不说,只有你确有相关、有价值的内容可贡献才 yes。"
- 输出 JSON:`{"should_speak": bool, "reason": "一句话"}`;解析失败 → 默认 `should_speak=False`(保守沉默)。
- 失败方向:网络/解析错误一律 no_reply。

### 主动余韵联动

主动发言成功后调 `state.activate_linger(triggering_sender_id, "proactive")`:
- 复用现有 linger 机制(180s 窗口、最多 3 条、按用户加权),不新造。
- **绑定对象**:触发这次主动的那条消息的发言者——让 bot 倾向于跟那个人把话接下去,契合"主动回复后更可能继续回复"。
- 后续该用户的消息自动走 linger 加权路径(direct_score 抬高),更易 continue。

## 数据 / 接口变更

- `core/group_runtime/state.py`:`GroupChatState` 新增 `proactive_ts_window: list[float] = field(default_factory=list)`(滑动窗口记录最近主动时间戳)。
  - 说明:in-memory,随 idle cleanup 重置;群闲置后预算自然复位,可接受。
- `core/route_metadata.py`:`ROUTE_METADATA` 注册 `"timing_proactive": {"type": "classifier", "label": "主动发言裁判"}`,使其自动出现在后台模型配置页、可独立配模型。
- `core/config_registry.py`:新增设置(category 复用 `classifier` 或新增 `proactive`)
  - `timing_gate.proactive.enabled`(bool,**默认 True**——真实安全由协议层许可保证,此开关为代码级 kill switch)
  - `timing_gate.proactive.window_sec`(int,默认 1800)
  - `timing_gate.proactive.max_per_window`(int,默认 2)
  - `timing_gate.proactive.activity_floor` / `activity_ceiling`(int,默认 3 / 20)
- 新增 `judge_proactive()`(放 `clients/classifier_client.py` 或新模块),含独立 prompt 与解析,走 `call_model_route(route_key="timing_proactive")`。
- `core/group_runtime/runtime.py` + `scoring.py`:在 ambient 短路 no_reply 后接入主动旁路。
- 主动决策结果写入 `meta_json.timing_gate`(新增 `proactive` 子字段:should_speak/reason/budget_ok),供静默群日志观测。

## 测试策略(TDD)

确定性单测(不依赖网络,mock LLM):
- `test_proactive_skipped_when_budget_exhausted`:last_proactive_ts 近 → 不调 LLM、no_reply。
- `test_proactive_skipped_when_group_dead_or_firehose`:活跃度越界 → 不调 LLM。
- `test_proactive_only_on_cold_ambient`:有 bot 信号 / linger 激活时不走主动旁路。
- `test_proactive_yes_produces_continue_and_activates_linger`:mock judge=yes → continue 且 linger 激活、绑定正确 sender。
- `test_proactive_no_keeps_no_reply`:mock judge=no → no_reply。
- `test_proactive_llm_error_defaults_silent`:LLM 抛错 → no_reply。
- `test_proactive_generation_mismatch_dropped`:主动期间新消息 → 丢弃。
- `test_proactive_disabled_setting`:enabled=False → 完全跳过。

语义质量验证:**读静默群 `timing_gate.proactive` 日志**(协议层影子),非单测。

## 验证计划

1. `python -m pytest tests/test_timing_runtime.py -v` → 0 failures。
2. `python -m pytest tests/ -q` → 全绿(不破坏现有)。
3. 上线后读静默群日志:统计主动决策频次(应符合预算)、抽查 reason 质量。达标后协议层放开更多群。

## Prompt Runtime 核查

- 本改动不动 enriched_query / 历史注入 / conversation 结构。
- 但它**改变了 bot 的发言时机行为**。须核查 `creatures/nanobot/prompt.md` 是否有"只在被叫时回复 / 不主动插话"之类描述,若有则在同一 PR 更新为"会偶尔主动参与群聊"。

## 风险与缓解

- **变吵/打扰用户**:最大风险。缓解:硬预算(默认每群每 30min ≤2 次)+ 活跃度带 + 协议层影子先行观察 + 保守默认沉默 + kill switch 设置。
- **LLM 主动误判乱说**:缓解:prompt 默认倾向沉默;先在静默群观察 reason 质量再放开;解析失败保守 no_reply。
- **LLM 成本上升**:缓解:调 LLM 前五道廉价前置节流,只有极少数冷 ambient 到达 LLM。
- **naive 关键词回潮**:明确非目标,主动裁判交给 LLM 语义判断,不加关键词规则。

## 执行顺序

- [ ] state 加 `last_proactive_ts` + 预算/活跃度前置检查(纯规则),写单测。
- [ ] `judge_proactive` LLM 裁判 + prompt + 解析,mock 单测。
- [ ] runtime 接入主动旁路(cold ambient no_reply 后),写链路单测。
- [ ] 主动成功 → activate_linger 绑定 sender,写余韵单测。
- [ ] config 设置 + kill switch。
- [ ] 主动决策写入 meta_json.timing_gate.proactive。
- [ ] 核查并按需更新 creatures/nanobot/prompt.md。
- [ ] 跑目标测试 + 全量回归。
- [ ] 中文规范提交(禁止 git add -A/.)。

## 已定决策(旋钮)

1. **预算**:每群每 30min(`window_sec=1800`)最多 2 次主动(`max_per_window=2`);活跃度带 `msg_5m>=3 且 msg_1m<20`。
2. **route_key**:新增独立路由 `timing_proactive`,复用现有可配置模型基建,后台可随意切换模型/base_url/限流。
3. **enabled 默认值**:True(代码级 kill switch;真实上线安全由协议层发言许可兜底)。
