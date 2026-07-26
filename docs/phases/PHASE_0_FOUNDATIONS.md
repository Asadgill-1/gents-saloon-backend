# Phase 0 — Specification, Secrets, and Engineering Foundation

## Progress — 2026-07-25

- Complete: canonical production contracts and dashboard documentation sync.
- Complete: backend package, validated/redacted configuration, async PostgreSQL pool, Redis client, Celery app, `workers/health.py`, liveness/readiness routes, strict checks, tests, and `uv.lock`.
- Complete: development PostgreSQL/Redis Compose definition and SHA-pinned backend CI.
- Complete: both Next.js 16 repositories with strict TypeScript, Tailwind, Supabase SSR clients/proxy using verified claims, security headers, environment tests, lockfiles, and SHA-pinned CI.
- Verified locally: backend lock, Ruff, formatting, mypy, 9 tests, and dependency audit pass; each frontend passes clean `npm ci`, lint, TypeScript, 3 tests, production build, and a zero-vulnerability full npm audit.
- Verified live without Docker: PostgreSQL 17.10 and the separate authenticated Memurai instance bind only to localhost; FastAPI liveness/readiness return `200`; Redis loss makes readiness return `503` while PostgreSQL remains ready; `workers.health.ping` completes through the real Celery worker with `SUCCESS`.
- Verified live through the project-scoped Supabase MCP: no application tables, project migrations, Edge Functions, security advisor findings, or performance advisor findings exist before Phase 1.
- Verified locally: credential-pattern scans found no matches in current project files (excluding the intentionally untracked owner token scratch file) or any of the three Git histories.
- Ponytail ledger: no source-code `ponytail:` debt markers.
- Security audit: rerun and recorded in [../security-audits/PHASE_0_2026-07-25.md](../security-audits/PHASE_0_2026-07-25.md). The audit is not passed while token rotation and remote-CI/repository-control evidence remain open.
- Deferred security: Next.js DevTools MCP `0.4.0` currently introduces unresolved high-severity npm audit findings through its pinned MCP SDK. Do not install or activate it until an audited fixed release is available. A custom product MCP is unnecessary until an approved external AI client needs scoped platform access.
- Fixed security finding P0-SEC-010: replaced the vulnerable `eslint-config-next` convenience chain with Next's documented direct ESLint plugin flat config plus TypeScript ESLint and React Hooks rules.
- Pending owner: revoke/replace every Telegram token in `tokkens.txt`.
- Environment gate closed through the documented native Windows fallback. Docker Desktop still requires BIOS virtualization/WSL repair, but it no longer blocks local Phase 0 dependency proof.
- Pending remote: CI workflows execute after these changes are pushed.

## Outcome

The three repositories have one consistent production contract, safe configuration, reproducible skeletons, and CI. No business feature is built on the former single-shop authorization or advance model.

## Work

1. Merge the approved production revision into canonical master plan, requirements, data model, architecture, security, bot/AI/UI specifications, and phase files.
2. Ignore local secret scratch files. Owner rotates every token documented in [../SECRET_ROTATION_RUNBOOK.md](../SECRET_ROTATION_RUNBOOK.md); run secret scanning over tree and history.
3. Scaffold backend package, configuration validation, structured/redacted logging, async PostgreSQL pool, Supabase Auth/admin clients, Redis, Celery, health endpoints, pytest, lint/type checks, and Docker development services.
4. Scaffold both Next.js repositories with supported stable versions, strict TypeScript, linting, tests, Supabase SSR auth, safe headers, and environment validation.
5. Add CI for secret scan, dependency audit, format/lint/type, tests, frontend build, and SQL migration reconstruction. Pin CI actions by commit SHA.
6. Create development and staging configuration without production data or credentials.

## Gates

- Fresh clones install from lockfiles and start locally using only documented environment names.
- Missing/invalid production configuration fails at startup without revealing values.
- No secrets are present in tracked files or Git history; old Telegram tokens are confirmed revoked by the owner.
- Backend health distinguishes liveness from platform-admin readiness details.
- Both frontends authenticate through Supabase SSR and contain no backend secret.
- CI passes on all three repositories.
- Run the full phase security audit from [../security-audits/README.md](../security-audits/README.md), write the dated audit note, and leave zero unresolved Critical/High findings.
- Run `ponytail-debt` and record intentional deferrals.
