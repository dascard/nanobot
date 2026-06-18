---
name: Python 数据分析工具
version: 1
kind: tool
tool_name: python_sandbox
description: python_sandbox 工具的使用边界和安全约束。
---
## python_sandbox 工具边界

用于 SQL 难以直接表达的统计、清洗、聚合和规则计算。

- 简单查历史、查上一句、查表结构、普通 SELECT 优先使用 `sql_analysis`。
- 需要分位数、复杂分桶、去重、正则清洗、多步聚合时才使用本工具。
- 沙箱内只能使用预置只读数据库连接 `_conn` / `_db_path`，不要尝试文件系统、网络、系统命令或导入受限模块。
- SQL 查询必须控制范围和 `LIMIT`，不要把大表全量载入内存。
- 最后用 `print()` 输出结论、样本数量、关键统计和必要证据。
