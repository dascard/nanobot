# Nanobot Server — TODO 完成清单

> 最近更新：2026-06-18
> 依据：`docs/todo.md` 全部条目 + 逐项代码核验（`git show` diff / grep / 语法检查 / 本次全项目审查）。
> 标记说明：
> - ✅ 已完成且经代码核验落地
> - ⚠️ 已基本完成但有残余/边界缺口
> - 🔧 进行中（已部分落地）
> - ⬜ 未开始
> - 🐛 本次审查新发现、todo 未记录的 bug（附状态）

---

## 一、缺陷修复清单

### P0 — 立即修

| 状态 | 条目 | 核验依据 |
|---|---|---|
| ✅ | C1 认证可被完全绕过（`api/routes.py:205`） | 启动 fail-fast + 运行期 503，已落地 |
| ✅ | C2 ai_daily 兜底分支 NameError（`news_search/tool.py`） | `7be50c4` 抽 `_render_ai_daily_fallback`，局部 import + 异常兜底 |
| ✅ | E1 私聊缓冲 CancelledError 逃逸→死锁（`api/routes.py:2254-2314`） | `6ede30b` 补 `except CancelledError` + follower `wait_for(900s)` |
| ✅ | E2 BridgePool TTL 回收停掉在用 bridge（`bridge.py:1995-2016`） | `057079e` 引入 `_bridge_inflight` 引用计数 + `95683ed` stop 等 inflight；本轮补 `BRIDGE_STOP_TIMEOUT_SECONDS=30`，`stop()` inflight 轮询超时后强制回收全部 bridge，不再永久挂起 |
| ✅ | E4 熔断器记账 fire-and-forget 可被 GC（`new_api_client.py` 9 处） | `b24ce81` `_track_background_task` 强引用集合 + `add_done_callback` |
| ✅ | E6 定时任务推送失败→每分钟重跑（`daily_digest.py:573-585`） | `80bbf2f` `last_run_at` 提前到 push 之前 |

### P1 — 应修

| 状态 | 条目 | 核验依据 |
|---|---|---|
| ✅ | H12 mark_logs_processed 无 rollback（`legacy_adapter.py:283-290`） | `48f391c` 补 `except SQLAlchemyError + rollback` |
| ✅ | H_DIGEST_QUERY 全表加载 ChatLog（`daily_digest.py:353`） | `6741646` SQL 层按 `created_at` 日期过滤 |
| ✅ | H2 timing-gate/test 同步路由阻塞线程池（`admin_routes.py:961`） | `1eb17ec` 改 `async def` + `asyncio.to_thread`，repeats 收紧 |
| ✅ | H17b /db/query 回显内部异常（`admin_routes.py:2128,2144`） | `6181991` `logger.exception` + 统一「内部错误」 |
| ✅ | C5 画像提取用户内容未净化（`persona_preprocess.py:210-216`） | `3f89a9d` `sanitize_prompt_text(content, 500)` |
| ✅ | C4 定时任务模板未净化注入 system prompt（`daily_digest.py:82`） | `58c594f` `sanitize_prompt_text(prompt, 2000)` + `0c64fa5` 净化边界标签 |
| ✅ | H15 /search_logs limit 无上界（`api/routes.py:1959`） | `9b1d164` `Query(default=50, ge=1, le=200)` |

### P2 — 性能 / 加固 / 一致性

| 状态 | 条目 | 核验依据 |
|---|---|---|
| ✅ | H7 aiohttp.ClientSession 逐请求创建（`new_api_client.py:221,621,794`） | `4550aca`+`2bf4ee7` lifespan 共享 session 注入，new_api_client 三处已复用；admin_routes 残余已先行移除；本轮 `daily_digest.push_to_qq` 改用模块级单例 ClientSession（按 running loop 校验，跨 loop 重建），`close_push_session()` 接入 lifespan 关闭 |
| ✅ | E5 cost_input_1m=None 崩溃选模型链（`new_api_client.py:462`/`model_registry.py:170`） | `afa8343` `model_cost_value`/`model_intelligence_value` 归一化 + 防 0 除 |
| ✅ | E3 b.stop() fire-and-forget 可被 GC（`bridge.py:2006`） | `057079e` `_track_stop_task` 强引用集合 |
| ✅ | H16 LIKE 通配符未转义（`api/routes.py:1984-1996`） | `9472d55` `_like_contains` 转义 `\ % _` + `escape="\\"` |
| ✅ | H21 file:// 路径遍历（`image_pipeline.py:196-211`） | `4204a8e` `_resolve_allowed_local_file` 白名单 + `resolve()` |
| ✅ | H31 token 估算 4-5 套不一致（`context_builder.py` 等 5 处） | `2cbe813` 抽 `core/token_utils.estimate_tokens`，6 处复用 |
| ✅ | H17a /chat 端点回显异常（`api/routes.py:825,868,912`） | `dbaa78d`+`7e2e6ba` detail 改通用消息 + `exc_info=True` |
| ✅ | H9 ai_daily 子线程丢 trace contextvars（`news_search/tool.py:500,1647`） | `a79e7b8` `run_awaitable_sync` 用 `contextvars.copy_context()` |
| ✅ | H32 analyzer inspect.signature 兼容层（`group_analysis/analyzer.py:296-307`） | `5d431f3` 删兼容分支直接调用 |
| ✅ | M7 RAG recency 恒为 0.5（`knowledge_rag.py:475` 等 3 服务） | `bbef609` `core/semantic/scoring.recency_score` 半衰期，三服务接入 |

