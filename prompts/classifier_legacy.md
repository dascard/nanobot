---
name: 旧分类器兼容
version: 1
description: 旧二分类回复判定模板。
required_vars:
  - message
optional_vars:
  - system_prompt
---
{{ system_prompt }}

待判定消息:
{{ message }}
