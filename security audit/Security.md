# SECURITY.md — Reusable Advanced Security Baseline

**Version 1.0 — 2026-07-18** (changelog at bottom). When you copy this into a project, record the version you copied so drift is detectable.

> **Portable security ruleset for full-stack projects built with AI ("vibe coding").** Drop into any project's `docs/`, fill the PROJECT CARD once, delete sections you don't use, keep the rest as binding rules. Written for a builder who ships through AI assistants and may not read code line-by-line — every rule states the *consequence*, carries a severity tag, and gives a runnable *VERIFY* or *MANUAL CHECK*.
>
> **Status: MANDATORY, not advice.** Any AI/human writing code reads this before touching the repo. A change that violates a rule is rejected regardless of who asked — "faster", "just a demo", "temporary" never override.
>
> Covers: secrets · databases & RLS · backend (Python/.py, Node) · auth/JWT · frontend (React/Next.js/Vue/any) · REST/GraphQL APIs · Telegram · WhatsApp Cloud API · AI/LLM APIs · Redis · Celery & Beat · advanced attack classes · vibe-coding behavior · supply chain · GitHub & CI/CD · deploy/ops · severity & fail-loop · payments · privacy/PII · mobile apps. Plus a measurable **GOAL**, enforcement **LOOPS**, and two ready-to-use enforcement files (`.pre-commit-config.yaml`, `security-ci.yml`).