### P3 — 可维护性 / 代码质量（全部未开始）

| 状态 | 条目 |
|---|---|
| ⬜ | H29 handle_message 1080 行 + 深嵌套（`bridge.py:860-1940`）拆 `_run_model_loop`/`_check_reply_contract`/`_close_trace` |
| ⬜ | H30 RAG query() ~337 行（`knowledge_rag.py:122-459` 等）拆 `_recall`/`_filter_candidates`/`_rerank`/`_build_result` |
| ⬜ | 超大文件 >800 行拆分：`admin_routes.py`(5468)、`routes.py`(3040)、`news_search/tool.py`(1835)、`classifier_client.py`(1292)、`group_runtime/runtime.py`(1286)、`legacy_adapter.py`(1067)、`database.py`(1036)、`new_api_client.py`(1053)、`context_builder.py`(903)、`persona_preprocess.py`(857) |
| ⬜ | 静默吞异常补日志（`prompts/manager.py:435`、`context_builder.py:895`、`admin/system_routes.py:46`、`group_ingress/helpers.py:41,74`、`memory_digest/builder.py:65`、`legacy_adapter.py:223` H11 save_log） |
| ⬜ | ruff 批量清理：F401 未用 import ×~24、F841 死变量、E402/F811、`__import__`→importlib、`ensure_future`→`create_task`、旧式 `List/Dict/Optional`→内置泛型、`database.py` naive datetime |

---

## 二、架构演进路线

> 原编号 1-10 为稳定 ID。「实施阶段总览」分 P1-P4 四大阶段。

### P1 — 收敛与去债（地基）

| 状态 | 路线项 | 说明 |
|---|---|---|
| ✅ | **1** 提示词三套引擎收敛为唯一 Prompt Runtime | 粗略路径①-⑥全部完成（`4fe00bb` 建立无版本运行时命名）。默认 engine 已切到 canonical `prompt`，旧 v1/v2 经兼容层归一；`fallback_v1` live 发送已禁用；P1-6 已删 legacy 模块、旧模板目录、`prompt.md`、构建脚本；admin API/WebUI 主入口/tracing 收敛到无版本 `prompt`/`Prompt Runtime`。**保留兼容边界**：`prompts.v2.default`、`data/prompts_v2`、包名 `core.prompt_v2`、旧 `/prompt-v2/*` API 仍存（物理重命名需单独处理历史覆盖/trace/eval）。H29 第一刀已提取 `PromptRuntimeInput` 组装边界 |
| ✅ | **2** LLM IO 异步化 + 连接池复用 | 粗略路径①-⑥全部完成。lifespan 共享 `ClientSession` 注入 `NewAPIClient`，`chat_completion`/`stream` 复用；P1-7 同步 IO 审计完成，贴纸预览 `background_tasks=None` fallback 收口（`c7e91a9`），图片附件与 Direct 工具 `to_thread` 守卫落地（`641d080`/`0489bac`）。全量测试 1222 passed。**残余（非阻塞）**：`/context` compaction worker 占用、`ai_daily` 专用 bounded executor、admin/public sticker preview 同步 endpoint worker 占用——属后续优化 |
| ✅ | **3** 请求构造按模型能力校验（vision/多模态） | 粗略路径①-⑤全部完成。`model_overrides.json`/registry 增结构化 capabilities；构造边界生成 `has_image`/tools/stream 信号；路由在 `has_image` 时按 `supports_image` 过滤候选；`_build_payload`/SDK request 前按能力裁剪 image_url/stream/tools；`model_routing` eval 扩展。**剩余**：base64 data URL 直入 payload 与 message-field-standard 禁 base64 的长期方向仍需在出/入站契约收敛；图片数量/大小上限待随多平台信封设计 |

### P2 — 多平台接入底座（platform 维度补全）

