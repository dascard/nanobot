# Sandbox Egress Proxy 镜像

该镜像基于固定摘要的 Ubuntu Squid 6.13，仅提供 `3128/tcp` 的 HTTP/HTTPS CONNECT 代理。域名白名单和目标地址拒绝规则固化在镜像中，Sandbox 不挂载也不能修改代理配置。

运行时必须同时满足：

- 代理容器仅连接目标 Lease 的内部网络和专用出口网络；
- 不发布宿主端口，不挂 Docker Socket；
- 非 root、只读根文件系统、`cap-drop=ALL`、`no-new-privileges`；
- `net.ipv4.ip_forward=0` 与 `net.ipv6.conf.all.forwarding=0`；
- Sandbox 只连接 Lease 内部网络，不能连接出口网络。

构建命令：

```bash
docker build \
  --tag nanobot-sandbox-egress-proxy:2026.07.25 \
  docker/sandbox/egress-proxy
```

构建后必须把完整 IMAGE ID 写入 canonical Profile catalog 的 `network_proxy_image_allowlist`，不得用浮动 tag 作为运行契约。
