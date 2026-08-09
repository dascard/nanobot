# Agent Harness 迁移指南

## 1. 迁移目标

本指南用于将旧的「KT 子模块 + Bridge 业务语义 + 当前值 Trace」部署迁移到当前架构：

- Native Runtime 为默认主链，KT 是固定版本、显式安装和显式灰度的可选 Adapter；
- Run / Event Ledger、Checkpoint 和 Durable Task 保存运行事实与恢复边界；
- Prompt Runtime 和 Context Engine 是模型可见上下文的唯一组装边界；
- Skill、MCP、Hook、权限、身份和 Workspace 由服务端 Registry 与治理合同控制；
- 多 Agent、受控进化和经验候选默认关闭，只能经过独立评测和人工批准进入真实数据面。

迁移不会自动删除 `ChatLog`、旧 Trace、Workspace、Artifact、正式 Skill 或 Sandbox 数据。涉及删除的治理操作
必须单独发起并通过权限、法律保留及清单确认。

## 2. 迁移前准备

### 2.1 冻结和备份

1. 记录当前部署 commit、镜像 ID、数据库 schema、Prompt Runtime revision 和所有运行开关；
2. 停止新增长任务和灰度发布，等待现有 Run、投递和 Sandbox Lease 收敛；
3. 按现有数据库与 Sandbox 手册完成可恢复备份；
4. 导出 Run、Skill、MCP、Hook、模型目录和 Provider 的安全清单，不导出凭据正文；
5. 确认回滚版本仍可取得，且数据迁移没有依赖未保留的旧容器可写层。

### 2.2 环境检查

迁移目标必须满足：

- Python 和核心依赖来自 `requirements-prod.lock` 或对应测试锁；
- 默认构建不含 `.gitmodules`、`vendor/KohakuTerrarium` 和 KT 可选锁；
- `DATABASE_URL` 继续指向受支持的 SQLite 数据库；本计划没有迁移到 PostgreSQL；
- Prompt 默认模板和 `data/prompts_v2/` 运行时模板来自同一受验证 revision；
- Sandbox 在未通过真实宿主验收前保持全部硬开关关闭。

## 3. 分 Wave 迁移

### 3.1 Wave A：Runtime 与 KT 解耦

1. 先在未安装 KT 的环境运行 `tests/test_native_without_kt.py`；
2. 将默认 Runtime 设置为 `native`，KT enable、rollout 和 session allowlist 全部设为关闭；
3. 部署核心镜像并验证私聊、群聊、任务、直接工具和服务启停；
4. 只有存在兼容需求时才安装 `requirements-kt.lock`，先用精确 session allowlist 灰度；
5. 删除部署脚本中的 submodule 初始化和 `vendor/KohakuTerrarium` 构建步骤。

回滚时先把 KT 灰度清零并恢复 Native。若 Native 发布本身需要回滚，应恢复上一组完整 Runtime、Prompt、
Manifest 和数据库兼容版本，不能只替换 Adapter 文件。

### 3.2 Wave B：Ledger、恢复、Artifact 与 Context

1. 运行数据库迁移，创建 Ledger、Checkpoint、Durable Task、Artifact 和治理表及不可变 trigger；
2. 保持旧 Trace 只读兼容，确认新 Run 已写入接纳、Prompt、模型、工具、权限、用量、Artifact、交付和终态；
3. 使用管理端投影核对 high-water、hash chain 和 legacy readiness；
4. 只选择没有不确定副作用的测试 Run 演练 Resume、Rewind 和 Fork；
5. 核对 Context Manifest、Prefix Cache Manifest 和 Prompt trace 不包含正文。

旧 `AgentRun` 当前值不能覆盖 Ledger 投影。迁移前完全没有 Ledger 的记录可以继续只读展示，但不得据此
执行恢复。旧 `asset://sha256` 只保留迁移读取，新写入统一使用版本化 Artifact 引用。

### 3.3 Wave C：Skill、MCP、Hook、权限和作用域

1. 导入或重建 Registry snapshot，并记录 generation 与 SHA-256；
2. 为每个正式 Skill 固定 package、版本、来源、scope、binding 和 lock；
3. 为 MCP Server 固定 transport、安全策略、工具目录和健康状态；
4. 将工具、Skill、MCP、文件、网络和记忆访问转换为精确权限，不使用通配符；
5. 核对 user、group、project、session、agent 与 actor 的 owner / ACL；
6. 先发放有界 session grant，再验证 `ask`、`allow-once`、过期和撤销。

回滚只停用 binding、Server 或 grant，并保留不可变版本和审计记录。不得为恢复旧行为重新开放全局写权限、
共享可写 Workspace 或 Docker Socket。

### 3.4 Wave D：多 Agent、协议与 Gateway

