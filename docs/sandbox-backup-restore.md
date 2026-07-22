# Nanobot Sandbox 备份与恢复手册

## 1. 备份范围

必须协调备份：

- Nanobot SQLite 数据库；
- /srv/nanobot/workspaces/；
- /srv/nanobot/assets/ 中已完成的不可变 blob。

数据库备份必须包含并保持一致：

- workspaces、assets、workspace_assets、sandbox_runs；
- sandbox_access_grants；
- workspace_quota_bindings；
- sandbox_admin_operations；
- sandbox_project_sequences；
- schema_migrations。

明确不备份：

- /srv/nanobot/runtime/；
- 单次运行的 /inputs staging；
- assets/sha256/.tmp/ 未完成上传；
- 一次性 Sandbox 容器及其 writable layer；
- 旧 sandbox-projects.tsv 作为活动配置。

数据库保存 canonical session 授权、Workspace/Asset 关系、project ID 和 quota generation；文件系统保存实际内容。只备份其中一侧不能视为可恢复备份。

## 2. 创建协调备份

1. 调用 kill switch，确认 sandbox.enabled 与 sandbox.exec_enabled 均为 false。
2. 由 root 同时关闭 infrastructure_enable_allowed。
3. 取消或等待所有 Sandbox 运行结束，确认没有 nanobot-sbx-* 活动容器。
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
9. 记录备份目录、模式、风险标记、源/目标设备、容量与水位、数据库 schema migration 版本、最大 project ID、Workspace/Asset/grant 数量和时间。
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
- sandbox_project_sequences.next_value 大于所有已分配 project ID；
- 每个 grant 的 workspace_id 存在；
- 每个 workspace_assets 链接的 Workspace 和 Asset 均存在；
- 每个 Workspace 的 used_bytes 不超过 desired quota。

归档中不得包含绝对路径、..、runtime、input staging、Token、数据库外副本或宿主密钥。

## 4. 恢复步骤

1. 保持宿主硬上限和两个业务开关关闭，停止固定服务与 sandboxd。
2. 记录现有数据库和数据盘状态；不要覆盖原盘。
3. 挂载一块空的、已启用 project quota 的恢复数据盘。
4. 在空暂存目录中校验哈希后解包，保留 UID/GID、ACL 和 xattr。
5. 恢复 SQLite 数据库到新的目标文件，不直接覆盖仍被进程打开的数据库。
6. 核对数据库中的 Workspace、Asset、grant 和 blob 数量与大小。
7. 启动 sandboxd 和 Nanobot，但继续保持 infrastructure_enable_allowed=false。
8. 对每个 Workspace 从 Web 重新提交当前 desired quota，或调用相同管理 API 生成 set_quota operation。
9. 等待所有 quota operation succeeded，确认宿主 project ID、generation、desired 与 applied 一致。
10. 运行数据盘门禁、A/B session 隔离和真实 Docker Smoke。
11. 只对一个私聊 canonical session 恢复 Workspace 能力，再依次恢复 Assets 与 Exec。
12. 全部验收后才允许 root 打开宿主硬上限，并按灰度手册开启业务开关。

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
- 数据盘水位、AppArmor、seccomp 和 Docker inspect；
- 无 Docker Socket、无网络、非 root、只读根和资源限制；
- 容器重建后的 Workspace 持久化；
- operation 重启恢复、kill switch 和数据无损回滚演练。

原数据盘、原数据库和备份至少保留到恢复演练及一个完整观察周期结束。恢复失败时切回原只读副本，不得通过删除新增表、grant、Workspace 或 Asset 来“回滚”。
