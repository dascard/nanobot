# Developer Sandbox 威胁模型

## 保护目标

Developer Profile 在保留代码工作台体验的同时，只允许匿名访问明确的 GitHub、PyPI 与 npm 域名。模型代码不得访问宿主、私网、云元数据、其他 Lease、其他服务容器或任意非白名单公网目标。

本阶段不支持 `trusted_developer`、私有仓库凭据、SSH Git、任意公网和跨 controller 重启存活的后台进程。

私有仓库与写入凭据的冻结范围、红线和未来设计约束见
[《Sandbox 私有仓库凭据范围》](sandbox-private-repository-credentials.md)。

## 拓扑与信任边界

每个 Lease 拥有独立的内部 bridge，网络中只有目标 Sandbox 与固定摘要的 Squid。Sandbox 只连接该内部网络。Squid 同时连接专用出口 bridge，且关闭 IPv4/IPv6 forwarding。

`HTTP_PROXY` 与 `HTTPS_PROXY` 只告诉 Git、pip、npm 和 curl 如何选路，不是安全边界。Sandbox 即使删除或篡改这些变量，也没有默认公网路由、宿主网关或到其他 Docker 网络的三层路径。

内部 bridge 同时使用：

- `internal=true`
- `enable_ip_masquerade=false`
- `inhibit_ipv4=true`

最后一项阻止 Docker 在宿主 bridge 上配置可被 Sandbox 访问的 IPv4 网关。Sandbox 与 Squid 仍可在同一二层网段通信。

## 代理策略

运行镜像固定为 canonical Profile 中的完整 IMAGE ID，当前实现是基于固定摘要 Ubuntu Squid 6.13 的派生镜像。代理容器：

- 使用 `13:13` 非 root 用户；
- 根文件系统只读；
- `cap-drop=ALL`、`no-new-privileges`；
- 不发布端口、不挂 Docker Socket、宿主目录或凭据；
- 只开放 `3128/tcp`，无独立管理端口；
- 日志和缓存有界，不记录 Sandbox 命令正文或响应正文。

域名白名单固定为：

- `github.com`
- `api.github.com`
- `objects.githubusercontent.com`
- `raw.githubusercontent.com`
- `codeload.github.com`
- `pypi.org`
- `files.pythonhosted.org`
- `registry.npmjs.org`

HTTP 只允许目标端口 80/443，CONNECT 只允许 443。域名通过后仍校验解析出的目标 IP；IPv4/IPv6 loopback、RFC1918、链路本地、共享地址、Docker 常用私网、云元数据、保留地址、组播和 ULA 均拒绝。域名白名单检查先于目标 DNS/IP 检查，允许域名解析到私网时仍会被拒绝。

## 主要攻击与结论

| 攻击 | 强制边界 |
|---|---|
| 清空或伪造代理变量后直连公网 | Lease 内部网络无默认公网路由 |
| 直接连接公网 IP 绕过域名规则 | Sandbox 不连接出口网络；只有 Squid 能出网 |
| 访问宿主网关 | 内部 bridge 抑制宿主 IPv4 网关 |
| 访问其他 Lease 或服务容器 | 每 Lease 独立网络，Sandbox 不加入共享出口网络 |
| 通过 Squid 访问私网或元数据 | Squid 在域名白名单后再次拒绝目标 IP CIDR |
| DNS rebinding | Squid 对最终解析地址应用私网/保留地址 ACL |
| CONNECT 到非 443 端口 | CONNECT 端口 ACL 拒绝 |
| HTTP 重定向到私网或非白名单域名 | 客户端的下一跳仍经 Squid，重新执行完整 ACL |
| hex、整数或 IPv6 私网编码 | URL 解析后的目标仍受域名与 IPv4/IPv6 ACL 约束 |
| 把代理当路由器 | Sandbox 无 NET_ADMIN；代理 forwarding 固定为 0 |
| 篡改或替换代理 | Profile 完整策略 SHA、代理 IMAGE ID、镜像用户/入口和所有权标签均重验 |

## 生命周期与失败关闭

代理和内部网络与 Lease 同生共死。代理缺失、额外网络、端口发布、镜像漂移、策略 SHA 漂移或 controller epoch 漂移都会使 Lease 失效并整体回收。周期 reconciler 清理孤儿代理与内部网络，只按明确 Lease ID 定向操作。

Server 的 `NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED` 与 sandboxd 同名硬开关必须同时为 true；数据库记录不能越过该上限。关闭任一侧后拒绝新 developer EXEC。

## 剩余风险

- CONNECT 代理不能查看 TLS 加密后的 HTTP 内容；安全边界是连接目标域名、解析地址和端口，而不是 URL 路径。
- 白名单站点自身的供应链、恶意仓库内容和依赖包不因此可信。系统不自动执行仓库脚本。
- Docker、Squid、内核和 DNS 解析器漏洞需要通过固定版本、及时修补和 P16 真实矩阵降低风险。
- 本阶段只有匿名 clone/install，不注入 PAT、SSH key、GitHub App Token 或宿主 SSH Agent。
