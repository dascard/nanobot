# Nanobot 最近需求综合任务指导

## 总目标

把最近几轮需求收敛成四条主线

1. 修复表情包自动描述失败
2. 完善 LLM API 请求与响应日志
3. 建立 reply/no_reply 调用审核与兜底重试机制
4. 在 Web 端提供 reply 手动测试、测试集管理、A/B 评估能力

另外
Dify 相关代码不再继续补 trace
后续应作为独立清理任务删除

---

# 一、执行优先级

## P0 必须优先完成

1. 表情包自动描述 JSON 容错解析
2. LLM API 日志写入 response_json / response_status / latency_ms
3. reply/no_reply 未调用时最多重试一次
4. Web 端能手动 dry-run 测试 reply 调用

## P1 第二阶段完成

1. ReplyContractCheckLog 审核日志表
2. AgentRun 详情页展示 reply 调用检查日志
3. Reply 测试集 CRUD
4. 测试集生成预览
5. baseline / prompt_only / code_retry 三组 A/B 评估

## P2 后续清理

1. 删除 Dify 相关代码
2. 清理旧配置
3. 清理旧测试
4. 给历史数据库做迁移或兼容

---

# 二、任务一：修复表情包自动描述失败

## 背景

日志示例

```text
[image_summary] << raw: {"image_count": 1, "overall_summary": "...", "per_image": [...]
[StickerMemory] auto describe failed id=744: Expecting ',' delimiter...
```

这说明 image_summary 已经返回了内容
失败点在 sticker_memory 自动描述阶段解析 JSON

当前问题是

```python
parsed = _parse_json_payload(raw)
```

只要模型返回半截 JSON
或者字符串里有未转义引号
或者尾部多文本
就会导致整张表情包打标失败

## 修改目标

表情包自动描述不能依赖严格 JSON
应该尽可能保住 description

即使 JSON 解析失败
也要 fallback 到 raw 文本摘要
不能直接 failed

## 修改文件

```text
core/sticker_memory.py
```

## 新增函数

```python
def _safe_parse_sticker_summary(raw: str) -> dict[str, Any]:
    """表情包描述专用容错解析。"""
```

逻辑顺序

1. 优先调用 image_summary.tool.\_parse_json_payload
2. 失败后尝试 EvolutionUtils.json_repair
3. 再失败用正则提取 overall_summary / summary / description
4. 还失败就清理 raw 文本并截取前 300 字作为 summary
5. 返回结构至少包含

```python
{
    "image_count": 1,
    "overall_summary": summary,
    "per_image": [{"index": 1, "summary": summary}],
    "keywords": [],
    "risk_flags": [],
    "confidence": "low",
    "_parse_fallback": True,
}
```

## 修改 describe_sticker_with_qwen

原逻辑

```python
parsed = _parse_json_payload(raw)
```

改成

```python
parsed = _safe_parse_sticker_summary(raw)
```

并将 raw[:2000] 放进 raw_summary

```python
"raw_summary": parsed | {"_raw_text": raw[:2000]}
```

## tags fallback

如果 keywords / objects / text 都为空
从 summary 中提取少量 token 作为 tags
避免表情包完全检索不到

```python
tags = _json_list(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,8}", summary)[:8])
```

## 修改 auto_describe_sticker

成功路径前检查 description 是否为空

如果为空

```python
row.describe_status = "failed"
row.describe_attempts += 1
row.describe_last_error = "empty description after parse"
return
```

不要空 description 也标 ok

## 测试

新增

```text
tests/test_sticker_memory_describe.py
```

覆盖

1. 正常 JSON 可解析
2. 代码块 JSON 可解析
3. 局部坏 JSON 但含 overall_summary 时仍能生成 description
4. 完全非 JSON 文本 fallback 为 description
5. description 为空时 auto_describe 不标 ok

---

# 三、任务二：LLM API 日志增加响应记录

## 背景

当前已经记录 request_json
但还没有把 response 写回同一条日志

要求不是新增一条 response log
而是同一条 LLMApiRequestLog 同时包含 request 和 response

## 修改文件

```text
core/database.py
core/tracing.py
clients/new_api_client.py
core/llm_sdk_tracing.py
clients/classifier_client.py
creatures/nanobot/prompts/skills/image_summary/tool.py
core/compaction.py
webui/src/App.jsx
api/admin_routes.py
```

## 数据库字段

在 LLMApiRequestLog 增加

