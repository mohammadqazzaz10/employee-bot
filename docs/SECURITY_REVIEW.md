# Security and operational review

## Scope and limitations

This review covers the source code and automated PostgreSQL integration tests on
`improvements/multibranch-foundation`. It is not a penetration test. It does not
verify a real Render service, production database, Telegram bot, webhook, or phone
notification behavior.

## Controls implemented on this development branch

1. **Employee onboarding:** a contact must be the sender's own Telegram contact and
   match an active pre-registered phone. Telegram linking is atomic, registration-only,
   and cannot move an already-linked account to another employee.
2. **Administrator authorization:** super-admin and explicit branch roles are read from
   PostgreSQL. Database errors fail closed. Branch permissions are checked again and
   locked inside employee creation, settings/type writes, and request decisions.
3. **Branch isolation:** employee listings, onboarding, request decisions, request
   notifications, configurable buttons, settings, and broadcast audiences are scoped
   to assigned branches. Only the super admin can target all branches.
4. **Request lifecycle:** requests are stored before notification. A conditional
   pending-to-decided update prevents duplicate decisions and records the deciding
   administrator and timestamps.
5. **Transactional smoking/lunch accounting:** policy validation and the daily
   cigarette/lunch accounting records commit or roll back with the request decision.
6. **Timer reliability:** approved timed requests create a timer-start outbox record in
   the same transaction as the decision. Timer state is stored in PostgreSQL, restored
   after restart, and pending timer-start messages are retried. Delivery is at-least-once;
   a crash between Telegram accepting a message and database persistence can create a
   duplicate start notification.
7. **Return confirmation:** only the employee can confirm their own timer, and the
   button is bound to the exact timer message/type so an old button cannot clear a
   newer timer.
8. **Broadcasts:** recipient selection is revalidated before delivery; delivery and
   acknowledgement states are stored per recipient; reminders resume after restart.
9. **Employee history:** employee removal is a soft deactivation. Historical requests
   and accounting rows remain.
10. **Phone normalization and uniqueness:** Jordanian local mobile numbers are
    canonicalized. Migration 005 adds a unique partial index for active employee
    phone numbers and stops if existing duplicates require manual reconciliation.
11. **Secrets:** tokens and database URLs are read from environment variables and
    excluded from repository files. No actual credentials are included in the repo.

## Database migration and CI

- The application initializes base tables on startup.
- Numbered migrations 001–006 add branches/roles, configurable request types,
  broadcast logs/acknowledgements, branch settings integrity, active-phone uniqueness,
  and the timer-start outbox.
- `scripts/migrate.py` applies migrations in order under a PostgreSQL advisory lock
  and records SHA-256 checksums in `schema_migrations`.
- GitHub Actions compiles the Python project and runs contract plus PostgreSQL
  integration tests against a temporary PostgreSQL 16 service.

CI integration tests do not establish that the migrations have been applied to the
intended Render database or that Telegram delivery succeeds.

## Remaining production gates

1. **No live environment validation yet:** migrations 001–006 have not been applied
   to the intended Render database, and no Render service or environment settings
   have been changed.
2. **Backup/restore rehearsal:** take a backup of the intended database, restore it to
   a separate database, and verify application reads before applying migrations.
3. **Telegram staging tests:** verify self-contact linking, wrong/duplicate phone
   rejection, branch-scoped approvals, concurrent approval behavior, timer start and
   expiry, restart recovery, broadcasts, acknowledgements, and delivery failures.
4. **At-least-once timer delivery:** staging should test the crash window between
   Telegram accepting a start message and the database committing the timer message
   ID. Duplicate notification is possible by design; a timed request must not be lost.
5. **Operational observability:** review Render logs and alerts for database
   connection failures, failed Telegram sends, retrying outbox records, and failed
   broadcast deliveries.
6. **Release control:** the pull request remains Draft. No merge or production
   deployment has been performed.

## Render and credentials

The runtime requires `TELEGRAM_BOT_TOKEN` and `DATABASE_URL`; the first super admin
is configured with `SUPER_ADMIN_TELEGRAM_ID`. Use Render's Environment settings and
keep all secret values out of GitHub and chat. See `DEPLOYMENT.md` for the staged
setup and migration procedure.

No Render service, environment variable, production database, webhook, or deployment
has been changed by this work.
