# Nanobot Server — 项目约定

## 语言

所有输出、注释、commit message 使用**中文**。代码标识符使用英文。

## Agent 工作方式（GPT-5.6）

- **结果优先**：需求明确时直接执行最小充分方案，不先生成设计文档、长计划或流程说明。
- **严格守界**：只修改用户放入范围的文件；不得顺手重构、增加门禁或扩大任务。发现相邻问题时先报告，不自动处理。
- **按需加载技能**：仅当用户点名技能，或任务与某个窄领域技能直接匹配时才加载。不要因为“可能有帮助”而加载元工作流技能，也不要串联多个规划/审查技能。
- **少问关键问题**：只有缺失信息会实质改变结果或产生风险时才澄清；其余情况采用最保守的合理假设继续。
- **轻量规划**：简单任务不进入 plan/goal 模式。复杂任务可在回复中维护简短步骤，但除非用户要求，不创建计划文件。
- **谨慎使用子 agent**：仅在多个独立子问题可明显并行时使用；简单改动、单点调查和同文件编辑由主 agent 完成。
- **验证后再结论**：修改后运行与改动直接相关的最小验证；声称完成、修复或测试通过时必须给出实际证据。
- **提交需授权**：用户明确说“提交”之前不得 commit；提交时遵循 `chinese-commit-conventions`。
- **外部信息要查证**：API/模型行为优先使用官方文档；社区经验只能作为待验证信号。参考外部代码时优先使用 GitHub 代码搜索。

## 提交规范

遵循 chinese-commit-conventions 技能：
- type: feat / fix / refactor / test / docs / chore / perf
- scope: 中文模块名
- subject: 中文动宾短语，≤50 字符
- 禁止 `git add -A` / `git add .`，必须按文件指定

## 测试

- 测试文件命名：`tests/test_<module>.py`
- 使用 pytest + in-memory SQLite
- 测试必须在提交前运行：`python -m pytest tests/ -v`
- 0 failures 才能提交

## 项目结构

| 路径 | 用途 |
|------|------|
| `api/routes.py` | FastAPI 路由和端点 |
| `clients/` | 外部 API 客户端（new-api, dify, model registry） |
| `nanobot_kt/bridge.py` | KT Agent 生命周期 + 请求处理 |
| `core/` | 数据库、进化、状态管理 |
| `creatures/nanobot/` | KT creature 配置 + 工具 |
| `vendor/KohakuTerrarium/` | KT 框架源码 |
| `tests/` | 测试 |
| `docs/superpowers/specs/` | 设计文档 |

## 数据模型

| 表 | 用途 | 写入方 |
|---|------|--------|
| `users` | 用户 ID + `history_clear_at` 清除标记 | `proxy_chat`、`mark-clear` |
| `chat_logs` | **原始消息存档**——进化/画像分析素材，含 tool、ambient | `_persist_chat_turn`、`log_ambient` |
| `conversation_turns` | **精简对话上下文**——仅 user/assistant，专用于历史注入 | `_persist_chat_turn` |
| `personas` | 用户画像 JSON | `evolution_task` |
| `scheduled_tasks` | 定时推送任务 | `/chat/tasks` |

**分离原则**：`ChatLog` 是档案馆（保留全部，永不删除），`ConversationTurn` 是工作内存（可清理，时间窗口查询）。

## 关键约束

- **HTTP 请求无状态**：KT conversation 每次清空，历史通过 `_build_session_memory()` 从 `ConversationTurn` 注入
- **历史清除**：`mark-clear` 打 `history_clear_at` 标记 + 删 `ConversationTurn`；`ChatLog` 保留不删
- **模型路由**：`get_ordered_candidates()` 按 priority score 排序，向下遍历（便宜优先）
- **熔断器**：`ModelFailureTracker` 连续 3 次失败后自动禁用 5min
- **Token 估算**：CJK 字符按 1.0，ASCII 按 0.35（不求精确，量级判断）
- **中文优先**：bot 使用者是中文用户，所有 prompt 和回复用中文
- **提示词同步**：修改 `enriched_query` 组装逻辑、历史注入方式、conversation 结构、工具输出契约或 prompt runtime 输入时，**必须检查 canonical Prompt Runtime 模板是否仍然准确**，重点包括 `prompts.v2.default/chat/*`、`prompts.v2.default/tasks/*`、`prompts.v2.default/tools/*/usage.md`、`core/prompt_v2/variables.py` 和 `core/prompt_v2/template_registry.py`。如果模板引用的变量、标记或行为描述已过时，必须在同一 PR 中更新默认模板与必要的 `data/prompts_v2/` 运行时模板。

## Docker 与模型沙箱约定

### 技术边界

