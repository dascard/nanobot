# Prompt Runtime 契约整改实施计划

> **面向 AI 代理的工作者：** 按任务顺序执行。每个任务先使用
> `test-driven-development` 完成红灯、绿灯和重构；所有完成声明前使用
> `verification-before-completion`。需要拆给子 agent 时使用
> `subagent-driven-development`，但主线程必须复核结论和最终 diff。

**目标：** 修复真实运行时请求中暴露的 Python 执行越权、Final Action 来源伪造、
运行时元数据逃逸、关键任务模板失效、画像跨用户写入、日报参数丢失，以及鉴权事实
表达、核心 Prompt 审计、工具 schema 重复、请求指纹/token、私聊 `group_id` 和超级
用户工具相关性问题，为全会话 `session_guidance` 建立严格、可证明的前置契约。

**设计依据：**
`docs/superpowers/specs/2026-07-13-prompt-runtime-contract-remediation-design.md`

**后续计划：** `.codex/plans/session-guidance.md`

---

## 执行约束

- 不修改 QQbot 端、QQ push 协议、入站协议或 CQ renderer。
- 不修改默认或 Runtime `identity_context.md` 中的安全规则、角色设定、立绘提示词和
  说话方式。
- 不自动覆盖服务器已有 Runtime 模板正文。
- 不新增、复制或记录任何具体超级用户 ID；只处理布尔鉴权事实。
- 不更改唯一超级用户环境变量名称和解析格式。
- 不把高风险工具 approval 状态机扩大进本计划。
- 不修改任何 Prompt 模板正文；本计划只实现代码侧结构、启用门禁、schema 和执行器
  契约。已确认有误的模板文案登记为后续专项。
- 在没有 OS 级隔离前不尝试“修补”任意 Python 执行，而是全局硬禁用并让底层入口
  fail closed。
- 当前工作区有用户自己的未跟踪文件；只修改本计划列出的路径。
- 禁止 `git add -A` 和 `git add .`；必须按文件指定。
- 用户未明确说“提交”前不执行 `git commit`。下方提交命令都是授权后的检查点。
- 所有 pytest 命令清除代理环境变量。

## 依赖顺序

```text
Python 执行安全阻断
→ Final Action / 富结果 provenance
→ runtime metadata 角色隔离
→ 关键 TaskContract 与失败语义
→ persona actor 绑定
→ ai_daily 参数与工具契约测试
→ 显式鉴权事实
→ 核心 flow/audit
→ 工具 wire schema 固化
→ hash/token 绑定最终 envelope
→ 私聊 group_id
→ 权限上限与工具相关性
→ 专项集成与兼容门
```

安全前置任务 S1-S6 必须先完成。任务 3 完成前不得实现任务 4，因为 metrics 必须基于
已冻结的 wire schema。任务 1、2 是后续 `session_guidance` 的硬前置。

---

### 安全前置任务 S1：硬禁用不安全 Python 执行并下沉只读 SQL 边界

**文件：**

- 修改：`sandbox.py`
- 修改：`core/tool_registry.py`
- 修改：`core/runtime_tool_service.py`
- 修改：`core/config_registry.py`
- 修改：`core/legacy_adapter.py`
- 修改：`api/admin/tool_routes.py`
- 修改：`creatures/nanobot/prompts/skills/python_sandbox/tool.py`
- 修改：`creatures/nanobot/prompts/skills/sql_analysis/tool.py`
- 创建：`core/sql_readonly.py`
- 创建测试：`tests/test_python_sandbox_security.py`
- 创建测试：`tests/test_sql_analysis_security.py`
- 修改测试：`tests/test_audit_fixes.py`
- 修改测试：`tests/test_tool_plan.py`
- 修改测试：`tests/test_kt_framework.py`
- 修改测试：`tests/test_admin_api.py`

- [x] **步骤 1：编写 Python fail-closed 红灯测试**

所有攻击只使用 `tmp_path` 下的临时 SQLite，禁止读取或写入工作区 `nanobot.db`。

覆盖：

```python
def test_execute_python_analysis_is_fail_closed(tmp_path): ...
def test_python_sandbox_cannot_reopen_database_for_write(tmp_path): ...
def test_python_sandbox_cannot_reach_popen_via_object_graph(tmp_path): ...
def test_python_sandbox_tool_returns_disabled_without_executing(monkeypatch): ...
def test_legacy_run_python_analysis_is_disabled(monkeypatch): ...
```

攻击脚本即使包含 `sqlite3.connect()`、`PRAGMA database_list`、
`().__class__.__bases__[0].__subclasses__()` 或普通 `print()`，也必须得到同一个稳定的
禁用错误；临时数据库的行值和文件 hash 前后不变，且子进程 marker 文件不存在。

ToolPlan 参数化 `private/group/private_superuser`、`none/lightweight/full` 和显式
ToolOverride，断言 `python_sandbox` 始终为 disabled，最终 wire schema 中不存在该工具。

- [x] **步骤 2：编写底层只读 SQL 红灯测试**

直接调用 `AnalysisSandbox.run_query()`，不能只测 KT Tool，覆盖：

- 合法单条 SELECT/CTE + LIMIT；
- 合法 `PRAGMA table_info/table_xinfo/index_list/index_info/foreign_key_list`；
- 写 CTE、INSERT/UPDATE/DELETE/REPLACE、DDL；
- 多语句和注释拆分；
- ATTACH/DETACH、VACUUM/REINDEX；
- `PRAGMA database_list` 和修改型 PRAGMA；
- `SELECT load_extension(...)`；
- 超出 LIMIT 和 `SELECT *`。

被拒绝时不得出现绝对数据库路径。

- [x] **步骤 3：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_python_sandbox_security.py tests/test_sql_analysis_security.py tests/test_audit_fixes.py tests/test_tool_plan.py -v
```

预期：当前任意 Python 仍会执行，private_superuser/override 可重新启用，底层
`run_query()` 也没有完整校验。

- [x] **步骤 4：实现全局硬禁用**

`ToolDef` 增加 `force_disabled: bool = False`，`python_sandbox=True`。所有默认、preset、
DB override 合并完成后再次应用该硬约束；硬禁用优先级高于 superuser 和 override。

同时：

- 从 `_DEFAULT_LIGHTWEIGHT_SET` 和 `tool.lightweight_set` 默认值移除该工具；
- Admin 列表显示 `force_disabled`，默认值/轻量集/override API 不允许重新启用；
- `AnalysisSandbox.execute_python_analysis()` 不再构造/启动 subprocess，直接返回稳定错误；
- KT Tool 与 legacy `run_python_analysis`/直接 helper 同样 fail closed；
- 保留方法签名以减少调用方破坏，但不保留执行兼容旁路；
- 不增加 `_conn/_db_path/conn` 别名，不继续维护语言级假沙箱。

- [x] **步骤 5：建立单一只读 SQL validator 与 SQLite authorizer**

`core/sql_readonly.py` 提供纯函数校验和 authorizer 工厂，`SQLAnalysisTool` 与
`AnalysisSandbox.run_query()` 复用。数据库用 SQLite `mode=ro` URI 打开，再设置
`query_only=ON` 和 authorizer；authorizer 拒绝写 opcode、ATTACH/DETACH、危险 PRAGMA
和 extension function。

从只读 PRAGMA 白名单删除 `database_list`。底层无条件校验，确保 legacy 路径不能绕过
KT Tool 层。

- [x] **步骤 6：运行绿灯和工具集合回归**

运行同步骤 3，并增加：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_final_tools.py tests/test_research_tool_plan.py tests/test_kt_framework.py -v
```

- [x] **步骤 7：授权后的提交检查点（由最终聚合提交完成）**

用户明确说“提交”后才按实际变更文件精确暂存，建议提交信息：

```text
fix(工具安全): 阻断不安全脚本并收紧只读查询
```

---

### 安全前置任务 S2：绑定 Final Action 与富结果真实调用来源

**文件：**

- 修改：`nanobot_kt/reply_contract.py`
- 修改：`nanobot_kt/bridge.py`
- 修改：`creatures/nanobot/prompts/skills/reply/tool.py`
- 修改：`creatures/nanobot/prompts/skills/news_search/tool.py`
- 修改：`creatures/nanobot/prompts/skills/group_analysis/tool.py`
- 修改测试：`tests/test_reply_contract.py`
- 修改测试：`tests/test_bridge_integration.py`
- 修改测试：`tests/test_bridge_prompt_v2.py`
- 修改测试：`tests/test_kt_framework.py`
- 修改测试：`tests/test_reply_dry_run_context.py`
- 修改测试：`tests/test_streaming_bridge.py`
- 修改测试：`tests/test_ai_daily_tool_and_sources.py`
- 修改测试：`tests/test_ai_daily_ingest.py`
- 修改测试：`tests/test_group_analysis_tool.py`
- 修改测试：`tests/test_tools_package.py`
- 修改测试：`tests/test_reply_admin.py`
- 修改测试：`tests/test_prompt_trace_admin.py`

