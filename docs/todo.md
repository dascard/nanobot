# Nanobot Server — 待办计划

> 本文件分两部分：**一、缺陷修复清单**（来自 2026-06-16 全仓库 Python 代码审查 + 逐条对抗式核验）；**二、架构演进路线**（2026-06-16 读真实代码核实重写，每项含现状/痛点/目标/关联/粗略路径）。
> 缺陷条目均经「读真实代码 + 引用行号」核验，已剔除 11 项误报（见附录）。严重度 = 真实可利用性，非原始审查给分。

---

## 一、缺陷修复清单

> 状态更新（2026-06-17）：P0、P1、P2 中已通过代码阅读和回归测试核验的缺陷项已标记为完成。P3 仍保留为后续可维护性和架构演进任务。

### P0 — 立即修（真实可利用 / 必崩 / 持久化故障）

- [x] **C1 认证可被完全绕过** · `api/routes.py:205` · CRITICAL · S
  `if not NANOBOT_API_TOKEN: return` —— 生产漏配 token 时，所有 `verify_token` 保护端点（`/search_logs`、`/recall` 等）对公网裸奔。admin 侧已正确改 503，主路由相反。
  **修**：启动时 fail-fast 断言 `NANOBOT_API_TOKEN` 非空；运行期未配置则 `raise HTTPException(503)`。

- [x] **C2 ai_daily 兜底分支必崩（NameError）** · `creatures/<省略路径>/news_search/tool.py:1822,1827` · CRITICAL · S
  `render_html`/`FALLBACK_DIGEST` 仅在 `search_and_extract_news_v2` 内局部 import（1538-1542），`_execute` 未 import 即用。空结果/非 HTML 兜底一触发即 `NameError`——恰在「永不为空」保证最需要时崩溃。
  **修**：二者提升为模块级 import。

- [x] **E1 私聊缓冲 CancelledError 逃逸 → 永久死锁 + 协程/DB 泄漏** · `api/routes.py:2254-2314` · HIGH · M ·〔补漏新发现〕
  owner 等待区（`try`@~2185，`except Exception:`@2312）无 `finally`，而 `except Exception` 不捕获 `CancelledError`（Py3.8+ 属 BaseException）。客户端在 `await asyncio.sleep`(2273)/`await qwen_task`(2293) 期间断连 → `_finalize_private_buffer` 不执行 → `buf["done"]` 永不 set。此后该 user_id 每条私聊都命中 2215 分支并在 2257 `done_event.wait()` 永久阻塞；`_private_buffers` 无 reaper。单次断连即永久卡死该用户私聊直到进程重启。
  **修**：owner 区域改 `try/finally` 保证 `_finalize`（set done）；follower `done_event.wait()` 加 `asyncio.wait_for` 超时；`_private_buffers` 增加基于 deadline 的过期清理。

- [x] **E2 BridgePool TTL 回收停掉「在用」bridge（use-after-stop）** · `nanobot_kt/bridge.py:1995-2016` · HIGH · M ·〔补漏新发现〕
  `_bridge_last_used[key]` 只在 `_get_bridge` 入口（2016）刷新，`handle_message`（1080 行，最多 8 次模型尝试 + retry，可达数百秒）执行期间不刷新。单请求耗时 > `BRIDGE_TTL_SECONDS`(600s) 时，另一 session 进入 `_get_bridge` 会把仍在 `process_event` 的 bridge 判为 stale → `pop` + `asyncio.create_task(b.stop())`，腰斩在途请求（取消工具任务、关 LLM 客户端）。
  **修**：回收前校验空闲（`session_lock.locked()` / 引用计数 / `is_busy`），或 `handle_message` 执行期周期回写 `last_used`；绝不 stop 正在服务的 bridge。

- [x] **E4 熔断器记账 fire-and-forget 任务可被 GC** · `clients/new_api_client.py:320,641,662,690,708,817,835,867,884` · HIGH · S ·〔补漏新发现〕
  所有 `record_success/record_failure` 用 `asyncio.create_task(coro)` 启动却不保存返回 Task（无 `_bg_tasks`、无 `add_done_callback`）。事件循环仅持弱引用，任务可在完成前被 GC → 熔断记账丢失，CLAUDE.md 记载的「连续 3 次失败禁用 5min」机制被破坏，坏模型持续被选中。
  **修**：维护类级 `set[asyncio.Task]` 强引用集合，`t = create_task(coro); _bg.add(t); t.add_done_callback(_bg.discard)`；320 行持久化同理并记录异常。

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
  **修**：改 `async def` + `await asyncio.to_thread(gate.judge, *args)`，并收紧 repeats 上限。

