# Nanobot Server — TODO 计划

> 本文件分两部分：**一、缺陷修复清单**（来自 2026-06-16 全仓库 Python 代码审查 + 逐条对抗式核验）；**二、架构演进路线**（2026-06-16 读真实代码核实重写，每项含现状/痛点/目标/关联/粗略路径）。
> 缺陷条目均经「读真实代码 + 引用行号」核验，已剔除 11 项误报（见附录）。严重度 = 真实可利用性，非原始审查给分。

---

## 一、缺陷修复清单

> 状态更新（2026-06-17）：P0、P1、P2 中已通过代码阅读和回归测试核验的缺陷项已标记为完成。P3 仍保留为后续可维护性和架构演进任务。

### P0 — 立即修（真实可利用 / 必崩 / 持久化故障）

- [x] **C1 认证可被完全绕过** · `api/routes.py:205` · CRITICAL · S
  `if not NANOBOT_API_TOKEN: return` —— 生产漏配 token 时，所有 `verify_token` 保护端点（`/search_logs`、`/recall` 等）对公网裸奔。admin 侧已正确改 503，主路由相反。
  **修**：启动时 fail-fast 断言 `NANOBOT_API_TOKEN` 非空；运行期未配置则 `raise HTTPException(503)`。

- [x] **C2 ai_daily 兜底分支必崩（NameError）** · `creatures/.../news_search/tool.py:1822,1827` · CRITICAL · S
  `render_html`/`FALLBACK_DIGEST` 仅在 `search_and_extract_news_v2` 内局部 import（1538-1542），`_execute` 未 import 即用。空结果/非 HTML 兜底一触发即 `NameError`——恰在「永不为空」保证最需要时崩溃。
  **修**：二者提升为模块级 import。

- [x] **E1 私聊缓冲 CancelledError 逃逸 → 永久死锁 + 协程/DB 泄漏** · `api/routes.py:2254-2314` · HIGH · M ·〔补漏新发现〕
  owner 等待区（`try`@~2185，`except Exception:`@2312）无 `finally`，而 `except Exception` 不捕获 `CancelledError`（Py3.8+ 属 BaseException）。客户端在 `await asyncio.sleep`(2273)/`await qwen_task`(2293) 期间断连 → `_finalize_private_buffer` 不执行 → `buf["done"]` 永不 set。此后该 user_id 每条私聊都命中 2215 分支并在 2257 `done_event.wait()` 永久阻塞；`_private_buffers` 无 reaper。单次断连即永久卡死该用户私聊直到进程重启。
  **修**：owner 区域改 `try/finally` 保证 `_finalize`（set done）；follower `done_event.wait()` 加 `asyncio.wait_for` 超时；`_private_buffers` 增加基于 deadline 的过期清理。

- [x] **E2 BridgePool TTL 回收停掉「在用」bridge（use-after-stop）** · `nanobot_kt/bridge.py:1995-2016` · HIGH · M ·〔补漏新发现〕
  `_bridge_last_used[key]` 只在 `_get_bridge` 入口（2016）刷新，`handle_message`（1080 行，最多 8 次模型尝试 + retry，可达数百秒）执行期间不刷新。单请求耗时 > `BRIDGE_TTL_SECONDS`(600s) 时，另一 session 进入 `_get_bridge` 会把仍在 `process_event` 的 bridge 判为 stale → `pop` + `asyncio.create_task(b.stop())`，腰斩在途请求（取消工具任务、关 LLM 客户端）。
  **修**：回收前校验空闲（`session_lock.locked()` / 引用计数 / `is_busy`），或 `handle_message` 执行期周期回写 `last_used`；绝不 stop 正在服务的 bridge。

- [x] **E4 熔断器记账 fire-and-forget 任务可被 GC** · `clients/new_api_client.py:320,641,662,690,708,817,835,867,884` · HIGH · S ·〔补漏新发现〕
  所有 `record_success/record_failure` 用 `asyncio.create_task(...)` 启动却不保存返回 Task（无 `_bg_tasks`、无 `add_done_callback`）。事件循环仅持弱引用，任务可在完成前被 GC → 熔断记账丢失，CLAUDE.md 记载的「连续 3 次失败禁用 5min」机制被破坏，坏模型持续被选中。
  **修**：维护类级 `set[asyncio.Task]` 强引用集合，`t=create_task(...); _bg.add(t); t.add_done_callback(_bg.discard)`；320 行持久化同理并记录异常。

- [x] **E6 定时任务推送失败 → 每分钟重跑昂贵 Agent** · `core/daily_digest.py:573-585` · HIGH · S ·〔补漏新发现〕
  `task.last_run_at = now` 只在 `if ok:`（push 成功）内执行，而 `_should_run` 同分钟去重依赖 `last_run_at`。当 `_generate_task_message` 成功（完整跑 KT Agent，超时上限 600s）但 `push_to_qq` 失败时，`last_run_at` 不推进，下一分钟 cron 仍匹配 → 重复完整执行 Agent，形成重试风暴（窗口内无上限）。
  **修**：内容生成成功后（push 之前）即推进 `last_run_at`；推送失败走独立、有上限的退避重试。

### P1 — 应修（确定性正确性 / 资源 / 信息泄露）

- [x] **H12 mark_logs_processed 无 rollback → 重复处理** · `core/legacy_adapter.py:283-290` · HIGH · S
  无 `except`/`rollback`，commit 失败则 `processed` 仍为 0，下轮 `get_unprocessed_logs` 重复处理同批，可能重复触发画像更新。
  **修**：`except SQLAlchemyError: db.rollback()` + 向 `evolve` 返回失败信号，使其跳过半提交的后续步骤。

