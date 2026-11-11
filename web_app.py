"""Secure Flask API and server-side authorization for the Employee Portal PWA."""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
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
JORDAN_TZ = ZoneInfo("Asia/Amman")

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
                       b.name AS branch_name, b.is_active AS branch_active, e.employee_code, e.job_title,
                       e.is_active AS employee_active, eb.is_active AS employee_branch_active
                FROM web_sessions s
                JOIN web_users u ON u.id=s.user_id
                LEFT JOIN branches b ON b.id=u.branch_id
                LEFT JOIN employees e ON e.id=u.employee_id
                LEFT JOIN branches eb ON eb.id=e.branch_id
                WHERE s.token_hash=%s AND s.revoked_at IS NULL
                  AND s.expires_at > CURRENT_TIMESTAMP AND u.is_active=TRUE
                """,
                (_hash(token),),
            )
            row = cur.fetchone()
            if (not row or (row["employee_id"] and (not row["employee_active"] or not row["employee_branch_active"]))
                    or (row["role"] in {"branch_manager", "supervisor"} and not row["branch_active"])):
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



def _jordan_now():
    return datetime.now(JORDAN_TZ)


def _within_time_window(current_time, start_time=None, end_time=None):
    if start_time is None and end_time is None:
        return True
    if start_time is None:
        return current_time < end_time
    if end_time is None:
        return current_time >= start_time
    if start_time <= end_time:
        return start_time <= current_time < end_time
    return current_time >= start_time or current_time < end_time


def _special_request_policy(cur, employee_id, branch_id, request_type, lock=False):
    """Validate the existing branch smoking/lunch policy using Jordan local time."""
    suffix = " FOR UPDATE" if lock else " FOR SHARE"
    cur.execute(
        """INSERT INTO branch_settings(branch_id) VALUES(%s) ON CONFLICT DO NOTHING""",
        (branch_id,),
    )
    cur.execute(
        """SELECT smoke_enabled,smoke_daily_limit,smoke_duration_minutes,
                  smoke_start_time,smoke_end_time,smoke_gap_minutes,
                  lunch_enabled,lunch_duration_minutes,lunch_start_time,lunch_end_time
           FROM branch_settings WHERE branch_id=%s""" + suffix,
        (branch_id,),
    )
    row = cur.fetchone()
    if not row:
        return False, "تعذر تحميل سياسة الفرع.", None, None
    now = _jordan_now()
    current_time = now.time().replace(tzinfo=None)
    today = now.date()
    if request_type == "smoke":
        enabled = row["smoke_enabled"]
        limit = row["smoke_daily_limit"]
        duration = row["smoke_duration_minutes"]
        start = row["smoke_start_time"]
        end = row["smoke_end_time"]
        gap = row["smoke_gap_minutes"]
        if not enabled:
            return False, "استراحة التدخين غير مفعّلة في هذا الفرع.", "استراحة تدخين", duration
        if not _within_time_window(current_time, start, end):
            return False, "الطلب خارج الوقت المسموح لاستراحة التدخين.", "استراحة تدخين", duration
        cur.execute(
            "SELECT count FROM daily_cigarettes WHERE employee_id=%s AND date=%s",
            (employee_id, today),
        )
        count_row = cur.fetchone()
        count = int(count_row["count"]) if count_row else 0
        if count >= int(limit):
            return False, "وصلت إلى الحد اليومي المسموح لاستراحات التدخين.", "استراحة تدخين", duration
        cur.execute(
            """SELECT taken_at FROM cigarette_times
               WHERE employee_id=%s ORDER BY taken_at DESC LIMIT 1""",
            (employee_id,),
        )
        last = cur.fetchone()
        last_at = last["taken_at"] if last else None
        if last_at and (now - last_at).total_seconds() < int(gap) * 60:
            remaining = int(gap) - int((now - last_at).total_seconds() // 60)
            return False, f"يجب الانتظار {max(1, remaining)} دقيقة قبل طلب استراحة تدخين أخرى.", "استراحة تدخين", duration
        return True, None, "استراحة تدخين", int(duration)
    if request_type in {"break", "lunch"}:
        enabled = row["lunch_enabled"]
        duration = row["lunch_duration_minutes"]
        start = row["lunch_start_time"]
        end = row["lunch_end_time"]
        if not enabled:
            return False, "استراحة الغداء غير مفعّلة في هذا الفرع.", "استراحة الغداء", duration
        if not _within_time_window(current_time, start, end):
            return False, "الطلب خارج الوقت المسموح لاستراحة الغداء.", "استراحة الغداء", duration
        cur.execute(
            "SELECT 1 FROM lunch_breaks WHERE employee_id=%s AND date=%s AND taken=TRUE FOR UPDATE",
            (employee_id, today),
        )
        if cur.fetchone():
            return False, "تم تسجيل استراحة الغداء لهذا اليوم بالفعل.", "استراحة الغداء", duration
        return True, None, "استراحة الغداء", int(duration)
    return False, "نوع الطلب غير مدعوم.", None, None


def _record_special_request_approval(cur, employee_id, request_type, duration):
    now = _jordan_now()
    today = now.date()
    if request_type == "smoke":
        cur.execute(
            """SELECT smoke_daily_limit FROM branch_settings
               WHERE branch_id=(SELECT branch_id FROM employees WHERE id=%s) FOR UPDATE""",
            (employee_id,),
        )
        row = cur.fetchone()
        limit = int(row["smoke_daily_limit"])
        cur.execute(
            """INSERT INTO daily_cigarettes(employee_id,date,count,updated_at)
               SELECT %s,%s,1,CURRENT_TIMESTAMP WHERE %s>0
               ON CONFLICT(employee_id,date)
               DO UPDATE SET count=daily_cigarettes.count+1,updated_at=CURRENT_TIMESTAMP
               WHERE daily_cigarettes.count<%s
               RETURNING count""",
            (employee_id, today, limit, limit),
        )
        if not cur.fetchone():
            raise ValueError("تم الوصول إلى الحد اليومي لاستراحات التدخين.")
        cur.execute(
            "INSERT INTO cigarette_times(employee_id,taken_at) VALUES(%s,%s)",
            (employee_id, now),
        )
        return
    if request_type in {"break", "lunch"}:
        cur.execute(
            """INSERT INTO lunch_breaks(employee_id,date,taken,taken_at)
               VALUES(%s,%s,TRUE,%s)
               ON CONFLICT(employee_id,date) DO UPDATE
                 SET taken=TRUE,taken_at=EXCLUDED.taken_at
                 WHERE lunch_breaks.taken=FALSE
               RETURNING id""",
            (employee_id, today, now),
        )
        if not cur.fetchone():
            raise ValueError("تم تسجيل استراحة الغداء لهذا اليوم بالفعل.")


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
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    if app.config["COOKIE_SECURE"]:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
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
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
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
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
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
                   JOIN branches b ON b.id=e.branch_id AND b.is_active=TRUE
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
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
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
                          CASE WHEN r.request_type='smoke' THEN 'استراحة تدخين'
                               WHEN r.request_type IN ('break','lunch') THEN 'استراحة الغداء'
                               ELSE COALESCE(t.label,r.request_type) END AS request_label
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
                """SELECT e.branch_id FROM employees e JOIN branches b ON b.id=e.branch_id
                   WHERE e.id=%s AND e.is_active=TRUE AND b.is_active=TRUE""",
                (_employee_id(g.current_user),))
            employee = cur.fetchone()
            if not employee:
                return _json_error("حساب الموظف غير نشط.", 403)
            branch_id = employee["branch_id"]
            cur.execute("INSERT INTO branch_settings(branch_id) VALUES(%s) ON CONFLICT DO NOTHING", (branch_id,))
            cur.execute(
                """SELECT r.id,r.code,r.label,r.duration_minutes
                   FROM branch_request_types r
                   WHERE r.branch_id=%s AND r.is_active=TRUE ORDER BY r.id""", (branch_id,))
            rows = [dict(x) for x in cur.fetchall()]
            cur.execute(
                """SELECT smoke_enabled,smoke_duration_minutes,lunch_enabled,lunch_duration_minutes
                   FROM branch_settings WHERE branch_id=%s""", (branch_id,))
            settings = cur.fetchone()
            if settings and settings["smoke_enabled"]:
                rows.insert(0, {"id": None, "code": "smoke", "label": "استراحة تدخين",
                                "duration_minutes": settings["smoke_duration_minutes"]})
            if settings and settings["lunch_enabled"]:
                rows.insert(1 if settings["smoke_enabled"] else 0,
                            {"id": None, "code": "break", "label": "استراحة الغداء",
                             "duration_minutes": settings["lunch_duration_minutes"]})
            conn.commit()
        return jsonify({"ok": True, "request_types": rows})
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
                          CASE WHEN r.request_type='smoke' THEN 'استراحة تدخين' WHEN r.request_type IN ('break','lunch') THEN 'استراحة الغداء' ELSE COALESCE(t.label,r.request_type) END AS request_label
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
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
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
            if request_code in {"smoke", "break", "lunch"}:
                allowed, policy_error, policy_label, policy_duration = _special_request_policy(
                    cur, employee["id"], employee["branch_id"], request_code, lock=True)
                if not allowed:
                    conn.rollback()
                    return _json_error(policy_error or "الطلب غير متاح وفق سياسة الفرع.", 409)
                kind = {"id": None, "label": policy_label, "duration_minutes": policy_duration}
            else:
                cur.execute(
                    """SELECT id,label,duration_minutes FROM branch_request_types
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
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
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


