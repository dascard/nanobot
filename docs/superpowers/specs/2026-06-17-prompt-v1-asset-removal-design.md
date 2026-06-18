# Prompt V1 资产删除与去版本化设计

> 2026-06-17 · P1-6 目标是在 P1-5 已完成 legacy 收口后，迁移仍依赖旧模板的后台任务，删除 V1 / legacy 冗余资产，并为后续无版本命名收敛铺路。

> 2026-06-18 更新：P1-6 任务 1-7 已完成。旧任务 prompt 已迁移到 task template，V1 live 分支、legacy 管理面和旧资产已下线或删除；主输出、配置默认值、admin API、WebUI 主入口和 trace 口径已收敛到无版本 `prompt` / `Prompt Runtime`。物理目录 `prompts.v2.default`、`data/prompts_v2`、内部包名 `core.prompt_v2`、旧 `/prompt-v2/*` API、`v2_code_retry` / `v2_prompt_only` variant 与 `prompt_v2_audit_failed` 作为兼容边界保留。

---

## 背景

P1-5 已把默认 live 主回复、reply-test / reply-eval 和 legacy / managed 管理写入口收口到 V2-only 或只读迁移状态。当前剩余债务集中在三个方向：

- 后台任务仍可能通过旧 `core.prompt_runtime` 读取 `prompts.default/`：`clients/classifier_client.py` 的 `classifier_legacy` 和 `core/legacy_adapter.py` 的 `memory_extract`。
- V1 / legacy 资产仍存在：`core/prompt_assembler.py`、`core/legacy_prompt_runtime.py`、`prompts.default/`、`prompts.legacy.default/`、`creatures/nanobot/prompt.md`、`scripts/build_nanobot_prompt.py`、`data/prompts/`、`data/prompt_fragments/`、`data/runtime_prompt/prompt.md`。
- V2 仍以版本化命名暴露：`prompts.v2.default`、`data/prompts_v2`、`prompt_v2` API、`Prompt Runtime V2` trace source、`v2_*` prompt key / eval variant。

`docs/todo.md` 路线项 1 的最终目标是只保留一套模板和一条 compile 路径。P1-6 不能再扩大旧路径，也不能一次性删除导致历史 trace、迁移读取和测试夹具全部失效。

## 目标

1. 将仍依赖旧 `PromptManager` 的后台任务 prompt 迁移到 V2 task template。
2. 让主运行时不再构造或加载 V1 prompt，显式 `engine=v1` 不再作为 live 主回复路径。
3. 删除不再被 live、迁移读取或测试夹具需要的 V1 / legacy 代码和模板资产。
4. 保留历史数据读取兼容：旧 `AgentRun`、`PromptRenderLog`、`ChatLog.meta_json` 中的 `prompt_mode` / `prompt_source` / `prompt_key` 仍可展示。
5. 将「V2」从新主路径命名中逐步降级为兼容别名，避免未来继续把当前唯一引擎称为 V2。

## 非目标

- 不在同一阶段引入 platform × chat_type 模板维度；这是路线项 9。
- 不重构模型路由、reply contract 或 SSE 流式协议。
- 不清理所有历史字段值；历史读取必须继续识别 `legacy`、`managed`、`v1`、`v2`。
- 不删除 V2 模板编辑能力；本阶段只处理旧 V1 / legacy 资产。
- 不把 `classifier_legacy` 模型路由名称强行改名；它属于模型路由配置兼容，先只迁移它的 prompt 来源。

## 方案比较

### 方案 A：一次性删除旧资产

优点：最终状态最干净，代码量下降快。

缺点：风险过高。`creatures/nanobot/config.yaml` 仍声明 `system_prompt_file: prompt.md`，历史 trace 和测试仍引用旧字段，后台任务还没有全部切换到 V2 task template。一次性删除会把运行、管理、测试和文档全部卷进一个难以审查的提交。

结论：不采用。

### 方案 B：先迁移 live 后台任务，再删除资产

优点：每一步都有明确红绿测试。先让 live 路径不再依赖旧模板，再删除资产；历史读取兼容可以独立测试。

缺点：短期内还会保留一部分旧文件，必须靠文档和守卫测试防止新代码继续引用它们。

结论：采用。

### 方案 C：只改命名，不删除旧资产

优点：对运行风险最小。

缺点：不能解决三套模板和 legacy 代码继续存在的问题，H29 的 prompt 分支复杂度仍保留。

结论：不足以完成 P1-6，不采用。

## 设计

### 0. 2026-06-18 实施状态

已完成：

