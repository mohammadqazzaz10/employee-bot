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
3. Run `python -m scripts.bootstrap` to initialize the base schema and apply all numbered migrations, including the latest numbered migrations through `011_web_push_and_work_tasks.sql`.
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

Employees can enable browser notifications and subscribe each device to authenticated Web Push. New notification rows are queued in PostgreSQL and delivered by the server-side pywebpush worker using VAPID keys, including when the browser/PWA is closed (subject to browser support, permission, network and OS battery policies). The service worker displays the push and opens the portal when tapped. Foreground SSE and polling remain as fallbacks. Configure `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_SUBJECT` on the host before enabling employee push subscriptions.

## Work task tracking (migration 011)

Managers and supervisors can create branch-scoped tasks, assign each task to one or more active employees, set priority and due time, and receive status updates. Employees can start, complete, or mark a task as blocked. Every assignment and status change is stored in an append-only event history, and task assignments generate in-app notifications plus Web Push for subscribed devices. Branch managers cannot assign or inspect tasks outside their branch; employees can only see and update their own assignments.


## Live portal synchronization

While an authenticated portal is open, employee and manager dashboard data refreshes automatically every 3 seconds without a full page reload. Notification inserts publish recipient-scoped PostgreSQL `NOTIFY` events through an authenticated Server-Sent Events (SSE) endpoint; the browser immediately refreshes the recipient's notifications and dashboard. The event contains no personal data, and each account listens on its own database channel. A 15-second polling fallback and EventSource automatic reconnection cover transient disconnects. The stream is short-lived and reconnects periodically to revalidate the session. Branch managers only receive events for notifications addressed to their own account; super-admin recipients follow the existing authorization scope.

SSE requires the page to remain open and a live network connection. It improves foreground delivery but is not Web Push and does not guarantee alerts while the browser/PWA is fully closed. Browser audio still requires an initial user gesture and notification permission is required for OS notifications.
