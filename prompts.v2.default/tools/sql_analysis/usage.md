---
name: SQL 分析
version: 1
kind: tool
tool_name: sql_analysis
description: SQL 分析模板占位。
---
## sql_analysis 工具边界

只做只读 SQL 查询，用于从 nanobot 本地数据库里找可验证证据。

- 只允许 `SELECT` / `WITH` / SQLite 只读 PRAGMA。
- 查询必须加合理 `LIMIT`，除非是聚合统计。
- 不要执行写入、删除、更新、建表、附件读取或网络请求。
- 用户要群日报、群聊总结、活跃统计时，优先使用 `group_analysis`，不要先绕到 SQL 查群号。
- 简单历史查证优先直接返回少量证据；复杂统计需要二次清洗时再交给 `python_sandbox`。
- 输出应包含结论和关键证据，不要把完整大表贴回回复。
