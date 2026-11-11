# Deployment: Employee Web Portal (Web-first)

The current Render Blueprint launches the employee portal as a Python Web Service and provisions PostgreSQL. It intentionally does **not** start the Telegram worker and does not request a Telegram token. Telegram source and its database records are retained for optional integration later.

## Deploy with Render Blueprint

1. Confirm the changes are merged into the repository's `main` branch.
2. In Render, choose **New → Blueprint** and connect `mohammadqazzaz10/employee-bot`.
3. Select `main` and review the resources described by `render.yaml`: `employee-web-portal` (Web Service) and `employee-bot-db` (PostgreSQL).
4. The Web Service uses the Starter plan because the deployment needs a pre-deploy database migration command and an always-on web process. The database plan is declared separately in the Blueprint; confirm its current price and retention terms in Render before provisioning.
5. When prompted, set `WEB_BOOTSTRAP_TOKEN` to a long random secret. Generate it locally, for example with `openssl rand -hex 32`. Do not send it in chat or commit it to Git.
6. Keep `DATABASE_URL` linked to the Blueprint-managed database and `DATABASE_SSLMODE=prefer`. Keep `COOKIE_SECURE=true`.
7. Confirm the Blueprint. Render installs the project, runs `python -m scripts.bootstrap` before release, then starts Gunicorn with `web_app:app`.
8. Wait for the deploy to become healthy. The health check is `/healthz`; it verifies a real PostgreSQL connection.
9. Open the HTTPS service URL followed by `/setup`. Enter the bootstrap token, the first administrator's name, username, and a strong password (at least 12 characters).
10. After the setup page confirms success, immediately remove `WEB_BOOTSTRAP_TOKEN` from the Render environment and restart/redeploy the service. The endpoint is one-time only and will refuse to create another super-admin once any web user exists.
11. Sign in at the root URL. Create employees from **الموظفون**, then issue each employee an activation code. Give the code privately to that employee; it expires after 24 hours and can be used once.
12. Employees open the same URL, choose **تفعيل حساب جديد**, enter their code, and create their own password. Their username is the employee code shown in the employee list.

## Database safety

Migrations `007_employee_web_portal.sql` through `009_web_site_settings.sql` are additive. They add employee web accounts and sessions, timers, notifications, audit events, employee status controls, meetings, and global site settings. They do not delete employee records, requests, Telegram IDs, or legacy tables.

If you are connecting an existing database, make a backup and verify that you can restore it before deploying. Review the existing migration ledger and checksums first. Do not point a new Blueprint at a production database without confirming the exact database resource and migration history.

## Runtime configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Render-managed PostgreSQL connection string |
| `DATABASE_SSLMODE` | TLS mode; Blueprint uses `prefer` |
| `COOKIE_SECURE` | Must remain `true` on the HTTPS production site |
| `WEB_BOOTSTRAP_TOKEN` | Temporary one-time setup secret; remove after first admin creation |

No Telegram bot token, Telegram user ID, webhook URL, or Telegram webhook secret is required for the web portal.

## Optional Telegram integration (not enabled by this Blueprint)

The legacy Telegram bot remains in the repository for a later, separately tested rollout. If it is enabled in a future deployment, its configuration may use `TELEGRAM_BOT_TOKEN`, `SUPER_ADMIN_TELEGRAM_ID`, `RENDER_EXTERNAL_URL`, and `WEBHOOK_SECRET` (only when deliberately switching to webhook mode). Render supplies `RENDER_EXTERNAL_URL` to Web Services. These variables are intentionally absent from the current web-first Blueprint; do not add them unless you are explicitly enabling and testing the Telegram worker.

## Local development

1. Install: `python -m pip install .`
2. Set `DATABASE_URL` and `DATABASE_SSLMODE=disable` for a local PostgreSQL instance.
3. Run `python -m scripts.bootstrap`.
4. Set `COOKIE_SECURE=false` for localhost HTTP only.
5. Set a temporary `WEB_BOOTSTRAP_TOKEN`, run `python web_app.py`, and open `http://localhost:8080/setup`.
6. After creating the first super-admin, remove the bootstrap token from the environment.

## Before inviting employees

- Confirm latest GitHub Actions checks are green.
- Confirm migrations `007`–`009` are applied and checksums match.
- Test admin bootstrap and remove the bootstrap secret.
- Test employee activation, login, password changes, session expiry/logout, and account lockout.
- Test branch isolation with at least two branches and two branch managers.
- Test request creation, concurrent approval attempts, timer expiry, and return confirmation.
- Test mobile layout and PWA installation on Android and iOS browsers.
- Verify a database backup and restore procedure.
- Keep the Telegram worker disabled until separately configured and tested.

A green CI run confirms automated checks only; it does not prove a live Render deployment or production readiness.

## Administration and notifications

Managers can add employees, issue activation codes, reset employee passwords, block/reactivate employees, configure branch break policies and request buttons, send branch announcements, and create/cancel meetings. Super-admins can also rename/disable branches and edit global portal branding and support text. Every administrative change is audited.

Meeting creation sends an in-app notification immediately. A 15-minute reminder is processed by authenticated portal clients polling every 20 seconds. It is not a guaranteed server-side scheduler when all clients are offline. Sound is a foreground browser tone after the user enables it; browser notifications require permission. Background push/ringing while the browser is closed is not implemented in this deployment.
