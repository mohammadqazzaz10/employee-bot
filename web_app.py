"""Secure Flask API and server-side authorization for the Employee Portal PWA."""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
SESSION_COOKIE = "employee_session"
CSRF_COOKIE = "employee_csrf"
SESSION_HOURS = 12
MAX_LOGIN_FAILURES = 6
LOCK_MINUTES = 15

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("employee-web")

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")
app.config["DATABASE_URL"] = os.environ.get("DATABASE_URL", "").strip()
app.config["DATABASE_SSLMODE"] = os.environ.get("DATABASE_SSLMODE", "prefer").strip() or "prefer"
app.config["COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "true").lower() not in {"0", "false", "no"}
app.config["WEB_BOOTSTRAP_TOKEN"] = os.environ.get("WEB_BOOTSTRAP_TOKEN", "")


def _db():
    url = app.config["DATABASE_URL"]
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(
        url,
        sslmode=app.config["DATABASE_SSLMODE"],
        connect_timeout=10,
        application_name="employee-web-portal",
    )


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_error(message, status):
    return jsonify({"ok": False, "message": message}), status


def _public_user(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "full_name": row["full_name"],
        "role": row["role"],
        "branch_id": row.get("branch_id"),
        "branch_name": row.get("branch_name"),
        "employee_id": row.get("employee_id"),
        "employee_code": row.get("employee_code"),
        "job_title": row.get("job_title"),
        "must_change_password": row["must_change_password"],
    }


def _load_session():
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) < 32:
        return None
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT s.id AS session_id, s.csrf_hash, s.expires_at,
                       u.id, u.username, u.full_name, u.role, u.branch_id,
                       u.employee_id, u.must_change_password, u.is_active,
                       b.name AS branch_name, e.employee_code, e.job_title,
                       e.is_active AS employee_active
                FROM web_sessions s
                JOIN web_users u ON u.id=s.user_id
                LEFT JOIN branches b ON b.id=u.branch_id
                LEFT JOIN employees e ON e.id=u.employee_id
                WHERE s.token_hash=%s AND s.revoked_at IS NULL
                  AND s.expires_at > CURRENT_TIMESTAMP AND u.is_active=TRUE
                """,
                (_hash(token),),
            )
            row = cur.fetchone()
            if not row or (row["employee_id"] and not row["employee_active"]):
                return None
            csrf = request.cookies.get(CSRF_COOKIE, "")
            if not csrf or not hmac.compare_digest(_hash(csrf), row["csrf_hash"]):
                return None
            cur.execute(
                "UPDATE web_sessions SET last_seen_at=CURRENT_TIMESTAMP WHERE id=%s",
                (row["session_id"],),
            )
            conn.commit()
            return dict(row)
    finally:
        conn.close()


def _require_csrf():
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("X-CSRF-Token", "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        return False
    return hmac.compare_digest(_hash(cookie), g.current_user["csrf_hash"])


def auth_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user = _load_session()
        if not user:
            return _json_error("انتهت الجلسة. سجّل الدخول من جديد.", 401)
        g.current_user = user
        if user["must_change_password"] and request.path not in {
            "/api/auth/me", "/api/auth/logout", "/api/auth/change-password"
        }:
            return _json_error("يجب تغيير كلمة المرور المؤقتة قبل استخدام النظام.", 403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not _require_csrf():
            return _json_error("تعذر التحقق الأمني من الطلب. حدّث الصفحة وحاول مجددًا.", 403)
        return fn(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    def decorator(fn):
        @wraps(fn)
        @auth_required
        def wrapped(*args, **kwargs):
            if g.current_user["role"] not in roles:
                return _json_error("ليس لديك صلاحية لتنفيذ هذه العملية.", 403)
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def _audit(cur, actor_id, branch_id, action, target_type=None, target_id=None, details=None):
    cur.execute(
        """INSERT INTO web_audit_log
           (actor_user_id, branch_id, action, target_type, target_id, details)
           VALUES (%s,%s,%s,%s,%s,%s::jsonb)""",
        (actor_id, branch_id, action, target_type, str(target_id) if target_id is not None else None,
         json.dumps(details or {}, ensure_ascii=False)),
    )


def _branch_allowed(cur, user, branch_id):
    if user["role"] == "super_admin":
        return True
    return user["branch_id"] is not None and int(user["branch_id"]) == int(branch_id)


def _employee_id(user):
    return user.get("employee_id")


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store" if request.path.startswith("/api/") else "public, max-age=300"
    return response


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/setup")
def setup_page():
    return send_from_directory(WEB_DIR, "setup.html")


@app.get("/healthz")
def healthz():
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        return jsonify({"ok": True, "service": "employee-web"})
    except Exception:
        logger.exception("Health check failed")
        return _json_error("database unavailable", 503)


@app.post("/api/setup/bootstrap")
def bootstrap_super_admin():
    expected = app.config["WEB_BOOTSTRAP_TOKEN"]
    payload = request.get_json(silent=True) or {}
    supplied = str(payload.get("bootstrap_token", ""))
    username = str(payload.get("username", "")).strip()
    full_name = str(payload.get("full_name", "")).strip()
    password = str(payload.get("password", ""))
    if not expected or not hmac.compare_digest(supplied, expected):
        return _json_error("رمز التهيئة غير صحيح.", 403)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username) or len(password) < 12 or len(full_name) < 2:
        return _json_error("تحقق من اسم المستخدم وكلمة المرور والاسم الكامل. كلمة المرور 12 خانة على الأقل.", 400)
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('employee-web-bootstrap'))")
            cur.execute("SELECT COUNT(*) FROM web_users")
            if cur.fetchone()[0] != 0:
                conn.rollback()
                return _json_error("تم إنشاء حساب الإدارة مسبقًا. أوقف WEB_BOOTSTRAP_TOKEN من إعدادات الاستضافة.", 409)
            cur.execute(
                """INSERT INTO web_users(username,password_hash,role,full_name)
                   VALUES(%s,%s,'super_admin',%s) RETURNING id""",
                (username, generate_password_hash(password, method="scrypt"), full_name),
            )
            user_id = cur.fetchone()[0]
            _audit(cur, user_id, None, "bootstrap_super_admin", "web_user", user_id)
        conn.commit()
        return jsonify({"ok": True, "message": "تم إنشاء حساب المدير العام. أزل WEB_BOOTSTRAP_TOKEN من إعدادات الاستضافة الآن."}), 201
    except psycopg2.IntegrityError:
        conn.rollback()
        return _json_error("اسم المستخدم مستخدم مسبقًا.", 409)
    finally:
        conn.close()


@app.post("/api/auth/login")
def login():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("employee_code") or payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if not username or not password or len(username) > 64 or len(password) > 256:
        return _json_error("أدخل اسم المستخدم وكلمة المرور.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            ip_hash = _hash(request.remote_addr or "unknown")
            cur.execute(
                """INSERT INTO web_login_throttles(ip_hash,attempts,window_started_at)
                   VALUES(%s,0,CURRENT_TIMESTAMP) ON CONFLICT(ip_hash) DO NOTHING""",
                (ip_hash,),
            )
            cur.execute(
                """SELECT attempts,window_started_at,blocked_until
                   FROM web_login_throttles WHERE ip_hash=%s FOR UPDATE""",
                (ip_hash,),
            )
            throttle = cur.fetchone()
            now = datetime.now(timezone.utc)
            if throttle["blocked_until"] and throttle["blocked_until"] > now:
                conn.rollback()
                return _json_error("محاولات دخول كثيرة. انتظر قليلًا ثم حاول مجددًا.", 429)
            attempts_ip = 1 if throttle["window_started_at"] < now - timedelta(minutes=10) else int(throttle["attempts"]) + 1
            blocked_ip = attempts_ip >= 25
            cur.execute(
                """UPDATE web_login_throttles SET attempts=%s,
                   window_started_at=CASE WHEN window_started_at < CURRENT_TIMESTAMP - INTERVAL '10 minutes'
                                           THEN CURRENT_TIMESTAMP ELSE window_started_at END,
                   blocked_until=CASE WHEN %s THEN CURRENT_TIMESTAMP + INTERVAL '15 minutes' ELSE NULL END,
                   updated_at=CURRENT_TIMESTAMP WHERE ip_hash=%s""",
                (attempts_ip, blocked_ip, ip_hash),
            )
            if blocked_ip:
                conn.commit()
                return _json_error("محاولات دخول كثيرة. تم إيقاف المحاولة مؤقتًا.", 429)
            cur.execute(
                """SELECT u.*, b.name AS branch_name, e.employee_code, e.job_title,
                          e.is_active AS employee_active
                   FROM web_users u
                   LEFT JOIN branches b ON b.id=u.branch_id
                   LEFT JOIN employees e ON e.id=u.employee_id
                   WHERE LOWER(u.username)=LOWER(%s) FOR UPDATE OF u""",
                (username,),
            )
            user = cur.fetchone()
            valid = bool(
                user and user["is_active"]
                and (user["employee_id"] is None or user["employee_active"])
                and (user["locked_until"] is None or user["locked_until"] <= datetime.now(timezone.utc))
                and check_password_hash(user["password_hash"], password)
            )
            if not valid:
                if user:
                    attempts = int(user["failed_login_attempts"]) + 1
                    locked = attempts >= MAX_LOGIN_FAILURES
                    cur.execute(
                        """UPDATE web_users SET failed_login_attempts=%s,
                           locked_until=CASE WHEN %s THEN CURRENT_TIMESTAMP + (%s * INTERVAL '1 minute') ELSE locked_until END,
                           updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                        (0 if locked else attempts, locked, LOCK_MINUTES, user["id"]),
                    )
                conn.commit()
                return _json_error("بيانات الدخول غير صحيحة أو الحساب موقوف مؤقتًا.", 401)
            cur.execute(
                """UPDATE web_users SET failed_login_attempts=0, locked_until=NULL,
                   last_login_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                (user["id"],),
            )
            cur.execute(
                """UPDATE web_login_throttles SET attempts=0,blocked_until=NULL,
                   window_started_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE ip_hash=%s""",
                (ip_hash,),
            )
            raw_token = secrets.token_urlsafe(40)
            csrf_token = secrets.token_urlsafe(32)
            cur.execute(
                """INSERT INTO web_sessions(user_id,token_hash,csrf_hash,expires_at,ip_address,user_agent)
                   VALUES(%s,%s,%s,CURRENT_TIMESTAMP + (%s * INTERVAL '1 hour'),%s,%s)""",
                (user["id"], _hash(raw_token), _hash(csrf_token), SESSION_HOURS,
                 request.remote_addr, request.headers.get("User-Agent", "")[:300]),
            )
            conn.commit()
            response = jsonify({"ok": True, "user": _public_user(dict(user)), "csrf_token": csrf_token})
            response.set_cookie(SESSION_COOKIE, raw_token, max_age=SESSION_HOURS * 3600,
                                httponly=True, secure=app.config["COOKIE_SECURE"],
                                samesite="Lax", path="/")
            response.set_cookie(CSRF_COOKIE, csrf_token, max_age=SESSION_HOURS * 3600,
                                httponly=False, secure=app.config["COOKIE_SECURE"],
                                samesite="Lax", path="/")
            return response
    finally:
        conn.close()


@app.post("/api/auth/activate")
def activate_employee():
    payload = request.get_json(silent=True) or {}
    code = str(payload.get("activation_code") or "")
    password = str(payload.get("password") or "")
    if len(code) < 24 or len(password) < 10 or len(password) > 256:
        return _json_error("رمز التفعيل أو كلمة المرور غير صالحين. استخدم كلمة مرور من 10 خانات على الأقل.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT t.id AS token_id,t.employee_id,e.employee_code,e.full_name,
                          e.job_title,e.is_active,e.branch_id
                   FROM web_activation_tokens t JOIN employees e ON e.id=t.employee_id
                   WHERE t.token_hash=%s AND t.used_at IS NULL
                     AND t.expires_at>CURRENT_TIMESTAMP AND e.is_active=TRUE
                   FOR UPDATE OF t,e""",
                (_hash(code),),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return _json_error("رمز التفعيل غير صالح أو منتهي الصلاحية أو مستخدم.", 400)
            cur.execute(
                """INSERT INTO web_users(username,password_hash,role,employee_id,full_name,must_change_password)
                   VALUES(%s,%s,'employee',%s,%s,FALSE)""",
                (row["employee_code"], generate_password_hash(password, method="scrypt"),
                 row["employee_id"], row["full_name"]),
            )
            cur.execute("UPDATE web_activation_tokens SET used_at=CURRENT_TIMESTAMP WHERE id=%s", (row["token_id"],))
            _audit(cur, None, row["branch_id"], "employee_account_activated", "employee", row["employee_id"])
        conn.commit()
        return jsonify({"ok": True, "employee_code": row["employee_code"], "message": "تم تفعيل الحساب. يمكنك تسجيل الدخول برقمك الوظيفي."}), 201
    except psycopg2.IntegrityError:
        conn.rollback()
        return _json_error("تم تفعيل حساب لهذا الموظف مسبقًا. تواصل مع الإدارة.", 409)
    finally:
        conn.close()


@app.get("/api/auth/me")
@auth_required
def me():
    return jsonify({"ok": True, "user": _public_user(dict(g.current_user)),
                    "csrf_token": request.cookies.get(CSRF_COOKIE, "")})


@app.post("/api/auth/logout")
@auth_required
def logout():
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE web_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE id=%s",
                        (g.current_user["session_id"],))
        conn.commit()
    finally:
        conn.close()
    response = jsonify({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@app.post("/api/auth/change-password")
@auth_required
def change_password():
    payload = request.get_json(silent=True) or {}
    old = str(payload.get("current_password") or "")
    new = str(payload.get("new_password") or "")
    if len(new) < 10 or len(new) > 256:
        return _json_error("كلمة المرور الجديدة يجب ألا تقل عن 10 خانات.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT password_hash FROM web_users WHERE id=%s FOR UPDATE", (g.current_user["id"],))
            row = cur.fetchone()
            if not row or not check_password_hash(row["password_hash"], old):
                conn.rollback()
                return _json_error("كلمة المرور الحالية غير صحيحة.", 400)
            cur.execute(
                """UPDATE web_users SET password_hash=%s,must_change_password=FALSE,
                   failed_login_attempts=0,locked_until=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                (generate_password_hash(new, method="scrypt"), g.current_user["id"]),
            )
            cur.execute(
                "UPDATE web_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=%s AND id<>%s AND revoked_at IS NULL",
                (g.current_user["id"], g.current_user["session_id"]),
            )
        conn.commit()
        return jsonify({"ok": True, "message": "تم تحديث كلمة المرور."})
    finally:
        conn.close()


@app.get("/api/employee/summary")
@roles_required("employee")
def employee_summary():
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            employee_id = _employee_id(g.current_user)
            cur.execute(
                """SELECT e.id,e.employee_code,e.full_name,e.job_title,b.name AS branch_name
                   FROM employees e JOIN branches b ON b.id=e.branch_id
                   WHERE e.id=%s AND e.is_active=TRUE""", (employee_id,))
            profile = cur.fetchone()
            cur.execute(
                """UPDATE web_timers SET completed_at=ends_at
                   WHERE employee_id=%s AND completed_at IS NULL AND returned_at IS NULL
                     AND ends_at<=CURRENT_TIMESTAMP""", (employee_id,))
            cur.execute(
                """SELECT id,request_type,started_at,ends_at,completed_at,returned_at
                   FROM web_timers WHERE employee_id=%s AND returned_at IS NULL
                   ORDER BY started_at DESC LIMIT 1""", (employee_id,))
            timer = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM requests WHERE employee_id=%s AND status='pending'", (employee_id,))
            pending = cur.fetchone()["count"]
            cur.execute(
                """SELECT r.id,r.request_type,r.status,r.requested_at,r.responded_at,r.notes,
                          COALESCE(t.label,r.request_type) AS request_label
                   FROM requests r LEFT JOIN branch_request_types t
                     ON t.branch_id=(SELECT branch_id FROM employees WHERE id=r.employee_id)
                    AND t.code=r.request_type
                   WHERE r.employee_id=%s ORDER BY r.requested_at DESC LIMIT 5""", (employee_id,))
            recent = cur.fetchall()
        conn.commit()
        return jsonify({"ok": True, "profile": dict(profile) if profile else None,
                        "timer": dict(timer) if timer else None, "pending_count": pending,
                        "recent_requests": [dict(x) for x in recent]})
    finally:
        conn.close()


@app.get("/api/employee/request-types")
@roles_required("employee")
def employee_request_types():
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT r.id,r.code,r.label,r.duration_minutes
                   FROM branch_request_types r JOIN employees e ON e.branch_id=r.branch_id
                   WHERE e.id=%s AND e.is_active=TRUE AND r.is_active=TRUE
                   ORDER BY r.id""", (_employee_id(g.current_user),))
            rows = cur.fetchall()
        return jsonify({"ok": True, "request_types": [dict(x) for x in rows]})
    finally:
        conn.close()


@app.get("/api/employee/requests")
@roles_required("employee")
def employee_requests():
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT r.id,r.request_type,r.status,r.requested_at,r.responded_at,r.notes,
                          COALESCE(t.label,r.request_type) AS request_label
                   FROM requests r LEFT JOIN branch_request_types t
                     ON t.branch_id=(SELECT branch_id FROM employees WHERE id=r.employee_id)
                    AND t.code=r.request_type
                   WHERE r.employee_id=%s ORDER BY r.requested_at DESC LIMIT 50""",
                (_employee_id(g.current_user),))
            rows = cur.fetchall()
        return jsonify({"ok": True, "requests": [dict(x) for x in rows]})
    finally:
        conn.close()


@app.post("/api/employee/requests")
@roles_required("employee")
def create_employee_request():
    payload = request.get_json(silent=True) or {}
    request_code = str(payload.get("request_type") or "").strip()
    notes = str(payload.get("notes") or "").strip()[:500]
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", request_code):
        return _json_error("نوع الطلب غير صالح.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT e.id,e.branch_id,e.full_name
                   FROM employees e JOIN branches b ON b.id=e.branch_id AND b.is_active=TRUE
                   WHERE e.id=%s AND e.is_active=TRUE FOR UPDATE OF e""",
                (_employee_id(g.current_user),))
            employee = cur.fetchone()
            if not employee:
                conn.rollback()
                return _json_error("حساب الموظف غير نشط.", 403)
            cur.execute(
                """SELECT id,label FROM branch_request_types
                   WHERE branch_id=%s AND code=%s AND is_active=TRUE""",
                (employee["branch_id"], request_code))
            kind = cur.fetchone()
            if not kind:
                conn.rollback()
                return _json_error("نوع الطلب غير متاح في فرعك.", 400)
            cur.execute(
                """SELECT 1 FROM requests WHERE employee_id=%s AND status='pending'
                   AND request_type=%s FOR UPDATE""",
                (employee["id"], request_code))
            if cur.fetchone():
                conn.rollback()
                return _json_error("لديك طلب مماثل قيد الانتظار بالفعل.", 409)
            cur.execute(
                """SELECT 1 FROM web_timers WHERE employee_id=%s
                   AND returned_at IS NULL FOR UPDATE""",
                (employee["id"],))
            if cur.fetchone():
                conn.rollback()
                return _json_error("لديك استراحة لم تُغلق بعد.", 409)
            cur.execute(
                """INSERT INTO requests(employee_id,branch_id,request_type,status,notes)
                   VALUES(%s,%s,%s,'pending',%s) RETURNING id,requested_at""",
                (employee["id"], employee["branch_id"], request_code, notes))
            created = cur.fetchone()
            cur.execute(
                """INSERT INTO web_notifications(recipient_user_id,employee_id,request_id,
                   notification_type,title,body)
                   SELECT id,%s,%s,'new_request','طلب موظف جديد',%s
                   FROM web_users WHERE role IN ('supervisor','branch_manager','super_admin')
                     AND is_active=TRUE AND (role='super_admin' OR branch_id=%s)""",
                (employee["id"], created["id"], f"طلب {kind['label']} من {employee['full_name']}",
                 employee["branch_id"]))
            _audit(cur, g.current_user["id"], employee["branch_id"], "employee_request_created",
                   "request", created["id"], {"request_type": request_code})
        conn.commit()
        return jsonify({"ok": True, "request_id": created["id"], "message": "تم إرسال طلبك إلى المشرف."}), 201
    finally:
        conn.close()


@app.post("/api/employee/timer/return")
@roles_required("employee")
def return_employee_timer():
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE web_timers SET completed_at=ends_at
                   WHERE employee_id=%s AND completed_at IS NULL
                     AND ends_at<=CURRENT_TIMESTAMP AND returned_at IS NULL""",
                (_employee_id(g.current_user),))
            cur.execute(
                """UPDATE web_timers SET returned_at=CURRENT_TIMESTAMP
                   WHERE employee_id=%s AND completed_at IS NOT NULL AND returned_at IS NULL
                   RETURNING id""", (_employee_id(g.current_user),))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return _json_error("لا توجد استراحة منتهية بانتظار تأكيد العودة.", 409)
            _audit(cur, g.current_user["id"], None, "employee_return_confirmed", "timer", row[0])
        conn.commit()
        return jsonify({"ok": True, "message": "تم تسجيل عودتك للعمل."})
    finally:
        conn.close()


@app.post("/api/employee/call-supervisor")
@roles_required("employee")
def call_supervisor():
    payload = request.get_json(silent=True) or {}
    note = str(payload.get("message") or "يرجى التواصل مع الموظف.").strip()[:300]
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id,branch_id,full_name FROM employees WHERE id=%s AND is_active=TRUE",
                        (_employee_id(g.current_user),))
            employee = cur.fetchone()
            if not employee:
                conn.rollback()
                return _json_error("حساب الموظف غير نشط.", 403)
            cur.execute(
                """INSERT INTO web_notifications(recipient_user_id,employee_id,
                   notification_type,title,body)
                   SELECT id,%s,'employee_call','استدعاء من موظف',%s
                   FROM web_users WHERE is_active=TRUE
                     AND role IN ('supervisor','branch_manager','super_admin')
                     AND (role='super_admin' OR branch_id=%s)
                   RETURNING id""",
                (employee["id"], f"{employee['full_name']}: {note}", employee["branch_id"]))
            count = len(cur.fetchall())
            _audit(cur, g.current_user["id"], employee["branch_id"], "supervisor_called",
                   "employee", employee["id"])
        conn.commit()
        return jsonify({"ok": True, "notified_count": count, "message": "تم إرسال استدعاء المشرف."}), 201
    finally:
        conn.close()


@app.get("/api/notifications")
@auth_required
def notifications():
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT id,notification_type,title,body,read_at,created_at
                   FROM web_notifications WHERE recipient_user_id=%s
                   ORDER BY created_at DESC LIMIT 30""", (g.current_user["id"],))
            rows = cur.fetchall()
        return jsonify({"ok": True, "notifications": [dict(x) for x in rows]})
    finally:
        conn.close()