- [x] **H17b /db/query 回显内部异常** · `api/admin_routes.py:2128,2144` · HIGH · S
  `raise HTTPException(500, str(e))` 泄露 SQL 片段/列名/SQLite 路径。
  **修**：`logger.exception("内部错误")` + 响应统一「内部错误」。

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
  **修**：强引用集合 + `add_done_callback`，或 `await asyncio.gather(*stop_tasks, return_exceptions=True)`（已持 `_create_lock`）。

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

- [x] **H9 ai_daily 子线程丢 trace contextvars** · `creatures/<省略路径>/news_search/tool.py:500,1647` · MEDIUM · M
  `threading.Thread` 内 `asyncio.run(coro)` 不复制父 contextvars，`trace_id/run_id` 断链（功能正常，仅可观测性）。**修**：`contextvars.copy_context().run` 或 `run_coroutine_threadsafe`。

- [x] **H32 analyzer inspect.signature 兼容层** · `creatures/<省略路径>/group_analysis/analyzer.py:296-307` · MEDIUM · S
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

- **现状（2026-06-18 已落地）**：模型记录已归一到顶层 `supports_image` / `supports_tools` / `supports_stream` 字段，并兼容 `model_overrides.json` 的顶层字段和嵌套 `capabilities`；`get_ordered_candidates(required_capabilities=<需求>)` 已支持硬过滤，显式不满足能力的候选不会进入排序。直接 `NewAPIClient.chat_completion()` / `chat_completion_stream()` 已能从 messages、tools 和 stream 推导能力需求；Bridge 主回复路由也已从 `metadata["files"]`、ToolPlan schema 和 KT 固定 streaming 请求事实生成能力需求，手动回复模型不满足能力时回退自动路由。payload / SDK request 前 guard 已防止绕过候选过滤；无视觉候选时会降级为纯文本说明并重新路由，不再把 `image_url` 发给纯文本模型。`model_routing` eval 已覆盖带图请求必须选 vision 候选，防止后续改动破坏能力硬过滤。
- **剩余痛点**：路线项 3 的能力校验主链路已完成。base64 data URL 直入 payload 与 `docs/message-field-standard.md` 禁 base64 的长期方向仍需在后续出站 / 入站契约中继续收敛；图片数量 / 大小上限也应跟随多平台消息信封和出站渲染契约继续设计。
- **目标**：模型能力（`supports_image` / `supports_tools` / `supports_stream`、单图大小 / 数量上限）结构化写入模型配置；构造阶段检测 messages 含 `image_url` 时，强制只在 vision 候选中选模型，并按能力校验图片格式 / 大小，不满足则降级（剥图 + 文本兜底）或换模型，而非无脑塞。
- **关联**：呼应项 9（多模态行为描述需同步 canonical Prompt Runtime 模板）；与熔断器记账正确性（E4/E5）相关；主 reply 与 sticker_describe（走专用 vision provider）的能力口径需统一。
- **粗略路径**：① `model_overrides.json` / registry 增结构化 capabilities（已完成）→ ② 构造边界生成 `has_image` / tools / stream 信号（直接 New API 与 Bridge 已完成）→ ③ 路由在 `has_image` 时按 `supports_image` 过滤候选（已完成）→ ④ `_build_payload` / SDK request 前按能力校验 / 裁剪 image_url、stream、tools（已完成）→ ⑤ 扩展 `model_routing` eval 并统一两套 vision 机制的能力引用（已完成）。

---

### P2 — 多平台接入底座（platform 维度补全）

#### 路线项 4 — 工具配置增加 platform 维度（session 级已具备）

