# Employee Portal (PWA)

Arabic RTL employee-facing Progressive Web App for the existing Employee Bot.

## Current implementation stage

This branch introduces the responsive employee portal shell, installable PWA manifest, app icon, and service-worker shell caching. It is deliberately **not production-authentication-ready yet**: the browser expects these same-origin API endpoints:

- `POST /api/auth/login` with `employee_code` and `password`
- `GET /api/auth/me`
- `POST /api/auth/logout`

Those endpoints, credential provisioning, manager approval flows, and database migration are not included in this UI-only stage. Do not publish this branch as the live employee portal or distribute credentials until the API and authorization work is completed and tested.

## Design and safety decisions

- Arabic RTL, responsive mobile-first layout.
- PWA install prompt; no app-store publication required.
- API requests use same-origin cookies and never store credentials in localStorage.
- The service worker never caches `/api/*` responses.
- Employee identity, branch, role, and permissions must be derived server-side from the authenticated session, never from browser-supplied IDs.
- Existing Telegram worker and PostgreSQL records remain unchanged.

## Next required implementation

1. Add additive migration for employee web credentials and revocable sessions / activation codes.
2. Implement secure authentication (slow password hashing, rate limiting, secure HttpOnly cookies, CSRF protection, session revocation).
3. Implement employee self-service endpoints using existing request, branch, request-type, and timer rules.
4. Implement manager web views and atomic branch-scoped approvals.
5. Add audit events, security tests, PostgreSQL integration tests, and end-to-end browser tests.
6. Add a Render Web Service for the API + static PWA only after the above checks pass. Keep the Telegram Background Worker as a separate service.

Never put real secrets, employee passwords, phone numbers, or database URLs in Git.
