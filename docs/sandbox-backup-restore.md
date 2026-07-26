# Nanobot Sandbox 备份与恢复手册

## 1. 备份范围

必须协调备份：

- Nanobot SQLite 数据库；
- /srv/nanobot/workspaces/；
- /srv/nanobot/assets/ 中已完成的不可变 blob。

数据库备份必须包含并保持一致：

- workspaces、assets、workspace_assets、sandbox_runs；
- sandbox_leases、sandbox_controller_states；
- sandbox_access_grants；
- workspace_quota_bindings；
- workspace_runtime_quota_bindings；
- workspace_maintenance_states；
- sandbox_admin_operations；
- sandbox_project_sequences；
- schema_migrations。

明确不备份：

- `/srv/nanobot/runtime/`，包括 venv、pip/npm cache、环境指纹和 `.sandboxd-leases` 临时控制状态；
- 单次运行的 /inputs staging；
- assets/sha256/.tmp/ 未完成上传；
- Restricted 一次性容器、Developer Lease、出口代理、Docker 网络、Exec 句柄、进程输出和全部容器 writable layer；
- 旧 sandbox-projects.tsv 作为活动配置。

数据库保存 canonical session 授权、Workspace／Asset 关系、Profile 选择、Lease／Run 历史、project ID 和 quota generation；文件系统保存 Workspace 与不可变 Asset 的实际内容。只备份其中一侧不能视为可恢复备份。

`/runtime` 只承载可重建缓存和临时 controller 状态，不是长期事实源。恢复后必须创建新 Lease、重新准备环境并生成新的 `process_id`；不得尝试恢复备份时的后台进程或 controller epoch。

备份记录应额外保存 RELEASE、部署 Profile manifest SHA256、`catalog_generation`、完整 `policy_sha256` 和三个镜像 IMAGE ID。它们用于选择兼容的恢复软件，不得把 sandboxd Token、管理 Token、私有凭据或宿主密钥写入数据归档。

## 2. 创建协调备份

1. 调用 kill switch，确认 `sandbox.enabled` 与 `sandbox.exec_enabled` 均为 false，并核对返回的全部 Process／Lease 回收结果。
2. 由 root 同时关闭 `infrastructure_enable_allowed`、session 执行硬开关，以及 Server／sandboxd 两侧 Developer 网络硬开关。
3. 确认没有托管 Restricted 容器、Developer Lease 或出口代理。只能按固定名称前缀和双所有权标签核对，不得模糊删除容器。
4. 等待 running 管理 operation 结束；记录 pending/retry_wait 项。
5. 停止当前 Compose 项目的固定服务，避免聊天、上传和 Worker 继续写库。
6. 停止 sandboxd；保留 /srv/nanobot 和数据库，不运行任何 prune。
7. 按已配置的备份模式执行。默认 `independent` 使用独立挂载点：

~~~bash
scripts/sandbox-coordinated-backup.sh \
  --database /opt/nanobot-server/nanobot.db \
  --data-root /srv/nanobot \
  --destination /mnt/nanobot-backup \
  --backup-mode independent \
  --max-bytes 17179869184 \
  --system-min-free-bytes 64424509440 \
  --quiesced \
  --apply
~~~

明确接受单盘故障风险时，可使用 `local_same_disk`。该模式只允许固定路径的 16 GiB 预分配 XFS loopback，目标必须位于根文件系统且不得位于生产仓库、Sandbox 数据根或 loopback 镜像目录：

~~~bash
scripts/sandbox-coordinated-backup.sh \
  --database /opt/nanobot-server/nanobot.db \
  --data-root /srv/nanobot \
  --destination /var/backups/nanobot-sandbox \
  --backup-mode local_same_disk \
  --risk-marker single_disk_logical_rollback_only \
  --max-bytes 17179869184 \
  --system-min-free-bytes 64424509440 \
  --quiesced \
  --apply
~~~

`local_same_disk` 备份仅用于数据库与文件系统的逻辑回滚，不承担物理硬盘损坏、整机丢失或根文件系统损坏后的灾难恢复。风险标记、16 GiB 单次容量上限和 60 GiB 根分区最低可用空间都会写入备份 manifest；缺少任一门禁时脚本失败关闭。

脚本使用 SQLite backup API 创建数据库副本，分别归档 workspaces/assets，并生成 manifest.sha256。`independent` 目标仍必须位于不同于数据盘的独立挂载点；已有同名目录不会覆盖。失败时保留 .partial 供人工核查，不自动递归删除。

8. 执行 sha256sum -c manifest.sha256。
9. 记录备份目录、模式、风险标记、源／目标设备、容量与水位、数据库 schema migration 版本、最大 project ID、Workspace／Asset／Grant／Lease／Run 数量、RELEASE、部署 manifest 与三个 IMAGE ID。
10. 重启服务后仍保持三道开关关闭，完成健康检查再按灰度流程恢复。

## 3. 恢复前验证

恢复必须先在非生产宿主演练：

~~~bash
cd <备份目录>
sha256sum -c manifest.sha256
sqlite3 nanobot.db 'PRAGMA quick_check;'
tar -tf workspaces.tar >/dev/null
tar -tf assets.tar >/dev/null
~~~