```python
response_json = Column(Text, default="{}")
response_preview = Column(Text, default="")
latency_ms = Column(Integer, default=0)
finished_at = Column(DateTime, nullable=True)
```

保留已有字段

```python
response_status
status
error
```

## migration

新增 migration

```sql
ALTER TABLE llm_api_request_logs ADD COLUMN response_json TEXT DEFAULT '{}';
ALTER TABLE llm_api_request_logs ADD COLUMN response_preview TEXT DEFAULT '';
ALTER TABLE llm_api_request_logs ADD COLUMN latency_ms INTEGER DEFAULT 0;
ALTER TABLE llm_api_request_logs ADD COLUMN finished_at DATETIME;
```

如果项目没有严格 migration runner
也要保证启动 create_all 场景和已有库场景都能兼容

## 修改 LLMRequestTracer.record_request

现在 record_request 只插入不返回
需要改为返回 log_id

成功

```python
db.add(log)
db.commit()
db.refresh(log)
return int(log.id)
```

失败

```python
return 0
```

## 新增 LLMRequestTracer.finish_request

接口

```python
@staticmethod
def finish_request(
    *,
    log_id: int = 0,
    response: Any = None,
    response_status: int = 0,
    status: str = "success",
    error: str = "",
    latency_ms: int = 0,
) -> None:
```

行为

1. log_id 为空直接 return
2. 查询同一条 LLMApiRequestLog
3. 写入

```python
response_json
response_preview
response_status
status
error
latency_ms
finished_at
```

response_json 使用 max_chars=200000
response_preview 使用 max_chars=4000

## NewAPIClient.chat_completion

请求前

```python
started = time.time()
log_id = LLMRequestTracer.record_request(...)
```

成功时

```python
result = await resp.json()
LLMRequestTracer.finish_request(
    log_id=log_id,
    response=result,
    response_status=resp.status,
    status="success",
    latency_ms=int((time.time() - started) * 1000),
)
```

HTTP 非 2xx 时

```python
LLMRequestTracer.finish_request(
    log_id=log_id,
    response={"detail": detail[:4000]},
    response_status=resp.status,
    status="failed",
    error=last_error,
    latency_ms=...,
)
```

异常时

```python
LLMRequestTracer.finish_request(
    log_id=log_id,
    response={},
    response_status=0,
    status="error",
    error=str(e),
    latency_ms=...,
)
raise
```

## NewAPIClient.chat_completion_stream

也要 finish_request

推荐记录最终 assistant 文本和少量 chunks sample

```python
response={
    "content": "".join(text_parts),
    "chunks_sample": chunks[-20:],
}
```

不要无限保存所有 chunks

## OpenAI SDK tracer

文件

```text
core/llm_sdk_tracing.py
```

逻辑

1. create 前 record_request 得到 log_id
2. 调用原始 create
3. 如果返回普通 response
   - 转为可 JSON 序列化
   - finish_request success

4. 如果抛异常
   - finish_request error
   - 重新 raise

5. 如果返回 stream iterator
   - 可以先记录 status="stream_created"
   - 完整包装 stream iterator 后续再做

安全转换函数

```python
def _safe_sdk_response(result: Any) -> Any:
    if result is None:
        return {}
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "dict"):
        return result.dict()
    if isinstance(result, (dict, list, str, int, float, bool)):
        return result
    return {"repr": repr(result)[:4000]}
```

## 直接 HTTP 出口

以下路径请求前已经 record_request
现在需要成功/失败后 finish_request

```text
clients/classifier_client.py call_model_route
creatures/nanobot/prompts/skills/image_summary/tool.py _call_qwen
core/compaction.py call_compaction_llm
```

模式统一

```python
started = time.time()
log_id = LLMRequestTracer.record_request(...)
try:
    ...
    body = ...
    LLMRequestTracer.finish_request(
        log_id=log_id,
        response=body,
        response_status=200,
        status="success",
        latency_ms=...,
    )
except Exception as e:
    LLMRequestTracer.finish_request(
        log_id=log_id,
        status="error",
        error=str(e),
        latency_ms=...,
    )
    raise
```

## WebUI

AgentRun 详情 API 请求区域显示

```text
status
response_status
latency_ms
error
request_json
response_json
```

request_json 和 response_json 都用 details + pre 展开

## 测试

新增/补充

