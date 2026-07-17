# PHASE 3 — PLATFORM OWNER DASHBOARD

Goal (owner spec Phase 3): "Customer onboarding and everything must be on platform owner dashboard — platform owner manages full business from here." A **separate Next.js app in its own repo: `https://github.com/Asadgill-1/saloon-gents-system-owner-dashboard` (D13)**, auto-deployed by Vercel from `main`. Routes live at the app root (no `/admin` prefix — the entire app is the admin surface), desktop-first, `app_role=platform_admin` only. Same design tokens (DESIGN_SYSTEM §7), sidebar layout, tables-over-cards density.

Scaffold mirrors T2.1 (create-next-app at repo root, same fonts/tokens/env keys). Shared components (`TokenNumeral`, `ChatTranscript`, `StatTile`, theme setup) are **copied** from the shop dashboard, not imported — `# ponytail: copied components across dashboard repos — extract a shared package only if a third consumer appears`.

Backend: extend `api/platform_web.py` with admin endpoints that reuse Phase-1 services (`shop_service.onboard`, escalation/blocked/report services). JWT check: `app_role == 'platform_admin'` — no shop_id scoping (global view is the point), every endpoint still audits.

## T3.1 — Admin shell + access

App layout: sidebar (Shops · Onboard shop · Global analytics · Escalations · Blocked users · Bot health · Audit), gold brand accent, breadcrumbs. platform_admin created via `create_web_user.py … --platform-admin` (no shop). Non-admin JWT → 404 (not 403 — don't advertise the surface); login page is the only unauthenticated route.

Verify: role gating automated test (shop_owner JWT on /admin API → 404); RLS proof test from MASTER_PLAN §7: shop-1 owner JWT selecting shop-2 rows via supabase-js → zero rows (this is the phase's mandatory security test).

## T3.2 — Shop onboarding UI (web twin of Master-bot wizard)

Multi-step form = BOT_FLOWS §6.2 steps 1–11 as web steps with the same validation (slug regex+unique, live token `getMe` check via backend proxy endpoint, ≥1 service, ≥1 barber, commission rule builder with live preview: "bill 120 → barber 25 / shop 95"). Draft autosave (localStorage), review screen, create → `shop_service.onboard` (identical transaction), success screen with per-bot status + [Send test messages again].

Verify: Playwright full onboarding of "Second Shop" with 4 fresh dev tokens → its 4 bots answer /start; bad token at step 6 surfaces inline error and blocks progress; abandoning at step 9 and returning restores the draft.

## T3.3 — Shops management

Shops table (name, slug, active, today's bookings/revenue, bot health dots) → shop detail: stats, staff list (add/deactivate staff, create web users for the shop — replaces the CLI script from T2.2), services editor, commission rules editor (new rule = new row with effective_from, never edit history), [Suspend]/[Activate] (typed-slug confirm), token replacement.

Verify: suspend from web → customer bot answers "temporarily closed" (manual); staff deactivation blocks their bot access (automated service test); commission edit creates a new row and old transactions keep old splits (pytest already guards this — extend with an API-path case).

## T3.4 — Global analytics

Cross-shop: platform totals (today/month revenue, bookings, active shops), per-shop comparison table, escalations trend, AI usage counters (from rate-limit/audit data), top barbers platform-wide. Reuses `report_service.aggregate` per shop + a thin cross-shop summing layer (`report_service.platform_rollup` — the ONE new aggregation function this phase).

Verify: rollup pytest: sum of per-shop reports == platform rollup for a seeded 2-shop dataset, to the fils.

## T3.5 — Escalations console + blocked users

Escalations table (filter status/shop), detail drawer: full chat transcript (reuse ChatTranscript component), customer history, actions [Block user] [Monitor] [Resolve] — same service calls as Master bot buttons; both surfaces stay in sync (status change reflected in bot card edits where feasible, else next render). Blocked users page: global list, unblock, per-shop block toggle on the customer record.

Verify: escalate from a real customer message → appears in console < 5 s (Realtime on escalations) → Block from web → customer silenced in Telegram (manual); Master-bot card and console show the same status afterwards.

## T3.6 — Bot health + audit

Health board: per shop × 4 bots grid (healthy, last_health_at, webhook state in prod), manual [Run check now] (triggers the Celery task), incident log (unhealthy transitions from audit). Audit explorer: filterable table (shop, actor, action, date), payload JSON viewer, export CSV.

Verify: revoke a dev token → board shows red within 5 min and Master bot alerted (existing 1H behavior observed from the new UI); audit filter returns the onboarding trail from T3.2.

## Phase 3 Definition of Done

MASTER_PLAN §7 Phase-3 script: platform admin onboards a second shop fully from the web, both shops isolated (RLS test green), escalation round-trip web↔telegram works, health board live. Playwright admin suite green. PROJECT_CONTEXT updated. The Master bot remains fully functional — the dashboard is a superset, not a replacement (owner works from phone when away).
