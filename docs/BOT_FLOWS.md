# Bot Flows — Four per Shop plus Global Master

> Target Phase 3 behavior; bot flows are not implemented yet. Read [../START_HERE.md](../START_HERE.md) for current status and prerequisites.

Customer bot supports English, Arabic, Hindi, and Urdu. Staff/master bots are English in first release. `/start` is the only command; actions use buttons, except customer free chat and validated wizard inputs.

## 1. Rules shared by every bot

Adapter order:

```text
private-chat check
→ bot/update authentication and update_id dedupe
→ platform/customer block check
→ flood limit
→ resolve bot, business, shop, and actor
→ effective subscription gate
→ guardrail (customer free text)
→ FSM/handler
```

- During inactive subscription/offboarding/archive, acknowledge a valid webhook, run no domain/AI operation, and rate-limit one localized generic unavailable reply.
- Callback data is versioned and validated. IDs are never authorization; handler rechecks entity scope and actor membership.
- Each mutation button calls one domain service with an idempotency key. Slow notifications use the outbox.
- PostgreSQL locks/constraints resolve races. A Redis lock may reduce contention but cannot be required for correctness.
- Unknown/expired menu: edit to a localized “This menu expired. Start again.” response.

## 2. Customer bot

### First contact

```text
Choose language: English | العربية | हिन्दी | اردو
→ optional name
→ optional Telegram contact-share phone
→ main menu
```

Main menu:

```text
[Book now] [Book appointment]
[My booking] [Live queue]
[Services & prices] [Talk to reception]
```

### Book now

1. Select one or more active services.
2. Select a qualified barber or “Any barber.”
3. Show application-rendered service summary and current estimate.
4. Confirm.
5. Create `requested`; receptionist receives an outbox notification.
6. Receptionist confirms/rejects, or five-minute auto-confirm uses shop default duration.
7. Customer receives token, estimate, assigned barber, and opaque live-queue link.

### Appointment

1. Select services, date, then available barber/Any Barber.
2. Availability includes business hours, closures, schedule, breaks, leave, current holds, and appointments.
3. Create five-minute hold; show available start times.
4. Confirm before expiry or restart.
5. Reschedule/cancel require confirmation and optional reason.
6. Promote to live queue at T-30 minutes idempotently.

### My booking

Show only this customer’s active shop booking: status, own token, services, barber, estimate/time, live queue, reschedule/cancel when permitted.

### Live queue

Show the same privacy-safe projection as public web: token number, coarse status, barber/chair label, and estimate. Never show another customer’s name.

### Reminder and escalation

- Barber “customer arriving in five minutes” sends a localized reminder once.
- Guardrail/escalation returns the approved localized safe sentence and creates a sanitized escalation through the outbox.
- AI can never confirm a price, position, time, or booking unless a tool returned it.

## 3. Receptionist bot

Main menu:

```text
[Queue] [Appointments]
[Walk-in] [Checkout]
[Cash shift] [Advance]
[EOD report]
```

New booking card contains this-shop customer/service/barber details, estimate controls, confirm/reject, and authorized last-25-message view.

Queue actions:

```text
requested: confirm | reject
confirmed: start | five-minute reminder | no-show | cancel
in service: checkout
```

Walk-in: choose barber → services → optional customer → queue/start choice.

Checkout:

1. Review/add service line snapshots and authorized discount.
2. Choose tip.
3. Record cash/card split rows; each card row requires slip reference.
4. Show backend-calculated gross/net/VAT/tip, barber commission, and shop share.
5. Confirm once; return printable receipt link/reference.

Advance:

```text
choose barber → amount → one-time-next-payout or monthly-payout policy
→ confirm cash disbursement and outstanding receivable
```

The grant does not reduce earned commission. Deduction occurs in the applicable payout run.

Cash shift supports open with float, pay-in/pay-out reason, expected preview, and close with counted amount/variance.

## 4. Barber crew bot

Identity is the Telegram user ID mapped to an active barber membership for this bot’s shop.

