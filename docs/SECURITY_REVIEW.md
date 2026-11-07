# Security and operational review

## Scope
This review is based on the repository's current `bot.py` and database initialization code. It is not a penetration test, and it does not establish that the deployed Render service is safe.

## High-priority findings

1. **Authorization is not database-backed.** `authorized_phones` starts with a phone number embedded in source code, and employee phone numbers are loaded into it at startup. `add_employee_to_authorized` and `remove_employee_from_authorized` only change process memory. This creates inconsistent behavior after restarts and makes source control an unsafe place for access policy.
2. **Contact onboarding persists before authorization.** `handle_contact` calls `save_employee` before checking `verify_employee`. An unapproved contact can therefore be inserted as an employee record. The onboarding flow must check a trusted invitation/allowlist before creating or linking an account.
3. **Approval callbacks lack an authorization check.** `button_callback` processes approve/reject callback data without first verifying that the clicking Telegram user is an authorized administrator. Callback data is not proof of authority.
4. **Requests are sent to admins without being persisted.** The `requests` table exists, but the leave, vacation, smoke and lunch request handlers primarily send Telegram messages. A restart or message deletion can leave no durable request lifecycle.
5. **Timers are process-local.** `active_timers` and `timer_completed` are in-memory dictionaries and the timer schedules are created one job per second. Restarts lose timer state and frequent edits create unnecessary load.
6. **No enforced branch isolation exists in application code.** Employee and request queries are global. Adding a branch column alone does not make the application multi-tenant.

## Migration included in this branch

`migrations/001_multibranch_foundation.sql` is additive and idempotent for the objects it creates. It:
- creates a default branch and assigns existing employees to it;
- adds branch references and indexes;
- adds active employee and request decision metadata;
- creates branch-scoped admin role assignments.

The migration is deliberately not run automatically. Review a database backup and row counts first. Existing admins remain global until application authorization is migrated. Requests with no linked employee retain a NULL branch and require manual review.

## Required implementation work before production

- Move phone authorization and all credentials to environment variables or database-managed invitations; rotate any credentials that were ever committed to source.
- Validate a shared contact belongs to the sender, then verify an active invitation before inserting/linking an employee.
- Enforce admin authorization inside every callback and command, and bind each pending request to its branch and intended action.
- Persist every request and its state transitions in PostgreSQL; use atomic conditional updates to prevent double approval.
- Replace per-second job creation with one persistent deadline per timer and recover active timers on startup.
- Scope every employee/request/admin query by branch; deny access by default and test cross-branch access explicitly.
- Add migration backup/restore procedures, health checks, structured logs, and automated tests.
- Configure Render environment variables and deployment only after review. No production settings were changed by this work.

## Current deployment safety
No Render service, environment variable, database, webhook, Telegram token, or production deployment was modified.
