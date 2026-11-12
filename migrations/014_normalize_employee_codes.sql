-- Normalize legacy employee-facing codes without changing internal employee IDs.
-- Existing web accounts keep working because their usernames are updated in the same transaction.
BEGIN;

CREATE TABLE IF NOT EXISTS employee_code_history (
    id BIGSERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    old_employee_code VARCHAR(24) NOT NULL,
    new_employee_code VARCHAR(24) NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (employee_id, old_employee_code, new_employee_code)
);

-- Fail safely rather than silently taking over a non-employee account username.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT id, 'EMP' || LPAD(ROW_NUMBER() OVER (ORDER BY id)::TEXT,
                                     GREATEST(5, LENGTH(ROW_NUMBER() OVER (ORDER BY id)::TEXT)), '0') AS new_code
            FROM employees
        ) ranked
        JOIN web_users u ON LOWER(u.username) = LOWER(ranked.new_code)
        WHERE u.role <> 'employee'
           OR u.employee_id IS DISTINCT FROM ranked.id
    ) THEN
        RAISE EXCEPTION 'Employee code normalization stopped: a target code is already used by another web account';
    END IF;
END $$;

WITH ranked AS (
    SELECT id,
           'EMP' || LPAD(ROW_NUMBER() OVER (ORDER BY id)::TEXT,
                         GREATEST(5, LENGTH(ROW_NUMBER() OVER (ORDER BY id)::TEXT)), '0') AS new_code
    FROM employees
)
INSERT INTO employee_code_history (employee_id, old_employee_code, new_employee_code)
SELECT e.id, e.employee_code, ranked.new_code
FROM employees e
JOIN ranked ON ranked.id = e.id
WHERE e.employee_code IS DISTINCT FROM ranked.new_code
ON CONFLICT (employee_id, old_employee_code, new_employee_code) DO NOTHING;

WITH ranked AS (
    SELECT id,
           'EMP' || LPAD(ROW_NUMBER() OVER (ORDER BY id)::TEXT,
                         GREATEST(5, LENGTH(ROW_NUMBER() OVER (ORDER BY id)::TEXT)), '0') AS new_code
    FROM employees
)
UPDATE employees e
SET employee_code = ranked.new_code
FROM ranked
WHERE ranked.id = e.id
  AND e.employee_code IS DISTINCT FROM ranked.new_code;

-- Employee usernames are their employee codes; synchronize them atomically.
UPDATE web_users u
SET username = e.employee_code,
    updated_at = CURRENT_TIMESTAMP
FROM employees e
WHERE u.role = 'employee'
  AND u.employee_id = e.id
  AND u.username IS DISTINCT FROM e.employee_code;

-- Future employees continue the same global sequence across all branches.
CREATE SEQUENCE IF NOT EXISTS employee_code_seq;
SELECT setval(
    'employee_code_seq',
    GREATEST((SELECT COUNT(*) FROM employees), 1),
    (SELECT COUNT(*) > 0 FROM employees)
);

CREATE OR REPLACE FUNCTION assign_employee_web_code()
RETURNS TRIGGER AS $employee_code$
DECLARE
    next_code_number BIGINT;
BEGIN
    IF NEW.employee_code IS NULL OR BTRIM(NEW.employee_code) = '' THEN
        next_code_number := nextval('employee_code_seq');
        NEW.employee_code := 'EMP' || LPAD(
            next_code_number::TEXT,
            GREATEST(5, LENGTH(next_code_number::TEXT)),
            '0'
        );
    END IF;
    RETURN NEW;
END;
$employee_code$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_employees_assign_web_code ON employees;
CREATE TRIGGER trg_employees_assign_web_code
BEFORE INSERT ON employees
FOR EACH ROW EXECUTE FUNCTION assign_employee_web_code();

COMMIT;
