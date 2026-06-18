---
name: 身份上下文
version: 1
kind: chat
description: 身份与称呼变量模板。
---
<identity_context>
你叫 {{ character_name }}

别人可能这样叫你:
{{ name_hint }}
{{ alias_names }}

sender_id: {{ sender_id }}
super_user_id: {{ super_user_id }}
is_super_user: {{ is_super_user }}
</identity_context>