- **现状（2026-06-18 已落地）**：工具启用**已按多维度解析**，并非全局单一。`build_tool_plan`（`core/tool_plan.py:134`）→ `resolve_effective_tools`（`core/runtime_tool_service.py:116`）按合并顺序生效：`TOOL_METADATA` 默认(private/group) → `force_enabled`/`force_disabled_group` → `runtime_preset`(none/lightweight/full) → `ToolOverride` 表(scope_type ∈ chat_type/platform/group/user) → 硬约束兜底。后端解析已支持 `ToolOverride(scope_type="platform", scope_id="<platform>")`，顺序固定为 `chat_type < platform < group < user`；`build_tool_plan()` 和 `resolve_final_tools()` 已透传 `platform` 参数。每请求经 `record_runtime_tool_decision` 落库，`RuntimeToolDecision.platform` 字段和旧库补列迁移已落地，`/tools/decisions` 已返回 platform。真实入口也已透传：`/chat` 和群聊 `_continue_to_bridge()` 会把标准化后的 `client_meta.platform` 写入 Bridge metadata，`NanobotBridge.handle_message()` 会继续传给 ToolPlan 和运行时决策记录；对应提交为 `73bbe8a feat(消息): 透传客户端平台`。Admin API 已支持写入和预览 platform override，`/tools/effective?platform=web`、`/tools?platform=web` 和 `/tools/targets?scope_type=platform` 均具备平台口径；对应提交为 `d9a1bae feat(工具): 支持平台覆盖接口`。WebUI 工具页已支持 platform selector 和「指定平台」覆盖入口，配置侧闭环已具备；对应提交为 `2b0e203 feat(工具): 配置平台覆盖`。`docs/message-field-standard.md` 已同步说明工具策略消费 `client_meta.platform` 的规则。
- **剩余痛点**：路线项 4 的 platform 工具策略闭环已完成；后续依赖项 P2-2 / P2-3 / P2-4 也已完成。当前只保留后续多平台扩展时的运营性维护：新增平台时补对应 platform override、消息信封和 Prompt Runtime 平台模板，不再把本项作为阻塞任务。
- **目标**：为工具解析增加完整 platform 维度，形成「平台 × 会话类型 × 群/用户」的工具可用性矩阵，多平台接入时各平台可独立配置工具白名单；同时保证 `runtime_preset=none`、`force_enabled` 和群聊强制禁用等硬约束不能被 platform override 绕过。
- **关联**：与项 9（platform×chat_type 提示词）、项 3（模型能力矩阵）、项 5/7（platform 化消息与渲染）同属 platform 维度，建议成簇。
- **粗略路径**：① 入口按来源注入 platform（默认 qq 向后兼容，已完成）→ ② `ToolOverride` 增 platform scope 并纳入解析顺序（已完成）→ ③ `build_tool_plan()` / `resolve_final_tools()` 透传 platform（已完成）→ ④ runtime_tool_decision / 审计带 platform（已完成）→ ⑤ Admin API 支持 platform override 创建和预览（已完成）→ ⑥ WebUI 工具配置页增平台维度（已完成）→ ⑦ 同步 `docs/message-field-standard.md` 和阶段计划（已完成）。

#### 路线项 5 — messages 接口统一为标准化请求 / 响应信封（响应信封与 client_meta 校验已完成）

- **现状（2026-06-18 已落地）**：两个对外入口——私聊 / Web 业务入口 `ChatProxyRequest`（`api/routes.py:220`）与群聊 `GroupMessageRequest`（:1036），请求字段已基本对齐 `docs/message-field-standard.md`。P2-2 只读审计已完成，设计文档已随 `c984036 docs(消息): 设计响应信封标准` 提交；实现计划已写入 `.Codex/plans/message-envelope.md`，采用接口先行、API / 群聊 / push owner 分工和兼容双写方案。共享 builder 已随 `147421b feat(消息): 构建响应信封` 提交；`/chat` 非流式和 SSE done 已随 `57006f3 feat(消息): 返回私聊响应信封` 接入 `reply`、`messages`、过滤后的 `reply_meta` 与 `meta`，同时保留 `answer`、`answer_chunks`、`unprocessed_logs` 和 SSE done 的 `answer`。群聊 `/group/message` 的 continue / wait / no_reply 也已接入 `status`、`messages`、过滤后的 `reply_meta` 和 `meta`，同时保留 `action`、`reply`、`generation`、`reason` 等旧字段；定时任务 push 已新增 `push_envelope_to_qq()` 适配标准信封并保留 `push_to_qq` 旧签名；route push call site 已接入信封推送，并保留流式断连图片 token `allow_base64=False` 展开边界。`client_meta` 边界校验已新增 `core/client_meta.py` 并接入 `/chat` 与 `/group/message`：`platform` 缺省 `qq` 且按格式归一，`chat_type` 必须与入口一致，`trace.request_id` / `correlation_id` / `source` 必须是字符串并裁剪到 128 字符；`/chat` 会把合法 `trace.request_id` 投影到响应信封 `meta.request_id`，群聊 ambient log 会保存归一化后的 `client_meta`，同时保留 `stickers` 等扩展字段。
- **剩余痛点**：路线项 5 的标准响应信封和 `client_meta` 关键字段校验已收口。P2-3 已把 QQ 出站 renderer、生成图 token、贴纸 token 和 push 信封出口收敛到统一渲染边界；HTML-to-pic、`reply_meta` 派生 QQ 引用 / @ 和跨平台结构化 payload 继续作为相邻演进项处理。
- **目标**：两入口共享一套标准化请求 / 响应信封 `{status|action, messages|reply, reply_meta, meta}`，正文字段名统一，私聊也带过滤后的 reply_meta；`client_meta` 从文档约定升级为运行时轻量 schema，至少校验 `platform`、`chat_type` 和 `trace` 关键字段。
- **关联**：与项 7（reply_meta 是渲染约定的载体）强耦合，建议合并设计；`push_to_qq`（`core/daily_digest.py:498`）是第三条出口，统一时不可漏。
- **粗略路径**：① 完成四出口只读审计和响应信封设计（已完成）→ ② 写入 `.Codex/plans/message-envelope.md` 实现计划（已完成）→ ③ 抽统一响应信封模型（已完成共享 builder）→ ④ 让 `/chat` 非流式和流式 done 同形态并接入过滤后的 reply_meta（已完成）→ ⑤ 接入 `/group/message` 信封（已完成）→ ⑥ 接入 push 信封适配与 route push call site（已完成）→ ⑦ `client_meta` 增边界层解析 / 校验（已完成）→ ⑧ 给 `message-field-standard.md` 补响应侧和 `client_meta` 运行时标准（已完成）。

