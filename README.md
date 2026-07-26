# Gents Saloon — UAE Multi-Shop SaaS, POS, and Reception

> **New agent or resumed session:** begin with [START_HERE.md](START_HERE.md). It contains the exact implemented state, blockers, verification evidence, and next command.

Production plan and future backend for a multi-tenant saloon platform: one business owner may operate multiple shops; each shop keeps its staff, customers, queue, appointments, POS, cash, commissions, advances, and payouts isolated.

Product surfaces:

- Four Telegram bots per shop plus one global master bot.
- Moonshot-assisted customer reception in English, Arabic, Hindi, and Urdu.
- Shop dashboard for owner overview/switching, reception, POS, money, and reports.
- Platform dashboard for onboarding, cash subscriptions, suspension, exports, and offboarding.
- Manual cash SaaS billing with business-wide or per-shop mode.

Status: Phase 2 is active by explicit owner approval. T2.0–T2.4 are complete locally and on the Supabase development project: operation sources, booking/queue, legal documents, fiscal-year counters, cash shifts, checkout/payments, commission snapshots, and balanced journal posting are verified. T2.5 void/refund/credit-note reversal is next. Phase 1 implementation is complete, but its [dated audit](docs/security-audits/PHASE_1_2026-07-26.md) remains open for inherited credential rotation, authenticated repository-protection evidence, and a live private-Storage round trip.

## Required reading

1. [Current handoff](START_HERE.md)
2. [CLAUDE.md](CLAUDE.md)
3. [Active Phase 2 checklist](docs/phases/PHASE_2_OPERATIONS_MONEY.md)
4. [Latest security audit](docs/security-audits/PHASE_1_2026-07-26.md)
5. [Security rules](docs/SECURITY.md)
6. [Project decisions](docs/PROJECT_CONTEXT.md)
7. [Master plan](docs/MASTER_PLAN.md)
8. [Requirements ledger](docs/REQUIREMENTS.md)
9. [Data model](docs/DATA_MODEL.md)

Supporting specifications: [architecture](docs/ARCHITECTURE.md), [bot flows](docs/BOT_FLOWS.md), [AI](docs/AI_SPEC.md), and [design system](docs/DESIGN_SYSTEM.md).

## Delivery order

1. [Foundation](docs/phases/PHASE_0_FOUNDATIONS.md)
2. [Tenant and SaaS platform](docs/phases/PHASE_1_TENANT_PLATFORM.md)
3. [Booking, POS, and money](docs/phases/PHASE_2_OPERATIONS_MONEY.md)
4. [Telegram and AI](docs/phases/PHASE_3_TELEGRAM_AI.md)
5. [Shop dashboard](docs/phases/PHASE_4_SHOP_DASHBOARD.md)
6. [Platform dashboard](docs/phases/PHASE_5_PLATFORM_DASHBOARD.md)
7. [Production hardening](docs/phases/PHASE_6_PRODUCTION.md)

## Repositories

| Repository | Responsibility | Deployment |
|---|---|---|
| `gents-saloon-backend` | This repository: backend, migrations, canonical docs | Hardened VPS Compose |
| `saloon-shop-dashboard` | Shop operations and business-owner frontend | Vercel |
| `saloon-gents-system-owner-dashboard` | Platform-owner frontend | Vercel |

Canonical documents live here and are synced into the relevant dashboard repository after changes.

## Immediate security action

The local `tokkens.txt` file is now ignored, but its existing Telegram credentials must be rotated by the owner before any bot work. Follow [SECRET_ROTATION_RUNBOOK.md](docs/SECRET_ROTATION_RUNBOOK.md) without copying token values into chat or Git.
