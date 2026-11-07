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

## Database migration
Read `docs/SECURITY_REVIEW.md` and inspect `migrations/001_multibranch_foundation.sql` before applying it. Take and verify a PostgreSQL backup first. The bot does not run this migration automatically.

## Branch strategy
- `main`: current production source; do not edit directly.
- `improvements/multibranch-foundation`: isolated development branch.
- Changes are proposed through a draft pull request for review; no production deploy is performed automatically.

## Important
The migration creates the database foundation only. The current bot code still needs a separate application-layer change to enforce branch-scoped reads/writes and role checks. Do not use multiple branches in production until that work and its integration tests are complete.
