# Agent Harness 运维手册

## 1. 适用范围

本文面向 Nanobot Server 的部署和运维人员，说明 Agent Runtime、运行账本、恢复、权限、评测、受控进化
和经验候选的启用、观测及回滚入口。Sandbox 的宿主准备和攻击面验证继续以
[Sandbox 运维手册](sandbox-operations.md) 为准；KT 可选 Runtime 的安装和升级以
[KT 兼容说明](kt-runtime-compatibility.md) 为准。

本文只描述已经接入实际运行链路的能力。标记为实验性或默认关闭的能力仍会真实执行，但必须先满足服务端
门禁并由操作人员显式启用；不能把离线评测、只读采样或 shadow 结果当成生产启用证明。

## 2. 运行事实与默认状态

| 能力 | 默认状态 | 实际数据面 | 回滚原则 |
| --- | --- | --- | --- |
| Native Runtime | 默认启用 | 私聊、群聊、任务和恢复均通过 `AgentRuntimePort` | 保留 Run、Checkpoint 和副作用回执，按会话或全局切换已验证 Runtime |
| KT Runtime | 未安装、未启用 | 仅在安装固定可选锁并命中显式灰度时进入 KT Adapter | 将灰度万分比和会话白名单清零，恢复 Native；不跨 Runtime 自动重试 |
| Run / Event Ledger | 权威写入 | 请求接纳、模型、工具、权限、用量、Artifact、交付和终态 | 不原地修改事件；用后续纠正事件或受控完整流删除表达变化 |
| Checkpoint / Recovery | Native 主链启用 | Resume、Rewind 和 Fork 会创建新的 child Run 并继续执行 | 保留源 Run；存在 `prepared` 或 `ambiguous` 副作用时禁止自动重放 |
| Skill / MCP / Hook | 受管 Registry | 运行时只消费冻结 snapshot、Skill lock 和权限决定 | 使用既有卸载、版本切换或配置禁用入口，不改写不可变版本 |
| 多 Agent | 默认关闭 | 显式批准的冻结 DAG 通过真实 child Runtime 执行 | 停止新调度，取消或收敛现有 Run；保留 Checkpoint、事件和回执 |
| 受控进化 | 默认无灰度 | 已批准路由候选可在真实模型路由中按稳定桶生效 | 调用 release rollback，运行时立即回到最近有效基线 |
| 经验 Skill 候选 | 默认无候选 | 新 `user` scope Skill 可进入正式 active binding；已有 Skill 只暂存新版本 | 新 Skill 使用正式 uninstall；已有 Skill 未切换运行版本，无需运行时回滚 |
| Sandbox | 全部硬开关关闭 | 仅 `sandboxd` 可访问 Docker Socket | 先执行 kill switch，再回收 Lease；Workspace 和 Artifact 默认保留 |

## 3. 部署前检查

### 3.1 代码与依赖

