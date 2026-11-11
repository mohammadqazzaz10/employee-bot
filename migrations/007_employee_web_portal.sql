-- Employee web portal: employee credentials, sessions, activation, requests and timers.
-- Additive only: preserves Telegram identities and all existing operational history.
BEGIN;

ALTER TABLE employees ADD COLUMN IF NOT EXISTS employee_code VARCHAR(24);
UPDATE employees
SET employee_code = 'EMP' || LPAD(id::TEXT, 5, '0')
WHERE employee_code IS NULL OR BTRIM(employee_code) = '';
ALTER TABLE employees ALTER COLUMN employee_code SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_employees_employee_code
    ON employees (UPPER(employee_code));

CREATE TABLE IF NOT EXISTS web_users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(64) NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(24) NOT NULL CHECK (role IN ('employee','supervisor','branch_manager','super_admin')),
    employee_id INTEGER UNIQUE REFERENCES employees(id) ON DELETE RESTRICT,
    branch_id BIGINT REFERENCES branches(id) ON DELETE RESTRICT,
    full_name VARCHAR(120) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    failed_login_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_login_attempts >= 0),
    locked_until TIMESTAMPTZ,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT web_users_role_scope_check CHECK (
        (role = 'employee' AND employee_id IS NOT NULL AND branch_id IS NULL)
        OR (role IN ('supervisor','branch_manager') AND employee_id IS NULL AND branch_id IS NOT NULL)
        OR (role = 'super_admin' AND employee_id IS NULL AND branch_id IS NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_web_users_username_ci ON web_users (LOWER(username));
CREATE INDEX IF NOT EXISTS idx_web_users_branch_role ON web_users (branch_id, role, is_active);

ALTER TABLE requests
    ADD COLUMN IF NOT EXISTS decided_by_web_user_id BIGINT REFERENCES web_users(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS web_sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES web_users(id) ON DELETE CASCADE,
    token_hash CHAR(64) NOT NULL UNIQUE,
    csrf_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    ip_address INET,
    user_agent VARCHAR(300)
);
CREATE INDEX IF NOT EXISTS idx_web_sessions_user_active
    ON web_sessions (user_id, expires_at DESC) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS web_login_throttles (
    ip_hash CHAR(64) PRIMARY KEY,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    window_started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    blocked_until TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS web_activation_tokens (
    id BIGSERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    token_hash CHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_by BIGINT REFERENCES web_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_web_activation_employee
    ON web_activation_tokens (employee_id, expires_at DESC) WHERE used_at IS NULL;

CREATE TABLE IF NOT EXISTS web_timers (
    id BIGSERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    request_id INTEGER NOT NULL UNIQUE REFERENCES requests(id) ON DELETE RESTRICT,
    request_type VARCHAR(50) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ends_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    returned_at TIMESTAMPTZ,
    created_by BIGINT NOT NULL REFERENCES web_users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_web_timers_employee_active
    ON web_timers (employee_id)
    WHERE returned_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_web_timers_active_end
    ON web_timers (ends_at)
    WHERE completed_at IS NULL AND returned_at IS NULL;

CREATE TABLE IF NOT EXISTS web_notifications (
    id BIGSERIAL PRIMARY KEY,
    recipient_user_id BIGINT NOT NULL REFERENCES web_users(id) ON DELETE CASCADE,
    employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    request_id INTEGER REFERENCES requests(id) ON DELETE CASCADE,
    notification_type VARCHAR(32) NOT NULL,
    title VARCHAR(160) NOT NULL,
    body TEXT NOT NULL,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_web_notifications_recipient
    ON web_notifications (recipient_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS web_audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor_user_id BIGINT REFERENCES web_users(id) ON DELETE SET NULL,
    branch_id BIGINT REFERENCES branches(id) ON DELETE SET NULL,
    action VARCHAR(64) NOT NULL,
    target_type VARCHAR(40),
    target_id VARCHAR(64),
    details JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_web_audit_branch_date
    ON web_audit_log (branch_id, created_at DESC);

COMMIT;
