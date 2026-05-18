# 任务：重构项目提示词系统，并增加工具调用记录与前端管理页面

## 背景

当前项目是一个 QQbot / nanobot / 个人 agent 系统，已有后端、数据库、模型调用、工具调用、群聊上下文、记忆、Admin WebUI 等模块。

现在需要重构提示词系统，并增加工具调用记录功能。

这次重构不要做过度复杂的 PromptOps 平台。不要设计复杂的 SQL 分层提示词系统，不要做 prompt_blocks / prompt_presets / prompt_preset_blocks / prompt_entries 这种复杂结构。

推荐方案如下：

- 提示词模板使用 `Markdown + frontmatter`
- 提示词文件保存在文件系统中，例如 `prompts/*.md`
- 前端可以编辑、预览、保存、回滚、热重载提示词
- 提示词“深度 / 顺序”只作为模板注释和前端编辑提示，不做数据库强制分层
- 动态内容通过占位符插入，例如 `{{recent_messages}}`、`{{user_memory}}`、`{{retrieved_knowledge}}`
- SQLite 只用于记录：
  - agent run
  - tool call
  - prompt render log
  - prompt file version manifest / 保存历史
- 所有模型调用统一经过 `PromptManager`
- 所有工具调用统一经过 `ToolRegistry` 或等价包装层记录

## 核心目标

完成以下四件事：

1. 重构提示词系统
   - 新增 `PromptManager`
   - 支持从 `prompts/*.md` 读取 Markdown + frontmatter 模板
   - 支持变量替换
   - 支持模板校验
   - 支持预览渲染结果
   - 支持运行时热重载
   - 支持保存前自动备份

2. 改造运行链路
   - 不再让 bridge / route / model caller 到处手动拼 prompt
   - 模型调用前统一调用 `PromptManager.render(...)`
   - 保留旧逻辑 fallback，避免一次重构导致运行失败
   - 支持 `legacy / shadow / managed` 三种模式

3. 增加工具调用记录
   - 新增 `agent_runs`
   - 新增 `tool_calls`
   - 新增 `prompt_render_logs`
   - 所有工具调用都记录：
     - trace_id
     - run_id
     - tool_name
     - args_json
     - result_preview
     - status
     - latency_ms
     - error
     - started_at
     - finished_at

4. 修改前端 Admin WebUI
   - 新增 Prompt 文件列表页
   - 新增 Prompt 编辑页
   - 新增 Prompt 预览功能
   - 新增 Agent Run 列表页
   - 新增 Agent Run 详情页
   - 新增 Tool Call 详情展示

---

## 非目标

本次不要做以下事情：

- 不要做复杂 SQL 分层 prompt 管理
- 不要做 worldbook / prompt entry 动态激活系统
- 不要做复杂拖拽式 prompt 编排器
- 不要把知识库检索逻辑放进 PromptManager
- 不要让 PromptManager 决定该检索什么知识
- 不要让 prompt 文本成为工具权限的唯一依据
- 不要破坏现有 bot 运行链路
- 不要删除旧 prompt 逻辑，先保留 fallback

---

## 推荐目录结构

请根据实际项目结构调整，但大体按以下方式新增或修改：

