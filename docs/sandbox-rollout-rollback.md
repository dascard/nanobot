# Nanobot Sandbox 灰度与回滚手册

## 1. 灰度前置条件

以下任一项未满足都不得开放工具：

- 全量 pytest 为 0 failures；
- 真实 Docker 隔离矩阵通过，而不是被跳过；
- 独立数据盘、project quota、总容量和磁盘水位门禁通过；
- Docker builtin seccomp 与精确 `nanobot-sandbox` AppArmor profile 生效；
- Nanobot Server、Worker 和 Sandbox 容器均看不到 Docker Socket；
- 固定镜像 digest/IMAGE ID allowlist 已核对；
- 备份、恢复和 kill switch 已演练；
- 旧 `bash/read/write/edit/grep/glob` 与通用宿主写入口继续硬禁用；
- Trace 中没有文件正文、完整命令、stdout/stderr、transport token 或宿主路径。

当前受信 Bearer 持有者可以声明 QQ 身份，因此首期只能开放给受信网关下的私聊
超级用户或精确 user ToolOverride，不能当作公网多租户身份系统。

## 2. 灰度顺序

1. 部署表、客户端和 sandboxd，但保持两个 feature flag 关闭。
2. 在 feature off 状态完成 UDS health/ready、镜像、AppArmor、磁盘和 Smoke 验收。
3. 为一个私聊超级用户的 Workspace 分配 project ID 和硬配额。
4. 只开放 `workspace_list/read/search/write`，观察容量、错误码和 Trace。
5. 再开放 `asset_import/asset_publish`，验证上传、Range 下载和收件人绑定 Token。
6. 单独把 `sandbox.exec_enabled` 打开，验证超时、OOM、输出限制和容器回收。
7. 观察至少一个完整使用周期后，逐个增加私聊 allowlist。
8. 群聊继续关闭；在群 Workspace、身份和硬配额均独立验收后另行灰度。

每增加一个 owner，都必须先完成 project quota 映射，不能先开放再补配额。

## 3. 立即回滚

1. 调用管理端 kill switch，把 `sandbox.enabled` 和
   `sandbox.exec_enabled` 设为关闭。
2. 删除对应 ToolOverride allowlist，确认七个工具不再进入 wire schema。
3. 取消活动运行；sandboxd 只处理同时满足名称前缀和双标签的自身容器。
4. 回滚 Nanobot Runtime 和 sandboxd 到最近一个已验证版本。
5. 保留当前与最近一个已验证 Sandbox 镜像，避免无限累计旧 IMAGE ID。
6. 保留数据库新增表、Workspace、Asset、project ID 映射和备份。
7. 保留旧 KT 文件/Bash 工具的禁用覆盖，避免应用版本回滚后重新形成绕过。

回滚不得执行全局 Docker prune、删除 volume、删除 `/srv/nanobot`、降级数据库表或
清空 Asset Store。功能关闭和数据删除是两个独立操作，本手册只授权前者。

## 4. 故障分流

- `runtime_unavailable`：检查 UDS、sandboxd、Docker、固定镜像和 AppArmor；不自动
  降级到宿主命令执行。
- `disk_pressure`：拒绝新写入/执行，保留已有数据；先核对 `df -h/df -i` 和配额。
- `workspace_quota_exceeded`：停止模型重试，核对该 Workspace project quota。
- `execution_timeout`、`process_oom_killed`、`output_limit_exceeded`：检查运行账本和
  Docker inspect，不提高模型可申请上限。
- 资产下载失败：保持 HMAC/收件人校验，不能临时改成公开静态 URL 或宿主路径。

QQ/NapCat 原生非图片文件投递在实机协议验证前继续使用短期签名下载链接。任何真实
QQ 外发 Smoke 都必须显式提供测试目标和凭据，不能自动向普通用户发送测试消息。
