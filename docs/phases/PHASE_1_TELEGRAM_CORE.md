# PHASE 1 — TELEGRAM CORE (bots, queue, money, AI, jobs)

Goal: the whole business runs on Telegram alone. Sub-phases 1A→1H strictly in order; each has its own Definition of Done. Flows are specified screen-by-screen in [BOT_FLOWS.md](../BOT_FLOWS.md); data rules in [DATA_MODEL.md](../DATA_MODEL.md); AI in [AI_SPEC.md](../AI_SPEC.md). Bots and API stay thin — every rule lives in `app/services/` (MASTER_PLAN convention 1).

Test accounts needed (owner provides): 1 platform-admin Telegram account, plus at least 2 more accounts (or devices) to play receptionist/barber/customer. 5 BotFather bots for dev: 1 master + 4 for the demo shop.

---

## 1A — Bot infrastructure + Master bot onboarding

Tasks:
1. `app/bots/registry.py`: loads `bots` rows, decrypts tokens, builds one aiogram `Bot` + `Dispatcher` per row with the right router set by role; `ENV=dev` → one polling task per bot (asyncio, started from FastAPI lifespan); `ENV=prod` → `setWebhook(WEBHOOK_BASE_URL/tg/{bot_id}/{webhook_secret}, secret_token=…)`. Registry reload function (called after onboarding creates bots).
2. `api/telegram.py`: `POST /tg/{bot_id}/{webhook_secret}` — validates secret (constant-time) + Telegram header, feeds update to the right dispatcher, always 200 fast.
3. `app/bots/common/`: callback-data codec (`v1|action|id|extra` + pydantic validation), shared keyboards (confirm/cancel, pagination), i18n catalog skeleton (`en` complete; `ar/hi/ur` keys present with English fallback + `# ponytail: translations pending — fill before first Arabic-market shop`).
4. Master bot (BOT_FLOWS §6): platform_admins gate, main menu, full onboarding wizard FSM (§6.2) calling `shop_service.onboard()` — one DB transaction creating shop/bots(encrypted)/staff/services/commission rule, live `getMe` validation per token, test message to each staff role, audit row. [Shops] list + suspend/activate + token replace. (Health/escalations/analytics menus arrive in 1G/1H.)
5. `audit_service.log()` + wire into every mutating service from here on.

Verify:
- pytest: callback codec fuzz (garbage → safe error); onboarding service unit test with mocked Telegram (transaction rolls back wholly on a bad token mid-wizard).
- Manual checklist: onboard "Demo Gents" end-to-end with 4 real dev tokens → all 4 bots answer `/start`; non-admin user gets silence from Master; suspend → customer bot replies "temporarily closed".

## 1B — Customer bot buttons + queue engine

Tasks:
1. `queue_service`: `next_token(shop)` (Redis INCR + DB unique fallback loop), `recompute(shop, barber)` (est_start chain per DATA_MODEL rules, under `lock:barber`), `preview(barber, service)` (position + est wait without writing), `snapshot(shop)` (for views/broadcast).
2. `booking_service`: `create/confirm/cancel/transition` implementing the status machine (DATA_MODEL §2.6) with audit + notification fan-out hooks (notification matrix BOT_FLOWS §7).
3. Customer bot flows 1.1–1.3, 1.5–1.7 (language, name, contact-share, queue booking wizard, My Booking, Live Queue) — buttons only, i18n keys.
4. Receptionist stub: new-booking card push (3.1) with buttons wired but [Confirm] path allowed to land in 1D — for 1B, card renders and [View last 25 messages] shows chat history.
5. `auto_confirm_booking` task (BOT_FLOWS §2) + scheduling on create.

Verify:
- pytest `test_queue.py`: token uniqueness under 50 concurrent creates (asyncio gather); est_start chain math table-driven (3 barbers × mixed durations); preview==post-create position.
- pytest `test_booking_flow.py`: status machine — every legal transition passes, every illegal raises; auto-confirm idempotency (fires after manual confirm → no-op).
- Manual: book via buttons on a real device; let one booking auto-confirm at 5:00 (timer!); token + link arrive; cancel works; two customers same barber → correct positions.

## 1C — Appointments

Tasks:
1. `appointment_service`: `free_slots(barber, service, date)` (15-min grid, shop hours, weekly_off, duration-aware conflicts, past-cutoff), `create` (via booking_service, type=appointment), conflict re-check at confirm time (slot taken while pending → receptionist card shows warning, confirm offers next free slot).
2. Customer bot flow 1.4 (day + slot pickers, paginated).
3. `workers/tasks_appointments.py`: `promote_appointments` (every 5 min: confirmed appointments with `scheduled_at − 30min ≤ now` → assign token, queue_date=today, insert into queue ordered by scheduled_at, notify customer with token) + `appointment_reminders` (T-2h, localized).
4. Beat entries for both (schedule lives in `celery_app.py` from 1H onward; temporary manual trigger acceptable until then — mark `# ponytail:` if so).

Verify:
- pytest: slot generation table-driven (hours, off-day, overlapping bookings, service longer than remaining day); double-book race (two creates same slot → one wins, one gets conflict).
- Manual: book tomorrow's slot; fake-forward `scheduled_at` to now+29min in DB → next promote run assigns token and messages arrive.

## 1D — Receptionist confirm / walk-in / queue ops