@app.post("/api/notifications/<int:notification_id>/read")
@auth_required
def mark_notification_read(notification_id):
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE web_notifications SET read_at=CURRENT_TIMESTAMP
                   WHERE id=%s AND recipient_user_id=%s""",
                (notification_id, g.current_user["id"]))
            if cur.rowcount == 0:
                conn.rollback()
                return _json_error("الإشعار غير موجود.", 404)
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.get("/api/admin/overview")
@roles_required("super_admin", "branch_manager", "supervisor")
def admin_overview():
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            branch_id = g.current_user["branch_id"]
            scope = "" if g.current_user["role"] == "super_admin" else " AND branch_id=%s"
            params = () if not scope else (branch_id,)
            cur.execute("SELECT COUNT(*) AS total FROM employees WHERE is_active=TRUE" + scope, params)
            employees = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) AS total FROM requests WHERE status='pending'" + scope, params)
            pending = cur.fetchone()["total"]
            cur.execute(
                """SELECT COUNT(*) AS total FROM web_timers t JOIN employees e ON e.id=t.employee_id
                   WHERE t.completed_at IS NULL AND t.returned_at IS NULL
                     AND t.ends_at>CURRENT_TIMESTAMP""" + ("" if not scope else " AND e.branch_id=%s"),
                params)
            timers = cur.fetchone()["total"]
            cur.execute(
                """SELECT COUNT(*) AS total FROM web_notifications n
                   JOIN web_users u ON u.id=n.recipient_user_id
                   WHERE n.recipient_user_id=%s AND n.read_at IS NULL""",
                (g.current_user["id"],))
            unread = cur.fetchone()["total"]
        return jsonify({"ok": True, "stats": {"employees": employees, "pending_requests": pending,
                                              "active_timers": timers, "unread_notifications": unread}})
    finally:
        conn.close()


@app.get("/api/admin/branches")
@roles_required("super_admin", "branch_manager", "supervisor")
def admin_branches():
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if g.current_user["role"] == "super_admin":
                cur.execute("SELECT id,code,name,is_active FROM branches ORDER BY name")
            else:
                cur.execute("SELECT id,code,name,is_active FROM branches WHERE id=%s", (g.current_user["branch_id"],))
            rows = cur.fetchall()
        return jsonify({"ok": True, "branches": [dict(x) for x in rows]})
    finally:
        conn.close()


@app.get("/api/admin/employees")
@roles_required("super_admin", "branch_manager", "supervisor")
def admin_employees():
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            sql = """SELECT e.id,e.employee_code,e.full_name,e.job_title,e.phone_number,
                            e.is_active,b.name AS branch_name,
                            EXISTS(SELECT 1 FROM web_users u WHERE u.employee_id=e.id AND u.is_active=TRUE) AS has_web_account
                     FROM employees e JOIN branches b ON b.id=e.branch_id"""
            if g.current_user["role"] == "super_admin":
                cur.execute(sql + " ORDER BY b.name,e.full_name")
            else:
                cur.execute(sql + " WHERE e.branch_id=%s ORDER BY e.full_name", (g.current_user["branch_id"],))
            rows = cur.fetchall()
        return jsonify({"ok": True, "employees": [dict(x) for x in rows]})
    finally:
        conn.close()


@app.post("/api/admin/employees")
@roles_required("super_admin", "branch_manager")
def create_employee():
    payload = request.get_json(silent=True) or {}
    full_name = str(payload.get("full_name") or "").strip()
    job_title = str(payload.get("job_title") or "").strip()
    phone = re.sub(r"[^0-9]", "", str(payload.get("phone_number") or ""))
    if phone.startswith("07") and len(phone) == 10:
        phone = "962" + phone[1:]
    branch_id = payload.get("branch_id") or g.current_user["branch_id"]
    if not full_name or not job_title or not (10 <= len(phone) <= 15) or not branch_id:
        return _json_error("أدخل الاسم والمسمى الوظيفي ورقم هاتف صحيحًا.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if not _branch_allowed(cur, g.current_user, branch_id):
                conn.rollback()
                return _json_error("لا تملك صلاحية إنشاء موظف في هذا الفرع.", 403)
            cur.execute("SELECT 1 FROM branches WHERE id=%s AND is_active=TRUE", (branch_id,))
            if not cur.fetchone():
                conn.rollback()
                return _json_error("الفرع غير موجود أو غير نشط.", 400)
            cur.execute(
                """INSERT INTO employees(employee_code,phone_number,full_name,job_title,branch_id,is_active)
                   VALUES(%s,%s,%s,%s,%s,TRUE) RETURNING id,employee_code""",
                ("EMP" + secrets.token_hex(4).upper(), phone, full_name, job_title, branch_id))
            employee = cur.fetchone()
            _audit(cur, g.current_user["id"], branch_id, "employee_created", "employee", employee["id"])
        conn.commit()
        return jsonify({"ok": True, "employee": dict(employee)}), 201
    except psycopg2.IntegrityError:
        conn.rollback()
        return _json_error("رقم الهاتف مسجل لموظف نشط بالفعل.", 409)
    finally:
        conn.close()


@app.post("/api/admin/employees/<int:employee_id>/activation")
@roles_required("super_admin", "branch_manager")
def create_activation(employee_id):
    raw = secrets.token_urlsafe(32)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id,branch_id,is_active FROM employees WHERE id=%s FOR UPDATE", (employee_id,))
            employee = cur.fetchone()
            if not employee or not employee["is_active"]:
                conn.rollback()
                return _json_error("الموظف غير موجود أو غير نشط.", 404)
            if not _branch_allowed(cur, g.current_user, employee["branch_id"]):
                conn.rollback()
                return _json_error("لا تملك صلاحية هذا الفرع.", 403)
            cur.execute("SELECT 1 FROM web_users WHERE employee_id=%s AND is_active=TRUE", (employee_id,))
            if cur.fetchone():
                conn.rollback()
                return _json_error("لدى الموظف حساب ويب مفعّل بالفعل.", 409)
            cur.execute(
                """INSERT INTO web_activation_tokens(employee_id,token_hash,expires_at,created_by)
                   VALUES(%s,%s,CURRENT_TIMESTAMP + INTERVAL '24 hours',%s)""",
                (employee_id, _hash(raw), g.current_user["id"]))
            _audit(cur, g.current_user["id"], employee["branch_id"], "employee_activation_issued",
                   "employee", employee_id)
        conn.commit()
        return jsonify({"ok": True, "activation_code": raw,
                        "expires_in_hours": 24,
                        "message": "اعرض الرمز للموظف مرة واحدة فقط. لا تحفظه في الملاحظات أو السجلات."}), 201
    finally:
        conn.close()


@app.get("/api/admin/requests")
@roles_required("super_admin", "branch_manager", "supervisor")
def admin_requests():
    status = request.args.get("status", "pending")
    if status not in {"pending", "approved", "rejected", "all"}:
        return _json_error("حالة الطلب غير صالحة.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            sql = """SELECT r.id,r.request_type,COALESCE(rt.label,r.request_type) AS request_label,
                            r.status,r.requested_at,r.notes,
                            e.id AS employee_id,e.employee_code,e.full_name,e.job_title,
                            b.name AS branch_name
                     FROM requests r JOIN employees e ON e.id=r.employee_id
                     JOIN branches b ON b.id=e.branch_id
                     LEFT JOIN branch_request_types rt
                       ON rt.branch_id=e.branch_id AND rt.code=r.request_type"""
            clauses, params = [], []
            if status != "all":
                clauses.append("r.status=%s")
                params.append(status)
            if g.current_user["role"] != "super_admin":
                clauses.append("e.branch_id=%s")
                params.append(g.current_user["branch_id"])
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY r.requested_at DESC LIMIT 100"
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return jsonify({"ok": True, "requests": [dict(x) for x in rows]})
    finally:
        conn.close()


@app.post("/api/admin/requests/<int:request_id>/decision")
@roles_required("super_admin", "branch_manager", "supervisor")
def decide_employee_request(request_id):
    payload = request.get_json(silent=True) or {}
    decision = payload.get("decision")
    if decision not in {"approved", "rejected"}:
        return _json_error("قرار الطلب غير صالح.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT r.id,r.employee_id,r.request_type,r.status,e.branch_id,e.full_name,
                          u.id AS employee_user_id
                   FROM requests r JOIN employees e ON e.id=r.employee_id
                   JOIN branches b ON b.id=e.branch_id AND b.is_active=TRUE
                   LEFT JOIN web_users u ON u.employee_id=e.id AND u.is_active=TRUE
                   WHERE r.id=%s FOR UPDATE OF r,e""", (request_id,))
            row = cur.fetchone()
            if not row or row["status"] != "pending":
                conn.rollback()
                return _json_error("الطلب غير موجود أو تمت معالجته مسبقًا.", 409)
            if not _branch_allowed(cur, g.current_user, row["branch_id"]):
                conn.rollback()
                return _json_error("لا تملك صلاحية هذا الفرع.", 403)
            cur.execute(
                """SELECT duration_minutes,label FROM branch_request_types
                   WHERE branch_id=%s AND code=%s AND is_active=TRUE""",
                (row["branch_id"], row["request_type"]))
            request_kind = cur.fetchone()
            if decision == "approved" and request_kind:
                cur.execute(
                    """SELECT 1 FROM web_timers WHERE employee_id=%s
                       AND returned_at IS NULL FOR UPDATE""",
                    (row["employee_id"],))
                if cur.fetchone():
                    conn.rollback()
                    return _json_error("لدى الموظف مؤقت نشط بالفعل.", 409)
            cur.execute(
                """UPDATE requests SET status=%s,responded_at=CURRENT_TIMESTAMP,
                   decided_by_web_user_id=%s,decided_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE id=%s AND status='pending'""",
                (decision, g.current_user["id"], request_id))
            if decision == "approved" and request_kind:
                cur.execute(
                    """INSERT INTO web_timers(employee_id,request_id,request_type,started_at,ends_at,created_by)
                       VALUES(%s,%s,%s,CURRENT_TIMESTAMP,
                         CURRENT_TIMESTAMP + (%s * INTERVAL '1 minute'),%s)""",
                    (row["employee_id"], request_id, row["request_type"],
                     request_kind["duration_minutes"], g.current_user["id"]))
            if row["employee_user_id"]:
                title = "تمت الموافقة على طلبك" if decision == "approved" else "تم رفض طلبك"
                body = f"طلب {request_kind['label'] if request_kind else row['request_type']}"
                cur.execute(
                    """INSERT INTO web_notifications(recipient_user_id,employee_id,request_id,
                       notification_type,title,body) VALUES(%s,%s,%s,%s,%s,%s)""",
                    (row["employee_user_id"], row["employee_id"], request_id,
                     "request_" + decision, title, body))
            _audit(cur, g.current_user["id"], row["branch_id"], "request_" + decision,
                   "request", request_id)
        conn.commit()
        return jsonify({"ok": True, "message": "تم تحديث حالة الطلب."})
    except psycopg2.IntegrityError:
        conn.rollback()
        return _json_error("تعذر بدء المؤقت؛ تحقق من وجود مؤقت آخر للموظف.", 409)
    finally:
        conn.close()


