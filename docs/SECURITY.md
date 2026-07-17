# SECURITY.md — binding rules for every AI/human writing code in this project

**Status: MANDATORY. Not advice.** Every LLM session working in any of the 3 repos reads this before writing code. A change that violates a rule here is rejected regardless of who asked for it — convenience, speed, or "it's just a demo" never override. CLAUDE.md's "Never simplify away" list includes this entire file.

Sections: **S1** secrets · **S2** Supabase/RLS · **S3** backend · **S4** bots · **S5** AI layer · **S6** money · **S7** frontend/Next.js · **S8** supply chain · **S9** process · **S10** advanced attack classes (IDOR, races, escalation) · **S11** vibe-coding guardrails (AI behavior rules — the owner does not code).

Why this exists: 2025–2026 research shows AI-generated code ships vulnerabilities at extreme rates — XSS in ~86% of tested samples, log injection in ~88%, SSRF introduced by every major coding agent in one Dec-2025 study, ~62% of AI code with at least one flaw, 35+ CVEs from AI-generated code in March 2026 alone, 170+ Supabase apps breached via missing RLS (CVE-2025-48757), and ~19.7% of AI-recommended packages being hallucinated (slopsquatting). This project handles money, PII, and multi-tenant data. Every class below has burned real vibe-coded apps; each rule closes one.

Rule IDs (S1.1 …) are stable — reference them in PRs and reviews. `VERIFY:` lines are runnable checks; a rule without its verify green is not satisfied.

---

## S1 — Credentials & secrets

- **S1.1** No secret ever committed: no keys, tokens, passwords, connection strings in code, docs, tests, fixtures, examples, or commit history. Secrets live in `.env` (local) / Vercel project settings / VPS env only. `.env` stays in `.gitignore` forever.
  VERIFY: `git grep -iE "(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}" -- ':!docs/SECURITY.md'` → zero hits; run before every commit.