- [x] **H_DIGEST_QUERY 全表加载 ChatLog 再 Python 过滤** · `core/daily_digest.py:353` · HIGH · S
  `generate_daily_digest_for_date` 用 `.all()` 无日期过滤；ChatLog 是「永不删除」的归档表，增长后 OOM/超时。
  **修**：SQL 层按 `created_at` 日期范围 `.filter()`。

- [x] **H2 timing-gate/test 同步路由阻塞线程池** · `api/admin_routes.py:961` · HIGH · S
  同步 `def` 内 `for range(repeats)` 调用阻塞分类器（urllib，15s 超时），`repeats≤20` 单请求最多占线程 ~300s。
  **修**：改 `async def` + `await asyncio.to_thread(gate.judge, ...)`，并收紧 repeats 上限。

- [x] **H17b /db/query 回显内部异常** · `api/admin_routes.py:2128,2144` · HIGH · S
  `raise HTTPException(500, str(e))` 泄露 SQL 片段/列名/SQLite 路径。
  **修**：`logger.exception(...)` + 响应统一「内部错误」。

- [x] **C5 画像提取用户内容未净化** · `core/persona_preprocess.py:210-216` · HIGH · S
  候选日志 `content` 仅 `.strip()` 即拼入 system prompt 后，可伪造 JSON 结构。下游 `_validate_evidence_log_ids` 校验 log_id 归属 + role，限制为同用户自污染、无法跨用户伪造，故非 CRITICAL。
  **修**：`sanitize_prompt_text(content, 500)`，与其余 30+ 处 prompt 边界一致。

- [x] **C4 定时任务模板未净化注入 system prompt** · `core/daily_digest.py:82` · HIGH（条件性）· S
  `task.prompt_template` 直接拼入 `<task_template>` f-string，绕过 `sanitize_prompt_text`，可用 `</task_template>` 越界。严重度取决于谁能写 `prompt_template`（经 `/chat/tasks`）——若普通用户可建定时任务则升级。
  **修**：`sanitize_prompt_text(prompt, 2000)`；并确认 `/chat/tasks` 的鉴权范围。

- [x] **H15 /search_logs limit 无上界** · `api/routes.py:1959` · HIGH · S
  `limit: int = 50` 无约束，可传任意大值触发全表扫描。
  **修**：`Query(default=50, ge=1, le=200)`。

### P2 — 性能 / 加固 / 一致性

- [x] **H7 aiohttp.ClientSession 逐请求创建** · `clients/new_api_client.py:221,621,794` · MEDIUM(perf) · M ·〔呼应路线图 §2〕
  `chat_completion`/`stream`/`fetch_models` 各自 `async with ClientSession()`，连接池失效，高并发下握手开销 + FD 抖动。
  **修**：lifespan 中创建应用级单例 session 注入 client。

- [x] **E5 cost_input_1m=None 崩溃选模型链** · `clients/new_api_client.py:462` / `clients/model_registry.py:170` · MEDIUM · S ·〔补漏新发现〕
  `m.get("cost_input_1m", 999)` 仅在 key 缺失时返回 999；`model_overrides.json` 写 `null` 经 override 合并绕过 sync 归一化（259 行），落库后 `None > max_cost` / `None / max_cost` 抛 TypeError，`get_ordered_candidates` 无 try/except → 全用户选模型 500。
  **修**：取值后 `cost = 999 if cost is None else cost`；override 合并强制 None→默认；overrides schema 校验拒 null。

- [x] **E3 b.stop() fire-and-forget 可被 GC** · `nanobot_kt/bridge.py:2006` · MEDIUM · S ·〔补漏新发现，同 E4 类〕
  **修**：强引用集合 + `add_done_callback`，或 `await asyncio.gather(*(b.stop()...), return_exceptions=True)`（已持 `_create_lock`）。

- [x] **H16 LIKE 通配符未转义** · `api/routes.py:1984-1996` · MEDIUM · S
  `.like(f"%{user_id}%")` / `content` 未转义 `%`/`_`；非经典注入（已参数化），但可强制全表扫描（配合 H15 放大 DoS）。
  **修**：转义 `% _ \` + `.like(pattern, escape="\\")`，或 `.contains(v, autoescape=True)`。

- [x] **H21 file:// 路径遍历（默认关闭）** · `nanobot_kt/image_pipeline.py:196-211` · MEDIUM · S
  `IMAGE_PREPROCESS_ALLOW_LOCAL_FILES=1` 时 `file://`/裸路径直接 `Path.read_bytes()`，可读 `/etc/passwd`。默认 False，属 opt-in 逃生口。
  **修**：`path.resolve()` 后校验在白名单沙箱目录内。

- [x] **H31 token 估算 4-5 套不一致实现** · `core/context_builder.py:104` / `core/prompts/manager.py:204` / `app/session_memory/windowing.py:40` / `section_renderer.py` / `admin_routes.py:411` · MEDIUM · M
  CJK 范围/非 ASCII 权重（0.35 vs 0.8）不一致，致截断阈值判断分歧。CLAUDE.md 言「量级判断」，但多实现漂移仍应收敛。
  **修**：抽 `core/token_utils.estimate_tokens` 单一实现，各处 import 复用。

- [x] **H17a /chat 端点回显异常** · `api/routes.py:825,868,912` · MEDIUM · S
  `HTTPException(500, str(e))` 泄露内部细节（上一行已有 `logger.error`）。**修**：detail 改通用消息 + `exc_info=True`。

