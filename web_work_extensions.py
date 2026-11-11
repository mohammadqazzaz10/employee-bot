"""Authenticated Web Push subscriptions and branch-scoped employee task workflows."""
import base64
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

from flask import g, jsonify, request
from psycopg2.extras import RealDictCursor

from web_app import app, _db, _audit, _branch_allowed, _json_error, auth_required, roles_required

logger = logging.getLogger("employee-web-push")
_push_thread = None
_push_thread_lock = threading.Lock()
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com").strip()


def _b64url_valid(value, maximum=512):
    if not isinstance(value, str) or not value or len(value) > maximum:
        return False
    try:
        base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        return True
    except Exception:
        return False


def _push_worker_loop():
    """Deliver queued notifications outside request threads; SKIP LOCKED supports multiple workers."""
    while True:
        row = None
        subscriptions = []
        try:
            conn = _db()
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """SELECT q.id AS queue_id,q.attempts,n.id,n.recipient_user_id,n.title,n.body
                           FROM web_push_queue q JOIN web_notifications n ON n.id=q.notification_id
                           WHERE q.next_attempt_at<=CURRENT_TIMESTAMP
                             AND (q.locked_at IS NULL OR q.locked_at<CURRENT_TIMESTAMP-INTERVAL '2 minutes')
                           ORDER BY q.id LIMIT 1 FOR UPDATE OF q SKIP LOCKED"""
                    )
                    row = cur.fetchone()
                    if row:
                        cur.execute("UPDATE web_push_queue SET locked_at=CURRENT_TIMESTAMP,attempts=attempts+1 WHERE id=%s",
                                    (row["queue_id"],))
                        cur.execute(
                            """SELECT endpoint,p256dh_key,auth_key FROM web_push_subscriptions
                               WHERE user_id=%s ORDER BY id""", (row["recipient_user_id"],)
                        )
                        subscriptions = cur.fetchall()
                conn.commit()
            finally:
                conn.close()

            if not row:
                time.sleep(2)
                continue
            if not subscriptions:
                conn = _db()
                try:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM web_push_queue WHERE id=%s", (row["queue_id"],))
                    conn.commit()
                finally:
                    conn.close()
                continue

            from pywebpush import WebPushException, webpush
            transient_failure = False
            successful_endpoints = []
            invalid_endpoints = []
            payload = json.dumps({
                "title": row["title"],
                "body": row["body"],
                "url": "/",
                "tag": "employee-notification-" + str(row["id"]),
            }, ensure_ascii=False)
            for subscription in subscriptions:
                info = {
                    "endpoint": subscription["endpoint"],
                    "keys": {"p256dh": subscription["p256dh_key"], "auth": subscription["auth_key"]},
                }
                try:
                    webpush(
                        subscription_info=info,
                        data=payload,
                        vapid_private_key=VAPID_PRIVATE_KEY,
                        vapid_claims={"sub": VAPID_SUBJECT},
                        ttl=86400,
                    )
                    successful_endpoints.append(subscription["endpoint"])
                except WebPushException as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status in (404, 410):
                        invalid_endpoints.append(subscription["endpoint"])
                    else:
                        transient_failure = True
                except Exception:
                    transient_failure = True

            conn = _db()
            try:
                with conn.cursor() as cur:
                    for endpoint in successful_endpoints:
                        cur.execute("UPDATE web_push_subscriptions SET last_success_at=CURRENT_TIMESTAMP WHERE endpoint=%s",
                                    (endpoint,))
                    for endpoint in invalid_endpoints:
                        cur.execute("DELETE FROM web_push_subscriptions WHERE endpoint=%s", (endpoint,))
                    if transient_failure and int(row["attempts"]) < 4:
                        cur.execute(
                            """UPDATE web_push_queue SET locked_at=NULL,
                               next_attempt_at=CURRENT_TIMESTAMP + (LEAST(attempts,4) * INTERVAL '30 seconds')
                               WHERE id=%s""", (row["queue_id"],)
                        )
                    else:
                        cur.execute("DELETE FROM web_push_queue WHERE id=%s", (row["queue_id"],))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.exception("Web Push worker cycle failed; retrying without exposing secrets")
            time.sleep(5)