@app.post("/api/admin/branches")
@roles_required("super_admin")
def create_branch():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    code = str(payload.get("code") or "").strip().lower()
    name = str(payload.get("name") or "").strip()
    if not re.fullmatch(r"[a-z0-9_-]{2,24}", code) or not 2 <= len(name) <= 80:
        return _json_error("أدخل رمز فرع من 2 إلى 24 حرفًا/رقمًا واسمًا صحيحًا.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO branches(code,name,is_active) VALUES(%s,%s,TRUE) RETURNING id,code,name,is_active",
                (code, name))
            branch = cur.fetchone()
            cur.execute("INSERT INTO branch_settings(branch_id) VALUES(%s) ON CONFLICT DO NOTHING",
                        (branch["id"],))
            cur.execute(
                """INSERT INTO branch_request_types(branch_id,code,label,duration_minutes,is_active)
                   VALUES(%s,'bathroom','🚻 استئذان الذهاب إلى الحمام',10,TRUE),
                         (%s,'prayer','🕌 استئذان الذهاب إلى الصلاة',10,TRUE)
                   ON CONFLICT(branch_id,code) DO NOTHING""",
                (branch["id"], branch["id"]))
            _audit(cur, g.current_user["id"], branch["id"], "branch_created", "branch", branch["id"])
        conn.commit()
        return jsonify({"ok": True, "branch": dict(branch), "message": "تم إنشاء الفرع وتجهيز إعداداته وأزرار الطلبات الأساسية."}), 201
    except psycopg2.IntegrityError:
        conn.rollback()
        return _json_error("رمز الفرع أو اسمه مستخدم مسبقًا.", 409)
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
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
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
            cur.execute("""SELECT e.id,e.branch_id,e.is_active FROM employees e
                           JOIN branches b ON b.id=e.branch_id AND b.is_active=TRUE
                           WHERE e.id=%s FOR UPDATE OF e""", (employee_id,))
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