1. record_request 返回 id
2. finish_request 更新同一条记录
3. response_json 保存完整响应
4. error 路径写 status=error
5. NewAPIClient 成功时写 response_json
6. image_summary 直接 HTTP 成功时写 response_json

---

# 四、任务三：reply/no_reply 调用审核与兜底重试

## 背景

当前 reply/no_reply 工具位于

```text
creatures/nanobot/prompts/skills/reply/tool.py
```

输出通过 marker 识别

```text
NANOBOT_REPLY_OUTPUT
```

bridge 当前能识别

```text
reply_tool
no_reply_tool
structured_buffer_reply
structured_buffer_no_reply
no_tool_call
fake_tool_call_claim
```

但是现在 no_tool_call 会直接 suppress
不会重试

## 修改目标

如果模型没有调用 reply/no_reply
最多重试一次

重试逻辑必须在 bridge 中实现
不要放到 reply 工具里

## 触发条件

满足以下条件才重试

1. 没有真实 reply tool
2. 没有 no_reply tool
3. 没有合法 structured fallback
4. 不是 HTML 工具输出
5. 当前还没 retry 过

## retry prompt

固定内容

```text
你刚才没有调用 reply 或 no_reply 工具

原始输出如下
{{raw_model_output}}

这轮必须只调用一个工具

如果你原本想回复用户
请调用 reply(content=...)

如果你认为不该回复
请调用 no_reply(reason=...)

不要直接输出普通文本
```

raw_model_output 截断到 3000 字

## bridge 实现建议

在 no_tool_call suppress 前插入

```python
if agent_result in {"no_tool_call", "fake_tool_call_claim"} and reply_contract_retry_count < 1:
    retry_prompt = _build_reply_contract_retry_prompt(buffer_text or response)
    append correction to conversation
    reply_contract_retry_count += 1
    rerun model once
    continue
```

重试时不要把用户消息重复塞一遍
应该向当前 conversation 追加 correction system/user 消息
然后触发空事件或继续模型循环

## 不能做的事

1. 不允许无限重试
2. 不允许把普通文本直接发出去
3. 不允许在 reply 工具内部 retry
4. retry 后仍失败则保持 suppress

---

# 五、任务四：ReplyContractCheckLog 审核日志

## 新增表

```python
class ReplyContractCheckLog(Base):
    __tablename__ = "reply_contract_check_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String, index=True, default="")
    run_id = Column(String, index=True, default="")
    session_id = Column(String, index=True, default="")
    attempt = Column(Integer, default=0)
    raw_output_preview = Column(Text, default="")
    has_reply_tool = Column(Integer, default=0)
    has_no_reply_tool = Column(Integer, default=0)
    has_structured_fallback = Column(Integer, default=0)
    result = Column(String, index=True, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)
```

result 取值

```text
ok
retry
retry_success
suppressed
fake_tool_call_claim
no_tool_call
```

## 记录时机

每次模型尝试结束后
进入 reply extraction 阶段时记录

记录字段

```text
attempt
raw output preview
是否有 reply tool
是否有 no_reply tool
是否有 structured fallback
result
```

## AgentRun 详情返回

`/agent-runs/{run_id}` 返回新增字段

```json
{
  "reply_contract_check_logs": []
}
```

WebUI AgentRun 详情新增区域

```text
Reply 调用检查
```

显示

```text
attempt
result
raw_output_preview
has_reply_tool
has_no_reply_tool
has_structured_fallback
```

---

# 六、任务五：Web 端 Reply 手动测试工具

## 页面名称

```text
Reply 调用测试
```

## 入口位置

Admin WebUI 中与 AgentRun / Prompt 管理相邻

## 功能

手动构造一条消息
dry-run 执行 agent
查看模型是否调用 reply/no_reply
查看 retry 是否生效
查看最终结果
查看 LLM API request/response

## 表单字段

```text
chat_type group/private
session_id
sender_id
sender_name
character_name
message
recent_context
persona_text
variant baseline/prompt_only/code_retry
enable_reply_contract_retry
dry_run
```

默认

```text
dry_run=true
enable_reply_contract_retry=true
```

真实发送必须单独按钮
并明确提示会发送到真实会话

## 后端接口

```text
POST /api/admin/reply-test/run
```

请求示例

```json
{
  "message": "你在吗",
  "chat_type": "group",
  "session_id": "test-group-1",
  "sender_id": "123",
  "sender_name": "tester",
  "character_name": "凛音",
  "recent_context": "",
  "persona_text": "",
  "variant": "code_retry",
  "enable_reply_contract_retry": true,
  "dry_run": true
}
```

