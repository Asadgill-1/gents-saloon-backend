# DESIGN SYSTEM — Phase 2 tablet app + Phase 3 platform dashboard

Design decisions are **embedded here in full** so any LLM can execute Phases 2–3 without external skills. If the executor has the `ui-ux-pro-max`, `frontend-design`, or `design` skills available, invoke them at phase start for refinement — but their output must not contradict the locked tokens below without an owner decision.

## 1. Direction

Premium gents-barber **operations tool**, not a marketing site. Two personalities, one system:

- **Working screens** (queue board, POS, analytics): dark-first, dense, calm. Slate surfaces, one accent per meaning. Everything reachable with a thumb on a 10" tablet at a busy counter.
- **Brand moments** (public TV board, login screen): the same dark canvas but with the shop's premium face — gold accents, serif display type, oversized token numerals.

**Signature element** (spend the boldness here, keep everything else quiet): the TV board's giant gold tabular token numerals — "NOW SERVING / 07" — with a single thin animated barber-pole divider stripe (CSS gradient animation, paused under `prefers-reduced-motion`). No other decorative motion anywhere in the product.

## 2. Color tokens

Define as CSS variables in `globals.css`; Tailwind reads them via the config. Semantic names only in components — never raw hex.

| Token | Dark (default) | Light | Use |
|---|---|---|---|
| `--bg` | `#020617` | `#F8FAFC` | app background |
| `--surface` | `#0F172A` | `#FFFFFF` | cards, columns |
| `--surface-2` | `#1E293B` | `#F1F5F9` | nested cards, modal body |
| `--border` | `#334155` | `#E2E8F0` | hairlines |
| `--text` | `#F8FAFC` | `#0F172A` | primary text |
| `--text-muted` | `#94A3B8` | `#64748B` | secondary text |
| `--brand` | `#C9A24B` (gold; on dark) | `#A16207` | brand moments, TV board, active nav |
| `--accent` | `#22C55E` | `#16A34A` | confirm actions, "paid", positive |
| `--warn` | `#F59E0B` | `#D97706` | waiting-long, pending |
| `--danger` | `#EF4444` | `#DC2626` | no-show, void, destructive |
| `--info` | `#38BDF8` | `#0284C7` | in-service, links |

Rules: status is never conveyed by color alone (icon + label always). Contrast ≥ 4.5:1 for text in **both** themes — verify with a checker during build; the gold `#C9A24B` is for large display text and accents on dark, use `--brand` light value `#A16207` for small text on light. Dark theme is default (salon evening ambience, OLED TV); toggle persisted per device (localStorage).

## 3. Typography

| Role | Font | Notes |
|---|---|---|
| UI (everything) | **Fira Sans** 400/500/600 | body 16px, line-height 1.5 |
| Numbers: tokens, prices, timers, table figures | **Fira Code** 500/700, `font-variant-numeric: tabular-nums` | zero layout shift on live updates |
| Brand display (TV board title, login wordmark) | **Cormorant** 600 | large sizes only (≥32px) |

```css
@import url('https://fonts.googleapis.com/css2?family=Fira+Sans:wght@400;500;600&family=Fira+Code:wght@500;700&family=Cormorant:wght@600&display=swap');
```
(Phase 2 uses `next/font/google` with these same families instead of the CSS import.)

Type scale (px): 12 (labels/eyebrows only, never body) · 14 · 16 (base) · 18 · 24 · 32 · 48 · TV numerals 120–200 (clamp by viewport).

## 4. Spacing, layout, touch

- Spacing scale (dense/dashboard): 4 · 8 · 12 · 16 · 24 · 32. Section gaps 24; card padding 16; grid gap 12.
- **Touch targets: 60×60px minimum for all queue/POS actions** (owner spec Module rule; exceeds the 44px platform minimum — do not shrink). 8px+ between adjacent targets. `touch-action: manipulation` globally; `cursor-pointer` on clickables.
- Breakpoints: design at **768×1024 (tablet portrait) and 1024×768 (landscape) first**; 375px phone (customer queue page) and ≥1280 desktop (owner/platform dashboards) second. No horizontal page scroll ever; wide tables scroll inside their own container.
- Radius: 12px cards, 10px buttons, 16px modals. Shadows minimal on dark (elevation via surface steps), soft single shadow on light.
- Icons: **Lucide** only, 1.5px stroke, 20/24px. Never emoji as UI icons (bots use emoji in Telegram text — that convention stays in Telegram).

