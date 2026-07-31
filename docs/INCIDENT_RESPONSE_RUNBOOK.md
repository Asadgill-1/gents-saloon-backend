# Incident Response Runbook

Last updated: **2026-07-31, Asia/Dubai**

## First response

1. Assign an incident commander, severity, UTC/Dubai start time, evidence owner, and communications owner.
2. Preserve request IDs, release manifest, audit rows, redacted logs, database/Storage/Grafana evidence, and deployment IDs. Never copy secrets, raw chat, authorization headers, receipt evidence, or customer PII into the incident channel.
3. Contain the smallest affected scope. Reserve platform-wide shutdown for platform-wide integrity or credential compromise.
4. Record every operator action/time. Never delete or rewrite financial, audit, receipt, export, inbox, or outbox evidence.
5. Recover using immutable rollback or isolated restore, verify tenant/money invariants, rotate affected credentials, and complete a blameless review with owned actions.

## Scenario controls

| Scenario | Immediate containment | Required verification before recovery |
|---|---|---|
| Credential leak | Revoke/rotate affected credential and disable affected integration | Search history/logs/artifacts, access window, replacement and old-key rejection |
| Cross-tenant exposure | Suspend affected scope, preserve evidence, block faulty endpoint/release | RLS/IDOR matrix, audit review, affected-subject and legal-notification decision |
| Financial corruption | Stop affected shop money mutations but preserve reads/evidence | Journal, receipts, cash, tender, commission, correction reconciliation |
| Telegram abuse | Disable registration/webhook and preserve safe inbox/outbox metadata | Secret rotation, role matrix, flood/budget, duplicate/retry behavior |
| AI abuse | Disable Moonshot while preserving buttons; block identity if authorized | Tool allowlist, server IDs, redaction, budget/timeout, authoritative templates |
| Database/Redis/worker outage | Stop unsafe mutations if entitlement/rate limit cannot fail closed | readiness, heartbeats, stale claims, outbox/dead letters, replay |
| Lost owner/admin device | Revoke sessions/device credentials using a second verified channel | MFA/account recovery audit and role/membership review |
| Backup/restore failure | Freeze destructive change and preserve all copies | PITR window, bundle checksum, restore-only access, isolated restore |

SEV-1 covers cross-tenant disclosure, active credential compromise, financial integrity loss, or production-wide outage and pages two responders immediately. SEV-2 covers material tenant outage, fleet/worker failure, backup alert, or checkout degradation without proven corruption. SEV-3 is contained with a safe workaround and no integrity/privacy impact.

Customer/regulator communication is owner/legal-controlled and states confirmed facts, affected period/scope, containment, and next update time without speculation.

Close only when service/alerts are healthy, audit/financial/tenant invariants pass, credentials are rotated, evidence is retained, notifications are decided, and every corrective action has an owner/date. A Critical/High action blocks release unless explicitly accepted with compensating control and expiry.
