-- Configurable branch-scoped request buttons (toilet, prayer, and admin-created types).
BEGIN;

CREATE TABLE IF NOT EXISTS active_timers (
    user_id BIGINT PRIMARY KEY,
    message_id BIGINT NOT NULL,
    timer_type VARCHAR(40) NOT NULL,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE active_timers ALTER COLUMN timer_type TYPE VARCHAR(40);

CREATE TABLE IF NOT EXISTS branch_request_types (
    id BIGSERIAL PRIMARY KEY,
    branch_id BIGINT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    code VARCHAR(40) NOT NULL,
    label VARCHAR(32) NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes BETWEEN 1 AND 240),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT,
    updated_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (branch_id, code),
    UNIQUE (branch_id, label)
);

CREATE TABLE IF NOT EXISTS branch_request_types_audit (
    id BIGSERIAL PRIMARY KEY,
    branch_id BIGINT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    request_type_id BIGINT NOT NULL REFERENCES branch_request_types(id) ON DELETE CASCADE,
    action VARCHAR(30) NOT NULL,
    changed_by BIGINT NOT NULL,
    new_value TEXT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO branch_request_types (branch_id, code, label, duration_minutes, created_by, updated_by)
SELECT b.id, defaults.code, defaults.label, 10, NULL, NULL
FROM branches b
CROSS JOIN (VALUES ('bathroom', '🚻 استئذان الذهاب إلى الحمام'),
                   ('prayer', '🕌 استئذان الذهاب إلى الصلاة')) AS defaults(code, label)
WHERE b.is_active = TRUE
ON CONFLICT (branch_id, code) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_branch_request_types_active
    ON branch_request_types (branch_id, is_active);

COMMIT;