## 5. Motion

- Micro-interactions 150–250ms, ease-out in / ease-in out; press feedback scale 0.97.
- Queue card moving column: 200ms translate+fade (FLIP or `framer-motion` layout — decide at build; prefer CSS-only if it stays simple).
- New-booking arrival on board: 1 subtle background pulse in `--warn`, once.
- Skeletons for anything > 300ms; no spinners on full screens.
- `prefers-reduced-motion`: all of the above off (instant swaps), barber-pole paused.

## 6. Module specs (Phase 2)

### 6.1 Live Queue Board — receptionist home (`/board`)

```
┌────────────────────────────────────────────────────────────────┐
│ ☰ Gents Saloon      ● Live        13:42   [Walk-in +]  [⚙]     │
├──────────────────────┬──────────────────────┬──────────────────┤
│ WAITING (4)          │ IN CHAIR (2)         │ PAID TODAY (17)  │
│ ┌──────────────────┐ │ ┌──────────────────┐ │ ┌──────────────┐ │
│ │ #07  Asad        │ │ │ #05  Omar        │ │ │ #04 Bilal    │ │
│ │ Haircut · Ahmed  │ │ │ Beard · Saif     │ │ │ 75 AED cash  │ │
│ │ est 13:55 · 12m  │ │ │ since 13:20      │ │ │ 13:31        │ │
│ │ [▶ Start][🔔][✖] │ │ │ [💳 Checkout]    │ │ └──────────────┘ │
│ └──────────────────┘ │ └──────────────────┘ │  …compact list   │
│  …cards sorted by    │                      │                  │
│  est_start           │                      │                  │
└──────────────────────┴──────────────────────┴──────────────────┘
```

- 3 columns per owner spec Module 1: Waiting `[Start Service] [5-Min Reminder] [No Show]`, In-Chair `[Checkout]`, Paid (read-only compact).
- Card fields: token (Fira Code, 24px), first name, service, barber, est wait. Waiting > 15 min past est → card border `--warn`.
- Realtime: Supabase Postgres Changes on `bookings`/`transactions` (shop JWT) → optimistic column moves; on reconnect full refetch. Connection lost > 5s → sticky offline banner "Reconnecting — data may be stale".
- [No Show] and [✖] destructive → confirm dialog. Empty state per column ("No one waiting — enjoy the quiet ☕" style copy, one line, no illustration bloat).
- Appointments due within 30 min appear at top of Waiting with a clock badge instead of token until promoted.

### 6.2 POS Checkout Modal (owner spec Module 2 — 4 steps, one modal)

```
Step 1 SERVICES   — service grid (60px buttons, multi-select, qty via re-tap), running total top-right
Step 2 TIP        — [0][5][10][20][Custom] AED
Step 3 PAYMENT    — [💵 Cash] [💳 Card]; Card reveals: Slip number* (required) + amount echo
Step 4 SPLIT      — staff-only flash card: "Shop 37.50 · Ahmed 37.50 · Tip 10.00" + [Confirm & close]
```
- Modal `max-w-2xl`, step indicator, [Back] every step, dismissible only at step 1 (after = explicit cancel confirm; sheet-dismiss-confirm rule).
- Mutation goes through the **backend API** (`POST /api/pos/checkout`, service-role path with JWT check) — not direct Supabase insert — so the ledger/commission logic stays single-sourced (same `pos_service.checkout` the bot uses).
- Success: green check flash 800ms, modal closes, card animates to Paid column.

### 6.3 Owner Analytics (`/analytics`, owner JWT)