#### 路线项 7 — QQbot 端出站渲染契约（响应信封到 QQ legacy message）

- **现状（2026-06-18 已落地）**：P2-2 已完成响应信封兼容双写，私聊、SSE done、群聊和 push 路径都能承载 `reply`、`messages`、`reply_meta` 和 `meta`；P2-2.5 已完成 `client_meta` 边界校验。P2-3 已新增 `core/qq_outbound_renderer.py`，以响应信封 `messages` 作为 canonical 出站内容层、`reply` 作为兼容 fallback，统一把信封渲染为 QQbot 旧 `message` 字符串。`push_envelope_to_qq()` 已改用 renderer，`push_to_qq(target_type, target_id, message) -> bool` 旧签名保持不变；schedule task `action == "run"` 已改走响应信封和 `push_envelope_to_qq()`；route push 与富媒体信封边界已固化回归；`reply`、`sticker_search`、`image_generation` 的 usage 文档已同步短 token 与出口 renderer 职责；P2-3 定向回归和全量回归均已通过。
- **剩余演进项**：QQbot 未来可以接收结构化 payload 后，可在 `push_envelope_to_qq()` 之后新增平台 adapter 层，而不是把结构化字段塞回裸字符串。`reply_meta` 目前只保留发送意图，不派生 `[CQ:at]` 或 `[CQ:reply]`；若要启用，需要单独设计幂等和平台兼容规则。`ReplyTool` 中 sticker 提前展开仍作为兼容路径保留，后续可进一步下沉到 renderer，减少工具层 CQ 细节。
- **目标**：保持响应信封 `messages` 为 QQ 出站内容 canonical 数组，所有 QQ-facing push 先经 `core.qq_outbound_renderer.render_qq_outbound_envelope()`；首版不新增顶层 `segments` 或 `out_segments`，富媒体优先公开 URL，生成图无公开 URL 时保留短 token，且 `allow_base64=False` 路径不出现 `base64://`。
- **关联**：依赖项 5 的响应信封和 `client_meta` 校验；与项 9 的 platform × chat_type 提示词拆分共享 prompt 语义边界；`docs/message-field-standard.md` 已补「响应出站渲染契约」章节。
- **粗略路径**：① 完成「内容类型 → QQ 渲染」只读审计和设计文档（已完成）→ ② 新增 `core/qq_outbound_renderer.py` 与 renderer 单测（已完成）→ ③ `push_envelope_to_qq()` 改用 renderer，保持旧 `push_to_qq()` 签名（已完成）→ ④ schedule task run 分支改走响应信封和 `push_envelope_to_qq()`（已完成）→ ⑤ 固化 route push 和富媒体信封边界回归（已完成）→ ⑥ 同步 reply / sticker_search / image_generation 工具说明（已完成）→ ⑦ 同步 `docs/message-field-standard.md`、`docs/todo.md` 和 walkthrough，并运行定向与全量验证（已完成）。

#### 路线项 9 — 提示词模板按 platform × chat_type 二维适配  ·〔依赖项 1〕