- 后台任务 prompt 来源已迁移到 task template：`classifier_legacy`、`private_decision`、`timing_gate`、`memory_extract` 不再走旧 `core.prompt_runtime`。
- 主回复 live 路径不再构造或回退到 V1 / legacy prompt；旧 `v1` 和 `v2` engine 输入会归一到 canonical `prompt`。
- 已删除旧 live tree 中的 V1 / legacy 模块、旧模板目录、`creatures/nanobot/prompt.md` 和旧构建脚本。
- 已新增 canonical admin API：`/api/v1/admin/prompt/templates`、`/prompt/flow`、`/prompt/variables`；旧 `/prompt-v2/*` 继续作为兼容 alias。
- WebUI 主入口已切到 `/prompt-templates`，旧 `/prompt-v2-templates` 作为 redirect alias。
- 新写入的 engine、prompt mode、prompt source 使用 `prompt` / `Prompt Runtime`；历史 trace / eval 仍兼容旧 `v1` / `v2` 值。
- 默认模板 frontmatter 用户可见标题和描述已去掉 `V2` 主标签。

本阶段特意保留：

- 物理目录 `prompts.v2.default`、`data/prompts_v2`、`data/prompts_v2_history`。
- 内部包名与测试命名中的 `core.prompt_v2` / `PromptV2*`。
- 兼容 API `/prompt-v2/*`、兼容输入 `engine=v2` / `prompt_engine=v2`。
- 兼容 variant `v2_code_retry`、`v2_prompt_only`。
- 历史字段 / 哨兵 `prompt_v2_audit_failed` 和旧 trace 中已有的 `prompt_mode=v2`。

### 1. 后台任务 prompt 迁移

`core.prompt_runtime.render_model_messages()` 和 `render_prompt_content()` 继续从 `core.prompts.PromptManager` 读取 `prompts.default/`。P1-6 应先消除 live 调用方：

- `clients/classifier_client.call_model_route()`：当 `route_key` 为 `timing_gate`、`private_decision`、`classifier_legacy` 时，不再通过 `render_model_messages()` 读取旧 PromptManager。改为加载 V2 task template 并按变量渲染。
- `core/legacy_adapter.LegacyEvolutionAdapter.evolve()`：`memory_extract` 不再通过 `render_prompt_content()` 读取旧模板。改为加载 `tasks/memory_extract`，并用 `conversation`、`existing_memory` 等变量渲染。

当前 `prompts.v2.default/tasks/memory_extract.md` 已存在，但它只是占位文案；实现时需要把旧 `prompts.default/memory_extract.md` 中仍有价值的格式、输出约束和证据要求迁移过去。`classifier_legacy` 目前没有 V2 task 模板，建议新增 `tasks/classifier_legacy.md`，先保持原输出契约不变。

### 2. 主回复运行时收敛

`nanobot_kt.prompt_runtime.build_prompt_runtime()` 当前仍保留 `input.prompt_engine != "v2"` 时调用 `_build_v1_prompt()` 的显式回滚路径。P1-6 后，主回复 live 路径只允许 V2 编译：

- `NanobotBridge._resolve_prompt_runtime_engine()` 对 `v1`、非法值和旧 metadata override 统一归一为 canonical engine。
- `build_prompt_runtime()` 删除 `_build_v1_prompt()` live 分支，或让非 canonical engine 直接抛出明确错误。
- `PromptRuntimeInput.prompt_engine` 在新代码里使用无版本 canonical 值；旧 `v2` 作为兼容输入别名。

这一步不删除数据库字段 `prompt_mode`。新写入统一为 canonical 值，历史读取继续显示旧值。

### 3. 管理面与迁移出口

P1-5 已把 legacy / managed 写接口返回 410，且 WebUI legacy 页面只读化。P1-6 有两种选择：

- 短期保留只读导出页：`GET /prompt`、`GET /prompt/fragments`、`GET /prompts` 用于人工迁移和历史比对。
- 删除旧页面和路由：仅当后台任务迁移完成、`data/prompts*` 差异已导出、测试不再依赖旧 GET 后执行。

设计采用先保留只读导出，再删除写代码。删除旧页面时必须保留迁移说明，避免用户不知道运行时覆盖文案去了哪里。

### 4. 资产删除顺序

删除顺序必须由引用清点驱动：

1. 先删除 `core.prompt_runtime` 的 live 调用方，再删除该模块。
2. 先移除 `creatures/nanobot/config.yaml` 对 `system_prompt_file: prompt.md` 的依赖，再删除 `creatures/nanobot/prompt.md`。
3. 先删除或改写 `tests/test_prompt_contract.py` 对 `scripts/build_nanobot_prompt.py` 的依赖，再删除构建脚本。
4. 先迁移 `prompts.default/` 中仍被后台任务使用的模板，再删除目录。
5. 先保留最小测试 fixture，再删除 `prompts.legacy.default/` 和 `data/prompt_fragments/` 的生产依赖。

每个删除步骤前必须运行 `rg` 守卫，确认剩余引用只属于历史文档、迁移说明或明确的测试 fixture。

### 5. 去版本化命名

去版本化是兼容迁移，不是简单搜索替换。建议分两层：

