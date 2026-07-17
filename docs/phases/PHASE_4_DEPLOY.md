# PHASE 4 — PRODUCTION DEPLOY & HARDENING

Goal: the system runs 24/7 on one VPS behind a domain with TLS, bots on webhooks, backups and monitoring in place, security audited, and a runbook the owner can actually follow. Minimum prerequisite: Phase 1 (bots-only launch is a valid go-live); full scope after Phase 3.

Owner supplies: VPS (Ubuntu LTS, 2 vCPU / 4 GB is enough to start), a domain, and DNS access. Supabase stays cloud. Frontend hosting: **decided (D13)** — both dashboards deploy on Vercel from their GitHub repos (`saloon-shop-dashboard`, `saloon-gents-system-owner-dashboard`); the VPS hosts backend only. Phase-4 frontend work: set prod env vars in both Vercel projects (`NEXT_PUBLIC_API_BASE_URL=https://<backend-domain>`), add both Vercel domains to backend CORS allowlist, update `PUBLIC_QUEUE_BASE_URL` to the shop-dashboard prod URL.

## T4.1 — Containers

`docker/Dockerfile.api` (uvicorn, non-root user), `docker/Dockerfile.worker` (same image, different command — worker and beat), `docker/compose.yml`:

| service | image/command | notes |
|---|---|---|
| api | Dockerfile.api, uvicorn :8000 | `env_file: .env`, restart unless-stopped, healthcheck `GET /health` |
| worker | Dockerfile.worker, `celery -A app.core.celery_app worker -l info` | prefork pool (Linux), concurrency 2 |
| beat | same image, `celery -A app.core.celery_app beat -l info` | single instance ONLY (duplicate beat = duplicate reports; the eod_reports latch protects money but not noise) |
| redis | `redis:7-alpine` | volume for AOF (`appendonly yes`) — FSM/session survival across restarts |
| caddy | `caddy:2` | ports 80/443, volume for certs |

`docker/Caddyfile`: domain → `reverse_proxy api:8000`; automatic HTTPS (Telegram requires valid TLS for webhooks).

Verify: `docker compose up -d` on the VPS → `curl https://<domain>/health` all ok; `docker compose ps` all healthy; reboot VPS → everything returns (restart policies).

## T4.2 — Cutover to webhooks

`ENV=prod`, `WEBHOOK_BASE_URL=https://<domain>`. Registry sets webhooks (per-bot secret path + `secret_token` header). Kill any dev polling. Confirm `getWebhookInfo` per bot: correct URL, 0 pending, no last-error.

Verify: customer message answered < 1 s in prod; `bot_health_check` green across the fleet; API logs show only legitimate webhook paths (random path probes → 403/404).

## T4.3 — Backups & data safety

- Supabase: confirm the project's automated daily backups are active (dashboard); document PITR availability on the current plan (**VERIFY AT BUILD TIME** — plan-dependent).
- Weekly logical dump as second line: cron on VPS `pg_dump` via Supabase connection string → encrypted, rotated 8 weeks, stored off-VPS (owner's choice: object storage or synced disk).
- `.env` + Fernet key: sealed copy in the owner's password manager — **losing FERNET_KEY = re-onboarding every bot token**; say so in the runbook in bold.
- Redis: AOF on; but by design (DATA_MODEL §6) Redis loss must cost nothing — chaos check below.

Verify: restore drill once — latest dump into a scratch Postgres, row counts match; `docker compose stop redis && rm -rf appendonlydir && up` → system degrades gracefully (FSM restarts, tokens re-INCR from DB max+1 guard, no money/booking loss).

## T4.4 — Monitoring & alerts

Lazy stack, no Prometheus (`# ponytail: metrics stack — add Grafana/Prom if >5 shops or perf questions appear`):
- The 1H `bot_health_check` already alerts the Master bot — extend the same task to check `/health` internals and alert on db/redis/celery failure.
- Uptime: external free pinger (UptimeRobot or equivalent) on `https://<domain>/health` → owner email/telegram.
- Logs: `docker compose logs` + logrotate; JSON logs already structured (T0.2).
- Celery: beat heartbeat task every 10 min writes a Redis key; api `/health` flags a stale heartbeat ("beat":"stale") so the pinger catches a dead scheduler.

Verify: stop worker container → Master bot alert + /health degraded within 10 min; stop beat → stale flag within 20 min.

## T4.5 — Security audit

- Run the `vibe-security-audit` skill (or equivalent manual checklist) across backend + frontend: secrets exposure, RLS coverage (re-run the Phase-3 isolation test against prod schema), injection surfaces (webhook path, slip number, wizard free-text), rate limiting, CORS (frontend origin only), security headers (Caddy defaults + explicit CSP for the app), dependency audit (`pip-audit`, `npm audit`).
- Telegram-specific: webhook secret rotation procedure tested; bot tokens nowhere in logs (grep the log volume); Fernet at rest confirmed.
- Fix criticals/highs; accepted risks written into the audit report file (`docs/SECURITY_AUDIT_<date>.md`).

Verify: audit report exists with zero unaccepted critical/high findings; token-in-logs grep clean.

## T4.6 — Runbook (`docs/RUNBOOK.md`)

Written for the platform owner, command-by-command: start/stop/restart; read logs; deploy an update (git pull → compose build → up); rotate a bot token; rotate webhook secrets; onboard a new shop SOP (BotFather steps with screenshots-level detail → dashboard wizard); backup restore; "bot not answering" triage tree; "queue stuck" triage; Supabase/Redis/VPS support contacts; the FERNET_KEY warning.

Verify: the owner (or a fresh AI session given only the runbook) executes restart + token rotation successfully without other docs.

## Phase 4 Definition of Done

MASTER_PLAN §7 Phase-4 list complete: compose up on VPS, webhooks < 1 s, TLS valid, backup drill done, monitoring alerts proven by induced failures, audit clean-or-accepted, runbook field-tested. Go-live.