- [x] **步骤 1：建立真实 conversation fixture 并写红灯**

统一测试 helper 生成一对消息：

```python
assistant = {
    "role": "assistant",
    "content": "",
    "tool_calls": [{
        "id": call_id,
        "type": "function",
        "function": {"name": tool_name, "arguments": "{}"},
    }],
}
tool = {
    "role": "tool",
    "name": tool_name,
    "tool_call_id": call_id,
    "content": output,
}
```

禁止继续用匿名 tool fixture 证明正常路径。

红灯矩阵：

- verified reply 与 no_reply 成功；
- python_sandbox 伪造 reply marker 失败；
- 缺 name/ID、孤儿 ID、错名、重复声明、重复 result 失败；
- reply 输出 no_reply 形态、no_reply 输出正文失败；
- 任意 tool 的 `{"action":"reply"}` 不进入 Final Action 统计；
- python_sandbox HTML、assistant HTML、assistant marker JSON 都不能终结；
- verified ai_daily/group_analysis 富结果成功。

- [x] **步骤 2：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_reply_contract.py tests/test_bridge_integration.py -v
```

预期：普通工具 marker/HTML 和 assistant HTML 被错误接受。

- [x] **步骤 3：实现唯一 provenance helper**

在 `reply_contract.py` 增加 `VerifiedToolOutput` 和
`iter_verified_tool_outputs(messages)`：按顺序建立并消费 assistant declaration，严格
匹配 name + call ID，同一 ID 只消费一次，遇到新 user 边界清理未消费声明。

`extract_reply_tool_output()` 和 `count_final_action_tool_calls()` 只消费 verified outputs。
`ReplyToolExtraction` 增加来源字段。错误日志不复制工具正文。

- [x] **步骤 4：实现独立富结果 envelope 与类型**

增加 `NANOBOT_RICH_OUTPUT` envelope builder/parser 和 `RichTerminalOutput`。固定映射：

```text
ai_daily       → report_kind=ai_daily       → news-brief
group_analysis → report_kind=group_analysis → group-analysis-report
```

ai_daily/group_analysis 改用独立 builder，不再调用 `build_reply_tool_result()`。Bridge 只从
verified tool output 构造 `RichTerminalOutput`，并以该类型贯穿 model loop 和 settlement。

- [x] **步骤 5：删除所有裸字符串终结旁路**

移除或停用：

- `_extract_reply_contract_text(content)` 的匿名 tool 伪装；
- 初次和重试 response HTML 嗅探；
- `retry_marker_json_repair`；
- 普通 structured JSON 作为正常成功；
- 无 tool provenance 的 reply runtime cache 成功路径。

允许一次严格工具重试；重试仍无 verified reply/no_reply 或 typed rich result 时 suppress。

- [x] **步骤 6：运行绿灯与指标回归**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_reply_contract.py tests/test_bridge_integration.py tests/test_kt_framework.py tests/test_streaming_bridge.py tests/test_ai_daily_tool_and_sources.py tests/test_ai_daily_ingest.py tests/test_group_analysis_tool.py tests/test_reply_admin.py tests/test_prompt_trace_admin.py -v
```

确认 Final Action 计数按 verified unique call ID，QQbot/外部投递接口没有修改。

定向验证结果：`45 passed`。完整回归结果：`2897 passed, 6 skipped`，无失败。
自审额外覆盖畸形首个 result 也必须消费已声明 call ID，防止第二个结果重新关联。

- [x] **步骤 7：授权后的提交检查点（由最终聚合提交完成）**

建议提交信息：

```text
fix(回复契约): 绑定终结结果真实工具来源
```

---

### 安全前置任务 S3：结构化运行时元数据并隔离不可信角色

**文件：**

- 修改：`core/prompt_v2/context_adapters.py`
- 修改：`core/prompt_v2/compiler.py`
- 修改：`core/prompt_v2/audit.py`
- 修改：`core/prompt_v2/flow.py`
- 修改：`core/prompt_v2/task_templates.py`
- 修改：`nanobot_kt/bridge.py`
- 创建测试：`tests/test_prompt_runtime_metadata_security.py`
- 修改测试：`tests/test_prompt_v2.py`
- 修改测试：`tests/test_prompt_v2_audit_policy.py`
- 修改测试：`tests/test_bridge_runtime_context.py`
- 修改测试：`tests/test_model_router.py`
- 修改测试：`tests/test_prompt_v2_template_admin.py`

- [x] **步骤 1：编写标签逃逸和角色隔离红灯**

参数化所有动态字段，值包含换行、引号、`</runtime_context>`、
`</persona_reference>`、`<fake_system>`、`&`、U+2028、U+2029。断言目标形态：

- runtime context 恰好一个开/闭标签，body `json.loads()` 为 object；
- system messages 中没有 sender/session/trigger 等攻击原文；
- `<message_meta>` 只在最后 user event 出现一次且可 round-trip；
- Prompt 文本中没有字面攻击闭合标签；
- persona entity 不能闭合属性或 section；
- 超长 ID/name/aliases 被按契约限制。

strict audit 对多闭合、非 JSON、非 object、错误关键字段类型全部拒绝。

私聊 `group_id` 的归一化仍由原整改任务 5 单独负责；S3 不提前锁定该行为，避免打乱
既定依赖顺序。

- [x] **步骤 2：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_prompt_runtime_metadata_security.py -q
```

红灯证据：`12 failed`，分别命中标签逃逸、角色混入、属性逃逸、分类输入升权、字段
上限、多模态保序和 strict audit 缺失。

- [x] **步骤 3：实现稳定 JSON 编码 helper**

稳定排序、紧凑 JSON，序列化后强转义 `&/< />/U+2028/U+2029`，并在编码前应用字段
长度和 aliases 数量上限。helper 只接受可 JSON 化的标量/list/object，不回退到 raw
字符串拼接。

- [x] **步骤 4：拆分 system facts 与 user metadata**

`build_runtime_context()` 只输出稳定事实 JSON。新增 `build_message_meta()`，Compiler 将其
放在最后一条 user event 的 `<message_meta>`，再接 `<user_input>`；多模态输入用首个
text part 承载 metadata。

`build_persona_reference()` 使用同一结构编码，不把原始 `user_id` 放进未转义属性。

- [x] **步骤 5：扩展 strict audit**

解析 runtime/message metadata section，验证标签唯一、JSON object、必要字段和类型、
metadata 只位于最后 user message。错误文本只含 section/key，不含原始值。

- [x] **步骤 6：运行绿灯**

验证证据：

```text
S3 安全用例：12 passed
Prompt/Bridge/Classifier 关联回归：115 passed
API/KT 装配回归：176 passed
完整测试：2909 passed, 6 skipped, 0 failed
ruff：All checks passed
compileall：通过
git diff --check：通过
```

按用户明确范围，本阶段没有修改任何 Prompt 模板正文。现有模板中对旧 runtime 字段
位置的文字说明登记为已知 drift，留待后续 Prompt 正文专项处理。

- [x] **步骤 7：授权后的提交检查点（由最终聚合提交完成）**

建议提交信息：

```text
fix(提示词): 隔离并编码运行时动态元数据
```

---

### 安全前置任务 S4：建立关键 TaskContract 与可恢复失败语义

**文件：**

- 创建：`core/prompt_v2/task_contracts.py`
- 修改：`core/prompt_v2/task_templates.py`
- 修改：`core/prompt_v2/template_store.py`
- 修改：`core/prompt_v2/template_registry.py`
- 修改：`bootstrap/prompt_runtime.py`
- 修改：`clients/classifier_client.py`
- 修改：`core/legacy_adapter.py`
- 修改：`core/persona_preprocess.py`
- 创建测试：`tests/test_prompt_v2_task_contracts.py`
- 修改测试：`tests/test_prompt_v2_template_admin.py`
- 修改测试：`tests/test_prompt_v2_template_registry.py`
- 修改测试：`tests/test_prompt_runtime_bootstrap.py`
- 修改测试：`tests/test_model_router.py`
- 修改测试：`tests/test_timing_gate.py`
- 修改测试：`tests/test_evolution.py`
- 修改测试：`tests/test_kt_integration.py`

**只读核对，不改正文：**

- `prompts.v2.default/tasks/memory_extract.md`
- `data/prompts_v2/tasks/memory_extract.md`
- `prompts.v2.default/tasks/timing_gate.md`
- `data/prompts_v2/tasks/timing_gate.md`

- [x] **步骤 1：编写模板启用门禁红灯**

覆盖：缺 required placeholder、调用值缺失、非法 Runtime → 合法 default、Runtime 和
default 都非法 → code fallback/disabled、registry completeness、Admin 保存失败旧文件
字节不变、错误不含模板正文或变量值。

classifier/timing 使用唯一 payload marker，断言攻击原文在完整 messages 中恰好一次且
只位于 user role；反转当前“system 内包含 ping”的旧断言。

- [x] **步骤 2：编写 Memory 处理语义红灯**

参数化模型输出：

```text
空正文 / garbage / {} / 顶层 list / candidates 非 list → processed 保持 0
{"candidates": []} → 合法空结果，可 processed
合法 candidates + 状态机失败/DB rollback → processed 保持 0
合法 candidates + commit 成功 → processed
```

fallback prompt 必须包含 conversation 与 existing_memory 的真实数据，但这些数据不得
进入 system role。

- [x] **步骤 3：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_prompt_v2_task_contracts.py tests/test_prompt_v2_template_admin.py tests/test_model_router.py tests/test_timing_gate.py tests/test_evolution.py -v
```

