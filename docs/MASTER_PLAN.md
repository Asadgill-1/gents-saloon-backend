# MASTER PLAN — UAE Multi-Tenant Barbershop Bot & POS System

> **Read order for any AI executing this plan:** this file → [DATA_MODEL.md](DATA_MODEL.md) → the phase file you are executing in [phases/](phases/). Reference [BOT_FLOWS.md](BOT_FLOWS.md), [AI_SPEC.md](AI_SPEC.md), [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) as the phase file directs. The original owner spec is [../Prompt.md.txt](../Prompt.md.txt) — it is the source of truth; if any doc contradicts it, the spec wins and the contradiction must be reported.

## 1. What is being built

A multi-tenant platform for gents barbershops in the UAE. Each shop gets:

- **4 Telegram bots** (Customer, Receptionist, Barber Crew, Shop Owner) — all interactions via inline buttons, never typed commands.
- **1 global Master bot** for the platform owner (onboarding shops, health, escalations, global analytics).
- **AI receptionist** (Moonshot AI, 4 languages: English, Arabic, Hindi, Urdu) on the Customer bot — intent extraction only, all facts fetched via Python tools, zero hallucination tolerance.
- **Dynamic queue** (daily tokens, live estimated waits) **plus future appointments** (time slots that convert into queue tokens on the day).
- **POS**: walk-in logging, multi-service checkout, cash/card (card requires slip number), tips (100% to barber).
- **Money engine**: hybrid commissions (fixed % or tiered threshold), advances (one-time or monthly deduction), append-only ledger, secret EOD/monthly reports per barber, owner summaries.
- **Phase 2**: touch-first tablet web app (Next.js) — live queue board, POS checkout, owner analytics, public TV queue display.
- **Phase 3**: platform-owner web dashboard — full business management.
- **Phase 4**: production deployment (single VPS, Docker Compose).

## 2. Locked decisions (do not re-litigate)

| # | Decision | Value |
|---|---|---|
| D1 | Backend | Python 3.12+, FastAPI, uvicorn |
| D2 | Bots | aiogram 3.x; **polling in dev** (`ENV=dev`), **webhooks in prod** (`ENV=prod`); FSM state in Redis (aiogram built-in `RedisStorage`) |
| D3 | DB/Auth | Supabase (PostgreSQL). RLS mandatory on all tenant tables. Backend uses service-role key; every service function takes explicit `shop_id` |
| D4 | Queue/locks/cache | Redis (token counters, booking locks, FSM, rate limits) |
| D5 | Background work | Celery 5.x workers + Celery Beat, Redis broker + result backend |
| D6 | AI | Moonshot AI via OpenAI-compatible API. Intent extraction + tool calling only. Code-level guardrail pre-filter runs BEFORE the AI |
| D7 | Frontend | **Two separate Next.js apps** (App Router) + Tailwind + shadcn/ui + Supabase JS (Auth + Realtime): shop dashboard (Phase 2) and platform-owner dashboard (Phase 3), each its own GitHub repo + Vercel deploy. English only. Dark theme default, light theme supported |
| D8 | Booking model | Live queue + future appointments (15-min slot granularity; appointments promoted into the live queue at T-30min) |
| D9 | Phone capture | Telegram native contact-share button on first booking; skippable; retention analytics fall back to `telegram_user_id` when absent |
| D10 | Deploy | Local dev now; final target = 1 VPS + Docker Compose (api, worker, beat, redis, caddy) + cloud Supabase |
| D11 | Money | Currency AED only. Timezone default `Asia/Dubai`. All DB timestamps UTC; convert at presentation/scheduling |
| D12 | Web auth | Supabase email/password for receptionists/owners/platform admin. No Telegram login on web |
| D13 | Repos & hosting | 3 GitHub repos (owner: Asadgill-1): `gents-saloon-backend` (this repo: backend + supabase + all docs — canonical), `saloon-shop-dashboard` (Phase 2 app), `saloon-gents-system-owner-dashboard` (Phase 3 app). Both dashboards auto-deploy on **Vercel from GitHub main**. Backend deploys per Phase 4 (VPS) |

## 3. Phase index and dependency order

Execute strictly in order. A phase starts only when the previous phase's Definition of Done is fully checked.

| Phase | File | Delivers | Depends on |
|---|---|---|---|
| 0 | [phases/PHASE_0_FOUNDATIONS.md](phases/PHASE_0_FOUNDATIONS.md) | Package layout, Supabase schema + RLS applied, config/secrets, Celery skeleton, seed script, health endpoint, pytest wiring | — |
| 1 | [phases/PHASE_1_TELEGRAM_CORE.md](phases/PHASE_1_TELEGRAM_CORE.md) | All 5 bots, queue + appointment engine, auto-confirm, POS + commissions + advances + ledger, Moonshot AI layer, EOD/monthly/health jobs, audit log | 0 |
| 2 | [phases/PHASE_2_TABLET_UI.md](phases/PHASE_2_TABLET_UI.md) | Tablet web app: queue board, POS modal, owner analytics, public TV display | 1 |
| 3 | [phases/PHASE_3_PLATFORM_DASHBOARD.md](phases/PHASE_3_PLATFORM_DASHBOARD.md) | Platform owner web console (onboarding, global analytics, escalations, blocks, health) | 2 |
| 4 | [phases/PHASE_4_DEPLOY.md](phases/PHASE_4_DEPLOY.md) | VPS deploy, webhooks, TLS, backups, monitoring, security audit, runbook | 1 (min) / 3 (full) |