- [x] **H9 ai_daily 子线程丢 trace contextvars** · `creatures/.../news_search/tool.py:500,1647` · MEDIUM · M
  `threading.Thread` 内 `asyncio.run(coro)` 不复制父 contextvars，`trace_id/run_id` 断链（功能正常，仅可观测性）。**修**：`contextvars.copy_context().run` 或 `run_coroutine_threadsafe`。

- [x] **H32 analyzer inspect.signature 兼容层** · `creatures/.../group_analysis/analyzer.py:296-307` · MEDIUM · S
  生产真实签名恒含 `prompt_key`，永走新路径无 bug；属测试耦合 + 每次分析 4 次 `inspect.signature` 异味。**修**：删兼容分支直接调用，统一两处测试 double 签名。

- [x] **M7 RAG recency 恒为 0.5（维度失效）+ 三服务 ~60 行重复** · `core/knowledge_rag.py:475` / `memory_rag.py:373` / `sticker_rag.py:475` · MEDIUM · M
  `recency` 得分硬编码 0.5，`FINAL_WEIGHTS["recency"]`(0.02-0.05) 形同虚设。**修**：基于 `updated_at` 计算时间衰减或删维度；抽 `core/semantic/rag_base.py` 去重。

### P3 — 可维护性 / 代码质量

- [ ] **H29 handle_message 1080 行 + 深嵌套** · `nanobot_kt/bridge.py:860-1940` · HIGH(可维护) · L ·〔呼应路线图 §1〕
  单函数混模型路由/重试/合同检查/tracing/stream。**修**：拆 `_run_model_loop`/`_check_reply_contract`/`_close_trace`。

- [ ] **H30 RAG query() ~337 行** · `core/knowledge_rag.py:122-459` / `core/memory_rag.py:126-349` · HIGH(可维护) · L ·〔呼应路线图 §6〕
  **修**：拆 `_recall`/`_filter_candidates`/`_rerank`/`_build_result`。

- [ ] **超大文件 >800 行拆分** · MEDIUM · L
  `admin_routes.py`(5849)、`routes.py`(2966)、`news_search/tool.py`(1831)、`context_builder.py`(907)、`group_runtime/runtime.py`(829)、`persona_preprocess.py`(856)。按职责拆模块。

- [ ] **静默吞异常补日志（best-effort 路径）** · LOW · S 批量
  `prompts/manager.py:435`(tracer)、`context_builder.py:895`(deprecated)、`admin/system_routes.py:46`(git)、`group_ingress/helpers.py:41,74`、`memory_digest/builder.py:65`。均非吞真错，补 `logger.debug` 提升可调试性即可；`H11 save_log`(legacy_adapter.py:223) 补 `except+rollback`（MEDIUM，聊天链路）。

- [ ] **ruff 批量清理** · LOW · S
  F401 未用 import ×~24（`ruff check --fix`）；F841 死变量（`classifier_client.py:1164`、`persona_update/tool.py:53,139`）；E402/F811（`admin_routes.py:34-38,89`）；`__import__("datetime")`(`model_registry.py:418,459`)；`asyncio.ensure_future`→`create_task`(`new_api_client.py:320`，并入 E4)；旧式 `List/Dict/Optional`→内置泛型；`database.py` naive datetime（单机部署，低优先）。

---

## 二、架构演进路线

> 原 1–10 编号保留为**稳定 ID**（第一节缺陷条目按 §N 引用，勿改号）。下方「实施阶段」仅表达推荐推进顺序与依赖，不改变编号。
> 贯穿性主题：**platform 维度**（QQ / 未来 Web）目前几乎处处缺失——项 3/4/5/7/9 本质都是「为多平台接入补上 platform 这一维」，宜成簇推进。

### 实施阶段总览

| 阶段 | 主线目标 | 路线项 | 关键依赖 | 关联缺陷 |
|------|----------|--------|----------|----------|
| **P1 收敛去债（地基）** | 消除多引擎/多套模板分叉，连接池与请求构造打底 | 1, 2, 3 | 项1 与 H29 同 PR | H29 / H7 / — |
| **P2 多平台底座** | 沿 platform 维度统一工具 / 消息 / 渲染 / 提示词 | 4, 5, 7, 9 | 项9 依赖项1；项7 依赖项5 | — |
| **P3 决策与流式** | 真 token 流式 + 规则化回复决策 | 6, 10 | 项6 依赖项2 连接池 | H30 / H2 |
| **P4 评测体系** | 既有 `evals/` 框架升级为基线 + 回归门禁 | 8 | 依赖前述行为先稳定 | — |

---

### P1 — 收敛与去债（地基）

#### 路线项 1 — 提示词三套引擎收敛为唯一 Prompt Runtime  ·〔关联 H29〕

