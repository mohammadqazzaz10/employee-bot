-- Employee presence/activity tracking and operational worker support.
BEGIN;

CREATE TABLE IF NOT EXISTS web_employee_presence (
    user_id BIGINT PRIMARY KEY REFERENCES web_users(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL UNIQUE REFERENCES employees(id) ON DELETE CASCADE,
    branch_id BIGINT NOT NULL REFERENCES branches(id) ON DELETE RESTRICT,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_visible BOOLEAN NOT NULL DEFAULT TRUE,
    current_page VARCHAR(80) NOT NULL DEFAULT 'dashboard',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_web_employee_presence_branch_seen
    ON web_employee_presence(branch_id, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS web_employee_activity (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES web_users(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    branch_id BIGINT NOT NULL REFERENCES branches(id) ON DELETE RESTRICT,
    event_type VARCHAR(32) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_web_employee_activity_branch_time
    ON web_employee_activity(branch_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_web_employee_activity_employee_time
    ON web_employee_activity(employee_id, created_at DESC);

COMMIT;
