# Employee Bot

Telegram bot for employee break, leave and vacation requests, with PostgreSQL persistence.

## Quick links
- [Deployment and Render setup](DEPLOYMENT.md)
- [Development notes](DEVELOPMENT.md)
- [Security review and remaining production gates](docs/SECURITY_REVIEW.md)
- [Database migrations](migrations/)

## Requirements
- Python 3.11+
- PostgreSQL
- Telegram bot token from BotFather

## Run locally
1. Install: `python -m pip install .`
2. Copy `.env.example` to `.env` and fill in local values.
3. Run: `python bot.py`

The application creates its base PostgreSQL tables on startup. It refuses to start without `TELEGRAM_BOT_TOKEN` or `DATABASE_URL`.

## Branch policy settings

Administrators can open `/settings` in Telegram to edit each branch's smoking and lunch rules. Changes are stored in PostgreSQL and take effect immediately; every change is recorded in `branch_settings_audit`. The initial defaults preserve the existing policy: 5 cigarettes per day, 6 minutes per smoking break, smoking from 10:00, a 90-minute gap, and a 30-minute lunch break. Optional end times and lunch windows are unrestricted until configured. Branch managers must have an `admin_branch_roles` assignment; the super admin grants it with `/assign_branch_admin TELEGRAM_ID BRANCH_ID`. Super admins can select an active branch with `/settings BRANCH_ID`.

**Important:** Apply `migrations/001_multibranch_foundation.sql` to the database before using branch-scoped settings. The migration is not run automatically. Back up the database first and do not deploy this draft until branch-scoped access and remaining production checks are reviewed.

## Configurable timed request buttons

After applying migrations 001 and 002, each branch receives two default buttons: **🚻 استئذان الذهاب إلى الحمام** and **🕌 استئذان الذهاب إلى الصلاة**, each set to 10 minutes. The timer starts only after an authorized manager approves the request.

A branch manager or super admin can send `/request_types` (or `/request_types BRANCH_ID`) to create additional buttons, set a duration from 1 to 240 minutes, and enable/disable each type. New buttons are shown in the employee keyboard after the employee sends `/start` to refresh it. Request types and their changes are stored and audited in PostgreSQL per branch. Custom request notifications go only to that branch's managers and super admins.

Run `migrations/002_configurable_request_types.sql` after migration 001, with a verified database backup. Do not deploy before both migrations and the latest GitHub Actions checks have succeeded.

## Render
Follow [DEPLOYMENT.md](DEPLOYMENT.md). Render supplies the web service's public URL automatically; a Background Worker can use long polling without a public URL. Never commit tokens or database URLs.

## Important status
This development branch is not yet production-ready for multi-branch operation. Branch-scoped authorization and database queries, durable timers, transactional request side effects, and PostgreSQL integration tests remain required.
