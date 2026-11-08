# Deployment and first-run setup

This project needs a Telegram bot token and a PostgreSQL database. Secrets belong in the Render service's Environment settings (or a local, untracked `.env` file), never in Git.

## Required environment variables

| Variable | Purpose | Where to obtain it |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection URL | Render PostgreSQL dashboard → Connect → Internal Database URL |
| `TELEGRAM_BOT_TOKEN` | Authenticates the Telegram bot | Create/manage the bot with Telegram's BotFather; add it only after database migrations pass |
| `SUPER_ADMIN_TELEGRAM_ID` | Numeric Telegram user ID of the first permanent super admin | Message `/my_id` to the bot after its first token-enabled startup |
| `DATABASE_SSLMODE` | PostgreSQL TLS mode | Use `prefer` for Render's internal URL; use `require` for an external TLS connection |

Optional:
- `WEBHOOK_SECRET`: required when webhook mode is enabled (Render Web Service); use a random 1–256 character value containing only letters, digits, underscores, or hyphens.
- `WEBHOOK_URL`: public base URL only when explicitly configuring a webhook. Render web services automatically use `RENDER_EXTERNAL_URL`; no manually invented URL is needed.
- `PORT`: Render supplies this automatically for a web service. Do not hardcode it in Render.

## Create the PostgreSQL database on Render

1. Open the Render Dashboard and choose **New → PostgreSQL**.
2. Create a database, for example `employee-bot-db`. Prefer the same region as the bot service.
3. Wait until the database is available.
4. Open its **Connect** tab and copy the **Internal Database URL**. Keep it private.
5. Keep the Internal Database URL private. It will be added to the migration/bootstrap environment when the database is ready.
6. Set `DATABASE_SSLMODE=prefer` when using the internal URL. If you deliberately use the external URL, set `DATABASE_SSLMODE=require`.

## Bootstrap the database before adding bot credentials

1. In a trusted environment that can reach the Render PostgreSQL instance, set
   `DATABASE_URL` and `DATABASE_SSLMODE`. Do not set the Telegram token yet.
2. Run `python bot.py` once. It creates the base tables and exits cleanly when no
   `TELEGRAM_BOT_TOKEN` is configured; it does not start Telegram polling/webhook.
3. Take a backup and verify restore into a separate database.
4. Run `python -m scripts.migrate` to apply migrations 001–006 in order.
5. Run `python -m scripts.migrate --check`. It must report that every migration is
   applied and all checksums match.
6. Only after these steps, add `TELEGRAM_BOT_TOKEN` to the Render service environment.

## Configure the Telegram bot and service

1. In BotFather, create the bot (or open the existing bot) and copy its API token.
2. In Render → bot service → **Environment**, add `TELEGRAM_BOT_TOKEN`. Do not put the
   token in this repository or in chat.
3. Use build command `pip install .` and start command `python bot.py`.
4. Prefer a **Background Worker** for the first deployment: leave `WEBHOOK_URL` and
   `WEBHOOK_SECRET` unset; the application uses Telegram long polling.
5. If using a **Web Service**, Render supplies `RENDER_EXTERNAL_URL` automatically.
   Set a separate random `WEBHOOK_SECRET`; the app validates Telegram's webhook
   secret-token header. Do not include the bot token in a manually entered URL.
6. The application refuses to run the bot if any required migration 001–006 is missing.
   Save the environment settings and deploy/restart only after the migration check passes.

## Create the first super administrator

1. After migrations 001–006 pass, start the bot with its token and database URL. The application verifies the migration ledger before starting Telegram handlers.
2. Open Telegram and send `/my_id` to the bot from the account that should own administration.
3. Copy the numeric ID returned by the bot.
4. In Render → bot service → **Environment**, add `SUPER_ADMIN_TELEGRAM_ID` with that number.
5. Save and restart/redeploy. On startup, the bot inserts that account into the `admins` table as the permanent super administrator.
6. Verify with `/list_admins`. The super admin can then add other administrators with `/add_admin TELEGRAM_ID`.

If `SUPER_ADMIN_TELEGRAM_ID` is absent, the bot starts without an administrator and logs a warning; it does not grant admin rights to a hard-coded account. The `/my_id` command remains available so the correct ID can be obtained.

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

The separate `migrations/001_multibranch_foundation.sql` adds branches, branch references, branch-admin roles, indexes and compatibility triggers. It is not executed automatically. Before applying it to an existing database:
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

This is not a production deployment approval. Apply migrations 001–006 in order after a verified backup, verify branch-role assignments, and complete a staging/Telegram end-to-end test before using multiple branches in production. If the branch migrations are absent, branch authorization fails closed; existing admins will need explicit branch roles or super-admin status.

## Configurable request buttons (migration 002)

After migration 001, take/verify a backup and apply `migrations/002_configurable_request_types.sql` using the migration runner. It creates the branch-scoped request-type and audit tables and seeds each active branch with bathroom and prayer buttons, both at 10 minutes.

After the migrations:
- Employees send `/start` to refresh the menu and see the two new buttons.
- Managers use `/request_types` to create more timed request buttons, change durations (1–240 minutes), or disable/enable a button.
- A timer starts only after a branch-authorized manager approves the request.
- Custom request notifications are routed to that branch's managers and super admins.

Do not deploy until migration 001 and 002 have been applied and the latest GitHub Actions workflow has passed.

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