红灯证据：首轮 `25 failed`，命中缺少契约 registry/selector、Admin 坏模板写入、payload
重复、Timing 兼容旁路/无重试、Memory 非法输出误消费；随后补充状态机 processing error、
persona 回写拒绝和 Outreach 模板兼容红灯。

- [x] **步骤 4：实现代码拥有的 TaskContract registry**

声明 required/payload variables、render mode、output contract、模板和输出失败策略。
首批至少 memory_extract、timing_gate、classifier_legacy；所有 live task 必须登记或显式
标为 `code_fallback_only`，completeness test 锁定。

- [x] **步骤 5：实现严格 task template 选择**

不要把严格校验塞进通用 `load_template()`，Admin 仍需读取坏模板以便修复。新增 live
选择函数，顺序为 Runtime → canonical default → code fallback；每层都通过同一契约。
无效 Runtime 不覆盖、不删除。

Admin create/save 写入前校验。bootstrap 只报告 active/fallback 状态；没有安全 fallback
的关键 task fail closed。

- [x] **步骤 6：消除 system/user 重复 payload**

对 `system_with_user_ref`，system placeholder 只渲染稳定代码 marker，真实 payload 只发
一次 user message。classifier route 不再同时提供两套会造成重复的 raw 值。

- [x] **步骤 7：修复 Memory 输出状态机**

严格解析 `memory_candidates_v1`；合法空与 contract error 使用不同类型。只有合法空或
状态机/DB 成功后才标 processed。网络、解析和持久化失败保持可重试。

- [x] **步骤 8：运行绿灯与启动回归**

运行同步骤 3，并增加：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_prompt_v2_template_registry.py tests/test_prompt_runtime_bootstrap.py -v
```

验证证据：

```text
TaskContract/Timing/Memory/Outreach 关联回归：409 passed
完整测试：2937 passed, 6 skipped, 0 failed
live task 契约完整性：9/9
当前 active task invalid_sources：0
ruff：All checks passed
compileall：通过
git diff --check：通过
```

按用户范围，本阶段没有修改任何 Prompt 模板正文。当前 canonical 与 Runtime 的关键
`memory_extract` / `timing_gate` 模板已经满足变量契约；本次新增的是防止未来坏 Runtime
静默启用的代码门禁。

已知后续架构项：`PersonaFact/Behavior`、`Persona/SystemPrompt` 与 `ChatLog.processed`
仍由三笔独立事务提交。本阶段已经保证模板/网络/解析/schema/embedding/persistence
失败不再消费日志，但晚段失败后的跨表原子回滚与多 worker claim 仍需统一 UoW/fenced
claim 设计，不能宣称已解决。

- [x] **步骤 9：授权后的提交检查点（由最终聚合提交完成）**

建议提交信息：

```text
fix(任务契约): 拒绝失效模板并保留失败输入
```

---

### 安全前置任务 S5：绑定 persona_update 当前用户并缩窄公开能力

**文件：**

- 修改：`creatures/nanobot/prompts/skills/persona_update/tool.py`
- 修改：`core/tool_schema_preview.py`
- 修改：`core/tool_registry.py`
- 创建测试：`tests/test_persona_update_tool.py`
- 修改测试：`tests/test_final_tools.py`
- 修改测试：`tests/test_tool_schema_config.py`

- [x] **步骤 1：编写授权红灯**

使用 in-memory SQLite 和 fake ToolContext，证明：

- runtime context user ID 是唯一 actor；
- 模型传不同 user_id 时在打开 DB/调用 LLM 前拒绝；
- context 缺失或 runtime user ID 为空时 fail closed；
- 拒绝路径 Persona/Fact/ChatLog 无变化；
- 相同 ID 或 schema 无 user_id 的正常刷新可以进入现有流程；
- `instructions` 不再出现在 executable/schema properties；
- 未实现的删除/重建请求不能返回“更新成功”。

- [x] **步骤 2：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_persona_update_tool.py tests/test_final_tools.py tests/test_tool_schema_config.py -v
```

- [x] **步骤 3：实现 actor 绑定**

设置 `needs_context=True`，从 `context.session.extra.nanobot_runtime_context.user_id` 取得
actor。模型参数最好移除；若保留兼容，只允许为空或完全相等。授权失败使用稳定错误，
不泄漏目标是否存在。

- [x] **步骤 4：缩窄 schema**

本阶段只保留“根据当前用户已持久化日志刷新画像”的真实能力。从 Tool class 和静态
schema 移除未执行的 `instructions`；tool registry 不再宣称删除/重建。Prompt usage
正文按用户要求不在本轮修改，并登记为已知 drift。

- [x] **步骤 5：运行绿灯与 Admin 隔离回归**

运行同步骤 2，并运行现有 Admin persona 测试，确认独立鉴权路径不受影响。

验收证据（2026-07-12）：

- 红灯：`8 failed, 17 passed`，失败均来自缺失 actor 绑定、未缩窄 schema/描述；
- 定向与 Admin 回归：`106 passed, 0 failed`；
- 全量回归：`2946 passed, 6 skipped, 0 failed`；
- ruff、compileall、`git diff --check` 通过；
- 暂存区为空，具体超级用户账号零命中，`cc2codex/` 与 `nanobot.db` 未被跟踪；
- Prompt usage 模板正文未修改，未实现的纠正/删除/重建能力仍登记为延期事项。

- [x] **步骤 6：授权后的提交检查点（由最终聚合提交完成）**

建议提交信息：

```text
fix(画像工具): 绑定当前用户并移除失效参数
```

---

### 安全前置任务 S6：贯穿 ai_daily 时间契约并建立 schema/执行器测试

**文件：**

- 创建：`core/tool_contracts/ai_daily.py`
- 修改：`creatures/nanobot/prompts/skills/news_search/tool.py`
- 修改：`creatures/nanobot/prompts/skills/news_search/runtime_cache.py`
- 修改：`creatures/nanobot/prompts/skills/news_search/news_daily/tool.py`
- 修改：`core/tool_schema_preview.py`
- 创建测试：`tests/test_tool_schema_executor_contracts.py`
- 修改测试：`tests/test_ai_daily_tool_and_sources.py`
- 修改测试：`tests/test_news_daily_pipeline.py`
- 修改测试：`tests/test_tools_package.py`

- [x] **步骤 1：编写参数行为红灯**

覆盖：

- today=北京时间当前自然日；
- latest=近 72 小时；
- week=近 7 天；
- custom=target_date 对应自然日；
- custom 缺日期、非法 ISO 日期、非法 enum 拒绝；
- 不同 freshness/target_date 的 cache key 不同；
- quality → daily fallback 不丢时间窗口；
- no_cache/refresh 只控制缓存；
- pipeline mock 捕获结构化请求，证明每个公开参数被消费。

- [x] **步骤 2：编写 schema/执行器结构一致性红灯**

比较静态 preview、Tool class 和程序化 request contract 的 properties/required/enum/
default/additionalProperties；忽略框架注入的 `run_in_background` 和 description overlay。
每个公开可选参数必须有行为测试，server-bound 参数必须明确标记且不得由模型提供。

