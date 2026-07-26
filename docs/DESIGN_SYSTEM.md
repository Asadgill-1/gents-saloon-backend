# Design System — Gents Saloon Operations

> Approved target for Phases 4–5. Only the technical frontend foundations exist today. Read [../START_HERE.md](../START_HERE.md) for current status.

Applies to the shop/business-owner and platform-owner Next.js apps. Direction: premium Dubai grooming craft meets a precise live operations board. The interface is dense where work is fast, calm where decisions are risky, and never looks like a generic gradient SaaS template.

## 1. Signature and principles

The signature is a restrained barber-pole status rail: a thin red/white/blue moving line signals live connectivity and pauses when offline or reduced-motion is enabled. It is functional, used once in the application shell/public display, not decorative wallpaper.

Principles:

- Operational truth first: queue state and money scope are unmistakable.
- One high-impact action per view.
- Owner always sees whether numbers are business-wide or for one named shop.
- Dangerous actions state scope/consequence and require explicit confirmation.
- Active, suspended, offboarding, archived, offline, loading, empty, and error states are first-class.
- Icons are Lucide SVGs with labels/tooltips; no emoji UI.

## 2. Three-layer tokens

Components use component tokens → semantic tokens → primitives. Raw colors never appear in component code.

Core primitives:

| Token | Value | Purpose |
|---|---:|---|
| `stone-950` | `#0C0A09` | dark canvas |
| `stone-900` | `#1C1917` | raised dark surface |
| `stone-700` | `#44403C` | secondary |
| `stone-200` | `#E7E5E4` | light border |
| `stone-50` | `#FAFAF9` | light canvas |
| `gold-700` | `#A16207` | brand/action; chosen for contrast |
| `green-600` | `#059669` | paid/success |
| `amber-600` | `#D97706` | due/warning |
| `red-600` | `#DC2626` | destructive/expired |
| `blue-600` | `#2563EB` | informational/live |

Semantic themes:

```text
background / foreground
surface / surface-raised / border
muted / muted-foreground
brand / brand-foreground / focus-ring
success / warning / destructive / info
money-positive / money-negative
status-live / status-offline / status-suspended
```

Dark is default for shop queue/POS and public display. Platform administration defaults light for dense long sessions; both apps support both themes. Contrast must meet WCAG AA; status never relies on color alone.

Component tokens cover at least button, input, table, card, queue card, stat tile, badge, dialog, toast, sidebar, and status rail. Token files are the only theme source.

## 3. Typography and data

- Body/UI: Fira Sans, 16px minimum body, 1.5 line height.
- Numbers/tokens/receipts: Fira Code with tabular numerals.
- Brand/display: Cormorant Garamond, used only for shop identity/public display—not admin headings.
- Hierarchy: 12 utility, 14 table/meta, 16 body, 20 section, 28 page, 40 business KPI, 96–160 public token.
- AED values always include currency in accessible text; negative/positive meaning is not color-only.
- Date/time includes timezone where ambiguity matters.

## 4. Spacing, shape, and motion

- 4px base spacing; dense dashboard scale 4/8/12/16/24/32.
- Board/POS touch targets ≥60×60px; all other interactive targets ≥44×44px with ≥8px separation.
- Cards use subtle depth and 10–12px radius; no neumorphism, excessive blur, or floating-card grids without hierarchy.
- Transitions 150–250ms for feedback/spatial continuity. No decorative scroll animation in authenticated dashboards.
- Queue moves may animate transform/opacity; never animate layout dimensions.
- `prefers-reduced-motion` disables status-rail movement and uses instant state transitions.

## 5. Application shell and navigation

### Shop/business app

Owner shell:

```text
Business name | scope pill: All shops / Shop name | shop switcher | status
Overview · Queue · Appointments · POS · Cash · Team · Money · Reports
```

Receptionist/barber shell omits business-wide navigation and shows the fixed assigned shop. A user with more than one explicit assignment may switch only among returned memberships.

On mobile, use a maximum of five primary bottom destinations plus More. Tablet/desktop uses a stable side/top rail. Scope persists in the URL as an authorized opaque shop ID, never PII.

### Platform app

Business-first navigation:

```text
Businesses · Billing · Due/suspended · Exports · Fleet · Escalations
Analytics · Audit/security · System
```

Business detail owns nested Shops, Owner, Billing, Receipts, Exports, and Offboarding. Dense tables are preferred over repetitive KPI cards.

## 6. Shop modules

### Business overview

