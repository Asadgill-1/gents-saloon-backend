# PHASE 2 — TABLET UI (Next.js web app)

Goal: the four owner-spec modules (Live Queue Board, POS Checkout Modal, Owner Analytics, Public TV Display) as a touch-first web app. Everything visual follows [DESIGN_SYSTEM.md](../DESIGN_SYSTEM.md) — tokens are locked; do not restyle ad hoc. If `ui-ux-pro-max` / `frontend-design` / `design` skills are available to the executing AI, invoke them at the start of this phase for refinement within those tokens.

Architecture rule: **reads** go straight to Supabase (RLS-scoped JWT + Realtime); **mutations** go to the FastAPI backend (`/api/*`), which reuses the exact Phase-1 services — the tablet and the bots are two faces of one logic. No business rule may be re-implemented in TypeScript.

## T2.1 — App scaffold

**This app lives in its own repo: `https://github.com/Asadgill-1/saloon-shop-dashboard` (D13), Next.js at repo root, auto-deployed by Vercel from `main`.** Clone it, then `npx create-next-app@latest .` (TypeScript, App Router, Tailwind; **VERIFY AT BUILD TIME**: current stable Next.js major + supabase-js + shadcn/ui init command). Install shadcn/ui, Lucide, Recharts. `next/font/google`: Fira Sans, Fira Code, Cormorant. Set up `globals.css` tokens (DESIGN_SYSTEM §2), dark default + `ThemeToggle`. Env (local `.env.local` + same keys in Vercel project settings): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_BASE_URL`. Keep the repo's `docs/` copies in sync with the backend repo (canonical).

Vercel note: in dev the backend runs on the local machine — `NEXT_PUBLIC_API_BASE_URL` on the Vercel deployment stays unset/dummy until Phase 4 gives the backend a public URL; until then mutations only work when running the dashboard locally against the local backend. Reads + TV display work on Vercel immediately (Supabase is cloud).

Verify: `npm run build` clean; `/` renders a themed shell on 768×1024; push to `main` → Vercel preview/production deploy goes green.

## T2.2 — Auth + backend bridge

1. Supabase Auth email/password. Login page (brand moment: Cormorant wordmark, gold on dark). Session via `@supabase/ssr` middleware; `/board` `/analytics` gated; role from JWT `app_metadata.app_role` routes receptionist→/board, shop_owner→/analytics (each can visit the other per owner discretion: receptionists cannot see /analytics — enforce).
2. Backend: `api/staff_web.py` — small authenticated surface used by the UI for mutations: `POST /api/web/bookings/{id}/confirm|start|no_show|cancel`, `POST /api/web/walkins`, `POST /api/web/checkout`, `POST /api/web/advances`. Auth: `Authorization: Bearer <supabase JWT>` verified server-side (Supabase JWKS / `auth.get_user`), shop_id + role read from app_metadata, then straight into Phase-1 services.
3. User provisioning: Phase-3 dashboard is the real admin path; for now a CLI script `backend/scripts/create_web_user.py <email> <shop_slug> <role>` (Supabase Admin API, sets app_metadata).

Verify: pytest for JWT verification (expired/wrong-audience/missing metadata rejected); manual: receptionist login lands on /board; owner on /analytics; direct URL cross-access blocked; a receptionist JWT hitting `POST /api/web/checkout` for another shop_id → 403 (automated test).

## T2.3 — Live Queue Board (`/board`)

Build DESIGN_SYSTEM §6.1 exactly: 3 columns, QueueCard actions [Start] [🔔 5-min] [No show] / [Checkout], walk-in button (wizard sheet mirroring bot flow 3.4), appointment badge cards, warn-state styling, empty states, offline banner.
Realtime: Postgres Changes subscription on `bookings` + `transactions` (filter `shop_id=eq.{id}`) → column reconciliation; full refetch on reconnect; optimistic move + rollback on API error (toast with cause).
The 🔔 button calls the same reminder service the barber bot uses (add `POST /api/web/bookings/{id}/remind`) — idempotent, shows "Sent ✓".

Verify: Playwright — seeded shop: card renders in correct column per status; Start moves card; No-show requires confirm dialog. Manual with Phase-1 running: booking created from a real Telegram customer appears on the board < 2 s; actions on the board push correct Telegram messages (matrix BOT_FLOWS §7).

## T2.4 — POS Checkout Modal

DESIGN_SYSTEM §6.2: 4 steps, multi-service select, tip quick-picks, cash/card (slip required client- AND server-side), split flash from the API response (never computed in TS), success animation to Paid column. Cancel-confirm after step 1.

Verify: Playwright — full cash and card flows; card without slip blocked at step 3 with field error; split numbers on screen equal `commission_service` pytest fixtures for identical inputs (assert via API response snapshot). Manual: a checkout done on the tablet produces identical DB rows (transactions/items/ledger) as the same checkout via receptionist bot — diff the rows.

## T2.5 — Owner Analytics (`/analytics`)

DESIGN_SYSTEM §6.3: stat tiles, barber performance table, retention donut+trend (Recharts), advances panel (give/deduct via API), date-range picker. All aggregates come from `GET /api/web/reports?range=…` (report_service) — the UI never sums raw rows for the headline numbers (reconciliation guarantee).

Verify: Playwright renders with seeded data; equality test: API report for "today" == numbers in the owner-bot [Today] message == EOD payload for that date (single pytest hitting all three paths). Retention: seeded returning customer counted correctly with phone and with telegram-only identity.

## T2.6 — Public TV Display (`/q/[slug]`)

DESIGN_SYSTEM §6.4 — the signature screen. Anon: RPC `get_public_queue` + Broadcast `queue:{slug}` (backend publishes on every queue mutation — add the publish call into `queue_service.recompute`), 15 s poll fallback, `?t=` highlight for customer phones, burn-in drift, cursor hidden, reduced-motion pause.

Verify: Playwright anon (no session cookie) renders tokens; grep rendered HTML for any digit-grouped phone/amount patterns → none (also covered by RPC column shape). Manual: TV route on a second screen updates < 3 s after a board action; Telegram confirmation link opens the same page highlighted on a phone.

## T2.7 — Quality gates + polish pass

Run the DESIGN_SYSTEM §10 checklist wholesale (axe/pa11y in Playwright, touch-target audit, reduced-motion, breakpoints, raw-hex grep). Fix everything. Lighthouse on /board and /q: no CLS > 0.1, TTI sane on tablet CPU throttle.

## Phase 2 Definition of Done

MASTER_PLAN §7 Phase-2 script executed on a physical tablet (or 768×1024 + touch emulation) against live Phase-1 bots; Playwright suite green in CI-able form (`npx playwright test`); §10 checklist all checked; PROJECT_CONTEXT updated.