- [x] **步骤 3：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_ai_daily_tool_and_sources.py tests/test_news_daily_pipeline.py tests/test_tools_package.py tests/test_tool_schema_executor_contracts.py -v
```

- [x] **步骤 4：实现 AiDailyRequest 单一解析契约**

统一规范化 query、max_results、freshness、target_date、no_cache/refresh。Tool class 和
preview schema 由同一 contract 生成，执行器只消费解析后的对象。

- [x] **步骤 5：贯穿 pipeline 和缓存**

显式传递时间窗口至收集/过滤和 fallback；缓存键包含 normalized query、freshness、
target_date、max_results、mode 和 pipeline version。不得只把日期拼进 query。

- [x] **步骤 6：处理 outreach_judge 边界**

本轮不改 `outreach_judge.md` 示例正文，也不把 `message|research` 宽松归一。把单值 enum
登记进 TaskContract/output schema；非法值继续 fail closed。provider constrained
decoding 与定向重试若当前公共 client 无可靠支持，登记 follow-up，不在本任务伪装完成。

- [x] **步骤 7：运行绿灯**

运行同步骤 3，并确认 ai_daily 富结果独立 envelope 回归通过。

验证记录：定向回归 `146 passed`，S6 关联回归 `309 passed`，独立只读复审无
Critical/Important，全量回归 `2993 passed, 6 skipped, 0 failed`。未注册的兼容
`NewsDailyTool` 仍返回裸 HTML，但生产桥只导出正式 `AiDailyTool`；登记为后续删除或
委托正式实现的非阻塞技术债。

- [x] **步骤 8：授权后的提交检查点（由最终聚合提交完成）**

建议提交信息：

```text
fix(日报工具): 贯穿时效参数与缓存契约
```

---

### 任务 1：显式透传代码鉴权事实

**文件：**

- 修改：`core/identity.py`
- 修改：`core/prompt_v2/schema.py`
- 修改：`core/prompt_v2/context_adapters.py`
- 修改：`nanobot_kt/prompt_runtime.py`
- 修改：`nanobot_kt/bridge.py`
- 修改测试：`tests/test_identity.py`
- 修改测试：`tests/test_prompt_v2.py`
- 修改测试：`tests/test_bridge_prompt_v2.py`

- [x] **步骤 1：编写显式事实链红灯测试**

在 `tests/test_identity.py` 增加覆盖：

```python
def test_build_identity_vars_uses_explicit_authorization_fact(monkeypatch):
    monkeypatch.setattr("core.identity.is_super_user_id", lambda _value: False)

    values = build_identity_vars(
        sender_id="placeholder-user",
        is_super_user=True,
    )

    assert values["is_super_user"] == "true"
```

在 `tests/test_prompt_v2.py` 构造 `PromptCompileRequest(is_super_user=True)`，断言：

```python
assert json.loads(runtime_context_body)["is_super_user"] is True
assert build_template_values(request)["is_super_user"] == "true"
```

再用 monkeypatch 返回一个完全不含 `{{ is_super_user }}` 的
`chat/identity_context` 模板，断言 runtime context 仍包含事实，identity section 不会
被代码强行改写。

在 `tests/test_bridge_prompt_v2.py` 捕获 `_build_prompt_runtime_input()`，断言
`metadata={"is_superuser": True}` 最终得到：

```python
assert prompt_input.is_super_user is True
```

- [x] **步骤 2：运行测试确认红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_identity.py tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py -v
```

预期：FAIL；`PromptCompileRequest`/`PromptRuntimeInput` 尚无显式字段，runtime context
也没有该行。记录首个与预期一致的失败原因后再改实现。

- [x] **步骤 3：扩展身份变量兼容接口**

把 `build_identity_vars()` 改为可接收显式覆盖：

```python
def build_identity_vars(
    *,
    sender_id: object = "",
    bot_name: object = "",
    bot_aliases: object = None,
    is_super_user: bool | None = None,
) -> dict[str, str]:
    ...
```

规则：

- `is_super_user is None`：保留现有按唯一环境配置判断的兼容行为；
- 显式 `True/False`：只使用传入事实，不再次读取配置；
- 输出仍为模板需要的字符串 `"true"/"false"`；
- 不记录 sender ID 或超级用户集合。

- [x] **步骤 4：把布尔字段贯穿 Prompt Runtime**

增加：

```python
PromptCompileRequest.is_super_user: bool = False
PromptRuntimeInput.is_super_user: bool = False
PromptRuntimeAssemblyContext.is_super_user: bool = False
```

Bridge 从已存在的 `meta.get("is_superuser")` 生成一次布尔值，并同时传给
runtime assembly 和 executor session 的脱敏 runtime context。

`build_prompt_runtime()` 显式填入 `PromptCompileRequest.is_super_user`。

`build_template_values()` 调用：

```python
build_identity_vars(..., is_super_user=bool(request.is_super_user))
```

`build_runtime_context()` 在稳定事实 JSON 中无条件写入：

```json
{"is_super_user":true}
```

不得从画像、历史、当前消息或身份模板反推布尔值。

- [x] **步骤 5：运行绿灯与兼容测试**

运行同步骤 2。额外确认：

- 未显式传字段的旧测试仍得到 `False`；
- `build_identity_vars()` 的旧直接调用仍按配置工作；
- 自定义 identity template 可省略展示字段，但 runtime fact 不消失；
- 没有修改任何 `identity_context.md`。

验证记录：计划内回归 `83 passed`，扩展 API/群入口回归 `176 passed`，独立只读复审
无 Critical/Important，全量回归 `3000 passed, 6 skipped, 0 failed`。Bridge 对非 bool
metadata 严格 fail closed；群聊 actor fact 可为 true，但 ToolPlan profile 保持 group。

- [x] **步骤 6：授权后的提交检查点（由最终聚合提交完成）**

只有用户明确说“提交”后：

```bash
git add core/identity.py core/prompt_v2/schema.py core/prompt_v2/context_adapters.py nanobot_kt/prompt_runtime.py nanobot_kt/bridge.py tests/test_identity.py tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py
git commit -m "fix(提示词): 显式透传超级用户运行时事实"
```

---

### 任务 2：强化核心 Prompt flow 与 strict audit

**文件：**

- 创建：`core/prompt_v2/flow_contract.py`
- 修改：`core/prompt_v2/flow.py`
- 修改：`core/prompt_v2/audit.py`
- 修改：`core/prompt_v2/compiler.py`
- 修改：`core/prompt_v2/preview.py`
- 修改：`app/prompt_runtime/preview_service.py`
- 修改：`bootstrap/prompt_runtime.py`
- 创建测试：`tests/test_prompt_v2_core_contract.py`
- 修改测试：`tests/test_prompt_v2_audit_policy.py`
- 修改测试：`tests/test_prompt_v2.py`
- 修改测试：`tests/test_prompt_v2_template_admin.py`
- 修改测试：`tests/test_prompt_runtime_bootstrap.py`

**只读核对，不自动改正文：**

- `prompts.v2.default/chat/flow.json`
- `data/prompts_v2/chat/flow.json`

- [x] **步骤 1：编写四分支核心契约红灯矩阵**

在 `tests/test_prompt_v2_core_contract.py` 参数化：

```python
cases = [
    ("qq", "group", {"base_contract", "qq_common_policy", "group_policy", "qq_group_policy", "runtime_context", "identity_context"}),
    ("qq", "private", {"base_contract", "qq_common_policy", "private_policy", "runtime_context", "identity_context"}),
    ("web", "group", {"base_contract", "group_policy", "runtime_context", "identity_context"}),
    ("web", "private", {"base_contract", "private_policy", "runtime_context", "identity_context"}),
]
```

每个分支用 `compile_prompt_plan(request, strict_audit=True)`，断言所需节点恰好一次、
不适用节点为零、状态为 `emitted`。

复制一个正常 plan 后逐项篡改并调用 `audit_prompt_plan()`：

- 删除 `base_contract`；
- 删除/重复/改名 `runtime_context`；
- 把 `identity_context` 改成 runtime node；
- 把 identity template key 改成其他模板；
- 在 Web 分支加入 QQ policy；
- QQ 群聊缺少 `qq_group_policy`；
- message index 越界、重复或指向错误 role；
- `current_user_event` 不指向最后一条 user message；
- `runtime_context` 排在 branch 之前；
- `identity_context` 排在 runtime context 之前；
- 用 `origin="fallback"` 冒充必需 flow 节点。

每个用例都断言 `audit.ok is False`，并校验稳定错误码或可定位的错误文本。

