# Prompt V2 默认接管设计

> 2026-06-17 · 第一阶段目标是让 V2 成为默认 live prompt 路径，同时保留显式 V1 回滚入口，不在本阶段删除 legacy 资产。

---

## 背景

`prompt_manifest.json` 已声明 `active_engine = "v2"`，但当前真实运行默认仍是 `prompt_runtime.engine = "v1"` + `prompt_system.mode = "shadow"`。这意味着默认主回复实际发送 legacy prompt，managed prompt 只做 shadow 对比，V2 只有在 metadata override 或显式配置 `prompt_runtime.engine=v2` 时才进入。

当前入口由 `NanobotBridge.handle_message()` 解析：

- metadata override 优先：`prompt_runtime_engine_override` / `prompt_engine_override`
- 否则读取 settings：`prompt_runtime.engine`
- 当前 fallback 为 `v1`

V2 编译链路已经存在：`nanobot_kt/prompt_runtime.py` 会在 `prompt_engine == "v2"` 时调用 `core.prompt_v2.compiler.compile_prompt_plan(..., strict_audit=True)`。但 `bootstrap/prompt_runtime.py` 目前只初始化 managed prompt 和 legacy prompt runtime 目录，没有初始化 `data/prompts_v2`。

---

## 目标

本阶段只完成「默认接管」：

1. 默认 live prompt engine 改为 V2。
2. V2 runtime 目录 `data/prompts_v2` 在启动时初始化或检查。
3. Admin effective preview 默认使用 V2。
4. Reply Test 默认使用 V2 路径。
5. 保留显式 V1 回滚：`NANOBOT_PROMPT_ENGINE=v1`、DB setting 或 metadata override 仍能切回 V1。
6. 记录 DB setting 覆盖风险，避免误以为只改代码默认值即可覆盖线上已有配置。

---

## 非目标

本阶段不做以下工作：

- 不删除 `core/prompt_assembler.py`、`core/legacy_prompt_runtime.py`、`prompts.default/`、`prompts.legacy.default/` 或 `creatures/nanobot/prompt.md`。
- 不移除 `fallback_v1` 策略，只把它作为后续收口项。
- 不迁移 classifier / legacy adapter 仍使用的 `core.prompt_runtime` 任务 prompt。
- 不做 `prompt_v2` / `prompts.v2.default` / API 路径去版本化。
- 不拆 `handle_message` 的模型路由和 reply contract 大块；只允许补最小 helper 或测试以支撑默认接管。

---

## 方案比较

### 方案 A：直接删除 V1 / legacy

优点：最终状态最干净。

缺点：风险过高。`prompts.default/` 仍被 classifier / legacy adapter 间接使用，admin legacy endpoint 和 `creatures/nanobot/config.yaml` 仍引用旧资产；一次性删除会把运行路径、管理面、测试和文档全部卷入同一提交。

结论：不采用。

### 方案 B：V2 默认接管，V1 显式回滚

优点：最小修复当前口径矛盾。默认请求进入 V2，但保留 `NANOBOT_PROMPT_ENGINE=v1`、DB setting 和 metadata override 回滚能力；不删除旧资产，便于定位线上差异。

缺点：短期仍保留三套资产，不能宣称“唯一 V2”已完全完成。

结论：采用。它是本阶段的目标。

### 方案 C：先只加诊断，不改默认

优点：风险最低。

缺点：不能解决 manifest active 与 live 默认路径不一致的问题，只能继续发现问题。

结论：不足以完成 P1 起点，不采用。

---

## 设计

### 默认 engine

`prompt_runtime.engine` 的配置默认值改为 `v2`。`NanobotBridge._prompt_runtime_engine()` 在读取 settings 失败或读到非法值时也回落到 `v2`，避免异常时静默走 V1。

显式回滚仍保留：

- `NANOBOT_PROMPT_ENGINE=v1`
- `SystemSetting(key="prompt_runtime.engine", value_json="v1")`
- metadata override：`prompt_runtime_engine_override="v1"` 或 `prompt_engine_override="v1"`

### DB setting 覆盖诊断

settings 读取顺序是 DB > env > default。只改 `core/config_registry.py` 默认值无法覆盖线上已经写入 DB 的 `prompt_runtime.engine=v1`。

本阶段增加轻量诊断，不直接改 DB：

- 启动初始化时读取当前有效 `prompt_runtime.engine`。
- 如果当前有效值仍是 `v1`，记录 warning，明确说明 V1 是由 DB / env / override 保留的显式回滚状态。
- 不在启动时自动迁移 DB，避免无提示改变线上行为。

### V2 runtime 目录初始化