- **现状（2026-06-18 已落地，保留兼容边界）**：默认 live 路径已切到唯一 Prompt Runtime 主路径：`prompt_runtime.engine` 注册默认值为 `prompt`，旧 `v1/v2` 值通过兼容层归一到 canonical runtime；`NanobotBridge` 缺省 / 非法 engine fallback 均回落 `prompt`，启动时仍初始化 `data/prompts_v2` 这个兼容物理目录。`fallback_v1` live 发送路径已禁用，残留配置仅作为废弃兼容项处理；P1-6 已封存 V1 live override，旧 `v1` settings / metadata 不再切回旧 assembler 主链路。`classifier_legacy`、`memory_extract` 与 `timing_gate` 已迁移到 task template，旧 managed / legacy 管理入口已下线为最小 410 兼容出口。P1-6 任务 6 已删除 legacy prompt 模块、旧模板目录和构建脚本，并通过引用扫描、相关回归、WebUI 构建和全量测试。任务 7 已完成无版本 canonical 命名兼容层实现与全量验证，并随 `4fe00bb refactor(提示词): 建立无版本运行时命名` 归档。
- **痛点**：主运行路径、admin API、WebUI 主入口、trace source 和默认配置已经收敛到无版本 `prompt` / `Prompt Runtime`。剩余 `prompts.v2.default`、`data/prompts_v2`、`data/prompts_v2_history`、内部包名 `core.prompt_v2`、旧 `/prompt-v2/*` API、旧 variant 和 `prompt_v2_audit_failed` 都是兼容边界；如果继续物理重命名目录或包名，需要单独处理历史运行时覆盖、旧 trace / eval 读取和兼容 alias，而不能再做无差别搜索替换。
- **目标**：当前 live 主路径只保留一套模板与一条 compile 路径，旧 assembler / legacy runtime、旧模板目录、旧 `prompt.md` 和旧构建脚本不再参与生产读取；新写入和用户可见主入口统一使用无版本 canonical 名称。后续若要清理兼容物理目录和内部包名，应作为单独迁移任务设计、导出旧覆盖数据并重新验证。
- **关联**：与 H29（bridge 巨函数拆分）同 PR；`core/prompt_v2/template_registry.py` 的 `_LEGACY_ALIASES` 改名时一并清理；`evals/`、tracing 按三套语义打点需同步改。
- **粗略路径**：① 默认 engine 切到 Prompt Runtime 并灰度验证 shadow/audit 无回归（已完成）→ ② 全仓清点 `prompt_runtime.engine`/`prompt_mode`/`legacy`/`shadow`/`managed` 引用（已完成）→ ③ `nanobot_kt/prompt_runtime.py` 收敛为单一 compile 路径，删 `_build_v1_prompt` 与 audit fallback_v1（已完成）→ ④ 删 V1/legacy 模块与冗余模板，迁移仍有价值的文案进 task template（已完成）→ ⑤ engine、settings、admin API、WebUI 主入口和 tracing 使用无版本 canonical 名称（已完成）→ ⑥ 同步 admin UI、测试和文档（已完成）。
- **实施状态（2026-06-18）**：粗略路径第 ①-⑥ 步已完成并验证，H29 第一刀已提取 `PromptRuntimeInput` 请求组装边界。P1-5「Prompt legacy 收口」已完成：live `fallback_v1` 发送路径已禁用，`reply-test` / `reply-eval` 默认与旧 alias 已转向 Prompt Runtime，legacy / managed 管理写入口已降级为只读迁移入口。P1-6 任务 1-8 已完成：旧任务 prompt 已迁移到 task template，V1 live 分支已封存，旧管理面已下线并通过回归测试，旧模块、旧模板目录、`prompt.md` 和构建脚本已从 live tree 移除；主输出、配置默认值、admin API、WebUI 主入口和 tracing 口径已收敛到无版本 `prompt` / `Prompt Runtime`。物理目录 `prompts.v2.default`、`data/prompts_v2`、内部包名 `core.prompt_v2` 和历史字段保留为兼容边界；文档同步、引用守卫、定向回归、WebUI 构建和全量测试均已通过。

#### 路线项 2 — LLM 等 IO 调用全面异步化与连接池复用  ·〔关联 H7；H1 已满足〕

- **现状（2026-06-18 已完成）**：核心 LLM 调用已是 aiohttp 异步，应用级共享 `ClientSession` 已在 lifespan 中创建并注入 `NewAPIClient`（`bootstrap/lifespan.py:30-44,66-68`），`chat_completion` / `chat_completion_stream` 已通过 `_request_session()` 复用实例或共享 session（`clients/new_api_client.py:113-120,669-674,854-861`）。已提交 `4550aca refactor(模型客户端): 支持复用注入会话` 与 `2bf4ee7 refactor(模型客户端): 接入共享会话生命周期`。分类器 / 护栏走 urllib 同步但调用点已用 `asyncio.to_thread` 卸载（H1 已满足，见附录）。P1-7 已完成：只读审计随 `8ce5210` 归档；贴纸预览 `background_tasks=None` fallback 已随 `c7e91a9` 收口；图片附件与 Direct 工具线程卸载守卫已随 `641d080` 和 `0489bac` 落地。图片附件主链路、私聊 / 群聊图片预缓存、`image_generation`、`image_summary`、`ai_daily` 和 `daily_digest_scheduler()` 均已有明确线程边界；`core/compaction.py` 当前从同步 `/context` endpoint 调用，风险是占用 worker，不是 event loop 阻塞。
- **剩余风险**：路线项 2 的 event loop 阻塞风险已完成收口。后续仍可优化 `/context` compaction 的 worker 占用、`ai_daily` 的专用 bounded executor，以及 admin / public sticker preview 同步 endpoint 的 worker 占用，但这些不阻塞路线项 2 完成。
- **目标**：保持 lifespan 应用级单例 `ClientSession` 复用连接池；残余同步 HTTP 要么已在线程边界内运行，要么在 async service 调用点显式 `asyncio.to_thread()` 卸载。P1-7 已把贴纸 fallback、图片附件和 Direct 工具同步调用纳入回归守卫。
- **关联**：H7（ClientSession 逐请求创建，P2 性能）；H1 已满足（附录）；连接池是项 6 真流式的前置。
- **粗略路径**：① lifespan 创建共享 session（已完成）→ ② new_api_client 三处 `async with ClientSession()` 改为复用注入的 session（已完成）→ ③ 审计 compaction / image / sticker / 工具层同步 IO（已完成）→ ④ 修复贴纸 `background_tasks=None` fallback 的 async 热路径风险（已完成）→ ⑤ 补图片附件与 Direct 工具 `to_thread` 回归守卫（已完成）→ ⑥ 同步文档并将路线项 2 标记为完成（当前文档收尾）。
- **验证状态（2026-06-18）**：P1-7 定向测试 `186 passed, 20 warnings`；全量测试 `1222 passed, 6 skipped, 113 warnings in 86.76s`。