- **S1.2** `SUPABASE_SERVICE_ROLE_KEY` is backend-only. It never appears in any frontend repo, any `NEXT_PUBLIC_*` var, any client bundle, any log. (11% of vibe-coded apps leak Supabase keys — this is the #1 leak.)
  VERIFY: `grep -ri "service_role" saloon-*-dashboard/` → zero hits; `grep -r "SERVICE_ROLE" frontend build output` after Phase 2 builds → zero.
- **S1.3** Everything prefixed `NEXT_PUBLIC_` is public by definition. Only `SUPABASE_URL` + `ANON_KEY` + `API_BASE_URL` qualify. Anything else needing secrecy goes through the backend.
- **S1.4** Telegram bot tokens: Fernet-encrypted at rest (DATA_MODEL `bots.token_encrypted`), decrypted only in memory, never logged, never in error messages, never in webhook URLs beyond the per-bot secret path already specified.
  VERIFY: grep runtime logs for any 46-char `\d+:AA` bot-token pattern → zero (Phase 4 T4.5 already requires this).
- **S1.5** `FERNET_KEY` and `.env` master copy live in the owner's password manager. Losing FERNET_KEY = re-onboarding all bots (RUNBOOK warning stays).
- **S1.6** Key rotation procedure exists and is tested (Phase 4): bot token, webhook secret, Supabase keys, FERNET re-wrap. Any suspected leak → rotate first, investigate second.
- **S1.7** No secrets in URLs or query strings, ever (they land in logs, proxies, browser history).

## S2 — Supabase / database (the #1 vibe-coding breach class)

- **S2.1** RLS **enabled on every table, no exceptions** — including new tables added later. Supabase ships RLS **disabled by default**; 83% of Supabase exposures trace to this. A migration creating a table without `ENABLE ROW LEVEL SECURITY` + policies in the same file is an incomplete migration.
  VERIFY: `SELECT tablename FROM pg_tables WHERE schemaname='public' AND rowsecurity=false;` → zero rows. Run after **every** migration, automated in the test suite.
- **S2.2** Deny by default. No policy = no access. Policies grant the minimum from the DATA_MODEL §4 matrix; anon gets **nothing** except `EXECUTE` on `get_public_queue(slug)` — the single anon surface, returning first-name/token/status only.
  VERIFY: automated test with anon key: SELECT on every table → denied; RPC returns no phone/money/chat fields (assert on column names).
- **S2.3** Cross-tenant isolation is proven, not assumed: the Phase-3 mandatory test (shop-1 owner JWT reads shop-2 rows → zero) runs in every phase's suite from the moment web JWTs exist, and against prod schema in Phase 4.
- **S2.4** `shop_id` never comes from the client. Web: from JWT `app_metadata` (set server-side via Admin API only). Backend: explicit parameter threaded from authenticated context. Telegram: resolved from the bot registry, never from message content.
- **S2.5** Backend uses service-role (bypasses RLS) → every service function scopes by `shop_id` in code (MASTER_PLAN convention 1). New service function without a `shop_id` parameter (or platform-admin justification) fails review.
- **S2.6** Storage buckets (if ever added): private by default, signed URLs only, no public buckets without an owner decision logged in PROJECT_CONTEXT.
- **S2.7** Run Supabase advisors (`get_advisors` MCP / dashboard Security Advisor) after every migration batch; fix or explicitly accept each finding in the phase completion note.
- **S2.8** DB content is untrusted input when it reaches an LLM prompt or a rendered page (a hostile customer name is both a stored-XSS and a prompt-injection payload — see S5.4, S7.3).
- **S2.9** No fake policies. `USING (true)` or "any authenticated user" policies on tenant data are RLS theater — documented as the pattern behind CVE-2025-48757-class breaches (AI tools generate them to "make it work"). Every policy must express real ownership: shop match + role, per the DATA_MODEL §4 matrix.
  VERIFY: `grep -rn "USING (true)\|USING(true)" supabase/migrations/` → hits allowed only on genuinely public reference data (currently: none exists → zero hits).

## S3 — Backend (FastAPI / Celery / Redis)

- **S3.1** Validate at every trust boundary with pydantic: Telegram updates, callback data, web API bodies, webhook payloads, wizard free-text (amounts, slip numbers, names). Reject, don't sanitize-and-hope. Callback data uses the versioned codec — unknown/expired → safe "menu expired" reply.
- **S3.2** No dangerous primitives on user-influenced data, ever: no `eval`/`exec`, no `pickle` loads (AI models notoriously reach for pickle — RCE), no `os.system`/`subprocess` with interpolated strings, no dynamic SQL strings. DB access via supabase-py client / parameterized RPC only.
  VERIFY: `grep -rnE "eval\(|exec\(|pickle\.load|os\.system|shell=True" backend/app backend/workers` → zero (allowlist in tests only if justified).
- **S3.3** SSRF guard: the backend never fetches a URL that originated from user input. The only outbound calls are Telegram API, Moonshot API, Supabase — fixed, config-defined hosts. Adding any user-influenced fetch requires an allowlist + IP-literal/redirect blocking and an owner decision. (Every major coding agent introduced SSRF in the Dec-2025 study — treat any new `httpx.get(variable_url)` as a red flag.)
  VERIFY: `grep -rn "httpx\.\|requests\." backend/ | grep -v "moonshot\|telegram\|supabase\|test"` → review every hit.
- **S3.4** Log injection: never log raw user text with control characters — strip `\r\n` from any user-supplied string that enters a log line (88% of AI samples fail this). Structured JSON logging (T0.2) largely solves it; keep it that way.
- **S3.5** Errors: users (Telegram or web) get safe generic messages; stack traces and internals go to logs only. FastAPI debug mode off outside `ENV=dev`.
- **S3.6** Rate limiting is load-bearing security, not polish: flood guard + AI budget (DATA_MODEL §6 `rl:*` keys) ship in Phase 1, not later. Web mutation endpoints get per-user rate limits in Phase 2.
- **S3.7** Webhook auth: per-bot secret path + `X-Telegram-Bot-Api-Secret-Token` header compared constant-time (`hmac.compare_digest`). Wrong secret → 403, no detail, no logging of the attempted secret.
- **S3.8** Celery: tasks take IDs, never rich objects (no pickle serialization — JSON serializer enforced in `celery_app.py`); every task re-validates preconditions from DB (idempotency = also a security property against replay).
- **S3.9** CORS: exact-origin allowlist (the two Vercel domains + localhost dev). Never `*`, never reflecting the request Origin.
- **S3.10** Mass assignment: every write endpoint uses an explicit pydantic DTO listing exactly the client-settable fields. Never spread a request body into a DB update, never accept `role`, `shop_id`, `is_blocked`, `price`, `status`, `amount` from a client unless that endpoint's contract explicitly owns that field. AI code loves `Model(**payload)` — that pattern is banned on trust-boundary input.
- **S3.11** JWT handling: always verify signature + expiry + audience with the library's verifying API. `decode(..., options={"verify_signature": False})`, accepting `alg: none`, or trusting an unverified decode "just to read the user id" — banned. One shared `verify_supabase_jwt()` helper; no endpoint rolls its own.
- **S3.12** DoS caps: every list endpoint has a hard `limit` ceiling (≤100) and pagination; every free-text input has a max length (names 100, notes 500, chat 4096); webhook body size capped by server config. Unbounded queries and unbounded strings are bugs.
- **S3.13** FastAPI `/docs`, `/redoc`, `/openapi.json` disabled when `ENV=prod` (`docs_url=None`) — API surface is not advertised to attackers.
- **S3.14** Randomness for anything security-relevant (webhook secrets, invite links, temp codes) comes from `secrets` module — never `random`, never timestamps, never uuid1.
- **S3.15** Redis is never exposed: binds to the internal Docker network only, no published port in prod compose, password set. An open Redis = full FSM/session/lock takeover.
  VERIFY: `docker compose config` shows no `ports:` on redis; `redis-cli -h <vps-ip>` from outside times out.
- **S3.16** Replay defense: Telegram `update_id` deduped per bot (Redis SETNX, 24h TTL) — a replayed webhook body must be a no-op even if the secret leaks.

## S4 — Telegram bots

- **S4.1** Staff bots (receptionist/barber/owner/master): auth middleware on **every** handler — telegram_user_id must match an active `staff`/`platform_admins` row for that bot's shop. Unknown users get silence (no "access denied" oracle).
- **S4.2** Customer bot middleware order is fixed and security-relevant: global block → shop block → flood limit → guardrail pre-filter → then logic (BOT_FLOWS preamble). Never reorder.
- **S4.3** Never trust IDs inside callback data alone — re-check the actor is still authorized for the entity at execution time (booking belongs to this shop, staff row still active).
- **S4.4** Media/links from customers are never fetched, opened, or forwarded to staff chats as live links — escalation cards show them as quoted text (defused: wrap in code formatting so Telegram doesn't linkify).
- **S4.5** Group-chat hardening: bots operate in private chats; ignore updates from groups/channels entirely (drop in middleware) — prevents privilege confusion via group membership.

## S5 — AI layer (prompt injection is OWASP LLM #1 and unsolved — strategy is containment)

- **S5.1** Least-privilege tools, by construction: the model gets exactly the 7 read/booking tools in AI_SPEC §5 — **no tool that moves money, edits staff, changes prices, reads other customers, or executes SQL.** Adding any tool = security review against this file first. A model that cannot call a dangerous tool cannot be injected into using it.
- **S5.2** Tenant/customer scoping injected server-side: `shop_id` + `customer_id` come from the update context; the model's arguments can never address another tenant or customer (already in AI_SPEC — this rule forbids ever "simplifying" it away).
- **S5.3** All tool arguments validated against strict schemas before dispatch; unknown tool names or malformed args → refuse + log, never "best effort".
- **S5.4** Everything in the prompt except our own system text is untrusted: customer messages AND database values (names, notes). A customer named `Ignore previous instructions…` must change nothing. Never put staff-only data, other customers' data, or secrets into the context window — nothing in context = nothing to exfiltrate.
- **S5.5** Model output is rendered as plain text to the customer — never executed, never interpreted as commands, never used to build URLs/SQL/shell, never echoed into staff bots unquoted.
- **S5.6** Guardrail pre-filter (AI_SPEC §4) runs BEFORE the model and cannot be disabled by conversation content. Escalations are the designed response to injection attempts — when in doubt, escalate + canned reply.
- **S5.7** Budget caps (per-user hourly, tool-round cap 3, 5s timeouts) are DoS/cost security — they ship with the AI layer, not after.
- **S5.8** If an AI dev-tool session (Claude/Cursor/etc.) has Supabase MCP or shell access while building: never run it against prod data with untrusted rows in context (documented Willison-class attack: hostile DB row instructs the agent to run SQL). Dev project for dev work; prod migrations reviewed by a human.

## S6 — Money & business-logic integrity

- **S6.1** All money math server-side, `Decimal`, `numeric(10,2)` — the frontend never computes a split, total, or balance for persistence (display echo only, from API responses).
- **S6.2** DB CHECKs are the last line: amounts `>= 0` (tips, prices, subtotal), `total = subtotal + tip`, card ⇒ slip present — already in DATA_MODEL; new money columns get equivalent CHECKs. Negative-quantity/negative-price attacks are a documented vibe-coding class.
- **S6.3** Ledger is append-only (DB trigger). Corrections = adjustment rows. Any code path that would UPDATE/DELETE ledger rows is a bug by definition.
- **S6.4** **Never store card numbers, CVV, expiry — anywhere, including logs and chat history.** `card_slip_number` is a paper-slip reference, not a PAN. If a customer messages a card number to the bot, it is not persisted in `chat_messages` (redact pattern `\d{12,19}` → `[redacted]` before insert).
- **S6.5** Privileged money ops (void, advance, commission-rule change) are role-gated server-side AND audited with actor id. UI hiding a button is not authorization.
- **S6.6** Reconciliation tests (Phase 1E/1F) are security controls: split sums exact, report totals match ledger — they catch both bugs and tampering.

## S7 — Frontend (Next.js dashboards, Vercel)

- **S7.1** Mutations go through the backend API with the user's Supabase JWT; direct client writes to Supabase are limited to nothing (reads + Realtime only, per PHASE_2). No business rule in TypeScript.
- **S7.2** JWT verified server-side per request (signature via Supabase JWKS, not just decoded); role/shop from `app_metadata` (server-set), never from client-editable `user_metadata`.
- **S7.3** XSS (86% of AI samples fail): no `dangerouslySetInnerHTML` with any user-originated string (names, chat transcripts, notes). React's default escaping + plain-text rendering everywhere; chat transcripts render as text nodes.
  VERIFY: `grep -rn "dangerouslySetInnerHTML" saloon-*-dashboard/` → zero (or each hit reviewed + static-content-only).
- **S7.4** Security headers on both apps (`next.config` headers): CSP (self + Supabase + backend origins; no `unsafe-eval`), `X-Frame-Options: DENY` (except `/q/[slug]` which may allow same-origin embedding for TV setups), `Referrer-Policy: strict-origin-when-cross-origin`, `X-Content-Type-Options: nosniff`.
- **S7.5** No PII in URLs: routes carry ids/slugs only; the public queue page shows first names + tokens, nothing else (enforced at the RPC — UI must not add fields).
- **S7.6** Auth pages: no user-enumeration oracles (login error = one generic message), Supabase default rate limits left on, password reset flows use Supabase's, never custom.
- **S7.7** Vercel: env vars set per-project in dashboard, preview deployments get dev/staging Supabase keys — never prod service keys (Vercel never holds service_role at all, per S1.2).
- **S7.8** **Middleware is routing, not auth** (CVE-2025-29927: the `x-middleware-subrequest` header let attackers skip Next.js middleware entirely — CVSS 9.1, all majors < 12.3.5/13.5.9/14.2.25/15.2.3). Rules: (a) always install a patched Next.js and keep it current (**VERIFY AT BUILD TIME**: `npm audit` clean on `next`); (b) even patched, every protected page/layout/route handler re-verifies the session server-side itself — middleware-only auth is banned. Vercel hosting mitigates this CVE, the rule still stands (defense in depth + future CVEs).
- **S7.9** Server Actions and `/api` route handlers are public HTTP endpoints — each one auth-checks + role-checks itself, first line, no exceptions. "Only our button calls it" is not a thing; attackers call endpoints directly.
- **S7.10** Client-side route guards are UX, never security. Hiding a nav link, redirecting in `useEffect`, or checking role in React state protects nothing — the server-side check (S7.2/S7.9) is the only real gate.
- **S7.11** Sessions live in httpOnly cookies via `@supabase/ssr` helpers only. Never move tokens into `localStorage`/`sessionStorage` (XSS-stealable), never hand-roll cookie code.
- **S7.12** No open redirects: any `?next=`/`?redirect=` param is validated against a relative-path allowlist (`^/[a-z]`), never a full URL.

## S8 — Dependencies & slopsquatting (supply chain)

- **S8.1** **Verify every new package before install** — AI-suggested names are hallucinated ~20% of the time and attackers register the recurring ones (slopsquatting; real malware confirmed in the wild, e.g. npm `unused-imports` impersonating `eslint-plugin-unused-imports`). Check on the registry page: exact name spelling, weekly downloads, repo link that matches, age > 6 months, maintainer plausibility. An LLM must do this check and say so in its output before adding any dependency.
- **S8.2** Never let an agent auto-install packages unreviewed. `requirements.txt` / `package.json` diffs are security-review surface. Lockfiles (`package-lock.json`) committed always; Python versions pinned exact.
- **S8.3** Stick to the boring core (already in the plan): fastapi, aiogram, celery, redis, supabase, openai, cryptography, next, tailwind, shadcn/ui, lucide, recharts, @supabase/ssr. Anything outside this list triggers S8.1 + the CLAUDE.md sizing ladder ("do we need it at all?").
- **S8.4** `pip-audit` + `npm audit` run at every phase gate (already Phase 4 T4.5; run lightweight at each phase end too). Criticals fixed before the phase closes.
- **S8.5** No install scripts trust: prefer `npm ci --ignore-scripts` in CI contexts; on dev machines, read what a new package's postinstall does if it has one.

## S9 — Process (how these rules stay alive)

- **S9.1** Pre-commit gate (every commit, every repo): S1.1 secret grep + `git status` eyeball for unexpected files. Pre-push for phase-closing commits: the phase's VERIFY lines from this file.
- **S9.2** Phase completion note lists: security VERIFYs run + results, advisors output (S2.7), audit deltas, new deps with their S8.1 verification one-liners.
- **S9.3** Full audit at Phase 4 (T4.5, `vibe-security-audit` skill or manual equivalent) — this file is the checklist baseline; findings resolved or accepted in writing in `docs/SECURITY_AUDIT_<date>.md`.
- **S9.4** Incident basics (pre-written so nobody improvises at 2am): suspected key leak → rotate (S1.6) → check audit_log + Supabase logs for abuse window → notify owner → post-mortem note in PROJECT_CONTEXT. Suspected data exposure → disable anon RPC + suspend affected shop first, investigate second.
- **S9.5** This file is canonical in `gents-saloon-backend/docs/SECURITY.md`; dashboard repos carry synced copies. Update canonical first, sync copies in the same change. New flaw classes get new rules with the date noted.

## S10 — Advanced attack classes (the ones "working" apps still fail)

- **S10.1** IDOR/BOLA (OWASP API #1): possessing an ID is not authorization. Every endpoint/tool/handler that takes an entity id re-checks ownership at execution time: booking→this shop, transaction→this shop, staff→this shop+active, customer→this shop. Sequential-looking or guessable IDs are irrelevant — we use UUIDs AND check ownership; UUID secrecy alone is banned as an auth mechanism.
  VERIFY: automated test per web endpoint: valid JWT of shop-1 + entity id of shop-2 → 403/404, never 200.
- **S10.2** Race conditions / TOCTOU on money and bookings: any read-then-write sequence that decides money or slot ownership runs inside one DB transaction plus the specified Redis lock (`lock:booking`, `lock:barber`), with the DB unique constraint as final referee. Known hot paths: double-checkout of one booking, double-confirm, two advances double-spending a balance, same appointment slot won twice, token collision. Every one has a concurrency pytest (two parallel calls → exactly one succeeds).
- **S10.3** File uploads (none exist today; rule pre-set for when logos/receipts arrive): allowlist extensions + MIME sniff (not filename trust), size cap, randomized stored name, private bucket + signed URLs, never serve from the upload path, never execute.
- **S10.4** Path traversal: no filesystem path is ever built from user input. Anything resembling `open(base + user_value)` is banned; static serving is the frontend's/Caddy's job.
- **S10.5** Privilege escalation via metadata: roles/permissions live only in server-set `app_metadata` (S7.2) and `staff.role` — endpoints never read role from request bodies, query params, or client-writable `user_metadata`. Role changes are owner/platform-admin actions, audited (S6.5).
- **S10.6** Idempotency as defense: money/booking mutations accept an idempotency key (booking id, txn id) so retries/replays (network flaps, double-taps, replayed webhooks S3.16) cannot double-charge or double-book — same guarantee the Celery latches give reports.
- **S10.7** Timing/enumeration oracles: secret compares constant-time (S3.7), login errors uniform (S7.6), staff-bot silence for strangers (S4.1), admin 404-not-403 (Phase 3) — never "helpful" errors that confirm a target exists.

## S11 — Vibe-coding guardrails (binding on every AI session; the owner does not code)

The owner builds this entire system through AI. These rules bind the AI's *behavior*, because research documents AI assistants doing each of these when stuck:

- **S11.1** **Never disable security to make something work.** When a query fails, documented AI behavior is suggesting: disable RLS, comment out the auth check, widen CORS to `*`, use service_role in the client, make the bucket public. All banned. Blocked by a security control → the control stays, fix the policy/query/flow, or stop and report the blocker to the owner in plain language.
- **S11.2** **Never game tests.** No deleting/skipping a failing test to go green, no weakening asserts, no hardcoding expected values into mocks (documented case: agent hardcoded $0.00 into a payment mock to pass). A failing security/money test means the CODE is wrong until proven otherwise. Removing any test requires the owner's explicit OK, stated in the commit message.
- **S11.3** **"Should work" is banned — run it and show it.** The owner cannot read code, so the proof is execution: every task ends with its VERIFY/pytest actually run and the real output shown. If verification cannot be run, the task is not done, say exactly that.
- **S11.4** **Secrets never travel through chat.** The AI never asks the owner to paste keys/tokens into the conversation; it instructs where to put them (`.env`, Vercel settings, password manager) and references names only. If the owner accidentally pastes a secret into chat: treat as leaked → rotate per S1.6.
- **S11.5** **Self-review the diff against this file before every commit.** One pass, checklist mindset: secrets (S1.1)? new table → RLS (S2.1)? new endpoint → auth+ownership (S7.9/S10.1)? new dep → S8.1 verification stated? touched money → tests still meaningful (S11.2)? Then commit, and name the relevant S-rules in the message when security-adjacent.
- **S11.6** **No "temporary" bypasses.** In vibe coding, temporary is permanent — nobody comes back. A bypass that would violate an S-rule doesn't get written even with a TODO on it. If genuinely stuck: stop, write the blocker into the phase note, tell the owner.
- **S11.7** **Refactors must not shed security.** AI refactors silently drop validation lines, auth decorators, and error handling. After any refactor, diff-check that every auth/validation/audit call present before is present after; the cross-tenant, guardrail, and money test suites are the tripwire — they run after every refactor touching their areas.
- **S11.8** **Plain-language security reporting.** Anything security-relevant (finding, risk accepted, rule bent, incident) is explained to the owner in non-technical language with the concrete consequence ("anyone on the internet could read every shop's revenue" — not "RLS policy gap on transactions").

---

## References (checked 2026-07-16)

- [CSA: AI-Generated CVE Surge (Mar 2026)](https://labs.cloudsecurityalliance.org/research/csa-research-note-ai-generated-code-vulnerability-surge-2026/) · [OX Security: 62% of AI code ships vulns](https://www.ox.security/blog/vibe-coding-security/) · [Infosecurity: researchers on AI-code vulns](https://www.infosecurity-magazine.com/news/ai-generated-code-vulnerabilities/)
- [CSA: Slopsquatting supply-chain note (Apr 2026)](https://labs.cloudsecurityalliance.org/research/csa-research-note-slopsquatting-ai-supply-chain-20260419-csa/) · [Snyk: slopsquatting mitigation](https://snyk.io/articles/slopsquatting-mitigation-strategies/) · [TechTimes: agents skip package verification (Jul 2026)](https://www.techtimes.com/articles/319457/20260701/ai-coding-agents-skip-package-verification-attackers-are-exploiting-it.htm)
- [VibeAppScanner: Supabase RLS & CVE-2025-48757 patterns](https://vibeappscanner.com/supabase-security) · [byteiota: 170+ apps exposed by missing RLS](https://byteiota.com/supabase-security-flaw-170-apps-exposed-by-missing-rls/) · [HN: 11% of vibe-coded apps leak Supabase keys](https://news.ycombinator.com/item?id=46662304)
- [Prompt injection 2026 — OWASP LLM #1 guide](https://www.kunalganglani.com/blog/prompt-injection-2026-owasp-llm-vulnerability) · [ecorpit: agent security, containment strategy](https://ecorpit.com/ai-agent-security-prompt-injection-guardrails-2026/) · [Red Dog: LLM attack map 2026](https://reddogsecurity.substack.com/p/llm-security-in-2026-a-complete-attack)
- [CVE-2025-29927 — Next.js middleware bypass: Datadog analysis](https://securitylabs.datadoghq.com/articles/nextjs-middleware-auth-bypass/) · [Vercel postmortem](https://vercel.com/blog/postmortem-on-next-js-middleware-bypass) · [NVD entry](https://nvd.nist.gov/vuln/detail/CVE-2025-29927)
- [Vibe-coding anti-patterns: 7 failure modes (RLS `USING (true)`, public buckets, service keys in bundles)](https://theweatherreport.ai/posts/vibe-coding-anti-patterns/) · [Augment: agents gaming tests instead of fixing code](https://www.augmentcode.com/guides/why-ai-coding-agents-fail-e2e-tests) · [OpenSSF: security-focused AI code-assistant instructions](https://best.openssf.org/Security-Focused-Guide-for-AI-Code-Assistant-Instructions.html)