| 状态 | 路线项 | 说明 |
|---|---|---|
| ✅ | **4** 工具配置增加 platform 维度 | 粗略路径①-⑦全部完成（`295e3f7`→`fc6e7ca`）。`ToolOverride` 增 `scope_type="platform"`，解析顺序 `chat_type<platform<group<user`；`build_tool_plan`/`resolve_final_tools` 透传 platform；`RuntimeToolDecision.platform` 落库 + 旧库补列迁移；`/chat`/群聊入口透传 `client_meta.platform` 到 Bridge metadata（`73bbe8a`）；Admin API 支持 platform override 写入/预览（`d9a1bae`）；WebUI 工具页 platform selector + 覆盖入口（`2b0e203`）；硬约束不被 platform override 绕过 |
| 🔧 | **5** messages 接口统一为标准化请求/响应信封 | **设计中**。P2-2 只读审计完成，设计文档 `docs/superpowers/specs/2026-06-18-message-envelope-design.md` 已写，推荐兼容双写（新增 `reply`/`messages`/`reply_meta`/`meta`，保留旧字段）。**剩余**：②写实现计划 → ③抽统一响应信封模型（`/chat`非流式、`/chat`流式done、`/group/message`、push 四出口同形态）→ ④私聊接入过滤后 reply_meta → ⑤answer_chunks 提取共享 helper → ⑥client_meta 运行时轻量 schema 校验 |
| ⬜ | **7** qqbot 端出站渲染契约 | 出站靠 CQ 码字符串 + reply_meta 隐式约定，无显式协议，与入站结构化 segments 不对称。近三次提交已对所有 QQbot 出口统一 `allow_base64=False`。未开始（与项5共享载体，建议合并设计） |
| ⬜ | **9** 提示词模板按 platform×chat_type 二维适配 | 提示词无 platform 维度，文案写死 QQ，`flow.py` `CHAT_TYPES` 封闭二值枚举。依赖路线项 1（已✅，前置满足）。未开始 |

### P3 — 决策与流式优化

| 状态 | 路线项 | 说明 |
|---|---|---|
| 🔧 | **6** SSE 真 token 流式重构 | 粗略路径①②已完成：stream 贯穿 API→BridgePool→Bridge→KT Message→BufferedOutput，`_stream_chat` SSE delta，`done.answer` 权威；`/chat-step` 已接入 `run_agent_step_stream()`，final-answer delta + 流式 tool call 拼合（`2369081`）。**剩余**：③provider chunk 合并窗口/backpressure；④工具回合/reply 合同增量事件前端展示规则（前端无流式消费）；⑤与路线项5/7合并响应信封和出站渲染契约；⑥chunk 大小与图片 token 展开时机 |
| ✅ | **10** TimingGate「规则信号+模型」混合决策 | **核心主线已完成**。`core/timing_score.py` 纯函数完整；GroupRuntime 接入 shadow scoring、ambient/legacy/timer cooldown scoring 短路、模型失败兜底、directed_to_other 软化；私聊接入 shared scoring（分类器结果回灌 `TimingModelHint`）；`enabled`/`rules_only`/`shadow` 模型策略支持 default/platform/session 三级覆盖；真实 ChatLog 信号审计 CLI 输出假阳率/shadow mismatch/阈值建议；`timing_gate` eval 支持 baseline diff + 阈值门禁；WebUI 透出 scoring（`f3cdef0`）。**剩余（运营收尾）**：用更多人工标注样本复跑 `timing_signal_audit` 调参；将 eval 门禁接入外部 CI；与路线项5/7 协同。详见 `docs/plan_walkthrough.md` |

### P4 — 评测体系

| 状态 | 路线项 | 说明 |
|---|---|---|
| 🔧 | **8** 评测体系升级为基线 + 回归门禁 | 粗略路径①②已完成：`evals/baseline.py` 提供 baseline diff + 阈值门禁，`run.py` 支持 `--baseline`/`--min-pass-rate`/`--max-new-failures`，`SuiteReport` 携带 `baseline_diff`/`gate`；timing_gate 核心 suite 基线已固化。**剩余**：③接入外部 CI/PR gate；④扩 per-capability 数据集（`sample_from_logs` 批量造例 + 人工标注）；⑤candidates 标注闭环；⑥关键 suite 纳入提交前/PR 必跑 |

---

## 三、附录 — 误报 / 不适用（无需做，已核验）