- **稳定组件优先**：模型执行环境直接基于稳定版 Docker Engine 和 runc。除非用户另行批准并完成独立评估，不引入 OpenSandbox、OpenShell、E2B 等平台作为运行时依赖；OpenClaw、Hermes 仅作为设计参考。
- **职责分离**：KT / Nanobot 继续负责 Agent Loop、会话、工具和 Prompt；独立的轻量 `sandboxd` 负责受控调用 Docker Engine。固定服务可由 Docker Compose 管理，按工作区动态创建的 Sandbox 容器必须通过 `sandboxd` / Docker Engine API 管理。
- **Docker Socket 隔离**：只有 `sandboxd` 可以访问 `/var/run/docker.sock`。禁止把 Docker Socket 挂载到 Nanobot Server 或任意 Sandbox 容器；`docker` 组权限按宿主机 root 权限对待。
- **工具语义分离**：现有 `python_sandbox` 是数据库分析工具，不是通用代码执行沙箱。真正的执行能力使用独立的 `sandbox_exec`，并配套 `workspace_*`、`asset_import`、`asset_publish` 等工具；新增或修改这些工具时必须同步检查 canonical Prompt Runtime 模板。
- **服务端决定 Docker 参数**：模型只能提交工作区内命令和受限参数，不能指定宿主路径、镜像、Docker 参数、volume、network mode、capability、device 或 namespace。镜像和挂载均由服务端根据 `workspace_id` 与固定策略生成。

### 工作区与资产

- **生命周期分离**：容器只负责执行，宿主机工作区才是长期事实源。停止、删除或重建容器不得删除工作区；删除工作区或资产必须是独立、显式且经过权限校验的操作。
- **存储位置**：生产长期数据放在仓库和应用容器之外，目标目录为 `/srv/nanobot/workspaces/`、`/srv/nanobot/assets/`、`/srv/nanobot/runtime/`。只有确认底层文件系统容量、备份和配额方案后才能启用；禁止把长期数据放进容器可写层、仓库内 `./workspace`、`/app/workspace` 或 WSL `/mnt/d`。
- **作用域隔离**：工作区必须绑定明确的用户、群组或项目 owner，并保存 ACL、配额和状态。禁止不同 owner 共享全局可写工作区；模型只能看到 `/workspace` 等容器内虚拟路径，不能看到或构造宿主真实路径。
- **挂载权限**：对应工作区可读写挂载到 `/workspace`；运行时缓存可挂载到 `/runtime`；已授权输入资产只读挂载到 `/inputs`。全局资产库不得以可写方式暴露给 Sandbox，生成结果必须先写工作区，再通过 `asset_publish` 发布为不可变资产。
- **构建上下文隔离**：工作区、资产、运行时缓存、密钥和数据库必须加入 `.dockerignore`，不得被 `COPY . .` 写入镜像。新增任何持久目录时必须同步检查 `.dockerignore`。

### Sandbox 安全基线

- **专用镜像**：Sandbox 使用独立的 `nanobot-sandbox-*` 镜像，禁止复用包含服务端源码和业务依赖的 `nanobot-runtime`。镜像必须固定版本和 digest，不使用浮动的 `latest` 作为运行契约。
- **最小权限**：容器必须使用非 root 用户、只读根文件系统、`cap-drop=ALL`、`no-new-privileges`、Docker 默认或更严格的 seccomp，以及启用的 AppArmor 配置。禁止 privileged、额外 capability、宿主设备、Docker Socket、宿主凭据目录及任意 bind mount。
- **默认断网**：首期统一使用 `network=none`。网页访问、下载和外部 API 调用通过 Nanobot 现有受控工具完成，再将结果作为只读资产导入；未经单独威胁建模和用户批准，不为模型代码开放公网或内网。
- **资源上限**：每个执行容器必须设置 CPU、内存、PID、tmpfs、磁盘/工作区配额、单次执行时间和输出大小上限。超时必须终止整个进程树；资源参数由服务端集中配置，模型不得提高上限。
- **临时与持久状态**：`/tmp` 使用有限大小的 tmpfs；Python venv、npm cache 等可重建状态放在 `/runtime`；操作系统依赖通过预构建镜像提供，不允许模型以 root 身份执行 `apt install`。

### 镜像构建与保留

