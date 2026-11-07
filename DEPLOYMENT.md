# Deployment and first-run setup

This project needs a Telegram bot token and a PostgreSQL database. Secrets belong in the Render service's Environment settings (or a local, untracked `.env` file), never in Git.

## Required environment variables

| Variable | Purpose | Where to obtain it |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Authenticates the Telegram bot | Create/manage the bot with Telegram's BotFather |
| `DATABASE_URL` | PostgreSQL connection URL | Render PostgreSQL dashboard → Connect → Internal Database URL |
| `SUPER_ADMIN_TELEGRAM_ID` | Numeric Telegram user ID of the first permanent super admin | Message `/my_id` to the bot after its first startup |
| `DATABASE_SSLMODE` | PostgreSQL TLS mode | Use `prefer` for Render's internal URL; use `require` for an external TLS connection |

Optional:
- `WEBHOOK_URL`: public base URL only when explicitly configuring a webhook. Render web services automatically use `RENDER_EXTERNAL_URL`; no manually invented URL is needed.
- `PORT`: Render supplies this automatically for a web service. Do not hardcode it in Render.

## Create the PostgreSQL database on Render

1. Open the Render Dashboard and choose **New → PostgreSQL**.
2. Create a database, for example `employee-bot-db`. Prefer the same region as the bot service.
3. Wait until the database is available.
4. Open its **Connect** tab and copy the **Internal Database URL**. Keep it private.
5. Open the bot service → **Environment** and add `DATABASE_URL` with that exact URL.
6. Set `DATABASE_SSLMODE=prefer` when using the internal URL. If you deliberately use the external URL, set `DATABASE_SSLMODE=require`.

## Configure the Telegram bot and service

1. In BotFather, create the bot (or open the existing bot) and copy its API token.
2. In Render → bot service → **Environment**, add `TELEGRAM_BOT_TOKEN` with the token. Do not put the token in this repository.
3. Use build command `pip install .` and start command `python bot.py`.
4. If the Render service is a **Background Worker**, leave `WEBHOOK_URL` unset: the application uses Telegram long polling.
5. If the Render service is a **Web Service**, Render supplies `RENDER_EXTERNAL_URL` automatically and the application uses it as the webhook base URL. Do not include the bot token in a manually entered URL.
6. Save the environment settings and deploy/restart the service.

## Create the first super administrator

1. Start the bot with its token and database URL. The application creates its base tables on startup.
2. Open Telegram and send `/my_id` to the bot from the account that should own administration.
3. Copy the numeric ID returned by the bot.
4. In Render → bot service → **Environment**, add `SUPER_ADMIN_TELEGRAM_ID` with that number.
5. Save and restart/redeploy. On startup, the bot inserts that account into the `admins` table as the permanent super administrator.
6. Verify with `/list_admins`. The super admin can then add other administrators with `/add_admin TELEGRAM_ID`.

If `SUPER_ADMIN_TELEGRAM_ID` is absent, the bot deliberately starts without an administrator and logs a warning; it does not grant admin rights to a hard-coded account. The `/my_id` command remains available so the correct ID can be obtained.

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
4. Apply the migration once using a PostgreSQL SQL client.
5. Verify the branch and row counts.

Important: the current application still needs branch-scoped authorization and queries before multiple branches can safely be used. Creating the schema alone does not provide branch isolation. Keep production on one branch until those controls and PostgreSQL integration tests are complete.

## Configurable request buttons (migration 002)

After migration 001, take/verify a backup and apply `migrations/002_configurable_request_types.sql`. It creates the branch-scoped request-type and audit tables and seeds each active branch with bathroom and prayer buttons, both at 10 minutes. The bot does not apply either migration automatically.

After the migrations:
- Employees send `/start` to refresh the menu and see the two new buttons.
- Managers use `/request_types` to create more timed request buttons, change durations (1–240 minutes), or disable/enable a button.
- A timer starts only after a branch-authorized manager approves the request.
- Custom request notifications are routed to that branch's managers and super admins.

Do not deploy until migration 001 and 002 have been applied and the latest GitHub Actions workflow has passed.

## Local development

```bash
python -m pip install .
cp .env.example .env
# Edit .env locally; do not commit it.
python bot.py
``

The repository's `.gitignore` excludes `.env`. Use `.env.example` only as a template with placeholders.


## Manager announcements and attention alerts (migration 003)

After migrations 001 and 002, take/verify a backup and apply `migrations/003_broadcast_logs.sql`. It creates the audit table for announcement delivery summaries.

Managers use `/broadcast`:
- The super admin can choose all employees across all branches or one branch.
- A branch manager must explicitly choose one of the branches assigned to that manager; cross-branch broadcast is rejected.
- The manager can send text, a Telegram voice message, or an audio file. The bot shows the audience and recipient count and requires confirmation before sending.
- The result reports successful and failed deliveries and records a delivery summary in PostgreSQL.

For a bell/ringing sound, send a short audio clip to the bot as the announcement. Telegram clients control how and whether audio plays automatically; the bot cannot force a recipient's phone to ring or override their notification/silent settings. Employees must have started the bot and have a linked Telegram account to receive broadcasts.

## Broadcast acknowledgement reminders (migration 004)

After migrations 001, 002, and 003 have been applied successfully, apply
`migrations/004_broadcast_acknowledgements.sql` to the same PostgreSQL database.
Take a database backup first. This migration creates per-recipient delivery and
acknowledgement records; it does not delete or rewrite existing employee data.

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