Tasks: full BOT_FLOWS §3 — confirm with duration quick-picks (beats auto-confirm via `lock:booking`), reject with reasons, [Queue] working list with [Start]/[No-show]/[Checkout→1E], walk-in wizard (add-to-queue path), staff-auth middleware.

Verify:
- pytest: confirm-vs-autoconfirm race (both fire, exactly one wins, loser silent); walk-in booking gets token immediately; Start blocked while barber has in_service.
- Manual: full counter morning simulation — 2 telegram bookings + 1 walk-in, confirm, start, no-show one, queue list correct after each step; customer messages correct language.

## 1E — POS checkout, commissions, advances, ledger

The money core. `commission_service` is **pure functions** (no I/O) so tests are trivial.

Tasks:
1. `commission_service`: `resolve_rule(shop, barber, date)` per DATA_MODEL §2.9; `split(rule, subtotal) -> (barber_amount, shop_amount)` (Decimal, banker's-safe: round half-up to 0.01, shop gets the remainder so split always sums exactly).
2. `pos_service.checkout(booking|walk_in_direct, items, tip, payment)` — single DB transaction: transactions + items + ledger(commission)+ledger(tip) + booking→completed + last_visit_at + audit; returns the split for the flash screen. Card requires non-empty slip (validated here, not just UI).
3. `advance_service.give(barber, amount, mode)` + ledger row; `ledger_service.balance(barber, period)`.
4. Receptionist flows 3.5 (checkout incl. multi-service + direct-checkout walk-in) and 3.6 (advance); owner-bot advance path reuses the same wizard module.
5. Void (owner-only, owner bot): `pos_service.void(txn)` → reversing ledger rows + audit; keep minimal (list today's txns → [Void] → reason).

Verify (mandatory, table-driven — this is the money):
- `test_commission.py`: fixed 50% on 75.00 → 37.50/37.50; fixed 33% on 10.01 → rounding sums exactly; tiered example from owner spec (>100 → barber flat 25: subtotal 120 → 25/95); tier boundary 100.00 vs 100.01; barber-specific rule overrides shop default; missing rule → error.
- `test_advance_ledger.py`: give 200 one-time → balance drops 200; checkout after → net correct; ledger UPDATE attempt raises (DB trigger).
- Manual: card checkout without slip refused; split flash matches pytest-computed numbers for same inputs; void reverses barber balance.

## 1F — Barber bot + Owner bot

Tasks: BOT_FLOWS §4 (my queue, 5-min reminder with exact spec §4B copy + reminded_at idempotency, month-so-far) and §5 (owner menus over `report_service` live queries + audit log viewer). `report_service.aggregate(shop, date_range)` built here — EOD (1H) reuses it unchanged.

Verify:
- pytest: reminder idempotent (second press → no second customer message); aggregate reconciliation property — for a generated random day of transactions: `shop_profit + barber_commissions + tips == revenue + tips` and per-barber sums match ledger.
- Manual: reminder round-trip on real devices; owner Today numbers equal a hand-computed day.

## 1G — Moonshot AI layer

Tasks: all of [AI_SPEC.md](../AI_SPEC.md) — client wrapper (timeouts, 3-round cap), guardrail pre-filter middleware (before FSM/AI routing), 7 tools dispatching into existing services (zero new business logic), chat persistence, escalation service + Master bot escalation cards with [Block user]/[Monitor]/[Resolve], blocked_users middleware, AI rate limits, buttons-only degradation.

Verify: AI_SPEC §6 suite (guardrail table, tool dispatch, mocked sequences, timeout fallback) + live smoke EN/AR + manual: send a t.me link as customer → canned reply verbatim + Master card < 2s → [Block user] → customer now gets silence everywhere.

## 1H — Beat jobs, EOD/monthly reports, health, retention

Tasks:
1. `send_eod_reports`: beat every 5 min → shops where local time ≥ eod_time and no `eod_reports(shop, today, daily)` row → aggregate via report_service → per-barber **private** message (Revenue, Commission, Tips, Advance deducted, Net payable — owner spec §4C fields) + owner shop summary → insert latch row. `force` param for receptionist manual trigger (BOT_FLOWS 3.7).
2. `send_monthly_reports`: beat daily 00:15 Asia/Dubai; on the 1st → previous month per barber + owner; applies `monthly` advance deductions (capped at positive balance, DATA_MODEL §2.11) inside the same transaction as the latch row.
3. `bot_health_check`: every 5 min getMe (+ getWebhookInfo in prod) per bot → update healthy/last_health_at → transition healthy→unhealthy pushes Master alert (and recovery notice).
4. `purge_chat_messages` monthly (>90 days). Final `beat_schedule` dict assembled in `celery_app.py` (promote/reminders from 1C included).
5. Master [System health] + [Global analytics] menus (BOT_FLOWS §6.4/§6.6) over now-existing data.

Verify:
- pytest: EOD idempotency (run twice → one send, latch respected; force → resend, history versioned); monthly advance deduction cap (advance 500, month net 300 → deduct 300, outstanding 200 rolls over).
- Manual: set demo shop eod_time = now+3min → wait → barber gets secret report, owner gets summary; numbers equal owner-bot [Today]; kill one bot token (revoke in BotFather) → Master alert within 5 min.

---

## Phase 1 Definition of Done

The full MASTER_PLAN §7 Phase-1 happy path executed on real devices in one sitting, filmed or step-logged. All pytest suites green (`pytest backend/tests -q`). Ponytail comments collected into the phase completion note. Update `docs/PROJECT_CONTEXT.md` status line.
