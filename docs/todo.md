# Nanobot Server — 待办计划

> 本文件分两部分：**一、缺陷修复清单**（来自 2026-06-16 全仓库 Python 代码审查 + 逐条对抗式核验）；**二、架构演进路线**（2026-06-16 读真实代码核实重写，后续状态已更新至 2026-06-25，每项含现状/痛点/目标/关联/粗略路径）。
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

- [x] **H29 handle_message 1080 行 + 深嵌套** · `nanobot_kt/bridge.py:860-1940` · HIGH(可维护) · L ·〔呼应路线图 §1〕
  已完成第一轮拆分：低风险 request helper、模型重试循环、reply contract 出口治理和 trace cleanup 已拆成私有边界；public signature、metadata、stream 侧通道和 `pop_last_reply_meta()` 语义保持不变。阶段提交为 `e65575c`、`1da43fb`、`786e707`、`1612158`。

- [x] **H30 RAG query() ~337 行** · `core/knowledge_rag.py:122-459` / `core/memory_rag.py:126-349` · HIGH(可维护) · L ·〔呼应路线图 §6〕
  已完成第一轮拆分：knowledge / memory 两个 `query()` 已按 recall、filter、rerank、gate、result 模块内私有边界拆分；public signature、result envelope、`stats`、`debug_trace`、degraded 语义和 RAG benchmark / Admin debug 消费契约保持不变。阶段提交为 `c319b4f`、`ba512f6`、`5391274`；跨模块公共 recall helper 暂不抽取，保留为后续稳定后评估项。

