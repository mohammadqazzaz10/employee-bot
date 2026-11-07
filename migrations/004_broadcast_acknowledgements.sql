-- Durable per-recipient acknowledgement and repeated Telegram reminders.
BEGIN;

CREATE TABLE IF NOT EXISTS broadcast_recipients (
    id BIGSERIAL PRIMARY KEY,
    broadcast_id BIGINT NOT NULL REFERENCES broadcast_logs(id) ON DELETE CASCADE,
    telegram_id BIGINT NOT NULL,
    delivery_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (delivery_status IN ('pending', 'delivered', 'failed', 'acknowledged')),
    delivered_at TIMESTAMPTZ,
    acknowledged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (broadcast_id, telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_broadcast_recipients_pending
    ON broadcast_recipients (delivery_status, delivered_at)
    WHERE delivery_status = 'delivered';

COMMIT;
