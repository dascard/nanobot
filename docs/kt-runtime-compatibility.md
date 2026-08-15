# KT 可选 Runtime 的安装、启用与升级

## 1. 定位

Nanobot Server 默认使用框架无关的 Native Runtime。KohakuTerrarium（KT）只保留为显式启用的兼容
Runtime，不属于核心生产依赖，也不再以 Git submodule 或仓库内 vendor 源码参与构建。

当前验证版本为 KT `v2.0.0`，固定到 commit
`acc2423df7a3e213d7de19d70bc2e507a405a2f8`。固定源记录位于 `requirements-kt.in`，可重放锁位于
`requirements-kt.lock`。不得把浮动 `main`、nightly 或未固定的 VCS 引用作为部署依赖。

KT `v2.0.0` 要求 `pydantic>=2.12,<2.13` 及 `pydantic-core>=2.41,<2.42`。Nanobot 的
Native、测试、KT 与 sandboxd 环境统一使用 Pydantic `2.12.5` / pydantic-core `2.41.5`，
避免同一个进程出现不可求解的 ABI 组合。调整该基线时必须同时重生成四套相关锁并运行
Native、KT 和 sandboxd 验证。

默认生产镜像只安装 `requirements-prod.lock`，并通过 `.dockerignore` 排除 KT 的输入文件和锁文件。因此，
默认镜像不能启用 KT，也不会因 KT 代码或依赖变化重建第三方依赖层。需要 KT 的兼容环境必须独立安装
可选锁文件或构建单独的内部镜像，不得修改默认镜像的依赖合同。

## 2. 安装

Native 开发、测试或生产环境只安装对应的核心锁文件：

```bash
python -m pip install -r requirements-prod.lock
```

只有兼容性测试或明确批准的 KT 灰度环境才额外安装：

```bash
python -m pip install -r requirements-kt.lock
python -m pip check
```

`requirements-kt.lock` 是在 `requirements-test.lock` 约束下生成的完整可选环境锁。不能只执行未锁定的
`pip install kohakuterrarium`，也不能重新引入 `vendor/KohakuTerrarium`。

## 3. 启用与灰度

默认配置必须保持：

```dotenv
NANOBOT_AGENT_RUNTIME_DEFAULT=native
NANOBOT_AGENT_RUNTIME_KT_ENABLED=0
NANOBOT_AGENT_RUNTIME_KT_ROLLOUT_BPS=0
NANOBOT_AGENT_RUNTIME_KT_SESSIONS=
```

安装可选依赖本身不会启用 KT。启用兼容 Runtime 时，先设置
`NANOBOT_AGENT_RUNTIME_KT_ENABLED=1`，再通过以下两种方式之一限定范围：

- `NANOBOT_AGENT_RUNTIME_KT_SESSIONS`：逗号分隔的精确 canonical session ID；不允许通配符；
- `NANOBOT_AGENT_RUNTIME_KT_ROLLOUT_BPS`：稳定哈希灰度的万分比，合法范围为 `0..10000`。

不建议把 `NANOBOT_AGENT_RUNTIME_DEFAULT` 改为 `kt`。若确需这样做，必须先确认目标环境安装了可选锁、
完成完整 KT 兼容测试，并准备将默认值恢复为 `native` 的回滚配置。运行时一旦为某个请求选定 Runtime，
失败后不会跨 Runtime 自动重试，以避免重复副作用。

## 4. 验证

核心依赖安装完成、KT 尚未安装时，先运行隔离证明：

```bash
python -m pytest tests/test_native_without_kt.py -v
```

该测试在新进程中阻断 KT 及其可选传递依赖，实际导入服务、启动并停止模型运行时、启动 Native Bridge，
再执行主回复工具。它用于发现被 KT 传递依赖意外掩盖的核心依赖。

安装 `requirements-kt.lock` 后运行兼容套件：

```bash
python scripts/check_architecture.py
python -m pytest \
  tests/test_kt_framework.py \
  tests/test_kt_integration.py \
  tests/test_agent_runtime_gateway.py \
  -v
```

合并或发布前仍需运行项目完整测试：

```bash
python -m pytest tests/ -v
```

CI 的顺序必须保持为“安装核心锁 → 验证 Native 无 KT → 安装可选 KT 锁 → 运行架构与完整测试”。这样
才能证明 Native 的成功不是因为同一环境恰好已经安装了 KT。

## 5. 升级流程

升级 KT 时按以下顺序执行：

1. 只选择正式稳定 tag，核验 Python／Pydantic 约束、公开 Agent／Conversation／Registry／Plugin API、
   依赖增量和许可证；`main` 与 nightly 只用于试验。
2. 把 `requirements-kt.in` 中的 tag commit 更新为完整 40 位 SHA。
3. 运行 `bash scripts/compile-requirements.sh`，重新生成全部相关锁文件。
4. 确认 `requirements-prod.lock` 与 `requirements-test.lock` 不包含 `kohakuterrarium` 或仅由 KT 引入的
   Provider SDK；确认 `requirements-kt.lock` 仍固定到目标 commit。
5. 先在未安装 KT 的核心环境运行 Native 隔离测试，再安装可选锁并运行架构、KT 兼容、行为 Golden 和
   完整测试。
6. 如果上游公开 API 发生变化，只在 `nanobot_kt/` Adapter 和 `bootstrap/` 组合根处理，不把 KT 类型、
   私有字段或生命周期语义带回 `core/`、`app/`、`api/` 或工具核心。

升级失败时，把 `requirements-kt.in` 和锁文件恢复到最近一个已验证 commit，并回滚对应 Adapter 变更。
默认 Native 生产镜像不包含 KT，因此可选兼容升级不得影响 Native 的部署和回滚。

## 6. 许可证

KT `v2.0.0` 使用 `KohakuTerrarium License 1.0`，不是标准 Apache-2.0。部署、分发或对外提供包含 KT 的
兼容镜像前，必须单独复核其命名、可见归属和其他许可证义务。默认 Native 镜像不打包 KT，但这不能替代
对可选兼容环境的许可证审查。