- **现状（2026-06-18 已落地）**：P2-4 已完成设计、计划、核心编排、Bridge / Admin 透传、QQ 模板迁移和集成回归，相关提交为 `27e632f`、`164b215`、`ca93dc2`、`18d0b0d`、`17a7bd8`、`fe2d81b`。Prompt Runtime 已按 `platform × chat_type` 过滤 flow；`chat_type` 仍只表达会话语义（`group` / `private`），`platform` 表达客户端平台并默认兼容 `qq`。`platform` 已从 Bridge metadata 进入 `PromptRuntimeInput`、`PromptCompileRequest`、`PromptPlan`、`debug` 和 `<runtime_context>`；Admin effective-preview 也会把同一个平台值传给 ToolPlan 与 PromptCompileRequest。
- **已落地边界**：`flow.py` 的节点和边已支持 `platforms` 条件，`ordered_nodes_for_chat(flow, chat_type, platform="qq")` 按二维条件过滤，`validate_flow()` 会拒绝 `chat_types × platforms` 条件重叠的歧义出边。QQ 专属规则已迁入 `chat/platform/qq/common.md` 与 `chat/platform/qq/group.md`，默认 flow 通过 `qq_common_policy` 和 `qq_group_policy` 注入；`web × private` 不再注入 QQ 平台模板。`prompts.v2.default` 与 `data/prompts_v2` 的相关模板保持同步。
- **目标**：组装从一维（`chat_type`）升级为二维（`platform × chat_type`），平台无关规则（输出契约 / 安全 / 风格）留在公共模板，平台相关约定（QQ 的 msg_id / 表情 / @ 机制等）下沉到 platform 专属分支，bridge/schema 全链路透传 platform。
- **剩余演进项**：工具模板 selector 暂不按平台拆分；TimingGate task 模板的平台化仍由 TimingGate 路线独立推进。
- **关联**：**依赖项 1** 已先收敛为单一 Prompt Runtime 主路径；本项与项 4/5/7 同属 platform 维度簇。
- **粗略路径**：① schema / `PromptRuntimeInput` / bridge meta 增 platform 字段（默认 qq，已完成）→ ② `flow.py` 节点 / 边支持 `platforms` 条件与二维冲突检测（已完成）→ ③ 拆模板：QQ 专属约定抽到 `platform/qq/*.md`，公共模板去 QQ 私有措辞（已完成）→ ④ compiler `ordered_nodes_for_chat` 按 `platform × chat_type` 过滤（已完成）→ ⑤ 入口按来源注入 platform（已完成）→ ⑥ 补 `qq/web × group/private` 编译和 Admin 预览回归（已完成）。

---

### P3 — 决策与流式优化

#### 路线项 6 — SSE 真 token 流式重构（stream 参数全链路贯穿）  ·〔关联 H30〕

- **现状（2026-06-18 已落地）**：API 层已有 stream 开关（`ChatProxyRequest.stream`）与 SSE 出口 `_stream_chat`，`stream` 已贯穿 API → BridgePool → Bridge → KT `Message`。`BufferedOutput.write_stream()` 会向 SSE 队列发送 `delta` 事件，生产 reply 链路仍通过 KT OpenAI provider 的 streaming 迭代输出进入 `BufferedOutput`；`/chat-step` 也已接入 `run_agent_step_stream()`，通过 `NewAPIClient.chat_completion_stream()` 下发 final-answer delta，并在工具选择阶段拼合流式 tool call 后发送最终 `tool_call` 事件（`2369081 feat(agent): 支持 step 流式输出`）。P3-1 已完成 SSE 收敛设计、实现计划、核心实现、文档收口和最终验证；`/chat` API 层已对队列事件做规范化，连续 `delta.text` 会在当前可用队列窗口内合并，非 delta 事件前强制 flush。Bridge 成功路径已在最终 response 确定后发送 `final.replace` 收敛事件，`done.answer` / `done.reply` 仍是最终业务权威结果。`/chat` stream queue 已设置上限，文本 delta / final 采用自然 backpressure；progress 满队列可丢弃，error 仍保留排队；断连后台路径会 drain bounded queue，避免 runner 因无人消费 SSE 草稿事件而卡住。`docs/message-field-standard.md` 已记录 `/chat` SSE 事件、`/chat-step` 的 `delta.content` 差异、图片 token 不在增量事件展开以及 `done` 权威语义。最终验证结果为流式定向回归 `23 passed`、API / Bridge 回归 `145 passed`、全量测试 `1311 passed, 6 skipped`。
- **痛点**：多工具回合的展示收敛已有 `final.replace`，但前端仍必须以 `done` 信封更新最终业务状态；当前合并窗口以 API 消费时队列中已可用的连续 delta 为边界，不做时间窗口等待；SSE 单事件字节数暂未引入独立硬上限。
- **目标**：保持 `stream` 参数全链路贯穿，SSE 稳定下发增量 token；继续收敛 chunk 大小上限、前端展示规则和响应信封，使 Web SSE、QQbot 推送与最终持久化共享同一套输出契约（兼顾 QQbot 单 chunk 大小限制与 base64 禁用约定）。
- **关联**：H30（RAG `query()` 巨函数拆分，便于流式分段）；依赖项 2 连接池；与项 5 响应信封、项 7 渲染（增量 chunk 如何渲染）协同。
- **粗略路径**：① 已完成 API / Bridge / KT Message / BufferedOutput 的 stream 贯穿 → ② 已完成 `/chat-step` SSE 增量输出与流式 tool call 拼合 → ③ 已完成 `/chat` API 层连续 delta 合并、bounded queue 和 progress backpressure 策略 → ④ 已完成 Bridge `final.replace` 收敛事件与 `done` 权威语义文档 → ⑤ 已与路线项 5/7 的响应信封和出站渲染契约合流，增量事件不展开图片 token，最终 `done` / push 保持 `allow_base64=False` → ⑥ 可继续评估 SSE 单事件字节硬上限和更细的时间窗口合并。