- [x] **步骤 2：运行测试确认红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_prompt_v2_core_contract.py tests/test_prompt_v2_audit_policy.py tests/test_prompt_v2.py -v
```

预期：至少删除 base/runtime/identity 的用例错误地通过，证明现有 audit 缺口。

- [x] **步骤 3：建立单一 flow contract 定义**

在 `core/prompt_v2/flow_contract.py` 定义不可变 section contract，至少包含：

```python
@dataclass(frozen=True)
class ReservedSectionContract:
    node_id: str
    node_type: Literal["template", "runtime"]
    template_key: str = ""
    runtime_key: str = ""
    expected_role: str = "system"
    platforms: frozenset[str] = frozenset()
    chat_types: frozenset[str] = frozenset()
    required: bool = True
```

提供：

```python
reserved_contract_by_node_id()
required_contracts(platform: str, chat_type: str)
forbidden_conditional_contracts(platform: str, chat_type: str)
```

收录设计文档中的 base、QQ common、branch、QQ group、runtime context、identity、
persona、runtime tool 和 current user。以后 `session_guidance` 在这个单一定义中扩展，
避免 `flow.py` 与 `audit.py` 再次漂移。

- [x] **步骤 4：让 flow validation 和 Python fallback 使用同一契约**

- `_validate_reserved_node_identity()` 从公共 contract 读取期望值；
- 保留节点不得通过换 ID、换 key 或换 type 逃逸；
- `DEFAULT_FLOW` 补齐当前 JSON 已存在的 `qq_common_policy` 和
  `qq_group_policy` 及条件边，确保文件丢失时 Python fallback 仍满足同一契约；
- 不改动 identity template 正文；
- 默认和 Runtime `flow.json` 若已经与 canonical flow 一致，只做测试，不制造无意义
  diff。

- [x] **步骤 5：实现严格审计的结构、角色、索引和顺序校验**

`audit_prompt_plan()` 使用公共 contract：

- 仅 `origin="flow"` 可满足必需节点；
- 校验当前分支 required/forbidden 条件节点数量；
- 校验 status；
- 校验 `message_indexes` 类型、范围、唯一性和 role；
- 校验 current user 是最后一个 message；
- 按 flow section 顺序和 message index 双重检查核心相对顺序；
- 错误信息不包含 Prompt 正文。

`compile_prompt_plan(strict_audit=True)` 继续抛 `PromptAuditError`；live runtime 保持
fail closed。

把 `compile_prompt_plan()` 的默认参数改为 `strict_audit=True`。只有专门诊断旧 flow
并明确验证 warnings 的调用方才允许显式传 `False`。`core/prompt_v2/preview.py` 和
`app/prompt_runtime/preview_service.py` 仍要显式传 `True`，避免以后默认值变化造成
回归；preview 捕获 `PromptAuditError/PromptFlowError` 并返回 400，不得返回
`200 + warnings`。启动 bootstrap 预检当前有效 flow，非法自定义 flow 在启动阶段
fail closed。

- [x] **步骤 6：运行绿灯和真实 flow 文件校验**

运行同步骤 2，并增加：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_prompt_v2_template_registry.py tests/test_prompt_runtime_bootstrap.py -v
```

预期全部 PASS。分别临时指向 default/runtime flow 做只读编译，四个分支都通过严格
审计。

实施结果（2026-07-13）：

- 初始核心红灯矩阵先后稳定复现缺失核心节点、`origin/status` 兼容默认、非对象
  section/message、非核心 role、未知平台、节点类型旁路和未归属 message 等缺口；
- `flow_contract.py` 统一维护保留节点与 `RUNTIME_NODE_KEYS`，`flow.py` 和
  `audit.py` 不再各自维护平行定义；
- strict audit 现在覆盖四分支 required/forbidden、section 形态、origin/status、
  message index 类型/范围/唯一归属、全节点 role、完整 flow/message 顺序、runtime
  facts 与 current user 尾事件；QQ/Web 之外的平台在请求和 flow 条件边界均显式拒绝；
- compiler 默认 strict，core/Admin preview 显式 strict 且合同错误返回 400，bootstrap
  对非法 active flow 启动失败；
- 核心矩阵：`41 passed`；任务 2 定向矩阵：`131 passed`；独立只读复审结论：
  `0 Critical / 0 Important / GO`；
- 全量：`3045 passed, 6 skipped, 0 failed`；ruff、compileall、`git diff --check`
  均通过；default/runtime 两套 flow 的 QQ/Web × 群聊/私聊四分支均 strict 编译通过；
- 两份 `flow.json` 字节一致且无差异；Prompt Markdown、QQ renderer、敏感账号、
  `nanobot.db`、`cc2codex/` 与暂存区边界检查均通过；未执行暂存或提交。

- [x] **步骤 7：授权后的提交检查点（由最终聚合提交完成）**

```bash
git add core/prompt_v2/flow_contract.py core/prompt_v2/flow.py core/prompt_v2/audit.py core/prompt_v2/compiler.py core/prompt_v2/preview.py app/prompt_runtime/preview_service.py bootstrap/prompt_runtime.py tests/test_prompt_v2_core_contract.py tests/test_prompt_v2_audit_policy.py tests/test_prompt_v2.py tests/test_prompt_v2_template_admin.py tests/test_prompt_runtime_bootstrap.py
git commit -m "fix(提示词): 强化核心编排严格审计"
```

仅在实际变更 flow JSON 时才按路径追加暂存；不得为了提交而改写未变化文件。

---

### 任务 3：固化请求级 wire tool schema 并消除重复 overlay

**文件：**

- 修改：`core/prompt_v2/tool_templates.py`
- 修改：`core/tool_schema_preview.py`
- 修改：`core/tool_plan.py`
- 修改：`core/final_tools.py`
- 修改：`nanobot_kt/tool_runtime.py`
- 修改测试：`tests/test_prompt_v2_tool_template_integration.py`
- 修改测试：`tests/test_tool_schema_config.py`
- 修改测试：`tests/test_tool_plan.py`
- 修改测试：`tests/test_final_tools.py`

- [x] **步骤 1：编写重复 overlay 与 wire 形态红灯测试**

增加以下证明：

```python
schema_once = overlay_tool_schema_description(base_schema)
schema_twice = overlay_tool_schema_description(schema_once)
assert schema_twice == schema_once
assert schema_twice["function"]["description"].count("[V2ToolTemplate:") == 1
```

模板 hash/body 改变后再次 overlay，断言旧 marker 消失、新 marker 恰好一次、人工
description 前缀保持。

构造带 `category/risk_level/label/source` 的 schema，生成 ToolPlan 后断言：

```python
assert set(plan.sent_tool_schemas[0]) == {"type", "function"}
assert plan.sent_tool_schemas[0]["function"]["name"] == "reply"
```

调用 `_tool_plan_native_schemas(plan)` 并逐个 `to_api_format()`，断言与
`list(plan.sent_tool_schemas)` 完全相等。

对 `filter_payload_tools()` 传已 overlay 的 schema，断言只裁剪、不改变 description、
不读取数据库/模板。可 monkeypatch overlay 为抛错，证明出口不再调用它。

- [x] **步骤 2：运行测试确认红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_prompt_v2_tool_template_integration.py tests/test_tool_schema_config.py tests/test_tool_plan.py tests/test_final_tools.py -v
```

预期：二次 overlay 产生两个 marker；最终过滤阶段仍调用 overlay；ToolPlan schema
仍含不会发送的管理元数据。

- [x] **步骤 3：让 overlay 可替换且幂等**

在 `tool_templates.py` 增加内部 helper，识别 description 尾部由 Nanobot 生成的
`[V2ToolTemplate:...]` 区块。处理顺序：

```text
保留人工 base description
→ 移除全部旧生成区块
→ 追加一个当前 marker/body
```

不要靠“当前完整 marker 已存在就返回”作为唯一逻辑，否则模板 hash 更新后仍会叠加。
不得删除用户正文中与生成区块无关的普通 Markdown。

- [x] **步骤 4：在 ToolPlan 构造阶段规范化 wire schema**

增加纯函数（放在 `core/tool_plan.py` 或同模块私有 helper）：

```python
def normalize_wire_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": ...,
            "description": ...,
            "parameters": deepcopy(...),
        },
    }