**How to read a rule:** `**S<id> [severity]**` — severity is `[C]` Critical / `[H]` High / `[M]` Medium / `[L]` Low (defined in [S17](#s17--severity--the-fail-loop)). `VERIFY:` = runnable proof. `MANUAL CHECK:` = a human/AI must look and confirm (no one-line command exists). `VERIFY AT BUILD TIME` = don't trust this doc's version/model facts; re-check the vendor's live docs.

---

## ⚡ 60-SECOND CARD — the non-negotiable Criticals

If you read nothing else, these are the rules whose violation has actually breached real vibe-coded apps. All are ship-blockers.

1. **No secret in the repo or the client bundle** — keys server-side only; `.env` git-ignored; scanner on (S1.1–S1.3, S9.1).
2. **RLS ON for every table, with real ownership predicates** — never off, never `USING(true)` (S2.1–S2.5).
3. **Every endpoint/tool checks auth AND object-ownership server-side** — having an id ≠ permission (IDOR/BOLA) (S3.6, S6.1, S12.1).
4. **Never trust the client for money, price, role, or tenant** — recompute/derive server-side (S2.4, S3.5, S6.3).
5. **Verify JWTs fully; roles only from server-set claims** (S4.2–S4.3).
6. **No `eval`/`exec`/`pickle`/shell-injection; JSON serializer in Celery** (S3.2, S11.1).
7. **Redis never internet-exposed; broker secured** (S10.1, S11.2).
8. **Verify every inbound webhook signature** — Telegram secret token, WhatsApp/Stripe HMAC (S7.1, S8.1, S18.3).
9. **Prompt injection: contain — least-privilege tools, server-injected scope, output treated as untrusted** (S9.3–S9.5).
10. **The AI never disables a security control or games a test to "make it work"** (S13.1–S13.2).

---

## PROJECT CARD — fill once per project (resolves every `<placeholder>` in this file)

```
PROJECT:            <name>
SECURITY.md version: 1.0
STACK:              <e.g. FastAPI + Next.js + Supabase + Redis + Celery + Moonshot>
DB / RLS platform:  <e.g. Supabase Postgres>   client-side DB access? <yes/no>
ALLOWED OUTBOUND HOSTS (S3.3 SSRF allowlist):
                    <e.g. api.telegram.org, api.moonshot.ai, *.supabase.co>
PROD DOMAINS / ORIGINS (S5.4 CSP, S6.4 CORS):
                    <e.g. app.example.com, admin.example.com>
MESSAGING CHANNELS: <Telegram? WhatsApp? none>
PAYMENTS:           <provider or "none"> (S18 applies only if a provider)
HANDLES PERSONAL DATA (PII)? <yes/no> jurisdiction: <e.g. UAE PDPL / EU GDPR / none>  (S19)
MOBILE APP?         <React Native / Flutter / Expo / none>  (S20)
SECRETS STORE:      <.env local + host env + password manager / Vault / etc>
CONFIG FILE PATHS (for VERIFY greps): backend=<path>  frontend=<path>  celery=<path>
L4 RECURRING SCHEDULE (owner sets real dates/cron):
   weekly dep+CVE audit: <day>   monthly history secret-scan: <date>   quarterly rotate+restore: <months>
AUDIT NOTE FILE:     docs/SECURITY_AUDIT_<date>.md
```

Delete a whole S-section only if the CARD says that tech is absent (e.g. no WhatsApp → delete S8). Write the deletion in the audit note so a reviewer knows it was a decision, not an omission.

---

## GOAL — what "secure enough to ship" means (measurable)

Done when **all** hold — not when the file is read:

- **G1** Every applicable rule is satisfied (VERIFY green / MANUAL CHECK confirmed) or has a written, dated risk-acceptance in the audit note. No silent skips.
- **G2** Zero open **Critical** or **High** findings at launch (S17). Medium/Low may ship only with written acceptance + follow-up date.
- **G3** The pre-launch checklist is fully ticked, each tick backed by a VERIFY actually run with output shown (not "should be fine").
- **G4** A dated audit note (`SECURITY_AUDIT_<date>.md`) exists listing every VERIFY run + result, every accepted risk + why, every dependency added + its S14.1 check, and every S-section deleted + why.

Any of G1–G4 false → **not ready to ship**, regardless of feature-completeness. Say so plainly to the builder.

## LOOPS — when the checks run (not just once at the end)

Security decays; a one-time pass rots. Run these on their triggers:

- **L1 — Per task / per commit**: S13.7 diff self-review + secret scan (S1.1). New table→RLS, new endpoint→auth+ownership, new dep→S14.1, touched money→tests real. Automated by `.pre-commit-config.yaml`. ~2 min, catches the most.
- **L2 — Per feature / per PR**: run the VERIFYs for every S-section the feature touched; write results in the PR. Automated by `security-ci.yml`. A red VERIFY enters the fail-loop (S17), never waved through.
- **L3 — Per phase / milestone**: full checklist + `pip-audit`/`npm audit` + platform advisor (S2.7). Update the audit note.
- **L4 — Recurring, calendar-driven** (dates in the PROJECT CARD): weekly dep+CVE check on your named stack; monthly full-history secret scan; quarterly credential rotation (S1.6) + restore drill (S2.9). An AI session can't remember these — they live on a real calendar/cron.
- **L5 — Pre-launch**: the GOAL gate (G1–G4). Nothing ships until green.
- **L6 — On incident**: contain → rotate → assess blast radius → fix → post-mortem note → add a rule here so it can't recur.

**Fix-loop inside every loop:** red VERIFY → fix the code/config (never the test, never the control — S13.1/S13.2) → re-run the same VERIFY → still red after a genuine attempt? stop and report to the builder in plain language, do not proceed. Green → record in audit note, continue. Never advance past a red Critical/High.

---

## S0 — How to use this file

- **S0.1 [H]** Every AI session's **first action** on this project: read this file + the PROJECT CARD, then state in one line which S-sections the task touches and which VERIFYs will run (entry to loop L1). Violating an S-rule to save effort is never a valid shortcut.
- **S0.2 [M]** Self-review the diff against this file before every commit (the S13 checklist). Name the S-rules touched in the commit message when security-adjacent.
- **S0.3 [—]** Threat model in one line: assume the frontend, every request, every webhook, every DB row, and every LLM output is attacker-controlled; assume secrets leak once, so nothing depends on a single secret staying secret forever.
- **S0.4 [—]** Defense in depth: no single control trusted alone. RLS *and* server checks; guardrail *and* least-privilege tools; signature check *and* rate limit.

---

## S1 — Secrets & credentials (the #1 leak class)

- **S1.1 [C]** No secret ever committed — no keys, tokens, passwords, connection strings, private certs in code, docs, tests, fixtures, sample data, or commit history. Secrets live in `.env` (local, git-ignored), the host's secret store, or a secrets manager. `.env` is in `.gitignore` from commit #1.
  VERIFY: `git grep -nIE "(api[_-]?key|secret|token|passwd|password|bearer)['\"]?\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}"` → zero hits. Scanner on (gitleaks / GitHub push protection).
- **S1.2 [C]** Split by trust side. A server secret (DB service key, provider API key, signing secret) never reaches the client bundle, a public env var, or logs. A public value (public URL, publishable/anon key) is the only thing the frontend gets.
  VERIFY: after a frontend build, grep the output bundle for each server secret's name and value → zero.
- **S1.3 [C]** Public prefix = public forever. `NEXT_PUBLIC_*`, `VITE_*`, `REACT_APP_*`, `EXPO_PUBLIC_*`, `PUBLIC_*` are embedded in shipped JS. Only genuinely public values get these prefixes.
- **S1.4 [H]** Encrypt third-party tokens at rest when stored in your DB (e.g. per-tenant bot tokens): symmetric encryption (Fernet / libsodium / KMS envelope), decrypt only in memory, never log plaintext, never in a URL.
- **S1.5 [H]** Leak response — **rotate first, investigate second.** Assume compromised the moment it left its box. Master copies (root `.env`, encryption keys) in a password manager, not on disk alone.
- **S1.6 [M]** Rotation is a tested procedure: every credential class (API keys, DB keys, webhook secrets, bot tokens, encryption keys) has a written rotation step, exercised once before launch.
- **S1.7 [H]** Secrets never in URLs or query strings (they land in logs, proxies, browser history, Referer). Bodies and headers only.
- **S1.8 [M]** Least privilege per credential: minimum permission, per-environment and per-workload where the provider allows. No shared "god" key.

## S2 — Databases & Row-Level Security (the #1 vibe-coding *breach* class)

Applies to Supabase/Postgres, Firebase, any DB with client-side access. If only your backend touches the DB, S2.1–S2.3 become "enforce tenancy in every query" instead.

- **S2.1 [C]** **RLS ON for every table, no exceptions**, including tables added later. Many platforms ship RLS **disabled by default** — anyone with the public key then reads/writes everything. A migration creating a table without enabling row security + policies in the same migration is incomplete.
  VERIFY (Postgres/Supabase): `SELECT tablename FROM pg_tables WHERE schemaname='public' AND rowsecurity=false;` → zero rows, in the test suite after every migration.
- **S2.2 [C]** Deny by default. No policy = no access. Minimum per role. Public/anon access is nothing unless a row is *designed* public, and then only via a narrow view/RPC exposing non-sensitive columns.
  VERIFY: automated test with the anon key: SELECT every table → denied; any public endpoint returns only whitelisted columns (assert names — no emails, phones, money, secrets).
- **S2.3 [C]** **No fake policies.** `USING (true)` / "any logged-in user" / "authenticated" without an ownership predicate is RLS theater — the documented pattern behind multi-tenant breaches. Every policy expresses real ownership: `tenant_id = auth.tenant()` + role.
  VERIFY: `grep -rn "USING (true)\|USING(true)" migrations/` → hits only on genuinely public reference tables, listed explicitly.
- **S2.4 [C]** Tenant id never from the client — derive from verified session/JWT claim, authenticated bot/user context, or server session. Never from a request body, query param, or client-writable metadata.
- **S2.5 [C]** If the backend uses a key that bypasses RLS (service role), every data function still scopes by tenant id in code. A new data-access function without a tenant parameter (or explicit admin justification) fails review.
- **S2.6 [H]** Storage buckets/objects private by default. Signed time-limited URLs or a server proxy. No "make the bucket public" — documented breach path.
- **S2.7 [M]** Run the platform's security advisor/linter after every migration batch; fix or explicitly accept each finding in the change note.
- **S2.8 [H]** DB content is untrusted once it flows into an LLM prompt or a page — a hostile stored value (`<script>`, `ignore previous instructions`) is both stored-XSS and prompt injection. See S9.4, S5.1.
- **S2.9 [H]** Backups are security: automated backups on, restore drill performed once, backup storage encrypted + access-controlled. A rogue-delete/ransomware event without a tested restore is total loss.

## S3 — Backend (Python `.py`, Node, any server)

- **S3.1 [H]** Validate at every trust boundary with a schema (pydantic / zod / Joi): request bodies, query params, headers you rely on, webhook payloads, bot updates, uploaded metadata. Reject what doesn't fit.
- **S3.2 [C]** No dangerous primitives on user-influenced data: no `eval`/`exec`, no `pickle.loads`/`yaml.load(unsafe)`/Node `vm`, no `os.system`/`subprocess(shell=True)` with interpolation, no string-built SQL. Parameterized queries/ORM, `subprocess` arg lists, JSON deserializers.
  VERIFY: `grep -rnE "eval\(|exec\(|pickle\.load|yaml\.load\(|os\.system|shell=True|child_process\.exec\(" <backend-path>` → zero, or each hit justified.
- **S3.3 [H]** **SSRF guard.** The server never fetches a URL built from user input. Outbound goes to the PROJECT CARD host allowlist only. If user URLs are truly required: allowlist hosts, resolve + block private/link-local ranges, disable redirects, block cloud metadata (169.254.169.254).
  VERIFY: `grep -rnE "requests\.(get|post)|httpx\.|fetch\(|axios\." <backend-path> | grep -v "<ALLOWED-OUTBOUND-HOSTS>|test"` → review every hit.
- **S3.4 [C]** No injection in any interpreter: SQL (parameterize), OS commands (arg arrays), NoSQL (typed), LDAP, XPath, server templates (no user input), and log injection — strip `\r\n` from user strings before logging; structured JSON logs.
- **S3.5 [H]** Mass assignment banned. Every write uses an explicit DTO of exactly the client-settable fields. Never spread a request body into a DB update; never accept `role`/`tenant_id`/`is_admin`/`price`/`status`/`balance`/`verified` from a client unless the endpoint owns them. `Model(**payload)` / `Object.assign(entity, req.body)` on boundary input is banned.
- **S3.6 [C]** AuthN + AuthZ on every non-public endpoint, server-side, first line. Authentication (who) and authorization (allowed to do *this* to *this object*) are separate — both required. IDOR/BOLA: S6.1.
- **S3.7 [M]** Errors: users get generic messages; stack traces/queries/hostnames to logs only. Debug mode off outside local dev. Interactive API docs (`/docs`, `/redoc`, GraphQL introspection) off in production.
- **S3.8 [H]** Rate limiting is security: per-IP and per-identity limits on auth, write, and anything that costs money or sends messages. Ship with the feature.
- **S3.9 [M]** DoS caps: hard `limit` + pagination on every list; max length on every free-text field; max request/body/upload size at the server; query timeouts.
- **S3.10 [H]** Cryptographic randomness for security values (tokens, secrets, reset codes, session ids): `secrets` (Python) / `crypto.randomBytes` (Node). Never `random`, `Math.random`, timestamps, sequential ids.
- **S3.11 [H]** TLS in transit; HTTPS-only; HSTS on. No plaintext HTTP for anything carrying data or tokens.
- **S3.12 [M]** Constant-time compare for secrets/signatures/tokens (`hmac.compare_digest` / `crypto.timingSafeEqual`), never `==`.

## S4 — Authentication, sessions & JWT

- **S4.1 [H]** Don't hand-roll auth. Use the platform/library (Supabase Auth, Auth0, Clerk, framework session middleware). Custom crypto/session code is where breaches live.
- **S4.2 [C]** Verify JWTs fully — signature, expiry, audience, issuer — via the library's *verifying* API. Banned: `decode(..., verify_signature=False)`, `alg: none`, unverified decode "just to read the id". One shared verify helper.
  VERIFY: `grep -rnE "verify_signature\s*=\s*False|algorithms=\[['\"]none|jwt\.decode\((?![^)]*verify)" <backend-path>` → zero or each reviewed.
- **S4.3 [C]** Roles/permissions only from server-set claims (`app_metadata` / server session), never client-writable fields (`user_metadata`), request bodies, or query params.
- **S4.4 [H]** Sessions in `httpOnly` + `Secure` + `SameSite` cookies. Never store tokens in `localStorage`/`sessionStorage` (XSS-stealable).
  VERIFY: `grep -rnE "localStorage|sessionStorage" <frontend-path> | grep -iE "token|jwt|session|secret|key"` → zero.
- **S4.5 [H]** Password flows: length-based minimum, breached-password check if available, hash bcrypt/argon2 (never plaintext/reversible), provider rate limits on. Prefer OAuth/magic-link/passkeys.
- **S4.6 [M]** No enumeration oracles: login/reset/signup return one uniform message whether or not the account exists.
- **S4.7 [M]** MFA available for admin/privileged accounts; session timeout + revocation on logout and password change.

## S5 — Frontend (React, Next.js, Vue, Svelte, any SPA/SSR)

- **S5.1 [H]** XSS is the top AI-generated flaw. No raw-HTML injection with user/DB data: no `dangerouslySetInnerHTML`, `v-html`, `innerHTML`, `document.write` on anything a user or the DB influences. Render as text; if HTML is required, sanitize with DOMPurify + strict allowlist.
  VERIFY: `grep -rnE "dangerouslySetInnerHTML|v-html|\.innerHTML\s*=|document\.write" <frontend-path>` → zero or each hit reviewed as static content.
- **S5.2 [C]** Business logic and authorization never live only in the client. Price/totals/permissions/discounts recomputed + re-authorized server-side; the client value is display only. Hiding a button is not access control.
- **S5.3 [C]** Secrets and privileged calls go through your backend, never browser→third-party with a secret key. The browser holds only public values (S1.3).
- **S5.4 [M]** Security headers on every response: real CSP (no `unsafe-eval`; tight `script-src`, origins from the PROJECT CARD), `X-Frame-Options: DENY` (or CSP `frame-ancestors`) except pages meant to be embedded, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, HSTS.
- **S5.5 [M]** No PII/secrets in URLs, `localStorage`, analytics events, or client logs. IDs and slugs in URLs only.
- **S5.6 [M]** No open redirects: any `?next=`/`?redirect=`/`returnTo=` validated against a relative-path allowlist (`^/[A-Za-z0-9]`), never a full external URL.
- **S5.7 [M]** Browser dependencies are attack surface: no unvetted `<script src>` from random CDNs; Subresource Integrity for any external script; prefer self-hosted/bundled.

### S5a — Next.js specific (also read S5)

- **S5a.1 [C]** **Middleware is routing, not authorization.** CVE-2025-29927 let attackers skip Next.js middleware via a crafted `x-middleware-subrequest` header (CVSS 9.1; all majors before 12.3.5 / 13.5.9 / 14.2.25 / 15.2.3). (a) Run a patched Next.js, keep current (`VERIFY AT BUILD TIME`: `npm audit` on `next`); (b) even patched, every protected page/layout/route handler re-verifies the session itself — middleware-only auth banned.
- **S5a.2 [H]** Server Actions and Route Handlers (`/api/*`, `app/**/route.ts`) are public HTTP endpoints. Each authenticates + authorizes on its first line. "Only our UI calls it" is false.
- **S5a.3 [H]** Client route guards (`useEffect` redirects, hidden links, role in React state) are UX only; the server-side check is the sole real gate.
- **S5a.4 [H]** Keep server-only code server-only: `server-only` package, never import secret-bearing code into client components, no secrets across the RSC boundary (props/serialized payloads).

## S6 — REST / GraphQL / API design

- **S6.1 [C]** Object-level authorization on every request naming an object id (IDOR/BOLA — OWASP API #1): having an id ≠ permission. Re-check ownership at execution. UUIDs help but are not authorization.
  VERIFY: per endpoint, automated test — valid session of user A + object id of user B → 403/404, never 200.
- **S6.2 [H]** Function-level authorization: admin/privileged routes gated by role server-side, not by path obscurity.
- **S6.3 [H]** Never trust client fields for pricing, quantity, status, identity, totals — recompute/validate server-side. Reject negatives/out-of-range (negative price/quantity is a documented exploit).
- **S6.4 [H]** CORS: exact-origin allowlist (PROJECT CARD origins). Never `*` with credentials, never reflect the request Origin.
- **S6.5 [M]** GraphQL: introspection off in prod, cap query depth/complexity, rate-limit, authorize per resolver/field (not just at the root).
- **S6.6 [H]** Idempotency keys on money/booking/order mutations so retries/replays can't double-act.
- **S6.7 [H]** Webhooks you *expose*: verify provider signatures (S7/S8/S18), dedupe by event id, treat the body as untrusted.

## S7 — Telegram bots

- **S7.1 [H]** Webhook auth: set a secret token, verify `X-Telegram-Bot-Api-Secret-Token` on every update (constant-time), plus a hard-to-guess webhook path. Wrong secret → 403, no logging of the attempted value. (Polling: no public endpoint, still authorize senders.)
- **S7.2 [C]** Authorize the *sender* on every handler: map `telegram_user_id` to your users/roles table; unknown senders get silence (no "access denied" oracle). Never trust names/text for identity.
- **S7.3 [H]** Bot tokens encrypted at rest (S1.4), never logged, never in URLs beyond the secret path, one per bot, rotation tested.
- **S7.4 [H]** Callback/command data is untrusted: version + validate payloads; unknown/expired → safe "menu expired" reply, never a crash or blind action. Re-check authorization at execution time (S6.1).
- **S7.5 [M]** Never fetch/open/forward user links or media; show to staff as quoted, de-linkified text. Cap message size and per-sender rate.
- **S7.6 [M]** Ignore updates from groups/channels if the bot is private-chat only (drop in middleware) — avoids privilege confusion via group membership.
- **S7.7 [M]** Replay defense: dedupe by `update_id` per bot (short-TTL store) so a replayed body is a no-op even if the secret leaks.

## S8 — WhatsApp Business Cloud API (Meta)

- **S8.1 [C]** **Verify every webhook signature.** Meta signs each payload HMAC-SHA256 with your App Secret in `X-Hub-Signature-256`. Validate (constant-time) before processing; mismatch → 403. Skipping = accepting data from anyone who learns your URL.
  VERIFY: a request with a wrong signature → 403; correct signature → processed.
- **S8.2 [H]** Verify-token for the GET subscription handshake is a strong random secret, compared constant-time; echo `hub.challenge` only on match.
- **S8.3 [H]** Access tokens: dedicated System User, minimum permissions, stored in a secrets manager/encrypted (never plaintext committed); long-lived System-User tokens with scheduled rotation (quarterly). App Secret server-only.
- **S8.4 [H]** Inbound message content untrusted (injection/XSS/prompt-injection if it reaches a page/LLM — S5.1, S9). Validate sender numbers; dedupe by message id (Meta retries); return 200 fast, process async.
- **S8.5 [M]** Respect opt-in/consent + template rules; don't log full PII message bodies; same rate/cost controls as any outbound path.
- **S8.6 [L]** Optional hardening: mTLS so your server also verifies Meta's TLS certificate.

## S9 — AI / LLM APIs (OpenAI, Anthropic, Moonshot, Gemini, local models)

- **S9.1 [C]** The API key is a billable server secret: server-side only, behind your backend as a proxy — never in the browser, mobile app, or a public env var. Segment keys per environment/workload; set provider spend limits + alerts.
- **S9.2 [H]** Cost/abuse controls are security: per-user + global rate limits, max tokens per request, tool-call-round cap, timeouts, exponential backoff on 429. An unmetered LLM endpoint is a financial-DoS target.
- **S9.3 [C]** **Prompt injection is unsolved (OWASP LLM #1) — contain, don't "prevent".** Make a landed injection harmless:
  - **Least-privilege tools:** only the exact tools needed; none that move money, delete data, change permissions, read other users' data, or run SQL/shell.
  - **Server-injected scope:** tenant/user id for tool calls comes from trusted request context, never the model's arguments.
  - **Strict tool schemas:** validate every argument before dispatch; unknown tool/bad args → refuse + log.
- **S9.4 [H]** Everything except your own system prompt is untrusted: user messages **and** retrieved/DB content (RAG rows, names, notes). Never put secrets, other users' data, or privileged data in the context window. A stored `ignore previous instructions` must change nothing.
- **S9.5 [H]** Model output is untrusted: render as text, never execute, never build SQL/shell/HTML/URLs from it, never auto-run returned code, never echo into a privileged channel unquoted. High-impact proposed actions need a human confirm or a deterministic server check.
- **S9.6 [M]** Code-level guardrail/pre-filter *before* the model (links/media/known-bad patterns, flood); monitor outputs for data-leak patterns. `VERIFY AT BUILD TIME`: current model IDs, endpoints, tool-call format from provider docs — never hardcode a model name from memory.
- **S9.7 [H]** If an AI *dev tool* (Cursor/Claude/Copilot with DB or shell) is used while building: never point it at production data with untrusted rows in context (a hostile DB row can instruct the agent to run SQL). Dev DB for dev work; a human reviews prod migrations.

## S10 — Redis (cache, queue broker, sessions, locks)

- **S10.1 [C]** **Never expose Redis to the internet.** Bind to `127.0.0.1`/private network; no published port in prod; firewall to trusted sources. Unauthenticated internet-reachable Redis is a top pentest finding (full read/write, often RCE).
  VERIFY: from an external host `redis-cli -h <PROD-IP> ping` times out; `docker compose config` shows no public `ports:` on redis.
- **S10.2 [H]** Require auth (`requirepass`/ACL user) even on a private network; TLS if it crosses any untrusted hop. Rename/disable dangerous commands (`FLUSHALL`, `CONFIG`, `DEBUG`, `KEYS`) where the client set allows.
- **S10.3 [M]** Redis is disposable: everything in it (cache, locks, sessions, counters) reconstructible from your source-of-truth DB. A flush loses no money/durable state. AOF for availability, never for correctness.
- **S10.4 [L]** Namespaced keys with TTLs; no unbounded key growth; no secrets/PII in cache longer than needed.

## S11 — Celery workers & Celery Beat (task queues generally)

- **S11.1 [C]** **JSON serializer only — pickle is RCE.** `accept_content=['json']`, `task_serializer='json'`, `result_serializer='json'`. With pickle, anyone who can write to the broker runs arbitrary code on your workers. The single most important task-queue rule.
  VERIFY: `grep -rn "pickle\|accept_content\|task_serializer" <celery-config-path>` → serializer json, pickle absent.
- **S11.2 [C]** The broker is the crown jewels: secure Redis/RabbitMQ per S10 (private, authed, not internet-exposed). Broker access = task injection = code execution.
- **S11.3 [H]** Tasks take plain data (ids, primitives), never rich/pickled objects; each task re-validates preconditions from the DB before acting (idempotency = replay defense). Safe to run twice.
- **S11.4 [M]** Beat schedules only trusted, code-defined tasks; exactly one Beat instance (duplicate Beat = duplicated side effects — protect money/notifications with DB idempotency latches).
- **S11.5 [M]** Workers run as non-root, least privilege; task time limits + retry caps set. Don't log task payloads with secrets/PII.

## S12 — Advanced attack classes (the ones "working" apps still fail)

- **S12.1 [C]** IDOR/BOLA (see S6.1) — the single most common serious AI-code flaw. Ownership check on every id, every time, with an automated cross-user test.
- **S12.2 [H]** Race conditions / TOCTOU on money and scarce resources: read-then-write that decides money, stock, seats, or slot ownership runs in one transaction + a lock, with a DB unique constraint as final referee. Hot paths: double-checkout, double-spend, double-book, coupon reuse. Each gets a concurrency test (two parallel calls → exactly one wins).
- **S12.3 [H]** File uploads: allowlist by content sniff (not filename/extension), size cap, randomized stored name, private storage + signed URLs, never serve from the upload dir, never execute, strip metadata.
- **S12.4 [H]** Path traversal: never build a filesystem path from user input (`../../etc/passwd`). Static serving is the web server's/CDN's job; validate + canonicalize any unavoidable path use.
- **S12.5 [C]** Privilege escalation via metadata: roles only in server-set fields (S4.3); role changes are audited admin actions.
- **S12.6 [H]** SSRF (see S3.3) — call out again for any new outbound fetch on user input.
- **S12.7 [H]** Business-logic abuse: negative/overflow amounts, quantity 0 or huge, double-applied discount, skipped paid step, replayed request — model the money/state machine and test the illegal transitions.
- **S12.8 [M]** Enumeration/timing oracles: uniform errors (S4.6), constant-time compares (S3.12), silence for unknown bot senders (S7.2), admin routes 404 not 403.
- **S12.9 [H]** Audit trail: every privileged/money/state-changing action writes an append-only record (who, what, when, on what). Append-only (no update/delete) so it survives tampering — also incident forensics.

## S13 — Vibe-coding behavior rules (the builder does not read code)

Bind the **AI assistant's behavior** — research documents assistants doing each when stuck.

- **S13.1 [C]** **Never disable security to make something work.** The documented reflex when blocked: turn off RLS, comment out the auth check, widen CORS to `*`, put the service key in the client, make the bucket public, switch to pickle. All banned. Fix the policy/query/flow — or stop and explain the blocker.
- **S13.2 [C]** **Never game tests.** No deleting/skipping a failing test, weakening assertions, or hardcoding expected values into mocks (documented: an agent hardcoded `$0.00` into a payment mock). A failing security/money test = the *code* is wrong until proven otherwise. Removing a test needs the builder's explicit OK in the commit message.
  MANUAL CHECK: `git log --diff-filter=D --name-only` for deleted `test_*`/`*.test.*` in security/money areas; test count didn't silently drop.
- **S13.3 [H]** **"Should work" is not done — run it and show output.** The builder can't read code; proof is execution. Every task ends with its VERIFY/tests actually run and the real output shown. Can't run it → say so plainly.
- **S13.4 [C]** **Secrets never travel through chat.** Never ask the builder to paste keys/tokens into the conversation — say *where* to put them (`.env`, host settings, secrets manager), reference names only. Pasted by accident → treat as leaked, rotate (S1.5).
- **S13.5 [H]** **No "temporary" bypasses.** Temporary is permanent — nobody comes back. A bypass that would violate an S-rule isn't written even with a `TODO`. Stuck → stop, write the blocker down, ask.
- **S13.6 [H]** **Refactors must not shed security.** AI refactors silently drop validation lines, auth decorators, error handling. After any refactor, diff-check that every auth/validation/audit call present before is present after; the security test suites are the tripwire and must run.
- **S13.7 [H]** **Self-review the diff against this file before committing** (S0.2): secrets? new table → RLS? new endpoint → auth + ownership? new dep → verified (S14)? touched money → tests still real? Then commit.
- **S13.8 [M]** **Report security in plain language** with the concrete consequence: "anyone on the internet could read every customer's phone number" — not "missing RLS predicate on customers".

## S14 — Dependencies & supply chain (slopsquatting)

- **S14.1 [H]** **Verify every new package before install.** AI-suggested names are hallucinated ~1-in-5, and attackers pre-register the recurring fakes (slopsquatting; real malware confirmed). Before adding: check the official registry — exact name/spelling, real download counts, matching source repo, age, plausible maintainer. The AI must do this and state it before adding anything.
- **S14.2 [M]** No unreviewed auto-install. `requirements.txt`/`package.json` diffs are review surface. Commit lockfiles always; pin versions (exact for Python; lockfile for npm). `npm ci` over `npm install` in automation.
- **S14.3 [L]** Boring known core; a new dependency triggers S14.1 *and* "do we even need it?" (a few lines often beat a dependency).
- **S14.4 [M]** `pip-audit` / `npm audit` (or Snyk/Dependabot) at every milestone; fix criticals/highs before shipping. Dependabot/renovate + secret scanning + push protection on the repo.
- **S14.5 [M]** Wary of install scripts: prefer `--ignore-scripts` in CI; read a new package's postinstall if it has one.

## S15 — GitHub & CI/CD

- **S15.1 [C]** Never commit secrets (S1.1). Push protection + secret scanning on every repo; a blocked push = rotate-then-remove, not force-past.
- **S15.2 [H]** Least-privilege `GITHUB_TOKEN`: default read-only (`permissions: contents: read`), grant more per-job only where needed. Prefer OIDC (short-lived cloud creds) over long-lived cloud keys in secrets.
- **S15.3 [H]** **Pin third-party Actions to a full commit SHA**, not a tag/branch (`uses: owner/action@<40-char-sha>`) — tags are mutable; CI supply-chain attacks (e.g. `tj-actions/changed-files`) hit tag-pinned projects. Verify the SHA is the real repo, not a fork.
- **S15.4 [C]** **Never run untrusted PR code with secrets.** Avoid `pull_request_target`; if unavoidable, never check out + execute the PR's code, never expose secrets to it. Fork PRs run read-only by default — keep it.
- **S15.5 [H]** No untrusted interpolation into `run:` shell steps (`${{ github.event.issue.title }}` → command injection). Pass through `env:` and quote, or use an action input, never inline.
- **S15.6 [H]** Secrets in GitHub Actions secrets/environments, scoped per environment; prod deploys gated by required reviewers/environment protection. Never printed to logs or uploaded in artifacts; mask them.
- **S15.7 [M]** Branch protection on `main`: required review, required status checks, no force-push, signed commits where possible. Deploy platforms (Vercel/Netlify) get only the env vars they need, per environment; prod keys never in preview builds.

## S16 — Deployment, hosting & operations

- **S16.1 [H]** Prod config hardened: debug off, verbose errors off, API docs/introspection off (S3.7); separate prod/staging/dev credentials and databases.
- **S16.2 [H]** Only necessary ports to the internet (typically 80/443). App servers, Redis, DB, workers on a private network behind a reverse proxy. Valid TLS + auto-renew; HSTS.
  VERIFY: from outside, `nmap <PROD-IP>` (or `ss -tlnp` on host) shows only 80/443 public; DB/Redis/worker ports closed externally.
- **S16.3 [M]** Containers/services run non-root, least privilege, minimal base images; OS/runtime patched.
- **S16.4 [M]** Monitoring & alerting: health checks, uptime pinging, alerts on error spikes and worker/scheduler death; centralized structured logs (secrets-scrubbed) with retention.
- **S16.5 [H]** Backups automated + restore-tested (S2.9); an incident runbook exists (leak → rotate; breach → contain then investigate) before launch.
- **S16.6 [H]** Full security audit before go-live: this file is the checklist; findings resolved or accepted in writing in the dated audit note.

## S17 — Severity & the fail-loop

Not every finding is equal; treating them equal drowns the critical one. Triage every finding, then run the fix-loop.

- **S17.1 Severity bands** (the meaning of the `[C]/[H]/[M]/[L]` tag on every rule):
  - **[C] Critical** — anyone on the internet can read/change other tenants' data or money, run code on your server, or a live secret is exposed. Ship-blocker; fix now, before anything else.
  - **[H] High** — exploitable with a logged-in account or a little effort; serious data/money impact. Ship-blocker (G2).
  - **[M] Medium** — needs unusual conditions or limited impact. Ships only with written acceptance + follow-up date.
  - **[L] Low / hardening** — defense-in-depth gaps, low exposure. Track, fix opportunistically.
- **S17.2 Fail-loop (mandatory on any red VERIFY):** (1) it already has a severity from its tag; (2) fix the **code/config** — never the test, never by disabling the control (S13.1/S13.2); (3) re-run the *same* VERIFY; (4) still red after a genuine attempt → stop and report in plain language (S13.8), do not proceed; (5) green → record in the audit note, continue.
- **S17.3** A Critical/High halts current work — not queued for "later" (S13.5).
- **S17.4** Every finding, its severity, and its resolution (fixed / accepted-with-reason) lands in the dated audit note — the G4 deliverable.

## S18 — Payments & billing (Stripe / any provider) — *delete if PROJECT CARD says no payments*

- **S18.1 [C]** Secret key server-only (S1.2); the browser uses the publishable key + provider-hosted fields/Elements only. Never handle raw card numbers/CVV in your own inputs, logs, or DB — let the provider tokenize (keeps you out of PCI scope).
- **S18.2 [C]** Never trust a client-reported "payment succeeded" or a client-sent amount/price. The server sets the amount from its own catalog; payment truth comes from the provider **webhook** (verified signature) or a server-side retrieve — never a browser redirect/callback alone.
- **S18.3 [C]** Payment webhooks: verify the provider signature (constant-time), dedupe by event id (providers retry — S6.6), process idempotently, return 200 fast + handle async.
- **S18.4 [H]** Money math server-side in integer minor units (cents/fils) or Decimal — never floats. Reject negative/zero/overflow (S12.7). Reconcile your ledger against the provider periodically.
- **S18.5 [H]** Fulfillment (grant access, ship, credit balance) only after verified confirmation, exactly once per order (idempotency key) — guards double-fulfilment on retries and refund races.

## S19 — Privacy & personal data (PII) — *delete if PROJECT CARD says no PII*

Applies once you store any personal data (names, phones, emails, addresses, photos, location, IDs). Jurisdiction (UAE PDPL / EU GDPR / etc.) is in the PROJECT CARD.

- **S19.1 [H]** Data inventory: keep a short list of what personal fields you store, where, and why. You can't protect or delete what you haven't mapped.
- **S19.2 [H]** Data minimization: collect only what a feature needs; don't log full PII bodies (S8.5, S11.5); don't store what you can derive or drop. Least data = least breach impact.
- **S19.3 [H]** Retention + deletion: define how long each data class is kept and delete past it (a scheduled job). Support a user deletion/export request path (right to erasure/access) if your jurisdiction requires it.
- **S19.4 [H]** Access control on PII: same tenant/role gating as everything else (S2, S6.1); PII columns never in public views/RPCs (S2.2) or client logs/URLs (S5.5).
- **S19.5 [M]** Encryption: TLS in transit (S3.11); at-rest encryption for the DB/backups (most managed platforms provide it — confirm it's on); extra field-level encryption for the most sensitive fields (government IDs, card refs).
- **S19.6 [M]** Third parties: only share PII with processors you've vetted (analytics, LLM providers, messaging); don't send PII to an LLM context unless needed (S9.4); disable third-party analytics that exfiltrate PII by default.
- **S19.7 [M]** Consent + transparency: a privacy notice exists; consent captured where required (marketing messages, cookies); honor opt-out (S8.5).
- **S19.8 [H]** Breach plan: PII exposure triggers the S16.5 runbook + any legal notification duty for your jurisdiction (know the window before you need it).

## S20 — Mobile apps (React Native / Flutter / Expo) — *delete if PROJECT CARD says no mobile*

- **S20.1 [C]** The app bundle is decompilable — no secrets, API keys, or signing keys shipped in it. Anything secret lives server-side; the app talks to your backend (S1.2, S9.1).
- **S20.2 [H]** Tokens/credentials in the OS secure store (iOS Keychain / Android Keystore / `expo-secure-store` / `react-native-keychain`), never `AsyncStorage`/plain files/`localStorage` (readable on a rooted/jailbroken device).
- **S20.3 [H]** Deep links / universal links are untrusted input: validate every parameter, don't auto-execute actions or auth from a link without a server check (S5.6 open-redirect logic applies).
- **S20.4 [M]** Certificate handling: use platform TLS defaults; never disable cert validation "to make it work" in dev and ship it. Consider certificate pinning for high-value apps.
- **S20.5 [M]** Least platform permissions (camera, location, contacts) — request only what a feature needs, when it needs it (mirrors S1.8 / S19.2).
- **S20.6 [M]** Same backend rules apply: the mobile app is just another untrusted client — every API it calls enforces S3.6/S6.1 server-side. A mobile client is not more trusted than a browser.

---

## Pre-launch checklist (tick before shipping — each tick backed by a VERIFY run with output)

- [ ] Secret scan clean; push protection on; `.env` git-ignored; keys split client/server (S1)
- [ ] RLS on every table; no `USING(true)`; anon access proven empty; cross-tenant test green (S2)
- [ ] Input validated at every boundary; no eval/pickle/shell-injection; SSRF-safe; mass-assignment-safe (S3)
- [ ] JWT fully verified; roles from server claims; sessions in httpOnly cookies; no enumeration (S4)
- [ ] No `dangerouslySetInnerHTML`/`v-html` on user data; CSP + headers set; no client-only auth; Next.js patched (S5, S5a)
- [ ] IDOR/BOLA ownership checks + tests; CORS exact-origin; idempotency on money (S6, S12)
- [ ] Telegram: webhook secret + sender auth + replay dedupe (S7)
- [ ] WhatsApp: `X-Hub-Signature-256` verified; token in secrets manager; message-id dedupe (S8)
- [ ] LLM: key server-side + spend caps; least-privilege tools; server-injected scope; output untrusted (S9)
- [ ] Redis private + authed + not internet-exposed; disposable (S10)
- [ ] Celery: JSON serializer (no pickle); broker secured; tasks idempotent; one Beat (S11)
- [ ] Advanced classes: races, uploads, path traversal, business logic, audit trail (S12)
- [ ] Deps verified (no slopsquatting); lockfiles committed; audit clean (S14)
- [ ] CI: token least-priv; Actions pinned to SHA; no untrusted PR code with secrets; branch protection (S15)
- [ ] Prod hardened; ports minimal (80/443 only); TLS valid; monitoring + backups + restore drill + runbook (S16)
- [ ] Every finding triaged by severity; zero open Critical/High; fail-loop applied (S17)
- [ ] Payments: server sets amount; webhook signature verified; fulfilment idempotent; no raw cards (S18 — or deleted)
- [ ] Privacy: PII inventory + retention/deletion + access-gated + breach plan (S19 — or deleted)
- [ ] Mobile: no secrets in bundle; tokens in secure store; deep links validated (S20 — or deleted)
- [ ] Enforcement files installed: `.pre-commit-config.yaml` active + `security-ci.yml` in `.github/workflows/`
- [ ] **GOAL gate G1–G4 green**: all VERIFYs run with output; audit note written; L4 recurring loops scheduled

---

## Enforcement files (ship with this doc)

Two files in this folder make the loops run automatically instead of relying on memory:

- **`.pre-commit-config.yaml`** → copy to the repo root, run `pip install pre-commit && pre-commit install`. Runs the L1 loop (secret scan + the S1.1/S3.2/S4.4/S5.1 greps) on every `git commit`. A hit blocks the commit.
- **`security-ci.yml`** → copy to `.github/workflows/`. Runs the L2 loop on every push/PR: gitleaks, `pip-audit`/`npm audit`, and the VERIFY greps. Red = the PR is blocked.

Both are starting points — extend them with the project-specific VERIFYs (RLS check needs DB creds, so it lives in the app test suite, not CI grep). Tune the paths to the PROJECT CARD.

---

## References (checked 2026-07-18)

**AI-code vulnerability rates & vibe coding**
- [CSA: AI-generated CVE surge (2026)](https://labs.cloudsecurityalliance.org/research/csa-research-note-ai-generated-code-vulnerability-surge-2026/) · [OX Security: 62% of AI code ships vulns](https://www.ox.security/blog/vibe-coding-security/) · [Vibe-coding anti-patterns](https://theweatherreport.ai/posts/vibe-coding-anti-patterns/) · [Augment: agents gaming tests](https://www.augmentcode.com/guides/why-ai-coding-agents-fail-e2e-tests) · [OpenSSF: AI code-assistant security guide](https://best.openssf.org/Security-Focused-Guide-for-AI-Code-Assistant-Instructions.html)

**Supply chain / slopsquatting**
- [CSA: slopsquatting note](https://labs.cloudsecurityalliance.org/research/csa-research-note-slopsquatting-ai-supply-chain-20260419-csa/) · [Snyk: slopsquatting mitigation](https://snyk.io/articles/slopsquatting-mitigation-strategies/) · [Black Duck: hardcoded LLM API key detection](https://www.blackduck.com/blog/llm-api-key-security-hardcoded-secrets-detection.html)

**Database / RLS**
- [byteiota: 170+ apps exposed by missing RLS](https://byteiota.com/supabase-security-flaw-170-apps-exposed-by-missing-rls/) · [VibeAppScanner: Supabase RLS & CVE-2025-48757](https://vibeappscanner.com/supabase-security)

**LLM / prompt injection**
- [OWASP LLM01: Prompt Injection (2025)](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) · [PortSwigger: Web LLM attacks](https://portswigger.net/web-security/llm-attacks) · [Datadog: monitoring prompt injection](https://www.datadoghq.com/blog/monitor-llm-prompt-injection-attacks/)

**Next.js middleware bypass**
- [Datadog: CVE-2025-29927 analysis](https://securitylabs.datadoghq.com/articles/nextjs-middleware-auth-bypass/) · [Vercel postmortem](https://vercel.com/blog/postmortem-on-next-js-middleware-bypass) · [NVD: CVE-2025-29927](https://nvd.nist.gov/vuln/detail/CVE-2025-29927)

**Telegram / WhatsApp**
- [Meta: WhatsApp webhooks overview](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview) · [Hookdeck: WhatsApp webhook best practices](https://hookdeck.com/webhooks/platforms/guide-to-whatsapp-webhooks-features-and-best-practices) · [Chatarmin: WhatsApp webhooks security (2026)](https://chatarmin.com/en/blog/whatsapp-webhooks)

**Redis / Celery**
- [Celery: security guide](https://docs.celeryq.dev/en/stable/userguide/security.html) · [Celery pickle RCE advisory (GHSA-4mwh-mwv4-m252)](https://github.com/inducer/relate/security/advisories/GHSA-4mwh-mwv4-m252) · [Redis RCE 2026 survival guide](https://www.penligent.ai/hackinglabs/redis-rce-exposed-the-2026-survival-guide-for-security-engineers/)

**GitHub / CI-CD**
- [GitHub Docs: Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use) · [Wiz: hardening GitHub Actions](https://www.wiz.io/blog/github-actions-security-guide) · [StepSecurity: Actions best practices](https://www.stepsecurity.io/blog/github-actions-security-best-practices) · [GitHub Actions 2026 security roadmap](https://github.blog/news-insights/product-news/whats-coming-to-our-github-actions-2026-security-roadmap/)

**Prompt injection 2026 landscape**
- [Prompt injection 2026 — OWASP LLM #1 guide](https://www.kunalganglani.com/blog/prompt-injection-2026-owasp-llm-vulnerability) · [ecorpit: agent security containment](https://ecorpit.com/ai-agent-security-prompt-injection-guardrails-2026/)

---

## Changelog

- **1.0 — 2026-07-18** — First versioned release. Full ruleset S1–S18 (secrets, DB/RLS, backend, auth, frontend + Next.js, APIs, Telegram, WhatsApp, LLM, Redis, Celery, advanced classes, vibe-coding behavior, supply chain, CI/CD, deploy, severity/fail-loop, payments) plus GOAL, LOOPS, and the pre-launch checklist. **Added in this release:** per-rule severity tags `[C]/[H]/[M]/[L]`; 60-second Critical card; PROJECT CARD fill-in block (resolves all placeholders + L4 schedule); VERIFY/MANUAL-CHECK lines for S4.2/S4.4/S13.2/S16.2; S19 Privacy & PII; S20 Mobile apps; version header + this changelog; two enforcement files (`.pre-commit-config.yaml`, `security-ci.yml`).
