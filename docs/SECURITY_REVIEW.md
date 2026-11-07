# Security and operational review

## Scope
This review is based on the repository source and its database initialization code. It is not a penetration test and does not establish that a deployed Render service or database is configured correctly.

## Findings addressed on this development branch

1. **Contact onboarding could persist unapproved contacts.** Fixed: `handle_contact` verifies the shared phone against an active employee record in PostgreSQL before linking the Telegram account, and checks that the contact belongs to the sender.
2. **Approval callbacks lacked authorization.** Fixed: callback payloads are validated; approve/reject actions require a database-backed administrator; return confirmation is restricted to the intended employee.
3. **Requests were not durable.** Fixed: smoke, lunch, leave and vacation handlers persist a pending row before admin notification. Decisions use a conditional update from `pending`, and record the deciding admin and timestamps.
4. **Daily limits used the server calendar date.** Fixed: daily smoke and lunch queries use the `Asia/Amman` local date.
5. **Administrator and employee phone IDs were embedded in source.** Fixed: hard-coded IDs/phone allowlist were removed. The first super administrator is provisioned using `SUPER_ADMIN_TELEGRAM_ID`; employee phone authorization is checked against active database records. Admin checks fail closed when PostgreSQL is unavailable.
6. **Employee deletion removed historical rows through cascading foreign keys.** Fixed in the command path: employee removal now sets `is_active = FALSE` and retains associated history. Existing inactive employees are excluded from login and active listings.
7. **Phone number formats could differ between local and international Jordanian forms.** Fixed: local `07XXXXXXXX` numbers are normalized to `9627XXXXXXXX`, and existing database values are normalized during initialization.

## Remaining high-priority findings

1. **Application-level branch isolation is not implemented yet.** Employee and request queries are still global, and existing admins are global. The branch migration creates schema and roles but does not yet scope every command, callback, or query. Do not operate multiple branches until this is complete and tested.
2. **Timers are process-local.** `active_timers` and `timer_completed` are in-memory dictionaries; jobs are scheduled every second. Restarts lose timer state and frequent edits create unnecessary load.
3. **Request decisions and business side effects are not one transaction.** The request is marked approved before incrementing counters, marking lunch, or starting a timer. A later database or Telegram failure can require reconciliation.
4. **Database connection lifecycle needs further hardening.** Some older helper functions close connections only on the success path. A follow-up refactor should consistently use context managers and add retry/timeout policy.
5. **Database migration has not been applied or exercised against a live PostgreSQL instance in this work.** It is syntax-checked by contract tests only; a real backup/restore rehearsal and integration tests are still required.

## Database and deployment setup

- The app requires `TELEGRAM_BOT_TOKEN` and `DATABASE_URL`.
- It creates/upgrades base tables at startup: `employees`, `requests`, `daily_cigarettes`, `admins`, `lunch_breaks`, `cigarette_times`, and `absences`.
- The first super admin is inserted into `admins` from `SUPER_ADMIN_TELEGRAM_ID`.
- Render Web Services use `RENDER_EXTERNAL_URL` as the webhook base URL. Background Workers use polling without a public URL.
- Local secrets belong in an untracked `.env`; `.env.example` contains placeholders only.

See `DEPLOYMENT.md` for the exact Render and first-admin setup.

## Multi-branch migration

`migrations/001_multibranch_foundation.sql` creates the initial branch, adds employee/request branch references and indexes, creates branch-role assignments, and adds default-branch assignment triggers. It expects the base tables (including `admins`) to exist, so start the app once against the intended database before applying it.

The migration is deliberately not run automatically. Back up and verify restore first. Requests without a linked employee retain a NULL branch and require review.

## Production release gates

- Implement and test branch-scoped authorization and queries for every employee, request, and admin operation.
- Replace in-memory timers with persistent deadlines and restart recovery.
- Make approval side effects transactional or use a reliable outbox/reconciliation mechanism.
- Add PostgreSQL integration tests, migration backup/restore procedures, and operational health checks.
- Verify Render environment variables and deploy only after review.

No Render service, environment variable, production database, webhook, or deployment was changed by this work.
