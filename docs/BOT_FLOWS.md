# BOT FLOWS — all 5 bots, screen by screen

Rule from the owner spec §5: **every interaction is buttons (inline keyboards). No typed commands anywhere** except free-text chat on the Customer bot (which routes to AI) and numeric/text inputs inside wizards (amounts, names, slip numbers). `/start` is the only slash command any bot reacts to.

Implementation notes that apply to every flow:
- aiogram 3 Routers per bot under `app/bots/<bot>/`; FSM states in Redis.
- Callback data format: `v1|<action>|<entity_id>|<extra>` — versioned, validated with pydantic; unknown/expired → edit message to "Menu expired. Press /start." (localized).
- Every screen edits the previous message where possible (no chat spam); wizards send fresh messages.
- Customer bot strings from i18n catalog (en/ar/hi/ur). Staff bots English.
- Middleware order (customer bot): blocked_users check → per-shop customers.is_blocked check → flood rate limit → guardrail pre-filter (AI_SPEC §4) → FSM/AI routing.
- Every button press that mutates state calls exactly one service function and answers the callback within 3 s (Telegram limit); slow work goes to Celery.

Concurrency guards (mandatory):
- [Confirm] and auto-confirm both take `lock:booking:{id}` — loser sees "Already confirmed".
- [Start Service] on a barber who has someone `in_service` → warning screen "Finish current customer first" (owner spec implies one chair per barber).

---

## 1. CUSTOMER BOT (per shop; languages en/ar/hi/ur)

### 1.1 First contact (`/start`)
```
[Language screen] — always first, 4 buttons: English | العربية | हिन्दी | اردو
→ saves customers.language (row created here: shop_id + telegram_user_id)
→ if customers.name IS NULL: "Sir, what is your good name?" → free-text captured as name
→ contact share screen: "To serve you better, share your number?"
   [📱 Share my number]  (Telegram request_contact keyboard — one tap)
   [Skip]
→ Main menu
```
Returning customer `/start` → "Mr. {name}, how are you? How can I help you today?" + Main menu.

### 1.2 Main menu
```
[✂️ Book Now]  [📅 Book Appointment]
[🎫 My Booking]  [👀 Live Queue]
[💈 Services & Prices]  [🕑 Timings]
```
Free text at any point (outside a wizard) → AI pipeline (AI_SPEC.md). AI unavailable → "Please use the buttons below 👇" + this menu. **Every booking capability must be reachable with buttons alone.**

### 1.3 [Book Now] — queue booking wizard
```
Step 1: barber picker — active barbers as buttons + [Any barber]
Step 2: service picker — active services "{name} — {price} AED"
Step 3: summary — "Haircut with Ahmed — 50 AED. Current queue: 2 waiting, est. wait ~40 min."
        (numbers from queue_service.preview(barber, service))
        [✅ Confirm request]  [↩️ Back]  [✖️ Cancel]
Step 4: → booking_service.create(type=queue, source=telegram, status=requested)
        → schedules auto_confirm_booking(countdown=300)
        → notifies receptionist bot (flow 3.1)
        → customer sees: "Request sent! You'll get your token and live link shortly."
```
Constraints enforced in service: shop open now (else offer [Book Appointment]); customer has no other active booking today (else show it); barber active.

### 1.4 [Book Appointment] — future appointment wizard
```
Step 1: barber picker
Step 2: service picker
Step 3: day picker — next 7 days minus weekly_off ("Today", "Tomorrow", "Sat 19 Jul", …)
Step 4: slot picker — appointment_service.free_slots(barber, service, day):
        15-min grid within open/close, minus existing appointments (duration-aware),
        minus past times; rendered 4 per row, paginated
Step 5: summary + [✅ Confirm request] → same requested→receptionist→auto-confirm pipeline;
        confirmation message includes date/time instead of token
        ("Your token arrives when you're moved into the live queue ~30 min before.")
```

### 1.5 Confirmation push (sent when receptionist confirms or auto-confirm fires)
```
"Booking Confirmed! Token #3. Estimated wait: 45 mins.
 [🔗 View live queue]  (PUBLIC_QUEUE_BASE_URL/q/{slug})
 [✖️ Cancel booking]"
```
Appointment variant: "Confirmed for Sat 19 Jul, 17:00 with Ahmed."