@app.post("/api/admin/employees/<int:employee_id>/reset-password")
@roles_required("super_admin", "branch_manager")
def reset_employee_password(employee_id):
    temporary_password = secrets.token_urlsafe(14)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT e.id,e.branch_id,e.is_active FROM employees e
                   JOIN branches b ON b.id=e.branch_id AND b.is_active=TRUE
                   WHERE e.id=%s FOR UPDATE OF e""",
                (employee_id,))
            employee = cur.fetchone()
            if not employee or not employee["is_active"]:
                conn.rollback()
                return _json_error("الموظف غير موجود أو غير نشط.", 404)
            if not _branch_allowed(cur, g.current_user, employee["branch_id"]):
                conn.rollback()
                return _json_error("لا تملك صلاحية هذا الفرع.", 403)
            cur.execute("SELECT id FROM web_users WHERE employee_id=%s AND is_active=TRUE FOR UPDATE",
                        (employee_id,))
            account = cur.fetchone()
            if not account:
                conn.rollback()
                return _json_error("الموظف لم يفعّل حساب الويب بعد.", 409)
            cur.execute(
                """UPDATE web_users SET password_hash=%s,must_change_password=TRUE,
                   failed_login_attempts=0,locked_until=NULL,updated_at=CURRENT_TIMESTAMP
                   WHERE id=%s""",
                (generate_password_hash(temporary_password, method="scrypt"), account["id"]))
            cur.execute(
                "UPDATE web_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=%s AND revoked_at IS NULL",
                (account["id"],))
            _audit(cur, g.current_user["id"], employee["branch_id"], "employee_password_reset",
                   "employee", employee_id)
        conn.commit()
        return jsonify({"ok": True, "temporary_password": temporary_password,
                        "message": "تم تعيين كلمة مرور مؤقتة. سلّمها للموظف بشكل خاص."})
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
            sql = """SELECT r.id,r.request_type,CASE WHEN r.request_type='smoke' THEN 'استراحة تدخين'
                               WHEN r.request_type IN ('break','lunch') THEN 'استراحة الغداء'
                               ELSE COALESCE(rt.label,r.request_type) END AS request_label,
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
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
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
            is_special = row["request_type"] in {"smoke", "break", "lunch"}
            timer_duration = None
            request_label = request_kind["label"] if request_kind else row["request_type"]
            if decision == "approved":
                if is_special:
                    allowed, policy_error, policy_label, policy_duration = _special_request_policy(
                        cur, row["employee_id"], row["branch_id"], row["request_type"], lock=True)
                    if not allowed:
                        conn.rollback()
                        return _json_error(policy_error or "تعذر اعتماد الطلب وفق سياسة الفرع.", 409)
                    timer_duration = policy_duration
                    request_label = policy_label
                elif request_kind:
                    timer_duration = int(request_kind["duration_minutes"])
                if timer_duration is not None:
                    cur.execute(
                        """SELECT 1 FROM web_timers WHERE employee_id=%s
                           AND returned_at IS NULL FOR UPDATE""",
                        (row["employee_id"],))
                    if cur.fetchone():
                        conn.rollback()
                        return _json_error("لدى الموظف مؤقت لم تُسجّل عودته بعد.", 409)
            cur.execute(
                """UPDATE requests SET status=%s,responded_at=CURRENT_TIMESTAMP,
                   decided_by_web_user_id=%s,decided_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE id=%s AND status='pending'""",
                (decision, g.current_user["id"], request_id))
            if decision == "approved" and is_special:
                _record_special_request_approval(cur, row["employee_id"], row["request_type"], timer_duration)
            if decision == "approved" and timer_duration is not None:
                cur.execute(
                    """INSERT INTO web_timers(employee_id,request_id,request_type,started_at,ends_at,created_by)
                       VALUES(%s,%s,%s,CURRENT_TIMESTAMP,
                         CURRENT_TIMESTAMP + (%s * INTERVAL '1 minute'),%s)""",
                    (row["employee_id"], request_id, row["request_type"],
                     timer_duration, g.current_user["id"]))
            if row["employee_user_id"]:
                title = "تمت الموافقة على طلبك" if decision == "approved" else "تم رفض طلبك"
                body = f"طلب {request_label}"
                cur.execute(
                    """INSERT INTO web_notifications(recipient_user_id,employee_id,request_id,
                       notification_type,title,body) VALUES(%s,%s,%s,%s,%s,%s)""",
                    (row["employee_user_id"], row["employee_id"], request_id,
                     "request_" + decision, title, body))
            _audit(cur, g.current_user["id"], row["branch_id"], "request_" + decision,
                   "request", request_id)
        conn.commit()
        return jsonify({"ok": True, "message": "تم تحديث حالة الطلب."})
    except ValueError as exc:
        conn.rollback()
        return _json_error(str(exc), 409)
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
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
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



