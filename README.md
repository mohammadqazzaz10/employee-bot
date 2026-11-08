# Employee Bot

Telegram employee operations bot for break requests, leave/vacation requests, branch management, announcements, and timed reminders, backed by PostgreSQL.

## Quick links
- [Deployment and Render setup](DEPLOYMENT.md)
- [Development notes](DEVELOPMENT.md)
- [Security review and production gates](docs/SECURITY_REVIEW.md)
- [SQL migrations](migrations/)

## Requirements
- Python 3.11.11 (pinned for CI and Render in `.python-version`)
- PostgreSQL (CI runs PostgreSQL 16; Render Blueprint provisions managed PostgreSQL)
- Telegram bot token from BotFather

## Local setup
1. Install dependencies: `python -m pip install .`
2. Copy `.env.example` to `.env` and set `DATABASE_URL` and `DATABASE_SSLMODE`; leave `TELEGRAM_BOT_TOKEN` empty for the bootstrap.
3. Run `python bot.py` once. It creates the base tables and exits without starting Telegram.
4. Take and verify a database backup, then apply numbered migrations:
   `python -m scripts.migrate`
5. Verify the migration ledger and checksums:
   `python -m scripts.migrate --check`
6. Only then add `TELEGRAM_BOT_TOKEN` and `SUPER_ADMIN_TELEGRAM_ID`, and start the bot with `python bot.py`.

The bot runtime refuses to start without `TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, and all migrations 001–006. The one-time database bootstrap needs only `DATABASE_URL`. Never commit `.env`, Telegram tokens, webhook secrets, or database URLs.

## Employee and branch administration
Every employee profile includes a full name, job title, phone number, and branch. An employee links their own Telegram account by sending `/start` and sharing their own contact. Linking is allowed only for an active pre-registered employee and cannot move an account or claim another employee's phone.

The super admin or a manager/supervisor assigned to a branch can create an employee profile with:
`/add_employee BRANCH_ID PHONE FULL_NAME | JOB_TITLE`

The super admin creates a branch with `/add_branch CODE | NAME` and lists branches with `/list_branches`. A new branch is seeded with its own settings and default bathroom/prayer request buttons. The super admin assigns a branch role with `/assign_branch_admin TELEGRAM_ID BRANCH_ID [branch_manager|supervisor]` and revokes one branch assignment with `/remove_branch_admin TELEGRAM_ID BRANCH_ID`. A manager can view and operate only within assigned branches; the super admin has global access.

## Branch policy settings
Managers use `/settings` to edit their branch's smoking and lunch policy. A super admin can select a branch with `/settings BRANCH_ID`.

- Smoking: enable/disable, daily limit, duration, allowed start/end times, and minimum interval.
- Lunch: enable/disable, duration, and optional allowed time window.
- Daytime, overnight, and open-ended time windows are supported.
- Every change is stored per branch and audited with the actor and timestamp.

## Timed request buttons
After migrations 001 and 002, each branch receives bathroom and prayer buttons, each with a 10-minute default duration. Managers use `/request_types` (or `/request_types BRANCH_ID`) to create additional timed buttons, set durations from 1–240 minutes, and enable/disable them. An authorized manager's approval is required before a timer begins.

## Timers and employee notifications
- Countdown with progress bar, remaining time, and expected finish time.
- Timer state is persisted in PostgreSQL and restored after restarts.
- Approved timed requests create a durable timer-start outbox record in the same transaction as the request decision and accounting changes.
- Temporary Telegram delivery or timer persistence failures are retried; a crash after Telegram accepts a message but before persistence may result in a duplicate start notification (at-least-once delivery).
- The expiry alert includes a return-to-work confirmation button. A stale button cannot clear a newer timer.

## Manager broadcasts
Managers use `/broadcast` to choose a permitted branch or audience, select employees individually, and send text, voice, or audio. Every recipient has an individual acknowledgement button. Reminders repeat every 60 seconds until acknowledged and are restored after restart. Telegram clients control notification sound and mute settings; the bot cannot force continuous ringing or autoplay an audio attachment.

## Database and deployment
- The root `render.yaml` defines a Render Background Worker and a managed PostgreSQL database in Frankfurt.
- The worker uses Telegram long polling, one instance, and a private database connection; no webhook URL or public web service is needed.
- Render runs `python -m scripts.bootstrap` before starting the bot. The bootstrap initializes base tables, applies migrations 001–006, and verifies migration checksums.
- Build command: `python -m pip install .`; start command: `python bot.py`.
- Secrets are entered only in Render environment settings. The Blueprint prompts for `TELEGRAM_BOT_TOKEN` and `SUPER_ADMIN_TELEGRAM_ID`; it never stores secret values in Git.
- See [DEPLOYMENT.md](DEPLOYMENT.md) for the exact Blueprint steps and safe handling of existing databases.

## Verification status
GitHub Actions compiles Python sources and runs unit/contract plus PostgreSQL integration tests against PostgreSQL 16. CI does not replace staging or live Telegram verification.

Before production, complete:
- Successful latest CI for the current `main` commit.
- For an existing database, a verified backup/restore rehearsal before applying migrations.
- Staging/Telegram end-to-end tests for onboarding, request approvals, timers, broadcasts, permissions, and recovery.
- Render Blueprint configuration, secret entry, and a controlled first deployment.

The multi-branch implementation has been merged into `main` (PR #1). GitHub Actions passed for the merge commit. This confirms automated code and PostgreSQL test success, not a live Render deployment or Telegram end-to-end verification.