```

要求：

- name 非空、parameters 为对象；非法 schema 让 ToolPlan 构造失败；
- `sent_tool_schemas` 只保存 wire 形态；
- 构造时深拷贝，后续消费者也返回/传递深拷贝，调用方原对象变化不影响计划；
- ToolPlan SHA-256 基于规范化后的 wire schemas；
- Admin 的 effective/editable schema 仍可保留管理元数据，不与 wire 快照混用。

- [x] **步骤 5：把最终过滤器收窄为纯裁剪**

删除 `filter_payload_tools()` 中的 overlay import 和调用。只按
`sent_tool_names/allowed` 保留 schema；无工具时删除 `tool_choice`；保留输入 payload
不变性。

`filter_sdk_kwargs()` 只转调纯裁剪。不得在 SDK trace 中重新读取模板。

- [x] **步骤 6：运行绿灯和 marker 计数回归**

运行同步骤 2。再对完整 enabled tool set 构造 ToolPlan，遍历每个 description：

```python
assert description.count("[V2ToolTemplate:") <= 1
```

有模板的工具必须等于 1；没有模板的工具等于 0。记录 schema 总字符数，确认二次出口
处理前后不再增长。

实施结果（2026-07-13）：

- 初始定向矩阵稳定复现 `7 failed / 36 passed`，分别命中重复 marker、旧模板正文残留、
  final filter 再次 overlay、管理元数据进入 wire、浅拷贝污染、非法 schema 未拒绝和
  ToolPlan SHA 被管理字段污染；
- overlay 现在只识别并替换 description 尾部的 Nanobot 生成区块，人工说明和普通
  Markdown 保留；模板正文/hash 更新后旧区块消失，当前 marker 恰好一个；
- ToolPlan 构造时把 schema 收窄为唯一 wire 形态，拒绝非法 type/function/name/
  parameters，深拷贝保存私有快照并向消费者返回防御性副本；SHA 只受 wire
  description/parameters 等真实字段影响；
- KT `ToolSchema.to_api_format()` 与 `sent_tool_schemas` 逐字节一致；final filter 不再
  读取模板或改写 description，只做深拷贝和 allowed 裁剪；
- 计划定向矩阵：`48 passed`；Bridge/KT/安全关联回归：`220 passed`；独立只读复审：
  `0 Critical / 0 Important / GO`；
- 完整 private/full 工具集为 20 个，marker 最大值为 1；final filter 前后 schema
  均为 20,276 字符且逐字节相等；
- 全量：`3056 passed, 6 skipped, 0 failed`；ruff、compileall、`git diff --check`
  均通过；Prompt/QQ、敏感账号、`nanobot.db`、`cc2codex/` 和暂存区边界检查通过；
  未执行暂存或提交。

- [x] **步骤 7：授权后的提交检查点（由最终聚合提交完成）**

```bash
git add core/prompt_v2/tool_templates.py core/tool_schema_preview.py core/tool_plan.py core/final_tools.py nanobot_kt/tool_runtime.py tests/test_prompt_v2_tool_template_integration.py tests/test_tool_schema_config.py tests/test_tool_plan.py tests/test_final_tools.py
git commit -m "fix(工具契约): 固化请求级工具结构并去重说明"
```

---

### 任务 4：统一最终 Prompt hash 与 token 分项统计

**文件：**

- 创建：`core/prompt_v2/request_metrics.py`
- 修改：`core/prompt_v2/schema.py`
- 修改：`core/prompt_v2/compiler.py`
- 修改：`nanobot_kt/prompt_runtime.py`
- 修改：`core/tracing.py`
- 修改：`core/llm_request_linter.py`
- 修改：`app/prompt_runtime/preview_service.py`
- 修改测试：`tests/test_prompt_v2.py`
- 修改测试：`tests/test_bridge_prompt_v2.py`
- 修改测试：`tests/test_prompt_v2_template_admin.py`
- 修改测试：`tests/test_llm_request_linter.py`
- 修改测试：`tests/test_llm_request_tracing.py`

- [x] **步骤 1：编写 metrics 红灯测试**

构造一条固定 messages + 两个 wire tools 的 plan，断言：

```python
assert plan.message_token_estimate > 0
assert plan.tool_schema_token_estimate > 0
assert plan.token_estimate == (
    plan.message_token_estimate + plan.tool_schema_token_estimate
)
assert plan.prompt_sha256 == sha256_text(stable_json(plan.request_json))
```

改变 tool description 或 parameters，messages 不变，断言 tool 分项和 hash 改变。
改变管理元数据但 wire schema 不变，断言 hash 不变。

空 tools 时：

```python
assert plan.tool_schema_token_estimate == 0
assert plan.request_json["tools"] == []
```

Admin preview 响应必须同时包含三个 token 字段。

SDK trace 测试捕获初始请求最终 `completions.create(**kwargs)`，取出规范化后的
`messages/tools` 重算 hash，与 PromptPlan 一致。该测试不得比较 model、temperature、
stream 等非 Prompt 字段。另为工具循环后的第二笔请求构造不同 messages，断言
`request_lint_json.payload_metrics` 使用该笔真实 payload 独立计算，而不是沿用初始
PromptPlan hash。

- [x] **步骤 2：运行测试确认红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py tests/test_prompt_v2_template_admin.py tests/test_llm_request_linter.py tests/test_llm_request_tracing.py -v
```

预期首轮因缺少分项字段和 outbound payload metrics 失败。

- [x] **步骤 3：实现统一 request metrics**

`core/prompt_v2/request_metrics.py` 提供：

```python
@dataclass(frozen=True)
class PromptRequestMetrics:
    message_token_estimate: int
    tool_schema_token_estimate: int
    token_estimate: int
    prompt_sha256: str


def calculate_request_metrics(
    *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> PromptRequestMetrics:
    ...
```

使用项目现有 `stable_json()`、`sha256_text()` 和 token estimator。messages/tools 先
深拷贝成最终 envelope；工具为空时 tool 分项固定为 0。

- [x] **步骤 4：扩展 PromptPlan 并替换 Compiler 旧统计**

在 `PromptPlan` 末尾增加带默认值的兼容字段，避免破坏测试中的关键字构造：

```python
message_token_estimate: int = 0
tool_schema_token_estimate: int = 0
```

现有 `token_estimate` 保留，但 Compiler 必须把它设置为总量。删除“只累加 message
content”的旧算法，统一调用 `calculate_request_metrics()`。

`debug` 增加：

```python
{
    "message_token_estimate": ...,
    "tool_schema_token_estimate": ...,
    "token_estimate": ...,
}
```

非严格 audit 返回的新 PromptPlan 也必须复制分项，不能在错误分支丢失。

- [x] **步骤 5：同步 trace 与 Admin preview**

- `PromptTracer.record_render(token_estimate=...)` 继续写总量；
- `variables_json` 使用已有 debug 保存分项，不新增数据库 migration；
- `lint_llm_request()` 对已经清理、裁剪的实际 payload 调用同一个 metrics helper，
  只把 hash 和三个计数写入 `request_lint_json["payload_metrics"]`；
- Admin effective preview 从 `plan.to_dict()` 或显式 serializer 返回分项；
- `rendered_content` 使用与 metrics 相同的 `plan.request_json`；
- 不把完整工具 schema 额外复制进普通日志。

- [x] **步骤 6：证明编译后 envelope 不再改变**

组合任务 3 的纯过滤器和 SDK tracer：

- ToolPlan wire schema；
- PromptCompileRequest.tool_schemas；
- PromptPlan.request_json.tools；
- KT ToolSchema round-trip；
- SDK 最终 kwargs.tools；

五者必须完全相等。messages 在 Nanobot 清理边界后也必须相等。若 provider 专有层加入
cache marker，只在 provider 测试中单独断言，不把它混进 Prompt Runtime hash。

- [x] **步骤 7：运行绿灯**

运行同步骤 2，预期全部 PASS。再运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_prompt_v2_tool_template_integration.py tests/test_final_tools.py -v
```

- [x] **步骤 8：授权后的提交检查点（由最终聚合提交完成）**

```bash
git add core/prompt_v2/request_metrics.py core/prompt_v2/schema.py core/prompt_v2/compiler.py nanobot_kt/prompt_runtime.py core/tracing.py core/llm_request_linter.py app/prompt_runtime/preview_service.py tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py tests/test_prompt_v2_template_admin.py tests/test_llm_request_linter.py tests/test_llm_request_tracing.py
git commit -m "fix(提示词): 对齐最终请求指纹与令牌统计"
```

---

### 任务 5：修复私聊 group_id 污染

**文件：**

- 修改：`nanobot_kt/bridge.py`
- 修改：`core/prompt_v2/context_adapters.py`
- 修改测试：`tests/test_bridge_prompt_v2.py`
- 修改测试：`tests/test_prompt_v2.py`
- 修改测试：`tests/test_tool_plan.py`
- 修改测试：`tests/test_final_tools.py`

- [x] **步骤 1：编写私聊双层清空红灯测试**

Bridge 测试使用：

```python
session_id = "private_placeholder"
metadata = {"is_group": False, "group_id": "private_placeholder"}
```

捕获以下调用，全部断言 `group_id == ""`：

- `build_tool_plan()`；
- `record_runtime_tool_decision()`；
- `PromptRuntimeAssemblyContext`；
- `PromptRuntimeInput`；
- executor session `nanobot_runtime_context`。

Prompt adapter 单元测试直接构造错误的：

```python
PromptCompileRequest(chat_type="private", group_id="should-not-leak")
```

断言 `build_template_values()["group_id"] == ""`，且 `<runtime_context>` JSON 不含
非空 `group_id`。

用 in-memory SQLite 添加 group scope ToolOverride，解析私聊工具计划，断言该覆盖不会
生效；群聊对照仍会生效。

- [x] **步骤 2：运行测试确认红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_bridge_prompt_v2.py tests/test_prompt_v2.py tests/test_tool_plan.py tests/test_final_tools.py -v
```