def _resolve_admin_branch(cur, user, requested=None):
    if requested is not None:
        try:
            requested = int(requested)
            if requested < 1:
                return None
        except (TypeError, ValueError):
            return None
    if user["role"] == "super_admin":
        if requested is None:
            cur.execute("""SELECT id FROM branches WHERE is_active=TRUE
                           ORDER BY CASE WHEN code='main' THEN 0 ELSE 1 END, id LIMIT 1""")
        else:
            cur.execute("SELECT id FROM branches WHERE id=%s AND is_active=TRUE", (requested,))
    else:
        if requested is not None and requested != int(user["branch_id"]):
            return None
        cur.execute("SELECT id FROM branches WHERE id=%s AND is_active=TRUE", (user["branch_id"],))
    row = cur.fetchone()
    return row["id"] if row else None


@app.get("/api/admin/settings")
@roles_required("super_admin", "branch_manager", "supervisor")
def admin_settings():
    requested = request.args.get("branch_id")
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            branch_id = _resolve_admin_branch(cur, g.current_user, requested)
            if not branch_id:
                conn.rollback()
                return _json_error("الفرع غير موجود أو لا تملك صلاحية الوصول إليه.", 403)
            cur.execute("INSERT INTO branch_settings(branch_id) VALUES(%s) ON CONFLICT DO NOTHING", (branch_id,))
            cur.execute(
                """SELECT branch_id,smoke_enabled,smoke_daily_limit,smoke_duration_minutes,
                          TO_CHAR(smoke_start_time,'HH24:MI') AS smoke_start_time,
                          TO_CHAR(smoke_end_time,'HH24:MI') AS smoke_end_time,
                          smoke_gap_minutes,lunch_enabled,lunch_duration_minutes,
                          TO_CHAR(lunch_start_time,'HH24:MI') AS lunch_start_time,
                          TO_CHAR(lunch_end_time,'HH24:MI') AS lunch_end_time
                   FROM branch_settings WHERE branch_id=%s""", (branch_id,))
            settings = cur.fetchone()
            cur.execute("SELECT id,name FROM branches WHERE id=%s", (branch_id,))
            branch = cur.fetchone()
        conn.commit()
        return jsonify({"ok": True, "branch": dict(branch), "settings": dict(settings)})
    finally:
        conn.close()


