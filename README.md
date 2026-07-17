# Gents Saloon — UAE Multi-Tenant Barbershop Bot & POS Platform

Multi-tenant platform for gents barbershops: 4 Telegram bots per shop + global Master bot, AI receptionist (Moonshot, EN/AR/HI/UR), dynamic queue + appointments, POS with commissions/advances/ledger, tablet web UI, platform-owner dashboard.

**Status: fully planned, not yet built.** The plan is the product of this repo right now — any AI (or human) can build the system from the docs alone.

## For any AI opening this repo

Read in this order, then start executing:

1. [CLAUDE.md](CLAUDE.md) — coding rules for this repo (mandatory)
2. [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) — history + decision log
3. [docs/MASTER_PLAN.md](docs/MASTER_PLAN.md) — system overview, locked decisions, phase index, conventions, definitions of done
4. The phase you're executing: [docs/phases/](docs/phases/) — **next up: [PHASE_0_FOUNDATIONS.md](docs/phases/PHASE_0_FOUNDATIONS.md)**

Reference specs (linked from phase docs as needed): [DATA_MODEL.md](docs/DATA_MODEL.md) · [BOT_FLOWS.md](docs/BOT_FLOWS.md) · [AI_SPEC.md](docs/AI_SPEC.md) · [DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) · [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [REQUIREMENTS.md](docs/REQUIREMENTS.md) (feature→phase traceability). Original owner spec: [Prompt.md.txt](Prompt.md.txt) — source of truth on conflicts.

## Stack

| Layer | Tech |
|---|---|
| Backend API + bots | Python 3.12+, FastAPI, aiogram 3 (buttons-only UX) |
| Database / Auth / Realtime | Supabase (PostgreSQL, RLS mandatory) |
| Queue/locks/cache | Redis |
| Background & scheduled | Celery 5 + Celery Beat |
| AI receptionist | Moonshot AI (OpenAI-compatible, tool-calling only) |
| Frontend (Phase 2/3) | Next.js App Router, Tailwind, shadcn/ui, Supabase Realtime |
| Deploy (Phase 4) | 1 VPS: Docker Compose (api, worker, beat, redis, caddy) |

## Repos (GitHub: Asadgill-1)

| Repo | Holds | Deploy |
|---|---|---|
| [gents-saloon-backend](https://github.com/Asadgill-1/gents-saloon-backend) | **this repo** — backend, supabase migrations, canonical docs | VPS Docker Compose (Phase 4) |
| [saloon-shop-dashboard](https://github.com/Asadgill-1/saloon-shop-dashboard) | Phase 2 Next.js app: /board, /analytics, /q/[slug] | Vercel from `main` |
| [saloon-gents-system-owner-dashboard](https://github.com/Asadgill-1/saloon-gents-system-owner-dashboard) | Phase 3 Next.js app: platform admin console | Vercel from `main` |

Docs here are canonical; dashboard repos carry synced copies of DESIGN_SYSTEM + their phase doc + ARCHITECTURE.

## Folder map (this repo)

```
docs/                 the plan (see reading order above)
  phases/             PHASE_0 … PHASE_4 execution docs
backend/              Python app (Phase 0 fills this)
supabase/migrations/  SQL migrations (Phase 0, from DATA_MODEL.md)
Prompt.md.txt         original owner spec — do not edit
```

## Quickstart (after Phase 0 is built)

Documented in PHASE_0_FOUNDATIONS.md T0.8 — install, `.env` from `.env.example`, apply migrations, `uvicorn app.main:app`, `celery -A app.core.celery_app worker`, seed script. Until then there is nothing to run.