Owner spec Module 3, top to bottom:
- 4 stat tiles (Today): Revenue · Shop profit · Barber payouts · Tips — Fira Code numerals, delta vs yesterday.
- Barber performance table: cuts, revenue, commission (sortable, tabular numerals).
- Retention widget: New vs Returning donut + 14-day trend line (Recharts; returning = customer's phone or telegram_id seen before; "New" = first transaction that day).
- Advances panel: outstanding list, [Give advance] (same wizard as bots), manual deduct entry.
- Date range picker: Today / Yesterday / This week / This month / Custom. All numbers must reconcile exactly with EOD reports (same `report_service` queries via API).

### 6.4 Public TV Display (`/q/[slug]` — no auth, the signature screen)

```
┌────────────────────────────────────────────┐
│        GENTS SALOON          (Cormorant)   │
│  ───────────── barber-pole ─────────────   │
│                                            │
│   NOW SERVING                              │
│   ┌──────┐  ┌──────┐                       │
│   │  05  │  │  06  │   (gold, 160px,       │
│   │ Saif │  │ Ahmed│    Fira Code)         │
│   └──────┘  └──────┘                       │
│                                            │
│   UP NEXT                                  │
│   07 Asad · Ahmed     ~13:55               │
│   08 Omar · Saif      ~14:10               │
│   09 Guest · Ahmed    ~14:25               │
│                                            │
│   "Token #07 — please take a seat"         │
└────────────────────────────────────────────┘
```
- Data: `get_public_queue` RPC + Broadcast channel `queue:{slug}`; 15s poll fallback. First names only; **zero financial data, zero phone numbers** (enforced at the RPC, not the UI).
- Dark only, OLED-friendly, no interaction, cursor hidden, auto-fit 16:9 and 4:3, screen-burn shift (1px drift per minute).
- Customer phones open the same route (responsive) from the Telegram confirmation link — their own token highlighted via `?t=07` param.

## 7. Phase 3 dashboard (platform owner) — same system

Same tokens/components; desktop-first (≥1280), sidebar nav (Shops · Onboarding · Analytics · Escalations · Blocked users · Bot health · Audit). Data density high: tables over cards. Platform accent stays `--brand` gold; destructive platform actions (suspend shop, block user) always double-confirm with typed shop name for suspend. Escalation console reuses the chat-transcript component from the receptionist "last 25 messages" web view.

## 8. Component inventory (build once, in `frontend/components/ui` + `components/app`)

shadcn/ui base: Button, Dialog, Sheet, Tabs, Table, Badge, Toast, Skeleton, Select, Input, DropdownMenu.
App components: `StatTile`, `QueueCard`, `QueueColumn`, `TokenNumeral` (Fira Code + tabular), `SplitFlash`, `ServiceGridButton`, `ChatTranscript`, `OfflineBanner`, `ThemeToggle`, `BarberPoleDivider` (the one decorative element), `EmptyState`, `ConfirmDialog`.

## 9. Copy rules (from frontend-design principles)

Sentence case everywhere. Buttons say what happens: "Start service", "Confirm booking", "Give advance" — never "Submit"/"OK". Same verb through a flow (button "Checkout" → toast "Checked out"). Errors: cause + fix ("Card slip number is required — find it on the card machine receipt"), no apologies, no vagueness. Empty states invite the next action in one line.

## 10. Build-time quality gates (Phase 2/3 definition of done inputs)

- [ ] Contrast pass both themes (automated: axe or pa11y in Playwright run)
- [ ] All targets ≥ 60×60 on board/POS; ≥ 44×44 elsewhere
- [ ] Keyboard: full checkout flow completable without touch; visible focus rings
- [ ] `prefers-reduced-motion` verified (barber-pole paused, moves instant)
- [ ] 768×1024, 1024×768, 375×812, 1920×1080 (TV) all no-horizontal-scroll
- [ ] Realtime disconnect/reconnect shows banner and recovers state
- [ ] No raw hex in components (grep `#[0-9A-Fa-f]{6}` limited to globals.css)