1. 保持多 Agent、ACP、A2A 和 Headless Feature 默认关闭；
2. 使用冻结 DAG、明确预算和独立 child 权限完成离线及单会话验收；
3. 只通过受管 Gateway 建立远程会话，验证 stop、resume、model switch 的幂等 request 与 generation；
4. 验证客户端重连只恢复 pending interaction 和视图，不创建第二个 Run；
5. 对主动能力核对租约、候选提交、Outbox、Delivery Attempt 和 ambiguous 冷静期。

回滚时先停止新调度和新会话，再取消或收敛现有 child Run；保留父子 lineage、Checkpoint 和副作用回执。
ACP / A2A 试验不得升级为绕过权限或直接共享凭据的生产传输。

### 3.5 Wave E：评测、进化和经验候选

1. 运行冻结 Harness Registry 的全部离线阻断 suite；
2. 冻结 baseline、training、validation 和 test，确认 validation / test 答案未暴露给候选生成器；
3. 分别验证安全、质量和成本门禁，生成器与评测器必须独立；
4. 使用人工精确 hash 批准和单次令牌进行最小灰度；
5. 演练进化 release rollback、新 Skill uninstall，以及已有 Skill 版本暂存但不切换 active 的路径；
6. 核对审计可以回答来源数据、比较基线、质量变化、费用、批准人和回滚方式。

任何候选都不能访问生产原始正文、网络或仓库写入，不能自行评测、批准、提交主干或扩大权限。

## 4. 兼容项处理

| 旧行为 | 当前行为 | 迁移处理 |
| --- | --- | --- |
| KT 仓库子模块和 `vendor/` 源码 | 固定 VCS 锁的可选 KT 包 | 删除构建中的 submodule 步骤；需要 KT 时单独安装可选锁 |
| 业务代码直接依赖 KT 类型或私有字段 | `AgentRuntimePort` + `nanobot_kt` 公共 API Adapter | 只在 Adapter 处理 KT 映射；核心层不得重新 import KT |
| `AgentRun` 当前值作为运行事实 | Append-only Ledger 投影 | 新 Run 只认投影；无 Ledger 的旧 Run 仅只读兼容 |
| 消息正文携带大工具输出或路径 | Workspace + 不可变 Artifact 引用 | 新写入发布 Artifact；旧引用仅在迁移读取边界解析 |
| Prompt、历史和工具说明分散拼接 | canonical Prompt Runtime + Context Manifest | 同步默认和运行时模板；删除旁路拼接或字符串删 Prompt |
| 工具靠 Prompt 自律控制权限 | 服务端 ToolPlan、Permission、grant 和预算 | 先登记精确资源和 scope，再开放实际工具 |
| 进化结果自动改代码或主干 | 离线候选、独立 gate、人工灰度 | 禁止仓库写入；通过运行时版本、release 或 binding 回滚 |

兼容注册表中的条目必须有 owner、使用计数、警告策略和删除条件。只有确认调用方已迁移且对应 Golden、
API 或数据迁移门禁通过后，才能删除兼容项；不能因名称包含 `legacy` 就在同一发布中直接移除。

## 5. 验证与完成条件

迁移至少通过以下验证：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python scripts/check_architecture.py
python scripts/generate_openapi_client.py --check
python scripts/build_behavior_baseline.py --check
python scripts/build_release_impact.py --check-golden
python scripts/build_verification_plan.py --check-golden
python scripts/build_task_slo_manifest.py --check
python scripts/audit_decision_rules.py --check
python -m evals.harness_gate offline --all
python -m pytest tests/ -v
npm --prefix webui run lint
npm --prefix webui test
npm --prefix webui run build
```

Sandbox 还必须在满足 AppArmor、独立 quota 数据盘和固定镜像条件的真实部署宿主运行完整 Smoke。若前置
检查返回 `blocked`、测试被 skip 或测试数为 0，迁移状态只能记录为「代码已就绪，生产 Sandbox 未启用」，
不能记录为生产验收完成。

## 6. 回滚完成条件

回滚后必须确认：

- 新请求只进入选定的稳定 Runtime，旧 Runtime 没有继续接收流量；
- 活跃 Run 已终结、取消或由 reconcile 接管，没有双 owner；
- 旧 Ledger、Checkpoint、Artifact、Skill 版本和审批证据仍可读取；
- 不确定外部副作用仍标记为 `ambiguous`，没有自动重放；
- Prompt Runtime、ToolPlan、权限和 owner 作用域与回滚版本一致；
- Sandbox kill switch 生效，Lease 已受控回收，Workspace 和 Artifact 未被误删；
- OpenAPI、行为 Golden、Harness Gate 和完整测试重新通过。
