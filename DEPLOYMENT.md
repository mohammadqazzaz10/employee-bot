# Deployment and first-run setup

The repository now includes a Render Blueprint at `render.yaml`. It provisions a Telegram **Background Worker** and a managed PostgreSQL database in Frankfurt. The worker uses Telegram long polling; it does not need a public web URL or webhook secret.

## Recommended: deploy with the Render Blueprint

1. Confirm the GitHub repository is up to date on the `main` branch.
2. In Render, choose **New → Blueprint** and connect `mohammadqazzaz10/employee-bot`.
3. Select the repository's `main` branch and let Render read the root `render.yaml`.
4. Review the two resources before confirming: `employee-bot` (Background Worker) and `employee-bot-db` (PostgreSQL). The Blueprint uses paid plans because Render's pre-deploy migration command is not available on free workers.
5. When prompted for environment values, enter:
   - `TELEGRAM_BOT_TOKEN`: the token from Telegram BotFather.
   - `SUPER_ADMIN_TELEGRAM_ID`: your numeric Telegram account ID. You can obtain it from a trusted Telegram ID utility, or leave it unset for the first start and then send `/my_id` to the bot and add the returned number in Render.
6. Keep the generated `DATABASE_URL` reference unchanged and keep `DATABASE_SSLMODE=prefer`. Do not add `WEBHOOK_URL`, `WEBHOOK_SECRET`, or a manual `PORT`; the Blueprint intentionally uses polling.
7. Confirm the Blueprint. Render builds with the pinned Python version, installs the project, runs `python -m scripts.bootstrap` before starting, and then runs `python bot.py`.
8. The pre-deploy bootstrap initializes the base tables, applies migrations 001–006 in order, and checks that every migration is recorded with the matching SHA-256 checksum. If any step fails, Render must not proceed to the new worker release.
9. After the worker starts, open its Logs and verify the startup messages show successful database initialization, migration verification, and Telegram polling. Then message the bot with `/start` and verify the admin account with `/list_admins`.

**Important database safety:** this Blueprint creates a new managed database for a new deployment. Do not point it at an existing database containing employee records until you have made a backup, verified a restore, reviewed duplicate active phone numbers/orphaned branch settings, and deliberately approved the migration run. The migration runner stops on checksum mismatches and integrity violations; it does not silently choose records to delete.

## Render resources and runtime configuration

- Service type: Background Worker (one instance; do not scale to multiple instances because each instance would run its own Telegram polling loop).
- Region: Frankfurt for both worker and PostgreSQL.
- Build command: `python -m pip install .`
- Pre-deploy command: `python -m scripts.bootstrap`
- Start command: `python bot.py`
- Python: pinned to `3.11.11` by `.python-version`, matching the CI runtime.
- Database: private internal connection string supplied by Render through the Blueprint; TLS mode `prefer`.
- Telegram: long polling. No public service URL or webhook secret is required.

## Required environment variables

| Variable | Purpose | Where to obtain it |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection URL | Created and linked automatically by the Render Blueprint |
| `TELEGRAM_BOT_TOKEN` | Authenticates the Telegram bot | Telegram BotFather; enter it in Render, never in Git or chat |
| `SUPER_ADMIN_TELEGRAM_ID` | Numeric Telegram ID of the first permanent super admin | Your Telegram numeric user ID; set it in Render |
| `DATABASE_SSLMODE` | PostgreSQL TLS mode | Blueprint sets `prefer` for Render's internal URL |

Do not create a Web Service for this bot unless you intentionally switch to webhook mode. Do not configure a second worker or a second copy of this bot token to poll concurrently.

## Manual setup (only if not using the Blueprint)

If you create Render resources manually, create a PostgreSQL database and a paid Background Worker in the same region. Set `DATABASE_URL` to the database's **Internal Database URL**, set `DATABASE_SSLMODE=prefer`, add `TELEGRAM_BOT_TOKEN` and `SUPER_ADMIN_TELEGRAM_ID` as Render environment variables, and use these commands:

- Build: `python -m pip install .`
- Pre-deploy: `python -m scripts.bootstrap`
- Start: `python bot.py`

The bootstrap command requires only `DATABASE_URL` to initialize the base schema and apply migrations. The bot token is used only by the worker process after the pre-deploy command succeeds.

## Database tables created by the application

