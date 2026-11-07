-- Distinguish countdown expiry from the employee's explicit return confirmation.
BEGIN;

ALTER TABLE active_timers
    ADD COLUMN IF NOT EXISTS returned_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_active_timers_unreturned
    ON active_timers (user_id)
    WHERE returned_at IS NULL;

COMMIT;
