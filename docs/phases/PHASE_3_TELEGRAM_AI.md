# Phase 3 — Telegram and AI Reception

## Status — 2026-08-01

**Security foundation, customer button flows, core receptionist operations, and transactional AI booking tools implemented; full phase incomplete.** A new forward migration and backend services now provide AES-256-GCM token envelopes, HMAC webhook-secret digests, durable update claims/deduplication/retry, database-derived role authorization, private-chat enforcement, aiogram transport, retrying outbox delivery, callback allowlisting, Redis-backed flood/AI budgets, strict AI tool schemas, redacted chat storage/retention, and authoritative rendering tests. Durable opaque customer callbacks now cover EN/AR/HI/UR language selection, service/barber/date/slot selection, requested queue creation, five-minute appointment holds and confirmation, own-booking views, cancellation/rescheduling, live queue, authoritative prices, and idempotent sanitized escalation. Receptionist queue/appointment cards reauthorize the active same-shop membership and execute confirm/reject/start/complete/no-show/cancel through the existing idempotent booking service. Cash-shift open, pay-in/pay-out, close, reconciliation, and a shop-local EOD summary now use short authorized sessions and existing backend accounting/report truth. Allowlisted AI tools find the same server-derived appointment slots and call the transactional create, cancel, and reschedule services. The 17 applied Phase 1/2 migrations remain immutable.

Receptionist walk-in/checkout and the owner-authorized advance handoff plus all barber/owner/master callbacks still acknowledge selections with a placeholder; customer contact-share/name capture, five-minute reminder, and full notification cards also remain. The receptionist bot does not weaken the Phase 2 rule that only owners/platform administrators may grant advances. Multilingual snapshot coverage, the 201-bot load gate, the complete adversarial role/retry/hallucination matrix, live Telegram/Moonshot staging proof, and the dated security audit remain open. See [../../START_HERE.md](../../START_HERE.md).

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