#### 路线项 10 — TimingGate 引入「规则信号 + 模型」混合决策  ·〔关联 H2〕

- **现状（2026-06-20 已落地）**：核心链路已从「纯 Qwen 三态判断」推进到 scoring 混合决策。已新增 `core/timing_score.py`，覆盖 `d0/linger/s_ack/s_transport/s_other/s_bot/w_*` 信号、`E_rule/E_final`、冲突升级、模型权重和 `rule_fallback`；`GroupRuntime` 已接入 shadow scoring、普通 ambient 确定性短路、模型失败规则兜底、`directed_to_other` scoring 软化、ambient / legacy / timer cooldown scoring 短路，以及 session / platform 级模型层策略。私聊已接入同一套 shared timing scoring，分类器结果回灌为 `TimingModelHint`。群聊 `timing_scoring` 已写入 ChatLog meta 并由 admin events / WebUI 调试页透出；私聊 `PrivateDecision.timing_scoring` 已随 user ChatLog、assistant ChatLog 和 ConversationTurn meta 持久化为 `timing_gate`，可回溯 action、reason、effort、runtime_preset 与 scoring 明细。`evals` 也能在 action 缺失时执行 scoring 并校验 `expected.scoring`。群聊 `s_bot` live path 收口已完成设计与计划归档（`6463ee8`、`1795d04`），任务 1 已随 `2fcfad7` 落地：`current_bot` 仍保持 hard stop，其他 bot 通过 `is_other_bot` 进入 scoring 软抑制。
- **已完成**：`@bot + 图片` 规则 WAIT 不调模型；纯 ambient / 纯确认可规则 `no_reply`；`directed_to_other + linger` 进入冲突升级；正常模型返回路径已采用 scoring blend 的最终 `action/delay/reason`，不再只把 scoring 当 shadow 字段；TimingGate JSON / 旧格式 / 非法 / 网络错误解析已回灌 `parse_quality` 与 `model_confidence`，旧格式按 `0.5` 低置信参与模型融合；模型 `network_error/parse_error` 后使用规则侧 `rule_fallback`，不再全群哑火；`wait.delay_seconds` 上限已与设计收敛到 15 秒；`s_ack` 排除请求词、问号、URL、代码、文件；`s_transport` 已按 secret/blob/url/codeblock/long dump 分档；`force_next_continue` 已降级为 `d0=1.0` 后完整走 Stage 1-4；`enabled` / `rules_only` / `shadow` 模型策略已支持 default / platform / session 三级覆盖；真实 ChatLog 信号审计 CLI 已输出假阳率、shadow mismatch 和阈值建议；`timing_gate` eval 已支持 baseline diff 和阈值门禁；私聊评分已补齐 ChatLog meta 可观测闭环；`explicit_bot` / `client_meta` 已进入 `GroupRuntime`，`timing_message` 和 `GroupPendingMessage` 已透传 `is_other_bot`，`_score_timing()` 会按 pending 窗口内 `is_other_bot=any(m.is_other_bot for m in msgs)` 调用 `decide_timing()`，并在 ChatLog meta 中记录 `s_bot=0.70`。
- **s_bot 验证状态（2026-06-20）**：三项定向测试结果为 `3 passed, 21 warnings in 2.16s`；相邻回归 `tests/test_api.py tests/test_timing_runtime.py tests/test_timing_score.py` 结果为 `157 passed, 21 warnings in 23.30s`。
- **剩余**：核心混合决策主线、私聊可观测闭环、P3-3A 标注审计复跑入口和 P3-3B 仓库自包含 CI / PR gate 均已完成。更多真实样本的选择、标注仲裁、定期复跑和是否按报告调参仍是运营动作；通用 `candidates → labeled` 产品化闭环留到路线项 8 / P4。
- **关联**：H2 已完成 admin route 异步化和 repeats 收紧；后续与路线项 8（评测体系）、路线项 5/7（响应信封与调试可观测）继续协同。
- **下一步**：路线项 8 / P4 已完成通用 `candidates → labeled` 标注闭环、Admin 契约化工作台、P4-3 per-capability 数据集扩展、P4-4 RAG baseline gate、P4-5A 统一 PR gate、P4-5B 周期性复跑与报告归档、P4-5C RAG manual 样本扩充、P4-5D memory fixture-backed positive RAG case，以及 P4-5E knowledge fixture citation 正例。下一阶段继续更多 fixture source 覆盖或真实样本运营动作。

