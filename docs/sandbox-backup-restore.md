# Nanobot Sandbox 备份与恢复手册

## 1. 备份范围

必须协调备份：

- Nanobot SQLite 数据库；
- `/srv/nanobot/workspaces/`；
- `/srv/nanobot/assets/` 中已完成的不可变 blob。

明确不备份：

- `/srv/nanobot/runtime/`；
- 单次运行的 `/inputs` staging；
- `assets/sha256/.tmp/` 未完成上传；
- 一次性 Sandbox 容器及其 writable layer。

数据库保存 Workspace、Asset 授权关系，文件系统保存实际内容，两者必须来自同一个
停止写入的维护窗口。仅备份其中一侧不能视为可恢复备份。

## 2. 创建协调备份

1. 调用管理端 kill switch，确认两个 Sandbox feature flag 均为关闭。
2. 取消或等待所有 Sandbox 运行结束，确认没有 `nanobot-sbx-*` 活动容器。
3. 停止当前 Compose 项目的全部固定服务，避免聊天、上传、任务 Worker 继续写库。
4. 停止 sandboxd；保留 `/srv/nanobot` 和数据库，不运行任何 prune。
5. 在独立备份挂载点上预览并执行：

```bash
scripts/sandbox-coordinated-backup.sh \
  --database /opt/nanobot-server/nanobot.db \
  --data-root /srv/nanobot \
  --destination /mnt/nanobot-backup \
  --quiesced \
  --apply
```

脚本会再次确认当前 Compose 服务和临时 Sandbox 容器均未运行，使用 SQLite backup
API 创建数据库副本，分别归档 workspaces/assets，并生成 `manifest.sha256`。目标
必须是不同于数据盘的独立挂载点；已有同名目录不会被覆盖。失败时保留
`.partial` 目录供人工核查，不自动递归删除。

6. 执行 `sha256sum -c manifest.sha256`，把结果、备份目录、源/目标设备与时间写入
   变更记录。
7. 重启 sandboxd 和固定服务，但继续保持 feature flag 关闭，完成健康检查后再按
   灰度流程恢复。

## 3. 恢复前验证

恢复必须先在非生产宿主演练。至少验证：

```bash
cd <备份目录>
sha256sum -c manifest.sha256
sqlite3 nanobot.db 'PRAGMA quick_check;'
tar -tf workspaces.tar >/dev/null
tar -tf assets.tar >/dev/null
```

检查归档中没有绝对路径、`..` 路径、runtime 或输入 staging。备份介质和恢复暂存
目录不得对 Nanobot/Sandbox 容器开放写权限。

## 4. 恢复步骤

1. 保持 kill switch 关闭，停止固定服务与 sandboxd，记录现有数据库和数据盘状态。
2. 挂载一块空的、已启用 project quota 的恢复数据盘；不要覆盖原盘。
3. 在空暂存目录中校验哈希后解包，保留 UID/GID、ACL 和 xattr。
4. 恢复 SQLite 数据库到新的目标文件；不要直接覆盖仍被进程打开的数据库。
5. 核对数据库中的 Workspace 数量、Asset 哈希/大小与文件系统清单。
6. 按 project ID 映射重新应用每个 Workspace 的硬配额，并运行数据盘门禁。
7. 使用只读工具检查 A/B owner 隔离、Asset 授权和 Workspace 持久化。
8. 切换挂载/数据库指向，启动 sandboxd，确认 `readyz` 和真实 Docker Smoke 通过。
9. 仅向一个私聊超级用户恢复工作区工具，再单独恢复 `sandbox_exec`。

原数据盘和原数据库至少保留到恢复演练及一个完整观察周期结束。恢复失败时切回原
只读保留副本；不得通过删除新增表、Workspace 或 Asset 来“回滚”。

## 5. 恢复验收证据

- `manifest.sha256` 全部通过；
- SQLite `quick_check` 为 `ok`；
- Workspace/Asset 数量与大小核对；
- A/B owner 隔离和 Asset 授权测试；
- project quota、磁盘水位、AppArmor/seccomp 和 Docker inspect；
- 容器重建后的 Workspace 持久化；
- kill switch 和无损回滚演练记录。