def _ensure_push_worker():
    global _push_thread
    if not (VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY):
        return
    if _push_thread and _push_thread.is_alive():
        return
    with _push_thread_lock:
        if _push_thread and _push_thread.is_alive():
            return
        _push_thread = threading.Thread(target=_push_worker_loop, name="web-push-delivery", daemon=True)
        _push_thread.start()


app.before_request(_ensure_push_worker)


@app.get("/api/push/config")
@auth_required
def push_config():
    return jsonify({"ok": True, "enabled": bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY),
                    "public_key": VAPID_PUBLIC_KEY if VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY else None})


@app.post("/api/push/subscriptions")
@auth_required
def save_push_subscription():
    if not (VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY):
        return _json_error("خدمة الإشعارات الفورية غير مهيأة على الخادم.", 503)
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    endpoint = payload.get("endpoint")
    keys = payload.get("keys") if isinstance(payload.get("keys"), dict) else {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if (not isinstance(endpoint, str) or not endpoint.startswith("https://") or len(endpoint) > 2048
            or not _b64url_valid(p256dh, 256) or not _b64url_valid(auth, 128)):
        return _json_error("بيانات اشتراك الإشعارات غير صالحة.", 400)
    conn = _db()
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
            _audit(cur, g.current_user["id"], g.current_user.get("branch_id"),
                   "push_subscription_saved", "web_user", g.current_user["id"])
        conn.commit()
        return jsonify({"ok": True, "message": "تم تفعيل الإشعارات على هذا الجهاز."}), 201
    finally:
        conn.close()


@app.delete("/api/push/subscriptions")
@auth_required
def delete_push_subscription():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    endpoint = payload.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        return _json_error("بيانات الجهاز غير صالحة.", 400)
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM web_push_subscriptions WHERE user_id=%s AND endpoint=%s",
                        (g.current_user["id"], endpoint))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.get("/api/work/tasks")
@auth_required
def list_work_tasks():
    user = g.current_user
    requested_branch = request.args.get("branch_id")
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if user["role"] == "employee":
                cur.execute(
                    """SELECT e.branch_id FROM employees e WHERE e.id=%s""", (user["employee_id"],)
                )
                emp = cur.fetchone()
                if not emp:
                    return _json_error("ملف الموظف غير موجود.", 404)
                branch_filter = "AND t.branch_id=%s"
                values = (user["employee_id"], emp["branch_id"])
                employee_filter = "AND a.employee_id=%s"
                values = (user["employee_id"], emp["branch_id"], user["employee_id"])
            else:
                if user["role"] == "super_admin":
                    if requested_branch:
                        try:
                            branch_id = int(requested_branch)
                        except ValueError:
                            return _json_error("الفرع المحدد غير صالح.", 400)
                        if not _branch_allowed(cur, user, branch_id):
                            return _json_error("لا تملك صلاحية الوصول إلى هذا الفرع.", 403)
                        branch_filter = "AND t.branch_id=%s"
                        values = (branch_id,)
                    else:
                        branch_filter = ""
                        values = ()
                else:
                    branch_filter = "AND t.branch_id=%s"
                    values = (user["branch_id"],)
                employee_filter = ""
            query = """SELECT t.id AS task_id,t.branch_id,t.title,t.description,t.priority,t.due_at,
                              t.created_at,t.created_by,u.full_name AS creator_name,
                              a.id AS assignment_id,a.employee_id,a.status,a.assigned_at,a.updated_at,
                              a.completed_at,e.full_name AS employee_name,e.employee_code
                       FROM work_tasks t
                       JOIN work_task_assignments a ON a.task_id=t.id
                       JOIN employees e ON e.id=a.employee_id
                       JOIN web_users u ON u.id=t.created_by
                       WHERE t.archived_at IS NULL """ + branch_filter + " " + employee_filter + """
                       ORDER BY CASE a.status WHEN 'pending' THEN 0 WHEN 'in_progress' THEN 1
                         WHEN 'blocked' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END,
                         t.due_at NULLS LAST,t.created_at DESC"""
            cur.execute(query, values)
            rows = [dict(row) for row in cur.fetchall()]
        return jsonify({"ok": True, "tasks": rows})
    finally:
        conn.close()


