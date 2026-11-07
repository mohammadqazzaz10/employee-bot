-- Employee Bot: additive multi-branch foundation
-- IMPORTANT: review and back up the production database before applying.
-- This migration is intentionally NOT executed automatically by the bot.
BEGIN;

CREATE TABLE IF NOT EXISTS branches (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO branches (code, name)
VALUES ('main', 'الفرع الرئيسي')
ON CONFLICT (code) DO NOTHING;

ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS branch_id BIGINT;

UPDATE employees
SET branch_id = (SELECT id FROM branches WHERE code = 'main')
WHERE branch_id IS NULL;

ALTER TABLE employees
    ALTER COLUMN branch_id SET NOT NULL;

-- Keep legacy employee INSERT statements working after this migration.
-- New employees default to the initial branch until branch-aware admin flows ship.
CREATE OR REPLACE FUNCTION assign_default_employee_branch()
RETURNS TRIGGER AS $
BEGIN
    IF NEW.branch_id IS NULL THEN
        SELECT id INTO NEW.branch_id FROM branches WHERE code = 'main';
    END IF;
    IF NEW.branch_id IS NULL THEN
        RAISE EXCEPTION 'Default branch is not configured';
    END IF;
    RETURN NEW;
END;
$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_employees_default_branch ON employees;
CREATE TRIGGER trg_employees_default_branch
BEFORE INSERT ON employees
FOR EACH ROW EXECUTE FUNCTION assign_default_employee_branch();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'employees_branch_id_fkey'
    ) THEN
        ALTER TABLE employees
            ADD CONSTRAINT employees_branch_id_fkey
            FOREIGN KEY (branch_id) REFERENCES branches(id)
            ON UPDATE RESTRICT ON DELETE RESTRICT;
    END IF;
END $$;

ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE requests
    ADD COLUMN IF NOT EXISTS branch_id BIGINT;

UPDATE requests AS r
SET branch_id = e.branch_id
FROM employees AS e
WHERE r.employee_id = e.id
  AND r.branch_id IS NULL;

-- Requests without a linked employee are retained; they must be reviewed
-- before branch isolation is enforced in application queries.
ALTER TABLE requests
    ADD COLUMN IF NOT EXISTS decided_by BIGINT,
    ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

-- Attach new requests to their employee's branch when callers use the legacy INSERT shape.
CREATE OR REPLACE FUNCTION assign_request_employee_branch()
RETURNS TRIGGER AS $
BEGIN
    IF NEW.branch_id IS NULL AND NEW.employee_id IS NOT NULL THEN
        SELECT branch_id INTO NEW.branch_id
        FROM employees WHERE id = NEW.employee_id;
    END IF;
    RETURN NEW;
END;
$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_requests_employee_branch ON requests;
CREATE TRIGGER trg_requests_employee_branch
BEFORE INSERT ON requests
FOR EACH ROW EXECUTE FUNCTION assign_request_employee_branch();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'requests_branch_id_fkey'
    ) THEN
        ALTER TABLE requests
            ADD CONSTRAINT requests_branch_id_fkey
            FOREIGN KEY (branch_id) REFERENCES branches(id)
            ON UPDATE RESTRICT ON DELETE RESTRICT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_employees_branch_active
    ON employees (branch_id, is_active, full_name);
CREATE INDEX IF NOT EXISTS idx_requests_branch_status_date
    ON requests (branch_id, status, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_requests_employee_date
    ON requests (employee_id, requested_at DESC);

-- Branch-scoped admin roles. Existing admins remain global until explicitly
-- assigned roles and the application is updated to enforce this table.
CREATE TABLE IF NOT EXISTS admin_branch_roles (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES admins(telegram_id) ON DELETE CASCADE,
    branch_id BIGINT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    role VARCHAR(30) NOT NULL CHECK (role IN ('branch_manager', 'supervisor')),
    granted_by BIGINT,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (telegram_id, branch_id)
);

CREATE INDEX IF NOT EXISTS idx_admin_branch_roles_branch
    ON admin_branch_roles (branch_id, role);

COMMIT;