预期：Bridge 仍把私聊 session 作为 group ID，adapter 也保留误传值。

- [x] **步骤 3：实现 Bridge chat-type aware group ID**

使用显式分支：

```python
if is_group:
    group_id = str(meta.get("group_id") or "").strip()
    if not group_id and str(session_id).startswith("group_"):
        group_id = str(session_id)[len("group_"):]
else:
    group_id = ""
```

不要修改运行时 `session_id`。不要让 `private_superuser` 进入 canonical chat type；它
只用于工具默认策略。

- [x] **步骤 4：实现 Prompt adapter 防御性清空**

`_request_group_id()` 首先判断 normalized chat type：

```python
if request.normalized_chat_type != "group":
    return ""
```

群聊继续支持显式 group ID 和 `group_` session 回退。任何模板变量和 runtime context
都复用这个 helper。

- [x] **步骤 5：运行绿灯和群聊兼容测试**

运行同步骤 2。再运行已有群聊工具覆盖、群聊 Prompt 和 group runtime ID 测试，证明
群聊行为不变。

- [x] **步骤 6：授权后的提交检查点（由最终聚合提交完成）**

```bash
git add nanobot_kt/bridge.py core/prompt_v2/context_adapters.py tests/test_bridge_prompt_v2.py tests/test_prompt_v2.py tests/test_tool_plan.py tests/test_final_tools.py
git commit -m "fix(私聊): 清除伪群号及群级工具覆盖"
```

---

### 任务 6：拆分超级用户权限上限与请求级工具相关性

**文件：**

- 修改：`core/private_timing.py`
- 修改：`api/chat_runtime_facade.py`
- 修改测试：`tests/test_private_timing.py`
- 修改测试：`tests/test_api_chat_pre_bridge_decision_split.py`
- 修改测试：`tests/test_api_chat_runtime_route_context_split.py`
- 修改测试：`tests/test_api_chat_runtime_facade_split.py`
- 修改测试：`tests/test_tool_plan.py`

- [x] **步骤 1：编写 intent/preset 红灯矩阵**

至少覆盖：

```python
cases = [
    # 简单短问句不能因超级用户身份自动 full
    ("我是不是超级用户?", True, "lightweight"),
    ("这件事靠谱吗?", True, "lightweight"),
    # 现有语义 probe 仍最小化
    ("你是谁?", True, "none"),
    ("你能做什么?", True, "none"),
    # 明确任务才允许超级用户 full
    ("请审查这段代码并给出修复方案", True, "full"),
    ("为什么这个 Traceback 会出现", True, "full"),
]
```

具体 effort/intent 名称按现有公共契约断言，核心必须锁定 runtime preset。

增加一条非超级用户对照，证明本改动不把普通用户权限提升到 full。

增加分类结果缺失对照：私聊 `private_decision=None` 得到 `lightweight`，群聊同样没有
私聊判定对象时仍得到 `full`。

构造上述简单超级用户请求的 ToolPlan，断言：

```python
assert not {"bash", "edit", "write"} & plan.sent_tool_names
assert {"reply", "no_reply"} <= plan.sent_tool_names
```

明确任务对照在配置允许时可包含 full 工具，证明不是永久禁用。

- [x] **步骤 2：运行测试确认红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_private_timing.py tests/test_api_chat_pre_bridge_decision_split.py tests/test_api_chat_runtime_route_context_split.py tests/test_tool_plan.py -v
```

预期：所有超级用户消息当前至少为 `full`，简单问句用例失败。

- [x] **步骤 3：调整语义判定顺序**

- 从 `_TASK_PATTERNS` 删除独立的句尾问号正则；
- identity/capability/bot/personal/missing-material 等现有语义 shortcut 在超级用户分支
  之前执行；
- `_looks_task_request()` 只依据已有语义任务模式，不因标点单独成立；
- 只有 `_looks_task_request()` 已确认的任务，超级用户才返回 serious/full；
- 其余超级用户普通问题返回 short/lightweight；
- 不新增只匹配测试原句的超级用户关键词列表；
- 保留现有 `reply/no_reply` force-enabled 契约。

将代码组织为“先判本轮需要，再应用权限上限”，注释明确
`is_superuser != full every turn`。

- [x] **步骤 4：验证路由 metadata 与 Bridge 使用同一 preset**

API split tests 捕获：

- `PrivateDecision.runtime_preset`；
- 传给 Bridge metadata 的 `runtime_preset`；
- 最终 `build_tool_plan(runtime_preset=...)`。

三者必须一致，避免路由算 lightweight、Bridge 又按超级用户恢复 full。

`api/chat_runtime_facade.py` 的缺省分支按 chat type 区分：

```python
runtime_preset = "full" if runtime_input.is_group else "lightweight"
```

不得因为私聊分类器异常或调用方漏传 decision 自动扩大权限。

- [x] **步骤 5：运行绿灯与研究预设回归**

运行同步骤 2，并额外运行 runtime tool/service 和 proactive research 相关测试，确认
`research` 固定上限不受影响。

- [x] **步骤 6：授权后的提交检查点（由最终聚合提交完成）**

```bash
git add core/private_timing.py api/chat_runtime_facade.py tests/test_private_timing.py tests/test_api_chat_pre_bridge_decision_split.py tests/test_api_chat_runtime_route_context_split.py tests/test_api_chat_runtime_facade_split.py tests/test_tool_plan.py
git commit -m "fix(工具策略): 按请求意图收敛超级用户工具集"
```

---

### 任务 7：专项集成回归与 Session Guidance 兼容门

**文件：**

- 创建：`tests/test_prompt_runtime_request_contract.py`
- 修改：`nanobot_kt/kt_adapter.py`
- 修改：`nanobot_kt/bridge.py`
- 修改：`nanobot_kt/prompt_runtime.py`
- 修改：`tests/test_reply_contract.py`
- 修改：`tests/test_bridge_integration.py`
- 修改：`tests/test_python_sandbox_security.py`
- 修改：`tests/test_prompt_v2_task_contracts.py`
- 修改：`tests/test_persona_update_tool.py`
- 修改：`tests/test_tool_schema_executor_contracts.py`
- 修改：`tests/test_prompt_v2_template_admin.py`
- 修改：`tests/test_bridge_prompt_v2.py`
- 修改：`docs/superpowers/specs/2026-07-13-prompt-runtime-contract-remediation-design.md`（仅当实现证明设计存在事实错误）

- [x] **步骤 1：捕获一轮最终 SDK 请求**

使用 fake OpenAI-compatible client 和 in-memory SQLite，模拟超级用户私聊发送一个
不需要外部工具的身份短问句。不要使用任何真实账号、真实 URL 或真实密钥。

捕获 PromptPlan、ToolPlan 和最终 SDK kwargs，断言：

```python
assert json.loads(runtime_context_body)["is_super_user"] is True
assert not json.loads(runtime_context_body).get("group_id")
assert all(desc.count("[V2ToolTemplate:") <= 1 for desc in descriptions)
assert final_tools == list(tool_plan.sent_tool_schemas)
assert final_tools == prompt_plan.request_json["tools"]
assert recompute_sha(prompt_plan.request_json) == prompt_plan.prompt_sha256
assert prompt_plan.token_estimate == (
    prompt_plan.message_token_estimate + prompt_plan.tool_schema_token_estimate
)
assert not {"bash", "edit", "write"} & final_tool_names
assert "python_sandbox" not in final_tool_names
```

identity template monkeypatch 为不含任何超级用户字段的普通正文，仍需通过上述事实
断言。模型调用次数恰好一次。

同一集成 fixture 还要使用完整 assistant/tool call pair，证明：

- reply/no_reply 的 tool name、call ID 和 declaration 可追溯；
- python_sandbox marker、普通 assistant HTML/JSON 都不会进入成功 settlement；
- verified ai_daily/group_analysis 才能产生 typed rich terminal output；
- runtime facts JSON 可解析，不可信 message metadata 只在最后 user event；
- classifier/timing payload 在完整请求中只出现一次且 role=user。

- [x] **步骤 2：增加 strict audit fail-closed 集成测试**

篡改 runtime flow，依次缺少 base/runtime/identity，调用 live
`build_prompt_runtime()`：

- 抛 `PromptRuntimeAuditFailure`；
- fake SDK/model 调用次数为 0；
- meta 只记录 audit issue，不复制 Prompt 正文；
- 不降级到旧 prompt 或未经审计的全量工具请求。

- [x] **步骤 3：增加 Admin preview/live 一致性对照**

对同一个 platform/chat type、用户输入、历史和 ToolPlan，比较 effective preview 与
live Prompt Runtime：

- `messages`；
- `tools`；
- `section_hashes`；
- `prompt_sha256`；
- 三个 token 字段。

两者都必须显式使用 `strict_audit=True`，且 preview 不调用任何模型、不写业务配置。
无模型依赖时必须完全一致；群记忆依赖 reranker 时，preview 既不得调用模型，也不得
读取模型派生缓存，必须显式报告降级，不能用不完整 envelope 冒充 live 精确结果。

- [x] **步骤 4：运行专项回归集**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_python_sandbox_security.py tests/test_sql_analysis_security.py tests/test_reply_contract.py tests/test_bridge_integration.py tests/test_prompt_v2_task_contracts.py tests/test_persona_update_tool.py tests/test_tool_schema_executor_contracts.py tests/test_prompt_runtime_request_contract.py tests/test_identity.py tests/test_private_timing.py tests/test_prompt_v2.py tests/test_prompt_v2_core_contract.py tests/test_prompt_v2_audit_policy.py tests/test_prompt_v2_tool_template_integration.py tests/test_tool_schema_config.py tests/test_tool_plan.py tests/test_final_tools.py tests/test_bridge_prompt_v2.py tests/test_prompt_v2_template_admin.py -v
```