- [x] **超大文件 >800 行拆分** · MEDIUM · L
  当前 P3 队列已完成：`api/routes.py` 已降至 783 行。按职责拆模块；`api/admin_routes.py` 已从 1009 行继续拆至 632 行，`news_search/tool.py` 已从原 1835 行拆至 798 行，`group_runtime/runtime.py` 已从原 1385 行拆至 722 行，`core/persona_preprocess.py` 已从原 857 行拆至 773 行，五者不再属于当前 >800 行清单。全仓仍有其他历史大文件未纳入本轮 P3 队列，后续如需治理应另立计划。
  - 进展：`core/context_builder.py` 第一刀已拆出 deprecated group context 到 `core/context_legacy.py`；当时整项仍未完成，后续转入 `api/admin_routes.py` 与 `api/routes.py` 拆分。
  - 进展：`api/admin_routes.py` 第一刀已拆出只读 DB Browser 到
    `api/admin/db_browser_routes.py`；`/db/backup`、`/db/vacuum` 及其他
    admin 子域仍留在旧文件。
  - 进展：`api/admin_routes.py` 第二刀已拆出 Sticker / Generated Images 管理到
    `api/admin/sticker_routes.py`；旧 `api.admin_routes` 继续 re-export 迁移后的
    request model、endpoint 和 `_sticker_dict()`，HTTP 路径、鉴权 monkeypatch 与
    `group_detail()` 展示语义保持不变。
  - 进展：`api/admin_routes.py` 第三刀已拆出 Group Memory 管理端路由到
    `api/admin/group_memory_routes.py`；旧 `api.admin_routes` 继续 re-export
    迁移后的 request model、helper 和 endpoint，保留 admin token monkeypatch、
    HTTP 路径和 `/groups/{group_id:path}` catch-all 路由顺序兼容。
  - 进展：`api/admin_routes.py` 第四刀已拆出 Observability 路由到
    `api/admin/trace_routes.py` 与 `api/admin/log_routes.py`；旧
    `api.admin_routes` 继续 re-export 迁移后的 endpoint、request model 和 helper，
    保留 admin token monkeypatch、HTTP 路径、日志读取、audit log 过滤和
    `/logs/{name}` 动态路由顺序兼容。
  - 进展：`api/admin_routes.py` 第五刀已拆出 Admin Tools 路由到
    `api/admin/tool_routes.py`；旧 `api.admin_routes` 继续 re-export 迁移后的
    request model、helper 和 endpoint，保留 admin token monkeypatch、HTTP 路径、
    audit action/detail、runtime preset、生效预览、schema override 和工具覆盖语义。
  - 进展：`api/admin_routes.py` 第六刀已拆出 Admin Models 路由到
    `api/admin/model_routes.py`；旧 `api.admin_routes` 继续 re-export 迁移后的
    request model、常量、helper 和 19 个 endpoint，保留 admin token monkeypatch、
    HTTP 路径、审计语义、provider/catalog/route test、本地组件测试、TimingGate
    稳定性测试和模型健康检查行为；`/model-replies` 仍留在父模块作为回复日志观测边界。
  - 进展：`api/admin_routes.py` 第七刀已拆出 Reply 手动测试 / Reply Eval 管理端
    路由到 `api/admin/reply_routes.py`；旧 `api.admin_routes` 继续 re-export
    迁移后的 request model、helper 和 11 个 endpoint，保留 HTTP 路径、
    admin token monkeypatch、Prompt Runtime metadata、评测 metrics、traffic 聚合
    和 `/reply-eval/runs` 静态路由顺序。`/model-replies`、`/evals/*`、
    `/settings/*`、`/db/*` 均未迁移；`api/admin_routes.py` 从 2647 行降至
    1935 行，`api/admin/reply_routes.py` 为 754 行，拆分测试为 151 行。验证结果：
    split 绿灯 `7 passed`，Reply 行为回归 `15 passed`，拆分兼容回归 `48 passed`，
    鉴权与 asyncio 策略回归 `10 passed`，全量回归
    `1542 passed, 6 skipped, 139 warnings in 112.96s`。下一刀候选为 Eval
    Workbench、Runtime / Overview、Settings，或先为普通 API 拆分设计
    `verify_token` common auth。
  - 进展：`api/admin_routes.py` 第八刀已拆出 Eval Workbench 管理端路由到
    `api/admin/eval_routes.py`；旧 `api.admin_routes` 继续 re-export
    迁移后的 request model、常量、helper 和 21 个 endpoint，保留 HTTP 路径、
    admin token monkeypatch、TimingGate proposal report 旧父模块 monkeypatch、
    candidate 静态路由顺序和 `/evals/runs` 静态路由顺序。`/model-replies`、
    Runtime / Overview、`/settings/*`、Configs、Prompt effective preview、Block /
    ContentBlock、DB backup / vacuum 均未迁移；`api/admin_routes.py` 从 1935 行
    降至 1390 行，`api/admin/eval_routes.py` 为 614 行，拆分测试为 194 行。
    验证结果：红灯 `4 failed, 5 passed`，split 绿灯 `9 passed`，Eval / Timing
    proposal 行为回归 `40 passed`，WebUI / asyncio 策略回归 `26 passed`，
    全量回归 `1551 passed, 6 skipped, 139 warnings in 112.38s`。下一刀候选为
    Runtime / Overview、Settings，或先为普通 API 拆分设计 `verify_token`
    common auth。
  - 进展：`api/admin_routes.py` 第九刀已拆出 Runtime / Overview 管理端路由到
    `api/admin/runtime_routes.py`；旧 `api.admin_routes` 继续 re-export
    迁移后的 request model、Runtime 专属 helper 和 5 个 endpoint，保留 HTTP 路径、
    admin token monkeypatch、Group Memory 子路由先于 `/groups/{group_id:path}`
    catch-all 的顺序、overview / groups / TimingGate events response shape 和
    `timing_gate_test()` 协程边界。`api/admin_routes.py` 从 1390 行降至
    1009 行，`api/admin/runtime_routes.py` 为 462 行，拆分测试为 143 行。
    验证结果：红灯 `5 failed, 4 passed`，split 绿灯 `9 passed`，管理端行为 /
    路由顺序 / asyncio 策略回归 `86 passed`，全量回归
    `1560 passed, 6 skipped, 139 warnings in 111.58s`。下一刀候选为 Settings，
    或先为普通 API 拆分设计 `verify_token` common auth；P3 队列仍剩
    `api/admin_routes.py` 1009 行、`api/routes.py` 2822 行。
  - 进展：`api/admin_routes.py` 第十刀已拆出 Chat Config 管理端路由到
    `api/admin/chat_config_routes.py`；旧 `api.admin_routes` 继续 re-export
    迁移后的 request model、helper 和 15 个 endpoint，保留 HTTP 路径、
    admin token monkeypatch、Block / ContentBlock / Config response shape、
    audit action/detail、`/configs` 静态路由顺序和 `/block-rules/test` 静态路由顺序。
    `api/admin_routes.py` 从 1009 行降至 632 行，已低于 800 行；新模块
    `api/admin/chat_config_routes.py` 为 396 行，拆分测试为 160 行。验证结果：
    红灯 `4 failed, 3 passed`，split 绿灯 `7 passed`，行为与相邻回归 `30 passed`，
    静态检查通过，全量回归 `1567 passed, 6 skipped, 139 warnings in 115.24s`。
    P3 超大文件队列当前只剩 `api/routes.py` 2822 行。
  - 进展：`creatures/nanobot/prompts/skills/news_search/tool.py` 第一刀已拆出
    旧版新闻报告 helper 到 `news_search/legacy_report.py`；`tool.py` 从 1835 行降至
    1149 行，搜索后端、AI 日报工具、缓存和 `_summarize_news_layout()` 当时仍留在旧文件。
  - 进展：`creatures/nanobot/prompts/skills/news_search/tool.py` 第二刀已拆出
    运行时缓存到 `news_search/runtime_cache.py`；`tool.py` 从 1149 行降至 1110 行，
    旧缓存符号保留为同一 dict / lock 和薄 wrapper，AI 日报缓存命中行为不变。
  - 进展：`creatures/nanobot/prompts/skills/news_search/tool.py` 第三刀已拆出
    搜索后端到 `news_search/search_backend.py`；`tool.py` 从 1110 行降至 798 行，
    旧 `WebTools`、RSS/Juya/DDG/trafilatura monkeypatch 入口保留为 `tool.py`
    facade，搜索后端定向回归和全量回归均已通过。
  - 进展：`core/group_runtime/runtime.py` 第一刀已拆出群运行时常量、状态模型和
    scoring 私有方法到 `core/group_runtime/constants.py`、`state.py` 与
    `scoring.py`；主状态机仍留在 `runtime.py`，旧 `core.group_runtime.runtime` 与
    `core.timing_runtime` 导入路径保留，拆分兼容、定向回归、相邻回归和全量回归均已通过。
  - 进展：`api/routes.py` 第一刀已收敛群消息 helper 重复实现到
    `app/group_ingress/helpers.py`；旧 underscore helper 名称保留为兼容别名，
    `/group/message` 与 `/chat` 主流程不变；文件从 3434 行降至 2822 行。
  - 进展：`api/routes.py` 第二刀已先抽出普通 API `verify_token` 共享兼容层到
    `api/common_auth.py`，再拆出 `/tasks*` 定时任务路由到 `api/task_routes.py`；
    `api.routes.verify_token` 与 `api.common_auth.verify_token` 保持同一函数对象，
    旧 `api.routes.NANOBOT_API_TOKEN` monkeypatch、`app.dependency_overrides[routes.verify_token]`、
    `/tasks*` HTTP 契约、push envelope 行为和 `run_scheduled_task_now()` 协程边界保持不变。
    `api/routes.py` 从 2822 行降至 2712 行，`api/task_routes.py` 为 169 行，
    拆分测试为 180 行。验证结果：红灯 `6 failed, 4 passed`，鉴权绿灯
    `5 passed`，split 绿灯 `10 passed`，行为与相邻回归 `9 passed`，静态检查通过，
    全量回归 `1577 passed, 6 skipped, 139 warnings in 112.05s`。下一刀候选为
    evolution、memory 或 models 路由。
  - 进展：`api/routes.py` 第三刀已拆出 memory HTTP 层到 `api/memory_routes.py`；
    旧 `api.routes` 继续 re-export `MemoryDigestRunRequest`、memory endpoint 和
    legacy helper，`_safe_meta` 留在父模块以服务聊天落库路径；`/memory/digests`、
    `/memory/digests/run`、`/memory/recall` HTTP 契约、日期过滤、AI daily tool log
    召回、旧 token monkeypatch 和 dependency override 均保持兼容。`api/routes.py`
    从 2712 行降至 2523 行，`api/memory_routes.py` 为 216 行，拆分测试为 139 行。
    验证结果：红灯 `3 failed, 4 passed`，split 绿灯 `7 passed`，memory 行为回归
    `32 passed`，相邻回归 `13 passed`，静态检查通过，全量回归
    `1584 passed, 6 skipped, 139 warnings in 114.10s`。下一刀候选为 `models`
    路由或 evolution route-only；继续避开 `/chat` 与 `/group/message` 主链路。
  - 进展：`api/routes.py` 第四刀已拆出 models HTTP 层到 `api/model_routes.py`；
    旧 `api.routes` 继续 re-export `ModelSyncRequest`、`list_models()` 和
    `sync_models()`，保留 `/models/list`、`/models/sync` HTTP 契约、provider / tier
    过滤、缺少 `NEW_API_KEY` 的 400 响应、`force` 透传、旧 token monkeypatch 和
    `sync_models()` 协程边界。`api/routes.py` 从 2523 行降至 2484 行，
    `api/model_routes.py` 为 57 行，拆分测试为 189 行。验证结果：红灯
    `6 failed, 11 passed`，split 绿灯 `17 passed`，相邻回归 `13 passed`，
    静态检查通过，全量回归
    `1594 passed, 6 skipped, 139 warnings in 114.82s`。下一刀候选为 evolution
    route-only，或继续寻找 stickers/media、history/context/log、agent-step/search/render
    等更大但低耦合边界；继续避开 `/chat` 与 `/group/message` 主链路。
  - 进展：`api/routes.py` 第五刀已拆出 evolution route-only HTTP 层到
    `api/evolution_routes.py`；旧 `api.routes` 继续 re-export
    `EvolutionTriggerRequest` 和 `trigger_evolution()`，保留手动
    `/evolution/trigger` 的 HTTP 契约、旧 token monkeypatch、同步
    `BackgroundTasks.add_task()` 排队边界和 `/health` 父模块边界。
    `evolution_task`、`EVOLUTION_THRESHOLD`、`init_legacy_memory()`、`memory`、
    `_persist_chat_turn()` 以及 `/chat` / `/log` 自动触发仍留在父模块。
    `api/routes.py` 从 2484 行降至 2469 行，`api/evolution_routes.py` 为 33 行，
    拆分测试为 140 行。验证结果：红灯 `5 failed, 19 passed`，split 绿灯
    `24 passed`，相邻回归 `14 passed`，静态检查通过，全量回归
    `1601 passed, 6 skipped, 139 warnings in 115.11s`。下一刀候选为
    stickers/media、history/context/log、agent-step/search/render 等更大但低耦合
    边界；继续避开 `/chat` 与 `/group/message` 主链路，除非先完成更细设计和红灯契约。
  - 进展：`api/routes.py` 第六刀已拆出 history / log HTTP 层到
    `api/history_log_routes.py`；旧 `api.routes` 继续 re-export `LogRequest`、
    `AmbientLogRequest`、`mark_clear()`、`get_history_summary()`、`compact_history()`、
    `get_context()`、`submit_log()`、`submit_ambient_log()` 和
    `search_history_logs()`。本阶段保留 `/chat/mark-clear`、`/chat/history-summary`、
    `/chat/compact-history`、`/context`、`/log`、`/log_ambient` 和 `/search_logs`
    的 HTTP 契约、旧 token monkeypatch、`/log` 同步 `BackgroundTasks.add_task()`
    evolution 排队边界、SQLite locked retry、`/search_logs` limit / context_size /
    LIKE 转义和同 session 上下文展开语义；`_persist_chat_turn()`、`_safe_meta()`、
    `init_legacy_memory()`、`memory`、`evolution_task`、`EVOLUTION_THRESHOLD`、`/chat`、
    `/group/message` 和 `/health` 仍留在父模块。`api/routes.py` 从 2469 行降至
    2134 行，`api/history_log_routes.py` 为 367 行，拆分测试为 208 行。
    验证结果：红灯 `5 failed, 4 passed`，split 绿灯 `9 passed`，相邻 split /
    SQLite retry 回归 `51 passed`，主 API 行为回归 `81 passed`，asyncio 策略
    回归 `3 passed`，静态检查通过，全量回归
    `1610 passed, 6 skipped, 139 warnings in 119.08s`。下一刀候选为 media /
    stickers 路由或 agent-step / render route-only 边界；继续避开 `/chat` 与
    `/group/message` 主链路。
  - 进展：`api/routes.py` 第七刀已拆出 sticker / media HTTP 层到
    `api/sticker_media_routes.py`；旧 `api.routes` 继续 re-export
    `StickerRegisterRequest`、`register_sticker_endpoint()`、
    `search_sticker_endpoint()`、`public_sticker_image()`、
    `public_generated_image()` 和 `disable_sticker_endpoint()`。本阶段保留
    `/stickers/register`、`/stickers/search`、`/stickers/{sticker_id}/image`、
    `/generated-images/{image_id}/image` 和 `/stickers/{sticker_id}/disable`
    的 HTTP 契约、旧 token monkeypatch、公开图片环境 token 边界、route 顺序、
    duplicate canonical 跳转、active 状态判断、cache fallback 和生成图片 404
    语义；`/chat`、`/group/message`、聊天图片 helper、群聊 sticker facade、
    `init_legacy_memory()`、`memory`、`evolution_task` 和 `/health` 仍留在父模块。
    `api/routes.py` 从 2134 行降至 1975 行，`api/sticker_media_routes.py` 为
    185 行，拆分测试为 182 行。验证结果：红灯 `3 failed, 7 passed`，split
    绿灯 `10 passed`，sticker / generated image / push renderer 行为回归
    `67 passed`，普通 API split 相邻回归 `56 passed`，静态检查通过，全量回归
    `1620 passed, 6 skipped, 139 warnings in 116.71s`。下一刀候选为
    `chat-step` / `render` 小刀，或继续审计更低风险 route-only 边界；继续避开
    `/chat` 与 `/group/message` 主链路。
  - 进展：`api/routes.py` 第八刀已拆出 Agent Step / Render route-only HTTP 层到
    `api/agent_step_routes.py`；旧 `api.routes` 继续 re-export `AgentStepRequest`、
    `agent_step_event_payload()`、`run_agent_step()`、`run_agent_step_stream()`、
    `agent_step_sse_data()`、`render_markdown()` 和 `chat_step()`。本阶段保留
    `/render` 与 `/chat-step` 的 HTTP 契约、旧 token monkeypatch、`/render`
    无鉴权 deprecated 响应、`Accept: text/event-stream` 与 body `stream=true`
    两种 SSE 触发、SSE 首事件和 `/render` -> `/chat-step` -> `/chat` 路由顺序；
    `/chat`、`/group/message`、group timing、`update_group_name()`、聊天落库、
    Prompt Runtime 和 message envelope 仍留在父模块或原边界。`api/routes.py`
    从 1975 行降至 1954 行，`api/agent_step_routes.py` 为 42 行，拆分测试为
    218 行。验证结果：红灯 `4 failed, 7 passed`，split 绿灯 `11 passed`，
    Agent Step 行为回归 `6 passed`，普通 API split 相邻回归 `67 passed`，
    `/chat` 流式相邻回归 `10 passed`，静态检查通过，全量回归
    `1631 passed, 6 skipped, 139 warnings in 121.40s`，文档收口定向回归
    `20 passed`。下一刀候选为 group utility / legacy timing route，或继续审计
    更低风险 route-only 边界；继续避开 `/chat` 与 `/group/message` 主链路。
  - 进展：`api/routes.py` 第九刀已拆出 group utility / legacy timing HTTP 层到
    `api/group_utility_routes.py`；旧 `api.routes` 继续 re-export
    `UpdateGroupNameRequest`、`GroupTimingRequest`、`GroupTimingTimerRequest`、
    `_build_group_timing_context()`、`update_group_name()`、
    `group_timing_deprecated()` 和 `group_timing_timer()`。本阶段迁移
    `/update_group_name`、`/group_timing` 与 `/group_timing/timer`，保留普通 API
    token monkeypatch、`api.routes.get_bridge` monkeypatch、group user id
    normalization、timer recent context、bridge 前事务释放、HTML 回复不截断、
    重复回复抑制、群回复持久化和
    `/group/message` -> `/update_group_name` -> `/group_timing` ->
    `/group_timing/timer` -> `/render` -> `/chat-step` -> `/chat` 路由顺序；
    `/chat`、`/group/message`、`/health`、聊天落库、Prompt Runtime、
    message envelope 和私聊 multimodal helper 仍留在父模块。`api/routes.py`
    从 1954 行降至 1754 行，`api/group_utility_routes.py` 为 283 行，
    拆分测试为 211 行。验证结果：红灯 `7 failed, 13 passed`，split 绿灯
    `20 passed`，timing 行为回归 `5 passed`，普通 API split 相邻回归
    `76 passed`，静态检查通过，全量回归
    `1640 passed, 6 skipped, 139 warnings in 120.42s`。下一刀需要重新审计
    `/chat`、`/group/message` 与 `/health` 的收益 / 风险比；继续避开主链路，
    除非先完成更细的设计文档和红灯契约。
  - 进展：`api/routes.py` 第十刀已拆出 group message HTTP 层到
    `api/group_message_routes.py`；旧 `api.routes` 继续 re-export
    `OneBotMessageSegmentPayload`、`GroupMessageRequest` 和 `group_message()`。
    本阶段迁移 `/group/message`，保留普通 API token monkeypatch、
    `api.routes.get_bridge` monkeypatch、`client_meta` 群聊边界校验和
    `/group/message` -> `/update_group_name` -> `/group_timing` ->
    `/group_timing/timer` -> `/render` -> `/chat-step` -> `/chat` 路由顺序；
    `/chat`、`/health`、聊天落库、Prompt Runtime、message envelope、私聊
    multimodal helper 和 group ingress helper facade 仍留在父模块。
    `api/routes.py` 从 1754 行降至 1709 行，`api/group_message_routes.py`
    为 88 行，拆分测试为 262 行。验证结果：红灯 `8 failed, 24 passed`，
    split 绿灯 `41 passed`，群消息行为回归 `13 passed`，普通 API split
    相邻回归 `87 passed`，静态检查通过，全量回归
    `1651 passed, 6 skipped, 139 warnings in 122.43s`。当时建议先做 chat helper /
    contract / persistence 抽取设计；`/health` 收益很低，不优先拆。
  - 进展：`api/routes.py` 第十一刀已拆出 chat content / response contract helper 到
    `api/chat_content_helpers.py` 与 `api/chat_response_contract.py`；旧 `api.routes`
    继续保留同名 wrapper，`/chat` 路由本体、`ChatProxyRequest`、私聊缓冲、
    `_persist_chat_turn()`、`_safe_meta()`、`get_bridge` / `get_guardrail` monkeypatch、
    `CHAT_STREAM_QUEUE_MAXSIZE` 和 `/health` 仍留在父模块。保留 SSE delta 合并、
    安全错误事件、response envelope、`answer_chunks`、ChatLog 完整图片归档和
    ConversationTurn 图片摘要语义；新模块不反向导入 `api.routes`，也没有
    `asyncio.run` 或 `run_awaitable_sync`。`api/routes.py` 从 1709 行降至
    1604 行，`api/chat_content_helpers.py` 为 76 行，
    `api/chat_response_contract.py` 为 163 行，拆分测试为 144 行。验证结果：
    红灯 `7 failed, 36 passed`（另一次初始红灯暴露并修正了测试侧 request_id 假设），
    helper split 绿灯 `8 passed`，普通 API split 相邻回归 `62 passed`，
    `/chat` 流式与信封回归 `21 passed`，私聊缓冲和持久化关键回归 `11 passed`，
    静态检查通过，全量回归 `1662 passed, 6 skipped, 139 warnings in 125.10s`。
    下一刀候选为聊天落库 writer、私聊缓冲状态机或 streaming finalizer 的进一步设计；
    继续不优先拆 `/health`。
  - 进展：`api/routes.py` 第十二刀已拆出聊天落库 writer 到
    `api/chat_persistence.py`；旧 `api.routes._safe_meta()` 和
    `api.routes._persist_chat_turn()` 继续保留父模块 wrapper，`proxy_chat()`、
    流式 finalizer 和非流式分支仍通过父模块名称调用，保留既有 monkeypatch spy
    契约。本阶段没有迁移 `/chat` 路由本体、`ChatProxyRequest`、私聊缓冲、
    streaming runner、Prompt Runtime 输入组装、`get_bridge` / `get_guardrail`
    monkeypatch、`CHAT_STREAM_QUEUE_MAXSIZE` 或 `/health`；新模块不反向导入
    `api.routes`，也没有 `asyncio.run` 或 `run_awaitable_sync`。新增
    `tests/test_api_chat_persistence_split.py` 覆盖 silent 遮罩、injection 安全提示、
    HTML 全量归档 / 上下文摘要、Prompt audit failure meta、timing meta、source ids
    去重、evolution running pending count 和 SQLite locked retry 相邻契约。
    `api/routes.py` 从 1604 行降至 1516 行，`api/chat_persistence.py` 为
    165 行，拆分测试为 194 行。验证结果：红灯
    `6 failed, 49 passed, 21 warnings in 11.14s`，新增 split 绿灯
    `10 passed`，普通 API split 相邻回归 `74 passed`，`/chat` 行为回归
    `8 passed`，静态检查通过，全量回归
    `1673 passed, 6 skipped, 139 warnings in 126.35s`。下一刀候选为私聊缓冲
    状态机或 streaming finalizer / push envelope 构造；继续不优先拆 `/health`。
  - 进展：`api/routes.py` 第十三刀已拆出聊天请求契约与请求元信息 helper 到
    `api/chat_request_contract.py`；旧 `api.routes.ChatProxyRequest` 继续可导入，
    `_clone_chat_request()`、`_resolve_push_target_id()`、
    `_extract_group_id_from_chat_request()`、`_chat_request_platform()`、
    `_chat_request_type()`、`_normalize_request_client_meta()`、
    `_private_prompt_audit_failure_meta()` 和 `_private_timing_meta()` 均保留父模块
    wrapper，`proxy_chat()` 仍通过父模块名称调用，保持既有 monkeypatch 入口。
    本阶段没有迁移 `/chat` 路由本体、私聊缓冲、streaming finalizer、聊天落库、
    Prompt Runtime 输入组装、response envelope 或 `/health`；新模块不反向导入
    `api.routes`，也没有 `asyncio.run` 或 `run_awaitable_sync`。`api/routes.py`
    从 1516 行降至 1468 行，`api/chat_request_contract.py` 为 96 行，拆分测试为
    196 行。验证结果：红灯 `1 failed, 25 passed`，相邻扫描红灯 `4 failed`，
    新增 split 绿灯 `26 passed`，相邻扫描绿灯 `4 passed`，`/chat` helper /
    persistence 回归 `18 passed`，全量回归
    `1699 passed, 6 skipped, 139 warnings in 127.31s`。下一刀候选为
    runtime / guardrail facade、私聊缓冲状态机或 streaming finalizer；继续不优先拆
    `/health`。
  - 进展：`api/routes.py` 第十四刀已拆出 Chat Runtime Facade 到
    `api/chat_runtime_facade.py`；旧 `/chat` 路由本体、`get_bridge` patch point、
    私聊缓冲、guardrail 预跑、streaming finalizer、聊天落库和 `/health` 仍留在父模块。
    新模块承载 `ChatRuntimeInput`、`ChatRuntimePayload`、
    `build_chat_runtime_payload()` 和 `call_bridge_non_streaming()`，不反向导入
    `api.routes`，也没有 `asyncio.run` 或 `run_awaitable_sync`。Prompt Runtime
    核查确认 `bridge_meta` 字段名、`<user_input>` 包裹、`raw_query`、
    `history_header`、`history_messages`、`effort_constraint`、`runtime_preset`
    和 `stream` 语义未改变，默认模板与运行时模板无需变更。`api/routes.py`
    当前为 1470 行，`api/chat_runtime_facade.py` 为 156 行，拆分测试为 269 行。
    验证结果：红灯 `6 failed, 2 passed`，相邻扫描红灯 `4 failed, 41 passed`，
    新增 split 绿灯 `8 passed`，相邻 split 回归 `45 passed`，`/chat` /
    streaming 回归 `90 passed`，聊天拆分与 asyncio 策略回归 `47 passed`，
    全量回归 `1707 passed, 6 skipped, 139 warnings in 127.22s`。下一刀候选为
    guardrail thin facade、私聊缓冲基础件或 streaming helper；继续不优先拆
    `/health`。
  - 进展：`api/routes.py` 第十五刀已拆出 Chat Guardrail Facade 到
    `api/chat_guardrail_facade.py`；旧 `api.routes._detect_guardrail()` 继续保留
    父模块 wrapper，`get_guardrail()` patch point、`_build_guardrail_input()`、
    私聊缓冲 `asyncio.to_thread()` 调度、superuser passthrough、streaming finalizer、
    聊天落库、response envelope 和 `/health` 仍留在父模块。新模块承载
    `detect_guardrail()` 与 `guardrail_status_from_result()`，兼容新
    `detect_injection()` 和 legacy `classify()`，不反向导入 `api.routes`，
    也没有 `asyncio.run` 或 `run_awaitable_sync`。`api/routes.py` 从 1470 行降至
    1449 行，`api/chat_guardrail_facade.py` 为 51 行，拆分测试为 153 行。
    验证结果：红灯 `8 failed, 1 passed`，相邻扫描红灯 `4 failed`，新模块绿灯
    `9 passed`，相邻扫描绿灯 `4 passed`，父模块门面回归 `9 passed`，
    guardrail 与私聊缓冲邻近回归 `9 passed`，asyncio 策略回归 `3 passed`，
    全量回归 `1716 passed, 6 skipped, 139 warnings in 137.97s`。下一刀候选为
    私聊缓冲基础件或 streaming helper；继续不优先拆 `/health`。
  - 进展：`api/routes.py` 第十六刀已拆出 Chat Streaming Helper 到
    `api/chat_streaming_helpers.py`；旧 `_stream_chat()`、`StreamingResponse`、
    `CHAT_STREAM_QUEUE_MAXSIZE`、断连后台落库、push envelope、private buffer
    finalize 和父模块 SSE wrapper 仍留在 `api.routes`。新模块承载
    `StreamEventCoalescer`、`collect_ready_stream_events()` 和
    `drain_stream_queue_until_task_done()`，不反向导入 `api.routes`，也没有
    `asyncio.run` 或 `run_awaitable_sync`。`api/routes.py` 从 1449 行降至
    1408 行，`api/chat_streaming_helpers.py` 为 81 行，拆分测试为 122 行。
    验证结果：红灯 `6 failed`，相邻扫描红灯 `4 failed`，新模块绿灯
    `6 passed`，相邻扫描绿灯 `4 passed`，streaming 行为回归 `15 passed`，
    断连后台与 asyncio 策略回归 `7 passed`，全量回归
    `1722 passed, 6 skipped, 139 warnings in 131.90s`。下一刀候选为私聊缓冲
    基础件；继续不优先拆 `/health`。
  - 进展：`api/routes.py` 第十七刀已拆出 Chat Private Buffer 基础件到
    `api/chat_private_buffer.py`；旧 `/chat` 路由本体、PrivateTimingGate、
    guardrail provider、Bridge 调用、owner deadline sleep、聊天落库、SSE、
    push envelope、response envelope 和 `/health` 仍留在 `api.routes`。新模块承载
    `PrivateBufferConfig`、`PrivateBufferStore`、owner / follower 状态结果和私聊缓冲
    纯 helper，不反向导入 `api.routes`，也没有 `asyncio.run` 或
    `run_awaitable_sync`。父模块 `_private_buffers`、`_private_lock`、窗口常量、
    `asyncio.sleep`、`_time.time`、`get_guardrail`、`_detect_guardrail`、
    `get_bridge` 和 `_persist_chat_turn` patch point 均保持兼容。本阶段未引入
    generation id，`_finalize_private_buffer(user_id)` 仍保持 user-level 语义。
    `api/routes.py` 从 1408 行降至 1351 行，`api/chat_private_buffer.py` 为
    138 行，拆分测试为 184 行。验证结果：红灯 `4 failed, 1 passed`，
    相邻扫描红灯 `4 failed`，新模块绿灯 `5 passed`，相邻扫描绿灯 `4 passed`，
    private buffer 行为回归 `11 passed`，asyncio 策略与相邻 `/chat` 回归
    `9 passed`，全量回归 `1727 passed, 6 skipped, 139 warnings in 129.45s`。
  - 进展：`api/routes.py` 第十八刀已拆出 Chat Push Envelope 辅助件到
    `api/chat_push_envelope.py`；断连后台 push envelope 手写 meta 已迁移到
    `build_chat_push_envelope()`，非流式和流式 done 的图片 token 传输展开已复用
    `expand_chat_transport_answer()`，并固定 `allow_base64=False`。本阶段保留
    `/chat` 路由本体、SSE 主循环、stream finalizer、`push_envelope_to_qq()`
    调用点、DB 持久化、`_chat_response_payload()`、`get_bridge()`、
    `BackgroundTasks.add_task()` 和父模块 patch point；未迁移完整 `_stream_chat()`、
    `StreamingResponse` 或 `_persist_stream_result_after_runner_done()`。新模块不反向
    导入 `api.routes`，也没有 `asyncio.run`、`run_awaitable_sync` 或同步函数包装
    awaitable；未使用的 `_chat_response_meta()` 父模块 dead facade 已删除。
    `api/routes.py` 从 1351 行降至 1331 行，`api/chat_push_envelope.py` 为
    68 行，拆分测试为 120 行。验证结果：红灯 `5 failed, 1 warning`，
    相邻扫描红灯 `4 failed, 1 warning`，新模块阶段 `4 passed, 1 failed`，
    接入组合 `13 passed, 21 warnings`，断连相邻回归 `4 passed, 1 warning`，
    扫描与 `asyncio.run` 策略回归 `7 passed, 1 warning`，全量回归
    `1732 passed, 6 skipped, 139 warnings in 124.30s`。
  - 进展：`api/routes.py` 第十九刀修复私聊缓冲 Deadline 主动唤醒到
    `api/chat_private_buffer.py`；每个 buffer 新增 `deadline_changed` 内部 signal，
    owner deadline wait 改为 `PrivateBufferStore.wait_until_deadline()`，follower
    在缩短 deadline 后会主动唤醒 owner 并取消旧 sleep，`finalize()` 也会唤醒
    deadline waiter，避免 waiter 卡在过期 sleep 上。本阶段保留 `/chat` 路由本体、
    PrivateTimingGate、guardrail、Bridge、落库、SSE、push envelope、response
    envelope、`_private_buffers`、`_private_lock` 和父模块 monkeypatch facade；
    未迁移完整私聊 flow，未迁移 stream finalizer，未引入 generation id，也没有
    新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
    `api/routes.py` 从 1331 行变为 1333 行，`api/chat_private_buffer.py` 为
    198 行，拆分测试为 311 行。验证结果：红灯 `4 passed / 3 failed` 与 route
    超时红灯，store 阶段 `6 passed / 1 failed`，接入组合 `13 passed, 1 warning`，
    asyncio 与断连流式回归 `7 passed, 1 warning`，全量回归
    `1734 passed, 6 skipped, 139 warnings in 124.18s`。
  - 进展：`api/routes.py` 第二十刀已拆出 Chat Persona Context 格式化 helper 到
    `api/chat_persona_context.py`；旧 `api.routes._format_persona_for_prompt()`
    继续作为父模块 wrapper，`proxy_chat()` 调用点、DB persona lookup、
    `PersonaInjectionService`、Prompt Runtime `persona_text` 字段、Bridge、落库、
    SSE、push envelope 和 response envelope 均保持不变。新模块不反向导入
    `api.routes`，也没有 `asyncio.run`、`run_awaitable_sync` 或同步函数包装
    awaitable。`api/routes.py` 从 1333 行降至 1236 行，
    `api/chat_persona_context.py` 为 114 行，拆分测试为 118 行。验证结果：
    红灯 `4 failed, 1 warning`，新模块阶段 `1 failed, 3 passed, 1 warning`，
    父模块接入定向绿灯 `5 passed, 1 warning`，相邻回归
    `13 passed, 21 warnings`，静态检查通过，全量回归
    `1738 passed, 6 skipped, 139 warnings in 122.53s`。
  - 进展：`api/routes.py` 第二十一刀已拆出 Chat Media Precache 调度 helper 到
    `api/chat_media_precache.py`；旧 `api.routes._schedule_image_precache()`
    继续作为父模块 wrapper，`proxy_chat()` 调用点、`_normalize_files` patch
    point、`BackgroundTasks.add_task()` 语义、图片预缓存懒加载、Bridge、私聊
    缓冲、guardrail、落库、SSE、push envelope 和 response envelope 均保持不变。
    新模块不反向导入 `api.routes`，也没有 `asyncio.run`、`run_awaitable_sync`
    或同步函数包装 awaitable。`api/routes.py` 从 1236 行降至 1233 行，
    `api/chat_media_precache.py` 为 34 行，拆分测试为 115 行。验证结果：
    红灯 `4 failed, 1 warning`，新模块阶段 `1 failed, 3 passed, 1 warning`，
    父模块接入定向绿灯 `4 passed, 1 warning`，相邻回归
    `6 passed, 21 warnings`，静态检查通过，全量回归
    `1742 passed, 6 skipped, 139 warnings in 124.31s`。
  - 进展：`api/routes.py` 第二十二刀已把用户屏蔽规则匹配 helper 拆到
    `core/user_block_rules.py`；旧 `api.routes._check_user_blocked()` 继续作为
    私聊父模块 wrapper，`app.group_ingress.helpers.check_user_blocked()` 继续作为
    群聊 wrapper，`evals/runners/moderation_runner.py` 仍可导入旧父模块入口。
    本阶段保留 `/chat` block 后只写 ChatLog 并 silent 返回的行为，也保留群聊
    block 后 annotate timing event 并返回 `no_reply/user_blocked` 的行为；Admin
    CRUD、DB schema、WebUI、`rule_mode` / `reason`、ChatLog、Bridge、SSE、
    push envelope 和 response envelope 均未迁移或改语义。新模块不反向导入
    `api.routes` 或 `app.group_ingress.helpers`，也没有 `asyncio.run`、
    `run_awaitable_sync` 或同步函数包装 awaitable。`api/routes.py` 从
    1233 行降至 1227 行，`app/group_ingress/helpers.py` 从 650 行降至
    644 行，`core/user_block_rules.py` 为 53 行，拆分测试为 224 行。
    验证结果：红灯 `11 failed, 1 warning`，core helper 阶段
    `2 failed, 9 passed, 1 warning`，接入定向绿灯 `12 passed, 1 warning`，
    相邻回归 `9 passed, 1 warning`，静态检查通过，全量回归
    `1754 passed, 6 skipped, 139 warnings in 122.64s`。
  - 进展：`api/routes.py` 第二十三刀已拆出 Chat Streaming Result 收尾 helper 到
    `api/chat_streaming_result.py`；旧 `_stream_chat()` 内 runner 完成后的落库、
    private buffer finalize、Prompt V2 audit no-send 和断连后台 push 逻辑改为通过
    `ChatStreamResultContext` 与 `ChatStreamResultCallbacks` 委托新模块。父模块继续保留
    `/chat` route、完整 `_stream_chat()` SSE 主循环、`StreamingResponse`、
    `CHAT_STREAM_QUEUE_MAXSIZE`、done / error event、evolution trigger 和全部
    monkeypatch facade。本阶段保持 request DB 与后台 `UnitOfWork` 新 session 的区别，
    保持 Prompt V2 audit 失败不 push、assistant no-context meta、断连后台 push、
    bounded queue drain 和 push 前传输层图片 token 展开语义。新模块不反向导入
    `api.routes`，不直接导入或调用 `push_envelope_to_qq`，也没有 `asyncio.run`、
    `run_awaitable_sync` 或同步函数包装 awaitable。`api/routes.py` 从 1227 行降至
    1163 行，`api/chat_streaming_result.py` 为 164 行，拆分测试为 237 行。
    验证结果：红灯 `8 failed, 1 warning`，新模块定向 `4 passed, 1 warning`，
    split 扫描 `4 passed, 1 warning`，父模块接入定向 `8 passed, 1 warning`，
    streaming 相邻回归 `27 passed, 21 warnings in 8.81s`，静态检查通过，
    全量回归 `1758 passed, 6 skipped, 139 warnings in 122.37s (0:02:02)`。
  - 进展：`api/routes.py` 第二十四刀已拆出 Chat SSE Loop queue pump helper 到
    `api/chat_sse_loop.py`；旧 `_stream_chat()` 内等待 `stream_queue` / `done`、
    heartbeat、delta coalescing、tail drain 和 pending delta flush 的循环改为通过
    `ChatSseLoopCallbacks` 委托新模块。父模块继续保留 `/chat` route、
    `StreamingResponse`、`CHAT_STREAM_QUEUE_MAXSIZE`、queue 创建、runner task、
    `bridge.handle_message(..., stream_queue=..., stream=True)`、业务收尾、Prompt V2
    audit、success done payload、evolution trigger 和断连后台调度。本阶段未迁移
    `proxy_chat()`、完整 `_stream_chat()`、message envelope、push envelope、
    Prompt Runtime 输入或模板；新模块不反向导入 `api.routes`、FastAPI、DB、
    `core.daily_digest`、`get_bridge()` 或 `get_guardrail()`，也没有 `asyncio.run`、
    `run_awaitable_sync` 或同步函数包装 awaitable。`api/routes.py` 从
    1163 行降至 1121 行，`api/chat_sse_loop.py` 为 100 行，拆分测试为 128 行。
    验证结果：红灯 `9 failed, 1 warning`，新模块阶段
    `1 failed, 4 passed, 1 warning`，父模块接入组合回归
    `39 passed, 21 warnings in 9.63s`，静态检查通过，全量回归
    `1763 passed, 6 skipped, 139 warnings in 122.15s (0:02:02)`。
  - 进展：`api/routes.py` 第二十五刀已拆出非流式 Bridge 成功结果收尾到
    `api/chat_non_streaming_result.py`；父模块继续保留 Bridge 调用、KT error path、
    `HTTPException`、evolution 后台任务和 SSE 路径，新模块只通过 callbacks
    处理 reply meta、Prompt V2 audit failure、private buffer finalize、原始
    answer 落库、transport answer 展开和响应 payload。本阶段未迁移
    `proxy_chat()`、`_do_chat()`、`get_bridge()`、`chat_runtime_facade`、
    Prompt Runtime 输入或模板、SSE、message envelope 或 push envelope；新模块
    不反向导入 `api.routes`、FastAPI、DB、`core.daily_digest`、Bridge 或 guardrail，
    也没有 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
    `api/routes.py` 从 1121 行降至 1098 行，`api/chat_non_streaming_result.py`
    为 128 行，拆分测试为 246 行。验证结果：红灯 `9 failed, 1 warning`，
    helper 阶段 `8 passed, 1 failed, 1 warning in 6.74s`，父模块接入定向
    `5 passed, 1 warning in 0.91s`，相邻回归 `31 passed, 21 warnings in 7.90s`，
    静态检查通过，全量回归
    `1768 passed, 6 skipped, 139 warnings in 125.22s (0:02:05)`。
  - 进展：`api/routes.py` 第二十六刀已拆出私聊 pre-bridge 决策编排到
    `api/chat_pre_bridge_decision.py`；父模块继续保留 DB、HTTP response、
    `_persist_chat_turn()`、`_chat_response_payload()`、guardrail silent 落库、
    `PersonaInjectionService`、Prompt Runtime payload、Bridge 调用、SSE、非流式结果
    收尾和所有 monkeypatch facade。新模块只通过 `ChatPreBridgeServices` 接收
    private timing、guardrail、private buffer、时间源和 logger patch point，并返回
    `ChatPreBridgeEarlyReturn` 或 `ChatPreBridgeContinue` outcome。本阶段未迁移
    `proxy_chat()` 路由本体、history 注入、`safe_user_input`、`enriched_query`、
    `bridge_meta`、message envelope、push envelope 或 Prompt Runtime 模板；新模块不
    反向导入 `api.routes`、FastAPI、DB、Bridge 或 Prompt Runtime，也没有新增
    `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。`api/routes.py`
    从 1098 行降至 1022 行，`api/chat_pre_bridge_decision.py` 为 316 行，
    拆分测试为 378 行。验证结果：红灯 `12 failed, 1 warning in 6.94s`，
    helper 阶段 `6 passed, 2 failed, 1 warning in 6.57s`，父模块接入定向
    `8 passed, 1 warning in 0.96s`，split 扫描 `4 passed, 1 warning in 1.11s`，
    private buffer / guardrail 相邻回归 `27 passed, 21 warnings in 5.18s`，
    静态检查通过，全量回归
    `1776 passed, 6 skipped, 139 warnings in 137.28s (0:02:17)`；文档收口
    提交前复跑 `1776 passed, 6 skipped, 139 warnings in 123.02s (0:02:03)`。
  - 进展：`api/routes.py` 第二十七刀已拆出 Chat persona snapshot lookup 到
    `api/chat_persona_lookup.py`；父模块继续保留 persona fallback / missing /
    lookup 日志、`PersonaInjectionService`、Prompt Runtime payload、Bridge 调用、
    SSE、response envelope 和落库边界。本阶段未迁移 `proxy_chat()` 路由本体、
    history 注入、`safe_user_input`、`enriched_query`、`bridge_meta`、message
    envelope、push envelope 或 Prompt Runtime 模板；新模块不反向导入
    `api.routes`、FastAPI、Bridge 或 Prompt Runtime，也没有新增 `asyncio.run`、
    `run_awaitable_sync` 或同步函数包装 awaitable。`api/routes.py` 从 1022 行降至
    1020 行，`api/chat_persona_lookup.py` 为 73 行，拆分测试为 207 行。验证结果：
    红灯 `9 failed, 1 passed, 21 warnings in 7.66s`，helper 阶段
    `5 passed, 1 failed, 21 warnings in 6.94s`，父模块接入定向
    `6 passed, 21 warnings in 1.38s`，split 扫描
    `4 passed, 1 warning in 1.27s`，相邻回归
    `14 passed, 21 warnings in 3.26s`，静态检查通过，全量回归
    `1782 passed, 6 skipped, 139 warnings in 133.42s (0:02:13)`。
  - 进展：`api/routes.py` 第二十八刀已拆出 Chat pre-bridge route result 转译到
    `api/chat_pre_bridge_route_result.py`；父模块继续保留 HTTP route、DB callback、
    persona injection、Prompt Runtime payload、Bridge、SSE、response envelope 和
    后续落库边界。新模块只把 `ChatPreBridgeEarlyReturn` /
    `ChatPreBridgeContinue` 转成 route early response 或 continue context，通过
    callbacks 复用 `_clone_chat_request()`、`_persist_chat_turn()`、
    `_chat_response_payload()` 和 `_finalize_private_buffer()` patch point；父模块
    用闭包把当前 `db` 绑定到持久化 callback。guardrail silent 分支继续使用
    `persist_req` 持久化 `（数据中转，自动静默）`。本阶段未迁移 history 注入、
    `PersonaInjectionService`、Prompt Runtime payload、Bridge、SSE、message
    envelope、push envelope 或 response envelope；新模块不反向导入 `api.routes`、
    FastAPI、Bridge、Prompt Runtime 或 DB 全局入口，也没有新增 `asyncio.run`、
    `run_awaitable_sync` 或同步函数包装 awaitable。`api/routes.py` 从 1020 行降至
    1013 行，`api/chat_pre_bridge_route_result.py` 为 115 行，拆分测试为 311 行。
    验证结果：红灯 `10 failed, 1 warning in 6.84s`，helper 阶段
    `5 passed, 1 failed, 1 warning in 6.49s`，父模块接入定向
    `6 passed, 1 warning in 0.96s`，split 扫描 `4 passed, 1 warning in 1.19s`，
    相邻回归 `15 passed, 21 warnings in 2.36s`，静态检查通过，全量回归
    `1788 passed, 6 skipped, 139 warnings in 124.64s (0:02:04)`。
  - 进展：`api/routes.py` 第二十九刀已拆出 Chat runtime route context 组装到
    `api/chat_runtime_route_context.py`；父模块继续保留 HTTP route、DB session、
    带 `label="chat_before_bridge"` 参数的 `release_clean_session_transaction()`、
    Bridge、SSE、response envelope、push envelope、非流式 / 流式结果收尾和后续
    落库边界。新模块只负责动态 persona injection、`ChatRuntimeInput` 委托构造、
    payload 展开和 Prompt budget 日志，通过 services 接收
    `_build_multimodal_user_input_text()`、`_estimate_tokens()`、
    `_chat_request_platform()`、`get_effort_constraint()`、
    `chat_runtime_facade.build_chat_runtime_payload()`、persona injection callback 和
    logger。Prompt Runtime 字段名、`<user_input>` 包裹、conversation 结构、工具输出
    契约、message envelope、push envelope 和 response envelope 均未改变；默认模板与
    `data/prompts_v2/` 运行时模板无需变更。新模块不反向导入 `api.routes`、FastAPI、
    Bridge、Prompt Runtime 模板注册或 DB 全局入口，也没有新增 `asyncio.run`、
    `run_awaitable_sync` 或同步函数包装 awaitable。`api/routes.py` 从 1013 行降至
    1005 行，`api/chat_runtime_route_context.py` 为 177 行，拆分测试为 342 行。
    验证结果：红灯 `7 failed, 1 warning in 3.73s` 与扫描红灯
    `4 failed, 1 warning in 3.95s`；helper 阶段
    `6 passed, 1 deselected, 1 warning in 0.73s`，完整新测试剩余父模块 wrapper
    红灯 `6 passed, 1 failed, 1 warning in 6.54s`；父模块接入后新测试
    `7 passed, 1 warning in 0.99s`，定向 / 相邻回归
    `24 passed, 21 warnings in 3.60s`，split 扫描 `4 passed, 1 warning in 1.13s`，
    静态检查通过，全量回归
    `1795 passed, 6 skipped, 139 warnings in 125.86s (0:02:05)`。
  - 进展：`api/routes.py` 第三十刀已拆出 Chat Route Runner 到
    `api/chat_route_runner.py`；旧 `/chat` endpoint、`get_bridge()` patch point、
    `StreamingResponse`、`HTTPException`、DB session、pre-bridge、Prompt Runtime
    payload、message envelope、push envelope 和 response envelope 边界继续留在父模块。
    新模块承载 stream / non-streaming bridge runner 编排、SSE 事件产出、断连后台
    收尾登记和 route 级错误描述；不反向导入 `api.routes`、不导入 FastAPI HTTP
    边界、不直接持有 DB / UoW 边界，也没有新增 `asyncio.run`、
    `run_awaitable_sync` 或同步函数包装 awaitable。Prompt Runtime 字段名、
    `<user_input>` 包裹、history / persona 注入、conversation 结构、工具输出契约、
    message envelope、push envelope 和 response envelope 均未改变，默认模板与
    `data/prompts_v2/` 运行时模板无需变更。`api/routes.py` 从 1005 行降至
    783 行，`api/chat_route_runner.py` 为 350 行，拆分测试为 432 行。
    验证结果：红灯 `10 failed, 1 warning in 6.34s`，扫描红灯
    `4 failed, 1 warning in 6.83s`；helper 阶段
    `9 passed, 1 deselected, 1 warning in 0.93s`，完整新测试剩余父模块接入红灯
    `9 passed, 1 failed, 1 warning in 6.56s`；父模块接入后新测试
    `10 passed, 1 warning in 0.92s`，相邻 chat split 回归
    `45 passed, 21 warnings in 4.57s`，split 扫描
    `4 passed, 1 warning in 0.96s`，主 API 回归
    `82 passed, 21 warnings in 22.08s`，streaming / envelope 回归
    `13 passed, 21 warnings in 6.08s`，wrapper / patch point 回归
    `61 passed, 1 warning in 3.31s`，静态检查通过，全量回归
    `1805 passed, 6 skipped, 139 warnings in 122.26s (0:02:02)`。
  - 进展：`core/persona_preprocess.py` 第一刀已拆出候选提取 prompt 和日志格式化
    helper 到 `core/persona_candidate_prompt.py`；旧 `core.persona_preprocess`
    导入路径保留同名符号兼容，状态机、embedding 懒加载、DB 写入和 monkeypatch
    契约保持原位；文件从 857 行降至 773 行。

- [x] **静默吞异常补日志（best-effort 路径）** · LOW · S 批量
  已在 `core/prompts/manager.py` 的 trace fallback、`core/context_legacy.py` 的 deprecated 群画像 fallback、`api/admin/system_routes.py` 的 git 探测 fallback、`app/group_ingress/helpers.py` 的 `safe_meta()` / `get_group_talk_value()` fallback，以及 `app/memory_digest/builder.py` 的 `_safe_meta()` fallback 补 `logger.debug`；日志只记录定位信息和异常摘要，不记录 prompt 正文、原始 `meta_json`、用户消息或群记忆 evidence。`core/legacy_adapter.py::SQLiteMemory.save_log()` 此前已完成 rollback + `logger.exception` + 回归测试，本次仅复验旧行为，未改业务语义。

- [x] **ruff 批量清理** · LOW · S
  已完成：tracked Python 当前通过 Ruff 默认检查；F401 facade / re-export 兼容项、旧式 typing 泛型、小类别告警和 naive datetime / DTZ 均已按批次收口。DB/ORM 仍保持 SQLite naive 本地时间语义，后续若要迁移到统一 aware 时间应另立设计，不再作为本项剩余。
  - 进展（2026-06-24）：F821 类型注解引用已清零；F841 未使用局部变量已清零，并补强 `news_daily` 同实体 render guard 测试，修复 top story 未计入 highlight 去重的问题；F541 冗余 f-string 前缀已清零；E401/E712/F402/E741 小类别已清零；F811 重复定义已清零；E701/E702 一行多语句已清零；E402 import 位置已清零。F401 第一批已完成 80 处低风险未用 import 清理，避开 `api/admin_routes.py`、`api/routes.py` 等 facade / re-export 兼容边界；第二批清理测试侧、runtime、news、persona 与 legacy adapter 的非大型 F401，并把拆分后的兼容符号改为显式 re-export；第三批先删除 `api/admin/model_routes.py` 中 6 个函数内真未用导入；第四批删除 `api/routes.py` 中 13 个真未用导入，并把 55 个普通 API 拆分后的旧路径兼容符号显式标记为 re-export；第五批将 `api/admin_routes.py` 中 235 个 Admin legacy facade 兼容导出改为冗余别名 re-export。当前 tracked Python F401 已清零。
  - 进展（2026-06-24）：旧式 typing 泛型第一批已完成非 vendor 代码的大部分 Ruff 自动现代化，覆盖 `List` / `Dict` / `Tuple` / `Set` 等 PEP 585 内置泛型、`Optional` 的 PEP 604 写法和相关 `typing` import 清理；第二批已收口 `core/legacy_adapter.py` 中剩余 40 个 `UP006/UP045/UP035` 命中，仅把本次改动行转为 LF 行尾，未做整文件换行归一化。当前非 vendor `UP006/UP007/UP045/UP035` 已清零；当时剩余的 naive datetime / DTZ 已在后续批次收口。
  - 进展（2026-06-24）：DTZ 第一批仅处理 eval/report artifact 时间戳，不触碰 ORM 持久化时间或历史窗口比较；`evals/periodic_manifest.py`、`evals/run.py`、`evals/rag_benchmark/report.py`、`evals/rag_benchmark/sample.py`、`evals/timing_signal_audit.py`、`evals/timing_tuning_proposal.py` 与 `evals/tuning_analysis.py` 已改为 timezone-aware 获取报告生成时间、文件名日期或文件 mtime。全仓 DTZ 统计从 312 降至 303；数据库审计确认 `core/database.py` 当前没有 Ruff DTZ 命中，实际 DB 风险在 `core/schema_migrations.py` 与各模块 ORM 写入/比较点，后续不能无脑将 DB 写入替换为 UTC aware。
  - 进展（2026-06-24）：DTZ 第二批处理生产 artifact / 中文展示时间，覆盖 Admin health / model catalog / RAG benchmark metadata、群记忆提取 metadata、生成图片 / Prompt 文件 mtime 展示、群分析 HTML 展示和新闻日报 fallback / cache 日期边界。机器可读 artifact 使用 aware UTC，中文用户展示和日报自然日使用 `Asia/Shanghai`，文件 mtime 展示保留本地 aware 墙钟语义；继续不触碰 ORM `DateTime` 写入、历史窗口比较、job retry/lock 和业务 recency 计算。全仓 DTZ 统计从 303 降至 281。
  - 进展（2026-06-24）：DTZ 第三批处理测试侧相对时间用例，覆盖 AI 日报新鲜度、旧新闻工具日期过滤、群分析 local RAG 临时消息和 semantic recency score 测试；新闻日期和临时消息时间改为 aware UTC，明确验证 naive reference 兼容的 semantic scoring 测试保留 naive 并加定点 `noqa`。全仓 DTZ 统计从 281 降至 271。
  - 进展（2026-06-24）：DTZ 第四批处理新闻日报测试 fixture，覆盖 `tests/test_news_daily_pipeline.py` 中 freshness、cluster、diversify、report 和 digest 的动态 `datetime.now()`；测试统一使用固定 aware UTC 基准，`parse_date()` 返回 naive datetime 的 4 个契约断言保留 naive expected 并加定点 `noqa`。全仓 DTZ 统计从 271 降至 227。
  - 进展（2026-06-24）：DTZ 第五批处理 EvalCandidate DB 时间戳，覆盖 `core/eval_sampling/store.py` 中 cursor、candidate status transition、promotion 和 trend 本地日期分桶；新增本地 `_db_now_naive()`，用 aware 当前时间转回本地 naive，保留 SQLite ORM 的 naive 本地时间语义。全仓 DTZ 统计从 227 降至 217。
  - 进展（2026-06-24）：DTZ 第六批处理 Admin Session Memory Browser 测试 DB fixture，覆盖 `tests/test_admin_session_memory_browser.py` 中 10 个固定 ORM 时间；新增 `_db_time()` 集中说明这些 fixture 保持 SQLite ORM naive 本地墙钟语义，并用单点 `noqa` 取代重复 DTZ 命中。全仓 DTZ 统计从 217 降至 207。
  - 进展（2026-06-24）：DTZ 第七批处理 Daily Digest 测试本地时间 fixture，覆盖 `tests/test_daily_digest.py` 中 ChatLog 目标日期、定时任务 prompt 北京时间入参和 `last_run_at` 近实时断言；新增 `_local_time()` / `_local_now()` 集中保留生产侧 naive 本地墙钟时间语义。全仓 DTZ 统计从 207 降至 200。
  - 进展（2026-06-24）：DTZ 第八批处理 RAG benchmark admin、memory digest 和 memory digest builder quality 测试 DB fixture，覆盖 `tests/test_rag_benchmark_admin.py`、`tests/test_memory_digest.py` 和 `tests/test_memory_digest_builder_quality.py` 中 6 个固定 ORM 时间；新增 `_db_time()` 集中保留 SQLite ORM naive 本地墙钟时间语义，不触碰生产代码、评测 fixture 数据层或 MemoryDigest 查询契约。定向回归 `52 passed, 21 warnings in 9.00s`，全量回归 `1805 passed, 6 skipped, 139 warnings in 122.37s`；全仓 DTZ 统计从 200 降至 194。
  - 进展（2026-06-24）：DTZ 第九批处理 RAG benchmark fixture 数据和 sticker RAG 测试默认时间，覆盖 `evals/rag_benchmark/fixtures.py` 与 `tests/test_sticker_rag.py` 中 4 个固定 ORM 时间；新增 `_db_time()` 集中保留 SQLite ORM naive 本地墙钟时间语义，不触碰生产缓存、RSS 日期解析或 runtime cache。定向回归 `47 passed, 21 warnings in 9.34s`，全量回归 `1805 passed, 6 skipped, 139 warnings in 121.59s`；全仓 DTZ 统计从 194 降至 190。
  - 进展（2026-06-24）：DTZ 第十批清零剩余 DTZ001，覆盖新闻搜索 runtime cache 日期规范化和 RSS published_parsed 日期格式化；`_coerce_date()` 改用 `date(...).isoformat()` 仅校验并输出日期字符串，RSS 解析改用 `date(*parsed[:3]).isoformat()` 保持原年月日输出。定向回归 `35 passed, 1 warning in 2.84s`；全仓 DTZ 统计从 190 降至 188，当前 DTZ001 已清零。
  - 进展（2026-06-24）：DTZ 第十一批处理 EvalCandidate 合同测试 DB now helper，覆盖 `tests/test_eval_candidate_contract.py` 中趋势按日聚合和只读 API fixture 的 3 个 `datetime.now()`；新增 `_db_now()` 集中保留 SQLite ORM naive 本地墙钟时间语义，不触碰 `core/eval_sampling/store.py` 生产趋势聚合逻辑或 Admin API 合同。定向回归 `34 passed, 21 warnings in 5.43s`；全仓 DTZ 统计从 188 降至 185。
  - 进展（2026-06-24）：DTZ 第十二批处理群体记忆测试 DB fixture，覆盖 `tests/test_group_memory_injection.py`、`tests/test_group_memory_rag.py` 和 `tests/test_group_memory_extraction_service.py` 中 13 个 `datetime.now()`；新增 `_local_now()` 集中保留 SQLite ORM naive 本地墙钟时间语义，窗口查询和相对新旧程度测试均保持原本本地时间语义。定向回归 `15 passed, 1 warning in 2.54s`；全仓 DTZ 统计从 185 降至 172。
  - 进展（2026-06-24）：DTZ 第十三批处理历史上下文测试 cutoff fixture，覆盖 `tests/test_history.py` 中 11 个 `datetime.now()`；新增 `_local_now()` 集中保留 SQLite ORM naive 本地墙钟时间语义，私聊/群聊 raw window、rolling summary 裁剪、ChatLog 群现场和 mark-clear 测试均用单个本地时间基准派生旧/新时间。定向回归 `21 passed, 1 warning in 2.92s`；全仓 DTZ 统计从 172 降至 161。
  - 进展（2026-06-24）：DTZ 第十四批处理生产侧 rolling summary / tracing DB 时间，覆盖 `app/session_memory/rolling_summary.py` 和 `core/tracing.py` 中 12 个 ORM 写入时间；新增 `core/time_utils.py` 提供 `db_now_naive()` / `to_db_naive()`，保持 SQLite ORM naive 本地墙钟时间语义，并兼容 tracing 外部传入的 aware `finished_at`。定向回归 `70 passed, 21 warnings in 6.16s`；全仓 DTZ 统计从 161 降至 149。
  - 进展（2026-06-24）：DTZ 第十五批处理 job 队列 DB 时间，覆盖 `app/session_memory/jobs.py`、`core/semantic/jobs.py` 和 `workers/semantic_index_worker.py` 中 22 个 job 状态机 / worker 写入时间；复用 `db_now_naive()` / `to_db_naive()`，claim、recover、failed retry 等同一状态迁移只取一次本地 naive 时间，不改 retry 状态语义。定向回归 `43 passed, 21 warnings in 3.10s`；全仓 DTZ 统计从 149 降至 127。
  - 进展（2026-06-24）：DTZ 第十六批处理任务相关测试 fixture，覆盖 `tests/test_session_memory.py` 和 `tests/test_semantic_index_worker.py` 中 10 个 `datetime.now()`；测试侧新增 `_local_now()`，集中保留 SQLite ORM naive 本地墙钟时间语义，raw window、history clear、群上下文 rollup、session summary worker stale job 和 semantic index worker transaction / recover fixture 均复用同一 helper 或单个 `now` 基准。定向回归 `43 passed, 21 warnings in 3.17s`；全仓 DTZ 统计从 127 降至 117。
  - 进展（2026-06-24）：DTZ 第十七批处理 Admin API 测试 fixture，覆盖 `tests/test_admin_api.py` 中 12 个 `datetime.now()`；测试侧新增 `_local_now()`，集中保留 ChatLog、ConversationTurn、GroupMemory 和 PersonaFact 等 SQLite ORM fixture 的 naive 本地墙钟时间语义，overview、timing gate events、画像治理、群记忆预览和工具目标列表用例继续使用单个 `now` 基准派生相对时间。定向回归 `30 passed, 1 warning in 4.89s`；全仓 DTZ 统计从 117 降至 105。
  - 进展（2026-06-24）：DTZ 第十八批处理画像预处理测试 fixture，覆盖 `tests/test_persona_preprocess.py` 中 8 个 `datetime.now()`；测试侧新增 `_local_now()`，集中保留 PersonaFact SQLite ORM fixture 和 `_apply_decay(now)` 入参的 naive 本地墙钟时间语义，衰减测试统一用单个 `now` 基准派生旧/新时间。定向回归 `33 passed, 3 skipped, 1 warning in 1.38s`；全仓 DTZ 统计从 105 降至 97。
  - 进展（2026-06-24）：DTZ 第十九批处理 RAG recency 测试 fixture，覆盖 `tests/test_knowledge_rag.py`、`tests/test_memory_query_rag.py` 和 `tests/test_sticker_rag.py` 中 3 个 `datetime.now()`；测试侧新增 `_local_now()`，集中保留 KnowledgeDocument、SemanticIndexItem 和 StickerMemory 等 SQLite ORM fixture 的 naive 本地墙钟时间语义，recency 断言继续使用单个 `now` 基准派生旧/新时间。定向回归 `45 passed, 1 warning in 5.55s`；全仓 DTZ 统计从 97 降至 94。
  - 进展（2026-06-24）：DTZ 第二十批处理群分析测试 fixture，覆盖 `tests/test_group_analysis_tool.py` 中 3 个 `datetime.now()`；测试侧新增 `_local_now()`，集中保留 ChatLog ORM fixture 与 RawChatLog dataclass fixture 的 naive 本地墙钟时间语义，最近窗口、artifact 过滤和 LLM 失败降级用例继续使用单个 `now` 基准派生相对时间。定向回归 `21 passed, 1 warning in 1.63s`；全仓 DTZ 统计从 94 降至 91。
  - 进展（2026-06-24）：DTZ 第二十一批处理 DB-backed 测试 fixture，覆盖 `tests/test_persona_injection_service.py`、`tests/test_prompt_trace_admin.py`、`tests/test_rag_debug.py` 和 `tests/test_reply_admin.py` 中 4 个 `datetime.now()`；测试侧新增 `_local_now()`，集中保留 PersonaFact、trace DB、GroupMemory 和 ReplyContractCheckLog fixture 的 naive 本地墙钟时间语义，reply traffic 窗口断言继续使用单个 `now` 基准派生相对时间。定向回归 `36 passed, 21 warnings in 8.88s`；全仓 DTZ 统计从 91 降至 87。
  - 进展（2026-06-24）：DTZ 第二十二批处理本地测试脚本时间戳，覆盖 `tests/local_test.py` 中 1 个 `datetime.now()`；该位置只用于 push raw / json 本地调试 artifact 文件名，改为复用文件内已有 `time.strftime()` 风格，保持本地墙钟文件名语义并移除 `datetime` 导入。目标文件 Ruff / `compileall` 通过；全仓 DTZ 统计从 87 降至 86。
  - 进展（2026-06-24）：DTZ 第二十三批处理 DB 管理辅助时间，覆盖 `core/schema_migrations.py`、`core/runtime_tool_service.py` 和 `core/knowledge_library.py` 中 4 个命中；migration applied_at、RuntimeToolDecision 清理 cutoff 和 KnowledgeDocument 写入时间统一复用 `db_now_naive()`，保留 SQLite ORM naive 本地墙钟语义，migration 备份文件名改为 `time.strftime()`。定向回归 `46 passed, 139 warnings in 8.97s`；全仓 DTZ 统计从 86 降至 82。
  - 进展（2026-06-24）：DTZ 第二十四批处理 Admin/API DB naive 时间，覆盖 `api/admin/persona_routes.py`、`api/history_log_routes.py` 和 `api/task_routes.py` 中 4 个命中；画像提取 cutoff / Persona.updated_at、mark-clear 清除边界和 ScheduledTask.last_run_at 均复用 `db_now_naive()`，保留 SQLite ORM naive 本地墙钟语义。定向回归 `24 passed, 21 warnings in 3.60s`；全仓 DTZ 统计从 82 降至 78。
  - 进展（2026-06-24）：DTZ 第二十五批处理 app 层记忆/画像 DB naive 时间，覆盖 `app/group_ingress/helpers.py`、`app/group_memory/retrieval_service.py`、`app/group_memory/injection_service.py`、`app/persona/injection_service.py`、`app/persona/retrieval_service.py` 和 `app/session_memory/llm_summarizer.py` 中 7 个命中；重复群回复窗口、群体记忆/画像 recency、注入记录时间和 LLM rolling summary 写入时间统一复用 `db_now_naive()`，保留 SQLite ORM naive 本地墙钟语义。`GROUP_MEMORY_RAG_CACHE` 的 3 个缓存 TTL 命中暂不混入本批，后续单独改为 monotonic 时间。定向回归 `104 passed, 3 skipped, 21 warnings in 7.04s`；全仓 DTZ 统计从 78 降至 71。
  - 进展（2026-06-24）：DTZ 第二十六批处理群体记忆 RAG 缓存 TTL，覆盖 `app/group_memory/injection_service.py` 中 3 个内存缓存命中；`GROUP_MEMORY_RAG_CACHE` 过期戳改为 `time.monotonic()` 浮点值，避免 wall-clock 调整影响短 TTL 缓存，不改变 DB naive 时间语义。新增缓存 TTL 行为测试，定向回归 `7 passed, 1 warning in 1.15s`；全仓 DTZ 统计从 71 降至 68。
  - 进展（2026-06-24）：DTZ 第二十七批处理 core 旧记忆/表情/画像 DB 时间，覆盖 `core/ai_daily_ingest.py`、`core/expression_memory.py`、`core/group_memory.py`、`core/legacy_adapter.py` 和 `core/sticker_memory.py` 中 14 个 ORM 写入时间；AI Daily 入库、Expression / Jargon 更新、GroupMemory upsert、旧 KT adapter 画像 / SystemPrompt 回写、StickerMemory 注册 / 使用 / 描述时间统一复用 `db_now_naive()`，保留 SQLite ORM naive 本地墙钟语义。定向回归 `41 passed, 1 warning in 4.30s` 与 `34 passed, 1 warning in 3.68s`；全仓 DTZ 统计从 68 降至 54。
  - 进展（2026-06-25）：DTZ 第二十八批处理 Admin/API 与 session memory 日期边界，覆盖 `api/admin/reply_routes.py`、`api/admin/runtime_routes.py`、`api/memory_routes.py` 和 `app/session_memory/retrieval_service.py` 中 11 个命中；回复评测 case 更新时间、真实流量窗口、runtime 统计窗口和 `since_last_reply` 统一复用 `db_now_naive()`，记忆摘要默认目标日期按本地 naive 自然日取昨天，session summary 日期过滤改用 `datetime.fromisoformat()` 保持 SQLite naive 日期边界。定向回归 `65 passed, 21 warnings in 6.18s`；全仓 DTZ 统计从 54 降至 43。
  - 进展（2026-06-25）：DTZ 第二十九批处理核心上下文与群分析时间清理，覆盖 `core/context_builder.py`、`core/context_legacy.py`、`core/expression_learner.py`、`core/group_runtime/state.py`、`core/persona_preprocess.py`、`core/semantic/scoring.py` 和 `creatures/nanobot/prompts/skills/group_analysis/repository.py` 中 11 个命中；上下文相对时间、TimingGate / group recent cutoff、表达学习扫描窗口、画像状态机候选时间、语义 recency 参考时间和群分析 SQL cutoff 统一复用 `db_now_naive()`，群 pending 的 epoch 秒转本地墙钟展示改为 aware UTC 解析后再转 naive。定向回归 `94 passed, 3 skipped, 1 warning in 5.61s`；全仓 DTZ 统计从 43 降至 32。
  - 进展（2026-06-25）：DTZ 第三十批处理 Daily Digest、定时任务执行和新闻搜索剩余时间清理，覆盖 `core/daily_digest.py`、`creatures/nanobot/prompts/skills/schedule_task/tool.py`、`creatures/nanobot/prompts/skills/news_search/runtime_cache.py`、`search_backend.py`、`tool.py`、`legacy_report.py`、`evidence.py` 以及 `news_daily` pipeline / sources 中剩余 32 个命中；调度任务、摘要查询、新闻缓存 key、日期抽取、展示时间、最近性判断和 RSS / HTML 日期解析均沿用本地 naive 或原有 UTC-aware 比较口径，`strptime` 无时区解析改为 `fromisoformat()`、`parsedate_to_datetime()` 或手写日期解析。定向回归 `21 passed, 21 warnings in 3.18s` 与 `120 passed, 1 warning in 4.85s`；tracked Python Ruff 默认检查通过；全量回归 `1806 passed, 6 skipped, 139 warnings in 128.89s`；tracked Python DTZ 已清零，本项完成。

---

## 二、架构演进路线

> 原 1–10 编号保留为**稳定 ID**（第一节缺陷条目按 §N 引用，勿改号）。下方「实施阶段」仅表达推荐推进顺序与依赖，不改变编号。
> 贯穿性主题：**platform 维度**（QQ / 未来 Web）目前几乎处处缺失——项 3/4/5/7/9 本质都是「为多平台接入补上 platform 这一维」，宜成簇推进。

### 实施阶段总览

| 阶段 | 主线目标 | 路线项 | 关键依赖 | 关联缺陷 |
|------|----------|--------|----------|----------|
| **P1 收敛去债（地基）** | 消除多引擎/多套模板分叉，连接池与请求构造打底 | 1, 2, 3 | 项 1 已完成；H29 后续沿 Bridge 边界独立推进 | H29 / H7 / — |
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
- **粗略路径**：① lifespan 创建共享 session（已完成）→ ② new_api_client 三处 `async with ClientSession()` 改为复用注入的 session（已完成）→ ③ 审计 compaction / image / sticker / 工具层同步 IO（已完成）→ ④ 修复贴纸 `background_tasks=None` fallback 的 async 热路径风险（已完成）→ ⑤ 补图片附件与 Direct 工具 `to_thread` 回归守卫（已完成）→ ⑥ 同步文档并将路线项 2 标记为完成（已完成）。
- **验证状态（2026-06-18）**：P1-7 定向测试 `186 passed, 20 warnings`；全量测试 `1222 passed, 6 skipped, 113 warnings in 86.76s`。

#### 路线项 3 — 请求构造按模型能力校验（image_url / 多模态），能力声明入模型配置

- **现状（2026-06-18 已落地）**：模型记录已归一到顶层 `supports_image` / `supports_tools` / `supports_stream` 字段，并兼容 `model_overrides.json` 的顶层字段和嵌套 `capabilities`；`get_ordered_candidates(required_capabilities=<需求>)` 已支持硬过滤，显式不满足能力的候选不会进入排序。直接 `NewAPIClient.chat_completion()` / `chat_completion_stream()` 已能从 messages、tools 和 stream 推导能力需求；Bridge 主回复路由也已从 `metadata["files"]`、ToolPlan schema 和 KT 固定 streaming 请求事实生成能力需求，手动回复模型不满足能力时回退自动路由。payload / SDK request 前 guard 已防止绕过候选过滤；无视觉候选时会降级为纯文本说明并重新路由，不再把 `image_url` 发给纯文本模型。`model_routing` eval 已覆盖带图请求必须选 vision 候选，防止后续改动破坏能力硬过滤。
- **剩余痛点**：路线项 3 的能力校验主链路已完成。base64 data URL 直入 payload 与 `docs/message-field-standard.md` 禁 base64 的长期方向仍需在后续出站 / 入站契约中继续收敛；图片数量 / 大小上限也应跟随多平台消息信封和出站渲染契约继续设计。
- **目标**：模型能力（`supports_image` / `supports_tools` / `supports_stream`、单图大小 / 数量上限）结构化写入模型配置；构造阶段检测 messages 含 `image_url` 时，强制只在 vision 候选中选模型，并按能力校验图片格式 / 大小，不满足则降级（剥图 + 文本兜底）或换模型，而非无脑塞。
- **关联**：呼应项 9（多模态行为描述需同步 canonical Prompt Runtime 模板）；与熔断器记账正确性（E4/E5）相关；主 reply 与 sticker_describe（走专用 vision provider）的能力口径需统一。
- **粗略路径**：① `model_overrides.json` / registry 增结构化 capabilities（已完成）→ ② 构造边界生成 `has_image` / tools / stream 信号（直接 New API 与 Bridge 已完成）→ ③ 路由在 `has_image` 时按 `supports_image` 过滤候选（已完成）→ ④ `_build_payload` / SDK request 前按能力校验 / 裁剪 image_url、stream、tools（已完成）→ ⑤ 扩展 `model_routing` eval 并统一两套 vision 机制的能力引用（已完成）。

---

### P2 — 多平台接入底座（platform 维度补全）

#### 路线项 4 — 工具配置增加 platform 维度（platform 策略闭环已完成）

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

- **现状（2026-06-20 已落地）**：核心链路已从「纯 Qwen 三态判断」推进到 scoring 混合决策。已新增 `core/timing_score.py`，覆盖 `d0/linger/s_ack/s_transport/s_other/s_bot/w_*` 信号、`E_rule/E_final`、冲突升级、模型权重和 `rule_fallback`；`GroupRuntime` 已接入 shadow scoring、普通 ambient 确定性短路、模型失败规则兜底、`directed_to_other` scoring 软化、ambient / legacy / timer cooldown scoring 短路，以及 session / platform 级模型层策略。私聊已接入同一套 shared timing scoring，分类器结果回灌为 `TimingModelHint`；分类器失败 / 非法输出已按 `c=0` 跳过模型融合并进入 `rule_fallback`，旧格式兼容仍按 `c=0.5` 低置信参与。群聊 `timing_scoring` 已写入 ChatLog meta 并由 admin events / WebUI 调试页透出；私聊 `PrivateDecision.timing_scoring` 已随 user ChatLog、assistant ChatLog 和 ConversationTurn meta 持久化为 `timing_gate`，可回溯 action、reason、effort、runtime_preset 与 scoring 明细。`evals` 也能在 action 缺失时执行 scoring 并校验 `expected.scoring`。群聊 `s_bot` live path 收口已完成设计与计划归档（`6463ee8`、`1795d04`），任务 1 已随 `2fcfad7` 落地：`current_bot` 仍保持 hard stop，其他 bot 通过 `is_other_bot` 进入 scoring 软抑制。
- **已完成**：`@bot + 图片` 规则 WAIT 不调模型；纯 ambient / 纯确认可规则 `no_reply`；`directed_to_other + linger` 进入冲突升级；TimingGate 模型 prompt 已同步“仅指向其他人默认 no_reply，`@bot` / 回复 bot / 余韵冲突结合上下文裁量”的语义，并用 paired eval case 覆盖纯 directed、`@bot + @其他人` 和余韵冲突三类场景；正常模型返回路径已采用 scoring blend 的最终 `action/delay/reason`，不再只把 scoring 当 shadow 字段；TimingGate JSON / 旧格式 / 非法 / 网络错误解析已回灌 `parse_quality` 与 `model_confidence`，旧格式按 `0.5` 低置信参与模型融合；模型 `network_error/parse_error` 后使用规则侧 `rule_fallback`，不再全群哑火；私聊分类器 `invalid output fallback` / `classifier fallback` 已按 `model_confidence=0.0` 进入规则兜底；`wait.delay_seconds` 上限已与设计收敛到 15 秒；`s_ack` 排除请求词、问号、URL、代码、文件；`s_transport` 已按 secret/blob/url/codeblock/long dump 分档；`force_next_continue` 已降级为 `d0=1.0` 后完整走 Stage 1-4；`enabled` / `rules_only` / `shadow` 模型策略已支持 default / platform / session 三级覆盖；真实 ChatLog 信号审计 CLI 已输出假阳率、shadow mismatch 和阈值建议；`timing_gate` eval 已支持 baseline diff 和阈值门禁；私聊评分已补齐 ChatLog meta 可观测闭环；`explicit_bot` / `client_meta` 已进入 `GroupRuntime`，`timing_message` 和 `GroupPendingMessage` 已透传 `is_other_bot`，`_score_timing()` 会按 pending 窗口内 `is_other_bot=any(m.is_other_bot for m in msgs)` 调用 `decide_timing()`，并在 ChatLog meta 中记录 `s_bot=0.70`。
- **s_bot 验证状态（2026-06-20）**：三项定向测试结果为 `3 passed, 21 warnings in 2.16s`；相邻回归 `tests/test_api.py tests/test_timing_runtime.py tests/test_timing_score.py` 结果为 `157 passed, 21 warnings in 23.30s`。
- **已完成与待确认**：核心混合决策主线、私聊可观测闭环、P3-3A 标注审计复跑入口和 P3-3B 仓库自包含 CI / PR gate 均已完成。更多真实样本的选择、标注仲裁、候选趋势报表、定期复跑、周期运行 manifest、跨 artifact 周期趋势、周期趋势只读调参分析和 TimingSignal 不可变 artifact 加厚均已进入运营闭环。TimingGate 调参提案运营链路已补齐 run-scoped audit、`final_timing_action` 人工 truth、候选参数治理、真实 audit 样本 simulation、Admin record-only 审核 API 和 WebUI record-only 审核入口。
- **关联**：H2 已完成 admin route 异步化和 repeats 收紧；后续与路线项 8（评测体系）、路线项 5/7（响应信封与调试可观测）继续协同。
- **评测体系依赖**：路线项 8 / P4 的完成情况见下方独立章节；本项仅保留与 TimingGate 调参提案相关的依赖说明，避免把 P4 误读为路线项 10 的子任务。
- **下一步**：用真实 run-scoped audit、final action truth 和候选参数文件持续生成 proposal，并通过 record-only 审核沉淀人工结论；仍不自动应用参数、不更新 baseline、不改变 gate。

---

### P4 — 评测体系

#### 路线项 8 — 评测体系从既有 `evals/` 框架升级为基线 + 回归门禁（大工程）

- **P4-4 验证状态（2026-06-18）**：RAG manual deterministic gate 输出 `cases=3 passed=3 failed=0` 和 `Gate passed`；RAG 三件套回归为 `29 passed, 21 warnings`；WebUI build 退出码为 0；全量回归为 `1359 passed, 6 skipped, 139 warnings in 99.40s`。
- **P4-5A 验证状态（2026-06-18）**：统一 gate 脚本输出 `timing_gate`、`capability_model_routing`、`capability_reply_contract`、`capability_rendering_contract` 和 RAG manual deterministic gate 全部 `Gate passed`；评测守卫组合为 `35 passed, 1 warning in 2.34s`；全量回归为 `1361 passed, 6 skipped, 139 warnings in 100.83s`。
- **P4-5B 验证状态（2026-06-18）**：定向评测组合 `tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py tests/test_rag_benchmark.py` 结果 `40 passed, 1 warning in 2.42s`；周期性脚本 `bash scripts/run_eval_periodic.sh` 输出评测守卫 `27 passed, 1 warning in 1.78s`，`timing_gate`、三个 capability gate 和 RAG manual deterministic gate 全部 `Gate passed`；PR gate `bash scripts/run_eval_pr_gate.sh` 输出评测守卫 `27 passed, 1 warning in 1.76s` 且全部子 gate `Gate passed`；全量回归为 `1366 passed, 6 skipped, 139 warnings in 101.52s`。
- **P4-5C 验证状态（2026-06-18）**：RAG manual case 数从 3 增加到 9；baseline 合同测试已收紧；RAG manual deterministic gate 输出 `cases=9 passed=9 failed=0` 和 `Gate passed`；评测守卫相邻回归 `tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `32 passed, 1 warning in 1.90s`；PR gate 和周期性 gate 均通过；全量回归为 `1367 passed, 6 skipped, 139 warnings in 99.86s`。
- **P4-5D 验证状态（2026-06-20）**：RAG stable gate 已从 `manual` 切到 `manual+fixture`；新增 `positive_v1` memory fixture DB builder 和 `memory_fixture_positive_001` 正例；baseline 的 `positive_cases` 从 0 提升到 1，`hit@5=1.0`、`mrr=1.0`；RAG fixture gate 输出 `cases=10 passed=10 failed=0` 和 `Gate passed`；相邻回归 `tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `35 passed, 1 warning in 2.51s`；PR gate 结果为评测守卫 `27 passed, 1 warning in 2.26s` 且所有子 gate 通过；周期性 gate 结果为评测守卫 `27 passed, 1 warning in 2.22s` 且所有子 gate 通过。
- **P4-5E 验证状态（2026-06-20）**：RAG `positive_v1` fixture 已从 memory 单正例扩展为 memory + knowledge 双正例；新增 `knowledge_fixture_positive_001`，固定命中 `knowledge:9001:chunk:0`，并通过 `requires_citation=true` 的 citation check；RAG stable gate 输出 `cases=11 passed=11 failed=0` 和 `Gate passed`；RAG 相邻回归 `tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `37 passed, 1 warning in 2.78s`；citation 相邻回归结果 `3 passed, 21 warnings in 1.84s`；全量回归为 `1374 passed, 6 skipped, 139 warnings in 105.13s`。
- **P4-5F 验证状态（2026-06-20）**：RAG `positive_v1` fixture 已从 memory + knowledge 双正例扩展为 memory + knowledge + sticker 三正例；新增 `sticker_fixture_positive_001`，固定命中 `sticker:9101:sticker`，并通过 `requires_sendable=true` 的 sendable check；RAG stable gate 输出 `cases=12 passed=12 failed=0` 和 `Gate passed`；RAG 相邻回归 `tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `39 passed, 1 warning in 2.27s`；sticker 相邻回归结果 `23 passed, 21 warnings in 3.26s`；全量回归为 `1377 passed, 6 skipped, 139 warnings in 105.89s`。
- **P4-5G 验证状态（2026-06-20）**：RAG `positive_v1` fixture 已从 memory + knowledge + sticker 三正例扩展为 memory + knowledge + sticker + group_memory 四正例；新增 `group_memory_fixture_positive_001`，固定命中 `group_memory:9201:memory`，并通过 `requires_group_id=true` 的 group filter check；跨群 decoy `group_memory:9202:memory` 未出现在候选中；RAG stable gate 输出 `cases=13 passed=13 failed=0` 和 `Gate passed`；RAG 相邻回归 `tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `40 passed, 1 warning in 2.65s`；group memory 相邻回归结果 `16 passed, 21 warnings in 1.99s`；PR gate 和周期性 gate 均通过；全量回归为 `1380 passed, 6 skipped, 139 warnings in 105.13s`。
- **P4-5H 验证状态（2026-06-20）**：RAG `positive_v1` fixture 已强化过滤约束：memory 正例新增跨 user、跨 session、跨 source decoy；knowledge 正例新增 `trust_level`、`source_type`、`published_after` decoy；sticker 正例新增其他 stream 与 global decoy；group_memory 保留跨群 decoy。RAG stable gate 输出 `cases=13 passed=13 failed=0` 和 `Gate passed`；RAG 相邻回归 `tests/test_rag_benchmark.py tests/test_eval_baseline.py` 结果 `40 passed, 1 warning in 2.33s`；PR gate 输出评测守卫 `27 passed, 1 warning in 1.82s` 且所有子 gate 通过；周期性 gate 输出评测守卫 `27 passed, 1 warning in 1.71s` 且所有子 gate 通过；全量回归为 `1380 passed, 6 skipped, 139 warnings in 104.70s`。
- **现状（2026-06-21 已完成 P4-5H、真实样本运营 1-10 与 TimingGate proposal record-only 运营链路）**：评测框架**已存在**而非空白：`evals/run.py`（CLI `python -m evals.run --suite timing_gate`）+ `schema.py`（`EvalCase`/`EvalOutput`/`EvalResult`/`SuiteReport` pydantic）+ `scorers.py` + `runners/`（sticker / memory / moderation / model_routing / rendering_contract 等 per-suite runner）+ `cases/`（regression 10 例、rag_benchmark/manual、timing_gate 多例、candidates、capability_model_routing、capability_reply_contract、capability_rendering_contract）+ `sample_from_db.py`/`sample_from_logs.py`（从库 / 日志采样造例）。`evals/baseline.py` 已提供 baseline diff 与阈值门禁，`run.py` 已支持 `--baseline`、`--min-pass-rate`、`--max-new-failures`，`SuiteReport` 可携带 `baseline_diff` 与 `gate`；TimingGate CI / PR gate、首个 `capability_model_routing` 能力数据集 gate、`capability_reply_contract` gate 和 `capability_rendering_contract` gate 均已落地。P4-2「Admin 标注工作台契约化与 promote 预检 UI」已完成：后端 expected contract schema/API、WebUI 标注表单契约化、`note` / `expected` 分离、promote dry-run → apply 预检 UI 均已落地，并通过 WebUI 静态测试 `18 passed`、候选闭环回归 `24 passed`、WebUI build 退出码 0 和全量回归 `1350 passed, 6 skipped`。P4-3 已新增 `capability_reply_contract` 与 `capability_rendering_contract` 数据集、baseline、离线 gate、渲染相邻回归和全量回归。P4-4 已为 `evals.rag_benchmark` 增加专用 baseline 纯函数、CLI gate、稳定 `evals/baselines/rag_benchmark.json`、Admin gate API、WebUI gate 展示和报告落盘字段。P4-5A 已新增 `scripts/run_eval_pr_gate.sh`，统一串联 TimingGate、capability 和 RAG gate，并让 `.github/workflows/timing-gate-eval.yml` 调用统一入口。P4-5B 已新增 `scripts/run_eval_periodic.sh` keep-going 周期性入口；`.github/workflows/timing-gate-eval.yml` 已具备 `workflow_dispatch`、每周 schedule 和 artifact 归档，artifact 包含 `evals/reports/*.json`、`evals/reports/runs/**/timing_signal_audit.json`、`tmp/rag_benchmark/reports/*.json` 和 `tmp/rag_benchmark/reports/*.md`。P4-5C 已把 RAG manual deterministic gate 的稳定 `constraint_only` 样本扩到 9 个，并补 baseline 与 manual case 集合一致性守卫；P4-5D 已新增固定 memory positive fixture，并把稳定 gate 的 `case_scope` 切到 `manual+fixture`；P4-5E 已新增固定 knowledge positive fixture 和 citation 正例断言；P4-5F 已新增固定 sticker positive fixture 和 sendable 正例断言；P4-5G 已新增固定 group_memory positive fixture、group filter 断言和跨群 decoy forbidden check；P4-5H 已补强 memory / knowledge / sticker 的同 query decoy，并在 CLI fixture gate 与 baseline contract 中守住 forbidden hits 为空。跨 artifact 周期趋势已基于 periodic manifest 落地，输出只读 `artifact_trends_latest.json`，用于观察 eval / RAG / TimingSignal 的跨 run 漂移；周期趋势只读调参分析已新增 `evals.tuning_analysis`，输出 `tuning_analysis_latest.json`，把趋势和 raw audit 转成复核、补标注、补 artifact 或暂不调整建议；TimingSignal audit 已扩展为 latest、dated 和 run-scoped 三类报告，manifest 优先索引 run-scoped 报告；`evals.timing_tuning_proposal` 已补齐 run-scoped audit 校验、`final_timing_action` truth 合同、候选参数治理、真实 audit 样本 simulation、Admin record-only 审核 API 和 WebUI record-only 审核入口，不自动调参。
- **已完成与剩余重点**：baseline diff 和门禁能力已具备，TimingGate CI / PR gate 已完成首个仓库自包含接入；`capability_model_routing`、`capability_reply_contract`、`capability_rendering_contract` 与 RAG benchmark gate 已完成第一批能力 / RAG 基线，P4-5A 已把这些稳定 gate 收敛为统一 PR gate，P4-5B 已补齐周期性复跑、手动触发和报告归档，P4-5C 已完成第一轮 RAG manual 样本扩充，P4-5D 已补上首个 fixture-backed positive RAG case，P4-5E 已补上 knowledge fixture citation 正例，P4-5F 已补上 sticker fixture sendable 正例，P4-5G 已补上 group_memory fixture 正例，P4-5H 已补上过滤约束 fixture。P4-1 已先修复会污染评测数据的契约缺口：`expected_json` / `expected` 字段错配已兼容，空 expected、`needs_label=true` 和不可评分字段会被拒绝，promote 已支持 dry-run 与 `target_dataset`，离线 candidates export / import-labels / promote CLI 已可用。P4-2 已补齐后端 expected 类型 / 枚举契约、WebUI 旧字段提交和直接 promote 的产品化缺口；真实样本运营已补齐候选 readiness / preflight、reject / defer / reopen 仲裁状态、record-only 人工仲裁批次审计、EvalCandidate 运营趋势报表、周期运行 manifest、跨 artifact 周期趋势、周期趋势只读调参分析、TimingSignal 不可变 artifact 和 TimingGate 调参提案 record-only 审核运营链路。剩余重点是用真实 run-scoped artifact、action truth 和候选参数持续生成可审查报告，并沉淀人工审核结论。
- **目标**：把既有 `evals/` 升级为体系——统一指标与基线快照、回归对比门禁（PR 跑核心 suite 并比对 pass_rate / score 漂移）、分能力数据集（提示词 / 路由 / RAG / TimingGate / 渲染）、人工标注回流与 `candidates → labeled` 闭环。
- **关联**：依赖项 1 / 6 / 10 等行为先稳定（否则基线频繁失效）；与项 10 共享 timing_gate 套件、项 3 共享 model_routing 套件。
- **粗略路径**：① 固化基线快照与指标口径（已完成 timing_gate 核心 suite）→ ② `run.py` 增 baseline diff + 阈值门禁（已完成）→ ③ 接入 TimingGate 外部 CI / PR gate（已完成 P3-3B）→ ④ P4-1 expected 契约、候选标注、promote dry-run、离线 CLI、dataset / suite 边界和首个 `capability_model_routing` 数据集（核心闭环已完成，计划记录在 `.Codex/plans/eval-dataset-labeling.md`）→ ⑤ P4-2A 后端 expected contract schema/API（已完成）→ ⑥ P4-2B Admin WebUI 标注表单契约化与 promote 预检 UI（已完成）→ ⑦ P4-3 扩 `capability_reply_contract` / `capability_rendering_contract` 等更多 per-capability 数据集（已完成）→ ⑧ P4-4 RAG benchmark baseline gate、Admin / WebUI 展示和稳定 baseline（已完成）→ ⑨ P4-5A 统一 PR gate（已完成）→ ⑩ P4-5B 周期性复跑与报告归档（已完成）→ ⑪ P4-5C RAG manual 样本扩充（已完成）→ ⑫ P4-5D fixture-backed positive RAG case（已完成）→ ⑬ P4-5E knowledge fixture citation 正例（已完成）→ ⑭ P4-5F sticker fixture sendable 正例（已完成）→ ⑮ P4-5G group_memory fixture 正例（已完成）→ ⑯ P4-5H 过滤约束 fixture（已完成）→ ⑰ 真实样本运营动作：TimingGate 信号周期审计、RAG generated → manual 仲裁入口、EvalCandidate 运营规则、候选 reject / defer 仲裁状态、人工仲裁批次审计、EvalCandidate 运营趋势报表、周期运行 manifest、跨 artifact 周期趋势、周期趋势只读调参分析、TimingSignal 不可变 artifact 加厚和 TimingGate 调参提案 record-only 审核运营链路均已完成；下一步是按真实数据持续复核报告，而不是自动改变 gate。

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
