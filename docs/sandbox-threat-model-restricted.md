# Restricted Sandbox 威胁模型

## 保护目标

Restricted Profile 用于短时、一次性的 Python 数据处理。保护目标是阻止模型代码访问宿主、Docker Engine、内外网络、其他 Workspace 和未授权资产，同时保证 Workspace 与 Runtime 的配额和持久性契约。

## 信任边界

- Nanobot Server 负责身份、Grant、capability、Workspace 和配额授权。
- sandboxd 是唯一可以访问 Docker Socket 与 `/srv/nanobot` 的组件。
- canonical Profile catalog 是 Server 与 sandboxd 共用的完整策略事实源，双方校验 generation 与完整策略 SHA。
- Sandbox 内的命令、环境变量和文件均视为不可信输入。

## 强制措施

- 每次命令创建一次性容器，固定 `network=none`。
- 非 root、只读根文件系统、`cap-drop=ALL`、`no-new-privileges`、固定 AppArmor、默认 seccomp。
- 只挂载目标 `/workspace`、目标 `/runtime` 和已授权只读 `/inputs`。
- 禁止模型指定镜像、网络、volume、宿主路径、用户、设备、namespace 或 capability。
- `/tmp` 是有限 tmpfs；CPU、内存、PID、执行时间和输出均有硬上限。
- `PIP_NO_INDEX=1`，只能使用镜像和 Runtime 中的离线缓存。

## 主要攻击与结论

| 攻击 | 结论 |
|---|---|
| 清空代理变量或直接连接 IP | 容器为 `network=none`，没有网络路径 |
| 访问 Docker Socket或宿主目录 | 未挂载，且调用方不能增加挂载 |
| 以 root、sudo 或 capability 提权 | 固定非 root，无 sudo，全部 capability 删除 |
| 写根文件系统或持久化恶意系统文件 | 根只读；持久写入只限 Workspace/Runtime |
| 跨 Workspace 读取 | 每次只挂目标 owner 的固定目录，并在操作前重验对象归属 |
| 超时后残留进程 | 一次性容器整体回收 |

## 剩余风险

Docker Engine、runc、内核、默认 seccomp 或 AppArmor 的漏洞不在应用层策略内消除。部署宿主必须及时修补，并在 P16 真实验证 AppArmor、OOM、PID、配额和跨 owner 隔离。Restricted 不承诺长进程、stdin、网络或后台服务。