预期：0 failures。

- [x] **步骤 5：运行相关恢复与工具契约回归**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/test_group_ingress_recovery.py tests/test_private_chat_route_recovery.py tests/test_tool_plan.py tests/test_final_tools.py tests/test_research_tool_plan.py tests/test_proactive_research.py -v
```

不得静默跳过对应子系统。

- [x] **步骤 6：检查 Prompt 模板同步边界**

逐项核对：

- `prompts.v2.default/chat/flow.json` 与 `data/prompts_v2/chat/flow.json` 的结构契约；
- 当前 active memory/timing task 的合同状态；不把本地合法模板误报为正文缺失；
- `core/prompt_v2/variables.py` 仍声明 `is_super_user`；
- `core/prompt_v2/template_registry.py` 不会覆盖现有自定义 identity body；
- 没有修改任何 `identity_context.md`；
- 工具 usage 模板正文没有因去重被误删；
- Prompt 模板正文无差异；已知 usage drift 只登记、不在本轮偷偷改写；
- 没有修改 QQbot 路径。

- [x] **步骤 7：执行全量验证（含基线例外记录）**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/ -v
npm --prefix webui run lint
npm --prefix webui run build
python -m compileall core app api nanobot_kt bootstrap scripts
git diff --check
git status --short
```

要求：pytest 0 failures、ESLint 0 errors、Vite build exit 0、compileall exit 0、
`git diff --check` 无输出。人工确认 `package-lock.json` 等用户原有差异完整保留且未
被暂存。

执行记录：

- 首轮全量 pytest：`3098 passed, 6 skipped`；复审整改后最终全量 pytest：
  `3101 passed, 6 skipped`。
- 任务 7 最终请求合同：`10 passed`；关联矩阵：`277 passed`；KT 恢复与 Conversation：`37 passed`。
- 专项安全与 Prompt Runtime 合同：`509 passed`；恢复与研究工具链：`220 passed`。
- Vite build、compileall、任务范围 Ruff、`git diff --check` 均通过。
- `npm --prefix webui run lint` 命中仓库 HEAD 已存在的 `9 errors / 5 warnings`；`git diff -- webui`
  为空，任务 7 没有前端差异，因此不在本任务混入无关 React effect 重构。
- 全仓 Ruff 命中既有 `.Codex/skills/ui-ux-pro-max`、`tests/test_chat_idempotency.py` 和
  `tests/test_kt_framework.py` 共 `16` 项；任务 7 的四个 Python 文件 Ruff 为零错误。
- 暂存区为空；敏感账号零命中；`nanobot.db`、`cc2codex/` 未跟踪；Prompt Markdown、
  QQ 路径和 KT 子模块均无差异。

- [x] **步骤 8：完成前审查**

- 使用 `chinese-code-review` 自审实际 diff；
- 使用 `requesting-code-review` 复核实现与两份设计规格；
- 使用 `verification-before-completion` 重跑能支撑最终声明的命令；
- 只有这一门通过，才可开始 `.codex/plans/session-guidance.md`。

独立只读复审在修复 Conversation 元数据累加、emergency-drop 实例替换后守卫失效、
`PromptFlowError`/非法 JSON 未归一三类问题后给出 `GO`，未发现新增 Critical 或 Important。

- [x] **步骤 9：授权后的最终提交检查点（由最终聚合提交完成）**

只有用户明确说“提交”后：

```bash
git add nanobot_kt/kt_adapter.py nanobot_kt/bridge.py nanobot_kt/prompt_runtime.py tests/test_prompt_runtime_request_contract.py .codex/plans/prompt-runtime-contract-remediation.md
git commit -m "fix(提示词): 锁定最终请求运行时契约"
```

若设计文档因事实错误需要修正，先向用户说明原因，再按精确路径加入；没有实际差异
时禁止空提交。

---

## 最终验收清单

- [x] `python_sandbox` 在所有 chat type/preset/override 下硬禁用，底层和 legacy 入口也不执行代码。
- [x] 底层只读 SQL 拒绝写 CTE、ATTACH、危险 PRAGMA、extension 和多语句，不泄漏 DB 路径。
- [x] Final Action 只统计并接受 verified reply/no_reply call pair。
- [x] 普通工具 marker/HTML 和 assistant JSON/HTML 无法终结。
- [x] ai_daily/group_analysis 使用独立富结果 envelope 和 typed settlement。
- [x] runtime facts 为可解析、强转义 JSON；不可信 metadata 只位于最后 user event。
- [x] persona reference 实体值不能逃逸 section。
- [x] 关键 task 缺 required placeholder/value 时不能启用，坏 Runtime 不被自动覆盖。
- [x] classifier/timing payload 只进入 user role 一次。
- [x] Memory 契约错误保持日志未处理，合法空和成功提交才消费。
- [x] persona_update target 绑定当前 actor，缺上下文/跨用户请求无副作用。
- [x] persona_update schema 不再暴露未执行的 instructions。
- [x] ai_daily freshness/target_date 贯穿 pipeline 与缓存键。
- [x] schema/执行器结构与每个公开参数的行为测试通过。
- [x] `is_super_user` 是从路由/Bridge 显式透传的代码事实。
- [x] 自定义 identity template 不引用变量时，runtime context 仍输出该事实。
- [x] base、平台、branch、runtime、identity 和 current user 受 strict audit 保护。
- [x] default/runtime/Python fallback flow 满足同一核心契约。
- [x] 工具模板说明 overlay 幂等，真实请求中每个 marker 最多一次。
- [x] ToolPlan 只保存真实 wire schema，不含 Admin 管理元数据。
- [x] 出口过滤只裁剪，不重新读取模板或改写 description。
- [x] PromptPlan、KT round-trip 和 SDK kwargs 的 tools 完全一致。
- [x] `prompt_sha256` 可从最终 messages/tools 独立重算。
- [x] token 总量包含 messages 和 tools，并提供两个分项。
- [x] 私聊 runtime context 不含 group ID，也不命中 group ToolOverride。
- [x] 群聊 group ID 和 group override 行为保持不变。
- [x] 超级用户简单问题不再自动获得 full 高风险工具集。
- [x] 明确任务仍可在权限允许时使用 full 工具。
- [x] live 和 Admin effective preview 都使用 strict audit；无模型依赖时 envelope
  一致，模型依赖上下文无法无模型重放时返回显式降级状态。
- [x] 未修改 QQbot、身份模板安全规则正文或服务器自定义角色正文。
- [x] 未写入具体超级用户 ID、密钥、Prompt 正文审计副本或敏感原始请求。
- [x] 全量 pytest、任务相关 WebUI lint、WebUI build 和 compileall 全部通过；全仓
  ESLint 保持已冻结的 `9 errors / 5 warnings` 基线。
- [x] 用户未授权前没有 commit，也没有混入用户已有文件。
