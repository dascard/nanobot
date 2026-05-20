# 私聊拦截层设计

## Context

私聊消息分三类：对话、数据中转、注入攻击。
群聊中需禁用文件操作工具，防止任意群成员操作服务端文件。

核心威胁：**存储型注入**——攻击消息落库后被模型查询到，在当前对话中被执行。

## Design

### 数据流

```
qqbot 端                                    nanobot 端
  │                                            │
  ├─ 私聊消息到达，等待窗口 3s                      │
  ├─ POST /api/v1/chat ─────────────────────→  │
  │                                            ├─ Guardrail.classify()
  │                                            │   L1: 输入清洗 + 注入检测
  │                                            │   L2: Qwen 分类
  │                                            │   L3: 输出格式校验
  │                                            │   L4: 超时 5s
  │                                            │
  │                                            ├─ 落库策略（防存储注入）:
  │                                            │   ChatLog: 始终写入（审计）
  │                                            │     injection → processed=-1
  │                                            │   ConversationTurn:
  │                                            │     normal → 写入
  │                                            │     injection → 占位标记
  │                                            │
  │  ←──────────────────────────────────────── ├─ injection → {status: "mock"}
  │  ←──────────────────────────────────────── ├─ silent → {status: "silent"}
  │  ←──────────────────────────────────────── ├─ reply → KT Agent 回复
  │                                            │
  ├─ mock → 嘲讽回复（不含原消息内容）                │
  ├─ silent → 不回复                              │
  ├─ reply → 拆分 + 发送                           │
```

### 防御层次

#### Layer 1: 输入清洗
- 正则快速扫描已知注入模式 → 匹配即标记
- 长度限制、控制字符过滤、换行规范化

#### Layer 2: Qwen 分类
- 判断：是/否 + 复杂度 1-10
- 输出格式：`是,N` 或 `否,N`（中文逗号）
- max_tokens=30, temperature=0

#### Layer 3: 输出格式校验
- 正则：`^(是|否)[,，]\d+$`
- 格式不匹配 → 标记 injection（正常模型不会违反格式）
- type="否" 且 complexity>2 → 模型混乱，仍不回复

#### Layer 4: 超时兜底
- >5s → 标记 injection

### 存储型注入防护

消息落库前过 guardrail，注入消息不进入对话上下文：

| 表 | injection | normal |
|----|-----------|--------|
| ChatLog | 写入（processed=-1，审计用途） | 写入 |
| ConversationTurn | 替换为 `[安全提示: 检测到注入已被拦截]` | 正常写入 |

后续模型通过 sql_analysis 查询 ChatLog 仍可看到原文（审计需求），
通过 ConversationTurn 的上下文注入永远看不到注入原文。

### 群聊文件工具禁用

`proxy_chat()` 中判断 `is_group_chat` → bridge 传标志 → KT Agent 过滤工具列表：
禁用 `read/write/edit/grep/glob/bash`。私聊不受影响。

### 嘲讽模式约束

- 不引用用户原消息内容（防二次注入）
- 不展示攻击细节
- 温和嘲讽即可

## Files to Modify

| 文件 | 改动 |
|------|------|
| `config.py` | + `CLASSIFIER_API_URL`, `CLASSIFIER_TIMEOUT` |
| `clients/classifier_client.py` | 新增：Guardrail 四层防御 |
| `api/routes.py` | 注入落库策略 + 群聊禁用文件工具 + 嘲讽模式 |
| `nanobot_kt/bridge.py` | 群聊工具过滤 |
| `QQbot/src/plugins/chat.py` | 等待窗口 + 合并消息 |
| `tests/test_classifier.py` | 新增 |

## Verification

1. `python -m pytest tests/test_classifier.py -v` — guardrail 测试
2. `python -m pytest tests/ -v` — 完整回归，0 failures
3. 私聊注入 → 嘲讽回复，原文不入 ConversationTurn
4. 后续查 sql_analysis 不会被历史注入污染
5. 群聊 read/write/bash → 工具不可用
6. Qwen 返回非法格式 → 标记 injection，落库用占位标记
