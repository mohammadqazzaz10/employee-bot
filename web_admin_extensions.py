"""Additional audited web-admin operations for employee and branch lifecycle, broadcasts and meetings."""
from datetime import datetime, timezone
import re

from flask import g, jsonify, request
from psycopg2.extras import RealDictCursor

from web_app import app, _db, _audit, _branch_allowed, _json_error, roles_required


@app.patch("/api/admin/employees/<int:employee_id>/status")
@roles_required("super_admin", "branch_manager")
def set_employee_status(employee_id):
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    active = payload.get("is_active")
    if not isinstance(active, bool):
        return _json_error("حدد حالة الموظف بشكل صحيح.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id,branch_id,is_active FROM employees WHERE id=%s FOR UPDATE", (employee_id,))
            employee = cur.fetchone()
            if not employee:
                conn.rollback()
                return _json_error("الموظف غير موجود.", 404)
            if not _branch_allowed(cur, g.current_user, employee["branch_id"]):
                conn.rollback()
                return _json_error("لا تملك صلاحية إدارة هذا الموظف.", 403)
            cur.execute("UPDATE employees SET is_active=%s WHERE id=%s", (active, employee_id))
            cur.execute("UPDATE web_users SET is_active=%s,updated_at=CURRENT_TIMESTAMP WHERE employee_id=%s",
                        (active, employee_id))
            cur.execute(
                """UPDATE web_sessions SET revoked_at=CURRENT_TIMESTAMP
                   WHERE user_id IN (SELECT id FROM web_users WHERE employee_id=%s)
                     AND revoked_at IS NULL""", (employee_id,))
            _audit(cur, g.current_user["id"], employee["branch_id"],
                   "employee_activated" if active else "employee_blocked", "employee", employee_id)
        conn.commit()
        return jsonify({"ok": True, "is_active": active,
                        "message": "تم تفعيل الموظف." if active else "تم إيقاف الموظف وإلغاء جلساته."})
    finally:
        conn.close()


@app.patch("/api/admin/branches/<int:branch_id>")
@roles_required("super_admin")
def update_branch(branch_id):
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    name = payload.get("name")
    active = payload.get("is_active")
    if not payload or not set(payload).issubset({"name", "is_active"}):
        return _json_error("لا توجد تغييرات صالحة.", 400)
    if "name" in payload and (not isinstance(name, str) or not 2 <= len(name.strip()) <= 120):
        return _json_error("اسم الفرع يجب أن يكون بين حرفين و120 حرفًا.", 400)
    if "is_active" in payload and not isinstance(active, bool):
        return _json_error("حالة الفرع غير صالحة.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id,name,is_active FROM branches WHERE id=%s FOR UPDATE", (branch_id,))
            old = cur.fetchone()
            if not old:
                conn.rollback()
                return _json_error("الفرع غير موجود.", 404)
            sets, values = [], []
            if "name" in payload:
                sets.append("name=%s")
                values.append(name.strip())
            if "is_active" in payload:
                sets.append("is_active=%s")
                values.append(active)
            values.append(branch_id)
            cur.execute("UPDATE branches SET " + ",".join(sets) + " WHERE id=%s RETURNING id,code,name,is_active", values)
            updated = dict(cur.fetchone())
            _audit(cur, g.current_user["id"], branch_id, "branch_updated", "branch", branch_id, payload)
        conn.commit()
        return jsonify({"ok": True, "branch": updated, "message": "تم تحديث بيانات الفرع."})
    finally:
        conn.close()