At startup, `initialize_database_tables()` creates or upgrades:
- `employees` — employee identity and active/inactive state
- `requests` — individual request records and decision audit fields
- `daily_cigarettes` — daily smoking counters
- `admins` — administrators and super-admin flag
- `lunch_breaks` — daily lunch-break records
- `cigarette_times` — smoking timestamps
- `absences` — absence records

Employee removal is a soft deactivation; historical request and attendance records are retained.

## Multi-branch schema

The separate `migrations/001_multibranch_foundation.sql` adds branches, branch references, branch-admin roles, indexes and compatibility triggers. On a new Render Blueprint database, `scripts/bootstrap.py` applies all numbered migrations automatically before the worker starts. Before applying them to an existing database:
1. Take a PostgreSQL backup and verify that it can be restored.
2. Confirm the base tables above exist.
3. Review the migration and check existing rows, especially requests without an employee link.
4. Run the ordered migration runner from a trusted environment that can reach PostgreSQL:
   `python -m scripts.migrate`.
5. Verify the migration ledger and checksums with `python -m scripts.migrate --check`.
6. Verify the branch and row counts. The runner takes a PostgreSQL advisory lock, applies numbered migrations in order, and records SHA-256 checksums in `schema_migrations`. It never logs the database URL.

Application-level branch controls are implemented on `main` and covered by contract/integration tests:
- A user is an administrator only if they are a super admin or have an explicit active branch role. An ordinary admin record without a branch role does not grant branch access.
- Standard and custom request approvals are checked against the employee's branch; the final decision SQL repeats the authorization condition atomically.
- Smoke, lunch, leave, vacation, custom-request, and return notifications are routed to the employee's branch managers/supervisors plus super admins, not to unrelated branch managers.
- `/list_employees` shows all active employees to the super admin and only assigned branches to branch managers.
- `/add_branch CODE | NAME` creates a branch with its own settings and default bathroom/prayer request buttons; `/list_branches` lists branch IDs, codes, status, and active employee counts.
- `/assign_branch_admin TELEGRAM_ID BRANCH_ID [branch_manager|supervisor]` grants a branch-specific manager or supervisor role; the default is `branch_manager`. `/remove_branch_admin TELEGRAM_ID BRANCH_ID` revokes only that branch assignment.
- `/add_employee BRANCH_ID PHONE FULL_NAME | JOB_TITLE` creates an employee profile with full name and job title in the selected active branch. The super admin can choose any active branch; a branch manager or supervisor can add employees only to a branch assigned to that account. Authorization is checked and locked inside the database transaction.
- Employee deactivation and administrator management remain super-admin-only; employee deactivation is currently performed by phone number and retains historical records.
- Smoke and lunch approvals now commit the request decision and their corresponding daily counter/time records in one PostgreSQL transaction. The current branch policy (enabled state, allowed time window, daily smoking limit, minimum gap, and one-lunch-per-day rule) is revalidated at approval time.
- Broadcast targeting and branch settings/request-type management also validate branch roles.

This is not proof of a live production deployment. For an existing database, apply migrations 001–006 only after a verified backup/restore rehearsal. Verify branch-role assignments and complete staging/Telegram end-to-end tests before using multiple branches in production. If the branch migrations are absent, branch authorization fails closed; existing admins will need explicit branch roles or super-admin status.

## Configurable request buttons (migration 002)

After migration 001, take/verify a backup and apply `migrations/002_configurable_request_types.sql` using the migration runner. It creates the branch-scoped request-type and audit tables and seeds each active branch with bathroom and prayer buttons, both at 10 minutes.

After the migrations:
- Employees send `/start` to refresh the menu and see the two new buttons.
- Managers use `/request_types` to create more timed request buttons, change durations (1–240 minutes), or disable/enable a button.
- A timer starts only after a branch-authorized manager approves the request.
- Custom request notifications are routed to that branch's managers and super admins.

Before using the bot, verify all migrations 001–006 have been applied and the latest GitHub Actions workflow has passed.

## Pre-production integrity migration (migration 005)

Migration 005 adds a unique constraint for active employee phone numbers, a foreign key
from branch settings to branches, operational indexes, and the durable
`timer_start_outbox` table. It deliberately stops if duplicate active phone numbers or
orphaned branch-settings rows are found; resolve those records manually rather than
letting a migration guess which employee or setting to keep.

The outbox ensures an approved timed request and its timer-start intent commit together.
If Telegram delivery or timer persistence temporarily fails, the worker retries the
pending intent and restores it after restart. Delivery is at-least-once: a process crash
between Telegram accepting a message and the database recording its message ID can
produce a duplicate start notification.

