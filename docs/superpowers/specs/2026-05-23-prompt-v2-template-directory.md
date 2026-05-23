# Prompt V2 模板目录化整理设计

## 背景

Prompt V2 已经从旧 prompt runtime 中隔离出来，但默认模板仍有一批扁平 key 和扁平文件名。工具模板、聊天编排模板、离线任务模板混在同一目录下，WebUI 只能靠 key 名推断类型，工具内部二次 LLM prompt 也容易和主回复工具说明混淆。

本设计按用户确认的目录结构收口：

- `chat/`：主回复编排和聊天分支模板。
- `tools/<tool>/`：每个工具独立目录，`usage.md` 是主模型看到的工具边界说明，其他文件只给工具内部二次 LLM 调用。
- `tasks/`：时机判断、记忆抽取、回复合约重试等非工具 schema 的任务模板。

## 目标

- 模板 key 统一为 slash namespace，例如 `chat/main`、`tools/group_analysis/topics`、`tasks/timing_gate`。
- WebUI、compiler、preview、bridge、工具内部 LLM 调用都通过同一套模板 registry 和 loader 解析模板。
- 运行时覆盖目录 `data/prompts_v2` 使用同样目录结构，且运行时覆盖不直接修改 Git 默认模板。
- 保留旧扁平 key alias，只做兼容读取，不再作为新页面主入口。
- V2 页面展示资源树，并支持运行时模板的新建、保存、删除覆盖、重置覆盖。

## 关键设计

### 模板 Registry

新增 `core.prompt_v2.template_registry` 作为模板索引层，负责：

- 校验模板 key，拒绝 `..`、绝对路径、反斜杠、空路径和非法字符。
- 解析旧扁平 alias 到 canonical slash key。
- 提供默认路径和运行时路径。
- 合并默认目录和运行时目录中的模板 key。
- 根据 key/frontmatter 分类为 `chat`、`tool`、`task`，并推导 `tool_name`。

### Template Loader

`core.prompt_v2.template_loader.load_template()` 继续返回 `PromptTemplate`，但读取优先级固定为：

1. `data/prompts_v2/<key>.md`
2. `prompts.v2.default/<key>.md`
3. 旧扁平 alias 的运行时/默认路径，仅用于兼容读取

当运行时覆盖文件没有 frontmatter 时，loader 继承默认模板 frontmatter，避免 WebUI 保存正文后丢失展示名、kind、tool_name 和描述。

### Template Store 和 Admin API

`core.prompt_v2.template_store` 负责 WebUI CRUD：

- 列表返回 `items` 和 `tree`，tree 按 `chat/tools/tasks` 分类。
- `GET/PUT/DELETE/RESET` 支持 `{template_key:path}`。
- `POST /prompt-v2/templates` 只创建运行时覆盖。
- 删除和重置只删除 `data/prompts_v2` 中的覆盖文件，不删除默认模板。
- 工具模板详情附带真实 schema 预览。

### 运行链路

- compiler 读取 `chat/flow.json`，flow 中模板节点只写 slash key。
- runtime tool prompt 和 tools schema description 使用 `tools/<tool>/usage`。
- 工具内部二次 LLM 调用使用各自目录内的模板，例如 `tools/group_analysis/topics`、`tools/news_search/digest_system`、`tools/image_summary/system`。

### WebUI

`/prompt-v2-templates` 使用资源树组织模板：

- 聊天编排：画布和 chat 模板。
- 工具模板：按工具目录折叠，显示正文、真实 schema、来源路径。
- 任务模板：显示任务模板正文和默认/运行时路径。

旧入口收口：

- `/prompts` 标记为“旧 PromptManager 模板（v1/迁移）”。
- `/prompt/fragments` 标记为“Legacy prompt.md 片段（v1 回滚）”。

## 验收

- slash key 和旧 alias 都能读取到同一模板。
- 非法 key 被拒绝。
- runtime 覆盖继承默认 frontmatter。
- admin CRUD 只写 `data/prompts_v2`。
- compiler 使用 `chat/*` key，群聊/私聊规则不混入。
- tool schema description 与 runtime tool prompt 使用 `tools/<tool>/usage`。
- group_analysis/news_search/ai_daily/image_summary 内部 prompt 使用新目录化 key。
- WebUI 展示资源树、真实 schema、运行时覆盖操作。
