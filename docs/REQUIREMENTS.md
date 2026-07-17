# Requirements — feature ledger & traceability

Source of truth: [../Prompt.md.txt](../Prompt.md.txt) (owner spec) + owner Q&A decisions (2026-07-16, logged in PROJECT_CONTEXT.md). Every spec item maps to a phase task below — this table is the "nothing dropped" checklist. Statuses: ☐ planned / ☑ built+verified.

## Confirmed features (traceability matrix)

| # | Requirement (spec ref) | Phase / task | Status |
|---|---|---|---|
| R1 | FastAPI backend, webhooks, business logic (§1) | 0 / 1A | ☐ |
| R2 | Supabase multi-tenant, shop_id isolation, RLS on all tables (§1) | 0 (T0.3) | ☐ |
| R3 | Redis: sessions, queue tokens, booking locks (§1) | 0/1B | ☐ |
| R4 | Celery + Beat: auto-confirms, EOD, monthly, health checks (§1) | 1B/1C/1H | ☐ |
| R5 | Moonshot AI, 4 languages, intent-only, tool-calling, zero hallucination (§1, §3) | 1G | ☐ |
| R6 | 4 bots per shop + telegram_user_id RBAC mapping (§2) | 1A | ☐ |
| R7 | Master bot: onboarding, health, escalations, blocks, global analytics (§2) | 1A/1G/1H | ☐ |
| R8 | Shop Owner bot: revenue, commissions, advances, audit (§2) | 1F | ☐ |
| R9 | Receptionist bot: confirms, service times, walk-ins, advances, EOD trigger (§2) | 1D/1E | ☐ |
| R10 | Barber bot: daily queue, 1-button 5-min reminder, private daily/monthly financials (§2) | 1F/1H | ☐ |
| R11 | Customer bot: AI booking + public queue link (§2) | 1B/1G | ☐ |
| R12 | AI system prompt verbatim + guardrail canned reply exact (§3) | 1G (AI_SPEC §3) | ☐ |
| R13 | Personalization: ask name, greet returning by name (§3) | 1B | ☐ |
| R14 | Dynamic queue booking flow via AI tools (§3, §4A) | 1B/1G | ☐ |
| R15 | Receptionist new-booking card + [View last 25 messages] (§4A) | 1B/1D | ☐ |
| R16 | 5-minute auto-confirm w/ default time; receptionist confirm sets est time (§4A) | 1B/1D | ☐ |
| R17 | Confirmation msg: token + est wait + live queue link; barber notified (§4A) | 1B | ☐ |
| R18 | Barber [Customer arriving in 5 mins] → exact customer message (§4B) | 1F | ☐ |
| R19 | POS walk-in: barber → service → amount (§4C) | 1D/1E | ☐ |
| R20 | Hybrid commissions: Rule A fixed %, Rule B tiered threshold (§4C) | 1E | ☐ |
| R21 | Tips 100% to barber, logged separately (§4C) | 1E | ☐ |
| R22 | Advances: one-time or monthly deduction, ledger adjusted (§4C) | 1E/1H | ☐ |
| R23 | EOD secret reports per barber (daily + monthly on 1st) + owner summary (§4C) | 1H | ☐ |
| R24 | Escalations table + instant Master notify + [Block]/[Monitor] (§4E) | 1G | ☐ |
| R25 | ALL bot interactions buttons, not typed commands (§5) | every bot task | ☐ |
| R26 | Web auth: email/password Supabase, no Telegram login (Ph2-A) | 2 (T2.2) | ☐ |
| R27 | Public read-only queue URL for TV + customer phones (Ph2-A) | 2 (T2.6) | ☐ |
| R28 | Tablet UX: touch-first 60px, minimal typing, dark mode, Realtime (Ph2-B) | 2 (DESIGN_SYSTEM) | ☐ |
| R29 | Live Queue Board: 3 columns + Start/Reminder/No-show/Checkout (Module 1) | 2 (T2.3) | ☐ |
| R30 | POS modal: services multi-select → tip → cash/card+slip → split flash (Module 2) | 2 (T2.4) | ☐ |
| R31 | Owner analytics: today metrics, barber table, retention widget, advance mgmt (Module 3) | 2 (T2.5) | ☐ |
| R32 | Public display mode: tokens only, zero financial/personal data (Module 4) | 2 (T2.6) | ☐ |
| R33 | Platform owner dashboard: onboarding + manage full business (Ph3) | 3 (all) | ☐ |
| R34 | **Future appointments** (owner decision 2026-07-16): slots, promotion to queue, reminders | 1C | ☐ |
| R35 | **Phone capture** via Telegram contact share, skippable (owner decision) | 1B | ☐ |
| R36 | Production deploy: VPS + Docker Compose + TLS + backups + monitoring + runbook | 4 (all) | ☐ |

## Open questions

- Guardrail canned reply: English-only exact sentence vs localized translations (default: exact English; AI_SPEC §4).

Resolved 2026-07-16: frontend hosting = Vercel from GitHub for both dashboards (owner decision D13, MASTER_PLAN).

## Out of scope (owner spec silent / explicitly deferred — MASTER_PLAN §8)

Online payments, customer web accounts, SMS/WhatsApp, inventory/loyalty/broadcasts, Arabic web UI, multi-currency.