- 对外 canonical 名称：engine、prompt mode、trace source、admin API 和 WebUI 主入口使用无版本 `prompt` / `Prompt Runtime` / `/prompt/*` / `/prompt-templates`。
- 兼容别名：旧 `prompts.v2.default`、`data/prompts_v2`、`/prompt-v2/*`、`v2_code_retry`、`v2_prompt_only`、`prompt_engine=v2` 在过渡期继续可读；新写入的运行时输出使用无版本名称。

由于旧 `prompts.default/` / `data/prompts/` 仍有历史运行时数据语义，本阶段不物理重命名目录。若后续要迁移到 `prompts.default` / `data/prompts`，必须单独设计、导出旧运行时覆盖并单独验证。

### 6. 历史兼容

以下内容不作为删除依据：

- `AgentRun.prompt_mode`、`PromptRenderLog.mode`、历史 `meta_json` 中的旧字段值。
- 文档中描述已完成历史阶段的 `v1` / `v2` 字样。
- 测试夹具中用于验证历史兼容的最小 legacy 样本。

这些引用应加白名单或命名为 `legacy_history`，避免后续守卫测试误报。

## 验收标准

- [x] `classifier_legacy` 和 `memory_extract` 不再调用 `core.prompt_runtime` 或 `core.prompts.PromptManager`。
- [x] 主回复 live 路径不再调用 `PromptAssembler` 或 `core.legacy_prompt_runtime`。
- [x] `creatures/nanobot/config.yaml` 不再要求 `prompt.md` 存在。
- [x] 删除旧资产后，`rg` 守卫证明剩余 V1 / legacy 引用只在历史兼容、迁移说明或测试 fixture 白名单内。
- [x] 新主路径使用无版本 canonical 命名；旧 `v2` 名称仅作为兼容别名存在。
- [x] Admin / WebUI 不暴露旧 PromptManager 或 legacy fragment 写入口。
- [x] 旧 trace 和旧 eval 报告仍可展示，不因字段改名崩溃。
- [x] P1-6 定向测试、prompt runtime / reply admin 回归、WebUI 构建和全量测试通过。

验证结果：

- P1-6 任务 7 红灯集合绿灯：`16 passed, 20 warnings`。
- P1-6 任务 7 相关回归：`126 passed, 20 warnings`。
- WebUI 构建：`npm run build` 通过，Vite 仅提示大 chunk 与插件耗时 warning。
- 全量测试：`1219 passed, 6 skipped, 113 warnings in 81.05s`。
- P1-6 任务 8 文档守卫：`git diff --check` 无输出；旧 prompt 引用扫描仅命中测试守卫 / 负向断言。
- P1-6 任务 8 定向回归：`71 passed, 1 warning`、`42 passed, 20 warnings`、`79 passed, 20 warnings`。
- P1-6 任务 8 WebUI 构建：`npm run build` 通过，Vite 仅提示大 chunk 与插件耗时 warning。
- P1-6 任务 8 全量测试：`1219 passed, 6 skipped, 113 warnings in 82.12s`。

## 测试计划

定向测试优先覆盖：

- `tests/test_classifier_client.py` 或相邻测试：`classifier_legacy` 使用 V2 task template 渲染，不调用 `core.prompt_runtime.render_model_messages()`。
- `tests/test_legacy_adapter.py` 或新增测试：`memory_extract` 使用 V2 task template 渲染，不调用 `core.prompt_runtime.render_prompt_content()`。
- `tests/test_bridge_prompt_v2.py`：显式 V1 override 不再进入 live V1 prompt，或被归一为 canonical engine。
- `tests/test_prompt_legacy_admin_readonly.py`：旧写入口仍为 410；删除阶段更新只读迁移策略。
- `tests/test_prompt_manifest.py`：manifest 从 `v1 rollback_only + v2 active` 过渡到单 canonical engine。
- `tests/test_webui_prompt_runtime_ui.py`：主导航和旧页面策略与删除阶段一致。

验证命令：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_bridge_prompt_v2.py tests/test_prompt_legacy_admin_readonly.py tests/test_reply_admin.py tests/test_prompt_manifest.py -q -p no:cacheprovider
python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
cd webui && npm run build
```

## 风险与控制

- **风险：误删历史兼容。** 控制：历史 trace / docs / fixture 允许保留旧字样，守卫测试只针对 live import 和写路径。
- **风险：目录去版本化与旧 `data/prompts/` 冲突。** 控制：先删除旧 PromptManager runtime，再把 V2 runtime 迁移到无版本目录。
- **风险：后台任务输出格式漂移。** 控制：先写红灯测试固定 `classifier_legacy` 和 `memory_extract` 的输入变量和输出约束，再迁移模板。
- **风险：线上已有 runtime 覆盖丢失。** 控制：删除 `data/prompts/`、`data/prompt_fragments/`、`data/runtime_prompt/prompt.md` 前提供导出 / diff 说明，不自动覆盖用户修改。

## 后续

P1-6 完成后，路线项 9 可以在单一 prompt 引擎上增加 platform × chat_type 维度；路线项 5 / 7 可以继续收敛响应信封与 QQ 出站渲染契约，而不再受 V1 / V2 双路径干扰。
