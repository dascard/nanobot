# Sandbox 私有仓库凭据范围

## 状态

本文件是未来设计记录，不是已实现能力，也不是可调用接口说明。

当前 Milestone B/C 只支持通过 Developer Profile 匿名克隆公网仓库，以及从明确域名白名单安装公开依赖。系统不支持私有仓库、SSH Git、仓库写入或任何形式的凭据注入。

私有仓库认证失败属于当前能力边界，不应被当作网络出口故障重试，也不得在产品、Prompt、验收或发布说明中宣称已经支持。

## 当前强制边界

当前实现没有以下能力：

- 没有私有仓库授权 API、GitHub App 安装绑定或 Token 签发端点；
- 没有 `repo_push`、私有 clone 或凭据管理模型工具；
- 不向 Sandbox 环境变量注入 PAT、GitHub Token、SSH key、`GIT_ASKPASS` 或 `SSH_AUTH_SOCK`；
- 不挂载宿主 `~/.ssh`、SSH Agent Socket、`.netrc`、Git 凭据文件或 Secret 目录；
- 不写入仓库 `.git/config`，也不配置 `credential.helper store`；
- 不在 Workspace、Runtime、业务账本、Trace 或管理页面保存原始凭据。

Developer Profile 的 `HTTP_PROXY` 与 `HTTPS_PROXY` 只用于网络选路。它们不是认证通道，也不是凭据安全边界。

## 永久红线

未来即使开放私有仓库，也必须继续满足以下约束：

- 长期 PAT 不得进入 Sandbox；
- 凭据不得写入 Workspace、Runtime、镜像层、Git 配置或命令历史；
- 不得挂载宿主 SSH 目录、SSH Agent Socket 或通用 Secret 目录；
- Token 必须绑定当前身份、canonical session、Grant、Workspace、Profile、仓库和操作类型；
- Token 默认只读、短期有效、单仓库可用，并在操作完成或超时后立即失效；
- 审计只记录仓库标识、权限范围、签发与撤销结果等 metadata，不记录 Token、认证头、命令正文或远端响应正文；
- 私网、宿主、云元数据和非白名单域名的拒绝边界不得因认证能力而放宽。

## 未来只读 clone 设计约束

未来开放必须另立独立里程碑与威胁模型，并至少完成以下流程：

1. Server 重新校验当前身份、canonical session、Grant、Workspace 和允许的 Profile。
2. Server 将请求仓库规范化为精确的 provider、owner 与 repository，不接受任意转发 URL。
3. 凭据服务签发短期、单仓库、最小权限、默认只读且绑定域名的 GitHub App Token。
4. Sandbox 只持有一次性、不具备上游权限的占位句柄；真实 Token 只存在于受控凭据服务内。
5. 受控 Git HTTPS 网关校验句柄、仓库和操作，再向固定上游注入认证。
6. clone 结束、失败或超时后立即撤销句柄，并按有界 metadata 记录结果。

当前 Squid 是标准 HTTPS CONNECT 代理，无法查看端到端 TLS 内的 HTTP 请求，也就无法安全注入 GitHub `Authorization` 头。未来实现必须单独引入并威胁建模 Git HTTPS 凭据网关，或采用经过独立评估的等价方案；不得把当前 Squid 配置描述为认证代理。

## 未来写入能力

仓库写入必须使用单独的 `repo_push` capability，不能由 EXEC 或只读 clone 权限隐式继承。每次操作必须：

- 显示目标仓库、分支和待推送提交；
- 要求人工确认；
- 签发独立的短期写凭据；
- 默认拒绝 force push、删除分支、修改保护分支和扩大仓库范围；
- 在请求边界再次校验 capability、目标与确认凭据；
- 操作完成后立即撤销写凭据。

## 开放前验收

未来里程碑至少需要真实验证：

- Token 只能读取绑定的单个仓库，不能横向访问同组织其他仓库；
- Sandbox、Workspace、Runtime、Git 配置、进程环境、日志和 Trace 中均不存在原始 Token；
- 占位句柄重放、跨 session 使用、跨 Workspace 使用和过期使用全部失败；
- 重定向、子模块、Git LFS 和依赖 URL 不能把凭据带到其他域名；
- clone 结束、失败、超时、Lease 回收和 controller 重启后凭据均失效；
- `repo_push` 未授权、未确认、目标漂移或 force push 时全部失败关闭；
- 现有宿主、私网、云元数据、跨 Lease 与非白名单拒绝矩阵继续通过。
