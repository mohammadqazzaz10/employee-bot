-- Admin controls: employee blocking, branch rename/activation, broadcasts and meetings.
BEGIN;

CREATE TABLE IF NOT EXISTS web_meetings (
    id BIGSERIAL PRIMARY KEY,
    branch_id BIGINT NOT NULL REFERENCES branches(id) ON DELETE RESTRICT,
    title VARCHAR(160) NOT NULL,
    agenda TEXT NOT NULL DEFAULT '',
    location VARCHAR(180) NOT NULL DEFAULT '',
    starts_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled','cancelled','completed')),
    reminder_sent_at TIMESTAMPTZ,
    created_by BIGINT REFERENCES web_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_web_meetings_branch_start
    ON web_meetings(branch_id, starts_at) WHERE status='scheduled';

CREATE INDEX IF NOT EXISTS idx_web_notifications_unread
    ON web_notifications(recipient_user_id, created_at DESC) WHERE read_at IS NULL;

COMMIT;
