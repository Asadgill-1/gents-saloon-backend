# Phase 3 — Telegram and AI Reception

## Status — 2026-07-25

**Not started.** Requires the verified Phase 1 tenant platform and Phase 2 booking/money services. No Telegram bot or Moonshot runtime is implemented. See [../../START_HERE.md](../../START_HERE.md).

## Outcome

Four bots per shop and the global master bot deliver secure, multilingual, suspension-aware reception and staff workflows.

## Work

1. Implement encrypted bot registry, development polling, production webhook registration, opaque bot routes, secret-header verification, update dedupe, and health checks.
2. Implement customer, receptionist, barber-crew, owner, and master flows from [../BOT_FLOWS.md](../BOT_FLOWS.md).
3. Resolve owner access across the business and staff access through the bot shop membership on every update.
4. Implement EN/AR/HI/UR customer catalog and English staff flows; every required action has a button path.
5. Implement Moonshot client, strict tools, guardrail, cost/rate/time limits, redacted chat retention, and safe fallback from [../AI_SPEC.md](../AI_SPEC.md).
6. Deliver confirmations, reminders, escalations, and reports through the transactional outbox.
7. Apply subscription gate before business logic/AI; rate-limit generic unavailable replies.

## Gates

- 201-bot registry capacity test (50 shops × four plus master) completes without scope collision.
- Unauthorized Telegram identities and group updates perform no operation.
- Wrong webhook secret fails; duplicate update is a no-op.
- Suspension causes no booking, POS, report, or AI mutation while webhook acknowledgement remains fast.
- Adversarial prompts cannot select tenant IDs, move money, invent prices, or expose other customers.
- Outbox retry produces neither lost nor duplicate notifications.
- Run the full phase security audit from [../security-audits/README.md](../security-audits/README.md), write the dated audit note, and leave zero unresolved Critical/High findings.
- Run `ponytail-debt`.
