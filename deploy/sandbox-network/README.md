# Sandbox 开发网络部署说明

## 目标拓扑

每个 developer Lease 都有独立的 `internal=true` bridge。该网络只连接一个 Sandbox 容器和一个固定摘要的 Squid 代理容器。Sandbox 不连接任何外联网；代理另接专用共享出口 bridge。

```text
Sandbox ── Lease internal bridge ── Squid
                                      │
                              egress uplink bridge
                                      │
                                   Internet
```

Lease 内部 bridge 使用 `com.docker.network.bridge.inhibit_ipv4=true`，不在宿主 bridge 上配置 IPv4 网关。Sandbox 因而只有到同网段代理的路径，没有宿主网关或默认公网路由。代理容器关闭 IPv4/IPv6 forwarding，不能充当三层路由器。

## 部署前置

1. 构建 `nanobot-sandbox-egress-proxy:2026.07.25`。
2. 确认 IMAGE ID 与 `config/sandbox-execution-profiles.v1.json` 的 `network_proxy_image_allowlist` 完全一致。
3. 保持 `NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED=false`，先执行真实拒绝矩阵。
4. 检查 Docker 支持 `internal` bridge 与 `com.docker.network.bridge.inhibit_ipv4=true`。
5. 按部署宿主的真实出口路径设置 `NANOBOT_SANDBOX_EGRESS_NETWORK_MTU`；默认 1450，必须通过真实 clone 验证，不能只按物理网卡 MTU 推断。
6. 真实矩阵全部通过后，才在 sandboxd 和 Nanobot Server 的启动环境中同时设置 `NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED=true`。

网络、代理和 Sandbox 都由 sandboxd 通过 Docker Engine API 管理，不使用 Compose 管理动态 Lease。不得提前手工创建同名网络或代理容器；同名但缺少所有权标签的对象会导致失败关闭。

## 固定安全参数

- 代理镜像：`nanobot-sandbox-egress-proxy:2026.07.25`
- 代理端口：`3128/tcp`，不发布到宿主
- 非 root 用户：`13:13`
- 只读根文件系统
- `cap-drop=ALL`
- `no-new-privileges`
- `net.ipv4.ip_forward=0`
- `net.ipv6.conf.all.forwarding=0`
- 无 Docker Socket、宿主目录、凭据或管理端口

共享出口网络只承载受管代理，设置 `enable_icc=false`。按 Lease 回收时只删除目标代理和目标内部网络；共享出口网络保留，禁止使用全局 prune。
