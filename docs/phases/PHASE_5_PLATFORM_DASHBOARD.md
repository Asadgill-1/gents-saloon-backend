# Phase 5 — Platform Dashboard and SaaS Operations

## Status — 2026-07-31

**Incomplete; visual prototype only.** The current screens preserve the intended styling, but tenant, receipt, offboarding, bot-health, and analytics records are hardcoded and actions are not persisted. Phase 5 remains open until cookie-based Supabase SSR, backend operations, pagination, guarded confirmations, Playwright, and its dated security audit pass. See [../../START_HERE.md](../../START_HERE.md).

## Outcome

The platform owner manages businesses, shops, cash subscriptions, bot fleet, security, exports, and offboarding from one production console.

## Work

1. Apply the UI implementation skills named in CLAUDE.md and build a business-first information architecture.
2. Build business/primary-owner onboarding, multi-shop creation, staff invites, bot setup status, and legal/tax configuration.
3. Build billing-mode setup/change, sequential cash receipt/reversal, due/expired filters, suspend/resume, and immutable history.
4. Build export generation/download state and guarded offboarding workflow.
5. Build global/business/shop analytics, bot fleet health, escalations, blocked users, audit explorer, backup/export status, and security events.
6. Require explicit reason and double confirmation for destructive/high-impact operations; show scope before confirmation.
7. Keep platform API authorization and audit server-side; frontend holds no global Supabase mutation policy.

## Gates

- A shop/business owner receives no platform route/API data.
- Cash payment extends only the selected billing scope; mode change cannot overlap coverage.
- Suspend/resume/export/offboard UI passes full API/state/audit E2E.
- Historical commission/subscription/financial rows cannot be silently edited.
- 50-shop/201-bot health view paginates and remains usable.
- Accessibility, responsive, build, dependency, and bundle-secret checks pass.
- Run the full phase security audit from [../security-audits/README.md](../security-audits/README.md), write the dated audit note, and leave zero unresolved Critical/High findings.
- Run `ponytail-debt`.