---

### P4 — 评测体系

#### 路线项 8 — 评测体系从既有 `evals/` 框架升级为基线 + 回归门禁（大工程）

- **P4-4 验证状态（2026-06-18）**：RAG manual deterministic gate 输出 `cases=3 passed=3 failed=0` 和 `Gate passed`；RAG 三件套回归为 `29 passed, 21 warnings`；WebUI build 退出码为 0；全量回归为 `1359 passed, 6 skipped, 139 warnings in 99.40s`。
- **P4-5A 验证状态（2026-06-18）**：统一 gate 脚本输出 `timing_gate`、`capability_model_routing`、`capability_reply_contract`、`capability_rendering_contract` 和 RAG manual deterministic gate 全部 `Gate passed`；评测守卫组合为 `35 passed, 1 warning in 2.34s`；全量回归为 `1361 passed, 6 skipped, 139 warnings in 100.83s`。
- **P4-5B 验证状态（2026-06-18）**：定向评测组合 `tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py tests/test_rag_benchmark.py` 结果 `40 passed, 1 warning in 2.42s`；周期性脚本 `bash scripts/run_eval_periodic.sh` 输出评测守卫 `27 passed, 1 warning in 1.78s`，`timing_gate`、三个 capability gate 和 RAG manual deterministic gate 全部 `Gate passed`；PR gate `bash scripts/run_eval_pr_gate.sh` 输出评测守卫 `27 passed, 1 warning in 1.76s` 且全部子 gate `Gate passed`；全量回归为 `1366 passed, 6 skipped, 139 warnings in 101.52s`。
- **P4-5C 验证状态（2026-06-18）**：RAG manual case 数从 3 增加到 9；baseline 合同测试已收紧；RAG manual deterministic gate 输出 `cases=9 passed=9 failed=0` 和 `Gate passed`；评测守卫相邻回归 `tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `32 passed, 1 warning in 1.90s`；PR gate 和周期性 gate 均通过；全量回归为 `1367 passed, 6 skipped, 139 warnings in 99.86s`。
- **P4-5D 验证状态（2026-06-20）**：RAG stable gate 已从 `manual` 切到 `manual+fixture`；新增 `positive_v1` memory fixture DB builder 和 `memory_fixture_positive_001` 正例；baseline 的 `positive_cases` 从 0 提升到 1，`hit@5=1.0`、`mrr=1.0`；RAG fixture gate 输出 `cases=10 passed=10 failed=0` 和 `Gate passed`；相邻回归 `tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `35 passed, 1 warning in 2.51s`；PR gate 结果为评测守卫 `27 passed, 1 warning in 2.26s` 且所有子 gate 通过；周期性 gate 结果为评测守卫 `27 passed, 1 warning in 2.22s` 且所有子 gate 通过。
- **P4-5E 验证状态（2026-06-20）**：RAG `positive_v1` fixture 已从 memory 单正例扩展为 memory + knowledge 双正例；新增 `knowledge_fixture_positive_001`，固定命中 `knowledge:9001:chunk:0`，并通过 `requires_citation=true` 的 citation check；RAG stable gate 输出 `cases=11 passed=11 failed=0` 和 `Gate passed`；RAG 相邻回归 `tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `37 passed, 1 warning in 2.78s`；citation 相邻回归结果 `3 passed, 21 warnings in 1.84s`；全量回归为 `1374 passed, 6 skipped, 139 warnings in 105.13s`。
- **现状（2026-06-20 已完成 P4-5E）**：评测框架**已存在**而非空白：`evals/run.py`（CLI `python -m evals.run --suite <name>`）+ `schema.py`（`EvalCase`/`EvalOutput`/`EvalResult`/`SuiteReport` pydantic）+ `scorers.py` + `runners/`（sticker / memory / moderation / model_routing / rendering_contract 等 per-suite runner）+ `cases/`（regression 10 例、rag_benchmark/manual、timing_gate 多例、candidates、capability_model_routing、capability_reply_contract、capability_rendering_contract）+ `sample_from_db.py`/`sample_from_logs.py`（从库 / 日志采样造例）。`evals/baseline.py` 已提供 baseline diff 与阈值门禁，`run.py` 已支持 `--baseline`、`--min-pass-rate`、`--max-new-failures`，`SuiteReport` 可携带 `baseline_diff` 与 `gate`；TimingGate CI / PR gate、首个 `capability_model_routing` 能力数据集 gate、`capability_reply_contract` gate 和 `capability_rendering_contract` gate 均已落地。P4-2「Admin 标注工作台契约化与 promote 预检 UI」已完成：后端 expected contract schema/API、WebUI 标注表单契约化、`note` / `expected` 分离、promote dry-run → apply 预检 UI 均已落地，并通过 WebUI 静态测试 `18 passed`、候选闭环回归 `24 passed`、WebUI build 退出码 0 和全量回归 `1350 passed, 6 skipped`。P4-3 已新增 `capability_reply_contract` 与 `capability_rendering_contract` 数据集、baseline、离线 gate、渲染相邻回归和全量回归。P4-4 已为 `evals.rag_benchmark` 增加专用 baseline 纯函数、CLI gate、稳定 `evals/baselines/rag_benchmark.json`、Admin gate API、WebUI gate 展示和报告落盘字段。P4-5A 已新增 `scripts/run_eval_pr_gate.sh`，统一串联 TimingGate、capability 和 RAG gate，并让 `.github/workflows/timing-gate-eval.yml` 调用统一入口。P4-5B 已新增 `scripts/run_eval_periodic.sh` keep-going 周期性入口；`.github/workflows/timing-gate-eval.yml` 已具备 `workflow_dispatch`、每周 schedule 和 artifact 归档，artifact 包含 `evals/reports/*.json`、`tmp/rag_benchmark/reports/*.json` 和 `tmp/rag_benchmark/reports/*.md`。P4-5C 已把 RAG manual deterministic gate 的稳定 `constraint_only` 样本扩到 9 个，并补 baseline 与 manual case 集合一致性守卫；P4-5D 已新增固定 memory positive fixture，并把稳定 gate 的 `case_scope` 切到 `manual+fixture`；P4-5E 已新增固定 knowledge positive fixture 和 citation 正例断言。
- **痛点**：baseline diff 和门禁能力已具备，TimingGate CI / PR gate 已完成首个仓库自包含接入；`capability_model_routing`、`capability_reply_contract`、`capability_rendering_contract` 与 RAG benchmark gate 已完成第一批能力 / RAG 基线，P4-5A 已把这些稳定 gate 收敛为统一 PR gate，P4-5B 已补齐周期性复跑、手动触发和报告归档，P4-5C 已完成第一轮 RAG manual 样本扩充，P4-5D 已补上首个 fixture-backed positive RAG case，P4-5E 已补上 knowledge fixture citation 正例。提示词质量 / RAG 召回 / TimingGate 仍需要更多真实样本选择、人工标注仲裁和按周期报告调参。P4-1 已先修复会污染评测数据的契约缺口：`expected_json` / `expected` 字段错配已兼容，空 expected、`needs_label=true` 和不可评分字段会被拒绝，promote 已支持 dry-run 与 `target_dataset`，离线 candidates export / import-labels / promote CLI 已可用。P4-2 已补齐后端 expected 类型 / 枚举契约、WebUI 旧字段提交和直接 promote 的产品化缺口；当前剩余痛点转为更多 fixture source 覆盖和真实样本运营动作。
- **目标**：把既有 `evals/` 升级为体系——统一指标与基线快照、回归对比门禁（PR 跑核心 suite 并比对 pass_rate / score 漂移）、分能力数据集（提示词 / 路由 / RAG / TimingGate / 渲染）、人工标注回流与 `candidates → labeled` 闭环。
- **关联**：依赖项 1 / 6 / 10 等行为先稳定（否则基线频繁失效）；与项 10 共享 timing_gate 套件、项 3 共享 model_routing 套件。
- **粗略路径**：① 固化基线快照与指标口径（已完成 timing_gate 核心 suite）→ ② `run.py` 增 baseline diff + 阈值门禁（已完成）→ ③ 接入 TimingGate 外部 CI / PR gate（已完成 P3-3B）→ ④ P4-1 expected 契约、候选标注、promote dry-run、离线 CLI、dataset / suite 边界和首个 `capability_model_routing` 数据集（核心闭环已完成，计划记录在 `.Codex/plans/eval-dataset-labeling.md`）→ ⑤ P4-2A 后端 expected contract schema/API（已完成）→ ⑥ P4-2B Admin WebUI 标注表单契约化与 promote 预检 UI（已完成）→ ⑦ P4-3 扩 `capability_reply_contract` / `capability_rendering_contract` 等更多 per-capability 数据集（已完成）→ ⑧ P4-4 RAG benchmark baseline gate、Admin / WebUI 展示和稳定 baseline（已完成）→ ⑨ P4-5A 统一 PR gate（已完成）→ ⑩ P4-5B 周期性复跑与报告归档（已完成）→ ⑪ P4-5C RAG manual 样本扩充（已完成）→ ⑫ P4-5D fixture-backed positive RAG case（已完成）→ ⑬ P4-5E knowledge fixture citation 正例（已完成）→ ⑭ 更多 fixture source 覆盖或真实样本运营动作。

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
