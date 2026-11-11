# Security and operational review — web-first portal

## Scope and limitations

This review covers the current feature branch and its automated source/contract/PostgreSQL tests. It is not a penetration test. It does not verify a live Render service, production database, real employee devices, or notification delivery while the browser is closed.

## Web portal controls implemented

1. **Account provisioning:** a super-admin is created once using a temporary high-entropy WEB_BOOTSTRAP_TOKEN. Bootstrap uses a PostgreSQL advisory transaction lock and refuses a second initialization after any web account exists.
2. **Employee activation:** employees are pre-registered by an authorized manager. Activation codes are random, stored only as SHA-256 hashes, expire after 24 hours, and are single-use.
3. **Password storage:** Werkzeug scrypt password hashes; plaintext passwords are not persisted.
4. **Login protection:** per-account lockout after repeated failures and per-IP throttling with hashed IP identifiers.
5. **Sessions:** opaque random session tokens stored as hashes, 12-hour expiry, revocation on logout, HttpOnly session cookie, Secure cookie in production, and SameSite=Lax.
6. **CSRF:** mutating authenticated requests require a double-submit CSRF token that is also bound to the hashed session record.
7. **Temporary passwords:** manager-created accounts must change their temporary password before using protected portal APIs.
8. **Identity and branch scope:** the server resolves employee identity, role, and branch from the authenticated session. Browser-supplied employee IDs do not grant access. Managers and supervisors are limited to their assigned branch.
9. **Request decisions:** pending requests are row-locked and can be decided once. Web decisions use decided_by_web_user_id, leaving legacy Telegram actor IDs untouched.
10. **Timers:** a timer is created in the same transaction as approval. One unreturned timer per employee is enforced by a partial unique index; expiry is not treated as return until the employee confirms.
11. **Audit and notifications:** web actions are audited; notifications are scoped to the relevant employee/branch.
12. **Service worker:** API paths are excluded from caching, so authenticated responses and personal data are not cached offline.

## Database and deployment

- Migration 007 is additive and retains employees, requests, branches, Telegram IDs, and legacy tables.
- Existing employee inserts that omit employee_code remain compatible through a database trigger.
- Render Blueprint is web-first: it provisions a Python Web Service and PostgreSQL without requiring Telegram credentials or starting a Telegram worker.
- Render runs python -m scripts.bootstrap before the web service starts and Gunicorn serves web_app:app.
- The first administrator is created at /setup; the bootstrap token must be removed from Render immediately after setup.

## Remaining production gates

1. Run the latest GitHub Actions checks on the exact PR head and fix all failures.
2. Deploy to Render staging and verify health checks and migration checksums.
3. Test account bootstrap, activation, lockout, CSRF rejection, session expiry, and logout against the real staging database.
4. Test branch isolation with two branches and two branch managers.
5. Test simultaneous employee requests and manager decisions, timer expiry, and return acknowledgement.
6. Verify backup and restore before connecting an existing database.
7. Test mobile PWA installation and foreground notification polling on actual Android/iOS devices.
8. If notifications are required while the PWA is closed, add and test Web Push separately; current in-app notification refresh works while the portal is open.
9. Keep the Telegram worker disabled until it is deliberately configured and tested as a separate service.

A green CI run is not a live deployment or a penetration test. Do not distribute employee activation codes until staging checks and backup/restore are complete.
