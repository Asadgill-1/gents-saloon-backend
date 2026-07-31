# Security — Binding Production Rules

Status: mandatory for all three repositories. A feature is not complete while its applicable checks fail. Never weaken RLS, authorization, validation, audit, or money integrity to make a test pass.

Current security decision and open findings: [security-audits/PHASE_1_2026-07-26.md](security-audits/PHASE_1_2026-07-26.md). Current implementation checkpoint: [../START_HERE.md](../START_HERE.md).

## S1. Secrets and credentials

- No secret, token, password, private connection string, production data, or credential export is committed to Git, copied into documentation, or printed in logs.
- `.env` files and known local scratch credential files remain ignored. Examples contain names and safe local placeholders only.
- `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`, bot tokens, Moonshot keys, encryption keys, and Redis credentials are backend-only.
- Frontend-public variables are restricted to Supabase URL/anon key and public API base URL.
- Bot tokens are encrypted at rest and decrypted only in backend memory.
- Webhook URLs contain only an opaque bot ID. Telegram’s `X-Telegram-Bot-Api-Secret-Token` header carries the secret; comparison is constant-time and attempted secrets are never logged.
- Secrets are stored in an owner-controlled password manager and deployment secret stores. Rotation procedures cover Telegram tokens/webhook secrets, database credentials, Supabase keys, Moonshot keys, and token-encryption rewrap.
- A credential discovered in a local plaintext file or chat is treated as exposed: rotate first, then investigate. Never ask the owner to paste it into chat.

Verify:

```text
secret scanner over working tree and Git history
frontend bundles contain no backend-only variable names or values
runtime logs contain no Telegram-token pattern, JWT, database URL, or Authorization header
```

## S2. Authentication, tenant authorization, and RLS

- Verify Supabase JWT signature using current JWKS plus issuer, audience, expiry, and not-before checks. Decoding without verification is banned.
- The JWT establishes identity, not current shop access. Resolve active business ownership and shop memberships from PostgreSQL on every request or a short cache invalidated by membership changes.
- A request’s `business_id`, `shop_id`, role, entity ID, or callback ID is only a locator. The server rechecks ownership/membership at execution time.
- Business owners can access all shops in their owned business. Staff access only explicitly assigned shops. Customers remain isolated per shop.
- Every tenant table enables and forces RLS, denies by default, and uses membership helper functions. `USING (true)` is prohibited.
- All tenant children use composite business/shop foreign keys so cross-shop references fail at the database layer.
- Browsers have RLS-backed read/Realtime access only. They have no direct mutation policies.
- Backend mutation code uses a server-only parameterized async PostgreSQL connection and repeats authorization inside each transaction.
- Supabase service role is limited to controlled Auth, Realtime, and administrative operations. It is never tenant authorization.
- Anonymous users have no table access. The only anonymous queue surface is a sanitized function/API projection returning token, coarse status, and estimate—never customer names or internal IDs.

Required test matrix for every table, RPC, and API:

```text
same shop staff
sibling shop staff (deny)
sibling shop business owner (allow as specified)
other business owner/staff (deny)
platform administrator (explicit allow)
anonymous (deny except public projection)
```

Verify:

```sql
SELECT tablename
FROM pg_tables
WHERE schemaname = 'public' AND rowsecurity = false;
-- zero application tables
```

## S3. Backend and API

- All trust-boundary inputs use explicit Pydantic schemas with length/range/enum constraints. Never spread a request body into an update.
- Client input never supplies a persisted role, tenant scope, calculated total, commission result, tax result, subscription status, or audit actor.
- SQL is parameterized. No `eval`, `exec`, unsafe deserialization, shell interpolation, or dynamic SQL from user content.
- The backend fetches only fixed allowlisted hosts. User-provided URLs are never fetched.
- Every list is cursor-paginated with a hard maximum. Webhook and request bodies have size limits.
- Production OpenAPI/docs and debug tracebacks are disabled. Responses use stable error codes and safe messages.
- CORS is an exact allowlist of the two production Vercel origins and explicit development origins; no wildcard or reflected origin.
- Authenticated mutation endpoints are rate-limited and require `Idempotency-Key`.
- A repeated key with the same request returns the prior result. Reuse with different payload returns conflict.
- Tenant APIs check effective subscription state before operation and again inside critical transactions. Inactive scope returns HTTP 423 with `subscription_suspended`.
- Health endpoints expose minimal status publicly; detailed dependency/bot/queue health is platform-admin only.
- Structured logs carry request ID and non-sensitive tenant/entity IDs. User text has control characters stripped or is stored only as structured data; PII and secrets are redacted.

## S4. Database correctness and concurrency