## 4. Repository layout (target state after Phase 0)

```
backend/
  app/
    main.py                  # FastAPI app factory; mounts webhook + API routers; starts polling in dev
    core/
      config.py              # pydantic-settings Settings; single source of env vars
      supabase.py            # supabase-py client factory (service role)
      redis.py               # redis asyncio client factory
      celery_app.py          # Celery instance, queues, beat schedule
      security.py            # Fernet encrypt/decrypt for bot tokens; webhook secret check
      logging.py             # structured logging setup
    bots/
      registry.py            # loads bots table, builds aiogram Bot/Dispatcher per bot, routes updates
      master/                # routers + keyboards + FSM states for Master bot
      customer/
      receptionist/
      barber/
      owner/
      common/                # shared keyboards (yes/no, pagination), i18n message catalog (en/ar/hi/ur)
    services/                # ALL business logic lives here; bots and API are thin
      shop_service.py
      customer_service.py
      queue_service.py       # tokens, est waits, ordering, promotion
      booking_service.py     # create/confirm/auto-confirm/cancel/no-show/start/complete
      appointment_service.py # slots, conflicts, promotion to queue
      pos_service.py         # checkout, transactions, items
      commission_service.py  # pure functions: rule resolution + split calculation
      advance_service.py
      ledger_service.py
      report_service.py      # EOD + monthly aggregation
      escalation_service.py
      audit_service.py
      ai/
        client.py            # Moonshot OpenAI-compatible client wrapper
        tools.py             # tool schemas + dispatch to services
        prompts.py           # system prompt builder (per shop)
        guardrails.py        # pre-filter (links/media/keywords), rate limits
    api/
      telegram.py            # POST /tg/{bot_id}/{webhook_secret}
      public.py              # GET /api/public/queue/{shop_slug} (TV/phone fallback polling)
      health.py              # GET /health (db, redis, celery, bots summary)
    models/
      enums.py               # single home for every enum (mirrors DB enums)
      schemas.py             # pydantic DTOs
  workers/
    tasks_confirm.py         # auto_confirm_booking
    tasks_reports.py         # send_eod_reports, send_monthly_reports
    tasks_appointments.py    # promote_appointments, appointment_reminders
    tasks_health.py          # bot_health_check
  tests/
    test_commission.py       # table-driven money tests (mandatory)
    test_queue.py
    test_booking_flow.py
    test_advance_ledger.py
  requirements.txt
supabase/
  migrations/                # numbered SQL files, idempotent (IF NOT EXISTS / CREATE OR REPLACE)
docker/                      # Phase 4: compose.yml, Dockerfile.api, Dockerfile.worker, Caddyfile
docs/                        # this plan (canonical copy)
```

Frontends live in their **own repos** (D13), not in this one:

- `saloon-shop-dashboard` — Phase 2 Next.js app at repo root (`create-next-app` there): `/board`, `/analytics`, `/q/[slug]`. Vercel project #1.
- `saloon-gents-system-owner-dashboard` — Phase 3 Next.js app at repo root: platform admin console (routes at root, no `/admin` prefix — the whole app is admin). Vercel project #2.

Each dashboard repo carries copies of `DESIGN_SYSTEM.md`, its phase doc, and `ARCHITECTURE.md` with a canonical-source header pointing here — update the canonical docs first, then sync copies.

Note: the scaffold folder `backend/telegram_bot/` is superseded by `backend/app/bots/` — remove it in Phase 0. `backend/app/api|models|services|core` folders already exist as placeholders.

## 5. Environment variables (complete table)

| Var | Used by | Notes |
|---|---|---|
| `ENV` | all | `dev` (polling, verbose logs) / `prod` (webhooks) |
| `SUPABASE_URL` | backend, frontend | project URL |
| `SUPABASE_ANON_KEY` | frontend | public key (RLS applies) |
| `SUPABASE_SERVICE_ROLE_KEY` | backend only | never ships to frontend |
| `REDIS_URL` | backend, celery | `redis://localhost:6379/0` in dev |
| `CELERY_BROKER_URL` | celery | usually = REDIS_URL |
| `CELERY_RESULT_BACKEND` | celery | usually = REDIS_URL |
| `MOONSHOT_API_KEY` | backend | from platform.moonshot.ai |
| `MOONSHOT_BASE_URL` | backend | OpenAI-compatible endpoint. **VERIFY AT BUILD TIME** from official Moonshot docs |
| `MOONSHOT_MODEL` | backend | **VERIFY AT BUILD TIME**: pick current recommended model from Moonshot docs (kimi family); do not hardcode from this plan |
| `FERNET_KEY` | backend | generated once (`cryptography.fernet.Fernet.generate_key()`); encrypts bot tokens at rest |
| `MASTER_BOT_TOKEN` | backend | the one bot created manually before onboarding exists |
| `PLATFORM_ADMIN_TELEGRAM_IDS` | backend | comma-separated telegram user ids; seeds `platform_admins` |
| `PUBLIC_QUEUE_BASE_URL` | backend | shop-dashboard URL (`http://localhost:3000` dev; its Vercel URL in prod); composes live-queue links sent to customers |
| `WEBHOOK_BASE_URL` | backend (prod) | `https://<domain>`; Phase 4 only |