`bootstrap/prompt_runtime.py` 增加 V2 runtime 初始化：

- 调用 `core.prompt_v2.template_registry` / `template_store` 中现有能力，把 `prompts.v2.default` 初始化到 `data/prompts_v2`。
- 如果现有 API 只能按单模板初始化，则在 `bootstrap` 中调用已存在的 runtime 目录 helper；若没有现成 helper，本阶段新增一个小的 `init_prompt_v2_runtime_dir()`，职责只做复制缺失默认模板，不覆盖用户修改。
- 启动日志输出 copied 数量、source dir 和 runtime dir。

### Admin 默认值

`EffectivePromptPreviewRequest.engine` 默认从 `v1` 改为 `v2`。这让 admin effective preview 的默认行为与 live 默认路径一致。

`ReplyTestRunRequest.prompt_engine` 默认从 `v1` 改为 `v2`，`variant` 默认从 legacy 风格的 `code_retry` 改为 `v2_code_retry`。旧 variant 仍继续兼容：

- `baseline` / `prompt_only` / `code_retry` 保留 V1 语义，用于历史对比。
- `v1_baseline`、`v2_prompt_only`、`v2_code_retry` 继续显式表达新旧路径。

### Bridge 行为

默认无 override 时，bridge 应调用 V2 compiler，且不调用 `PromptAssembler`。

非法 engine 值回落到 V2。显式 V1 时仍走旧路径。V2 audit 失败仍按当前 `prompt_runtime.v2_audit_failure_policy` 处理，本阶段不改变 fail-fast / fallback_v1 策略。

### H29 协同边界

本阶段不拆大段 `handle_message`。但测试和后续计划会把第一步 H29 重构定义为「提取 prompt/runtime 请求组装」。这与默认 V2 接管协同，因为后续删除 V1 live 分支时可以集中修改 prompt runtime 边界。

---

## 验收标准

- [ ] 无 metadata override、无 env override、无 DB override 时，`NanobotBridge` 默认调用 V2 compiler。
- [ ] 显式 `prompt_runtime_engine_override="v1"` 时仍走 V1 路径。
- [ ] 非法 engine 设置回落到 V2，而不是 V1。
- [ ] `prompt_runtime.engine` 注册默认值为 `v2`。
- [ ] `EffectivePromptPreviewRequest.engine` 默认值为 `v2`。
- [ ] `ReplyTestRunRequest.prompt_engine` 默认值为 `v2`，默认 variant 为 `v2_code_retry`。
- [ ] 启动初始化会准备 `data/prompts_v2`，且不覆盖已有 runtime 修改。
- [ ] 若有效 engine 仍为 `v1`，启动日志明确提示这是显式回滚状态。
- [ ] 现有 V2 bridge、preview、reply-test 测试通过。
- [ ] 全量测试通过。

---

## 测试计划

新增或更新测试：

- `tests/test_bridge_prompt_v2.py`
  - 无 override 默认走 V2。
  - metadata override 为 V1 时仍可回滚。
  - 非法 engine 值回落 V2。
- `tests/test_prompt_manifest.py`
  - manifest active engine 与 config registry 默认值一致。
- `tests/test_prompt_v2_template_registry.py` 或相邻测试
  - V2 runtime 初始化复制缺失模板，不覆盖已有文件。
- `tests/test_prompt_trace_admin.py`
  - effective preview 默认 engine 为 V2。
- `tests/test_reply_admin.py`
  - reply-test 默认 prompt engine / variant 使用 V2。

验证命令：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_bridge_prompt_v2.py tests/test_prompt_manifest.py tests/test_prompt_v2_template_registry.py tests/test_prompt_trace_admin.py tests/test_reply_admin.py -q -p no:cacheprovider
python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

---

## 后续阶段

本阶段完成后再进入后续收敛：

1. 提取 `handle_message` 的 prompt/runtime 请求组装 helper。
2. 禁用 live `fallback_v1` 发送路径。
3. 管理面和评估面改为 V2-only，legacy 页面降级为只读迁移入口。
4. 迁移 classifier / legacy adapter 的任务 prompt。
5. 删除 V1 / legacy 资产。
6. 去掉 V2 命名后缀，统一为无版本运行时。

### P1-5 补充（2026-06-17）

P1-5 已完成前 3 项收口：`fallback_v1` 已从 live 发送路径移除，V2 audit 失败统一 fail-fast；`reply-test` / `reply-eval` 默认和旧 alias 已转向 V2；legacy / managed 管理写入口已降级为只读迁移入口。显式 `prompt_runtime.engine=v1` 应急回滚和旧资产删除延后到 P1-6 前置迁移后处理。