返回示例

```json
{
  "ok": true,
  "run_id": "...",
  "first_attempt": {
    "raw_output": "...",
    "called_reply": false,
    "called_no_reply": false
  },
  "retry_attempt": {
    "enabled": true,
    "raw_output": "...",
    "called_reply": true
  },
  "final": {
    "action": "reply",
    "content": "在\n怎么了?"
  },
  "metrics": {
    "reply_contract_ok": true,
    "retry_used": true
  },
  "llm_api_request_logs": []
}
```

---

# 七、任务六：测试集管理与 A/B 评估

## 新增表 ReplyEvalCase

```python
class ReplyEvalCase(Base):
    __tablename__ = "reply_eval_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, unique=True, index=True)
    title = Column(String, default="")
    chat_type = Column(String, default="group")
    input_text = Column(Text, default="")
    context_json = Column(Text, default="{}")
    expected_action = Column(String, default="any")
    expected_keywords_json = Column(Text, default="[]")
    forbidden_keywords_json = Column(Text, default="[]")
    source = Column(String, default="manual")
    tags_json = Column(Text, default="[]")
    enabled = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
```

expected_action 取值

```text
reply
no_reply
any
```

## 新增表 ReplyEvalRun

```python
class ReplyEvalRun(Base):
    __tablename__ = "reply_eval_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, default="")
    variant = Column(String, default="")
    total = Column(Integer, default=0)
    reply_contract_ok = Column(Integer, default=0)
    retry_used = Column(Integer, default=0)
    passed = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    summary_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now, index=True)
```

## 新增表 ReplyEvalResult

```python
class ReplyEvalResult(Base):
    __tablename__ = "reply_eval_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, index=True)
    case_id = Column(String, index=True)
    variant = Column(String, default="")
    expected_action = Column(String, default="")
    actual_action = Column(String, default="")
    called_reply_or_no_reply = Column(Integer, default=0)
    retry_used = Column(Integer, default=0)
    passed = Column(Integer, default=0)
    raw_output_preview = Column(Text, default="")
    final_content_preview = Column(Text, default="")
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)
```

## 测试集 API

```text
GET    /api/admin/reply-eval/cases
POST   /api/admin/reply-eval/cases
PUT    /api/admin/reply-eval/cases/{id}
DELETE /api/admin/reply-eval/cases/{id}

POST /api/admin/reply-eval/generate-preview
POST /api/admin/reply-eval/save-generated

POST /api/admin/reply-eval/run
GET  /api/admin/reply-eval/runs
GET  /api/admin/reply-eval/runs/{id}
```

## 测试集生成

先用规则生成
不要上 LLM

生成类型

```text
被叫到
直接问题
普通闲聊
情绪低落
技术求助
信息不足
纯哈哈哈
别人在和别人说话
半句话
身份试探
生活场景邀请
```

每类 3 到 5 条
默认 40 到 60 条

生成流程

```text
generate-preview
→ Web 端预览
→ 勾选/编辑
→ save-generated
```

不能生成后直接启用写入

## A/B 评估 variant

```text
baseline
当前 prompt
retry disabled

prompt_only
实验 prompt
retry disabled

code_retry
当前 prompt
retry enabled
```

## 评估指标

```text
reply_call_rate
valid_action_rate
expected_action_accuracy
retry_used_rate
retry_success_rate
no_tool_call_rate
fake_tool_claim_rate
empty_output_rate
```

## WebUI 展示

测试集页面

```text
case_id
title
input_text
expected_action
tags
enabled
编辑/删除
```

生成预览页面

```text
可勾选
可编辑
保存选中
```

评估结果页面

```text
variant | total | reply_call_rate | expected_action_accuracy | retry_used_rate | retry_success_rate | no_tool_call_rate | fake_tool_claim_rate
```

每条 result 可展开查看

```text
raw_output_preview
final_content_preview
error
AgentRun 链接
LLM API logs
```

---

# 八、Dify 清理任务

不要继续给 Dify 补 trace
Dify 应作为独立删除任务

## 删除范围

```text
clients/dify_client.py
legacy_adapter 中 provider_type == "dify" 分支
config.py 中 DIFY_* 配置
相关文档
相关测试
```

## 兼容处理

如果旧配置里 provider_type=dify
启动时给出明确错误

```text
Dify provider has been removed. Please migrate to NewAPI/OpenAI-compatible route.
```