- **本地快速迭代**：当修改不涉及复杂功能、依赖锁、数据库迁移、基础设施或安全边界，且目的是验证本地源码改动时，默认直接从当前源码构建 `nanobot-runtime` 镜像并部署，不等待远端 CI 或 GHCR 发布。构建前仍须运行与改动直接相关的最小测试，注入准确的 Git revision/dirty 状态；部署后必须校验固定服务镜像身份、健康状态和回滚条件。
- **稳定分层**：第三方大依赖必须先于经常变化的 `vendor/` 和业务代码安装。KT 更新不得触发 torch、transformers 等完整依赖层重建；本地 vendor 应单独构建或单独安装。
- **依赖可复现**：生产依赖与测试依赖分离并锁定版本；基础镜像固定版本或 digest。PyTorch 必须与实际 CPU/GPU 运行方式匹配，禁止在无 GPU 使用需求时打入无用 CUDA 运行库。
- **最小构建上下文**：生产镜像只复制运行所需文件；不得把测试、文档、Agent 配置、工作区或生成资产无条件复制进镜像。现有 `nanobot-runtime:latest` 属于待迁移的兼容行为，不得复制到新 Sandbox 镜像设计。
- **有限回滚**：默认只保留当前已部署镜像和最近 1 个已验证回滚镜像；如需更多回滚版本，必须明确保留数量和容量预算。同一 IMAGE ID 的多个 tag 不重复占用层空间，但旧 IMAGE ID 不得无限累计。
- **缓存保留**：BuildKit 缓存使用时间或容量上限控制，优先保留最近成功构建所需缓存。清理缓存只影响后续构建速度，仍须先查看 `docker system df -v` / `docker buildx du`，不得把无界缓存留到系统盘耗尽。

### 运维与验证

- **部署前检查**：构建、部署或启用 Sandbox 前检查 `df -h`、`df -i`、`docker system df` 和数据目录所在文件系统。长期资产必须配置总配额、owner 配额和磁盘水位保护；空间不足时拒绝新的资产写入和 Sandbox 执行，不能侵占系统保留空间。
- **定向清理**：未经用户明确授权，禁止执行 `docker system prune`、`docker image prune -a`、`docker volume prune`、`docker compose down -v` 等破坏性命令。获得授权后也必须先列出容器、镜像、缓存和 volume，再按明确的名称或 IMAGE ID 定向清理；运行容器和持久 volume 默认不可删除。
- **健康检查后淘汰**：新镜像部署并通过健康检查和直接相关测试后，才能淘汰超出保留数量的旧镜像。创建 rollback tag 的部署入口必须同时承担保留策略，不能只创建、不回收。
- **真实隔离验证**：不能仅凭 Docker 配置声明 Sandbox 安全。实现后必须实际验证非 root、只读根文件系统、无网络、无 Docker Socket、资源限制、超时终止、工作区跨容器重建持久化，以及不同 owner 之间无法互读文件。

## 禁止行为（反复犯错的教训）

### 1. 未验证就声称完成
- ✗ 没测 Qwen 连通性就说端口改了 OK
- ✗ 没测 news_search 就说"网络问题非代码"
- ✗ 没跑 benchmark 就说 prompt 优化好了
- ✓ **任何修改后，必须实际运行验证命令并确认输出**

### 2. 急于提交
- ✗ 多次在测试没跑完、用户没确认时就要 commit
- ✓ **用户明确说"提交"之前不 git commit**

### 3. 照搬外部回答，不做独立搜索
- ✗ 用户贴了一段 AI 分析，直接当正确答案
- ✗ 用户说"用 qualifire 模型"，没验证就加进代码
- ✓ **用户提出的方案也是假设，必须自己查证**
- ✓ **用 GitHub MCP / Context7 / WebSearch 独立调研**

### 4. 提 naive 技术方案
- ✗ "用关键词匹配做领域分类"——不可靠
- ✗ "asyncio.Lock 嵌套 + Event.wait"——必然死锁
- ✗ "_preprocess 重排消息"——hacky
- ✓ **复杂且需求不明确时先确认关键约束；需求明确时直接实施最小方案**

### 5. 代码改了但没读现有实现
- ✗ PRAGMA regex `\b` 不知道实际 SQL 格式
- ✗ 声称改了 QQBOT_PUSH_URL 但实际没改
- ✓ **改之前 grep 相关代码，读明白现有逻辑**

### 6. 不用好用的工具
- ✗ 用 WebSearch/WebFetch 搜网页——结果不可控、解析易出错、吃上下文
- ✗ 明明有 Tavily（tavily-search/tavily-crawl/tavily-cli）却不用
- ✓ **网页搜索优先用 Tavily**——LLM 优化结果，干净结构化输出
- ✓ **代码搜索用 GitHub MCP**——search_code/search_repositories
- ✓ **API/库文档用 Context7**——query-docs

## 服务器地址

| 服务 | 地址 | 用途 |
|------|------|------|
| new-api (LLM) | `10.60.42.158:9000` | LLM API 网关 |
| nanobot server | `10.60.42.158:8000` | 主服务 |
| QQbot push | `10.60.42.158:8082/nanobot/push` | 向 QQ 推送消息 |
| Qwen classifier | `172.17.0.1:9999` | 容器内访问宿主机的 llama-server |

本地测试时需 `unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY` 清除代理。
