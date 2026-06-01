---
name: 长期记忆摘要输入提示词 V2
version: 1
kind: task
tool_name: memory_digest
description: memory_digests 长期摘要 LLM 的 user prompt。
---
Generate a three-level memory digest for the following digest_source.

Metadata:

date: {{ date }}
session_id: {{ session_id }}
source_id: {{ source_id }}
source_type: {{ source_type }}
source_range: {{ source_range }}
message_count: {{ message_count }}

Generation requirements:

- Generate exactly one "long_summary" for level 0.
- Generate exactly one "preview" for level 1.
- Generate multiple "recall_cards" for level 2.
- The recall cards should be atomic, concrete, independently understandable, and useful for future RAG retrieval.
- Do not produce one recall card per message.
- Do not include temporary noise, raw tool outputs, stack traces, or irrelevant URLs.
- Do not invent facts.
- Use fewer recall cards if the source has little durable information.
- Prefer preserving concrete identifiers such as project names, table names, module names, function names, and configuration names.

Cleaned digest_source:

{{ digest_source }}

Optional existing hints or previous digest context:

{{ existing_digest_hint }}

Now output strict JSON only.
