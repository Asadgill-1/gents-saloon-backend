# PHASE 0 — FOUNDATIONS

Goal: a runnable empty skeleton — config, DB schema + RLS live in Supabase, Redis + Celery wired, tests running. No product features. Prereqs: Python 3.12+, a Supabase project (owner supplies URL + keys), Redis reachable locally (installer or Docker or Memurai on Windows — dev machine is Windows 10).

Execute tasks in order. Every task ends with its **Verify** green before moving on.

## T0.1 — Backend package layout + dependencies

Files: `backend/requirements.txt`, `backend/app/__init__.py` and every package `__init__.py`, `backend/app/main.py` (FastAPI factory + `/health` stub), delete `backend/telegram_bot/` (superseded by `app/bots/`, see MASTER_PLAN §4), delete `.gitkeep` files in folders that gain real files.

`requirements.txt` — pin exact versions at build time (**VERIFY AT BUILD TIME**: latest stable of each): `fastapi`, `uvicorn[standard]`, `aiogram` (3.x line), `celery[redis]` (5.x), `redis`, `supabase` (2.x), `openai`, `pydantic-settings`, `cryptography`, `httpx`, `pytest`, `pytest-asyncio`.

Verify: `pip install -r requirements.txt` clean; `python -c "import app"` (from `backend/`) no error.

## T0.2 — Settings and clients (`app/core/`)

- `config.py`: `Settings(BaseSettings)` — every var from MASTER_PLAN §5, `.env` loaded, missing required var fails fast at startup with a clear name.
- `supabase.py`: `get_supabase()` returning a service-role client (singleton).
- `redis.py`: `get_redis()` asyncio client (singleton).
- `security.py`: `encrypt_token(str)->str` / `decrypt_token(str)->str` (Fernet with `FERNET_KEY`); `constant_time_eq` for webhook secrets (`hmac.compare_digest`).
- `logging.py`: stdlib logging, JSON lines in prod, human format in dev, level from ENV.

Verify: `pytest tests/test_core.py` — settings load from a temp `.env`; encrypt→decrypt round-trip; missing `FERNET_KEY` raises at import of settings.

## T0.3 — Migrations (all of DATA_MODEL.md)

Files: `supabase/migrations/0001_enums.sql` … `0006_seed_static.sql` exactly as specified in [DATA_MODEL.md](../DATA_MODEL.md) (enums, 17 tables, indexes, RLS enable + policies, `get_public_queue`, append-only triggers for `ledger_entries`/`audit_log`).

Apply via Supabase MCP `apply_migration` if available, else the SQL editor / `supabase db push`. Idempotency is mandatory — applying the full set twice must produce zero errors.

Verify:
1. Apply twice, second run clean.
2. SQL probe: `SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'` ≥ 17; `SELECT * FROM pg_policies` shows policies on every table.
3. `UPDATE ledger_entries …` on a dummy row raises the append-only exception.
4. As `anon` key: every table SELECT denied; `SELECT * FROM get_public_queue('nope')` returns 0 rows without error.

## T0.4 — Celery skeleton

- `app/core/celery_app.py`: Celery instance (`broker/backend` from settings), `task_acks_late=True`, `worker_prefetch_multiplier=1`, timezone UTC, autodiscover `workers/`. Empty `beat_schedule` dict (filled in 1H) + one `ping` task in `workers/tasks_health.py`.

Verify (3 terminals or background): `celery -A app.core.celery_app worker -l info --pool=solo` (Windows dev needs `--pool=solo`), `celery -A app.core.celery_app inspect ping` → pong; `ping.delay().get(timeout=10)` returns.

## T0.5 — FastAPI app + health

- `app/main.py`: app factory; routers `api/health.py` (real checks: Supabase trivial select, Redis PING, Celery ping with 2s timeout — each reported `ok|fail`), `api/telegram.py` and `api/public.py` as empty routers (Phase 1 fills them).
- Dev entry: `uvicorn app.main:app --reload` from `backend/`.

Verify: `GET /health` → 200 `{"db":"ok","redis":"ok","celery":"ok"}` with everything up; kill Redis → `"redis":"fail"` and HTTP 503.

## T0.6 — Enums + DTO mirrors

`app/models/enums.py`: Python `StrEnum` for every DB enum in DATA_MODEL §1 (single source for code; names identical to SQL values). `app/models/schemas.py`: pydantic DTOs for Booking, Transaction, Service, Customer, Staff, Shop (fields matching tables; Decimal for money).

Verify: `pytest tests/test_models.py` — enum values match a hardcoded expected list (guards accidental drift from SQL).

## T0.7 — Seed script

`backend/scripts/seed_demo.py`: creates (idempotent by slug) demo shop "Demo Gents" (`demo-gents`), 1 owner + 1 receptionist + 2 barbers (telegram ids from CLI args or env), 5 services (Haircut 50/30min, Beard 30/20, Haircut+Beard 70/45, Kids 35/20, Shave 25/15), shop-default commission rule fixed 50%, and prints created ids. Uses the same `shop_service` functions the Master bot will use later where they exist; direct inserts otherwise (replaced in 1A).

Verify: run twice → second run reports "exists, skipping"; rows visible in Supabase; `SELECT get_public_queue('demo-gents')` runs (0 rows, no error).

## T0.8 — Repo hygiene

Update root `.env.example` to the full MASTER_PLAN §5 table. Update `README.md` quickstart (install, .env, migrations, run api/worker, seed). Add `backend/pytest.ini` (asyncio mode auto, testpaths).

Verify: fresh-clone dry-run per MASTER_PLAN §7 Phase 0 Definition of Done — all steps pass on this machine.

## Ponytail ledger (allowed shortcuts this phase)

- Single Redis DB, no cluster — upgrade path: env-split DBs if contention appears.
- No Alembic-style migration tool — plain numbered SQL is the system; revisit only if a second environment demands rollbacks.
- Windows dev uses `--pool=solo` Celery — prod (Linux) uses default prefork; documented in PHASE_4.