或者提供一次性迁移脚本

---

# 九、最终验收标准

## 表情包

1. 损坏 JSON 不再导致 auto_describe failed
2. 至少能写入 description
3. description 为空不会标 ok
4. meta 中保留 raw_summary 和 raw_text 便于排查

## LLM API 日志

1. 每条请求有 request_json
2. 每条成功响应有 response_json
3. 同一 row 里有 request 和 response
4. headers 脱敏
5. response_status / latency_ms / finished_at 正确
6. AgentRun 详情可展开查看 request_json 和 response_json

## Reply 审核

1. 正常 reply tool 不触发 retry
2. 正常 no_reply tool 不触发 retry
3. 第一次无工具调用时触发一次 retry
4. retry 成功后最终发送 reply 或 no_reply
5. retry 失败后 suppress
6. fake_tool_call_claim 能识别并记录
7. ReplyContractCheckLog 能在 AgentRun 详情看到

## Web 手动测试

1. 能 dry-run 单条消息
2. 能看到 first_attempt / retry_attempt / final
3. 能看到 LLM API request/response
4. dry-run 不真实发群消息
5. 真实发送必须单独确认入口

## 测试集与 A/B

1. Web 可 CRUD 测试集
2. 规则生成可预览
3. 预览后才保存
4. baseline / prompt_only / code_retry 可运行
5. 能输出 reply 调用率和预期动作准确率

---

# 十、建议提交顺序

## Commit 1

修复 sticker_memory 容错解析

```text
fix(sticker): tolerate broken image_summary JSON in auto describe
```

## Commit 2

LLM API 日志增加 response 记录

```text
feat(trace): persist llm api responses with request logs
```

## Commit 3

reply/no_reply 兜底重试和审核日志

```text
feat(reply): add reply contract retry and audit logs
```

## Commit 4

Web 手动测试工具

```text
feat(admin): add reply contract manual test page
```

## Commit 5

测试集和 A/B eval

```text
feat(eval): add reply contract eval cases and comparison runs
```

## Commit 6

Dify 清理

```text
chore: remove dify provider legacy code
```

---

# 十一、给开发 agent 的压缩版任务提示词

```md
你要完成 Nanobot 最近需求的综合改造
请按阶段提交
不要一次性大爆炸修改

阶段 1 修复表情包自动描述

- core/sticker_memory.py 增加 \_safe_parse_sticker_summary
- describe_sticker_with_qwen 使用容错解析
- JSON 坏掉时 fallback 到 summary/raw text
- description 为空不能标 ok
- 补测试

阶段 2 完善 LLM API 日志

- LLMApiRequestLog 增加 response_json/response_preview/latency_ms/finished_at
- record_request 返回 log_id
- 新增 finish_request 更新同一 row
- NewAPIClient 非流式和流式都写 response
- OpenAI SDK tracer 写 response
- classifier/image_summary/compaction 直接 HTTP 出口写 response
- WebUI AgentRun 详情展示 request 和 response
- 补测试

阶段 3 reply/no_reply 运行时兜底

- bridge 中 no_tool_call/fake_tool_call_claim 不再直接 suppress
- 第一次失败时追加 correction prompt 重试一次
- retry 后仍失败才 suppress
- 不允许普通文本直接发出
- 新增 ReplyContractCheckLog
- AgentRun 详情返回并展示审核日志
- 补测试

阶段 4 Web 手动测试

- 新增 /api/admin/reply-test/run
- WebUI 新增 Reply 调用测试页面
- 支持 dry-run
- 展示 first_attempt/retry_attempt/final/LLM logs
- 真实发送必须单独按钮

阶段 5 测试集和 A/B eval

- 新增 ReplyEvalCase/ReplyEvalRun/ReplyEvalResult
- Web 可查看/新增/编辑/删除测试集
- 规则生成测试集必须先 preview
- 支持 baseline/prompt_only/code_retry 三组评估
- 输出 reply_call_rate/expected_action_accuracy/retry_success_rate 等指标

阶段 6 Dify 清理

- 删除 Dify 相关 provider/client/config/tests
- 旧配置给明确错误或迁移提示

注意

- 不要再给 Dify 补 trace
- 不要把 response 另插一条日志
- request 和 response 必须在同一条 LLMApiRequestLog
- reply retry 只能最多一次
- retry 逻辑放 bridge 不放 reply 工具
- Web 测试默认 dry-run
```