- PostgreSQL is authoritative for subscriptions, appointment holds, queue and receipt counters, money, advances, payouts, idempotency, and outbox state.
- Each business mutation uses one transaction: authorize, lock, validate, write domain state, write journal/audit/outbox, commit.
- Row/advisory locks and unique/exclusion constraints are correctness controls. Redis locks are optional contention controls.
- Appointment conflicts use a time-range exclusion constraint.
- Queue and receipt numbers use locked PostgreSQL counters.
- Completed financial documents and effective commission rules are immutable.
- Forward migrations are versioned and checksummed. Never edit an applied migration or rely on “run it twice.”
- CI reconstructs a clean database from all migrations. Staging tests forward migration against production-like data after a verified backup.
- Database functions with elevated privileges set a safe `search_path`, use qualified objects, validate arguments, and are owned by a non-login migration role.

Concurrency tests run two or more simultaneous calls against checkout, confirm, appointment hold, queue allocation, receipt allocation, advance application, payout, refund, and subscription mode changes. Exactly one permitted state change must result.

## S5. Telegram bots

- Staff bot middleware authenticates every update using bot → shop → Telegram identity → active owner/membership.
- Master bot authenticates against active `platform_admins`.
- Customer middleware order is: platform block → shop/customer block → flood limit → subscription gate → guardrail → flow.
- Bots operate in private chats and ignore groups/channels.
- Telegram `update_id` is deduplicated per bot for at least 24 hours.
- Callback IDs are versioned and treated as untrusted. Entity authorization is rechecked when executed.
- Links/media from users are never fetched. Escalation rendering is plain, non-clickable text.
- During suspension, valid updates are acknowledged but no business state or AI call occurs. Replies are generic and rate-limited.
- Notification delivery uses the transactional outbox and a unique dedupe key.

## S6. AI containment

- Moonshot may classify intent and invoke the exact allowlisted read/booking tools in [AI_SPEC.md](AI_SPEC.md). It has no money, staff, price-edit, subscription, cross-customer, SQL, filesystem, or network tool.
- Server code injects business/shop/customer context. The model cannot choose tenant/customer identifiers.
- Tool names and arguments are strict-schema validated; unknown calls fail closed.
- Model context contains the minimum current customer/shop data and no secrets, staff-private information, or other customer records.
- Customer and database text are untrusted prompt content. Prompt instructions inside them have no authority.
- Authoritative facts—price, availability, position, wait, booking ID, total—are rendered by application code from verified tool results.
- Model output is plain text, never executable commands, SQL, HTML, or URL input.
- Guardrails run before the model. AI rate, token, tool-round, timeout, and cost limits are mandatory.
- AI failure always leaves button-based booking and queue access available for active tenants.

## S7. Financial integrity

- All money calculations run server-side using `Decimal` and PostgreSQL `numeric`, with round-half-up to fils.
- Transaction item, discount, tax, payment, commission, tip, refund, and payout equations are protected by database checks and reconciliation tests.
- No card PAN, CVV, expiry, magnetic-stripe, or PIN data is collected. Card slip/reference is a merchant reference only. PAN-like text is redacted before chat persistence.
- Commission base excludes VAT and tips and follows the effective immutable rule snapshot. Tips are a separate 100% barber credit.
- Granting an advance creates cash out plus advance receivable. It does not reduce earnings.
- Advance deduction happens once inside payout and reduces outstanding by the same amount. Unique application constraints prevent double deduction.
- Journal entries/postings are append-only and balanced. Corrections are reversals, refunds, or credit notes.
- Checkout, void, refund, advance, payout, cash adjustment, tax setting, and commission rule actions are server-authorized and audited.
- Frontends display backend-calculated money and never persist their own calculations.
- Subscription cash receipts are append-only; corrections use one linked mirror reversal. Reversal never silently changes access; suspension/coverage removal is separately authorized and audited.
- Platform subscription e-invoice source envelopes are database-derived from those immutable receipts, append-only, and owner/platform read-only. Receipt reversal creates a linked credit-note envelope.
- B2C POS receipts cannot reference the platform B2B/B2G envelope. `prepared` means only that a provider-neutral source snapshot exists; it does not mean transmitted, accepted, delivered, accredited, or legally compliant.
- Provider credentials/payloads are absent from the current boundary. The safe source envelope is included in the versioned tenant export so offboarding does not omit billing evidence.

Required locked regression:

```text
service commission base AED 120
tier barber flat AED 25
shop share AED 95
tip credited separately 100% to barber
```

## S8. Frontend and browser