@app.post("/api/admin/tasks")
@roles_required("super_admin", "branch_manager", "supervisor")
def create_work_task():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    title = str(payload.get("title") or "").strip()
    description = str(payload.get("description") or "").strip()
    priority = payload.get("priority", "normal")
    employee_ids = payload.get("employee_ids")
    requested_branch = payload.get("branch_id") or g.current_user.get("branch_id")
    due_at = payload.get("due_at")
    if not title or len(title) > 160 or len(description) > 4000:
        return _json_error("أدخل عنوان المهمة ووصفًا لا يتجاوز 4000 حرف.", 400)
    if priority not in {"low", "normal", "high", "urgent"}:
        return _json_error("مستوى أولوية المهمة غير صالح.", 400)
    if not isinstance(employee_ids, list) or not employee_ids or len(employee_ids) > 50:
        return _json_error("اختر موظفًا واحدًا على الأقل لإسناد المهمة.", 400)
    try:
        employee_ids = list(dict.fromkeys(int(value) for value in employee_ids))
        if any(value < 1 for value in employee_ids):
            raise ValueError
    except (TypeError, ValueError):
        return _json_error("قائمة الموظفين غير صالحة.", 400)
    due_value = None
    if due_at:
        try:
            parsed = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                from web_app import JORDAN_TZ
                parsed = parsed.replace(tzinfo=JORDAN_TZ)
            due_value = parsed.astimezone(timezone.utc)
        except ValueError:
            return _json_error("موعد إنجاز المهمة غير صالح.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                branch_id = int(requested_branch)
            except (TypeError, ValueError):
                return _json_error("حدد الفرع الذي ستُسند إليه المهمة.", 400)
            if not _branch_allowed(cur, g.current_user, branch_id):
                conn.rollback()
                return _json_error("لا تملك صلاحية إنشاء مهام لهذا الفرع.", 403)
            cur.execute("SELECT id FROM branches WHERE id=%s AND is_active=TRUE", (branch_id,))
            if not cur.fetchone():
                conn.rollback()
                return _json_error("الفرع غير موجود أو غير نشط.", 404)
            cur.execute(
                "SELECT id FROM employees WHERE id=ANY(%s) AND branch_id=%s AND is_active=TRUE ORDER BY id",
                (employee_ids, branch_id),
            )
            found = {int(row["id"]) for row in cur.fetchall()}
            if found != set(employee_ids):
                conn.rollback()
                return _json_error("يجب اختيار موظفين نشطين من الفرع المحدد فقط.", 400)
            cur.execute(
                """INSERT INTO work_tasks(branch_id,title,description,priority,due_at,created_by)
                   VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
                (branch_id, title, description, priority, due_value, g.current_user["id"]),
            )
            task_id = cur.fetchone()["id"]
            assignment_ids = []
            for employee_id in employee_ids:
                cur.execute(
                    """INSERT INTO work_task_assignments(task_id,employee_id)
                       VALUES(%s,%s) RETURNING id""", (task_id, employee_id)
                )
                assignment_id = cur.fetchone()["id"]
                assignment_ids.append(assignment_id)
                cur.execute(
                    """INSERT INTO work_task_events(assignment_id,actor_user_id,event_type,to_status,note)
                       VALUES(%s,%s,'assigned','pending',%s)""",
                    (assignment_id, g.current_user["id"], "تم إسناد المهمة"))
                cur.execute(
                    """INSERT INTO web_notifications(recipient_user_id,employee_id,notification_type,title,body)
                       SELECT u.id,e.id,'task_assigned','مهمة جديدة: ' || %s,%s
                       FROM employees e JOIN web_users u ON u.employee_id=e.id
                       WHERE e.id=%s AND u.is_active=TRUE""",
                    (title, description or "تم إسناد مهمة جديدة إليك. افتح صفحة المهام للاطلاع على التفاصيل.", employee_id),
                )
            _audit(cur, g.current_user["id"], branch_id, "work_task_created", "work_task", task_id,
                   {"assignments": len(assignment_ids), "priority": priority})
        conn.commit()
        return jsonify({"ok": True, "task_id": task_id, "assignment_ids": assignment_ids,
                        "message": "تم إنشاء المهمة وإشعار الموظفين المحددين."}), 201
    except Exception:
        conn.rollback()
        logger.exception("Could not create work task")
        return _json_error("تعذر إنشاء المهمة. تحقق من البيانات وحاول مجددًا.", 500)
    finally:
        conn.close()


@app.patch("/api/work/tasks/<int:assignment_id>")
@auth_required
def update_work_task(assignment_id):
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    status = payload.get("status")
    note = str(payload.get("note") or "").strip()
    if status not in {"pending", "in_progress", "completed", "blocked", "cancelled"} or len(note) > 1000:
        return _json_error("حالة المهمة أو الملاحظة غير صالحة.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT a.id,a.employee_id,a.status,t.id AS task_id,t.branch_id,t.title,
                          e.full_name AS employee_name
                   FROM work_task_assignments a JOIN work_tasks t ON t.id=a.task_id
                   JOIN employees e ON e.id=a.employee_id
                   WHERE a.id=%s AND t.archived_at IS NULL FOR UPDATE OF a""",
                (assignment_id,),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return _json_error("المهمة غير موجودة.", 404)
            is_employee = g.current_user["role"] == "employee"
            if is_employee:
                if int(g.current_user["employee_id"]) != int(row["employee_id"]):
                    conn.rollback()
                    return _json_error("هذه المهمة ليست مسندة إليك.", 403)
                if status not in {"in_progress", "completed", "blocked"}:
                    return _json_error("يمكنك بدء المهمة أو إكمالها أو الإبلاغ عن تعذر تنفيذها.", 403)
            elif not _branch_allowed(cur, g.current_user, row["branch_id"]):
                conn.rollback()
                return _json_error("لا تملك صلاحية تعديل مهمة هذا الفرع.", 403)
            old_status = row["status"]
            cur.execute(
                """UPDATE work_task_assignments SET status=%s,updated_at=CURRENT_TIMESTAMP,
                   completed_at=CASE WHEN %s='completed' THEN CURRENT_TIMESTAMP ELSE NULL END
                   WHERE id=%s""", (status, status, assignment_id)
            )
            cur.execute(
                """INSERT INTO work_task_events(assignment_id,actor_user_id,event_type,from_status,to_status,note)
                   VALUES(%s,%s,'status_changed',%s,%s,%s)""",
                (assignment_id, g.current_user["id"], old_status, status, note),
            )
            if is_employee:
                cur.execute(
                    """INSERT INTO web_notifications(recipient_user_id,notification_type,title,body)
                       SELECT id,'task_updated','تحديث مهمة: ' || %s,%s
                       FROM web_users WHERE branch_id=%s AND role IN ('branch_manager','supervisor') AND is_active=TRUE""",
                    (row["title"], row["employee_name"] + " حدّث حالة المهمة إلى " + status),
                )
            else:
                cur.execute(
                    """INSERT INTO web_notifications(recipient_user_id,employee_id,notification_type,title,body)
                       SELECT u.id,e.id,'task_updated','تحديث المهمة: ' || %s,%s
                       FROM employees e JOIN web_users u ON u.employee_id=e.id
                       WHERE e.id=%s AND u.is_active=TRUE""",
                    (row["title"], "تم تحديث حالة المهمة من الإدارة.", row["employee_id"]),
                )
            _audit(cur, g.current_user["id"], row["branch_id"], "work_task_status_changed",
                   "work_task_assignment", assignment_id, {"from": old_status, "to": status})
        conn.commit()
        return jsonify({"ok": True, "message": "تم تحديث حالة المهمة."})
    finally:
        conn.close()


@app.get("/api/work/tasks/<int:assignment_id>/events")
@auth_required
def work_task_events(assignment_id):
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT a.employee_id,t.branch_id FROM work_task_assignments a
                   JOIN work_tasks t ON t.id=a.task_id WHERE a.id=%s""", (assignment_id,)
            )
            scope = cur.fetchone()
            if not scope:
                return _json_error("المهمة غير موجودة.", 404)
            if g.current_user["role"] == "employee":
                allowed = int(g.current_user["employee_id"]) == int(scope["employee_id"])
            else:
                allowed = _branch_allowed(cur, g.current_user, scope["branch_id"])
            if not allowed:
                return _json_error("لا تملك صلاحية الاطلاع على سجل المهمة.", 403)
            cur.execute(
                """SELECT ev.id,ev.event_type,ev.from_status,ev.to_status,ev.note,ev.created_at,
                          u.full_name AS actor_name
                   FROM work_task_events ev LEFT JOIN web_users u ON u.id=ev.actor_user_id
                   WHERE ev.assignment_id=%s ORDER BY ev.created_at DESC LIMIT 100""",
                (assignment_id,),
            )
            events = [dict(row) for row in cur.fetchall()]
        return jsonify({"ok": True, "events": events})
    finally:
        conn.close()
