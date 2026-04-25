---
name: python_sandbox
description: 在安全限制的沙箱中执行 Python 数据分析脚本
allowed-tools: [python_sandbox]
---

# Python 分析沙箱

在独立的受限环境中执行 Python 脚本，以灵活的形式处理统计数据、日志分析或更复杂的运算。

## 何时使用
- SQL 查询不够灵活时（如复杂的文本处理任务、统计回归等）
- 需要处理多张表间复杂的业务关联计算
- 对数据进行 json 解析后多级字典组合分析

## 环境限制
这是一个严格受限的环境，保障了系统的安全性：
1. **网络拦截**: 禁止使用 socket、requests 等进行网络访问
2. **文件系统隔离**: 已禁止 os、subprocess、open 等敏感操作
3. **内置可用**: 工具已引入 sqlite3、json、math、statistics、collections、datetime 等常用分析库
4. **数据库**: 在内存中挂载了 nanobot 数据库实例即可直接访问 (如 `import sqlite3; conn = sqlite3.connect(...)`)

代码最后应通过 `print()` 或赋值到变量供最后结果输出。
