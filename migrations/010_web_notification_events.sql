-- Deliver a lightweight, recipient-scoped wake-up event after a notification commits.
-- The event contains no personal data; clients fetch their own notifications through the
-- existing authenticated endpoint. PostgreSQL delivers NOTIFY only after transaction commit.
BEGIN;

CREATE OR REPLACE FUNCTION notify_web_notification_recipient()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $notification_event$
BEGIN
    PERFORM pg_notify(
        'employee_web_user_' || NEW.recipient_user_id::TEXT,
        'refresh'
    );
    RETURN NEW;
END;
$notification_event$;

DROP TRIGGER IF EXISTS trg_web_notifications_notify_recipient ON web_notifications;
CREATE TRIGGER trg_web_notifications_notify_recipient
AFTER INSERT ON web_notifications
FOR EACH ROW
EXECUTE FUNCTION notify_web_notification_recipient();

COMMIT;