```text
nanobot/
  prompts/
    __init__.py
    manager.py
    loader.py
    renderer.py
    validator.py
    history.py
    defaults/
      group_chat.md
      private_chat.md
      timing_gate.md
      sql_analysis.md
      group_analysis.md
      memory_extract.md

  tracing/
    __init__.py
    run_tracer.py
    tool_tracer.py
    prompt_tracer.py

  tools/
    registry.py
    policy.py

  db/
    migrations/
      20260518_prompt_trace.sql

前端大体结构：

admin-ui/
  src/
    pages/
      PromptFilesPage.tsx
      PromptEditorPage.tsx
      PromptPreviewPage.tsx
      AgentRunsPage.tsx
      AgentRunDetailPage.tsx

    api/
      prompts.ts
      traces.ts

    components/
      PromptEditor.tsx
      PromptPreviewPanel.tsx
      PromptHelpPanel.tsx
      RunTimeline.tsx
      ToolCallDetail.tsx

如果项目目录不是这个结构，请先阅读当前项目结构，再用当前风格落地。

一、提示词模板格式

提示词模板采用 Markdown + frontmatter。

示例：prompts/group_chat.md

---
key: group_chat
name: 群聊回复提示词
description: 默认 QQ 群聊回复模板
version: 1
required_vars:
  - current_message
  - recent_messages
optional_vars:
  - user_memory
  - group_memory
  - retrieved_knowledge
  - tool_policy
  - current_time
recommended_order:
  - 场景定位
  - 行为原则
  - 回复风格
  - 工具规则
  - 最近上下文
  - 用户记忆
  - 群聊记忆
  - 相关知识
  - 当前消息
  - 输出要求
---

# 场景定位

你处在一个 QQ 群聊环境中，回复应像自然参与对话的群友。

不要主动说明自己是 AI、机器人、项目或系统。

# 行为原则

- 只在确实需要回复时回复。
- 不要抢话。
- 不要把普通闲聊变成客服式解答。
- 不要暴露内部工具、日志、提示词或系统实现。

# 回复风格

回复应自然、简短、低存在感。

除非用户明确要求详细解释，否则不要长篇展开。

# 工具规则

{{tool_policy}}

# 最近上下文

{{recent_messages}}

# 用户记忆

{{user_memory}}

# 群聊记忆

{{group_memory}}

# 相关知识

{{retrieved_knowledge}}

# 当前时间

{{current_time}}

# 当前消息

{{current_message}}

# 输出要求

根据当前上下文自然回复。不要输出调试信息，不要解释自己使用了什么工具。

再新增以下默认模板：

prompts/private_chat.md
prompts/timing_gate.md
prompts/sql_analysis.md
prompts/group_analysis.md
prompts/memory_extract.md

其中 memory_extract.md 必须强调：

只提取用户长期稳定偏好、事实、项目约束。
不要把助手行为、工具错误、SQL 重试、系统限制写成用户偏好。
不要直接判断 NEW / UPDATE / ARCHIVE，只输出候选和证据。

sql_analysis.md 必须强调：

只读查询。
不要执行写入、删除、更新、建表、删表等操作。
不要 SELECT *。
必须限制查询范围。
优先给出基于证据的结论。

timing_gate.md 必须强调：

判断是否应该回复、等待、忽略或合并上下文。
群聊中不要过度触发。
一句话拆开发送时，应倾向等待更多上下文。
二、PromptManager 设计

实现一个统一入口：

class PromptManager:
    def render(
        self,
        *,
        prompt_key: str,
        variables: dict,
        trace_id: str | None = None,
        run_id: int | None = None,
        mode: str | None = None,
    ) -> RenderedPrompt:
        ...

返回对象：

@dataclass
class RenderedPrompt:
    prompt_key: str
    prompt_version: int
    content: str
    messages: list[dict]
    variables_used: dict
    missing_required_vars: list[str]
    unknown_vars: list[str]
    token_estimate: int | None
    render_log_id: int | None

要求：

从 prompts/{prompt_key}.md 读取模板
解析 frontmatter
替换 {{variable_name}}
必填变量缺失时记录 warning
未知变量可以保留 warning，不要直接崩溃
支持热重载
支持缓存
支持保存渲染日志
不要在 PromptManager 内部做知识库检索
不要在 PromptManager 内部做记忆筛选
PromptManager 只负责渲染
三、ContextBuilder 与 PromptManager 的边界

ContextBuilder 负责准备变量，例如：

variables = {
    "current_message": current_message,
    "recent_messages": recent_messages_text,
    "user_memory": user_memory_text,
    "group_memory": group_memory_text,
    "retrieved_knowledge": retrieved_knowledge_text,
    "tool_policy": tool_policy_text,
    "current_time": current_time_text,
}

PromptManager 只负责：

rendered = prompt_manager.render(
    prompt_key="group_chat",
    variables=variables,
    trace_id=trace_id,
    run_id=run_id,
)

不要把检索逻辑、记忆选择逻辑、SQL 查询逻辑写进 PromptManager。

四、运行模式

新增配置：

prompt_system:
  mode: legacy

支持三种模式：

legacy:
  继续使用旧提示词拼接逻辑。

shadow:
  运行时仍使用旧提示词，但同时用新 PromptManager 渲染一份并记录 prompt_render_log，用于对比。

managed:
  正式使用 PromptManager 渲染结果。

要求：

默认先使用 legacy 或 shadow
不要默认直接切到 managed
在代码中保留 fallback
如果新模板渲染失败，自动回退旧逻辑，并记录错误
五、提示词文件保存与版本备份

提示词文件保存在：

prompts/*.md

每次前端保存提示词时：

读取旧文件
将旧文件备份到：
prompts_history/{prompt_key}/YYYYMMDD_HHMMSS.md
写入新文件
更新 manifest

manifest 可以是 JSON 文件：

prompts_history/manifest.json

结构示例：

{
  "group_chat": {
    "version": 12,
    "updated_at": "2026-05-18T22:30:00+08:00",
    "updated_by": "admin",
    "current_path": "prompts/group_chat.md",
    "latest_backup_path": "prompts_history/group_chat/20260518_223000.md"
  }
}

也可以用 SQLite 表记录版本，但不要把完整 prompt 系统做成复杂 SQL 分层。

如果使用 SQLite，最多新增简单表：

CREATE TABLE prompt_file_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    backup_path TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL,
    note TEXT
);
六、数据库迁移

新增迁移文件，例如：

db/migrations/20260518_prompt_trace.sql

至少包含三张表。

agent_runs
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL UNIQUE,
    session_id TEXT,
    chat_type TEXT,
    group_id TEXT,
    user_id TEXT,
    trigger_type TEXT,
    status TEXT NOT NULL,
    prompt_key TEXT,
    model TEXT,
    input_preview TEXT,
    output_preview TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_trace_id ON agent_runs(trace_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_group_id ON agent_runs(group_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_user_id ON agent_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_started_at ON agent_runs(started_at);
tool_calls
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    trace_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT,
    result_preview TEXT,
    status TEXT NOT NULL,
    latency_ms INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT,
    FOREIGN KEY (run_id) REFERENCES agent_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_run_id ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_trace_id ON tool_calls(trace_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_name ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_calls_started_at ON tool_calls(started_at);
prompt_render_logs
CREATE TABLE IF NOT EXISTS prompt_render_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    trace_id TEXT NOT NULL,
    prompt_key TEXT NOT NULL,
    prompt_version INTEGER,
    variables_json TEXT,
    rendered_preview TEXT,
    token_estimate INTEGER,
    missing_vars_json TEXT,
    warnings_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES agent_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_prompt_render_logs_run_id ON prompt_render_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_prompt_render_logs_trace_id ON prompt_render_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_prompt_render_logs_prompt_key ON prompt_render_logs(prompt_key);
CREATE INDEX IF NOT EXISTS idx_prompt_render_logs_created_at ON prompt_render_logs(created_at);

注意：

rendered_preview 只存截断后的内容，避免数据库膨胀。
完整 prompt 只在 debug 模式保存，或者不保存。
args_json 需要做长度限制或截断策略。
result_preview 必须截断。
敏感字段需要脱敏。
七、AgentRun 记录

新增 tracer：

class RunTracer:
    def start_run(
        self,
        *,
        trace_id: str,
        session_id: str | None,
        chat_type: str | None,
        group_id: str | None,
        user_id: str | None,
        trigger_type: str | None,
        prompt_key: str | None,
        model: str | None,
        input_preview: str | None,
    ) -> int:
        ...

    def finish_run(
        self,
        *,
        run_id: int,
        status: str,
        output_preview: str | None = None,
        error: str | None = None,
    ) -> None:
        ...

要求：

每次外部消息进入并触发一次 agent 流程时创建 agent_runs
所有 prompt render / model call / tool call 都关联同一个 trace_id
能在前端根据 trace_id 查完整链路
八、工具调用记录

所有工具调用必须通过统一包装层。

如果已有 ToolRegistry，则改造它。

如果没有，则新增一个轻量 ToolRegistry 或 ToolTracer 包装器。

示例逻辑：

class ToolRegistry:
    async def call_tool(self, name: str, args: dict, ctx: ToolContext):
        started = now()
        call_id = tool_tracer.start_tool_call(
            run_id=ctx.run_id,
            trace_id=ctx.trace_id,
            tool_name=name,
            args=args,
        )

        try:
            result = await self.tools[name].invoke(args, ctx)
            tool_tracer.finish_tool_call(
                call_id=call_id,
                status="success",
                result_preview=summarize_result(result),
                latency_ms=elapsed_ms(started),
            )
            return result
        except Exception as e:
            tool_tracer.finish_tool_call(
                call_id=call_id,
                status="error",
                error=str(e),
                latency_ms=elapsed_ms(started),
            )
            raise

要求：

不要让每个工具手动写日志
工具调用日志应该集中处理
支持同步和异步工具
支持异常记录
支持 result 截断
支持 args 脱敏
至少记录：
tool_name
args_json
result_preview
status
latency_ms
error
九、后端 API

新增或扩展 Admin API。

Prompt API
GET /admin/prompts

返回所有 prompts/*.md 文件及 metadata。

GET /admin/prompts/{prompt_key}

返回模板内容、frontmatter、版本信息。

PUT /admin/prompts/{prompt_key}

保存模板，保存前自动备份。

请求：

{
  "content": "...完整 Markdown...",
  "note": "调整群聊回复风格"
}
POST /admin/prompts/{prompt_key}/preview

根据输入变量预览渲染结果。

请求：

{
  "variables": {
    "current_message": "这个 cooldown 怎么这么大",
    "recent_messages": "...",
    "user_memory": "",
    "group_memory": "",
    "retrieved_knowledge": "",
    "tool_policy": "..."
  }
}

返回：

{
  "prompt_key": "group_chat",
  "prompt_version": 1,
  "content": "...渲染后的 prompt...",
  "messages": [
    {
      "role": "system",
      "content": "..."
    }
  ],
  "missing_required_vars": [],
  "unknown_vars": [],
  "token_estimate": 1234,
  "warnings": []
}
POST /admin/prompts/reload

热重载 prompt 缓存。

GET /admin/prompts/{prompt_key}/history

查看历史版本。

POST /admin/prompts/{prompt_key}/rollback

回滚到某个历史版本。

Trace API
GET /admin/agent-runs

支持 query 参数：

trace_id
group_id
user_id
status
prompt_key
limit
offset
GET /admin/agent-runs/{run_id}

返回单次 run 的详情，包括：

{
  "run": {},
  "prompt_render_logs": [],
  "tool_calls": []
}
GET /admin/tool-calls

支持过滤：

trace_id
run_id
tool_name
status
limit
offset
GET /admin/tool-calls/{tool_call_id}

返回工具调用详情。

十、前端页面
1. PromptFilesPage

路径建议：

/admin/prompts

展示：

prompt_key
name
description
version
updated_at
required_vars
optional_vars

操作：

编辑
预览
历史
热重载
2. PromptEditorPage

路径建议：

/admin/prompts/:promptKey

布局：

左侧：Markdown 编辑器
右侧：提示词组织建议 + 变量说明 + 校验结果
底部：保存 / 预览 / 回滚 / 热重载

右侧固定显示提示词组织建议：

推荐组织顺序：

1. 场景定位
2. 行为原则
3. 回复风格
4. 工具规则
5. 最近上下文
6. 用户记忆
7. 群聊记忆
8. 相关知识
9. 当前消息
10. 输出要求

变量说明：

{{current_message}}       当前用户消息，通常必填
{{recent_messages}}       最近上下文，通常必填
{{user_memory}}           用户长期记忆，可选
{{group_memory}}          群聊风格 / 群记忆，可选
{{retrieved_knowledge}}   知识库检索结果，可选
{{tool_policy}}           工具使用规则，可选
{{current_time}}          当前时间，可选

校验结果显示：

缺少 required_vars
出现未知变量
frontmatter 解析失败
模板为空
token 估算过长
保存成功 / 失败
3. PromptPreviewPage 或编辑页内预览面板

支持输入模拟变量。

至少提供：

current_message
recent_messages
user_memory
group_memory
retrieved_knowledge
tool_policy
current_time

点击预览后显示：

渲染后的 prompt
messages JSON
缺失变量
token 估算
warnings
4. AgentRunsPage

路径建议：

/admin/agent-runs

展示：

trace_id
chat_type
group_id
user_id
prompt_key
status
model
started_at
latency
tool_call_count

支持过滤：

trace_id
group_id
user_id
status
prompt_key
5. AgentRunDetailPage

路径建议：

/admin/agent-runs/:runId

展示：

基本信息
Prompt Render Logs
Tool Calls
错误信息
输入摘要
输出摘要

Tool Calls 表格：

tool_name
status
latency_ms
started_at
args_json
result_preview
error
十一、运行链路改造要求

找到当前模型调用链路，大概率类似：

route / handler
  → context builder
  → prompt 拼接
  → model call
  → tool call
  → reply

改造为：

route / handler
  → trace_id
  → RunTracer.start_run
  → ContextBuilder 准备 variables
  → PromptManager.render
  → model call
  → ToolRegistry.call_tool
  → reply
  → RunTracer.finish_run

注意：

每次 run 必须有 trace_id
trace_id 传递到 prompt render 和 tool call
工具调用必须带 run_id
如果模型调用失败，agent_run 状态为 error
如果工具调用失败，tool_call 状态为 error，但是否终止 run 按原业务逻辑决定
如果 PromptManager 渲染失败，按配置 fallback 到 legacy prompt
十二、默认模板内容

请至少初始化以下模板。

group_chat.md

用途：群聊自然回复。

核心要求：

像群友，不像客服
短回复
低存在感
不主动暴露 AI / bot / 项目名
不暴露工具和日志
private_chat.md

用途：私聊助手回复。

核心要求：

直接、有帮助
可以比群聊更详细
但仍然不要暴露内部系统实现
timing_gate.md

用途：判断是否回复。

核心要求：

输出结构化判断
判断 reply / wait / ignore
群聊里避免过度触发
一句话拆开时倾向等待
sql_analysis.md

用途：SQL 分析工具提示词。

核心要求：

只读
不 SELECT *
限制范围
优先查必要字段
避免反复查询
总结时引用查询依据
group_analysis.md

用途：群聊总结 / 群聊分析。

核心要求：

基于证据总结
不要把助手行为当成用户偏好
区分事实、推测、建议
memory_extract.md

用途：记忆候选提取。

核心要求：

只输出候选和证据
不决定 NEW / UPDATE / ARCHIVE
不把 bot 行为、工具错误、系统限制写成用户偏好
关注长期稳定偏好、事实、项目约束
十三、测试要求

至少补以下测试。

后端测试
frontmatter 解析测试
required_vars 缺失测试
变量替换测试
未知变量 warning 测试
prompt 保存自动备份测试
prompt reload 测试
prompt preview API 测试
agent_run 创建 / 完成测试
tool_call success / error 记录测试
PromptManager legacy fallback 测试
前端测试或手动验收
能看到 prompt 文件列表
能打开并编辑 prompt
保存后能生成历史备份
preview 能看到渲染结果
缺变量时有提示
agent_runs 页面能看到最近运行
run detail 能看到 tool calls
tool call error 能正确展示
十四、兼容性要求

非常重要：

不要破坏当前启动流程
不要强制依赖前端才能运行
不要让 prompt 文件不存在时直接崩溃
如果模板不存在，使用旧逻辑 fallback
如果 frontmatter 解析失败，返回明确错误
如果工具调用记录失败，不应该影响工具本身执行
如果 tracing 数据库写入失败，不应该导致 bot 主流程失败，但要写普通日志
默认不要保存完整 prompt 到数据库，避免膨胀
十五、安全与脱敏要求

记录 tool call 时需要处理：

args_json 截断
result_preview 截断
敏感字段脱敏，例如：
token
api_key
password
secret
cookie
authorization
不要在前端默认展示完整敏感内容
前端展示 JSON 时要格式化，但不要执行 HTML
后端 API 需复用现有 Admin 鉴权逻辑
十六、提交内容要求

完成后输出：

修改了哪些文件
新增了哪些文件
数据库迁移说明
新增 API 列表
前端新增页面列表
如何切换 prompt_system.mode
如何测试
当前还有哪些未完成或风险点
十七、建议实现顺序

请按以下顺序实现，避免一次性改崩：

Step 1：先审查项目结构

先阅读：

后端入口
当前 prompt 拼接逻辑
当前 model call 逻辑
当前 tool call 逻辑
当前 Admin WebUI 结构
当前 DB migration 方式

不要假设目录一定存在，按实际项目结构落地。

Step 2：增加数据库迁移

新增：

agent_runs
tool_calls
prompt_render_logs
可选 prompt_file_versions
Step 3：实现 PromptManager

实现：

loader
renderer
validator
history backup
reload cache
preview
Step 4：新增默认 prompt 文件

新增：

group_chat.md
private_chat.md
timing_gate.md
sql_analysis.md
group_analysis.md
memory_extract.md
Step 5：接入 shadow 模式

先不要完全替换旧 prompt。

在现有运行链路中：

旧 prompt 正常使用
新 PromptManager 同时 render
记录 prompt_render_log
对比是否报错
Step 6：封装工具调用记录

改造 ToolRegistry 或工具调用入口，统一记录 tool_calls。

Step 7：接入 managed 模式

当配置为 managed 时，模型调用使用 PromptManager 渲染结果。

Step 8：增加 Admin API

新增 Prompt API 和 Trace API。

Step 9：增加前端页面

新增：

PromptFilesPage
PromptEditorPage
PromptPreview 功能
AgentRunsPage
AgentRunDetailPage
Step 10：测试和整理

跑现有测试。

补充必要测试。

确认 legacy / shadow / managed 三种模式都能正常工作。

十八、验收标准

最终必须满足：

后端能从 prompts/*.md 读取提示词
前端能编辑并保存提示词
保存提示词时自动备份旧版本
前端能预览变量替换后的 prompt
运行时能记录 prompt_render_logs
每次 agent run 有 trace_id
工具调用能记录到 tool_calls
前端能查看 agent_runs 和 tool_calls
legacy 模式不影响旧逻辑
shadow 模式能同时渲染新 prompt 并记录日志
managed 模式能正式使用新 PromptManager
出错时有 fallback，不会直接导致 bot 崩溃
十九、特别注意

本次重构重点是“轻量可管理”，不是“复杂 PromptOps”。

不要把提示词拆成一堆 SQL block。

不要把提示词深度做成强制数据库层级。

提示词深度只体现在：

模板内推荐顺序
Markdown 标题结构
前端编辑提示
渲染时变量插入位置

知识库动态拼接只需要：

ContextBuilder 负责检索
PromptManager 负责插入 {{retrieved_knowledge}}

工具调用记录必须结构化，因为这是 agent runtime 可观测性的基础。

请优先保证运行链路稳定，再逐步切到新提示词系统。# 任务：重构项目提示词系统，并增加工具调用记录与前端管理页面

## 背景

当前项目是一个 QQbot / nanobot / 个人 agent 系统，已有后端、数据库、模型调用、工具调用、群聊上下文、记忆、Admin WebUI 等模块。

现在需要重构提示词系统，并增加工具调用记录功能。

这次重构不要做过度复杂的 PromptOps 平台。不要设计复杂的 SQL 分层提示词系统，不要做 prompt_blocks / prompt_presets / prompt_preset_blocks / prompt_entries 这种复杂结构。

推荐方案如下：

- 提示词模板使用 `Markdown + frontmatter`
- 提示词文件保存在文件系统中，例如 `prompts/*.md`
- 前端可以编辑、预览、保存、回滚、热重载提示词
- 提示词“深度 / 顺序”只作为模板注释和前端编辑提示，不做数据库强制分层
- 动态内容通过占位符插入，例如 `{{recent_messages}}`、`{{user_memory}}`、`{{retrieved_knowledge}}`
- SQLite 只用于记录：
  - agent run
  - tool call
  - prompt render log
  - prompt file version manifest / 保存历史
- 所有模型调用统一经过 `PromptManager`
- 所有工具调用统一经过 `ToolRegistry` 或等价包装层记录

## 核心目标

完成以下四件事：

1. 重构提示词系统
   - 新增 `PromptManager`
   - 支持从 `prompts/*.md` 读取 Markdown + frontmatter 模板
   - 支持变量替换
   - 支持模板校验
   - 支持预览渲染结果
   - 支持运行时热重载
   - 支持保存前自动备份

2. 改造运行链路
   - 不再让 bridge / route / model caller 到处手动拼 prompt
   - 模型调用前统一调用 `PromptManager.render(...)`
   - 保留旧逻辑 fallback，避免一次重构导致运行失败
   - 支持 `legacy / shadow / managed` 三种模式

3. 增加工具调用记录
   - 新增 `agent_runs`
   - 新增 `tool_calls`
   - 新增 `prompt_render_logs`
   - 所有工具调用都记录：
     - trace_id
     - run_id
     - tool_name
     - args_json
     - result_preview
     - status
     - latency_ms
     - error
     - started_at
     - finished_at

4. 修改前端 Admin WebUI
   - 新增 Prompt 文件列表页
   - 新增 Prompt 编辑页
   - 新增 Prompt 预览功能
   - 新增 Agent Run 列表页
   - 新增 Agent Run 详情页
   - 新增 Tool Call 详情展示

---

## 非目标

本次不要做以下事情：

- 不要做复杂 SQL 分层 prompt 管理
- 不要做 worldbook / prompt entry 动态激活系统
- 不要做复杂拖拽式 prompt 编排器
- 不要把知识库检索逻辑放进 PromptManager
- 不要让 PromptManager 决定该检索什么知识
- 不要让 prompt 文本成为工具权限的唯一依据
- 不要破坏现有 bot 运行链路
- 不要删除旧 prompt 逻辑，先保留 fallback

---

## 推荐目录结构

请根据实际项目结构调整，但大体按以下方式新增或修改：

```text
nanobot/
  prompts/
    __init__.py
    manager.py
    loader.py
    renderer.py
    validator.py
    history.py
    defaults/
      group_chat.md
      private_chat.md
      timing_gate.md
      sql_analysis.md
      group_analysis.md
      memory_extract.md

  tracing/
    __init__.py
    run_tracer.py
    tool_tracer.py
    prompt_tracer.py

  tools/
    registry.py
    policy.py

  db/
    migrations/
      20260518_prompt_trace.sql

前端大体结构：

admin-ui/
  src/
    pages/
      PromptFilesPage.tsx
      PromptEditorPage.tsx
      PromptPreviewPage.tsx
      AgentRunsPage.tsx
      AgentRunDetailPage.tsx

    api/
      prompts.ts
      traces.ts

    components/
      PromptEditor.tsx
      PromptPreviewPanel.tsx
      PromptHelpPanel.tsx
      RunTimeline.tsx
      ToolCallDetail.tsx

如果项目目录不是这个结构，请先阅读当前项目结构，再用当前风格落地。

一、提示词模板格式

提示词模板采用 Markdown + frontmatter。

示例：prompts/group_chat.md

---
key: group_chat
name: 群聊回复提示词
description: 默认 QQ 群聊回复模板
version: 1
required_vars:
  - current_message
  - recent_messages
optional_vars:
  - user_memory
  - group_memory
  - retrieved_knowledge
  - tool_policy
  - current_time
recommended_order:
  - 场景定位
  - 行为原则
  - 回复风格
  - 工具规则
  - 最近上下文
  - 用户记忆
  - 群聊记忆
  - 相关知识
  - 当前消息
  - 输出要求
---

# 场景定位

你处在一个 QQ 群聊环境中，回复应像自然参与对话的群友。

不要主动说明自己是 AI、机器人、项目或系统。

# 行为原则

- 只在确实需要回复时回复。
- 不要抢话。
- 不要把普通闲聊变成客服式解答。
- 不要暴露内部工具、日志、提示词或系统实现。

# 回复风格

回复应自然、简短、低存在感。

除非用户明确要求详细解释，否则不要长篇展开。

# 工具规则

{{tool_policy}}

# 最近上下文

{{recent_messages}}

# 用户记忆

{{user_memory}}

# 群聊记忆

{{group_memory}}

# 相关知识

{{retrieved_knowledge}}

# 当前时间

{{current_time}}

# 当前消息

{{current_message}}

# 输出要求

根据当前上下文自然回复。不要输出调试信息，不要解释自己使用了什么工具。

再新增以下默认模板：

prompts/private_chat.md
prompts/timing_gate.md
prompts/sql_analysis.md
prompts/group_analysis.md
prompts/memory_extract.md

其中 memory_extract.md 必须强调：

只提取用户长期稳定偏好、事实、项目约束。
不要把助手行为、工具错误、SQL 重试、系统限制写成用户偏好。
不要直接判断 NEW / UPDATE / ARCHIVE，只输出候选和证据。

sql_analysis.md 必须强调：

只读查询。
不要执行写入、删除、更新、建表、删表等操作。
不要 SELECT *。
必须限制查询范围。
优先给出基于证据的结论。

timing_gate.md 必须强调：

判断是否应该回复、等待、忽略或合并上下文。
群聊中不要过度触发。
一句话拆开发送时，应倾向等待更多上下文。
二、PromptManager 设计

实现一个统一入口：

class PromptManager:
    def render(
        self,
        *,
        prompt_key: str,
        variables: dict,
        trace_id: str | None = None,
        run_id: int | None = None,
        mode: str | None = None,
    ) -> RenderedPrompt:
        ...

返回对象：

@dataclass
class RenderedPrompt:
    prompt_key: str
    prompt_version: int
    content: str
    messages: list[dict]
    variables_used: dict
    missing_required_vars: list[str]
    unknown_vars: list[str]
    token_estimate: int | None
    render_log_id: int | None

要求：

从 prompts/{prompt_key}.md 读取模板
解析 frontmatter
替换 {{variable_name}}
必填变量缺失时记录 warning
未知变量可以保留 warning，不要直接崩溃
支持热重载
支持缓存
支持保存渲染日志
不要在 PromptManager 内部做知识库检索
不要在 PromptManager 内部做记忆筛选
PromptManager 只负责渲染
三、ContextBuilder 与 PromptManager 的边界

ContextBuilder 负责准备变量，例如：

variables = {
    "current_message": current_message,
    "recent_messages": recent_messages_text,
    "user_memory": user_memory_text,
    "group_memory": group_memory_text,
    "retrieved_knowledge": retrieved_knowledge_text,
    "tool_policy": tool_policy_text,
    "current_time": current_time_text,
}

PromptManager 只负责：

rendered = prompt_manager.render(
    prompt_key="group_chat",
    variables=variables,
    trace_id=trace_id,
    run_id=run_id,
)

不要把检索逻辑、记忆选择逻辑、SQL 查询逻辑写进 PromptManager。

四、运行模式

新增配置：

prompt_system:
  mode: legacy

支持三种模式：

legacy:
  继续使用旧提示词拼接逻辑。

shadow:
  运行时仍使用旧提示词，但同时用新 PromptManager 渲染一份并记录 prompt_render_log，用于对比。

managed:
  正式使用 PromptManager 渲染结果。

要求：

默认先使用 legacy 或 shadow
不要默认直接切到 managed
在代码中保留 fallback
如果新模板渲染失败，自动回退旧逻辑，并记录错误
五、提示词文件保存与版本备份

提示词文件保存在：

prompts/*.md

每次前端保存提示词时：

读取旧文件
将旧文件备份到：
prompts_history/{prompt_key}/YYYYMMDD_HHMMSS.md
写入新文件
更新 manifest

manifest 可以是 JSON 文件：

prompts_history/manifest.json

结构示例：

{
  "group_chat": {
    "version": 12,
    "updated_at": "2026-05-18T22:30:00+08:00",
    "updated_by": "admin",
    "current_path": "prompts/group_chat.md",
    "latest_backup_path": "prompts_history/group_chat/20260518_223000.md"
  }
}

也可以用 SQLite 表记录版本，但不要把完整 prompt 系统做成复杂 SQL 分层。

如果使用 SQLite，最多新增简单表：

CREATE TABLE prompt_file_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    backup_path TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL,
    note TEXT
);
六、数据库迁移

新增迁移文件，例如：

db/migrations/20260518_prompt_trace.sql

至少包含三张表。

agent_runs
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL UNIQUE,
    session_id TEXT,
    chat_type TEXT,
    group_id TEXT,
    user_id TEXT,
    trigger_type TEXT,
    status TEXT NOT NULL,
    prompt_key TEXT,
    model TEXT,
    input_preview TEXT,
    output_preview TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_trace_id ON agent_runs(trace_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_group_id ON agent_runs(group_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_user_id ON agent_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_started_at ON agent_runs(started_at);
tool_calls
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    trace_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT,
    result_preview TEXT,
    status TEXT NOT NULL,
    latency_ms INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT,
    FOREIGN KEY (run_id) REFERENCES agent_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_run_id ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_trace_id ON tool_calls(trace_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_name ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_calls_started_at ON tool_calls(started_at);
prompt_render_logs
CREATE TABLE IF NOT EXISTS prompt_render_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    trace_id TEXT NOT NULL,
    prompt_key TEXT NOT NULL,
    prompt_version INTEGER,
    variables_json TEXT,
    rendered_preview TEXT,
    token_estimate INTEGER,
    missing_vars_json TEXT,
    warnings_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES agent_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_prompt_render_logs_run_id ON prompt_render_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_prompt_render_logs_trace_id ON prompt_render_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_prompt_render_logs_prompt_key ON prompt_render_logs(prompt_key);
CREATE INDEX IF NOT EXISTS idx_prompt_render_logs_created_at ON prompt_render_logs(created_at);

注意：

rendered_preview 只存截断后的内容，避免数据库膨胀。
完整 prompt 只在 debug 模式保存，或者不保存。
args_json 需要做长度限制或截断策略。
result_preview 必须截断。
敏感字段需要脱敏。
七、AgentRun 记录

新增 tracer：

class RunTracer:
    def start_run(
        self,
        *,
        trace_id: str,
        session_id: str | None,
        chat_type: str | None,
        group_id: str | None,
        user_id: str | None,
        trigger_type: str | None,
        prompt_key: str | None,
        model: str | None,
        input_preview: str | None,
    ) -> int:
        ...

    def finish_run(
        self,
        *,
        run_id: int,
        status: str,
        output_preview: str | None = None,
        error: str | None = None,
    ) -> None:
        ...

要求：

每次外部消息进入并触发一次 agent 流程时创建 agent_runs
所有 prompt render / model call / tool call 都关联同一个 trace_id
能在前端根据 trace_id 查完整链路
八、工具调用记录

所有工具调用必须通过统一包装层。

如果已有 ToolRegistry，则改造它。

如果没有，则新增一个轻量 ToolRegistry 或 ToolTracer 包装器。

示例逻辑：

class ToolRegistry:
    async def call_tool(self, name: str, args: dict, ctx: ToolContext):
        started = now()
        call_id = tool_tracer.start_tool_call(
            run_id=ctx.run_id,
            trace_id=ctx.trace_id,
            tool_name=name,
            args=args,
        )

        try:
            result = await self.tools[name].invoke(args, ctx)
            tool_tracer.finish_tool_call(
                call_id=call_id,
                status="success",
                result_preview=summarize_result(result),
                latency_ms=elapsed_ms(started),
            )
            return result
        except Exception as e:
            tool_tracer.finish_tool_call(
                call_id=call_id,
                status="error",
                error=str(e),
                latency_ms=elapsed_ms(started),
            )
            raise

要求：

不要让每个工具手动写日志
工具调用日志应该集中处理
支持同步和异步工具
支持异常记录
支持 result 截断
支持 args 脱敏
至少记录：
tool_name
args_json
result_preview
status
latency_ms
error
九、后端 API

新增或扩展 Admin API。

Prompt API
GET /admin/prompts

返回所有 prompts/*.md 文件及 metadata。

GET /admin/prompts/{prompt_key}

返回模板内容、frontmatter、版本信息。

PUT /admin/prompts/{prompt_key}

保存模板，保存前自动备份。

请求：

{
  "content": "...完整 Markdown...",
  "note": "调整群聊回复风格"
}
POST /admin/prompts/{prompt_key}/preview

根据输入变量预览渲染结果。

请求：

{
  "variables": {
    "current_message": "这个 cooldown 怎么这么大",
    "recent_messages": "...",
    "user_memory": "",
    "group_memory": "",
    "retrieved_knowledge": "",
    "tool_policy": "..."
  }
}

返回：

{
  "prompt_key": "group_chat",
  "prompt_version": 1,
  "content": "...渲染后的 prompt...",
  "messages": [
    {
      "role": "system",
      "content": "..."
    }
  ],
  "missing_required_vars": [],
  "unknown_vars": [],
  "token_estimate": 1234,
  "warnings": []
}
POST /admin/prompts/reload

热重载 prompt 缓存。

GET /admin/prompts/{prompt_key}/history

查看历史版本。

POST /admin/prompts/{prompt_key}/rollback

回滚到某个历史版本。

Trace API
GET /admin/agent-runs

支持 query 参数：

trace_id
group_id
user_id
status
prompt_key
limit
offset
GET /admin/agent-runs/{run_id}

返回单次 run 的详情，包括：

{
  "run": {},
  "prompt_render_logs": [],
  "tool_calls": []
}
GET /admin/tool-calls

支持过滤：

trace_id
run_id
tool_name
status
limit
offset
GET /admin/tool-calls/{tool_call_id}

返回工具调用详情。

十、前端页面
1. PromptFilesPage

路径建议：

/admin/prompts

展示：

prompt_key
name
description
version
updated_at
required_vars
optional_vars

操作：

编辑
预览
历史
热重载
2. PromptEditorPage

路径建议：

/admin/prompts/:promptKey

布局：

左侧：Markdown 编辑器
右侧：提示词组织建议 + 变量说明 + 校验结果
底部：保存 / 预览 / 回滚 / 热重载

右侧固定显示提示词组织建议：

推荐组织顺序：

1. 场景定位
2. 行为原则
3. 回复风格
4. 工具规则
5. 最近上下文
6. 用户记忆
7. 群聊记忆
8. 相关知识
9. 当前消息
10. 输出要求

变量说明：

{{current_message}}       当前用户消息，通常必填
{{recent_messages}}       最近上下文，通常必填
{{user_memory}}           用户长期记忆，可选
{{group_memory}}          群聊风格 / 群记忆，可选
{{retrieved_knowledge}}   知识库检索结果，可选
{{tool_policy}}           工具使用规则，可选
{{current_time}}          当前时间，可选

校验结果显示：

缺少 required_vars
出现未知变量
frontmatter 解析失败
模板为空
token 估算过长
保存成功 / 失败
3. PromptPreviewPage 或编辑页内预览面板

支持输入模拟变量。

至少提供：

current_message
recent_messages
user_memory
group_memory
retrieved_knowledge
tool_policy
current_time

点击预览后显示：

渲染后的 prompt
messages JSON
缺失变量
token 估算
warnings
4. AgentRunsPage

路径建议：

/admin/agent-runs

展示：

trace_id
chat_type
group_id
user_id
prompt_key
status
model
started_at
latency
tool_call_count

支持过滤：

trace_id
group_id
user_id
status
prompt_key
5. AgentRunDetailPage

路径建议：

/admin/agent-runs/:runId

展示：

基本信息
Prompt Render Logs
Tool Calls
错误信息
输入摘要
输出摘要

Tool Calls 表格：

tool_name
status
latency_ms
started_at
args_json
result_preview
error
十一、运行链路改造要求

找到当前模型调用链路，大概率类似：

route / handler
  → context builder
  → prompt 拼接
  → model call
  → tool call
  → reply

改造为：

route / handler
  → trace_id
  → RunTracer.start_run
  → ContextBuilder 准备 variables
  → PromptManager.render
  → model call
  → ToolRegistry.call_tool
  → reply
  → RunTracer.finish_run

注意：

每次 run 必须有 trace_id
trace_id 传递到 prompt render 和 tool call
工具调用必须带 run_id
如果模型调用失败，agent_run 状态为 error
如果工具调用失败，tool_call 状态为 error，但是否终止 run 按原业务逻辑决定
如果 PromptManager 渲染失败，按配置 fallback 到 legacy prompt
十二、默认模板内容

请至少初始化以下模板。

group_chat.md

用途：群聊自然回复。

核心要求：

像群友，不像客服
短回复
低存在感
不主动暴露 AI / bot / 项目名
不暴露工具和日志
private_chat.md

用途：私聊助手回复。

核心要求：

直接、有帮助
可以比群聊更详细
但仍然不要暴露内部系统实现
timing_gate.md

用途：判断是否回复。

核心要求：

输出结构化判断
判断 reply / wait / ignore
群聊里避免过度触发
一句话拆开时倾向等待
sql_analysis.md

用途：SQL 分析工具提示词。

核心要求：

只读
不 SELECT *
限制范围
优先查必要字段
避免反复查询
总结时引用查询依据
group_analysis.md

用途：群聊总结 / 群聊分析。

核心要求：

基于证据总结
不要把助手行为当成用户偏好
区分事实、推测、建议
memory_extract.md

用途：记忆候选提取。

核心要求：

只输出候选和证据
不决定 NEW / UPDATE / ARCHIVE
不把 bot 行为、工具错误、系统限制写成用户偏好
关注长期稳定偏好、事实、项目约束
十三、测试要求

至少补以下测试。

后端测试
frontmatter 解析测试
required_vars 缺失测试
变量替换测试
未知变量 warning 测试
prompt 保存自动备份测试
prompt reload 测试
prompt preview API 测试
agent_run 创建 / 完成测试
tool_call success / error 记录测试
PromptManager legacy fallback 测试
前端测试或手动验收
能看到 prompt 文件列表
能打开并编辑 prompt
保存后能生成历史备份
preview 能看到渲染结果
缺变量时有提示
agent_runs 页面能看到最近运行
run detail 能看到 tool calls
tool call error 能正确展示
十四、兼容性要求

非常重要：

不要破坏当前启动流程
不要强制依赖前端才能运行
不要让 prompt 文件不存在时直接崩溃
如果模板不存在，使用旧逻辑 fallback
如果 frontmatter 解析失败，返回明确错误
如果工具调用记录失败，不应该影响工具本身执行
如果 tracing 数据库写入失败，不应该导致 bot 主流程失败，但要写普通日志
默认不要保存完整 prompt 到数据库，避免膨胀
十五、安全与脱敏要求

记录 tool call 时需要处理：

args_json 截断
result_preview 截断
敏感字段脱敏，例如：
token
api_key
password
secret
cookie
authorization
不要在前端默认展示完整敏感内容
前端展示 JSON 时要格式化，但不要执行 HTML
后端 API 需复用现有 Admin 鉴权逻辑
十六、提交内容要求

完成后输出：

修改了哪些文件
新增了哪些文件
数据库迁移说明
新增 API 列表
前端新增页面列表
如何切换 prompt_system.mode
如何测试
当前还有哪些未完成或风险点
十七、建议实现顺序

请按以下顺序实现，避免一次性改崩：

Step 1：先审查项目结构

先阅读：

后端入口
当前 prompt 拼接逻辑
当前 model call 逻辑
当前 tool call 逻辑
当前 Admin WebUI 结构
当前 DB migration 方式

不要假设目录一定存在，按实际项目结构落地。

Step 2：增加数据库迁移

新增：

agent_runs
tool_calls
prompt_render_logs
可选 prompt_file_versions
Step 3：实现 PromptManager

实现：

loader
renderer
validator
history backup
reload cache
preview
Step 4：新增默认 prompt 文件

新增：

group_chat.md
private_chat.md
timing_gate.md
sql_analysis.md
group_analysis.md
memory_extract.md
Step 5：接入 shadow 模式

先不要完全替换旧 prompt。

在现有运行链路中：

旧 prompt 正常使用
新 PromptManager 同时 render
记录 prompt_render_log
对比是否报错
Step 6：封装工具调用记录

改造 ToolRegistry 或工具调用入口，统一记录 tool_calls。

Step 7：接入 managed 模式

当配置为 managed 时，模型调用使用 PromptManager 渲染结果。

Step 8：增加 Admin API

新增 Prompt API 和 Trace API。

Step 9：增加前端页面

新增：

PromptFilesPage
PromptEditorPage
PromptPreview 功能
AgentRunsPage
AgentRunDetailPage
Step 10：测试和整理

跑现有测试。

补充必要测试。

确认 legacy / shadow / managed 三种模式都能正常工作。

十八、验收标准

最终必须满足：

后端能从 prompts/*.md 读取提示词
前端能编辑并保存提示词
保存提示词时自动备份旧版本
前端能预览变量替换后的 prompt
运行时能记录 prompt_render_logs
每次 agent run 有 trace_id
工具调用能记录到 tool_calls
前端能查看 agent_runs 和 tool_calls
legacy 模式不影响旧逻辑
shadow 模式能同时渲染新 prompt 并记录日志
managed 模式能正式使用新 PromptManager
出错时有 fallback，不会直接导致 bot 崩溃
十九、特别注意

本次重构重点是“轻量可管理”，不是“复杂 PromptOps”。

不要把提示词拆成一堆 SQL block。

不要把提示词深度做成强制数据库层级。

提示词深度只体现在：

模板内推荐顺序
Markdown 标题结构
前端编辑提示
渲染时变量插入位置

知识库动态拼接只需要：

ContextBuilder 负责检索
PromptManager 负责插入 {{retrieved_knowledge}}

工具调用记录必须结构化，因为这是 agent runtime 可观测性的基础。

请优先保证运行链路稳定，再逐步切到新提示词系统。