@app.get("/api/admin/users")
@roles_required("super_admin")
def admin_users():
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT id,username,full_name,role,branch_id,is_active,created_at,last_login_at
                   FROM web_users WHERE role<>'employee' ORDER BY role,full_name""")
            rows = cur.fetchall()
        return jsonify({"ok": True, "users": [dict(x) for x in rows]})
    finally:
        conn.close()


@app.post("/api/admin/users")
@roles_required("super_admin")
def create_admin_user():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    full_name = str(payload.get("full_name") or "").strip()
    role = payload.get("role")
    branch_id = payload.get("branch_id")
    password = str(payload.get("temporary_password") or "")
    if role not in {"supervisor", "branch_manager"} or not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        return _json_error("بيانات حساب المشرف غير صالحة.", 400)
    if len(password) < 12 or len(full_name) < 2:
        return _json_error("أدخل اسمًا وكلمة مرور مؤقتة من 12 خانة على الأقل.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM branches WHERE id=%s AND is_active=TRUE", (branch_id,))
            if not cur.fetchone():
                conn.rollback()
                return _json_error("الفرع غير موجود أو غير نشط.", 400)
            cur.execute(
                """INSERT INTO web_users(username,password_hash,role,branch_id,full_name,must_change_password)
                   VALUES(%s,%s,%s,%s,%s,TRUE) RETURNING id,username,role,branch_id""",
                (username, generate_password_hash(password, method="scrypt"), role, branch_id, full_name))
            user = cur.fetchone()
            _audit(cur, g.current_user["id"], branch_id, "admin_user_created", "web_user", user["id"])
        conn.commit()
        return jsonify({"ok": True, "user": dict(user)}), 201
    except psycopg2.IntegrityError:
        conn.rollback()
        return _json_error("اسم المستخدم مستخدم مسبقًا.", 409)
    finally:
        conn.close()


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return _json_error("المسار غير موجود.", 404)
    return send_from_directory(WEB_DIR, "index.html")


@app.errorhandler(500)
def server_error(error):
    logger.exception("Unhandled application error: %s", error)
    if request.path.startswith("/api/"):
        return _json_error("حدث خطأ داخلي. حاول لاحقًا.", 500)
    return "حدث خطأ داخلي.", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
