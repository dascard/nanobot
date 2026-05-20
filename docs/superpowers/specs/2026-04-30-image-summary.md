# 图片摘要工具设计

## Context

当前多模态链路已经支持把图片原样送入模型，但还没有一个显式的“图片摘要”能力。
对于 OCR、版面分析、图片归档等场景，直接让主对话模型反复看图会增加延迟，也不利于复用。

## Design

### 目标

- 提供一个可显式调用的 `image_summary` 工具
- 工具直接调用本地部署的 Qwen 视觉模型生成结构化摘要
- 该 Qwen 同时也是私聊输入意图分类所用的模型，只是这里使用多模态输入和更宽松的输出长度

### 工具行为

输入：
- `files`: 图片 URL 列表
- `focus`: 可选的摘要侧重点，例如 `OCR`、`人物`、`场景`、`风险`

输出：
- 严格 JSON
- 包含整体摘要、单图摘要、关键词、风险提示、置信度

### 模型选择

- 不再走模型注册表路由
- 直接调用本地 Qwen 视觉模型接口
- 通过配置项控制 `max_tokens` / `temperature` / `top_p` / `timeout`

### 主提示词

- 更新 `creatures/nanobot/prompt.md`
- 新增 `image_summary` 工具说明
- 明确：如果模型本身能直接识图，优先给出结构化摘要；需要更稳妥的 OCR 或归档时再调用工具

### 输出体验

- 工具执行期间给 QQ 端一个简短进度提示
- 进度提示需要能和现有自动撤回机制兼容

## Files to Modify

- `creatures/nanobot/prompts/skills/image_summary/tool.py`
- `nanobot_kt/tools/image_summary.py`
- `creatures/nanobot/config.yaml`
- `creatures/nanobot/prompt.md`
- `config.py`
- `nanobot_kt/output.py`
- `tests/test_kt_framework.py`
- `tests/test_image_summary_tool.py`

## Verification

1. `python -m pytest tests/test_image_summary_tool.py -v`
2. `python -m pytest tests/test_kt_framework.py -v`
3. `python -m pytest tests/ -v`