@app.patch("/api/admin/settings")
@roles_required("super_admin", "branch_manager", "supervisor")
def update_admin_settings():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    requested = payload.pop("branch_id", None)
    bool_fields = {"smoke_enabled", "lunch_enabled"}
    int_fields = {
        "smoke_daily_limit": (0, 30),
        "smoke_duration_minutes": (1, 120),
        "smoke_gap_minutes": (0, 1440),
        "lunch_duration_minutes": (1, 240),
    }
    time_fields = {"smoke_start_time", "smoke_end_time", "lunch_start_time", "lunch_end_time"}
    allowed = bool_fields | set(int_fields) | time_fields
    if not payload or not set(payload).issubset(allowed):
        return _json_error("لا توجد إعدادات صالحة للحفظ.", 400)
    for key, value in payload.items():
        if key in bool_fields and not isinstance(value, bool):
            return _json_error("قيمة التفعيل يجب أن تكون صحيحة أو خاطئة.", 400)
        if key in int_fields and (isinstance(value, bool) or not isinstance(value, int) or not int_fields[key][0] <= value <= int_fields[key][1]):
            return _json_error("إحدى القيم الرقمية خارج النطاق المسموح.", 400)
        if key in time_fields and value is not None and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(value)):
            return _json_error("استخدم صيغة الوقت HH:MM.", 400)
        if key == "smoke_start_time" and value is None:
            return _json_error("يجب تحديد بداية وقت الاستراحة.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            branch_id = _resolve_admin_branch(cur, g.current_user, requested)
            if not branch_id:
                conn.rollback()
                return _json_error("الفرع غير موجود أو لا تملك صلاحية الوصول إليه.", 403)
            cur.execute("INSERT INTO branch_settings(branch_id) VALUES(%s) ON CONFLICT DO NOTHING", (branch_id,))
            cur.execute("SELECT * FROM branch_settings WHERE branch_id=%s FOR UPDATE", (branch_id,))
            old = cur.fetchone()
            assignments = [f"{key}=%s" for key in payload]
            values = list(payload.values())
            assignments.extend(["updated_by_web_user_id=%s", "updated_at=CURRENT_TIMESTAMP"])
            values.extend([g.current_user["id"], branch_id])
            cur.execute(
                "UPDATE branch_settings SET " + ", ".join(assignments) + " WHERE branch_id=%s",
                tuple(values),
            )
            for key, value in payload.items():
                old_value = old.get(key)
                cur.execute(
                    """INSERT INTO branch_settings_audit
                       (branch_id,setting_key,old_value,new_value,changed_by,changed_by_web_user_id)
                       VALUES(%s,%s,%s,%s,NULL,%s)""",
                    (branch_id, key, str(old_value) if old_value is not None else None,
                     str(value) if value is not None else None, g.current_user["id"]),
                )
            _audit(cur, g.current_user["id"], branch_id, "branch_settings_updated",
                   "branch", branch_id, {"fields": list(payload)})
        conn.commit()
        return jsonify({"ok": True, "message": "تم حفظ إعدادات الفرع."})
    finally:
        conn.close()


@app.get("/api/admin/request-types")
@roles_required("super_admin", "branch_manager", "supervisor")
def admin_request_types():
    requested = request.args.get("branch_id")
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            branch_id = _resolve_admin_branch(cur, g.current_user, requested)
            if not branch_id:
                conn.rollback()
                return _json_error("الفرع غير موجود أو لا تملك صلاحية الوصول إليه.", 403)
            cur.execute(
                """SELECT id,code,label,duration_minutes,is_active
                   FROM branch_request_types WHERE branch_id=%s ORDER BY id""",
                (branch_id,))
            rows = cur.fetchall()
        return jsonify({"ok": True, "request_types": [dict(row) for row in rows]})
    finally:
        conn.close()


@app.post("/api/admin/request-types")
@roles_required("super_admin", "branch_manager", "supervisor")
def create_admin_request_type():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    label = str(payload.get("label") or "").strip()
    duration = payload.get("duration_minutes")
    requested = payload.get("branch_id")
    if not label or len(label) > 32 or isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 240:
        return _json_error("أدخل اسمًا لا يتجاوز 32 حرفًا ومدة بين دقيقة و240 دقيقة.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            branch_id = _resolve_admin_branch(cur, g.current_user, requested)
            if not branch_id:
                conn.rollback()
                return _json_error("الفرع غير موجود أو لا تملك صلاحية الوصول إليه.", 403)
            code = "web-" + secrets.token_hex(6)
            cur.execute(
                """INSERT INTO branch_request_types
                   (branch_id,code,label,duration_minutes,is_active,created_by_web_user_id,updated_by_web_user_id)
                   VALUES(%s,%s,%s,%s,TRUE,%s,%s) RETURNING id,code,label,duration_minutes,is_active""",
                (branch_id, code, label, duration, g.current_user["id"], g.current_user["id"]))
            row = cur.fetchone()
            cur.execute(
                """INSERT INTO branch_request_types_audit
                   (branch_id,request_type_id,action,changed_by,new_value,changed_by_web_user_id)
                   VALUES(%s,%s,'created',NULL,%s,%s)""",
                (branch_id, row["id"], f"{label} | {duration} minutes", g.current_user["id"]))
            _audit(cur, g.current_user["id"], branch_id, "request_type_created", "request_type", row["id"])
        conn.commit()
        return jsonify({"ok": True, "request_type": dict(row), "message": "تمت إضافة نوع الطلب."}), 201
    except psycopg2.IntegrityError:
        conn.rollback()
        return _json_error("يوجد نوع طلب بالاسم نفسه في هذا الفرع.", 409)
    finally:
        conn.close()


@app.patch("/api/admin/request-types/<int:type_id>")
@roles_required("super_admin", "branch_manager", "supervisor")
def update_admin_request_type(type_id):
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    requested = payload.pop("branch_id", None)
    duration = payload.get("duration_minutes")
    active = payload.get("is_active")
    if not payload or not set(payload).issubset({"duration_minutes", "is_active"}):
        return _json_error("لا توجد تغييرات صالحة.", 400)
    if "duration_minutes" in payload and (isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 240):
        return _json_error("المدة يجب أن تكون بين دقيقة و240 دقيقة.", 400)
    if "is_active" in payload and not isinstance(active, bool):
        return _json_error("حالة التفعيل غير صالحة.", 400)
    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            branch_id = _resolve_admin_branch(cur, g.current_user, requested)
            if not branch_id:
                conn.rollback()
                return _json_error("الفرع غير موجود أو لا تملك صلاحية الوصول إليه.", 403)
            cur.execute(
                """SELECT id,label,duration_minutes,is_active FROM branch_request_types
                   WHERE id=%s AND branch_id=%s FOR UPDATE""", (type_id, branch_id))
            old = cur.fetchone()
            if not old:
                conn.rollback()
                return _json_error("نوع الطلب غير موجود في هذا الفرع.", 404)
            sets, values = [], []
            if "duration_minutes" in payload:
                sets.append("duration_minutes=%s")
                values.append(duration)
            if "is_active" in payload:
                sets.append("is_active=%s")
                values.append(active)
            sets.extend(["updated_by_web_user_id=%s", "updated_at=CURRENT_TIMESTAMP"])
            values.extend([g.current_user["id"], type_id, branch_id])
            cur.execute(
                "UPDATE branch_request_types SET " + ", ".join(sets) + " WHERE id=%s AND branch_id=%s",
                tuple(values))
            for key, value in payload.items():
                cur.execute(
                    """INSERT INTO branch_request_types_audit
                       (branch_id,request_type_id,action,changed_by,new_value,changed_by_web_user_id)
                       VALUES(%s,%s,'updated',NULL,%s,%s)""",
                    (branch_id, type_id, f"{key}={value}", g.current_user["id"]))
            _audit(cur, g.current_user["id"], branch_id, "request_type_updated", "request_type", type_id, payload)
        conn.commit()
        return jsonify({"ok": True, "message": "تم تحديث نوع الطلب."})
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


# Register additive admin APIs after the core app and its security decorators exist.
# This must run before app.run so direct local execution and Gunicorn expose the same routes.
import web_admin_extensions  # noqa: E402,F401


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
