# Nanobot Sandbox 运维手册

## 1. 控制面与事实源

Sandbox 由宿主 systemd 服务 sandboxd 通过 Docker Engine API 创建一次性容器。Nanobot Server 只访问 Unix Socket，不挂载 Docker Socket 或 /srv/nanobot；Worker 不访问 sandboxd。

生产控制面有三类彼此独立的状态：

- 宿主硬上限：sandbox.infrastructure_enable_allowed，只允许由 root 管理的环境变量设置，默认关闭，Web 不可修改。
- 业务开关：sandbox.enabled 与 sandbox.exec_enabled，由 Web「Sandbox 管理」页控制；sandbox.group_enabled 首期固定关闭。
- 会话授权：sandbox_access_grants 是七个 Sandbox 工具的唯一授权事实源，通用 ToolOverride 和 private_superuser 均不能授权 Sandbox。

授权主体是 canonical chat session：

~~~text
chat_stream_id = platform:encoded_external_session_id:chat_type
~~~

user_id 只用于页面显示、操作者识别和审计，不能作为授权键。首期每个私聊 session 默认拥有独立 Workspace；未来如需共享，必须通过显式 Workspace 绑定功能实现，不能自动按 user_id 合并。

project quota 的唯一事实源是 workspace_quota_bindings。project ID 由数据库从 10000 起原子分配，宿主 TSV 不再参与日常分配。

## 2. 上线硬门禁

以下条件全部满足前，必须同时保持宿主硬上限、sandbox.enabled 和 sandbox.exec_enabled 关闭：

1. /srv/nanobot 是独立 XFS/ext4 挂载点，并启用 project quota。
2. Workspace、Asset Store、runtime 的容量预算、备份和磁盘水位已配置。
3. Docker SecurityOptions 包含 builtin seccomp 和 AppArmor。
4. nanobot-sandbox AppArmor profile 已精确加载；profile 缺失时必须失败关闭。
5. 固定 Sandbox 镜像存在，IMAGE ID 位于 sandboxd allowlist，默认用户为 10001:10001。
6. 普通 sandboxd Token 与独立管理 Token 均为 root 管理的 0600 文件。
7. scripts/sandbox-smoke-test.sh 的真实 Docker 隔离矩阵全部通过。
8. Nanobot Server、Worker 和 Sandbox 容器均看不到 Docker Socket。
9. 协调备份、恢复和无损 kill switch 已演练。

代码测试通过、healthz 成功或 Docker 配置看起来正确，都不能替代真实隔离 Smoke。

## 3. 安装与部署顺序

生产脚本只负责基础设施，不再接受 owner ID 或 project ID：

~~~bash
sudo scripts/manage-sandbox-production.sh configure ...
sudo scripts/manage-sandbox-production.sh prepare-host --initialize-storage
sudo scripts/manage-sandbox-production.sh build-image
sudo scripts/manage-sandbox-production.sh smoke
sudo scripts/manage-sandbox-production.sh install-control-plane
sudo scripts/manage-sandbox-production.sh deploy
~~~

deploy 完成后所有 Sandbox 开关仍应关闭。provision-owner、enable-workspace、enable-assets、enable-exec 和 disable-owner 是兼容拒绝入口，不会执行旧 ToolOverride 或 TSV 操作。

宿主硬上限只能在全部基础设施门禁通过后，由 root 修改 Nanobot Runtime 环境：

~~~text
NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED=true
~~~

设置硬上限只代表“允许 Web 开启”，不会自动打开业务开关或授予任何 session。

## 4. 数据盘与目录

先按变更单准备独立数据盘，再执行只读门禁：

~~~bash
scripts/check-sandbox-data-disk.sh /srv/nanobot
~~~

参考模板：

- deploy/storage/sandbox-xfs-prjquota.example
- deploy/storage/sandbox-ext4-project-quota.example

随后安装 tmpfiles 配置并核验权限：

~~~bash
install -m 0644 deploy/systemd/nanobot-sandboxd.tmpfiles.conf \
  /etc/tmpfiles.d/nanobot-sandboxd.conf
systemd-tmpfiles --create /etc/tmpfiles.d/nanobot-sandboxd.conf
namei -l /srv/nanobot/workspaces /srv/nanobot/assets /srv/nanobot/runtime
~~~

不得在当前根文件系统、仓库目录、容器 writable layer 或 WSL /mnt/d 中保存生产 Workspace/Asset。

## 5. sandboxd 管理接口与权限

sandboxd 使用两个独立 Token：

- 普通 Token：文件、资产和运行接口。
- 管理 Token：Workspace ensure 与 project quota apply/inspect 接口。

管理接口只接受 request_id、workspace UUID、project ID、quota bytes 和 generation，不接受宿主路径、命令或 Docker 参数。普通 Token 调用管理接口必须返回拒绝。

