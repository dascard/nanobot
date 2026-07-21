# Nanobot Sandbox 运维手册

## 1. 上线结论与硬门禁

Sandbox 由宿主 `sandboxd` 通过 Docker Engine API 创建一次性容器。Nanobot
Server 只访问 Unix Socket，不挂载 Docker Socket 或 `/srv/nanobot`。在以下条件
全部满足前，必须保持 `sandbox.enabled=false` 和
`sandbox.exec_enabled=false`：

1. `/srv/nanobot` 是独立 XFS/ext4 挂载点，并启用 project quota。
2. Docker 的 `SecurityOptions` 同时包含 builtin seccomp 和 AppArmor。
3. `nanobot-sandbox` AppArmor profile 已精确加载，不能退回 `unconfined`。
4. 固定 Sandbox 镜像存在，IMAGE ID 位于 sandboxd allowlist，默认用户为
   `10001:10001`。
5. `scripts/sandbox-smoke-test.sh` 的真实 Docker 隔离矩阵通过。
6. 新 Workspace 在加入 ToolOverride allowlist 前已经分配唯一 project ID 和硬配额。

2026-07-20 的当前宿主只报告 builtin seccomp，没有启用 AppArmor；`/srv` 仍在
根 ext4 文件系统。因此当前状态是“代码可测试、生产门禁关闭”，不得启用 Sandbox。

## 2. 数据盘准备

格式化、分区、修改 `/etc/fstab` 和重挂载均不由仓库脚本自动执行。运维应在独立
窗口中使用以下模板编制变更单：

- `deploy/storage/sandbox-xfs-prjquota.example`：推荐的 XFS `prjquota` 方案。
- `deploy/storage/sandbox-ext4-project-quota.example`：已验证发行版支持时使用的
  ext4 project quota 方案。

挂载完成后先运行只读门禁：

```bash
scripts/check-sandbox-data-disk.sh /srv/nanobot
```

该脚本要求数据根目录本身就是独立挂载点、来源不同于根文件系统、文件系统为
XFS/ext4，并已启用对应 project quota 挂载参数。它不会创建目录或修改挂载。

随后安装 tmpfiles 配置并核验权限：

```bash
install -m 0644 deploy/systemd/nanobot-sandboxd.tmpfiles.conf \
  /etc/tmpfiles.d/nanobot-sandboxd.conf
systemd-tmpfiles --create /etc/tmpfiles.d/nanobot-sandboxd.conf
namei -l /srv/nanobot/workspaces /srv/nanobot/assets /srv/nanobot/runtime
```

## 3. Workspace project quota

project ID 必须由受控运维或配置系统分配，不能由模型提供。首次灰度可维护一张
`workspace_id → project_id → quota_bytes` 的唯一映射，并在开放该 owner 前执行：

```bash
scripts/assign-sandbox-project-quota.sh \
  --workspace-id <规范小写 UUID> \
  --project-id <唯一整数> \
  --quota-bytes 2147483648
```

默认只打印命令。确认执行入口已关闭、没有活动 Sandbox 容器后，才可增加：

```text
--quiesced --apply
```

脚本对 XFS 使用 `xfs_quota project/limit`，对 ext4 使用 project ID、`+P` 继承
属性和 `setquota -P`。它不会格式化或重挂载文件系统。当前数据库还没有自动分配
project ID 的生产调度器，因此扩大到动态多用户前，必须把该步骤接入受控的
Workspace 开通流程；未配置硬配额的 Workspace 不能加入灰度 allowlist。

Asset Store 不按 owner 重复存储物理 blob，应在独立监控中设置总容量告警和硬水位。
当水位达到配置阈值时，sandboxd 拒绝新上传、写入和执行，不自动删除资产。

## 4. Sandbox 镜像与 AppArmor

构建镜像时使用不可变版本，记录完整 IMAGE ID：

```bash
scripts/build-sandbox-image.sh <version>
docker image inspect nanobot-sandbox-python:<version> \
  --format 'ID={{.Id}} USER={{.Config.User}}'
```