Migration 006 adds `returned_at` to distinguish timer expiry from an employee's
explicit return. A timed request remains blocked after expiry until the employee
presses the return button; expiry alone is not treated as a return acknowledgement.

## Migration safety and backup/restore

Before running migrations against an existing database:
1. Create a PostgreSQL backup using Render's backup facility or `pg_dump`.
2. Restore that backup into a separate test database and verify that the application
   can read the restored schema and records.
3. Run `python -m scripts.migrate --check` to inspect pending migrations/checksums.
4. Run `python -m scripts.migrate` once against the intended database.
5. Run `python -m scripts.migrate --check` again; it must report all migrations applied
   with matching checksums.

Do not edit a migration after it has been recorded in `schema_migrations`; add a new
numbered migration instead. If migration 005 reports duplicate active phone numbers,
stop and reconcile those records before retrying. Do not delete or deactivate records
without reviewing their request history.


## Local development

```bash
python -m pip install .
cp .env.example .env
# Edit .env locally; do not commit it.
python bot.py
```

The repository's `.gitignore` excludes `.env`. Use `.env.example` only as a template with placeholders.


## Manager announcements and attention alerts (migration 003)

After migrations 001 and 002, the migration runner applies `migrations/003_broadcast_logs.sql`. It creates the audit table for announcement delivery summaries.

Managers use `/broadcast`:
- The super admin can choose all employees across all branches or one branch.
- A branch manager must explicitly choose one of the branches assigned to that manager; cross-branch broadcast is rejected.
- After choosing the audience, the manager sees employees by name and can select or deselect individual people, move between pages, select everyone in that audience, or clear the selection.
- Only active employees with a linked Telegram account are listed. Branch managers can only see and select employees in branches assigned to them; the recipient list is checked again immediately before delivery.
- The manager can send text, a Telegram voice message, or an audio file. The bot shows the selected recipient count and requires confirmation before sending.
- The result reports successful and failed deliveries and records a delivery summary and per-recipient delivery/acknowledgement state in PostgreSQL.

For a bell/ringing sound, send a short audio clip to the bot as the announcement. Telegram clients control how and whether audio plays automatically; the bot cannot force a recipient's phone to ring or override their notification/silent settings. Employees must have started the bot and have a linked Telegram account to receive broadcasts.

## Broadcast acknowledgement reminders (migration 004)

After migrations 001, 002, and 003 have been applied successfully, the migration
runner applies `migrations/004_broadcast_acknowledgements.sql` to the same PostgreSQL
database. This migration creates per-recipient delivery and acknowledgement records;
it does not delete or rewrite existing employee data.

When a manager sends a Telegram announcement, each successfully reached employee
gets an individual **👁️ تم الاطلاع** button. Until that employee presses it, the
bot sends a reminder notification every 60 seconds. Pending reminders are restored
after a bot restart. The manager's delivery report continues to show sent/failed
counts; individual acknowledgement timestamps are retained in
`broadcast_recipients`.

Telegram bots cannot force a phone to play a continuous looping sound or override
the user's mute, notification, battery, or Do Not Disturb settings. These reminders
are normal Telegram notifications (`disable_notification=false`), not guaranteed
continuous audio. Employees should enable notifications for the bot chat and choose
a noticeable chat notification sound in Telegram settings. The attached voice/audio
is a message attachment and is not autoplayed by Telegram.


## Branch-aware employee onboarding and approval safeguards

Use `/add_employee BRANCH_ID PHONE FULL_NAME | JOB_TITLE` after assigning a branch manager or
supervisor with `/assign_branch_admin TELEGRAM_ID BRANCH_ID [branch_manager|supervisor]`.
Use `/remove_branch_admin TELEGRAM_ID BRANCH_ID` to revoke only that branch role. The employee is created without a
Telegram link; they link their own Telegram account by sending `/start` and
sharing their own phone contact. Duplicate active phone numbers and inactive or
unknown branches are rejected.

For smoke/lunch requests, approval rechecks the branch's current policy while
holding the employee and settings rows in a PostgreSQL transaction. The request
decision and its daily cigarette/lunch accounting records commit together or are
rolled back together. Approved smoking, lunch, and custom-request decisions also create a durable timer-start
outbox row in the same database transaction. Telegram message delivery and timer-row
persistence are retried from that outbox after temporary failures and process restarts.
A crash after Telegram accepts a message but before the database confirms it can still
cause a duplicate start message; staging must verify this at-least-once delivery behavior.
