# Employee Operations Platform — development notes

## Runtime modes

### Web portal (current web-first target)
- Python 3.11+, Flask, Gunicorn, PostgreSQL.
- Environment: DATABASE_URL, DATABASE_SSLMODE, COOKIE_SECURE, WEB_BOOTSTRAP_TOKEN.
- Run migrations with python -m scripts.bootstrap.
- Run locally with python web_app.py; open http://localhost:8080.
- Create the first web super-admin once through /setup; remove WEB_BOOTSTRAP_TOKEN immediately afterward.
- Telegram is not required for the web portal.

### Optional legacy Telegram worker
- TELEGRAM_BOT_TOKEN and SUPER_ADMIN_TELEGRAM_ID.
- Webhook mode additionally uses RENDER_EXTERNAL_URL and WEBHOOK_SECRET; polling mode does not.
- Telegram source remains in the repository but the current Render Blueprint does not start it.

## Database lifecycle

The bootstrap initializes the base schema and applies numbered migrations 001–007 in numeric order. The migration runner uses a PostgreSQL advisory lock and records SHA-256 checksums. Verify with python -m scripts.migrate --check.

Migration 007 is additive. It adds employee codes, web users, hashed sessions, login throttles, activation tokens, web timers, notifications, audit records, and a separate web decision actor field. It also installs an employee-code trigger so existing Telegram/admin insertion paths that omit employee_code remain compatible. Do not edit any applied migration; add a new numbered migration.

## Implemented web portal features

- Arabic RTL, responsive PWA layout with animated states and install prompt.
- Employee activation with one-time expiring codes and self-chosen passwords.
- Employee login, password hashing, account lockout, IP throttling, CSRF-protected sessions, logout, and mandatory temporary-password changes.
- Employee dashboard, branch-configured request types, request history, timer countdown, return confirmation, and supervisor call.
- Manager dashboard, branch-scoped request inbox, atomic decisions, employee creation, and activation-code issue.
- Super-admin provisioning of branches, branch managers, and supervisors.
- Branch-scoped smoking/lunch policy controls and audited configurable request types with per-branch durations.
- Notifications, audit log, branch isolation, and API responses excluded from service-worker caches.

## Local checks

```bash
python -m pip install .
python -m scripts.bootstrap
python -m compileall -q .
python -m unittest discover -s tests -v
```

## Production gates

- Latest GitHub Actions run must pass on the exact PR head SHA.
- Back up and verify restore before using an existing database.
- Test bootstrap, employee activation, login, password change, session expiry/logout, and account lockout.
- Test branch isolation with at least two branches and different manager accounts.
- Test request creation, concurrent decisions, timer expiry, and return confirmation.
- Test Android/iOS PWA install behavior and notification behavior on real devices.
- Configure Render secrets only in Render; never commit them.
- Do not invite employees until a real Render staging deployment passes.

CI does not prove live Render deployment, browser/device compatibility, or delivery of notifications while the PWA is closed.
