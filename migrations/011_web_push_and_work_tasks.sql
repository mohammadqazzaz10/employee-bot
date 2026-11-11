-- Web Push delivery queue and branch-scoped work task tracking.
BEGIN;

CREATE TABLE IF NOT EXISTS web_push_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES web_users(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh_key TEXT NOT NULL,
    auth_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_success_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_web_push_subscriptions_user
    ON web_push_subscriptions(user_id);

CREATE TABLE IF NOT EXISTS web_push_queue (
    id BIGSERIAL PRIMARY KEY,
    notification_id BIGINT NOT NULL UNIQUE REFERENCES web_notifications(id) ON DELETE CASCADE,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    locked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_web_push_queue_ready
    ON web_push_queue(next_attempt_at, id) WHERE locked_at IS NULL;

CREATE OR REPLACE FUNCTION enqueue_web_push_notification()
RETURNS TRIGGER AS $push_queue$
BEGIN
    INSERT INTO web_push_queue(notification_id)
    VALUES(NEW.id)
    ON CONFLICT(notification_id) DO NOTHING;
    RETURN NEW;
END;
$push_queue$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_web_notifications_push_queue ON web_notifications;
CREATE TRIGGER trg_web_notifications_push_queue
AFTER INSERT ON web_notifications
FOR EACH ROW EXECUTE FUNCTION enqueue_web_push_notification();

CREATE TABLE IF NOT EXISTS work_tasks (
    id BIGSERIAL PRIMARY KEY,
    branch_id BIGINT NOT NULL REFERENCES branches(id) ON DELETE RESTRICT,
    title VARCHAR(160) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    priority VARCHAR(12) NOT NULL DEFAULT 'normal'
        CHECK (priority IN ('low','normal','high','urgent')),
    due_at TIMESTAMPTZ,
    created_by BIGINT NOT NULL REFERENCES web_users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_work_tasks_branch_due
    ON work_tasks(branch_id, due_at, created_at DESC)
    WHERE archived_at IS NULL;

CREATE TABLE IF NOT EXISTS work_task_assignments (
    id BIGSERIAL PRIMARY KEY,
    task_id BIGINT NOT NULL REFERENCES work_tasks(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','in_progress','completed','blocked','cancelled')),
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    UNIQUE(task_id, employee_id)
);
CREATE INDEX IF NOT EXISTS idx_work_task_assignments_employee
    ON work_task_assignments(employee_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS work_task_events (
    id BIGSERIAL PRIMARY KEY,
    assignment_id BIGINT NOT NULL REFERENCES work_task_assignments(id) ON DELETE CASCADE,
    actor_user_id BIGINT REFERENCES web_users(id) ON DELETE SET NULL,
    event_type VARCHAR(24) NOT NULL,
    from_status VARCHAR(16),
    to_status VARCHAR(16),
    note VARCHAR(1000) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_work_task_events_assignment
    ON work_task_events(assignment_id, created_at DESC);

COMMIT;
