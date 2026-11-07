# Security and operational review

## Scope
This review is based on the repository's current `bot.py` and database initialization code. It is not a penetration test, and it does not establish that the deployed Render service is safe.

## Findings addressed on this development branch

1. **Contact onboarding persisted unapproved contacts.** Fixed: `handle_contact` now verifies the shared phone against the trusted employee allowlist before creating or linking an employee record. It also checks that the contact belongs to the sender.
2. **Approval callbacks lacked authorization.** Fixed: callback payload shape and values are validated; approve/reject actions require an administrator; the employee return button can only be used by its intended employee; missing employees are handled safely.
3. **Requests were not durable.** Fixed: smoke, lunch, leave and vacation handlers now write a pending row before notifying admins. Decisions use an atomic conditional update from `pending` to `approved` or `rejected`, so only one decision succeeds. The deciding Telegram ID and decision timestamps are recorded.
4. **Daily limits used the server calendar date.** Fixed: daily smoke and lunch queries now use the `Asia/Amman` local date.

## Remaining high-priority findings

1. **Phone authorization still has a source-code allowlist.** `authorized_phones` starts with a phone number embedded in source code, and employee phone numbers are loaded into it at startup. The add/remove helpers still mutate process memory only. Move authorization to database-managed invitations or a controlled environment configuration, and rotate any credentials that were ever committed to source.
2. **Timers are process-local.** `active_timers` and `timer_completed` are in-memory dictionaries and the timer schedules are created one job per second. Restarts lose timer state and frequent edits create unnecessary load.
3. **No enforced branch isolation exists in application code.** Employee and request queries are global. The branch migration creates schema and default-branch assignment triggers, but it does not yet scope application reads/writes or admin permissions.
4. **Request side effects are not yet one database transaction.** The request decision is recorded before actions such as incrementing smoke counts or starting a timer. A database or Telegram failure after the decision can require administrator reconciliation.
5. **Employee deletion cascades historical rows.** The current delete path can remove related request/history rows through foreign-key cascades. Prefer deactivation/soft deletion and retention rules before production use.

## Migration included in this branch

`migrations/001_multibranch_foundation.sql` is additive and idempotent for the objects it creates. It:
- creates a default branch and assigns existing employees to it;
- adds branch references and indexes;
- adds active employee and request decision metadata;
- creates branch-scoped admin role assignments;
- adds triggers so legacy employee inserts inherit the initial branch and request inserts inherit their employee's branch.

The migration is deliberately not run automatically. Review a database backup and row counts first. Existing admins remain global until application authorization is migrated. Requests with no linked employee retain a NULL branch and require manual review.

## Required implementation work before production

- Move phone authorization and all credentials to environment variables or database-managed invitations; rotate any credentials that were ever committed to source.
- Enforce branch authorization inside every command and callback, and scope every employee/request/admin query by branch; deny access by default and test cross-branch access explicitly.
- Replace per-second job creation with one persistent deadline per timer and recover active timers on startup.
- Make request decision and its business side effects transactional or use a reliable outbox/reconciliation mechanism.
- Replace employee hard deletion with deactivation and retention-safe history.
- Add PostgreSQL integration tests, migration backup/restore procedures, health checks, and structured logs.
- Configure Render environment variables and deploy only after review. No production settings were changed by this work.

## Current deployment safety
No Render service, environment variable, database, webhook, Telegram token, or production deployment was modified by this work.
