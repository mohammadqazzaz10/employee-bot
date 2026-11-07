-- Auditable log of manager-to-employee announcements.
BEGIN;
CREATE TABLE IF NOT EXISTS broadcast_logs (
    id BIGSERIAL PRIMARY KEY,
    sent_by BIGINT NOT NULL,
    audience VARCHAR(40) NOT NULL,
    content_type VARCHAR(12) NOT NULL CHECK (content_type IN ('text','voice','audio')),
    message_preview TEXT,
    recipient_count INTEGER NOT NULL DEFAULT 0,
    sent_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_broadcast_logs_created_at
    ON broadcast_logs (created_at DESC);
COMMIT;
