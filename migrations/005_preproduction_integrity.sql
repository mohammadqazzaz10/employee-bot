-- Pre-production integrity constraints. Apply only after migrations 001-004.
BEGIN;

-- Do not silently choose between duplicate active employee phone records.
DO $$
DECLARE duplicate_phones TEXT;
BEGIN
    SELECT string_agg(phone_number, ', ' ORDER BY phone_number)
      INTO duplicate_phones
    FROM (
        SELECT phone_number
        FROM employees
        WHERE is_active = TRUE
        GROUP BY phone_number
        HAVING COUNT(*) > 1
        ORDER BY phone_number
        LIMIT 20
    ) duplicates;
    IF duplicate_phones IS NOT NULL THEN
        RAISE EXCEPTION
            'Migration 005 blocked: duplicate active employee phone numbers: %. Resolve duplicates before retrying.',
            duplicate_phones;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_employees_active_phone
    ON employees (phone_number)
    WHERE is_active = TRUE;

-- Every branch must have a settings row before employees can use its policy.
INSERT INTO branch_settings (branch_id)
SELECT id FROM branches
ON CONFLICT (branch_id) DO NOTHING;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM branch_settings bs
        LEFT JOIN branches b ON b.id = bs.branch_id
        WHERE b.id IS NULL
    ) THEN
        RAISE EXCEPTION
            'Migration 005 blocked: orphaned branch_settings rows exist. Review them before retrying.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'branch_settings_branch_id_fkey'
          AND conrelid = 'branch_settings'::regclass
    ) THEN
        ALTER TABLE branch_settings
            ADD CONSTRAINT branch_settings_branch_id_fkey
            FOREIGN KEY (branch_id) REFERENCES branches(id)
            ON UPDATE RESTRICT ON DELETE RESTRICT;
    END IF;
END $$;

-- A durable outbox prevents an approved break from being lost if Telegram
-- message delivery or timer persistence temporarily fails after the decision.
CREATE TABLE IF NOT EXISTS timer_start_outbox (
    id BIGSERIAL PRIMARY KEY,
    request_id INTEGER NOT NULL UNIQUE REFERENCES requests(id) ON DELETE CASCADE,
    employee_telegram_id BIGINT NOT NULL,
    timer_type VARCHAR(40) NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes BETWEEN 1 AND 240),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_timer_start_outbox_created
    ON timer_start_outbox (created_at);

CREATE INDEX IF NOT EXISTS idx_active_timers_end_at
    ON active_timers (end_at)
    WHERE completed = FALSE;

CREATE INDEX IF NOT EXISTS idx_requests_pending_employee
    ON requests (employee_id, requested_at DESC)
    WHERE status = 'pending';

COMMIT;