还应查询：

- schema_migrations 包含 Sandbox 控制面和 ToolOverride 退役迁移；
- workspace_quota_bindings 的 project_id 唯一；
- workspace_runtime_quota_bindings 的 project_id 唯一，且不与 Workspace project ID 冲突；
- sandbox_project_sequences.next_value 大于所有已分配 project ID；
- 每个 grant 的 workspace_id 存在；
- 每个 sandbox_lease 的 grant_id、workspace_id 和 profile_id 都可解析；
- 每个 sandbox_run 的 lease_id 为空或引用存在的 Lease 历史；
- 每个 workspace_assets 链接的 Workspace 和 Asset 均存在；
- 每个 Workspace 的 used_bytes 不超过 desired quota。

归档中不得包含绝对路径、`..`、runtime、input staging、Token、数据库外副本或宿主密钥。

## 4. 恢复步骤

1. 保持宿主硬上限和两个业务开关关闭，停止固定服务与 sandboxd。
2. 记录现有数据库和数据盘状态；不要覆盖原盘。
3. 挂载一块空的、已启用 project quota 的恢复数据盘。
4. 在空暂存目录中校验哈希后解包，保留 UID/GID、ACL 和 xattr。
5. 恢复 SQLite 数据库到新的目标文件，不直接覆盖仍被进程打开的数据库。
6. 核对数据库中的 Workspace、Asset、grant 和 blob 数量与大小。
7. 创建空的 `/srv/nanobot/runtime/`，不要从备份复制旧 venv、cache、`.sandboxd-leases`、controller epoch 或进程输出。
8. 部署与备份记录兼容的 Nanobot Runtime、sandboxd、Profile manifest 和三镜像发布单元；禁止混用不同发布的 Server 与 manifest。
9. 启动 sandboxd 和 Nanobot，但继续保持 `infrastructure_enable_allowed=false`、session 执行硬开关和两侧 Developer 网络硬开关关闭。
10. 对每个 Workspace 从 Web 重新提交当前 desired Workspace／Runtime quota，或调用相同管理 API 生成 set_quota operation。
11. 等待所有 quota operation succeeded，确认两类 project ID、generation、desired 与 applied 一致。
12. 运行数据盘门禁、A／B session 隔离和六组真实 Docker Smoke。
13. 确认恢复前所有 active/running Lease／Run 历史都已收敛为恢复或 controller 重启终态，不存在可复用的旧 `process_id`。
14. 只对一个私聊 canonical session 恢复 Workspace 能力，再依次恢复 Assets、Restricted Exec 和 Developer Exec。
15. Developer 网络必须最后单独打开两侧硬开关；`trusted_developer` 与私有仓库凭据继续关闭。
16. 全部验收后才允许 root 打开宿主硬上限，并按灰度手册开启业务开关。

恢复数据库中的 applied 状态只能说明备份时旧数据盘已应用，不能证明新数据盘已应用。必须在硬上限关闭期间重新提交并确认配额，禁止直接信任旧 applied 值启用工具。

## 5. 旧 TSV 的处理

如备份来自旧版本，可先保留 /etc/nanobot/sandbox-projects.tsv 作为证据，再使用 scripts/migrate-sandbox-project-map.py 完整预检和一次性迁移。

迁移结果初始为 pending、applied_quota_bytes=0，且不会创建 session grant。迁移完成后：

- 数据库成为 project ID 和 quota 唯一事实源；
- 将 sandbox_project_sequences 推进到最大 project ID 加一；
- TSV 设为只读归档，不再由部署脚本写入；
- 后续授权全部从 Web 按 canonical session 操作。

## 6. 恢复验收证据

必须保存：

- manifest.sha256 全部通过；
- SQLite quick_check 为 ok；
- schema migration 版本和关键表计数；
- Workspace/Asset 文件数量与大小核对；
- canonical session A/B 隔离和 Asset 授权；
- project ID 唯一性、quota generation、desired/applied 一致性；
- Workspace／Runtime 两类 project quota 的真实硬限制；
- 数据盘水位、两个 AppArmor profile、seccomp、部署 manifest 和三个镜像 Docker inspect；
- Restricted 无网络，以及 Developer allowlist、IP 直连／私网／宿主／其他 Lease 拒绝矩阵；
- 无 Docker Socket、非 root、只读根和资源限制；
- Lease 重建后的 Workspace／Runtime 持久化与 `/tmp` 清空；
- controller 重启回收、Server `controller_restarted` 收敛、operation 重启恢复、kill switch 和数据无损回滚演练；
- 真实 Agent 兼容性 Eval artifact；合成 artifact 不得计入恢复验收。

原数据盘、原数据库和备份至少保留到恢复演练及一个完整观察周期结束。恢复失败时切回原只读副本，不得通过删除新增表、grant、Workspace 或 Asset 来“回滚”。

截至 2026-07-26，当前开发宿主没有可验收的 `/srv/nanobot` project quota 数据盘、AppArmor 和 quota 工具，因此尚不能完成本手册要求的真实备份／恢复演练。该状态应记录为 `BLOCKED`，不能用归档脚本单元测试代替。
