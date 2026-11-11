# Employee Operations Platform

Branch-aware employee management platform with an Arabic RTL web portal (PWA), PostgreSQL backend, and optional Telegram integration.

## Main capabilities

- Employee web accounts activated by one-time codes; no Telegram registration or Premium subscription is required.
- Super-admin can create branches, provision branch managers, add employees, and reset employee passwords with one-time temporary credentials.
- Employee self-service for branch-configured requests, request history, countdown timers, return confirmation, and supervisor call requests.
- Manager and supervisor portal with branch-scoped request review, approval/rejection, employee management, and notifications.
- Branch policy settings for smoking/lunch windows, durations, daily limits, minimum gaps, and configurable request buttons.
- Super-admin account setup and branch-scoped manager provisioning.
- Responsive installable PWA with Arabic RTL interface.
- Existing Telegram bot source is retained as an optional future channel; the web portal can run without a Telegram bot token.

## Requirements

- Python 3.11
- PostgreSQL 16+
- Render (or another HTTPS-capable Python host)

## Local setup

1. Install dependencies: `python -m pip install .`
2. Set `DATABASE_URL` and `DATABASE_SSLMODE` in your environment.
3. Run `python -m scripts.bootstrap` to initialize the base schema and apply all numbered migrations, including `007_employee_web_portal.sql`.
4. Set `COOKIE_SECURE=false` only for local HTTP development.
5. Run `python web_app.py` and open `http://localhost:8080`.
6. Set `WEB_BOOTSTRAP_TOKEN` to a long random secret and open `/setup` to create the first super-admin account. Remove the token after successful setup.

## Render web-first deployment

The root `render.yaml` provisions the web service and PostgreSQL database. It does not request a Telegram token and does not start the Telegram worker. Render runs the database bootstrap before launching Gunicorn.

See [DEPLOYMENT.md](DEPLOYMENT.md) for the deployment and initial admin setup steps.

## Security model

- Employee identity, role, and branch are loaded from the authenticated server-side session.
- Employee passwords are never stored in plaintext.
- Session tokens are opaque and stored as SHA-256 hashes; session cookies are HttpOnly, Secure in production, and SameSite=Lax.
- Mutating authenticated API calls require a CSRF token.
- Branch managers and supervisors are restricted to their assigned branch.
- Web decisions use a dedicated `decided_by_web_user_id` field and do not overwrite legacy Telegram actor IDs.
- Activation codes are random, hashed at rest, single-use, and expire after 24 hours.
- API responses are not cached by the service worker.

## Verification

GitHub Actions compiles the Python code, initializes PostgreSQL, applies/checks numbered migrations, and runs unit, contract, and PostgreSQL integration tests. CI success is not a substitute for live staging tests and a verified database backup before production use.

Never commit `.env`, bootstrap tokens, employee passwords, activation codes, or database URLs.

## Web administration additions (migrations 008–009)

The web portal includes branch-scoped employee activation/blocking, super-admin branch rename/activation, branch-scoped employee announcements, meeting scheduling/cancellation, and super-admin site branding/support text settings. These actions are audited. Meeting creation sends an immediate in-app notification; a 15-minute reminder is queued when an authenticated portal client polls the reminder endpoint. This is best-effort while clients are online, not a guaranteed background scheduler.

Employees can enable a two-tone browser alert and optional system notifications. While the browser page remains alive, notifications are polled and may alert even when the tab is hidden; browser throttling, device power settings and permission can affect delivery. Web Push delivery and guaranteed ringing while the browser is closed are not implemented. Telegram and SMS are not required.


## Live portal synchronization

While an authenticated portal is open, employee and manager dashboard data refreshes automatically every 3 seconds without a full page reload. Notification checks also run every 3 seconds. The browser tab must remain open; background throttling, connectivity, and device power settings can delay updates. This is near-real-time polling, not a persistent push channel. Web Push while the PWA is closed remains a separate feature.
