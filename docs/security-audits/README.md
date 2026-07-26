# Phase Security Audit Gate

> Current project checkpoint: [../../START_HERE.md](../../START_HERE.md).

Run this gate after every implementation phase and before any release or deployment. The audit covers all three repositories and uses [../SECURITY.md](../SECURITY.md) plus the reusable security baseline version recorded in the audit note.

## Required evidence

1. Run full-history and current-tree secret scans without printing secret values; scan built frontend bundles for backend secret names and values.
2. Run `pip-audit` and `npm audit` from locked dependencies.
3. Run backend and frontend lint, type, test, and production-build checks.
4. Run static guards for dangerous execution/deserialization, wildcard CORS, unverified JWTs, browser token storage, raw HTML sinks, permissive RLS, unsafe Celery serializers, mutable/untrusted CI actions, and client-exposed server secret names.
5. Inspect authentication, object/tenant authorization, input validation, idempotency, logging/redaction, CSP/security headers, Redis/broker exposure, and CI permissions for the code added in the phase.
6. Run the phase-specific adversarial tests: RLS/IDOR for tenant work, race and reconciliation tests for money, webhook/prompt tests for bots and AI, bundle/accessibility/E2E checks for dashboards, and network/restore/penetration checks for production.
7. Run the Supabase security advisor after every migration batch and record results. If the environment is unavailable, the phase remains open.
8. Run `ponytail-audit` separately for complexity and `ponytail-debt` for intentional deferrals.

## Audit note

Create `PHASE_<N>_<YYYY-MM-DD>.md` in this directory. Record scope, baseline version, exact evidence, every finding and severity, resolution, external blockers, complexity result, debt ledger, and final decision.

- Critical or High open: audit fails and the phase cannot complete.
- Medium or Low open: record an owner, trigger/due date, and explicit written acceptance.
- A check that could not run is not a pass.