`.env.example` at repo root must stay in sync with this table (Phase 0 task).

## 6. Global conventions (every phase, every task)

1. **Thin bots, fat services.** aiogram handlers and API routes parse input, call one service function, render output. Business rules live only in `app/services/`. This is what makes Phase 2/3 reuse possible.
2. **Money is Decimal.** Never float. `numeric(10,2)` in DB, `decimal.Decimal` in Python, string serialization in JSON.
3. **Every state change is audited.** Any service function that mutates bookings, transactions, advances, ledger, staff, shops, or blocks writes an `audit_log` row in the same operation.
4. **Idempotency everywhere Celery touches.** Every task re-checks preconditions from DB before acting (e.g. auto-confirm checks status is still `requested`; report tasks check `eod_reports` unique row first).
5. **Trust boundaries validated.** Everything arriving from Telegram, the web frontend, or webhooks is validated with pydantic before any service call. Unknown callback data → answer with a safe "expired menu" message, never crash.
6. **i18n via message catalog.** All customer-facing bot strings live in `app/bots/common/i18n.py` keyed `(key, lang)`, langs `en/ar/hi/ur`. Staff bots (receptionist/barber/owner/master) are English-only. Web UI English-only.
7. **No invented facts.** Anything the AI tells a customer (price, wait, position) comes from a tool result. Anything a report states comes from `ledger_entries`/`transactions` queries.
8. **Verification is part of the task.** A task without its verify step green is not done. Money/queue/booking logic requires pytest; bot flows require the scripted manual checklist in the phase file.
9. **Deliberate shortcuts** are marked `# ponytail: <what's cut> — <upgrade path>` in code and collected in phase completion notes — collect via `/ponytail-debt` at phase end (see CLAUDE.md "Skills to use" section for all workflow skills).
10. **Package versions**: `requirements.txt` pins exact versions chosen at build time (**VERIFY AT BUILD TIME**: latest stable of each; this plan pins only major lines listed in D1–D7). Every NEW dependency passes the slopsquatting check in [SECURITY.md](SECURITY.md) S8.1 first.
11. **Security rules bind.** [SECURITY.md](SECURITY.md) S1–S9 apply to every task in every phase; phase completion notes include the security VERIFY results (S9.2).

## 7. Definition of Done per phase

- **Phase 0**: fresh clone + `.env` → `pip install -r requirements.txt` → migrations apply cleanly twice (idempotent) → `pytest` green → `uvicorn app.main:app` serves `/health` OK → `celery -A app.core.celery_app inspect ping` OK → seed script creates demo shop visible in Supabase.
- **Phase 1**: full happy path on a real Telegram account set: onboard demo shop via Master bot → customer books via buttons AND via AI text → receptionist confirms (and separately: lets auto-confirm fire) → barber sees queue, presses 5-min reminder → receptionist starts service, checks out with card+slip → split flash correct → advance given and reflected → EOD report arrives to barber + owner → escalation fires on a link message and reaches Master bot with working [Block User]. All pytest suites green.
- **Phase 2**: on a real tablet (or 768×1024 emulation): login as receptionist → live board reflects bot-created bookings in <2s via Realtime → checkout via modal writes identical transaction/ledger rows as the bot flow → owner dashboard numbers reconcile with EOD report exactly → TV route on a second screen updates live with zero financial data. Playwright smoke suite green.
- **Phase 3**: platform admin logs in → onboards a second shop entirely from the web (no Master bot) → sees global analytics across both shops → resolves an escalation → blocks/unblocks a user → sees bot health board. RLS proven: shop-1 owner JWT cannot read shop-2 rows (automated test).
- **Phase 4**: `docker compose up -d` on the VPS brings up api/worker/beat/redis/caddy → bots switched to webhooks and answer in <1s → TLS valid → backup/restore drill documented and performed once → security audit findings resolved or accepted in writing → runbook covers restart, token rotation, new-shop SOP, incident basics.

## 8. What is explicitly OUT of scope (do not build unless owner asks)

- Online payments / payment gateways (POS logs cash and card-slip only).
- Customer accounts on the web (customers exist only in Telegram + public read-only queue page).
- SMS/WhatsApp channels. Multi-currency. Multi-country/timezone UI (fields exist, UI assumes UAE).
- Inventory, product sales, loyalty points, marketing broadcasts.
- Arabic/RTL web UI (bots handle Arabic; web is English per D2 decision — revisit only on owner request).