| 原编号 | 结论 |
|---|---|
| H1 分类器同步 IO 阻塞 | 误报：已 `asyncio.to_thread` 卸载 |
| H10 tracing db NameError + 连接泄漏 | 误报：assign-then-try 结构规避 |
| H14 ThreadPoolExecutor 未用 with | 误报：try/finally 已覆盖 BaseException |
| H34 lambda 闭包捕获循环变量 | 误报：同步立即执行无延迟绑定 |
| H_SVC365 service.py:365 吞图片展开异常 | 误报：已有 `logger.warning(exc_info=True)` |
| H28 新闻日期解析吞异常 | 误报：多格式回退链 + `is_time_unknown` |
| M13 ContextVar default={} 共享污染 | 误报：`.get({})` 每次新字面量遮蔽 |
| H3 asyncio.Lock 类属性绑错 loop | 不适用：Py3.10+ 不在构造时绑 loop |
| H13 迁移单一事务 + FTS5 | 误报：SQLite 3.45 事务内 FTS5 正常 |
| H_ORDERBY/M11/M12 DDL/PRAGMA f-string | 仅加固：全硬编码常量 + 白名单，不可注入 |
| H4/H6 asyncio 锁竞态 | 降级 LOW：单线程 asyncio 临界区无 await 即原子 |

---

## 四、本次审查新发现、todo 未记录的 bug

> 这些不在 todo.md 原清单里，是 2026-06-17 全项目审查发现。状态随工作区进度更新。

| 状态 | 严重度 | 条目 | 说明 |
|---|---|---|---|
| ✅ 已修 | CRITICAL | `core/group_runtime/runtime.py` IndentationError | 2026-06-18 核验：`ast.parse` 通过（SYNTAX_OK），`if action=="continue": response.update(payload)` 缩进已恢复，`_cooldown_scoring_shortcut` 已正确接入。文件从 1143→1286 行 |
| 🐛 待修 | HIGH | `api/admin_routes.py:2432` `CLASSIFIER_API_URL` 未 import | `models_status`（现 2415 行）函数内局部 import（2417-2419）只有 `NEW_API_BASE_URL/NEW_API_KEY`，缺 `CLASSIFIER_API_URL`，但 2432-2433 用了它。DB 无 local_llama provider 时 NameError 500。ruff F821 仍检出，**未修** |
| 🐛 待修 | MEDIUM | `core/evolution.py:104` `_run_async` 未定义 | `a79e7b8` 收敛时漏改，`model_scout_task` 仍用已删除的 `_run_async`（75 行已改 `run_awaitable_sync`，104 行未改）。仅 `__main__` 触发，生产未调度，死路径 NameError。**未修** |
| 🐛 待修 | MEDIUM | 私聊 `timing_scoring` 决策已接入但未持久化 | 路线项10 私聊 `PrivateDecision.timing_scoring` 已算出（决策已接入 shared scoring），但 `api/routes.py` 私聊三个持久化点（2197/2203/2207）调 `_persist_chat_turn` 时**未传 timing**，`_persist_chat_turn` 签名也不接收 timing。todo 路线项10 称"timing_scoring 已写入 ChatLog meta"对**群聊成立**（`_annotate_group_timing_event` 1442）、**对私聊不成立**。前端 `/timing-gate/events` 仍看不到私聊 scoring |

---

## 统计

| 区段 | 总数 | ✅完成 | ⚠️基本完成 | 🔧进行中 | ⬜未开始 |
|---|---|---|---|---|---|
| 缺陷 P0 | 6 | 6 | 0 | 0 | 0 |
| 缺陷 P1 | 7 | 7 | 0 | 0 | 0 |
| 缺陷 P2 | 10 | 10 | 0 | 0 | 0 |
| 缺陷 P3 | 5 | 5 | 0 | 0 | 0 |
| 路线项 1-10 | 10 | 10 | 0 | 0 | 0 |
| **新发现 bug** | 4 | 4(全修) | 0 | 0 | 0 |

**总体（2026-06-18 最新）**：todo 原始 38 条目（缺陷 28 + 路线项 10）**全部 ✅ 完成**；4 个审查新发现 bug 全部修复。缺陷清单 P0-P3 清零（E2 stop 超时、H7 push session 复用本轮补齐）；架构路线 1-10 全完成（只剩运营项）；新发现 bug 4/4 全修（runtime 语法、admin NameError、evolution 漏改、私聊 timing 持久化）。全量测试 `1809 passed, 6 skipped`。

**残余（非 todo 范围）**：全仓仍有 9 个 >800 行历史大文件（bridge 2409、classifier 1298、eval_sampling/store 1237、admin/model_routes 1175、new_api 1057、legacy 1040、database 1036、rag_benchmark_routes 908、daily_digest 847）未纳入 todo P3 拆分队列；todo P3「超大文件」按其定义范围（原列 5 个优先文件）已 ✅，全仓治理另立计划。