把完整 `sha256:...` IMAGE ID 写入
`NANOBOT_SANDBOX_IMAGE_ALLOWLIST`，运行引用不得使用 `latest`。AppArmor profile
应由宿主安全变更流程安装：

```bash
apparmor_parser -r deploy/apparmor/nanobot-sandbox
grep '^nanobot-sandbox ' /sys/kernel/security/apparmor/profiles
docker info --format '{{json .SecurityOptions}}'
```

指定 profile 不存在、securityfs 不可读或 Docker 未报告 AppArmor 时，
`/v1/readyz` 必须失败，不得改成 `unconfined` 绕过。

## 5. sandboxd 安装

1. 使用 `requirements-sandboxd.lock` 创建独立 venv。
2. 创建 `nanobot-sandboxd` 组和随机内部 Token；Token 文件权限为 `0600`。
3. 复制并填写 `/etc/nanobot/sandboxd.env`，固定镜像引用与 IMAGE ID allowlist。
4. 安装 `deploy/systemd/nanobot-sandboxd.service`，先保持应用 feature flag 关闭。
5. 启动后从宿主通过 UDS 检查 `healthz/readyz`；Nanobot 容器只读挂载
   `/run/nanobot-sandboxd/`。

不得给 Nanobot Server、Worker 或临时 Sandbox 容器挂载
`/var/run/docker.sock`。`sandboxd` 持有 Docker Socket，必须按宿主 root 等价代码审查。

## 6. runtime TTL 清理

`runtime` 只保存可重建缓存，不进入备份。预览超过七天的候选：

```bash
scripts/cleanup-sandbox-runtime.sh --data-root /srv/nanobot --ttl-hours 168
```

实际清理要求 kill switch 已关闭执行入口、没有活动 Sandbox 容器，并显式传入
`--quiesced --apply`。systemd timer 还增加一次性批准门禁：只有维护窗口内由 root
创建 `/run/nanobot-sandboxd/runtime-cleanup-approved`，service 才会执行，并在开始前
消费该标记。不要在尚未建立维护窗口流程时启用 timer。

TTL 清理后若总量仍高于 `NANOBOT_SANDBOX_RUNTIME_MAX_BYTES`，脚本失败并告警，
不删除 Workspace 或 Asset。应用层每 Workspace runtime 配额和磁盘硬水位仍同时生效。

## 7. 真实隔离 Smoke

默认 pytest 会跳过真实 Docker 测试：

```bash
python -m pytest tests/test_sandbox_security.py -v
```

显式测试使用：

```bash
scripts/sandbox-smoke-test.sh nanobot-sandbox-python:<version>
```

测试脚本不会调用 `sudo`、任何 prune 或全局清理。它把测试前后 `df -h`、
`df -i`、容器清单、`docker system df`、SecurityOptions、镜像用户、AppArmor
状态和 pytest 输出保存到：

```text
${XDG_CACHE_HOME:-$HOME/.cache}/nanobot-sandbox-smoke/<UTC 时间>/
```

完整矩阵未通过时不得开启 feature flag。尤其不能把“默认跳过”当作隔离验收通过。

## 8. 监控与应急关闭

管理接口只展示健康、容量、当前/失败运行和脱敏运行账本，不提供文件正文浏览。
出现 Docker、AppArmor、磁盘或输出异常时先调用只关闭型 kill switch：

```text
POST /api/v1/admin/sandbox/kill-switch
```

它只把 `sandbox.enabled` 和 `sandbox.exec_enabled` 设为关闭，不删除 Workspace、
Asset 或运行账本。随后取消必要的活动运行，检查双标签和名称前缀；禁止全局 prune。

资产外发默认使用收件人绑定、短期 HMAC 签名的下载链接，并支持 HTTP Range。
QQ/NapCat 原生非图片文件消息未实机验收前，不开放模型直写 `[CQ:file,...]`；渲染器
会拒绝该形式并使用签名下载链接回退。