#### 路线项 3 — 请求构造按模型能力校验（image_url / 多模态），能力声明入模型配置

- **现状（2026-06-18 已落地）**：模型记录已归一到顶层 `supports_image` / `supports_tools` / `supports_stream` 字段，并兼容 `model_overrides.json` 的顶层字段和嵌套 `capabilities`；`get_ordered_candidates(required_capabilities=...)` 已支持硬过滤，显式不满足能力的候选不会进入排序。直接 `NewAPIClient.chat_completion()` / `chat_completion_stream()` 已能从 messages、tools 和 stream 推导能力需求；Bridge 主回复路由也已从 `metadata["files"]`、ToolPlan schema 和 KT 固定 streaming 请求事实生成能力需求，手动回复模型不满足能力时回退自动路由。payload / SDK request 前 guard 已防止绕过候选过滤；无视觉候选时会降级为纯文本说明并重新路由，不再把 `image_url` 发给纯文本模型。`model_routing` eval 已覆盖带图请求必须选 vision 候选，防止后续改动破坏能力硬过滤。
- **剩余痛点**：路线项 3 的能力校验主链路已完成。base64 data URL 直入 payload 与 `docs/message-field-standard.md` 禁 base64 的长期方向仍需在后续出站 / 入站契约中继续收敛；图片数量 / 大小上限也应跟随多平台消息信封和出站渲染契约继续设计。
- **目标**：模型能力（`supports_image` / `supports_tools` / `supports_stream`、单图大小 / 数量上限）结构化写入模型配置；构造阶段检测 messages 含 `image_url` 时，强制只在 vision 候选中选模型，并按能力校验图片格式 / 大小，不满足则降级（剥图 + 文本兜底）或换模型，而非无脑塞。
- **关联**：呼应项 9（多模态行为描述需同步 canonical Prompt Runtime 模板）；与熔断器记账正确性（E4/E5）相关；主 reply 与 sticker_describe（走专用 vision provider）的能力口径需统一。
- **粗略路径**：① `model_overrides.json` / registry 增结构化 capabilities（已完成）→ ② 构造边界生成 `has_image` / tools / stream 信号（直接 New API 与 Bridge 已完成）→ ③ 路由在 `has_image` 时按 `supports_image` 过滤候选（已完成）→ ④ `_build_payload` / SDK request 前按能力校验 / 裁剪 image_url、stream、tools（已完成）→ ⑤ 扩展 `model_routing` eval 并统一两套 vision 机制的能力引用（已完成）。

---

### P2 — 多平台接入底座（platform 维度补全）

#### 路线项 4 — 工具配置增加 platform 维度（session 级已具备）

- **现状（2026-06-18 已落地）**：工具启用**已按多维度解析**，并非全局单一。`build_tool_plan`（`core/tool_plan.py:134`）→ `resolve_effective_tools`（`core/runtime_tool_service.py:116`）按合并顺序生效：`TOOL_METADATA` 默认(private/group) → `force_enabled`/`force_disabled_group` → `runtime_preset`(none/lightweight/full) → `ToolOverride` 表(scope_type ∈ chat_type/platform/group/user) → 硬约束兜底。后端解析已支持 `ToolOverride(scope_type="platform", scope_id="<platform>")`，顺序固定为 `chat_type < platform < group < user`；`build_tool_plan()` 和 `resolve_final_tools()` 已透传 `platform` 参数。每请求经 `record_runtime_tool_decision` 落库，`RuntimeToolDecision.platform` 字段和旧库补列迁移已落地，`/tools/decisions` 已返回 platform。真实入口也已透传：`/chat` 和群聊 `_continue_to_bridge()` 会把标准化后的 `client_meta.platform` 写入 Bridge metadata，`NanobotBridge.handle_message()` 会继续传给 ToolPlan 和运行时决策记录；对应提交为 `73bbe8a feat(消息): 透传客户端平台`。Admin API 已支持写入和预览 platform override，`/tools/effective?platform=web`、`/tools?platform=web` 和 `/tools/targets?scope_type=platform` 均具备平台口径；对应提交为 `d9a1bae feat(工具): 支持平台覆盖接口`。WebUI 工具页已支持 platform selector 和「指定平台」覆盖入口，配置侧闭环已具备；对应提交为 `2b0e203 feat(工具): 配置平台覆盖`。`docs/message-field-standard.md` 已同步说明工具策略消费 `client_meta.platform` 的规则。
- **剩余痛点**：路线项 4 的 platform 工具策略闭环已完成。下一步进入 P2-2 / P2-3，统一 `/chat`、流式 done、`/group/message` 和 push 响应信封，并把 QQ 出站渲染契约结构化，避免不同入口继续维护不兼容的响应字段和富媒体约定。
- **目标**：为工具解析增加完整 platform 维度，形成「平台 × 会话类型 × 群/用户」的工具可用性矩阵，多平台接入时各平台可独立配置工具白名单；同时保证 `runtime_preset=none`、`force_enabled` 和群聊强制禁用等硬约束不能被 platform override 绕过。
- **关联**：与项 9（platform×chat_type 提示词）、项 3（模型能力矩阵）、项 5/7（platform 化消息与渲染）同属 platform 维度，建议成簇。
- **粗略路径**：① 入口按来源注入 platform（默认 qq 向后兼容，已完成）→ ② `ToolOverride` 增 platform scope 并纳入解析顺序（已完成）→ ③ `build_tool_plan()` / `resolve_final_tools()` 透传 platform（已完成）→ ④ runtime_tool_decision / 审计带 platform（已完成）→ ⑤ Admin API 支持 platform override 创建和预览（已完成）→ ⑥ WebUI 工具配置页增平台维度（已完成）→ ⑦ 同步 `docs/message-field-standard.md` 和阶段计划（已完成）。

