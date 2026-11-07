# Employee Bot — development notes

## Runtime configuration
- Python 3.11+
- PostgreSQL via `DATABASE_URL`
- Telegram via `TELEGRAM_BOT_TOKEN`
- First administrator via `SUPER_ADMIN_TELEGRAM_ID`
- Render Web Services use the automatically supplied `RENDER_EXTERNAL_URL`; Background Workers use polling.
- Local secrets belong in `.env`, which is ignored by Git. See `.env.example` and `DEPLOYMENT.md`.

## Database tables
At startup, the application creates/upgrades employees, requests, daily_cigarettes, admins, lunch_breaks, cigarette_times, and absences. It refuses to start without a Telegram token and PostgreSQL URL. The configured super-admin ID is inserted into `admins` as a super administrator.

## Implemented on this development branch
- Contact onboarding verifies a phone against an active employee record in PostgreSQL before linking the Telegram account.
- Admin checks read PostgreSQL and fail closed when the database is unavailable.
- The first super administrator is bootstrapped from `SUPER_ADMIN_TELEGRAM_ID`; no admin ID or employee phone is hardcoded.
- Employee removal deactivates the employee instead of deleting historical rows.
- Callback payloads are validated; only administrators can approve/reject, and only the target employee can confirm their return.
- Smoke, lunch, leave, and vacation requests are persisted before admin notification.
- Request decisions use a conditional update from `pending`, preventing a second decision, and record the deciding admin and timestamps.
- Daily smoke/lunch limits use the Jordan-local calendar date.
- The multi-branch migration adds the initial branch, employee/request branch references, branch role assignments, indexes, and default-branch triggers.

## Local checks
```bash
python -m pip install .
python -m compileall -q .
python -m unittest discover -s tests -v
```

## Database migration
Read `DEPLOYMENT.md` and `docs/SECURITY_REVIEW.md`. Back up and verify restore before applying `migrations/001_multibranch_foundation.sql`. The migration is not executed automatically. Existing requests without employee links require review.

## Remaining production gates
Application-level branch-scoped authorization and queries, durable/recoverable timers, transactional request side effects, and PostgreSQL integration tests remain outstanding. The schema alone does not enforce branch isolation. Do not use multiple branches in production until these gates are complete.
