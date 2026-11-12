"""Authenticated Web Push subscription endpoints and durable queue delivery worker."""
import json
import logging
import os
import threading
import time

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("employee-web.push")
_worker_started = False
_worker_lock = threading.Lock()


def _connect():
    return psycopg2.connect(
        os.environ.get("DATABASE_URL", ""),
        sslmode=os.environ.get("DATABASE_SSLMODE", "prefer"),
        connect_timeout=10,
        application_name="employee-web-push",
    )


def register_push_delivery(app, auth_required, _json_error):
    """Register per-user subscription APIs and start the queue worker."""
    @app.get("/api/push/config")
    @auth_required
    def push_config():
        public_key = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
        private_key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
        return {"ok": True, "enabled": bool(public_key and private_key), "public_key": public_key or None}

    @app.post("/api/push/subscriptions")
    @auth_required
    def save_push_subscription():
        from flask import g, request
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        endpoint = str(body.get("endpoint") or "").strip()
        keys = body.get("keys") if isinstance(body.get("keys"), dict) else {}
        p256dh = str(keys.get("p256dh") or "").strip()
        auth = str(keys.get("auth") or "").strip()
        if not endpoint.startswith("https://") or len(endpoint) > 2048 or not p256dh or not auth:
            return _json_error("بيانات اشتراك الإشعارات غير صالحة.", 400)
        if not os.environ.get("VAPID_PUBLIC_KEY", "").strip() or not os.environ.get("VAPID_PRIVATE_KEY", "").strip():
            return _json_error("خدمة إشعارات الهاتف غير مهيأة على الخادم بعد.", 503)
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO web_push_subscriptions(user_id,endpoint,p256dh_key,auth_key)
                       VALUES(%s,%s,%s,%s)
                       ON CONFLICT(endpoint) DO UPDATE SET user_id=EXCLUDED.user_id,
                         p256dh_key=EXCLUDED.p256dh_key,auth_key=EXCLUDED.auth_key,
                         updated_at=CURRENT_TIMESTAMP""",
                    (g.current_user["id"], endpoint, p256dh, auth),
                )
            conn.commit()
            return {"ok": True, "message": "تم تفعيل إشعارات الهاتف لهذا الحساب."}
        finally:
            conn.close()

    @app.delete("/api/push/subscriptions")
    @auth_required
    def delete_push_subscription():
        from flask import g, request
        body = request.get_json(silent=True)
        endpoint = str((body or {}).get("endpoint") or "").strip()
        if not endpoint:
            return _json_error("عنوان الاشتراك غير صالح.", 400)
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM web_push_subscriptions WHERE user_id=%s AND endpoint=%s",
                            (g.current_user["id"], endpoint))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    _start_worker()


def _deliver_one():
    private_key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    subject = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com").strip()
    if not private_key or not os.environ.get("VAPID_PUBLIC_KEY", "").strip():
        return False
    from pywebpush import WebPushException, webpush

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT q.id AS queue_id,n.id AS notification_id,n.title,n.body,
                          n.notification_type,n.request_id,n.employee_id,
                          s.id AS subscription_id,s.endpoint,s.p256dh_key,s.auth_key
                   FROM web_push_queue q
                   JOIN web_notifications n ON n.id=q.notification_id
                   JOIN web_push_subscriptions s ON s.user_id=n.recipient_user_id
                   WHERE q.next_attempt_at<=CURRENT_TIMESTAMP
                     AND (q.locked_at IS NULL OR q.locked_at<CURRENT_TIMESTAMP-INTERVAL '2 minutes')
                   ORDER BY q.id,s.id
                   FOR UPDATE OF q SKIP LOCKED LIMIT 20"""
            )
            jobs = cur.fetchall()
            if not jobs:
                conn.rollback()
                return False
            queue_ids = sorted({job["queue_id"] for job in jobs})
            cur.execute("UPDATE web_push_queue SET locked_at=CURRENT_TIMESTAMP WHERE id=ANY(%s)", (queue_ids,))
        conn.commit()
    finally:
        conn.close()

    for job in jobs:
        payload = {
            "title": job["title"] or "تنبيه جديد",
            "body": job["body"] or "لديك إشعار جديد.",
            "notification_id": job["notification_id"],
            "notification_type": job["notification_type"],
            "request_id": job["request_id"],
            "employee_id": job["employee_id"],
            "url": "/",
            "tag": "employee-notification-" + str(job["notification_id"]),
        }
        subscription = {
            "endpoint": job["endpoint"],
            "keys": {"p256dh": job["p256dh_key"], "auth": job["auth_key"]},
        }
        try:
            webpush(
                subscription_info=subscription,
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},
                ttl=86400,
            )
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("UPDATE web_push_subscriptions SET last_success_at=CURRENT_TIMESTAMP WHERE id=%s",
                                (job["subscription_id"],))
                    cur.execute("DELETE FROM web_push_queue WHERE id=%s", (job["queue_id"],))
                conn.commit()
            finally:
                conn.close()
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    if status in (404, 410):
                        cur.execute("DELETE FROM web_push_subscriptions WHERE id=%s", (job["subscription_id"],))
                    cur.execute(
                        """UPDATE web_push_queue SET attempts=attempts+1,
                           next_attempt_at=CURRENT_TIMESTAMP +
                             (LEAST(3600, power(2, LEAST(attempts+1,11))::int) * INTERVAL '1 second'),
                           locked_at=NULL WHERE id=%s""",
                        (job["queue_id"],),
                    )
                    cur.execute("DELETE FROM web_push_queue WHERE id=%s AND attempts>=8",
                                (job["queue_id"],))
                conn.commit()
            finally:
                conn.close()
            logger.warning("Web Push delivery failed (status=%s): %s", status, exc)
        except Exception:
            logger.exception("Unexpected Web Push delivery error")
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE web_push_queue SET attempts=attempts+1,locked_at=NULL,
                           next_attempt_at=CURRENT_TIMESTAMP+INTERVAL '30 seconds' WHERE id=%s""",
                        (job["queue_id"],),
                    )
                conn.commit()
            finally:
                conn.close()
    return True


def _worker_loop():
    while True:
        try:
            worked = _deliver_one()
            if not worked:
                time.sleep(4)
        except Exception:
            logger.exception("Web Push queue worker cycle failed")
            time.sleep(8)


def _start_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker_loop, name="web-push-delivery", daemon=True)
        thread.start()
        _worker_started = True
        logger.info("Background Web Push delivery worker started")