Main menu:

```text
[My queue today] [My earnings]
[My payouts] [My advances]
```

Queue shows current and next customers for this shop only. Reminder is available once on the next confirmed booking.

Private financial views show:

- closed transaction count and service revenue attributed;
- commission and tips earned;
- payout periods and net paid;
- advance outstanding and deductions actually applied.

An advance notice states cash given and when deduction is scheduled. It never describes the amount as already deducted.

## 5. Business-owner bot

There is an owner bot per shop, but an authenticated primary owner can see the owned business:

```text
[Business today] [Choose shop]
[Shop today] [This month]
[Barber performance] [Advances & payouts]
[Audit] [Subscription status]
```

- Business summary aggregates all owned shops and permits drill-down.
- Shop operations always require an explicit owned shop context.
- Money totals come from backend reports/journal reconciliation.
- Owner may create an advance/payout only under the configured role policy and receives a final scope/amount confirmation.
- Owner can view subscription state but cannot self-resume, change `paid_until`, or see platform-only receipt evidence.

## 6. Global master bot

Only active platform-admin Telegram identities receive a response.

Main menu:

```text
[Businesses] [Onboard business]
[Cash subscriptions] [Due / suspended]
[Exports / offboarding] [Bot health]
[Escalations] [Global analytics]
[Blocked users] [System health]
```

### Onboarding

1. Business legal/display/contact and optional trade-license/VAT details.
2. Primary owner identity and secure Auth invite flow.
3. Billing mode: business-wide or per-shop.
4. Initial coverage and cash receipt/reference.
5. One or more shops: schedule, public configuration, legal override if needed.
6. Services and default commission rule.
7. Staff assignments.
8. Four bot tokens validated through `getMe`, encrypted, and webhook/polling registered.
9. Review full business/shop/billing scope and create atomically.

Never send bot tokens through chat history in a production onboarding flow; use the authenticated platform web dashboard for token entry/rotation. Master bot may show setup status only.

The Supabase Auth invitation is a trusted-server prerequisite and produces the owner UUID. The tenant-domain transaction starts only after that UUID exists; it creates the application profile and ownership/shop/subscription/audit/outbox records atomically. Direct writes to Supabase-managed Auth tables are prohibited.

### Cash subscription

Choose business → billing scope → amount → receipt reference → coverage/`paid_until` → confirm. Result is an append-only receipt, entitlement update, audit/outbox evidence, and one provider-neutral B2B `prepared` e-invoice source envelope. Correction uses a linked mirror reversal, not edit, and creates a linked credit-note source envelope. Reversal corrects cash evidence; any access removal remains a separate audited suspension/coverage action. Provider transmission is not part of this flow.

### Suspend/resume

Show exact business/shop scope and impact, require reason and double confirmation. Resume non-payment only with current paid coverage or time-bound documented override.

### Export/offboard

Start export → poll status → notify when checksum-protected download is ready → mark delivery → archive only after confirmation. Never hard-delete.

### Health/escalations

Health covers four bots per shop plus API/database/Redis/worker/outbox/backup state without exposing secrets. Escalations show sanitized context with block/monitor/resolve. Global analytics are summary only.

## 7. Notification guarantees

| Event | Customer | Receptionist | Barber | Business owner | Platform admin |
|---|---|---|---|---|---|
| Booking requested | acknowledgement | action card | — | — | — |
| Confirmed/rescheduled/cancelled | yes | yes | assigned barber | — | — |
| Five-minute reminder | yes | acknowledgement | sent state | — | — |
| Checkout | receipt summary | confirmation | earnings update | report aggregation | — |
| Advance granted/deducted | — | confirmation | private notice | confirmation | — |
| Subscription suspended/resumed | generic unavailable only | generic unavailable only | generic unavailable only | generic status notice | full audited detail |
| Escalation | safe reply | optional notice | — | optional notice | immediate action card |
| Export ready | — | — | — | delivery notice if policy allows | full link/status |

All sends originate from committed outbox events with unique dedupe keys.
