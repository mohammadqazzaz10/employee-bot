# Employee Portal (PWA)

The employee portal is a mobile-first Arabic RTL Progressive Web App served by the Flask API in `web_app.py`.

## Implemented

- Employee and manager sign-in with employee code / username.
- One-time employee activation codes issued by an authorized manager.
- One-time super-admin bootstrap protected by `WEB_BOOTSTRAP_TOKEN`.
- Password hashing with Werkzeug scrypt, account lockout after repeated failures, hashed opaque sessions, 12-hour expiry, revocation, HttpOnly session cookie, SameSite cookie policy, and CSRF double-submit validation.
- Employee profile, branch-scoped request types, request creation, request history, active timer, return confirmation, and supervisor call.
- Branch-scoped manager request inbox, atomic approve/reject, employee creation, activation-code issue, and super-admin creation of branch supervisors/managers.
- Audit trail, notifications, responsive RTL interface, animated interactions, and PWA installation.
- Service worker never caches API responses or authenticated data.

## Deployment gate

The database migration `007_employee_web_portal.sql` is additive and retains the existing employee, request, and Telegram data. Render runs the migration bootstrap before the web process starts. The Telegram bot is not required to launch the web portal and no Telegram token is requested by the web-first Render Blueprint.

The portal still requires a real PostgreSQL-backed staging test before employee rollout. Do not distribute activation codes until the deployment, login, branch-isolation, request-approval, timer, and session-revocation tests pass. Remove `WEB_BOOTSTRAP_TOKEN` from Render immediately after creating the first super-admin account.

## First deployment

1. Deploy the Render Blueprint from `main`.
2. Set a long random `WEB_BOOTSTRAP_TOKEN` in Render.
3. Open `/setup` over HTTPS and create the first super-admin account.
4. Remove `WEB_BOOTSTRAP_TOKEN` from Render and redeploy/restart.
5. Sign in at `/`, create employee records, and issue each employee a one-time activation code.
6. Employees activate their own accounts and choose their own passwords.

Never commit secrets, employee passwords, activation codes, phone numbers, or database URLs.