#### 路线项 5 — messages 接口统一为标准化请求 / 响应信封（计划已写入，兼顾 qqbot）

- **现状（2026-06-18 计划已写入）**：两个对外入口——私聊 / Web 业务入口 `ChatProxyRequest`（`api/routes.py:220`）与群聊 `GroupMessageRequest`（:1036），请求字段已基本对齐 `docs/message-field-standard.md`。但**响应不统一**：私聊 `/chat` 返回 `{status, answer, answer_chunks, ...}`（按换行在服务端拆气泡），群聊 `/group/message` 返回 `{action, reply, reply_meta, ...}`（用 `action=continue/no_reply`，正文字段叫 `reply` 且无 answer_chunks）。同一语义在请求 / 响应 / 推送三处各叫 query/message/answer/reply。P2-2 只读审计已完成，设计文档已随 `c984036 docs(消息): 设计响应信封标准` 提交；实现计划已写入 `.Codex/plans/message-envelope.md`，采用接口先行、API / 群聊 / push owner 分工和兼容双写方案：新增 `reply`、`messages`、`reply_meta`、`meta`，同时保留旧字段。
- **痛点**：私聊与群聊响应是两套不兼容 schema，调用方按入口分别处理；私聊路径 `reply_meta`（send_mode/quote/at）只取出做审计、**从不进响应**，AI 发送意图丢失；answer_chunks 拆分只在私聊侧；client_meta 是「君子协定」，新平台易塞裸 ID / 整份 event。
- **目标**：两入口共享一套标准化请求 / 响应信封 `{status|action, messages|reply, reply_meta, meta}`，正文字段名统一，私聊也带过滤后的 reply_meta；client_meta 由文档约定升级为运行时轻量 schema（至少校验 platform / chat_type / trace.request_id）。
- **关联**：与项 7（reply_meta 是渲染约定的载体）强耦合，建议合并设计；`push_to_qq`（`core/daily_digest.py:498`）是第三条出口，统一时不可漏。
- **粗略路径**：① 完成四出口只读审计和响应信封设计（已完成）→ ② 写入 `.Codex/plans/message-envelope.md` 实现计划（已完成）→ ③ 抽统一响应信封模型，让 `/chat` 非流式、`/chat` 流式 done、`/group/message`、push 四出口同形态 → ④ 私聊路径接入过滤后的 reply_meta → ⑤ answer_chunks 拆分提取为共享 helper → ⑥ client_meta 增边界层解析 / 校验 → ⑦ 给 `message-field-standard.md` 补响应侧标准。

#### 路线项 7 — qqbot 端出站渲染契约（与入站对称的结构化输出）

- **现状**：qq 端渲染**无显式协议**，靠两类隐式约定拼凑。① 富媒体内联 OneBot CQ 码字符串：sticker 在**工具层**展开 `[sticker:id]→[CQ:image,file=...]`（`reply/tool.py:88`、`sticker_memory.py:107`），生成图 `[generated_image:token]` 在**传输出口层**展开为 `[CQ:image,file=URL]`/base64（`generated_images.py:349-380`，优先公开代理 URL）；近三次提交已对所有 QQbot 出口统一 `allow_base64=False`。② 发送行为靠 reply_meta（send_mode/quote/at/mentions，`reply/tool.py:9`），群聊响应透传给 qqbot，私聊不带。③ HTML 报告 `is_html_reply` 不截断，靠 qqbot 端 `html_to_pic` 渲染。
- **痛点**：图片展开分散在工具层(sticker)与传输层(generated_image)两套机制；入站是结构化 segments、出站却是裸 CQ 字符串（不对称，下游需反解析）；`NANOBOT_PUBLIC_BASE_URL` 未配置时保留无法渲染的短 token（约定不闭环）；send_mode/quote/at 在私聊丢失；HTML / 文本 / 图片渲染分支判断散落多处。`docs/message-field-standard.md` 只规范入站，**完全没有出站渲染约定**。
- **目标**：定一份「qq 端出站渲染契约」，以结构化 segments（text/image/html/at/reply）输出（与入站 `GroupMessageRequest.segments` 对称），富媒体统一走公开代理 URL（base64 仅作显式 fallback），图片展开收敛到单一出口层，发送指令私聊群聊一致下发。
- **关联**：与项 5 共享 reply_meta / 响应信封载体，强烈建议合并设计；落地时为 `message-field-standard.md` 补「出站渲染契约」一节。
- **粗略路径**：① 盘点「内容类型 → qq 渲染」映射表 → ② 定义出站 segments 契约或集中 CQ renderer 模块 → ③ sticker 与 generated_image 展开收敛到统一出站层（保留不污染 conversation/token 的约束）→ ④ 明确 base URL 未配置时降级策略 → ⑤ reply_meta 纳入信封、私聊也下发 → ⑥ 同步 canonical Prompt Runtime 模板中的 reply 工具发送约定描述。

