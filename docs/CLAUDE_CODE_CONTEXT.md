# Instructions for the Claude Code agent

_Put this at the root of the project or include its content in your Claude Code system prompt._

## Project overview

Lera Logistics is an AI-powered freight brokerage MVP for the Ukrainian market. The single human operator (Lera) manages the platform via a web dashboard. AI agents propose matches, draft messages, and handle documents — but every outbound action requires human approval until a feature flag is explicitly turned on.

See `docs/PROJECT_GOAL.md` for the full business context.

## Current state

See `docs/PROJECT_STATE.md` — it lists exactly what's implemented and what's not.

TL;DR:
- ✅ Full skeleton: FastAPI + async SQLAlchemy 2 + Postgres + Alembic + Jinja2/HTMX
- ✅ Lardi-Trans adapter with normalization layer
- ✅ AI Matcher agent (safe — only writes to DB)
- ✅ Dashboard with live-updated counters
- ✅ 7 passing unit tests
- ❌ No scheduler, no pricing/negotiator/risk/document agents yet
- ❌ No email/Telegram/Viber integrations
- ❌ Alembic first migration not yet generated (waiting for first local run)

## Golden rules when modifying this codebase

### 1. Never import exchange-specific code from services or agents

```python
# ❌ WRONG
from app.adapters.lardi import LardiAdapter

class SomeService:
    def foo(self):
        adapter = LardiAdapter()  # hardcoded to Lardi

# ✅ RIGHT
from app.adapters.base import ExchangeAdapter

class SomeService:
    def __init__(self, adapter: ExchangeAdapter):
        self._adapter = adapter
```

### 2. Feature flags for all "dangerous" agents

Dangerous = agent does something externally visible (sends message, books deal, makes payment).

- Check `settings.agent_X_enabled` at the start of the agent's method
- Default to `False` in `.env.example`
- Document the risk in the flag's comment

### 3. Always save raw payloads

When receiving data from an external system, save the full JSON in the
`raw_payload` JSONB column. This has saved countless hours when APIs
change their shape.

### 4. Tests for non-LLM logic only

Don't try to test the LLM output — it's non-deterministic. Test:
- Pre-filters (they're pure Python)
- Normalization (they're pure functions)
- HTTP adapters (mock httpx responses)
- Services (use in-memory SQLite for tests)

### 5. Never commit `.env`

If you see `.env` in `git status`, stop and fix `.gitignore` before
continuing.

### 6. Migrations for all schema changes

When you add a column or table:

```bash
alembic revision --autogenerate -m "add foo column"
# review the generated file in migrations/versions/
alembic upgrade head
```

Never edit tables manually in psql on production.

## How to verify something works

After any significant change, run:

```bash
ruff check app/
ruff format app/ --check
pytest -v
python -c "from app.main import app; print('OK')"
```

All four should succeed.

## When in doubt

- Re-read `docs/ARCHITECTURE.md` — it explains the layered design.
- Check `docs/ROADMAP.md` — if the feature you're about to build belongs
  to a later phase, ask the user before starting.
- If you're about to add a dependency, add it to `pyproject.toml` and
  document why in the commit message.

## Style preferences

- Line length 100
- Type hints everywhere (`ruff` will catch missing ones)
- Short docstrings on every public function/class
- Ukrainian/Russian in user-facing strings (dashboard, messages to carriers)
- English in code, comments, log messages, commit messages

## Specific next steps to consider

In priority order, if asked "what should we do next":

1. Generate the first Alembic migration (requires local Postgres running).
2. Activate the Lardi API token and run first real `ingest_loads`.
3. Inspect a real Lardi response and fix any field-mapping issues in
   `app/adapters/lardi.py` (see `docs/LARDI_API_NOTES.md`).
4. Add `app/routes/vehicle_detail.py` (symmetric to `load_detail.html`).
5. Add APScheduler to start autopolling Lardi every 60 seconds.
6. Add match_feedback table + UI to collect human ratings.

Anything beyond that belongs to Phase 2+ of the roadmap — discuss with
the user first.