- Date range and explicit “All shops” scope.
- Revenue/service net/VAT/tips/shop share/barber payable from report API.
- Shop comparison table with status, today sales, queue, variance, subscription state.
- No client-side aggregation for authoritative totals.

### Queue board

Three columns: Waiting, In chair, Paid today. Internal authorized cards may show customer first name, token, services, barber, estimate, and action buttons. Public pages may not.

Actions:

```text
Waiting: start · reminder · no-show · cancel
In chair: checkout
Paid: receipt · refund policy entry
```

Realtime reconnect displays a sticky “Reconnecting—data may be stale” banner and refetches before clearing it.

### POS checkout

One focused workflow:

1. Services/quantities and authorized discount.
2. Tip.
3. Cash/card split payments and card references.
4. Backend-calculated review: gross, net, VAT, tip, barber commission, shop share.
5. Confirm exactly once; receipt/print result.

Inputs have visible labels and inline errors. A post-step cancel requires confirmation. Duplicate submission state is explicit.

### Cash and barber money

- Shift open/close shows opening float, cash sales/movements, expected, counted, variance.
- Advances show original/outstanding and next deduction policy.
- Payout review shows commission, tips, adjustments, advance deduction, net. Confirmation repeats barber, period, shop, and amount.
- There is no “manual deduct” shortcut; corrections are audited adjustments or payout applications.

### Suspension shell

Before tenant content renders, replace the application with:

```text
Service temporarily unavailable
Contact the platform administrator for assistance.
[Sign out]
```

No shop name, numbers, queues, billing amount, `paid_until`, cached navigation, or data flash. Platform admins use their separate app.

## 7. Public queue

Route uses an opaque token, not a human slug. Display:

```text
NOW SERVING: 05 · 06
UP NEXT: 07 ~13:55 · 08 ~14:10
```

Allowed: public shop display name, token, coarse status, chair/barber public label, estimate. Forbidden: customer name, phone, service, chat, money, internal IDs, or billing reason.

Dark-only, 16:9/4:3/mobile fit, cursor hidden on TV, subtle one-pixel burn-in shift, reconnect state. A suspended/archived/invalid token gets the same neutral unavailable page.

## 8. Platform modules

- Business list: owner, shop count, billing mode, effective status, due date, health.
- Business detail: consolidated context with nested shops.
- Cash receipt flow: scope, amount, reference, coverage, `paid_until`, collector, final immutable review.
- Suspend/resume: scope and affected shops, reason, typed confirmation, result audit ID.
- Export/offboarding: state timeline, checksum, expiring download, delivery confirmation, archive action disabled until prerequisite completes.
- Fleet: four bots per shop, filter/pagination, last healthy time, webhook state—never token/secret.
- Audit/security: actor, scope, action, time, request ID, redacted before/after.

Paid/active, due soon, expired, manual/security suspension, offboarding, and archived have distinct icon + label + color.

## 9. Components

Use shadcn/Radix primitives for accessible behavior and keep wrappers minimal:

```text
Button, Input, Select, Checkbox, Form, Dialog, AlertDialog, Sheet
Tabs, Table/DataTable, Badge, Tooltip, Toast, Skeleton, Command
ScopeSwitcher, SubscriptionBadge, QueueCard, QueueColumn, MoneyValue
ReceiptPreview, CashReconciliation, PayoutBreakdown, StatusRail
OfflineBanner, EmptyState, ErrorState, AuditDiff, ConfirmScopeDialog
```

Dialogs trap/focus correctly, restore focus, and have accessible titles/descriptions. Tables support keyboard use and responsive row/detail views. Virtualize only when measured row counts require it.

## 10. Copy

Use plain action language: “Record cash payment,” “Suspend business,” “Close cash shift,” “Pay barber.” The same verb appears in button, progress, success, and audit label. Errors state cause and recovery. Empty states invite the next permitted action.

Never expose implementation words such as webhook, RLS, ledger posting, or HTTP 423 to shop users.

## 11. Verification

- 375×812, 768×1024, 1024×768, 1440×900, and 1920×1080 without unintended horizontal scroll.
- Keyboard-only completion of login, shop switch, booking actions, checkout, cash receipt, suspend/resume, and export.
- Visible focus, semantic landmarks, form errors linked to inputs, live-region status, contrast ≥4.5:1 for body.
- Reduced motion; no hover-only action.
- Public/suspension pages pass privacy assertions and have no data flash.
- Raw hex usage limited to token primitives.
- No emoji icons, inaccessible icon-only controls, placeholder-only labels, or client-calculated authoritative money.
