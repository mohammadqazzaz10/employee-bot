# Employee Bot — development notes

## Runtime configuration
- Python 3.11+
- PostgreSQL via `DATABASE_URL`
- Telegram via `TELEGRAM_BOT_TOKEN`
- First super administrator via `SUPER_ADMIN_TELEGRAM_ID`
- Render Web Services use `RENDER_EXTERNAL_URL`; Background Workers use long polling.
- Local secrets belong in `.env`, which is ignored by Git. See `.env.example` and `DEPLOYMENT.md`.

## Database lifecycle
The application initializes the base tables on startup. Numbered SQL migrations 001–006 are applied separately with `python -m scripts.migrate` after a verified backup. The runner uses a PostgreSQL advisory lock, applies migrations in numeric order, and records SHA-256 checksums. Use `python -m scripts.migrate --check` to verify the migration ledger without changing the schema.

Do not edit an applied migration; add a new numbered migration. Migration 005 deliberately stops if duplicate active phone numbers or orphaned branch-settings rows are detected.

## Implemented on this development branch
- Registration-only Telegram contact linking, with atomic checks against account/phone reassignment.
- Database-backed super-admin and branch roles; authorization fails closed when PostgreSQL is unavailable.
- Branch-scoped employee onboarding/listing, request approvals, notifications, settings, custom request types, and broadcast recipients.
- Full employee profile fields: name, job title, phone, branch, Telegram link, and active state.
- Branch-specific smoking and lunch policy, including daily limits, durations, time windows, and minimum intervals.
- Audited configurable request buttons with per-branch durations and active state.
- Persisted countdowns, expiry alerts, exact timer-message return confirmation, and restart recovery.
- Durable timer-start outbox committed with approval/accounting; failed delivery is retried.
- Broadcast text/voice/audio, individual recipient selection, delivery logs, acknowledgements, and repeated reminders.
- CI workflow with Python compile checks and PostgreSQL 16 integration tests.

## Local checks
```bash
python -m pip install .
python -m compileall -q .
python -m unittest discover -s tests -v
```

## Production gates
- Latest GitHub Actions run must pass on the exact PR head SHA.
- Apply migrations 001–006 to the intended database only after a backup has been restored successfully in a separate test database.
- Verify existing employee phone uniqueness before migration 005.
- Exercise Telegram onboarding, approvals, timers, restart recovery, broadcast acknowledgements, and access revocation in staging.
- Rehearse backup/restore and review logs for delivery failures.
- Configure Render secrets and deploy only after explicit approval.

CI tests do not prove Telegram API delivery, notification sound behavior, or Render deployment. The PR remains a draft and is not production-approved.