#### 路线项 9 — 提示词模板按 platform × chat_type 二维适配  ·〔依赖项 1〕

- **现状**：提示词**无 platform 维度**，唯一区分维度是 chat_type ∈ {group, private}，且文案写死 QQ。V2 编排图只认 `CHAT_TYPES={'group','private'}`（`core/prompt_v2/flow.py:26`，强校验，传其它值即 `PromptFlowError`），分支模板开头硬编码「当前对话发生在 QQ 群聊 / 私聊中」（`branch_group.md`/`branch_private.md`/`main.md`）；bridge 仅由 `is_group` 推 chat_type（`bridge.py:1041`），`PromptCompileRequest`/`PromptRuntimeInput` 均无 platform 字段。
- **痛点**：平台耦合写死在自然语言里、散落四处；flow chat_type 是封闭二值枚举，不改 schema 无法扩展；QQ 特有约定（msg_id / 表情包 / @ 机制）混在通用群聊规则里，Web 端不成立；三套模板都无平台维度（见项 1），现在加要在三处同时改。
- **目标**：组装从一维(chat_type)升级为二维(platform×chat_type)，platform ∈ {qq, web, ...} 可扩展；平台无关规则（输出契约 / 安全 / 风格）留公共模板，平台相关约定（QQ 的 msg_id/表情/@、Web 的 markdown/富文本）下沉 platform 专属分支，bridge/schema 全链路透传 platform。
- **关联**：**依赖项 1** 先收敛单引擎，否则要在 legacy/managed/v2 三套上同时加平台维度，成本极高；与项 4/5/7 同属 platform 维度簇。
- **粗略路径**：① schema / `PromptRuntimeInput` / bridge meta 增 platform 字段(默认 qq) → ② `flow.py` 节点 / 边支持 platform（或合成 `qq_group`/`web_private` 复合 key），放宽 `CHAT_TYPES` 强校验 → ③ 拆模板：QQ 专属约定抽到 `platform/qq/*.md`，`main.md` 去 QQ 字样 → ④ compiler `ordered_nodes_for_chat` 按 (platform, chat_type) 过滤 → ⑤ 入口按来源注入 platform → ⑥ 补 group/private × qq/web 渲染快照测试。

---

### P3 — 决策与流式优化

#### 路线项 6 — SSE 真 token 流式重构（stream 参数全链路贯穿）  ·〔关联 H30〕

- **现状（2026-06-17 已部分落地）**：API 层已有 stream 开关（`ChatProxyRequest.stream`）与 SSE 出口 `_stream_chat`，`stream` 已贯穿 API → BridgePool → Bridge → KT `Message`。`BufferedOutput.write_stream()` 会向 SSE 队列发送 `delta` 事件，`done.answer` 仍作为最终业务权威结果。`/chat-step` 也已接入 `run_agent_step_stream()`，通过 `NewAPIClient.chat_completion_stream()` 下发 final-answer delta，并在工具选择阶段拼合流式 tool call 后发送最终 `tool_call` 事件（`2369081 feat(agent): 支持 step 流式输出`）。生产 reply 链路仍通过 KT OpenAI provider 的 streaming 迭代输出进入 `BufferedOutput`。
- **痛点**：多工具回合下，增量文本可能先于最终 `reply()` 工具合同出现，前端必须把 `done.answer` 视为权威结果；chunk 粒度目前直接跟随 provider，尚未做小窗口合并或 backpressure；`/group/message` 与 QQbot 出站渲染仍不是同一套响应信封。
- **目标**：保持 `stream` 参数全链路贯穿，SSE 稳定下发增量 token；继续收敛 chunk 合并、工具回合语义和响应信封，使 Web SSE、QQbot 推送与最终持久化共享同一套输出契约（兼顾 QQbot 单 chunk 大小限制与 base64 禁用约定）。
- **关联**：H30（RAG `query()` 巨函数拆分，便于流式分段）；依赖项 2 连接池；与项 5 响应信封、项 7 渲染（增量 chunk 如何渲染）协同。
- **粗略路径**：① 已完成 API / Bridge / KT Message / BufferedOutput 的 stream 贯穿 → ② 已完成 `/chat-step` SSE 增量输出与流式 tool call 拼合 → ③ 为 provider chunk 增加可选合并窗口与 backpressure 策略 → ④ 明确工具回合 / reply 合同与增量事件的前端展示规则 → ⑤ 与路线项 5/7 合并响应信封和出站渲染契约 → ⑥ 约定 chunk 大小与图片 token 展开时机。

#### 路线项 10 — TimingGate 引入「规则信号 + 模型」混合决策  ·〔关联 H2〕