- Every protected page/layout/route verifies the session server-side. Middleware is navigation convenience, not the only authorization layer.
- Server Actions and route handlers are public endpoints and authenticate/authorize themselves.
- Supabase session cookies use supported SSR helpers and secure/httpOnly settings. Tokens never move to local/session storage.
- React escaping is preserved. No user-originated value enters `dangerouslySetInnerHTML`.
- Security headers include a restrictive CSP, `nosniff`, a strict referrer policy, frame restrictions, and HSTS at the production edge.
- Redirect parameters accept only validated relative application paths.
- No PII, auth token, or secret appears in URLs, analytics events, client logs, cache keys, error tools, or page metadata.
- The public queue URL uses an opaque high-entropy token. Its page shows no customer identity, money, billing reason, or internal IDs.
- Suspension/archive replaces the application shell before any tenant query result is rendered or cached.
- Vercel preview deployments use staging services, never production credentials.

## S9. Subscription, export, and offboarding safety

- Billing mode is mutually exclusive: business-wide or per-shop. Mode changes are atomic, platform-admin-only, and reject incomplete/incompatible subscription coverage.
- `paid_until` is inclusive through the shop’s Asia/Dubai day. Expiry evaluation is idempotent; critical operations also resolve entitlement transactionally.
- Manual/security/offboarding suspension overrides paid coverage.
- Non-payment resume requires valid coverage. Manual override requires a reason, actor, and expiry.
- Platform administration bypasses only the tenant entitlement gate, never authentication, authorization, audit, or input validation.
- Export jobs authorize scope at request and download time, encrypt output, record schema version and SHA-256, use short-lived signed downloads, and never place secrets in the archive.
- Offboarding freezes first, exports second, confirms delivery, revokes tenant sessions, disables bots/public pages, then archives.
- Hard tenant deletion is unsupported. Customer anonymization preserves financial references and respects legal hold/retention.
- Export/offboarding state transitions and download events are audited.

## S10. Privacy, retention, and observability

- Maintain a data inventory with purpose, fields, actor access, retention, export, and anonymization behavior.
- Collect only required customer data. Phone sharing remains optional.
- Chat content defaults to 90-day retention. Financial/tax/audit records default to at least seven years or the longer applicable legal period.
- Production logs avoid raw chat, names, phone numbers, receipt evidence, and export contents.
- Backup and export objects are encrypted in transit and at rest with least-privilege credentials.
- Metrics use low-cardinality identifiers and contain no PII.
- Incident runbook covers credential leak, cross-tenant exposure, financial corruption, Telegram abuse, AI abuse, unavailable dependencies, and lost device/account access.
- Suspected cross-tenant exposure: suspend the affected scope, preserve evidence, rotate relevant credentials, inspect audit/database logs, notify the owner, and document remediation.

## S11. Dependencies, CI, and delivery

- Verify every new package’s exact registry identity, official repository, maintenance status, and necessity before installation.
- Pin direct dependencies and commit lockfiles. CI installations are reproducible and avoid unreviewed install scripts where possible.
- CI actions are pinned to immutable commit SHAs.
- Required gates: format/lint, type check, unit, migration reconstruction, RLS/tenant matrix, integration/concurrency, secret scan, dependency audit, frontend build, Playwright, and accessibility smoke tests.
- Branch protection requires review and passing gates. Production deployment is from an immutable tested revision.
- Critical/high security findings block release unless the owner explicitly accepts a documented risk with compensating control and expiry.
- Before each commit, inspect the diff for secrets, new tables without RLS, endpoints without auth/ownership, money changes without reconciliation, and dependencies without verification.
- Never delete, skip, weaken, or hard-code around a failing test.
- Developer MCP connections are project-scoped and target development data. Read-only mode is the default once production data exists; full-write MCP access requires explicit owner approval and a development branch or otherwise isolated non-production project. MCP credentials never enter Git, chat, application runtime configuration, or customer-facing tools.

## S12. Production operations

- Redis is private to the Docker network, authenticated, and not published to the internet.
- Caddy enforces TLS, request/body limits, safe proxy headers, and appropriate timeouts.
- Workers use JSON serialization and receive IDs, not serialized application objects.
- Outbox retry uses bounded exponential backoff, dead-letter visibility, and idempotent delivery.
- Backup/PITR target is RPO ≤ 15 minutes. A timed restore drill must prove RTO ≤ 4 hours before launch and at a recurring interval.
- Alerts cover API error/latency, database availability/connections, Redis, worker heartbeat/queue age, outbox backlog/failures, bot health, webhook rejection spikes, subscription job failures, backup failure, and disk/certificate expiry.
- Production rollout uses staging, owner acceptance, a controlled pilot, monitored expansion, and a documented rollback.

## Verification record

Each phase completion note records:

- commands/checks run and real results;
- RLS advisor/security findings;
- new dependencies and verification;
- migrations applied and clean rebuild result;
- money/tenant/concurrency tests affected;
- unresolved risk, owner decision, and expiry;
- `ponytail-debt` results.

Legal/tax behavior must be rechecked against current official UAE sources before production launch; this document is an engineering control set, not legal advice.