### 1.6 [My Booking]
Active booking card: status line (Waiting #3 / You're next! / In chair / Confirmed for {dt}), barber, service, est time, [🔗 Live queue] [✖️ Cancel]. None → "No active booking." + [Book Now].
Cancel → confirm screen → `booking_service.cancel(by=customer)` → receptionist + barber notified, queue recomputed.

### 1.7 [Live Queue]
Text snapshot of today's confirmed/in_service list (tokens + first names + barber) + link button. Data from the same query as `get_public_queue`.

### 1.8 5-min reminder push (triggered by Barber bot flow 4.2)
Exact copy (owner spec §4B): "Mr. {name}, your barber is ready for you. Please come to the salon now." (localized).

### 1.9 Guardrail canned reply (owner spec §3)
Exactly: "I apologize, but I cannot process this request. Our management team will review your message." — sent by the pre-filter or the AI `escalate_to_owner` path. No further engagement; repeat offense within 10 min → silent ignore.

---

## 2. Auto-confirm (system flow, no UI)
`auto_confirm_booking(booking_id)` fires 300 s after create:
1. Take `lock:booking:{id}`; re-read status — not `requested` → exit silently.
2. Set status=confirmed, auto_confirmed=true, est_duration_min=shops.default_service_minutes, assign token (`qtoken` INCR + persist), recompute queue.
3. Push customer confirmation (1.5), push barber "New booking: Token #{n}, Mr. {name}" , edit receptionist's card to "✅ Auto-confirmed (no action in 5 min)".
4. Audit `booking.auto_confirm`.

---

## 3. RECEPTIONIST BOT (per shop; English)

Auth middleware: telegram_user_id ∈ staff(role='receptionist' OR 'owner', active) for this shop; else silence.

### 3.1 New booking card (pushed on every `requested` booking)
```
"🆕 Booking request
 Mr. Asad — Haircut — with Ahmed — today (queue)   [or: Sat 19 Jul 17:00 (appointment)]
 Requested 14:02. Auto-confirms 14:07."
 [✅ Confirm]  [❌ Reject]
 [💬 View last 25 messages]
```
- [Confirm] → duration quick-picks: `[15] [20] [30] [45] [60] [Other…]` (Other → type minutes) → `booking_service.confirm(est_minutes)` → token assigned, customer + barber pushed, card edited to "✅ Confirmed — Token #3, 30 min".
- [Reject] → reason quick-picks `[Fully booked] [Barber unavailable] [Other…]` → customer gets polite localized decline.
- [View last 25 messages] → last 25 `chat_messages` rendered `👤/🤖 {text}` (paginated 5/screen).

### 3.2 Main menu (`/start`)
```
[📋 Queue]  [🚶 Walk-in]
[💰 Advance]  [📊 EOD report now]
```

### 3.3 [Queue] — working list
Today's bookings grouped: **Waiting** (confirmed, by est_start) / **In chair** / **Done**. Each waiting entry:
```
"#3 Asad — Haircut — Ahmed — est 14:45"
[▶️ Start]  [🚫 No-show]
```
In-chair entry: `[💳 Checkout]`. Start → status in_service (guard: barber free). No-show → status no_show, queue recomputed, customer notified politely.

### 3.4 [Walk-in] wizard (POS counter, owner spec §4C)
```
barber picker → service picker → customer: [Anonymous] | [Pick recent] | [New name…]
→ [➕ Add to queue]  or  [⚡ Direct checkout] (service already done at counter)
   Add to queue → booking(type=walk_in, source=pos, status=confirmed, token assigned now)
   Direct checkout → jumps to 3.5 with a synthetic completed booking
```

### 3.5 [Checkout] — payment logging
```
Step 1: services on the bill — start with booking's service; [➕ Add service] loop for multi-service
Step 2: "Subtotal: 75 AED. Tip?" quick-picks [0] [5] [10] [20] [Other…]
Step 3: [💵 Cash]  or  [💳 Card]
        Card → "Enter slip number:" (required, non-empty) → "Amount on slip: {total} AED — [Confirm]"
Step 4: SPLIT FLASH (staff-only, owner spec Module 2 Step 4):
        "Shop: 37.50 | Ahmed: 37.50 | Tip: 10.00"   [✅ Done]
→ pos_service.checkout(): one DB transaction = transactions + items + ledger (commission + tip)
  + booking completed + customers.last_visit_at + audit. Queue recomputed, broadcast.
```

### 3.6 [Advance] wizard (owner spec §4C)
```
barber picker → "Amount (AED)?" free-text numeric →
"Deduct how?"  [One-time full deduction]  [Deduct from monthly salary/commission]
→ confirm screen → advance_service.give(): advances row + ledger(advance, −amount) + audit
→ barber's crew bot pushed: "Advance recorded: 200 AED (one-time deduction)."
```

### 3.7 [EOD report now]
Confirm screen → triggers `send_eod_reports(shop_id, force=True)` → "Report queued." (Same Celery code path as the nightly run; `force` bypasses the eod_time check but not the idempotency row — re-run today = resend, payload regenerated and versioned in `eod_reports.payload.history[]`.)

---

## 4. BARBER CREW BOT (per shop; English; one bot shared by all barbers — identity = telegram_user_id)

### 4.1 Main menu (`/start`, auth: staff role barber)
```
[📋 My queue today]  [📈 My month so far]
```

### 4.2 [My queue today]
```
"NOW: #3 Asad — Haircut (started 14:40)"
"NEXT: #5 Omar — Beard — est 15:10"
 … up to 5, then count
[🔔 Customer arriving in 5 mins]   ← only on the NEXT waiting customer
```
Button → customer push (flow 1.8), `reminded_at` set, button becomes "🔔 Sent ✓" (idempotent per booking).

### 4.3 Pushes barbers receive
- "New booking: Token #{n}, Mr. {name}" on every confirm affecting them.
- Cancellation/no-show updates.
- **Daily secret report** (EOD Celery): revenue, commission, tips, advance deducted, net payable — that barber only.
- **Monthly secret report** (1st of month): same fields for the month + outstanding advances.
- Advance notices (3.6).

### 4.4 [My month so far]
Live aggregation from ledger: cuts done, revenue generated, commission, tips, advances outstanding, projected net.

---

## 5. SHOP OWNER BOT (per shop; English; auth: staff role owner)

### 5.1 Main menu
```
[📊 Today]  [📆 This month]
[💇 Barber performance]  [💰 Advances]
[🧾 Audit log]
```
- **Today / This month**: revenue, shop profit, total commissions, tips, txn count (report_service, same numbers as EOD).
- **Barber performance**: per barber — cuts, revenue, commission (paginated).
- **Advances**: open advances list; [➕ Give advance] → same wizard as 3.6 (owners may give advances, owner spec §4C).
- **Audit log**: last 20 entries, humanized, paginated.

---

## 6. MASTER BOT (global, single; auth: platform_admins; English)

### 6.1 Main menu
```
[🏪 Shops]  [➕ Onboard new shop]
[❤️ System health]  [🚨 Escalations]
[🌍 Global analytics]  [⛔ Blocked users]
```

### 6.2 [Onboard new shop] — wizard (FSM, ~10 steps, every step re-promptable on invalid input)
```
1  Shop name (text)
2  Slug (suggested from name; validated unique + regex)
3  Hours: open time → close time (quick-picks) → weekly off day(s) [None][Fri][Sun]…
4  Default service minutes [20][30][45][Other…]   ← the auto-confirm fallback
5  EOD report time [22:00][23:00][23:30][Other…]
6  Bot tokens, one prompt per role (owner/receptionist/barber_crew/customer):
   paste token → live getMe validation → shows "@username OK" or re-prompt.
   (Platform owner creates the 4 bots in BotFather beforehand — runbook SOP; tokens Fernet-encrypted at rest)
7  Services loop: name → price → duration → [➕ another] / [Done]  (≥1 required)
8  Barbers loop: name → telegram id (forwarded message or numeric) → [➕ another] / [Done]  (≥1)
9  Staff: owner telegram id; receptionist telegram id(s)
10 Default commission rule:
   [Fixed %] → barber % quick-picks [40][50][60][Other…]
   [Tiered]  → threshold amount → barber flat below/above prompts (builds tiers json)
11 Review card (everything) → [🚀 Create shop]
    → shop_service.onboard(): all rows in one transaction; prod: setWebhook ×4; dev: polling registry reload;
      test message sent from each bot to its staff; audit 'shop.onboard'
```

### 6.3 [Shops] → per-shop card
`[⏸ Suspend]/[▶️ Activate]` (active flag; customer bot answers "temporarily closed" when suspended), `[📊 Stats]`, `[🔁 Replace a bot token]` (re-prompt + re-validate + re-webhook).

### 6.4 [System health]
Per shop: 4 bots ✅/❌ (bots.healthy from health task), open escalations count, today's booking count. Plus Redis/DB/Celery status line (same checks as `/health`).

### 6.5 [Escalations] (owner spec §4E)
Open escalations, newest first:
```
"🚨 Shop X — Mr. Asad (id 123…)
 Trigger: guardrail. Message: «…»"
[⛔ Block user]  [👁 Monitor]  [✅ Resolve]
[💬 Full context]
```
Block → `blocked_users` insert + escalation status=blocked + audit. Monitor → status=monitoring. New escalations also push instantly (owner spec: "Master Bot instantly notifies you").

### 6.6 [Global analytics]
Across active shops, today + this month: bookings, revenue, AI escalation count, top shop. (Deep analytics live in the Phase 3 dashboard; bot shows the essentials.)

### 6.7 [Blocked users]
Paginated list + [Unblock] per entry.

---

## 7. Push-notification matrix (who gets told what — implementation checklist)

| Event | Customer | Receptionist | Barber | Owner | Master |
|---|---|---|---|---|---|
| booking requested | ack | card 3.1 | — | — | — |
| confirmed (manual/auto) | 1.5 | card edit | new-booking push | — | — |
| cancelled by customer | ack | notify | notify | — | — |
| rejected / no-show | polite notice | card edit | notify | — | — |
| 5-min reminder | 1.8 | — | button feedback | — | — |
| checkout done | thank-you + "see you again" | split flash | — | — | — |
| advance given | — | ack | notice | (if given by receptionist) notice | — |
| EOD / monthly | — | — | secret report | shop summary | — |
| escalation | canned reply | — | — | — | instant card 6.5 |
| bot unhealthy | — | — | — | — | alert |
