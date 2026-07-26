# Secret Rotation Runbook

> **Inherited Critical owner action blocking Phase 0 and Phase 1.** Completion must be recorded in [../START_HERE.md](../START_HERE.md), the [Phase 0 audit](security-audits/PHASE_0_2026-07-25.md), and the [Phase 1 audit](security-audits/PHASE_1_2026-07-26.md).

Use this runbook for the plaintext Telegram tokens currently stored locally in `tokkens.txt`. Never copy token values into Git, an issue, a screenshot, or an AI chat.

## Immediate owner action

For every bot listed in the local file:

1. Open Telegram `@BotFather` from the platform owner's secured account.
2. Revoke the current token and generate a replacement.
3. Put the master bot replacement in the backend deployment secret `MASTER_BOT_TOKEN`.
4. Put each shop-bot replacement through the authenticated onboarding/rotation flow once it exists; that flow must encrypt it before database storage.
5. Re-register the webhook with a newly generated secret-token header.
6. Run `getMe`, webhook health, and one private-chat authorization test.
7. Remove the plaintext local file after all replacements are verified, using a recoverable deletion method where available.

The repository now ignores `tokkens.txt` and `tokens.txt`, but ignore rules do not make existing credentials safe. Rotation is required because plaintext credentials have existed outside the password manager.

## Production rotation behavior

- Generate webhook secrets and encryption nonces using a cryptographically secure generator.
- Store deployment secrets only in the VPS/Vercel/Supabase secret controls or the owner's password manager.
- Rotate one bot at a time to limit outage.
- Audit who initiated and completed the rotation; never audit the secret value.
- On token-encryption key rotation, decrypt and re-encrypt each bot token inside backend memory in one controlled job; retain the old key only until verification completes.

## Verification

```text
old token fails Telegram API authentication
new token passes getMe
webhook secret mismatch returns 403
valid webhook is acknowledged
staff/customer behavior matches subscription and membership state
logs and database contain no plaintext token
secret scan of working tree and Git history is clean
```
