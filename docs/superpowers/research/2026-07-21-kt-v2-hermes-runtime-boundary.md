# KT 2.0 与 Hermes Agent 运行时边界核验

## 结论

当前版本继续固定 KohakuTerrarium 1.3.0，不在本轮直接升级到 2.0.0，也不引入
Hermes Agent 作为运行时依赖。

这不是否定新版框架，而是由现有 `AgentRuntimePort` 合同决定：KT 2.0 的公开接口
已经覆盖启动、停止、流式聊天、模型切换和中断，但尚未完整覆盖 Nanobot 依赖的
请求级主体上下文、每轮工具策略、pending 状态清理和结构化工具调用检查。若直接升级，
这些能力仍要通过 KT 私有字段实现，会重新把框架细节泄漏到 Bridge。

未来升级必须新增独立的 `Kt20RuntimeAdapter`，让它通过与 `Kt13RuntimeAdapter`
相同的合同测试；业务层、Bridge 和 ToolPlan 不因升级而修改。

## 核验基线

- 当前子模块：KohakuTerrarium `v1.3.0`，提交
  `6c2c5f1d059ac7f99379b0cddeea21da8e9b55c0`。
- 官方稳定版：KohakuTerrarium `v2.0.0`，提交
  `acc2423df7a3e213d7de19d70bc2e507a405a2f8`，发布于 2026-05-29。
- Hermes Agent：NousResearch 官方仓库，核验提交
  `413ed6b9df18f22152d26b6de4093280dcb2b16b`。
- 核验日期：2026-07-21。

官方来源：

- <https://github.com/Kohaku-Lab/KohakuTerrarium/releases/tag/v2.0.0>
- <https://github.com/Kohaku-Lab/KohakuTerrarium/blob/v2.0.0/docs/zh-CN/reference/python.md>
- <https://github.com/Kohaku-Lab/KohakuTerrarium/blob/v2.0.0/docs/zh-CN/guides/plugins.md>
- <https://github.com/Kohaku-Lab/KohakuTerrarium/blob/v2.0.0/docs/zh-CN/reference/plugin-hooks.md>
- <https://github.com/NousResearch/hermes-agent/blob/413ed6b9df18f22152d26b6de4093280dcb2b16b/docs/middleware/README.md>
- <https://github.com/NousResearch/hermes-agent/blob/413ed6b9df18f22152d26b6de4093280dcb2b16b/docs/observability/README.md>

## KT 2.0 与 AgentRuntimePort 对照

| Nanobot 合同能力 | KT 2.0 公开能力 | 判断 |
|---|---|---|
| `start` / `stop` | `Agent`、`Creature` 和 `Terrarium` 都提供显式生命周期 | 可直接适配 |
| 单轮执行 | `Creature.chat()` 返回流，`Agent.inject_input()` 负责注入 | 需要 Adapter 聚合单轮结果 |
| 流式与非流式 | `Creature.chat()` 是公开流式入口 | 可适配，但需保持现有非流式结果合同 |
| 中断 | `Agent.interrupt()` 是公开、线程安全入口 | 可直接适配 |
| 模型路由 | `Agent.switch_model(profile_name)` | 只覆盖 profile 切换；Nanobot 的 provider、超时和 thinking 仍需专用映射 |
| 读取对话 | `Agent.conversation_history` | 可适配 |
| 原子替换对话 | 未提供等价的公开批量替换合同 | 不完整 |
| 请求级 principal/context | `inject_input(..., source=...)` 不承载 Nanobot 的完整受信上下文 | 不完整 |
| 每轮 ToolPlan guard/schema filter | 插件提供全局 pre-tool Hook，但不是请求级 wire schema 合同 | 不完整 |
| 清理 pending events/injections | 未发现公开的等价操作 | 不完整 |
| 结构化检查 tool calls/results | 公开状态以会话/事件为主，未提供等价稳定 DTO | 不完整 |
| shutdown 后 fail-closed | 官方说明 Agent 停止后不可复用 | 与当前终态生命周期一致 |

因此，KT 2.0 具备未来 Adapter 的基础，但还不能无损替换当前实现。升级必须先补齐
上述公开能力映射或由 Nanobot Adapter 自己拥有必要状态，不能让 Bridge 重新读取
`controller._pending_events`、`conversation._messages` 等私有成员。

## 可吸收的 KT 设计

KT 2.0 明确区分两条轴：运行时拓扑与单个 Agent 的组成模块；这与 Nanobot 的
“HTTP/Worker 交付层”和“Agent Runtime Port”分离方向一致。其插件设计还提供：

- Prompt contribution 与 lifecycle Hook 分离；
- Prompt 插件声明数字优先级，数值越小越早；
- `pre_*`、`post_*` 和只通知 Hook 的返回语义明确；
- `PluginBlockError` 是显式阻断，而不是约定字符串；
- `on_load` / `on_unload` 与 Agent 生命周期绑定；
- 插件 option schema 可被运行时和管理界面共同读取。

Nanobot 不照搬 KT 插件对象，而是在自身边界内保留更强的 Descriptor 信息：owner、
domain、authority、trust、source precedence、editable 和 failure policy。KT 的 Hook
只能在未来通过 Adapter 映射，不能绕过 ToolPlan、Trace 脱敏或主体授权策略。

## 可吸收的 Hermes 设计

Hermes 最值得借鉴的是“可观察”与“可改变行为”分离：

- Observer Hook 使用版本化的 `hermes.observer.v1`，默认只读、fail-open；
- Middleware 使用独立的 `hermes.middleware.v1`，显式声明 request transform 或
  execution wrapper；
- session、turn、API request、tool call 使用独立关联 ID；
- 插件通过 manifest 声明 kind、版本、依赖和提供的能力；
- bundled、user、project、entry-point 的发现顺序明确，工具覆盖还需要单独授权；
- 工具策略、审批、执行和结果转换的顺序被写成稳定合同。

这些原则已对应到本轮实现中的 RuntimeEvent、Task/Tool/Prompt/Setting Descriptor、
冻结 Registry、显式 source precedence 和 composition root。Nanobot 当前是受信 QQ
网关上的模块化单体，不需要复制 Hermes 的动态 Python 插件加载、CLI 或 Gateway。
未经独立威胁建模，项目目录插件和同名覆盖也不应开放。

## 后续升级门禁

只有同时满足以下条件，才允许把 KT 2.0 接入生产路径：

1. 新增 `Kt20RuntimeAdapter`，且 `core.agent_runtime` 不导入 KT。
2. 现有 AgentRuntimePort 合同测试对 1.3 Fake、1.3 Adapter 和 2.0 Adapter 共用。
3. 请求级 principal、ToolPlan、模型路由和 Trace 关联不依赖 KT 私有字段。
4. 流式取消、超时、工具调用与会话替换具有行为等价测试。
5. Bridge 不因升级新增任何 KT 私有成员访问。
6. 完整测试、真实 QQ smoke、回滚到 1.3 的演练全部通过。
