# Key Files

Critical file paths in the nanobot-server project.

## Core Application

| File | Role |
|------|------|
| `api/routes.py` | All HTTP endpoints — chat proxy, evolution, data API |
| `config.py` | Centralized environment config |
| `nanobot_kt/bridge.py` | KT Agent lifecycle + persona injection |
| `server.py` | FastAPI app factory + lifespan |
| `clients/new_api_client.py` | New-API gateway client + model routing |
| `clients/model_registry.py` | Model registry sync + override system |

## Creature Config

| File | Role |
|------|------|
| `creatures/nanobot/config.yaml` | Agent config — tools, sub-agents, controller |
| `prompts.v2.default/` | Canonical Prompt Runtime default templates |
| `data/prompts_v2/` | Runtime prompt templates copied from defaults and editable in deployments |

## Database

| File | Role |
|------|------|
| `core/database.py` | SQLAlchemy models — User, Persona, ChatLog, SystemPrompt |
| `core/evolution.py` | Evolution pipeline — persona extraction, prompt auditing |
| `core/legacy_adapter.py` | PersonaArchitectAgent, PromptAuditorAgent |

## Data

| File | Role |
|------|------|
| `data/nanobot.db` | Primary SQLite database |
| `clients/data/model_overrides.json` | Model tier/tag/cost overrides |
