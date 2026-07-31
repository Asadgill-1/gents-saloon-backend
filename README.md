# Gents Saloon — UAE Multi-Shop SaaS, POS, and Reception

> **New agent or resumed session:** begin with [START_HERE.md](START_HERE.md). It contains the exact implemented state, blockers, verification evidence, and next command.

Production plan and future backend for a multi-tenant saloon platform: one business owner may operate multiple shops; each shop keeps its staff, customers, queue, appointments, POS, cash, commissions, advances, and payouts isolated.

Product surfaces:

- Four Telegram bots per shop plus one global master bot.
- Moonshot-assisted customer reception in English, Arabic, Hindi, and Urdu.
- Shop dashboard for owner overview/switching, reception, POS, money, and reports.
- Platform dashboard for onboarding, cash subscriptions, suspension, exports, and offboarding.
- Manual cash SaaS billing with business-wide or per-shop mode.

Status: Phase 2 is locally verified and committed on the recovery branch. Phase 3 is incomplete and being rebuilt; Phases 4 and 5 contain reusable visual prototypes but still use mock data and client-only mutations, so they are not complete. Phase 1 implementation remains open for inherited credential rotation, authenticated repository-protection evidence, and a live private-Storage round trip. See [START_HERE.md](START_HERE.md) for the current evidence and gates.

## Required reading

1. [Current handoff](START_HERE.md)
2. [CLAUDE.md](CLAUDE.md)
3. [Active Phase 3 checklist](docs/phases/PHASE_3_TELEGRAM_AI.md)
4. [Latest completed security audit](docs/security-audits/PHASE_2_2026-07-26.md)
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
