# Nanobot Project Overview

Nanobot is an AI chatbot backend powered by the KohakuTerrarium agent framework.

## Architecture

- **HTTP API**: FastAPI in `api/routes.py` — receives chat requests, returns responses
- **KT Bridge**: `nanobot_kt/bridge.py` — wraps the KT Agent for request/response usage
- **Creature Config**: `creatures/nanobot/config.yaml` — defines tools, sub-agents, controller
- **Controller**: OpenAI-compatible LLM via new-api gateway (`clients/new_api_client.py`)
- **Model Registry**: `clients/model_registry.py` — auto-syncs available models, applies overrides

## Key Design Decisions

1. **Ephemeral conversations** (`ephemeral: true`): KT conversation is cleared each request. Session context is re-injected from the database (ChatLog table) as enriched query text.
2. **Persona as system message**: User persona is injected at the system level in KT's conversation, giving it authoritative weight that persists across ephemeral clears.
3. **Model routing**: Dynamic tier-based routing (reasoning/smart/fast) with fallback on failure. Budget caps enforced via `LLM_BUDGET_CAP`.
4. **Dual DB**: SQLite for persistence (chat logs, personas, system prompts) + sandbox `_conn` for read-only data analysis.

## Tools Available

- `sql_analysis` — Execute SQL queries against the nanobot database
- `python_sandbox` — Execute Python analysis scripts in a security-restricted sandbox
- `news_search` — Search for recent news articles

## Sub-Agents Available

- `memory_read` — Search and retrieve from the memory folder
- `memory_write` — Store information to memory (can create files)
