# Project Context

Log of decisions and context so any AI picking up this repo has full history. Append new entries at the bottom, don't rewrite old ones.

## What this is

**UAE multi-tenant barbershop platform** (upgraded from single-shop reception system on 2026-07-16 when the owner delivered the full spec in [../Prompt.md.txt](../Prompt.md.txt)): per-shop Telegram bot suite (Customer/Receptionist/Barber/Owner) + global Master bot, AI receptionist (Moonshot, EN/AR/HI/UR), dynamic queue + appointments, POS with hybrid commissions/advances/ledger, tablet web UI, platform-owner dashboard.

## Stack decisions (locked in)

- **Backend:** Python 3.12+, FastAPI, aiogram 3 (bots), Celery 5 + Beat, Redis
- **Database/Auth/Realtime:** Supabase (RLS mandatory)
- **AI:** Moonshot (OpenAI-compatible), intent-extraction + tools only
- **Frontend:** Next.js App Router + Tailwind + shadcn/ui (corrected from generic "React" after owner spec)
- **Deploy:** dev = local polling; prod = 1 VPS Docker Compose (api/worker/beat/redis/caddy)

## Status

2026-07-16 — **Full production plan written, nothing built.** Plan docs: [MASTER_PLAN.md](MASTER_PLAN.md) (start here) → DATA_MODEL, BOT_FLOWS, AI_SPEC, DESIGN_SYSTEM, phases/PHASE_0…PHASE_4. Next action: execute PHASE_0_FOUNDATIONS.md.

## Decision log

- 2026-07-16: Repo scaffolded; explicitly no code yet.
- 2026-07-16: Owner spec delivered (Prompt.md.txt) — multi-tenant bots + POS + AI; supersedes "reception system" scope.
- 2026-07-16: Owner Q&A (4 decisions): **(1)** dev local now → VPS Compose for prod; **(2)** web UI English-only (bots stay 4-language); **(3)** customer phone via Telegram contact-share on first booking, skippable; **(4)** booking = live queue **+ future appointments** (slots → promoted to queue at T-30min).
- 2026-07-16: Full plan written (10 docs). Design tokens locked in DESIGN_SYSTEM.md (dark-first slate + gold brand, Fira Sans/Fira Code/Cormorant). Anti-hallucination: volatile facts (Moonshot model IDs, package versions) marked `VERIFY AT BUILD TIME` in docs.

- 2026-07-16 (later): Owner created 3 GitHub repos (D13 in MASTER_PLAN): `gents-saloon-backend` (this repo — canonical docs + backend + supabase), `saloon-shop-dashboard` (Phase 2 app), `saloon-gents-system-owner-dashboard` (Phase 3 app). Both dashboards deploy on **Vercel from GitHub main**. Consequence: Phase 2 and Phase 3 are now **two separate Next.js apps** (was: one app with /admin) — phase docs updated; shared components get copied between dashboard repos (ponytail: shared package only if a 3rd consumer appears). Local `frontend/` placeholder removed.

<!-- Append new entries below as prompts/features/decisions come in -->
