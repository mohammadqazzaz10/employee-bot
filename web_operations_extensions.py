"""Background meeting reminders and branch-scoped employee presence/activity APIs."""
import json
import logging
import re
import threading
import time

from flask import g, jsonify, request
from psycopg2.extras import RealDictCursor

from web_app import app, _db, _branch_allowed, _json_error, roles_required

logger = logging.getLogger("employee-web-operations")
_worker_thread = None
_worker_lock = threading.Lock()
_WORKER_ADVISORY_LOCK = 734821905771


def _process_due_meeting_reminders():
    """Fan out reminders from a server worker, independent of open browser sessions."""
    conn = _db()
    processed = 0
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(%s) AS acquired", (_WORKER_ADVISORY_LOCK,))
            if not cur.fetchone()["acquired"]:
                conn.rollback()
                return 0
            cur.execute(
                """SELECT id,branch_id,title,starts_at FROM web_meetings
                   WHERE status='scheduled' AND reminder_sent_at IS NULL
                     AND starts_at > CURRENT_TIMESTAMP
                     AND starts_at <= CURRENT_TIMESTAMP + INTERVAL '15 minutes'
                   ORDER BY starts_at LIMIT 50 FOR UPDATE SKIP LOCKED"""
            )
            due = cur.fetchall()
            for meeting in due:
                cur.execute(
                    """INSERT INTO web_notifications(recipient_user_id,employee_id,notification_type,title,body)
                       SELECT u.id,u.employee_id,'meeting_reminder','تذكير باجتماع: ' || %s,
                              'موعد الاجتماع خلال 15 دقيقة أو أقل.'
                       FROM web_users u JOIN employees e ON e.id=u.employee_id
                       WHERE u.role='employee' AND u.is_active=TRUE AND e.is_active=TRUE
                         AND e.branch_id=%s""",
                    (meeting["title"], meeting["branch_id"]),
                )
                cur.execute(
                    "UPDATE web_meetings SET reminder_sent_at=CURRENT_TIMESTAMP WHERE id=%s AND reminder_sent_at IS NULL",
                    (meeting["id"],),
                )
                processed += 1
        conn.commit()
        return processed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _meeting_reminder_worker_loop():
    """A DB advisory transaction lock prevents duplicate fan-out across Gunicorn workers."""
    while True:
        try:
            count = _process_due_meeting_reminders()
            if count:
                logger.info("Delivered reminders for %s meeting(s)", count)
        except Exception:
            logger.exception("Meeting reminder worker cycle failed; will retry")
        time.sleep(15)


def _ensure_meeting_reminder_worker():
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _worker_thread = threading.Thread(
            target=_meeting_reminder_worker_loop,
            name="meeting-reminder-worker",
            daemon=True,
        )
        _worker_thread.start()


app.before_request(_ensure_meeting_reminder_worker)


