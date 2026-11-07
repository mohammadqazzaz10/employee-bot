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

**Important:** Apply `migrations/001_multibranch_foundation.sql` to the database before using branch-scoped settings. The migration is not run automatically. Back up the database first. Branch permissions are enforced in application queries and approval transactions; staging and live Telegram verification are still required before production use.

## Configurable timed request buttons

After applying migrations 001 and 002, each branch receives two default buttons: **🚻 استئذان الذهاب إلى الحمام** and **🕌 استئذان الذهاب إلى الصلاة**, each set to 10 minutes. The timer starts only after an authorized manager approves the request.

A branch manager or super admin can send `/request_types` (or `/request_types BRANCH_ID`) to create additional buttons, set a duration from 1 to 240 minutes, and enable/disable each type. New buttons are shown in the employee keyboard after the employee sends `/start` to refresh it. Request types and their changes are stored and audited in PostgreSQL per branch. Custom request notifications go only to that branch's managers and super admins.

Run `migrations/002_configurable_request_types.sql` after migration 001, with a verified database backup. Do not deploy before both migrations and the latest GitHub Actions checks have succeeded.

## Branch employee onboarding

The super admin or a manager assigned to a branch can add a registered employee with:
`/add_employee BRANCH_ID PHONE FULL_NAME`

The super admin can choose any active branch. A branch manager can add employees only to their assigned branch. The employee then links their Telegram account by sending `/start` and sharing their own phone contact.

## Render
Follow [DEPLOYMENT.md](DEPLOYMENT.md). Render supplies the web service's public URL automatically; a Background Worker can use long polling without a public URL. Never commit tokens or database URLs.

## Production readiness

The development branch now includes branch-scoped administrator access, employee listing/onboarding, request approval checks, per-branch notifications and broadcast targeting, configurable smoking/lunch policy, timed custom request buttons, persistent countdowns, and PostgreSQL integration tests.

Still required before production:
- Apply migrations 001–004 in order after a verified backup and verify administrator-to-branch assignments.
- Confirm the latest GitHub Actions runs pass.
- Complete staging and real Telegram tests for employee linking, approvals, countdown recovery, broadcast delivery/acknowledgements, and failure handling.
- Rehearse database backup/restore.
- Configure and deploy the actual Render database and bot service.
- Timer message delivery and timer persistence still occur after the request/accounting transaction; this path needs staging validation and operational recovery checks.