systemd 单元因 project quota helper 需要 CAP_SYS_ADMIN；sandboxd 同时持有 Docker Socket，本身就是 root 等价控制面。必须保持：

- 固定 helper 路径 /opt/nanobot-server/scripts/assign-sandbox-project-quota.sh；
- helper 为 root 拥有的普通文件，不是符号链接，group/other 不可写；
- 结构化 argv，shell=False；
- subprocess stdout/stderr 不进入 API、日志或 Web 错误详情。

## 6. Web 会话授权

在 Web「Sandbox 管理」页完成授权：

1. 从服务端真实出现过的私聊 session 中选择目标。
2. 核对 canonical chat_stream_id；user_id 仅作显示。
3. 选择 off、workspace、assets、exec 四档能力。
4. 输入 Workspace 配额和审计原因。
5. 一次保存产生一个 set_access operation。
6. 等待 operation succeeded，且 quota 状态为 applied、desired 与 applied 完全一致。
7. 最后再按灰度阶段打开对应业务开关。

能力包含关系固定为：

~~~text
off < workspace < assets < exec
~~~

降级和关闭立即收窄权限；升级必须等待 sandboxd 创建 Workspace 并确认 project quota。关闭能力不会删除 Workspace、Asset、project ID、grant 或 operation 账本。

## 7. 配额修改

Workspace 配额可从 Web 独立修改：

- 缩容不得低于 Workspace 当前 used_bytes。
- 总 desired quota 不得超过 sandbox.total_quota_bytes。
- 每次修改增加或沿用受 fencing 保护的 generation。
- desired 与 applied 不一致、状态非 applied 或应用失败时，该 Workspace 的全部 Sandbox 工具保持拒绝。
- project ID 只读且不会因关闭能力而回收。

管理写操作返回 202 和 operation_id。持久化 runner 使用租约、幂等 request ID、有限重试、指数退避、generation/version fencing 和重启恢复；不得把 HTTP 202 误判为宿主配额已经生效。

## 8. 旧 TSV 一次性迁移

旧 /etc/nanobot/sandbox-projects.tsv 只允许通过一次性迁移工具读取。先完整预检：

~~~bash
python scripts/migrate-sandbox-project-map.py \
  --database /opt/nanobot-server/nanobot.db \
  --map /etc/nanobot/sandbox-projects.tsv \
  --data-root /srv/nanobot
~~~

确认预检结果后再显式应用：

~~~bash
python scripts/migrate-sandbox-project-map.py \
  --database /opt/nanobot-server/nanobot.db \
  --map /etc/nanobot/sandbox-projects.tsv \
  --data-root /srv/nanobot \
  --apply
~~~

迁移会校验数据库迁移版本、Workspace 状态、quota、project ID 冲突和宿主 xattr，并创建 root-only 固定备份。导入绑定初始为 pending、applied_quota_bytes=0，不创建 grant；必须再由 Web/operation runner 重新确认后才能授权。迁移完成后 TSV 只作历史证据，不再写入。

## 9. runtime TTL 清理

runtime 只保存可重建缓存，不进入备份。默认先预览：

~~~bash
sudo scripts/manage-sandbox-production.sh runtime-cleanup
~~~

实际执行要求 kill switch 已关闭执行入口、没有活动 Sandbox 容器，并显式批准：

~~~bash
sudo scripts/manage-sandbox-production.sh runtime-cleanup --apply
~~~

清理不得删除 Workspace 或 Asset，不得执行 Docker 全局 prune。

## 10. 真实隔离 Smoke

默认测试会跳过真实 Docker 隔离项。生产验收必须显式运行：

~~~bash
scripts/sandbox-smoke-test.sh nanobot-sandbox-python:<version>
~~~

至少验证非 root、只读根、network=none、无 Docker Socket、cap drop、AppArmor、seccomp、CPU/内存/PID/tmpfs 限制、超时终止整个进程树、输出硬上限、Workspace 跨容器持久化、A/B Workspace 隔离，以及 reconciler 不触碰非 Nanobot 容器。

Smoke 前后保存 df、inode、Docker 占用、容器与镜像清单和 inspect 证据。禁止把 skipped 当作 passed。

## 11. 监控与应急关闭

Web 页面展示 sandboxd 健康、镜像 ID、AppArmor、磁盘水位、Workspace 占用、operation、运行账本和审计；不展示命令、stdout/stderr、文件正文或宿主路径。

出现控制面、磁盘、配额或隔离异常时：

1. 调用 POST /api/v1/admin/sandbox/kill-switch。
2. 确认 sandbox.enabled 与 sandbox.exec_enabled 均为 false。
3. 必要时取消单个活动运行。
4. 只检查同时满足名称前缀和双标签的 sandboxd 容器。
5. 保留数据库、Workspace、Asset、grant、quota binding 和 operation 账本。

禁止全局 prune、删除 volume 或通过恢复旧 KT bash/read/write/edit/grep/glob 绕过故障。