合并或部署前至少执行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python scripts/check_architecture.py
python scripts/generate_openapi_client.py --check
python scripts/audit_decision_rules.py --check
python -m evals.harness_gate offline --all
python -m pytest tests/ -v
```

Native 环境不得因预装 KT 或其传递依赖而误通过。发布流水线应先安装核心锁并执行：

```bash
python -m pytest tests/test_native_without_kt.py -v
```

只有明确批准的 KT 兼容环境才安装 `requirements-kt.lock`。核心生产锁、默认镜像和构建上下文不得包含
KT 包、旧 `vendor/KohakuTerrarium` 或 `.gitmodules`。

### 3.2 数据目录

运行时可重建或受治理的数据必须位于 `RuntimePaths` 解析后的数据根目录。当前新增目录包括：

- `evals/evolution_control/`：冻结数据集、离线候选、门禁、人工批准和灰度发布；
- `evals/skill_candidates/`：脱敏经验候选、独立评测、批准和正式发布回执；
- Run Ledger、Checkpoint、Skill 版本及治理记录：保存在业务数据库中。

这些目录不得进入镜像构建上下文。生产长期 Workspace、Asset 和 Sandbox Runtime 仍必须位于
`/srv/nanobot/` 对应独立数据盘，不得放入仓库、容器可写层或 WSL 的 `/mnt/*`。

### 3.3 Sandbox 前置条件

启用 Sandbox 前必须在真实 Linux 部署宿主以 root 运行：

```bash
scripts/sandbox-smoke-test.sh \
  --manifest /etc/nanobot/sandbox-profiles.json \
  --data-root /srv/nanobot \
  --evidence-root /var/lib/nanobot/sandbox-smoke
```

以下任一情况都必须保持 `sandbox.enabled=0` 和 `sandbox.exec_enabled=0`：

- Docker 未同时报告 builtin seccomp 和 AppArmor；
- 两个固定 AppArmor profile 未实际加载；
- 镜像引用未固定到唯一 digest；
- `/srv/nanobot` 不是满足 project quota 的独立数据盘；
- 六组真实 Docker 测试存在失败、错误、跳过、零测试或缺少 JUnit 证据。

开发机上的单元测试和 `--preflight-only` 只能证明失败关闭是否有效，不能替代真实宿主验收。

## 4. 运行观测与恢复

### 4.1 Run Viewer

管理员通过以下接口读取权威投影：

- `GET /api/v1/admin/agent-runs`：列出 Run；
- `GET /api/v1/admin/agent-runs/{run_id}`：查看 Run、Context、工具、模型、恢复和 Artifact 投影；
- `GET /api/v1/admin/agent-runs/{run_id}/events`：按固定 high-water 分页读取事件；
- `POST /api/v1/admin/agent-runs/{run_id}/cancel`：只记录取消请求，不创建第二个执行 owner。

读取时会校验 sequence、事件摘要和前向 hash chain。若 Ledger 无法形成完整投影，接口必须返回失败，不能
退回可能漂移的 legacy 当前值。只有迁移前完全没有 Ledger 的旧 Run 才能显式标记为兼容记录。

### 4.2 Resume、Rewind 与 Fork

执行恢复前依次检查：

1. owner、Run 和 Checkpoint 摘要链；
2. Runtime 协议及恢复能力；
3. Manifest、Prompt、模型路由、ToolPlan、Workspace、Artifact 和安全策略固定点；
4. 文件状态、Artifact ACL 与内容 hash；
5. side-effect receipt 与 Ledger 的双向锚点；
6. Durable Task lease、generation、attempt 和当前执行 owner。

恢复会创建独立 child Run，并真实执行 `CONTINUE`。`prepared`、`ambiguous`、版本漂移、权限漂移或
TOCTOU 校验失败都必须停止恢复，由操作人员先处理不确定副作用。

### 4.3 Gateway 远程控制

`/api/v1/admin/gateway-control/*` 只接受管理员身份和幂等 `request_id`。停止、恢复和模型切换均验证当前
generation；客户端刷新或重连只恢复视图，不重复启动 Run。模型切换只能选择服务端已经登记并通过能力
预检的 profile。

## 5. 评测和发布门禁

### 5.1 Agent Harness Gate

阻断式门禁只接受冻结 Registry 中的 pytest selector：

```bash
python -m evals.harness_gate catalog
python -m evals.harness_gate offline --all
```

离线门禁要求每个 suite 至少执行 1 项测试，且 failure、error、skip 和 timeout 全部为 0。真实模型
benchmark 必须显式启用并受费用预算约束；线上采样只能读取聚合量，二者都不能提升为阻断证据。

### 5.2 受控进化

运维顺序固定为：冻结数据集 → 导入离线候选 → 独立 gate → 人工精确 hash 批准 → 有限灰度 → 观测或
回滚。管理接口位于 `/api/v1/admin/evals/evolution/*`。

批准令牌的明文只返回一次；不得写入日志、工单或审计 detail。灰度最多覆盖合同规定的万分比和时长，
路由候选只能重排当前已验证的 Provider / model profile。回滚使用：

```text
POST /api/v1/admin/evals/evolution/canary/{release_id}/rollback
```

回滚不删除冻结数据集、候选、gate、批准或历史 release；这些记录用于解释基线、质量、成本和批准人。

### 5.3 经验 Skill 候选

运维顺序固定为：选择同时包含成功和失败的 Run → 离线提取 → 独立 gate → 人工批准 → 正式发布。
管理接口位于 `/api/v1/admin/skills/candidates/*`，离线 CLI 为：

```bash
python -m evals.skill_candidates catalog
python -m evals.skill_candidates extract --help
python -m evals.skill_candidates gate --help
python -m evals.skill_candidates --root <候选目录> state
```

新 Skill 只允许显式 `user` scope 首发。发布回执会给出正式 package、binding、评测 ID 和精确 uninstall
请求；应先保存并演练该回滚请求。已有 Skill 候选只进入正式版本库，当前 active 版本保持不变，后续升级
仍使用既有 Skill 人工升级入口。

## 6. 故障处理

| 现象 | 处理 |
| --- | --- |
| Ledger 完整性错误 | 停止恢复和治理写入，保存 high-water 与摘要证据；不要用 legacy 行覆盖投影 |
| lease 丢失或 controller epoch 变化 | 取消当前执行，等待 reconcile；不要由客户端重试启动同一 Run |
| 外部副作用结果未知 | 将 Run 收敛为 `ambiguous`，核对上游幂等回执后再人工处置 |
| Harness Registry 漂移 | 废弃旧 gate，使用当前 Registry 和冻结数据重新评测 |
| 进化或 Skill 基线漂移 | 重新生成候选和批准；不要复用旧令牌或跳过 generation 校验 |
| Sandbox readiness 失败 | 保持硬开关关闭，修复宿主条件后重新运行完整六组矩阵 |
| KT Adapter 失败 | 将 KT 灰度设为 0，恢复 Native；不要在失败请求内跨 Runtime 重试 |

## 7. 审计与隐私

- Ledger、评测和候选只保存合同允许的摘要、计数、状态与安全投影；
- 原始消息仍按 `ChatLog` 档案治理，不复制到评测候选或 Run Viewer 导出；
- `ConversationTurn` 只用于可清理工作记忆，清除历史不会删除 `ChatLog`；
- 批准令牌、API Key、OAuth Token、隐藏推理、工具参数／结果和 Sandbox 命令／输出不得进入候选审计；
- 删除 Run 证据必须经过 owner、终态、法律保留、导出清单 hash 和短时删除授权的完整校验。
