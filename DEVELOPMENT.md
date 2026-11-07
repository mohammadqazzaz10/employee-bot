# Employee Bot — development notes

## Current baseline
- Python 3.11+
- Telegram bot: python-telegram-bot
- PostgreSQL: psycopg2
- Intended deployment: Render (deployment is not managed by this repository change)

## Local checks
```bash
python -m compileall -q .
python -m unittest discover -s tests -v
```

## Implemented on the development branch
- Contact onboarding checks the authorized phone before creating/linking an employee record.
- Callback payloads are validated; only administrators can approve/reject, and only the target employee can confirm their return.
- Smoke, lunch, leave, and vacation requests are persisted before admin notification.
- Request decisions use a conditional update from `pending`, preventing a second decision, and record the deciding admin and timestamps.
- Daily smoke/lunch limits use the Jordan-local calendar date.
- The branch migration assigns existing employees to the initial branch and adds triggers so legacy employee/request inserts inherit branch context.

## Database migration
Read `docs/SECURITY_REVIEW.md` and inspect `migrations/001_multibranch_foundation.sql` before applying it. Take and verify a PostgreSQL backup first. The bot does not run this migration automatically. The request audit columns are added by the bot's database initialization for existing installations; the branch migration remains a separate, reviewed operation.

## Branch strategy
- `main`: current production source; do not edit directly.
- `improvements/multibranch-foundation`: isolated development branch.
- Changes are proposed through a draft pull request for review; no production deploy is performed automatically.

## Not production-ready yet
The current application still needs branch-scoped authorization and queries, durable/recoverable timers, environment-managed phone authorization, and database integration tests. The migration alone does not enforce branch isolation. Do not use multiple branches in production until application-layer enforcement and cross-branch integration tests are complete.
