# 主动研究伙伴修复与加速验证实现计划

> 本计划按 `test-driven-development` 执行：每组功能先写失败测试并观察红灯，再做最小实现，
> 最后重构。用户未要求提交，本轮不执行 Git commit。

**目标：** 修复主动外呼从未发送和模型输出截断问题，落地受限研究型伙伴，并用加速模拟替代
七天真实影子期。

**设计文档：**
`docs/superpowers/specs/2026-07-10-proactive-research-companion-design.md`

## 改动边界

- 模型契约与路由：`clients/classifier_client.py`、`core/config_registry.py`、
  `core/route_metadata.py`、`api/admin/model_routes.py`。
- 主动外呼：`core/proactive_outreach.py`、`api/admin/proactive_outreach_routes.py`。
- 研究能力：新增 `core/proactive_research.py`、`core/web_search/url_policy.py`，修改
  `core/runtime_tool_service.py`、`core/web_search/relevance.py` 和 Web Search 工具契约。
- Prompt Runtime：新增 8 个模板文件，修改 `core/prompt_v2/template_registry.py` 和
  `core/prompt_v2/variables.py`。
- 加速验证：新增 `core/proactive_simulation.py`、CLI 和对应测试。
- 并发与 dry-run：新增数据库评估租约；`nanobot_kt/bridge.py` 只传递请求级 dry-run
  ContextVar。禁止修改 `vendor/`，不覆盖用户正在调整的 Prompt V2 编译链。

## 任务 1：模型结构化返回和独立路由

- [x] 在 `tests/test_model_router.py` 先增加结构化响应、`finish_reason/reasoning_content/usage`
  保留、旧字符串 API 兼容测试。
- [x] 增加 outreach 四个路由默认关闭 thinking、独立 max_tokens、管理端可编辑测试。
- [x] 运行定向测试并确认红灯。
- [x] 实现 `ModelRouteResponse`、`call_model_route_response()`、路由继承与配置元数据。
- [x] 运行定向测试至绿灯。

## 任务 2：严格 Judge/Generator 契约与历史修复

- [x] 在 `tests/test_proactive_outreach.py` 增加 length、空正文、部分 JSON、字段类型错误、空
  Generator、纯 think Generator 的失败测试。
- [x] 增加 `ConversationTurn/history_clear_at/private session/user role` grounding 与活跃时段测试。
- [x] 增加虚拟 `now` 贯穿 grounding、pending `created_at` 使用虚拟时间、Judge 错误不写正常
  pending 的测试。
- [x] 运行新增测试并确认红灯。
- [x] 实现严格三态契约、独立路由调用、历史查询 helper、空投递守卫和调度开关热读取。
- [x] 运行 `tests/test_proactive_outreach.py` 至绿灯。

## 任务 3：Canonical Prompt Runtime

- [x] 在 `tests/test_prompt_v2.py` 增加四个 task 模板可枚举、变量合法、默认/运行时同步测试。
- [x] 运行新增测试并确认红灯。
- [x] 新增 outreach extract/judge/generate/research 默认及运行时模板。
- [x] 更新模板 alias、task 分类、变量 scope 和 classifier route 映射。
- [x] 运行 Prompt V2 定向测试至绿灯。

## 任务 4：不可扩权的 research preset

- [x] 新增 `tests/test_research_tool_plan.py`：工具集合上限、后台禁用不被重启用、ToolOverride
  不能扩权、reply/no_reply 保留。
- [x] 运行测试并确认红灯。
- [x] 在 `core/runtime_tool_service.py` 实现 research 归一化和最终 ceiling。
- [x] 运行工具计划相关测试至绿灯。

## 任务 5：研究执行器与来源门控

- [x] 新增 `tests/test_proactive_research.py`，覆盖独立 bridge metadata、探索预算、超时、stop、
  no_reply、0/1/2 来源、跨 trace/幻造 URL、来源服务端附加和 runner 不触发 publisher。
- [x] 运行新增测试并确认红灯。
- [x] 实现 ResearchRequest/Budget/Source/Result、预算插件、bridge 生命周期、ToolCall 来源提取、
  引用校验和草稿长度限制。
- [x] 把 research 决策接入 `run_outreach_once()`，先持久化 candidate，再走独立 publisher。
- [x] 运行研究与主动外呼测试至绿灯。

## 任务 6：加速七日模拟和硬 dry-run

- [x] 新增 `tests/test_proactive_simulation.py`，覆盖场景矩阵、虚拟时间、确定性报告、重复率、
  预算和 `external_push_count=0`。
- [x] 增加 Admin `/proactive-outreach/simulate` 安全入口测试；确认它不调用生产 run-once/push。
- [x] 运行新增测试并确认红灯。
- [x] 实现内存 SQLite 模拟器、RecordingPublisher、脚本场景和 live dry-run 候选入口。
- [x] 增加 CLI，失败门禁返回非零退出码。
- [x] 运行两次模拟并比较规范化报告一致，确认七天回放不 sleep。

## 任务 7：对抗性权限与状态机加固

- [x] ToolPlan 和研究预算同时覆盖 `pre_subagent_run`，阻断 native `memory_write` 绕过。
- [x] 在 provider 出站前校验 query 相关性、敏感数据和长度；修复连续中文实体拼接洗白。
- [x] URL 只接受规范公开 HTTP(S)，正文先去 think 再核验，候选和投递前重复核验。
- [x] 研究正文拒绝 CQ/媒体/贴纸控制 token，来源标题做纯文本转义。
- [x] 模拟 gate 不信任自报字段，从 required case、ledger 和 publisher records 重算。
- [x] Judge/Generator 错误写 `evaluation_error` 锚点；publisher 前失租恢复 candidate；陈旧
  不确定 `sending` 转 `ambiguous`，不重发旧 key 但允许未来新语义外呼。
- [x] NewAPI 严格校验 HTTP 200 tool_calls，dry-run 通过 ContextVar 抑制贴纸计数副作用。
- [x] forced Generator 契约失败使用无事实断言的服务端安全短句；未知发布结果进入 ambiguous，
  并在完整最大沉默窗口内禁止新语义重发。
- [x] 研究 preset 移除可能同步阻塞/写索引/下载模型的 `knowledge_query`；Web Search query 增加
  分组数字、拆分敏感词门禁，最终正文拒绝普通裸域名和裸 IPv4。
- [x] NewAPI HTTP 200 同时严格校验 finish_reason、usage 和 reasoning 字段类型。
- [x] 七日 gate 从 candidate/attempt 原始行重算聚合，并逐条核对 publisher 的正文、naive ISO
  时间、case 和 key；证据时间非法或时区混用时失败关闭。
- [x] 主动外呼评估租约在取得 SQLite 写锁后再冻结时钟，release 失败不覆盖业务结果；迁移表
  的非空约束、默认值和索引语义与 ORM 对齐，已有 schema 漂移时失败关闭。

## 任务 8：验证与审查

- [x] 运行所有定向测试和模拟 CLI。
- [x] 清除代理环境变量后尝试真实模型 live dry-run；服务不可达时保留错误证据，不误称通过。
- [x] 运行 `python -m pytest tests/ -v`，要求 0 failures。
- [x] 运行 `git diff --check`、`git diff --name-only -- vendor`，确认无格式错误、无 vendor 改动。
- [x] 使用中文代码审查检查高风险路径：QQ 不可达性、幂等状态、契约失败、历史清除、来源
  伪造和预算并发。
- [x] 根据审查结果修复并重跑相关定向验证，不提交 Git。