@app.post("/api/admin/announcements")
@roles_required("super_admin", "branch_manager", "supervisor")
def create_announcement():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    branch_id = payload.get("branch_id") or g.current_user["branch_id"]
    employee_id = payload.get("employee_id")
    if not title or len(title) > 160 or not body or len(body) > 5000 or not branch_id:
        return _json_error("أدخل عنوانًا ونصًا صحيحين وحدد الفرع.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if not _branch_allowed(cur, g.current_user, branch_id):
                conn.rollback()
                return _json_error("لا تملك صلاحية الإرسال لهذا الفرع.", 403)
            if employee_id is not None:
                cur.execute("SELECT id FROM employees WHERE id=%s AND branch_id=%s AND is_active=TRUE",
                            (employee_id, branch_id))
                if not cur.fetchone():
                    conn.rollback()
                    return _json_error("الموظف غير موجود أو غير نشط في هذا الفرع.", 404)
                recipient_filter = "AND u.employee_id=%s"
                params = (branch_id, employee_id)
            else:
                recipient_filter = ""
                params = (branch_id,)
            cur.execute(
                """INSERT INTO web_notifications
                   (recipient_user_id,employee_id,notification_type,title,body)
                   SELECT u.id,u.employee_id,'announcement',%s,%s
                   FROM web_users u JOIN employees e ON e.id=u.employee_id
                   WHERE u.role='employee' AND u.is_active=TRUE AND e.is_active=TRUE
                     AND e.branch_id=%s """ + recipient_filter + """
                   RETURNING id""",
                (title, body, *params))
            created = cur.fetchall()
            _audit(cur, g.current_user["id"], branch_id, "announcement_sent", "branch", branch_id,
                   {"recipients": len(created), "employee_id": employee_id})
        conn.commit()
        return jsonify({"ok": True, "recipients": len(created), "message": f"تم إرسال التعميم إلى {len(created)} موظف."}), 201
    finally:
        conn.close()


@app.get("/api/admin/meetings")
@roles_required("super_admin", "branch_manager", "supervisor")
def list_meetings():
    requested = request.args.get("branch_id")
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if g.current_user["role"] == "super_admin":
                if requested:
                    cur.execute("SELECT id,code,name FROM branches WHERE id=%s AND is_active=TRUE", (requested,))
                    branch = cur.fetchone()
                    if not branch:
                        conn.rollback()
                        return _json_error("الفرع غير موجود أو غير نشط.", 404)
                    branch_ids = (int(requested),)
                    cur.execute("""SELECT m.*,b.name AS branch_name FROM web_meetings m
                                   JOIN branches b ON b.id=m.branch_id WHERE m.branch_id=%s
                                   ORDER BY m.starts_at DESC LIMIT 100""", branch_ids)
                else:
                    cur.execute("""SELECT m.*,b.name AS branch_name FROM web_meetings m
                                   JOIN branches b ON b.id=m.branch_id
                                   ORDER BY m.starts_at DESC LIMIT 100""")
            else:
                cur.execute("""SELECT m.*,b.name AS branch_name FROM web_meetings m
                               JOIN branches b ON b.id=m.branch_id WHERE m.branch_id=%s
                               ORDER BY m.starts_at DESC LIMIT 100""", (g.current_user["branch_id"],))
            rows = [dict(row) for row in cur.fetchall()]
        return jsonify({"ok": True, "meetings": rows})
    finally:
        conn.close()


@app.post("/api/admin/meetings")
@roles_required("super_admin", "branch_manager", "supervisor")
def create_meeting():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    title = str(payload.get("title") or "").strip()
    agenda = str(payload.get("agenda") or "").strip()
    location = str(payload.get("location") or "").strip()
    starts_raw = str(payload.get("starts_at") or "").strip()
    branch_id = payload.get("branch_id") or g.current_user["branch_id"]
    try:
        starts_at = datetime.fromisoformat(starts_raw.replace("Z", "+00:00"))
        if starts_at.tzinfo is None:
            return _json_error("أرسل موعد الاجتماع مع المنطقة الزمنية.", 400)
        starts_at = starts_at.astimezone(timezone.utc)
    except ValueError:
        return _json_error("موعد الاجتماع غير صالح.", 400)
    if not title or len(title) > 160 or len(agenda) > 5000 or len(location) > 180 or not branch_id:
        return _json_error("تحقق من عنوان الاجتماع والفرع والحقول.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if not _branch_allowed(cur, g.current_user, branch_id):
                conn.rollback()
                return _json_error("لا تملك صلاحية إنشاء اجتماع لهذا الفرع.", 403)
            cur.execute("SELECT id,name FROM branches WHERE id=%s AND is_active=TRUE", (branch_id,))
            branch = cur.fetchone()
            if not branch:
                conn.rollback()
                return _json_error("الفرع غير موجود أو غير نشط.", 404)
            cur.execute(
                """INSERT INTO web_meetings(branch_id,title,agenda,location,starts_at,created_by)
                   VALUES(%s,%s,%s,%s,%s,%s) RETURNING id,starts_at""",
                (branch_id, title, agenda, location, starts_at, g.current_user["id"]))
            meeting = cur.fetchone()
            cur.execute(
                """INSERT INTO web_notifications(recipient_user_id,employee_id,notification_type,title,body)
                   SELECT u.id,u.employee_id,'meeting',%s,%s
                   FROM web_users u JOIN employees e ON e.id=u.employee_id
                   WHERE u.role='employee' AND u.is_active=TRUE AND e.is_active=TRUE AND e.branch_id=%s""",
                ("اجتماع جديد: " + title,
                 "الموعد: " + starts_at.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Amman")).strftime("%Y-%m-%d %H:%M")
                 + ((" | المكان: " + location) if location else "")
                 + ((" | " + agenda) if agenda else ""), branch_id))
            _audit(cur, g.current_user["id"], branch_id, "meeting_created", "meeting", meeting["id"])
        conn.commit()
        return jsonify({"ok": True, "meeting": dict(meeting), "message": "تم إنشاء الاجتماع وإشعار موظفي الفرع."}), 201
    finally:
        conn.close()


@app.patch("/api/admin/meetings/<int:meeting_id>")
@roles_required("super_admin", "branch_manager", "supervisor")
def update_meeting(meeting_id):
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    status = payload.get("status")
    if status not in {"cancelled", "completed"}:
        return _json_error("الحالة المطلوبة غير مدعومة.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id,branch_id,status FROM web_meetings WHERE id=%s FOR UPDATE", (meeting_id,))
            meeting = cur.fetchone()
            if not meeting:
                conn.rollback()
                return _json_error("الاجتماع غير موجود.", 404)
            if not _branch_allowed(cur, g.current_user, meeting["branch_id"]):
                conn.rollback()
                return _json_error("لا تملك صلاحية إدارة هذا الاجتماع.", 403)
            cur.execute("UPDATE web_meetings SET status=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                        (status, meeting_id))
            if status == "cancelled":
                cur.execute(
                    """INSERT INTO web_notifications(recipient_user_id,employee_id,notification_type,title,body)
                       SELECT u.id,u.employee_id,'meeting_cancelled','إلغاء اجتماع',
                              'تم إلغاء الاجتماع رقم ' || %s::text
                       FROM web_users u JOIN employees e ON e.id=u.employee_id
                       WHERE u.role='employee' AND u.is_active=TRUE AND e.is_active=TRUE AND e.branch_id=%s""",
                    (meeting_id, meeting["branch_id"]))
            _audit(cur, g.current_user["id"], meeting["branch_id"], "meeting_" + status, "meeting", meeting_id)
        conn.commit()
        return jsonify({"ok": True, "message": "تم تحديث حالة الاجتماع."})
    finally:
        conn.close()


@app.get("/api/admin/site-settings")
@roles_required("super_admin")
def get_site_settings():
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT setting_key,setting_value FROM web_app_settings ORDER BY setting_key")
            return jsonify({"ok": True, "settings": {row["setting_key"]: row["setting_value"] for row in cur.fetchall()}})
    finally:
        conn.close()


@app.patch("/api/admin/site-settings")
@roles_required("super_admin")
def update_site_settings():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    allowed = {"site_name": (2, 80), "site_tagline": (2, 120),
               "support_message": (0, 240), "maintenance_notice": (0, 300)}
    if not payload or not set(payload).issubset(allowed):
        return _json_error("إعدادات الموقع غير صالحة.", 400)
    for key, value in payload.items():
        if not isinstance(value, str) or not allowed[key][0] <= len(value.strip()) <= allowed[key][1]:
            return _json_error("تحقق من طول النص في إعدادات الموقع.", 400)
    conn = _db()
    try:
        with conn.cursor() as cur:
            for key, value in payload.items():
                cur.execute(
                    """INSERT INTO web_app_settings(setting_key,setting_value,updated_by,updated_at)
                       VALUES(%s,%s::jsonb,%s,CURRENT_TIMESTAMP)
                       ON CONFLICT(setting_key) DO UPDATE
                         SET setting_value=EXCLUDED.setting_value,updated_by=EXCLUDED.updated_by,
                             updated_at=CURRENT_TIMESTAMP""",
                    (key, __import__("json").dumps(value, ensure_ascii=False), g.current_user["id"]))
            _audit(cur, g.current_user["id"], None, "site_settings_updated", "site", "global",
                   {"keys": list(payload)})
        conn.commit()
        return jsonify({"ok": True, "message": "تم حفظ إعدادات الموقع العامة."})
    finally:
        conn.close()


@app.get("/api/system/process-meeting-reminders")
@roles_required("employee", "super_admin", "branch_manager", "supervisor")
def process_meeting_reminders():
    """Durably fan out the 15-minute reminder when an authenticated client is polling."""
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT id,branch_id,title,starts_at FROM web_meetings
                   WHERE status='scheduled' AND reminder_sent_at IS NULL
                     AND starts_at > CURRENT_TIMESTAMP
                     AND starts_at <= CURRENT_TIMESTAMP + INTERVAL '15 minutes'
                   ORDER BY starts_at LIMIT 20 FOR UPDATE SKIP LOCKED"""
            )
            due = cur.fetchall()
            for meeting in due:
                cur.execute(
                    """INSERT INTO web_notifications(recipient_user_id,employee_id,notification_type,title,body)
                       SELECT u.id,u.employee_id,'meeting_reminder','تذكير باجتماع: ' || %s,
                              'موعد الاجتماع بعد 15 دقيقة تقريبًا.'
                       FROM web_users u JOIN employees e ON e.id=u.employee_id
                       WHERE u.role='employee' AND u.is_active=TRUE AND e.is_active=TRUE
                         AND e.branch_id=%s""",
                    (meeting["title"], meeting["branch_id"]))
                cur.execute("UPDATE web_meetings SET reminder_sent_at=CURRENT_TIMESTAMP WHERE id=%s",
                            (meeting["id"],))
            conn.commit()
            return jsonify({"ok": True, "processed": len(due)})
    finally:
        conn.close()


@app.get("/api/site-info")
def public_site_info():
    """Expose only non-sensitive branding/help text for portal views."""
    defaults = {"site_name": "رفيق العمل", "site_tagline": "منظومة إدارة الموظفين", "support_message": "تواصل مع مدير فرعك عند الحاجة.", "maintenance_notice": ""}
    try:
        conn = _db()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT setting_key,setting_value FROM web_app_settings")
                for row in cur.fetchall():
                    defaults[row["setting_key"]] = row["setting_value"]
        finally:
            conn.close()
    except Exception:
        app.logger.exception("Could not load public site branding")
    return jsonify({"ok": True, "settings": defaults})
