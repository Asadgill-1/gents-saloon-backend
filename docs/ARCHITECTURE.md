# Architecture

Full detail lives in [MASTER_PLAN.md](MASTER_PLAN.md) + [DATA_MODEL.md](DATA_MODEL.md). This is the one-screen picture.

```
                         ┌────────────────────────────┐
   Telegram (5 bots/shop │  FastAPI (backend/app)     │
   incl. global Master)  │                            │
  ───── webhooks/polling►│  api/telegram ── bots/*    │
                         │        │   (aiogram 3,     │
   Customer free text    │        ▼    buttons-only)  │
   ── Moonshot AI ◄──────│  services/ai (tools only)  │
      (intent only,      │        │                   │
       facts via tools)  │        ▼                   │
                         │  app/services/*  ◄─────────┼── api/staff_web + platform_web
                         │  (ALL business logic:      │        ▲ JWT (Supabase Auth)
                         │   queue, booking, POS,     │        │
                         │   commission, ledger,      │   2 Next.js apps (own repos, Vercel):
                         │   advances, reports,       │   shop-dashboard: /board /analytics
                         │   escalations, audit)      │     /q/[slug] (public TV, anon RPC)
                         └──────┬──────────┬──────────┘   owner-dashboard: platform console
                                │          │                              (Ph3)
                     Supabase (Postgres)  Redis
                     • 17 tables, RLS     • FSM states
                     • ledger append-only • daily token INCR
                     • Auth (web JWT)     • booking/confirm locks
                     • Realtime:          • rate limits
                       - Postgres Changes (staff UI)
                       - Broadcast queue:{slug} (public)

                     Celery worker + Beat (Redis broker)
                     • auto_confirm_booking (5-min countdown)
                     • promote_appointments / reminders (5-min beat)
                     • send_eod_reports (per-shop local time, idempotent latch)
                     • send_monthly_reports (1st, applies monthly advance deductions)
                     • bot_health_check (5-min, alerts Master bot)
```

Key invariants:

- **Thin adapters, one core.** Telegram handlers, web API routes, and (later) dashboard endpoints all call the same `app/services/*` functions. No business rule exists twice.
- **Tenant isolation twice over.** RLS for web clients (JWT `app_metadata.shop_id`); explicit `shop_id` parameters in every service call for the service-role backend.
- **Money = append-only `ledger_entries`.** Reports, bot summaries, and dashboards aggregate the same rows; corrections are new adjustment rows, never edits.
- **Redis is disposable.** Every Redis fact is reconstructible from Postgres; flushing Redis loses no bookings or money.
- **AI is optional at runtime.** Guardrail pre-filter runs before the model; every booking action has a button path; AI down → buttons-only degradation.
- **Idempotent background jobs.** DB unique latches (`eod_reports`) + status re-checks make every Celery task safe to re-run.

Deploy (Phase 4): single VPS — Docker Compose: api, celery worker, celery beat, redis, caddy (TLS + reverse proxy). Supabase cloud. Both dashboards on Vercel from their GitHub repos (D13). Dev mode: polling bots, local uvicorn/celery/redis, no domain needed.

Repos: `gents-saloon-backend` (this repo — backend + supabase + canonical docs) · `saloon-shop-dashboard` (Phase 2) · `saloon-gents-system-owner-dashboard` (Phase 3). GitHub owner: Asadgill-1.
