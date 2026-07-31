# Application Operations Runbook

Last updated: **2026-07-31, Asia/Dubai**

All operations use authenticated platform/backend workflows, require a reason where defined, and atomically create audit/outbox records. Never edit Supabase application rows directly to bypass a workflow.

## Bot onboarding and rotation

Create the correct master/shop role and scope, submit the token only through the backend-only registration/rotation flow, store the webhook-secret digest, register HTTPS, and verify private-chat authorization plus a synthetic update. During encryption-key rotation, retain the old key only for controlled in-memory rewrap until every envelope is verified. Follow `SECRET_ROTATION_RUNBOOK.md` for exposure.

## Suspension and resume

Require exact scope, typed confirmation, reason, and second visual confirmation. Verify APIs, dashboards, public page, and bots all return the generic unavailable state. Resume only after backend-proven coverage or a reasoned expiring override. Retain the audit/request ID.

## Cash receipt reversal

Never edit/delete a receipt. Verify immutable scope/amount/reference, require reason/confirmation, and create the linked mirror reversal. Entitlement changes are separate. Verify the e-invoice source credit-note envelope, audit, and outbox.

## Export and offboarding

Freeze exact scope, request the versioned export, wait for the private object/checksum, reauthorize the short-lived download, verify delivery, then confirm and archive. Never archive before delivery or expose object keys/signed links in logs.

## Worker, Beat, inbox, and outbox recovery

Prove Redis/database health, worker/Beat heartbeat, queue depth, oldest outbox age, dead letters, and Telegram status. Restart only the failed current-release container. Let bounded stale-claim logic reclaim durable jobs; never manually mark delivery/completion. Replay only through idempotent service/task entry points and verify Telegram acceptance precedes delivered state. Escalate with safe IDs, never raw messages or token-bearing URLs.

## Release operator checklist

Confirm environment, Git SHA/digest, migrations, Vercel IDs, PITR, alerts, approval, and rollback target. After deployment verify readiness, context, cross-tenant denial, booking replay, checkout replay without a second sale, worker/Beat, webhook acknowledgement, outbox delivery, and dashboards. Record safe IDs/results in the owner-controlled operations register.
