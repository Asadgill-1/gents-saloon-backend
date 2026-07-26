# Phase 4 — Shop and Business-Owner Dashboard

## Status — 2026-07-25

**Product UI not started.** The separate repository has only a verified Next.js/Supabase SSR technical foundation. Begin this phase only after Phases 1–3 expose stable, tested APIs. See [../../START_HERE.md](../../START_HERE.md).

## Outcome

The shop dashboard gives owners a consolidated business view and shop switcher while receptionist/barber experiences remain isolated and touch-first.

## Work

1. Apply `ui-ux-pro-max`, `frontend-design`, `design-system`, and `ui-styling` to implement the approved design contract.
2. Build login/session shell, server-verified context, owner business overview, shop switcher, and role-specific navigation.
3. Build queue board, appointments, walk-ins, checkout, receipts, cash shifts, services/staff, commission view, advances, payouts, and reports.
4. Use FastAPI for every mutation and backend report totals for every headline number.
5. Subscribe to authorized Realtime projections; refetch after reconnect and rollback optimistic UI on API failure.
6. Build opaque-token public queue with active/unavailable states and no PII.
7. Add browser print/PDF receipts, responsive tablet/desktop behavior, accessibility, empty/error/loading states, and exact suspension shell.

## Gates

- Playwright actor matrix proves owner aggregate/switching and staff isolation.
- A bot-created booking appears within three seconds at target load.
- Touch targets, keyboard use, focus order, contrast, responsive layouts, and reduced motion pass.
- Public route contains no names, phones, chats, money, IDs, or billing details.
- No TypeScript commission/VAT/authorization logic and no direct Supabase mutation.
- Build, unit, E2E, accessibility, dependency, and bundle-secret checks pass.
- Run the full phase security audit from [../security-audits/README.md](../security-audits/README.md), write the dated audit note, and leave zero unresolved Critical/High findings.
- Run `ponytail-debt`.