- **现状（2026-06-17 已落地）**：核心链路已从「纯 Qwen 三态判断」推进到 scoring 混合决策。已新增 `core/timing_score.py`，覆盖 `d0/linger/s_ack/s_transport/s_other/w_*` 信号、`E_rule/E_final`、冲突升级、模型权重和 `rule_fallback`；`GroupRuntime` 已接入 shadow scoring、普通 ambient 确定性短路、模型失败规则兜底、`directed_to_other` scoring 软化、ambient / legacy / timer cooldown scoring 短路，以及 session / platform 级模型层策略。私聊已接入同一套 shared timing scoring，分类器结果回灌为 `TimingModelHint`。`timing_scoring` 已写入 ChatLog meta 并由 admin events / WebUI 调试页透出，`evals` 也能在 action 缺失时执行 scoring 并校验 `expected.scoring`。
- **已完成**：`@bot + 图片` 规则 WAIT 不调模型；纯 ambient / 纯确认可规则 `no_reply`；`directed_to_other + linger` 进入冲突升级；模型 `network_error/parse_error` 后使用规则侧 `rule_fallback`，不再全群哑火；`s_ack` 排除请求词、问号、URL、代码、文件；`s_transport` 已按 secret/blob/url/codeblock/long dump 分档；`force_next_continue` 已降级为 `d0=1.0` 后完整走 Stage 1-4；`enabled` / `rules_only` / `shadow` 模型策略已支持 default / platform / session 三级覆盖；真实 ChatLog 信号审计 CLI 已输出假阳率、shadow mismatch 和阈值建议；`timing_gate` eval 已支持 baseline diff 和阈值门禁。
- **剩余**：核心混合决策主线已完成。后续只保留持续运营项：用更多人工标注样本复跑 `timing_signal_audit` 并按报告调参；把 `python -m evals.run --suite timing_gate --baseline ... --min-pass-rate ... --max-new-failures ...` 接入外部 CI；继续与路线项 5/7 的响应信封和调试可观测协同。
- **关联**：H2 已完成 admin route 异步化和 repeats 收紧；后续与路线项 8（评测体系）、路线项 5/7（响应信封与调试可观测）继续协同。
- **下一步**：进入文档和运营收尾：复跑真实日志审计、按标注结果调阈值，并将 timing gate eval 门禁接入外部 CI。

---

### P4 — 评测体系

#### 路线项 8 — 评测体系从既有 `evals/` 框架升级为基线 + 回归门禁（大工程）

- **现状（2026-06-17 已部分升级）**：评测框架**已存在**而非空白：`evals/run.py`（CLI `python -m evals.run --suite <name>`）+ `schema.py`（`EvalCase`/`EvalOutput`/`EvalResult`/`SuiteReport` pydantic）+ `scorers.py` + `runners/`（sticker / memory / moderation / model_routing 等 per-suite runner）+ `cases/`（regression 10 例、rag_benchmark/manual、timing_gate 多例、candidates）+ `sample_from_db.py`/`sample_from_logs.py`（从库 / 日志采样造例）。`evals/baseline.py` 已提供 baseline diff 与阈值门禁，`run.py` 已支持 `--baseline`、`--min-pass-rate`、`--max-new-failures`，`SuiteReport` 可携带 `baseline_diff` 与 `gate`。
- **痛点**：baseline diff 和门禁能力已具备，但覆盖仍偏核心 suite；外部 CI 尚未接入；提示词质量 / 回复合同 / RAG 召回 / TimingGate 各有少量 case 但无系统化标注数据集与人工评分回流；候选 case(`candidates`，needs_label) 标注流程未闭环。
- **目标**：把既有 `evals/` 升级为体系——统一指标与基线快照、回归对比门禁（PR 跑核心 suite 并比对 pass_rate / score 漂移）、分能力数据集（提示词 / 路由 / RAG / TimingGate / 渲染）、人工标注回流与 `candidates → labeled` 闭环。
- **关联**：依赖项 1 / 6 / 10 等行为先稳定（否则基线频繁失效）；与项 10 共享 timing_gate 套件、项 3 共享 model_routing 套件。
- **粗略路径**：① 固化基线快照与指标口径（已完成 timing_gate 核心 suite）→ ② `run.py` 增 baseline diff + 阈值门禁（已完成）→ ③ 接入外部 CI / PR gate → ④ 扩 per-capability 数据集（`sample_from_logs` 批量造例 + 人工标注）→ ⑤ 打通 candidates 标注闭环 → ⑥ 关键 suite 纳入提交前 / PR 必跑。

---

## 附录 — 已核验为误报 / 不适用（勿重复审查）

| 原编号 | 描述 | 结论 |
|--------|------|------|
| H1 | 分类器同步 IO 阻塞事件循环 | **误报**：所有 async 调用点已 `asyncio.to_thread` 卸载 |
| H10 | tracing `db` 未定义 NameError + 连接泄漏 | **误报**：assign-then-try 结构已规避，`_session()` 抛错不进 finally |
| H14 | ThreadPoolExecutor 未用 with 致泄漏 | **误报**：try/finally 已覆盖 BaseException；改 with 反而变 wait=True |
| H34 | lambda 闭包捕获循环变量 | **误报**：`run_sqlite_locked_retry` 同步立即执行，无延迟绑定 |
| H_SVC365 | service.py:365 吞图片展开异常 | **误报**：已有 `logger.warning(exc_info=True)`（审查 agent 自我撤回） |
| H28 | 新闻日期解析吞异常致静默产空 | **误报**：多格式回退链，`is_time_unknown` 字段下游可区分 |
| M13 | ContextVar `default={}` 共享污染 | **误报**：`.get({})` 每次新字面量已遮蔽构造默认值 |
| H3 | `asyncio.Lock()` 类属性绑错 loop | **不适用**：Py3.10+ 不在构造时绑 loop，cpython-313 安全 |
| H13 | 迁移单一事务 + FTS5 炸事务 | **误报**：SQLite 3.45 事务内 FTS5 正常；单一事务与 `_record` 原子性是正确设计 |
| H_ORDERBY/M11/M12 | DDL/PRAGMA f-string 拼接 | **仅加固**：全硬编码常量 + 白名单，当前不可注入 |
| H4/H6 | asyncio 锁竞态（sync_is_disabled/_get_nli） | **降级 LOW**：单线程 asyncio 临界区无 await 即原子；仅潜在隐患 |

---

_审查与核验：2026-06-16 · 5 路并行 python-reviewer + 32 agent 对抗式核验工作流（含补漏扫描）+ 人工复核 P0/新发现。_
