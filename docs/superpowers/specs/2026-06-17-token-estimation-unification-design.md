# Token 估算统一设计

## 背景

项目里存在多套 token 估算：

- `core.context_builder.estimate_tokens`：CJK 记 1.0，其他字符记 0.35。
- `app.session_memory.windowing.estimate_tokens`：与 context builder 类似。
- `core.prompts.manager._estimate_tokens`：CJK 记 1.0，ASCII 记 0.35，其他非 ASCII 记 0.8。
- `core.prompt_v2.section_renderer.estimate_tokens`：与 prompt manager 类似。
- `api.admin_routes._prompt_metrics`：内联同一套 prompt manager 公式。
- `core.legacy_adapter.PromptAuditorAgent._estimate_tokens`：按 `len(text) // 2` 粗估。

这些差异会导致同一段包含 emoji、全角符号或非 CJK 非 ASCII 文本的内容，在
不同模块得到不同预算判断。

## 目标

- 新增单一入口 `core.token_utils.estimate_tokens()`。
- 统一采用现有更完整的三段公式：CJK 记 1.0，ASCII 记 0.35，其他非 ASCII
  记 0.8。
- 保留旧模块导出的函数名，降低调用方迁移风险。
- 为 emoji / 全角符号 / CJK / ASCII 混合文本补单元测试。

## 非目标

- 不引入 tiktoken 或真实 tokenizer。
- 不调整各业务模块的 token 上限。
- 不重构 prompt / session memory 的窗口算法。

## 方案

新增 `core/token_utils.py`：

- `is_cjk_char(ch)`：判断 CJK 统一表意文字基本区。
- `estimate_tokens(text)`：空文本返回 `0`；CJK `1.0`，ASCII `0.35`，
  其他字符 `0.8`，最终取 `int()`。

迁移方式：

- `core.context_builder` 从 `core.token_utils` import 并 re-export
  `estimate_tokens`。
- `app.session_memory.windowing` 同样 import 并保留 `estimate_tokens` 名称。
- `core.prompts.manager._estimate_tokens` 改为包装共享 helper。
- `core.prompt_v2.section_renderer` import 共享 helper，删除本地实现。
- `api.admin_routes._prompt_metrics` 使用共享 helper。
- `PromptAuditorAgent._estimate_tokens` 改为 `max(1, estimate_tokens(text))`，
  保留该审计路径的非空最小值语义。

## 测试策略

- 新增 `tests/test_token_utils.py`，覆盖空字符串、CJK、ASCII、emoji 和混合文本。
- 增加一致性测试，断言上述旧入口对同一混合文本返回相同结果。
- 运行 token 相关目标测试，再运行完整测试集。

## 风险与兼容性

对 ASCII 和 CJK 的估算不变；主要变化是其他非 ASCII 字符从部分模块的 `0.35`
变为 `0.8`，避免低估 emoji、全角符号和非中文 Unicode 文本。该变更可能让少数
包含大量符号的内容更早触发截断，但方向更保守。
