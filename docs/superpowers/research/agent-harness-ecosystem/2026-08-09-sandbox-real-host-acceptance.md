# Sandbox 独立 Linux 宿主真实验收记录

> 验收日期：2026-08-09
>
> 结论：通过
>
> 机器可读摘要：[六组 Smoke 摘要](2026-08-09-sandbox-host-smoke-summary.json)

## 1. 验收结论

本次在启用 KVM 的独立 Ubuntu 24.04 虚拟机中，以 root 运行生产
scripts/sandbox-smoke-test.sh。宿主前置检查和六组真实 Docker 测试全部通过：

- preflight：passed；
- groups：6；
- tests：6；
- passed：6；
- failed、failures、errors、skipped、blocked：均为 0；
- 最终结果：passed。

本次没有用 mock、静态配置检查、被 skip 的 pytest 或服务健康状态代替隔离测试。网络组执行了真实
GitHub clone、codeload 下载、PyPI 索引与 wheel 下载、npm registry ping，并同时验证非白名单域名、
非许可端口、IP 直连、私网、宿主、元数据地址、其他 Lease 代理、DNS rebind 和去除代理变量后的直连
均被拒绝。

该结论表示 Sandbox 实现已经在满足内核、AppArmor、Docker 和 project quota 条件的独立 Linux
宿主完成真实验收。它不等于任意生产机可以跳过本机 preflight、备份恢复演练、控制面安装或灰度门禁
直接打开业务开关。

## 2. 候选内容与不可变身份

验收虚拟机先导入仓库提交 0729e5154c3d85990341cba5c0394c983e7fdf0b，再同步本模块的候选文件。
以下摘要把实际运行内容与验收结果绑定：

| 对象 | 身份或 SHA256 |
| --- | --- |
| canonical Profile | e3d10301adf36e745994ab28849ce17d958875f3d675558695d9421e05c86af8 |
| 出口代理 Dockerfile | 5f422efec15cc50b19cda25789901ec5389e0184c4fa360d1d32b86c49945b3e |
| 出口代理 Squid 配置 | 3a59211120e99b593d7d56c2eb75bf4d176e789dbfe6e4275858419ca0b74d03 |
| 网络真实矩阵测试 | 4c44a7d45d7097cdfa70c2095385ab4e8db56afa15359792f2fb580a7b36f24e |
| Smoke 入口脚本 | 303da825748492387bb301fcd65f65d52d74990b40b5f0122c4082b20b397257 |
| 部署 manifest | 43ac5843c79d0c3ea9c0ddd35159be0ccabe025e0b69e07f6214ef3df3d243a6 |
| Smoke summary | 52f44d4cf3150c689af850d36db466b52ea7125afccb475d0a568c603c7529bf |

manifest 的 catalog_generation 为 stage11-20260809-final，完整 policy_sha256 为
939906040d03778ec87cf587c76deb86c1c6a7f080642cd26502cf52b1f90002。

实际运行的固定镜像为：

| 角色 | 镜像 | IMAGE ID | 默认用户 |
| --- | --- | --- | --- |
| Restricted | nanobot-sandbox-python:poc-20260720 | sha256:0c395eebdbd1aa663caf49021753148d04b03b12f7a3b96c0f3b8c7d362ea169 | 10001:10001 |
| Developer | nanobot-sandbox-developer:p5-20260725 | sha256:ac848b5823e5435115d3a1be0e6467b67e3b701ccd0fc6e89430ae820f0c44ba | 10001:10001 |
| 出口代理 | nanobot-sandbox-egress-proxy:2026.08.09 | sha256:26e5108ac2576446f18541e8966319e7fd91f3e6a5ffe4fe937993bdd7f29d3c | 13:13 |

出口代理基于固定
ubuntu/squid:6.6-24.04_beta@sha256:6a097f68bae708cedbabd6188d68c7e2e7a38cedd05a176e1cc0ba29e3bbe029
构建，没有使用浮动基础镜像作为运行契约。

## 3. 真实宿主事实

| 项目 | 实际值 |
| --- | --- |
| 虚拟化 | KVM，全虚拟化，8 vCPU |
| 系统 | Ubuntu 24.04 |
| 内核 | 6.8.0-136-generic |
| Docker Engine | 29.1.3 |
| Docker 存储与 cgroup | overlayfs、cgroup v2 |
| Docker SecurityOptions | AppArmor、builtin seccomp、cgroup namespace |
| AppArmor | 已启用 |
| Restricted Profile | nanobot-sandbox-restricted，enforce |
| Developer Profile | nanobot-sandbox-developer，enforce |
| 长期数据盘 | /dev/vdb 独立挂载到 /srv/nanobot |
| 文件系统 | XFS，32 GiB，挂载参数包含 prjquota |
| project quota | Accounting ON、Enforcement ON |

测试还通过容器 inspect 和行为验证确认非 root、只读根文件系统、cap-drop=ALL、
no-new-privileges、资源上限、默认断网、无 Docker Socket、超时终止进程树、容器重建后工作区保留，
以及不同 owner 不能互读。