@app.post("/api/employee/presence")
@roles_required("employee")
def update_employee_presence():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    visible = payload.get("visible", True)
    if not isinstance(visible, bool):
        return _json_error("حالة ظهور الصفحة غير صالحة.", 400)
    page = str(payload.get("page") or "dashboard").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", page):
        page = "dashboard"

    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT e.id AS employee_id,e.branch_id,e.is_active
                   FROM employees e WHERE e.id=%s FOR UPDATE""",
                (g.current_user["employee_id"],),
            )
            employee = cur.fetchone()
            if not employee or not employee["is_active"]:
                conn.rollback()
                return _json_error("حساب الموظف غير نشط.", 403)
            cur.execute(
                """SELECT last_seen_at,is_visible,current_page
                   FROM web_employee_presence WHERE user_id=%s FOR UPDATE""",
                (g.current_user["id"],),
            )
            previous = cur.fetchone()
            event_type = None
            if not previous:
                event_type = "online"
            elif previous["last_seen_at"] < __import__("datetime").datetime.now(__import__("datetime").timezone.utc) - __import__("datetime").timedelta(minutes=2):
                event_type = "resumed"
            elif bool(previous["is_visible"]) != visible:
                event_type = "tab_visible" if visible else "tab_hidden"
            elif previous["current_page"] != page:
                event_type = "page_changed"
            cur.execute(
                """INSERT INTO web_employee_presence
                   (user_id,employee_id,branch_id,last_seen_at,is_visible,current_page,updated_at)
                   VALUES(%s,%s,%s,CURRENT_TIMESTAMP,%s,%s,CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id) DO UPDATE SET employee_id=EXCLUDED.employee_id,
                     branch_id=EXCLUDED.branch_id,last_seen_at=CURRENT_TIMESTAMP,
                     is_visible=EXCLUDED.is_visible,current_page=EXCLUDED.current_page,
                     updated_at=CURRENT_TIMESTAMP""",
                (g.current_user["id"], employee["employee_id"], employee["branch_id"], visible, page),
            )
            if event_type:
                cur.execute(
                    """INSERT INTO web_employee_activity(user_id,employee_id,branch_id,event_type,details)
                       VALUES(%s,%s,%s,%s,%s::jsonb)""",
                    (g.current_user["id"], employee["employee_id"], employee["branch_id"],
                     event_type, json.dumps({"page": page, "visible": visible}, ensure_ascii=False)),
                )
        conn.commit()
        return jsonify({"ok": True, "message": "تم تحديث حالة الاتصال."})
    finally:
        conn.close()


@app.get("/api/admin/employee-activity")
@roles_required("super_admin", "branch_manager", "supervisor")
def get_employee_activity():
    requested = request.args.get("branch_id", "").strip()
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if g.current_user["role"] == "super_admin":
                if requested:
                    try:
                        branch_id = int(requested)
                    except ValueError:
                        return _json_error("الفرع المحدد غير صالح.", 400)
                    if not _branch_allowed(cur, g.current_user, branch_id):
                        return _json_error("لا تملك صلاحية الوصول إلى هذا الفرع.", 403)
                    branch_filter, params = "AND e.branch_id=%s", (branch_id,)
                    activity_filter, activity_params = "AND a.branch_id=%s", (branch_id,)
                else:
                    branch_filter, params = "", ()
                    activity_filter, activity_params = "", ()
            else:
                branch_filter, params = "AND e.branch_id=%s", (g.current_user["branch_id"],)
                activity_filter, activity_params = "AND a.branch_id=%s", (g.current_user["branch_id"],)

            cur.execute(
                """SELECT e.id AS employee_id,e.employee_code,e.full_name,e.job_title,
                          p.last_seen_at,p.is_visible,p.current_page,
                          CASE
                            WHEN p.last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '90 seconds'
                                 AND p.is_visible THEN 'online'
                            WHEN p.last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '5 minutes' THEN 'idle'
                            ELSE 'offline'
                          END AS presence_status
                   FROM employees e
                   LEFT JOIN web_users u ON u.employee_id=e.id AND u.role='employee'
                   LEFT JOIN web_employee_presence p ON p.user_id=u.id
                   WHERE e.is_active=TRUE """ + branch_filter + """
                   ORDER BY CASE
                     WHEN p.last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '90 seconds' AND p.is_visible THEN 0
                     WHEN p.last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '5 minutes' THEN 1
                     ELSE 2 END, e.full_name""",
                params,
            )
            employees = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT a.id,a.employee_id,e.full_name,a.event_type,a.details,a.created_at
                   FROM web_employee_activity a
                   JOIN employees e ON e.id=a.employee_id
                   WHERE e.is_active=TRUE """ + activity_filter + """
                   ORDER BY a.created_at DESC LIMIT 60""",
                activity_params,
            )
            events = [dict(row) for row in cur.fetchall()]
        return jsonify({"ok": True, "employees": employees, "events": events})
    finally:
        conn.close()