## 4. 六组矩阵

| 分组 | 结果 | 耗时 |
| --- | --- | ---: |
| 基础安全 | 1 passed | 25.36 秒 |
| Lease | 1 passed | 41.94 秒 |
| Process | 1 passed | 30.75 秒 |
| Developer 工具链 | 1 passed | 19.60 秒 |
| 网络 | 1 passed | 42.98 秒 |
| 数据连续性 | 1 passed | 57.00 秒 |

原始证据目录为
/var/tmp/nanobot-sandbox-evidence/20260809T100641Z-17336，大小 952 KiB。
仓库保留其中完整、机器可读且哈希一致的 summary.json；临时虚拟机及原始运行目录在提交和推送完成后
定向销毁，不作为长期生产事实源。

## 5. 网络故障与实验环境出口说明

首次运行暴露了两个真实网络问题：

1. objects.githubusercontent.com 返回多个 A 记录，但当前出口只能连接其中一个地址。
   [Squid connect_timeout 官方说明](https://www.squid-cache.org/Doc/config/connect_timeout/)给出的默认值
   是 1 分钟，并说明连接超时后会尝试其他路径；该等待会耗尽 30 秒调用方时限。canonical 配置因此新增
   connect_timeout 5 seconds，让 Squid 在单个地址不可达时及时尝试同域其他地址。
2. 原来的 npm view npm version 会传输约 6 MiB，与“只验证 registry 可达”的目标不相称。测试改为
   npm ping --registry=https://registry.npmjs.org，仍访问真实 registry，不删除 npm 门禁。

最终验收时，Cloudflare DNS 从该虚拟机出口把 github.com 解析到 GitHub 官方地址
20.205.243.166，但该路由持续触发 Git 低速保护；同一虚拟机经工作机既有 HTTP 代理访问 GitHub
正常。为区分外部路由故障和 Sandbox 策略故障，验收环境使用了以下临时、精确的上游链：

1. Sandbox 只连接每个 Lease 的 canonical Squid；
2. Squid 仍先执行域名、端口、IPv4/IPv6 拒绝和 DNS rebind 策略；
3. 只有 Squid 已允许、且宿主目的地址精确为 20.205.243.166:443 的连接，才在临时 VM 的
   PREROUTING 层转到非 root 的瞬态 CONNECT 转发器；
4. 转发器通过工作机已有 HTTP 代理建立 CONNECT github.com:443，随后只透传 TLS 字节。

该链没有进入 Dockerfile、Squid 配置、Profile manifest、Sandbox 容器或生产脚本；没有向
Sandbox 注入宿主代理地址、凭据、额外网络、额外挂载或 extra_hosts。GitHub TLS 证书仍由容器内 Git
校验，真实 clone 得到提交 7fd1a60b01f91b314f59955a4e4d4e80d8edf11d。完整网络测试中的全部拒绝
断言随后继续执行并通过。因此这是一项明确记录的实验宿主上游路由修正，不是跳过 clone、伪造 HTTP
状态或把 shadow 测试改名。

两个宿主转发容器均为精确命名、非 root、只读根文件系统、cap-drop=ALL、
no-new-privileges、64 MiB 内存和 64 PID 上限；虚拟机内 CONNECT 服务使用 systemd 瞬态单元、
nobody:nogroup、ProtectSystem=strict、NoNewPrivileges=yes、64 MiB 内存和 64 Task 上限。
这些临时资源在交付收口时定向删除，不使用 Docker prune。

## 6. 运行命令

完整矩阵使用：

~~~bash
sudo env PATH=/opt/nanobot-smoke-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONPATH=/opt/nanobot-server /opt/nanobot-server/scripts/sandbox-smoke-test.sh --manifest /etc/nanobot/sandbox-execution-profiles.acceptance.json --data-root /srv/nanobot --evidence-root /var/tmp/nanobot-sandbox-evidence
~~~

仓库提交前的本地完整回归使用：

~~~bash
python -m pytest tests/ -v
~~~

结果为 6913 passed、12 skipped、0 failed。12 个 skip 仍是显式 opt-in 的外部环境测试，没有计入上述
六组真实宿主结果。

## 7. 对生产启用的含义

- 阶段 11 的“实现是否具备真实隔离证据”门禁已经解除。
- 每台实际生产候选机仍必须用自己的固定镜像和 manifest 重新运行 preflight 与六组 Smoke；不得复用
  本记录作为宿主配置凭据。
- 默认业务开关、session 执行开关和 Developer 双网络硬开关继续关闭，直到目标宿主完成控制面安装、
  Token 权限、备份恢复、kill switch 和有限 session 灰度。
- trusted_developer、私有仓库凭据和群聊 Sandbox 仍不在本次开放范围。
- Docker、内核、AppArmor、镜像、Squid 策略、网络拓扑、quota 或数据盘变化后，必须重新运行真实矩阵。
