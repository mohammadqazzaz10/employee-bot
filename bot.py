import os
import logging
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes
from branch_settings import get_branch_settings, register_settings_handlers
from request_types import get_employee_buttons, get_request_type, get_request_type_by_code, register_request_type_handlers
from broadcast import register_broadcast_handlers, restore_pending_broadcast_reminders

# تعريف مراحل المحادثة
LEAVE_REASON, VACATION_REASON = range(2)

# --- إعدادات البيئة لـ Telegram و Webhook ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DATABASE_SSLMODE = os.environ.get("DATABASE_SSLMODE", "prefer").strip() or "prefer"
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_URL = (os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()

def _parse_telegram_id(value, variable_name):
    if not value:
        return None
    try:
        parsed = int(value)
        if parsed <= 0:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise RuntimeError(f"{variable_name} must be a positive Telegram numeric user ID")

SUPER_ADMIN_TELEGRAM_ID = _parse_telegram_id(
    os.environ.get("SUPER_ADMIN_TELEGRAM_ID", "").strip(),
    "SUPER_ADMIN_TELEGRAM_ID",
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- إعدادات المديرين ---
# The first administrator is provisioned from SUPER_ADMIN_TELEGRAM_ID into PostgreSQL.
daily_smoke_count = {}

# --- إعدادات السجائر الجديدة ---

JORDAN_TZ = ZoneInfo('Asia/Amman')

active_timers = {}
timer_completed = {}

def clear_interactive_drafts(context):
    """Prevent an unfinished manager/employee workflow from consuming another flow's input."""
    for key in (
        "pending_broadcast",
        "pending_branch_setting",
        "new_request_type_state",
        "editing_request_type",
        "new_request_type_label",
        "managed_request_branch",
    ):
        context.user_data.pop(key, None)


def get_db_connection():
    """Connect to PostgreSQL using the URL configured by the hosting provider."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(
        DATABASE_URL,
        sslmode=DATABASE_SSLMODE,
        connect_timeout=10,
        application_name="employee-bot",
    )

REQUIRED_MIGRATIONS = (
    "001_multibranch_foundation.sql",
    "002_configurable_request_types.sql",
    "003_broadcast_logs.sql",
    "004_broadcast_acknowledgements.sql",
    "005_preproduction_integrity.sql",
    "006_timer_return_acknowledgements.sql",
)


def verify_required_migrations():
    """Fail closed unless every required migration is recorded as applied."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
        missing = [version for version in REQUIRED_MIGRATIONS if version not in applied]
        if missing:
            logger.critical(
                "Required database migrations are missing: %s. Run python -m scripts.migrate.",
                ", ".join(missing),
            )
            return False
        return True
    except Exception:
        logger.exception(
            "Could not verify database migrations. Run python -m scripts.migrate --check."
        )
        return False
    finally:
        if conn:
            conn.close()


def initialize_database_tables():
    """إنشاء الجداول المطلوبة"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # جدول الموظفين
        cur.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                phone_number VARCHAR(20) NOT NULL,
                full_name VARCHAR(100) NOT NULL,
                age INTEGER,
                job_title VARCHAR(100),
                department VARCHAR(100),
                hire_date DATE,
                last_active TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            );
        """)
        cur.execute("""
            ALTER TABLE employees
                ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE,
                ADD COLUMN IF NOT EXISTS job_title VARCHAR(100),
                ADD COLUMN IF NOT EXISTS last_active TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;
        """)
        # Normalize legacy phone strings and Jordanian local mobile numbers once.
        cur.execute("""
            UPDATE employees
            SET phone_number = CASE
                WHEN regexp_replace(phone_number, '[^0-9]', '', 'g') ~ '^07[0-9]{8}$'
                    THEN '962' || SUBSTRING(regexp_replace(phone_number, '[^0-9]', '', 'g') FROM 2)
                ELSE regexp_replace(phone_number, '[^0-9]', '', 'g')
            END
            WHERE phone_number ~ '[^0-9]' OR phone_number ~ '^07[0-9]{8}$';
        """)

        # جدول الطلبات
        cur.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                request_type VARCHAR(50) NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                requested_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                responded_at TIMESTAMP WITH TIME ZONE,
                notes TEXT
            );
        """)
        # Add decision audit fields to existing installations without dropping data.
        cur.execute("""
            ALTER TABLE requests
                ADD COLUMN IF NOT EXISTS decided_by BIGINT,
                ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;
        """)
        
        # جدول السجائر اليومية
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_cigarettes (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                count INTEGER DEFAULT 0,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, date)
            );
        """)
        
        # جدول المديرين
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                added_by BIGINT,
                added_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                is_super_admin BOOLEAN DEFAULT FALSE
            );
        """)
        cur.execute("""
            ALTER TABLE admins
                ADD COLUMN IF NOT EXISTS added_by BIGINT,
                ADD COLUMN IF NOT EXISTS added_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ADD COLUMN IF NOT EXISTS is_super_admin BOOLEAN NOT NULL DEFAULT FALSE;
        """)
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_admins_telegram_id ON admins(telegram_id)"
        )
        
        # جدول استراحات الغداء
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lunch_breaks (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                taken BOOLEAN DEFAULT FALSE,
                taken_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, date)
            );
        """)
        
        # جدول أوقات السجائر (لحساب الفجوة)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cigarette_times (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                taken_at TIMESTAMP WITH TIME ZONE NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # جدول الغيابات (للمغادرات والعطل)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS absences (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                absence_type VARCHAR(50) NOT NULL,
                reason TEXT,
                excuse TEXT,
                is_excused BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, date)
            );
        """)
        
        # Durable timer state: countdowns survive process restarts and deploys.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS active_timers (
                user_id BIGINT PRIMARY KEY,
                message_id BIGINT NOT NULL,
                timer_type VARCHAR(20) NOT NULL,
                start_at TIMESTAMPTZ NOT NULL,
                end_at TIMESTAMPTZ NOT NULL,
                completed BOOLEAN NOT NULL DEFAULT FALSE,
                completed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS branch_settings (
                branch_id BIGINT PRIMARY KEY,
                smoke_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                smoke_daily_limit INTEGER NOT NULL DEFAULT 5 CHECK (smoke_daily_limit BETWEEN 0 AND 30),
                smoke_duration_minutes INTEGER NOT NULL DEFAULT 6 CHECK (smoke_duration_minutes BETWEEN 1 AND 120),
                smoke_start_time TIME NOT NULL DEFAULT '10:00',
                smoke_end_time TIME,
                smoke_gap_minutes INTEGER NOT NULL DEFAULT 90 CHECK (smoke_gap_minutes BETWEEN 0 AND 1440),
                lunch_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                lunch_duration_minutes INTEGER NOT NULL DEFAULT 30 CHECK (lunch_duration_minutes BETWEEN 1 AND 240),
                lunch_start_time TIME,
                lunch_end_time TIME,
                updated_by BIGINT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS branch_settings_audit (
                id BIGSERIAL PRIMARY KEY, branch_id BIGINT NOT NULL,
                setting_key VARCHAR(60) NOT NULL, old_value TEXT, new_value TEXT,
                changed_by BIGINT NOT NULL, changed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        if SUPER_ADMIN_TELEGRAM_ID:
            cur.execute("""
                INSERT INTO admins (telegram_id, added_by, is_super_admin)
                VALUES (%s, NULL, TRUE)
                ON CONFLICT (telegram_id)
                DO UPDATE SET is_super_admin = TRUE
            """, (SUPER_ADMIN_TELEGRAM_ID,))

        conn.commit()
        cur.close()
        conn.close()
        logger.info("Database tables initialized successfully")
        if not SUPER_ADMIN_TELEGRAM_ID:
            logger.warning("SUPER_ADMIN_TELEGRAM_ID is not set; no administrator has been bootstrapped")
        return True
    except Exception:
        if conn:
            conn.rollback()
            conn.close()
        logger.exception("Error initializing database tables")
        return False

# --- دوال الموظفين وقاعدة البيانات (لم يتم تغييرها) ---
# ... (دوال save_employee, get_employee_by_telegram_id, get_employee_by_phone, get_all_employees, delete_employee_by_phone)
def save_employee_in_branch(phone_number, full_name, job_title, branch_id, actor_id):
    """Create an unlinked employee only when the actor can administer the target branch."""
    conn = None
    try:
        normalized_phone = normalize_phone(phone_number)
        if not normalized_phone or not full_name.strip() or not job_title.strip() or not branch_id:
            return None
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Check and lock the actor's identity/branch grant in this same transaction.
            cur.execute(
                "SELECT is_super_admin FROM admins WHERE telegram_id=%s FOR SHARE",
                (actor_id,),
            )
            actor = cur.fetchone()
            if not actor:
                conn.rollback()
                return None
            if not actor[0]:
                cur.execute("""
                    SELECT 1 FROM admin_branch_roles
                    WHERE telegram_id=%s AND branch_id=%s
                      AND role IN ('branch_manager', 'supervisor')
                    FOR SHARE
                """, (actor_id, branch_id))
                if not cur.fetchone():
                    conn.rollback()
                    logger.warning("Denied employee creation in branch %s by %s", branch_id, actor_id)
                    return None
            cur.execute("SELECT 1 FROM branches WHERE id=%s AND is_active=TRUE FOR SHARE", (branch_id,))
            if not cur.fetchone():
                conn.rollback()
                return None
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (normalized_phone,))
            cur.execute(
                "SELECT id FROM employees WHERE phone_number=%s AND is_active=TRUE",
                (normalized_phone,),
            )
            if cur.fetchone():
                conn.rollback()
                return None
            cur.execute("""
                INSERT INTO employees
                    (phone_number, full_name, job_title, branch_id, last_active, is_active)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, TRUE)
                RETURNING id
            """, (normalized_phone, full_name.strip(), job_title.strip(), branch_id))
            employee_id = cur.fetchone()[0]
        conn.commit()
        return employee_id
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Could not create employee in branch %s", branch_id)
        return None
    finally:
        if conn:
            conn.close()


def save_employee(telegram_id, phone_number, full_name):
    """Link a Telegram account only to an existing active pre-registered employee."""
    conn = None
    try:
        normalized_phone = normalize_phone(phone_number)
        if not normalized_phone or not telegram_id:
            return None
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Serialize attempts to claim the same registered phone.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (normalized_phone,))
            cur.execute(
                """SELECT id, telegram_id
                   FROM employees
                   WHERE phone_number=%s AND is_active=TRUE
                   ORDER BY id DESC LIMIT 1
                   FOR UPDATE""",
                (normalized_phone,),
            )
            employee = cur.fetchone()
            if not employee:
                conn.rollback()
                return None
            employee_id, linked_telegram_id = employee
            if linked_telegram_id is not None and int(linked_telegram_id) != int(telegram_id):
                conn.rollback()
                return None
            # An account already linked to another employee must not be moved.
            cur.execute(
                """SELECT id FROM employees
                   WHERE telegram_id=%s AND is_active=TRUE AND id<>%s
                   FOR UPDATE""",
                (telegram_id, employee_id),
            )
            if cur.fetchone():
                conn.rollback()
                return None
            cur.execute(
                """UPDATE employees
                   SET telegram_id=%s, last_active=CURRENT_TIMESTAMP
                   WHERE id=%s AND is_active=TRUE
                     AND (telegram_id IS NULL OR telegram_id=%s)
                   RETURNING id""",
                (telegram_id, employee_id, telegram_id),
            )
            linked = cur.fetchone()
            if not linked:
                conn.rollback()
                return None
        conn.commit()
        return linked[0]
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Could not securely link Telegram employee account")
        return None
    finally:
        if conn:
            conn.close()

def get_employee_by_telegram_id(telegram_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM employees WHERE telegram_id=%s AND is_active=TRUE",
                (telegram_id,),
            )
            employee = cur.fetchone()
        return dict(employee) if employee else None
    except Exception:
        logger.exception("Error getting employee by Telegram ID")
        return None
    finally:
        if conn:
            conn.close()
def get_employee_by_phone(phone_number):
    conn = None
    try:
        normalized = normalize_phone(phone_number)
        if not normalized:
            return None
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT * FROM employees
                   WHERE phone_number=%s AND is_active=TRUE
                   ORDER BY id DESC LIMIT 1""",
                (normalized,),
            )
            employee = cur.fetchone()
        return dict(employee) if employee else None
    except Exception:
        logger.exception("Error getting employee by phone")
        return None
    finally:
        if conn:
            conn.close()
def get_all_employees():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM employees WHERE is_active=TRUE ORDER BY full_name"
            )
            employees = cur.fetchall()
        return [dict(employee) for employee in employees]
    except Exception:
        logger.exception("Error getting all employees")
        return []
    finally:
        if conn:
            conn.close()
def delete_employee_by_phone(phone_number):
    conn = None
    try:
        normalized = normalize_phone(phone_number)
        if not normalized:
            return False
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE employees
                SET is_active=FALSE, last_active=CURRENT_TIMESTAMP
                WHERE phone_number=%s AND is_active=TRUE
                RETURNING id
            """, (normalized,))
            deleted = cur.fetchone()
        conn.commit()
        return bool(deleted)
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Error deactivating employee")
        return False
    finally:
        if conn:
            conn.close()
def create_request(employee_id, request_type, notes=None):
    """Persist every employee request before notifying administrators."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO requests (employee_id, request_type, status, notes)
            VALUES (%s, %s, 'pending', %s)
            RETURNING id
        """, (employee_id, request_type, notes))
        request_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return request_id
    except Exception as e:
        logger.error("Error creating request: %s", e)
        if conn:
            conn.rollback()
            conn.close()
        return None


def decide_request(request_id, employee_id, request_type, decision, admin_id):
    """Atomically allow one administrator to decide a pending request once."""
    if decision not in {"approved", "rejected"}:
        return False
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Lock the actor's admin identity and branch grant so a concurrent
        # revocation cannot race an approval already in progress.
        cur.execute(
            "SELECT is_super_admin FROM admins WHERE telegram_id=%s FOR SHARE",
            (admin_id,),
        )
        actor = cur.fetchone()
        if not actor:
            conn.rollback()
            return False
        cur.execute(
            "SELECT branch_id, telegram_id FROM employees WHERE id=%s AND is_active=TRUE FOR UPDATE",
            (employee_id,),
        )
        employee_branch = cur.fetchone()
        if not employee_branch:
            conn.rollback()
            return False
        if not actor[0]:
            cur.execute("""
                SELECT 1 FROM admin_branch_roles
                WHERE telegram_id=%s AND branch_id=%s
                  AND role IN ('branch_manager', 'supervisor')
                FOR SHARE
            """, (admin_id, employee_branch[0]))
            if not cur.fetchone():
                conn.rollback()
                return False
        cur.execute(
            "SELECT 1 FROM branches WHERE id=%s AND is_active=TRUE FOR SHARE",
            (employee_branch[0],),
        )
        if not cur.fetchone():
            conn.rollback()
            return False
        if decision == "approved" and (
            request_type in {"smoke", "break"} or request_type.startswith("custom-")
        ):
            if not employee_branch[1]:
                conn.rollback()
                return False
            cur.execute(
                """SELECT 1 FROM active_timers
                   WHERE user_id=%s AND returned_at IS NULL FOR UPDATE""",
                (employee_branch[1],),
            )
            if cur.fetchone():
                conn.rollback()
                return False
            cur.execute(
                """SELECT 1 FROM timer_start_outbox
                   WHERE employee_telegram_id=%s FOR UPDATE""",
                (employee_branch[1],),
            )
            if cur.fetchone():
                conn.rollback()
                return False

        cur.execute("""
            UPDATE requests
            SET status = %s,
                responded_at = CURRENT_TIMESTAMP,
                decided_by = %s,
                decided_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND employee_id = %s
              AND request_type = %s AND status = 'pending'
              AND EXISTS (
                  SELECT 1
                  FROM employees e
                  WHERE e.id = requests.employee_id
                    AND (
                        EXISTS (
                            SELECT 1 FROM admins a
                            WHERE a.telegram_id = %s AND a.is_super_admin = TRUE
                        )
                        OR EXISTS (
                            SELECT 1 FROM admin_branch_roles r
                            WHERE r.telegram_id = %s
                              AND r.branch_id = e.branch_id
                              AND r.role IN ('branch_manager', 'supervisor')
                        )
                    )
              )
            RETURNING id
        """, (decision, admin_id, request_id, employee_id, request_type, admin_id, admin_id))
        changed = cur.fetchone()
        if changed and decision == "approved" and request_type in {"smoke", "break"}:
            branch_id = employee_branch[0]
            cur.execute("""
                SELECT smoke_enabled, smoke_daily_limit, smoke_duration_minutes,
                       smoke_start_time, smoke_end_time, smoke_gap_minutes,
                       lunch_enabled, lunch_duration_minutes, lunch_start_time,
                       lunch_end_time
                FROM branch_settings WHERE branch_id=%s FOR UPDATE
            """, (branch_id,))
            settings_row = cur.fetchone()
            if not settings_row:
                conn.rollback()
                return False
            (smoke_enabled, smoke_limit, smoke_duration, smoke_start, smoke_end, smoke_gap,
             lunch_enabled, lunch_duration, lunch_start, lunch_end) = settings_row
            now = get_jordan_time()
            today = now.date()
            current_time = now.time().replace(tzinfo=None)
            if request_type == "smoke":
                if not smoke_enabled or not is_within_time_window(current_time, smoke_start, smoke_end):
                    conn.rollback()
                    return False
                cur.execute("""
                    SELECT taken_at FROM cigarette_times
                    WHERE employee_id=%s ORDER BY taken_at DESC LIMIT 1
                """, (employee_id,))
                last_smoke = cur.fetchone()
                if last_smoke and (now - last_smoke[0]).total_seconds() < int(smoke_gap) * 60:
                    conn.rollback()
                    return False
                cur.execute("""
                    INSERT INTO daily_cigarettes (employee_id, date, count, updated_at)
                    SELECT %s, %s, 1, CURRENT_TIMESTAMP
                    WHERE %s > 0
                    ON CONFLICT (employee_id, date)
                    DO UPDATE SET count = daily_cigarettes.count + 1,
                                  updated_at = CURRENT_TIMESTAMP
                    WHERE daily_cigarettes.count < %s
                    RETURNING count
                """, (employee_id, today, smoke_limit, smoke_limit))
                if not cur.fetchone():
                    conn.rollback()
                    return False
                cur.execute(
                    "INSERT INTO cigarette_times(employee_id, taken_at) VALUES(%s, %s)",
                    (employee_id, now),
                )
            else:
                if not lunch_enabled or not is_within_time_window(current_time, lunch_start, lunch_end):
                    conn.rollback()
                    return False
                cur.execute("""
                    INSERT INTO lunch_breaks(employee_id, date, taken, taken_at)
                    VALUES(%s, %s, TRUE, %s)
                    ON CONFLICT(employee_id, date) DO NOTHING
                    RETURNING id
                """, (employee_id, today, now))
                if not cur.fetchone():
                    conn.rollback()
                    return False
            timer_duration = smoke_duration if request_type == "smoke" else lunch_duration
            if not employee_branch[1]:
                conn.rollback()
                return False
            cur.execute(
                """INSERT INTO timer_start_outbox
                   (request_id, employee_telegram_id, timer_type, duration_minutes)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (request_id) DO NOTHING""",
                (request_id, employee_branch[1], request_type, timer_duration),
            )
        elif changed and decision == "approved" and request_type.startswith("custom-"):
            try:
                custom_type_id = int(request_type.split("-", 1)[1])
            except ValueError:
                conn.rollback()
                return False
            cur.execute(
                """SELECT duration_minutes FROM branch_request_types
                   WHERE id=%s AND branch_id=%s AND code=%s AND is_active=TRUE
                   FOR UPDATE""",
                (custom_type_id, employee_branch[0], request_type),
            )
            custom_duration = cur.fetchone()
            if not custom_duration or not employee_branch[1]:
                conn.rollback()
                return False
            cur.execute(
                """INSERT INTO timer_start_outbox
                   (request_id, employee_telegram_id, timer_type, duration_minutes)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (request_id) DO NOTHING""",
                (request_id, employee_branch[1], request_type, custom_duration[0]),
            )
        conn.commit()
        cur.close()
        conn.close()
        return bool(changed)
    except Exception as e:
        logger.error("Error deciding request %s: %s", request_id, e)
        if conn:
            conn.rollback()
            conn.close()
        return False
# --- دوال السجائر والاستراحات (لم يتم تغييرها) ---
# ... (جميع دوال السجائر والاستراحات)
def get_smoke_count_db(employee_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count FROM daily_cigarettes WHERE employee_id=%s AND date=%s",
                (employee_id, get_jordan_time().date()),
            )
            result = cur.fetchone()
        return result[0] if result else 0
    except Exception:
        logger.exception("Error getting smoke count")
        return 0
    finally:
        if conn:
            conn.close()
def increment_smoke_count_db(employee_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO daily_cigarettes (employee_id, date, count, updated_at)
                VALUES (%s, %s, 1, CURRENT_TIMESTAMP)
                ON CONFLICT (employee_id, date)
                DO UPDATE SET count=daily_cigarettes.count+1,
                              updated_at=CURRENT_TIMESTAMP
                RETURNING count
            """, (employee_id, get_jordan_time().date()))
            new_count = cur.fetchone()[0]
        conn.commit()
        return new_count
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Error incrementing smoke count")
        return 0
    finally:
        if conn:
            conn.close()
def get_last_cigarette_time(employee_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT taken_at FROM cigarette_times
                WHERE employee_id=%s ORDER BY taken_at DESC LIMIT 1
            """, (employee_id,))
            result = cur.fetchone()
        if not result:
            return None
        last_time = result[0]
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        return last_time.astimezone(JORDAN_TZ)
    except Exception:
        logger.exception("Error getting last cigarette time")
        return None
    finally:
        if conn:
            conn.close()
def record_cigarette_time(employee_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO cigarette_times(employee_id, taken_at) VALUES(%s, %s)",
                (employee_id, get_jordan_time()),
            )
        conn.commit()
        return True
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Error recording cigarette time")
        return False
    finally:
        if conn:
            conn.close()
def has_taken_lunch_break_today(employee_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM lunch_breaks
                WHERE employee_id=%s AND date=%s AND taken=TRUE
            """, (employee_id, get_jordan_time().date()))
            result = cur.fetchone()
        return bool(result)
    except Exception:
        logger.exception("Error checking lunch break")
        return False
    finally:
        if conn:
            conn.close()
def mark_lunch_break_taken(employee_id):
    conn = None
    try:
        now = get_jordan_time()
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO lunch_breaks (employee_id, date, taken, taken_at)
                VALUES (%s, %s, TRUE, %s)
                ON CONFLICT (employee_id, date)
                DO UPDATE SET taken=TRUE, taken_at=EXCLUDED.taken_at
            """, (employee_id, now.date(), now))
        conn.commit()
        return True
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Error marking lunch break")
        return False
    finally:
        if conn:
            conn.close()
def get_all_admins():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT telegram_id FROM admins ORDER BY added_at")
        admins = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        return admins
    except Exception as e:
        logger.error("Error getting admins: %s", e)
        if conn:
            conn.close()
        return []  # Fail closed if PostgreSQL is unavailable.

def is_admin(user_id):
    """Only super-admins or explicitly branch-assigned admins have access."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 1 FROM admins a
            WHERE a.telegram_id = %s AND (
                a.is_super_admin = TRUE OR EXISTS (
                    SELECT 1 FROM admin_branch_roles r
                    WHERE r.telegram_id = a.telegram_id
                )
            )
        """, (user_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return bool(result)
    except Exception as e:
        logger.error("Error checking admin access: %s", e)
        if conn:
            conn.close()
        return False

def admin_can_access_branch(user_id, branch_id):
    """Fail-closed authorization for every branch-owned operation."""
    if not branch_id:
        return False
    if is_super_admin(user_id):
        return True
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 1 FROM admin_branch_roles
            WHERE telegram_id=%s AND branch_id=%s
              AND role IN ('branch_manager', 'supervisor')
        """, (user_id, branch_id))
        allowed = bool(cur.fetchone())
        cur.close()
        conn.close()
        return allowed
    except Exception as e:
        logger.error("Error checking branch access: %s", e)
        if conn:
            conn.close()
        return False

def get_branch_admin_ids(branch_id):
    """Return only branch administrators plus explicitly global super-admins."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT a.telegram_id
            FROM admins a
            LEFT JOIN admin_branch_roles r ON r.telegram_id = a.telegram_id
            WHERE a.is_super_admin = TRUE
               OR (r.branch_id = %s AND r.role IN ('branch_manager', 'supervisor'))
            ORDER BY a.telegram_id
        """, (branch_id,))
        ids = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        return ids
    except Exception as e:
        logger.error("Error getting branch administrators: %s", e)
        if conn:
            conn.close()
        return []

async def send_to_branch_admins(context, branch_id, text, reply_markup=None):
    for admin_id in get_branch_admin_ids(branch_id):
        try:
            await context.bot.send_message(
                chat_id=admin_id, text=text, reply_markup=reply_markup
            )
        except Exception as e:
            logger.error("Failed to notify branch administrator %s: %s", admin_id, e)

def get_employees_for_admin(user_id):
    """List only employees visible to this administrator."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if is_super_admin(user_id):
            cur.execute("""
                SELECT e.*, b.name AS branch_name
                FROM employees e
                LEFT JOIN branches b ON b.id=e.branch_id
                WHERE e.is_active=TRUE ORDER BY e.full_name
            """)
        else:
            cur.execute("""
                SELECT e.*, b.name AS branch_name
                FROM employees e
                JOIN branches b ON b.id=e.branch_id
                JOIN admin_branch_roles r ON r.branch_id=e.branch_id
                WHERE r.telegram_id=%s
                  AND r.role IN ('branch_manager','supervisor')
                  AND e.is_active=TRUE
                ORDER BY e.full_name
            """, (user_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error("Error getting scoped employee list: %s", e)
        if conn:
            conn.close()
        return []

def is_super_admin(user_id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT is_super_admin FROM admins WHERE telegram_id = %s", (user_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return bool(result and result[0])
    except Exception as e:
        logger.error("Error checking super-admin access: %s", e)
        if conn:
            conn.close()
        return False

def add_admin_to_db(telegram_id, added_by=None, is_super=False):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO admins (telegram_id, added_by, is_super_admin)
            VALUES (%s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE
            SET is_super_admin = admins.is_super_admin OR EXCLUDED.is_super_admin
        """, (telegram_id, added_by, is_super))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error adding admin: {e}")
        return False

def remove_admin_from_db(telegram_id):
    try:
        if telegram_id == SUPER_ADMIN_TELEGRAM_ID: return False
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM admins WHERE telegram_id = %s AND is_super_admin = FALSE", (telegram_id,))
        rows = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        return rows > 0
    except Exception as e:
        logger.error(f"Error removing admin: {e}")
        return False

async def send_to_all_admins(context, text, reply_markup=None):
    """Global administrative notices are restricted to super-admins."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT telegram_id FROM admins WHERE is_super_admin=TRUE ORDER BY telegram_id")
        admin_ids = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
    except Exception as e:
        logger.error("Error getting super administrators: %s", e)
        if conn:
            conn.close()
        return
    for admin_id in admin_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=reply_markup)
        except Exception as e:
            logger.error("Failed to send to super-admin %s: %s", admin_id, e)

# --- أدوات مساعدة (لم يتم تغييرها) ---
# ... (جميع الأدوات المساعدة)
def get_jordan_time():
    return datetime.now(JORDAN_TZ)


def is_within_time_window(current_time, start_time=None, end_time=None):
    """Support open-ended, daytime, and overnight operating windows."""
    if start_time is None and end_time is None:
        return True
    if start_time is None:
        return current_time < end_time
    if end_time is None:
        return current_time >= start_time
    if start_time <= end_time:
        return start_time <= current_time < end_time
    return current_time >= start_time or current_time < end_time

def normalize_phone(phone_number):
    if not phone_number:
        return ""
    digits_only = ''.join(filter(str.isdigit, str(phone_number)))
    while digits_only.startswith("00"):        digits_only = digits_only[2:]
    # Normalize common Jordanian local mobile format (07XXXXXXXX) to 9627XXXXXXXX.
    if len(digits_only) == 10 and digits_only.startswith("07"):
        digits_only = "962" + digits_only[1:]
    return digits_only

def verify_employee(phone_number):
    normalized_input = normalize_phone(phone_number)
    if not normalized_input:
        return False
    employee = get_employee_by_phone(normalized_input)
    return bool(employee and employee.get("is_active", True))

def get_user_phone(user_id):
    employee = get_employee_by_telegram_id(user_id)
    return employee.get("phone_number") if employee else None

def get_employee_name(user_id, default="المستخدم"):
    employee = get_employee_by_telegram_id(user_id)
    if employee and employee.get('full_name'): return employee.get('full_name')
    return default

# --- أوامر البوت (لم يتم تغييرها) ---
# ... (جميع أوامر البوت: start, help_command)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_interactive_drafts(context)
    user = update.message.from_user
    user_name = get_employee_name(user.id)
    user_phone = get_user_phone(user.id)
    
    if user_phone and verify_employee(user_phone):
        employee = get_employee_by_telegram_id(user.id)
        settings = get_branch_settings(employee.get('branch_id')) if employee and employee.get('branch_id') else None
        if not settings:
            await update.message.reply_text('⚠️ إعدادات الفرع غير جاهزة. أبلغ المدير بتطبيق migration 001.')
            return
        menu_keyboard = [
            [KeyboardButton("🚬 طلب استراحة التدخين"), KeyboardButton("☕ طلب استراحة الغداء")],
            [KeyboardButton("🚪 طلب مغادرة"), KeyboardButton("🌴 طلب إجازة")],
        ]
        for request_label in get_employee_buttons(employee["branch_id"]):
            menu_keyboard.append([KeyboardButton(request_label)])
        menu_keyboard.append([KeyboardButton("ℹ️ المساعدة")])
        menu_markup = ReplyKeyboardMarkup(
            menu_keyboard,
            resize_keyboard=True,
            input_field_placeholder="اختر الخدمة المطلوبة…",
        )
        msg = (
            f"مرحبًا {user_name}! 👋\n\n"
            "✅ هويتك مفعلة.\n\n"
            "🚬 **قوانين التدخين الجديدة:**\n"
            f"- العدد المسموح: {settings['smoke_daily_limit']} سجائر.\n"
            f"- مدة السيجارة: {settings['smoke_duration_minutes']} دقائق.\n"
            f"- وقت البدء: من {settings['smoke_start_time'].strftime('%H:%M')}.\n"
            f"- الفاصل الزمني: {settings['smoke_gap_minutes']} دقيقة.\n\n"
            "📝 **الأوامر المتاحة:**\n"
            "/smoke - طلب استراحة تدخين 🚬\n"
            "/break - طلب استراحة غداء ☕\n"
            "/leave - طلب مغادرة 🚪\n"
            "/vacation - طلب عطلة 🌴\n"
        )
        if is_admin(user.id):
            msg += (
                "\n👔 **أوامر مدير الفرع:**\n"
                "/settings أو /settings رقم_الفرع - إعدادات الفرع\n"
                "/request_types - إدارة أزرار الاستئذان في فرعك\n"
                "/list_employees - عرض موظفي الفروع المصرح بها\n"
                "/broadcast - إرسال إعلان لموظفي فرعك\n"
            )
        if is_super_admin(user.id):
            msg += (
                "\n🛡️ **أوامر المدير العام:**\n"
                "/add_employee رقم_الفرع رقم_الهاتف الاسم | المسمى - إضافة موظف\n"
                "/remove_employee - إيقاف موظف\n"
                "/list_admins - عرض المديرين\n"
                "/add_admin TELEGRAM_ID - إضافة مدير\n"
                "/remove_admin TELEGRAM_ID - إزالة مدير\n"
                "/assign_branch_admin TELEGRAM_ID BRANCH_ID [branch_manager|supervisor] - تعيين صلاحية فرع\n"
                "/remove_branch_admin TELEGRAM_ID BRANCH_ID - إلغاء صلاحية فرع\n"
                "/add_branch CODE | NAME - إنشاء فرع جديد\n"
                "/list_branches - عرض الفروع\n"
            )
        await update.message.reply_text(msg, reply_markup=menu_markup)
    else:
        keyboard = [[KeyboardButton("مشاركة رقم الهاتف 📱", request_contact=True)]]
        markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text(
            f"مرحبًا {user_name}!\nالرجاء مشاركة رقم هاتفك للتحقق من هويتك.",
            reply_markup=markup
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

# --- منطق التدخين الجديد (لم يتم تغييرها) ---
# ... (smoke_request, break_request)
async def smoke_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_broadcast", None)
    user = update.message.from_user
    phone = get_user_phone(user.id)
    
    if not phone or not verify_employee(phone):
        await update.message.reply_text("❌ غير مصرح لك. شارك رقم هاتفك أولاً.")
        return

    now = get_jordan_time()
    employee = get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text("❌ خطأ في البيانات.")
        return
    settings = get_branch_settings(employee.get('branch_id')) if employee.get('branch_id') else None
    if not settings:
        await update.message.reply_text("⚠️ إعدادات الفرع غير جاهزة. أبلغ المدير بتطبيق migration 001.")
        return
    if not settings['smoke_enabled']:
        await update.message.reply_text("🚫 استراحات التدخين متوقفة حالياً بقرار الإدارة.")
        return
    current_time = now.time().replace(tzinfo=None)
    start_time, end_time = settings['smoke_start_time'], settings['smoke_end_time']
    if not is_within_time_window(current_time, start_time, end_time):
        end_text = f" وحتى {end_time.strftime('%H:%M')}" if end_time else ""
        await update.message.reply_text(f"⛔️ وقت السماح بالتدخين من {start_time.strftime('%H:%M')}{end_text}.\nالساعة الآن: {now.strftime('%H:%M')}")
        return
    # التحقق من الفجوة الزمنية
    last_cig = get_last_cigarette_time(employee['id'])
    if last_cig:
        diff = now - last_cig
        elapsed_minutes = diff.total_seconds() / 60
        required_gap = settings['smoke_gap_minutes']
        if elapsed_minutes < required_gap:
            remaining_mins = int(required_gap - elapsed_minutes + 0.999)
            await update.message.reply_text(f"⏳ يرجى الانتظار! يجب مرور {required_gap} دقيقة بين الاستراحات.\nالمتبقي: {remaining_mins} دقيقة.")
            return

    # التحقق من العدد
    count = get_smoke_count_db(employee['id'])
    if count >= settings['smoke_daily_limit']:
        await update.message.reply_text(f"❌ انتهى رصيد السجائر لهذا اليوم ({settings['smoke_daily_limit']}).")
        return

    # Persist request before sending approval buttons.
    name = employee['full_name']
    request_id = create_request(employee['id'], 'smoke')
    if not request_id:
        await update.message.reply_text("❌ تعذر حفظ الطلب. حاول مرة أخرى.")
        return

    await update.message.reply_text("⏳ تم حفظ الطلب وإرساله للمدير...")
    
    keyboard = [[
        InlineKeyboardButton("✅ قبول", callback_data=f"approve_smoke_{request_id}_{user.id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_smoke_{request_id}_{user.id}")
    ]]
    markup = InlineKeyboardMarkup(keyboard)
    
    msg = (
        f"🚬 **طلب تدخين جديد**\n"
        f"👤 الموظف: {name}\n"
        f"🔢 المستهلك: {count}/{settings['smoke_daily_limit']}\n"
        f"⏱ المدة المطلوبة: {settings['smoke_duration_minutes']} دقائق"
    )
    await send_to_branch_admins(context, employee["branch_id"], msg, markup)

# --- منطق الاستراحة ---
async def break_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_broadcast", None)
    user = update.message.from_user
    phone = get_user_phone(user.id)
    if not phone or not verify_employee(phone): return
    
    employee = get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text("❌ تعذر العثور على بيانات الموظف.")
        return
    settings = get_branch_settings(employee.get('branch_id')) if employee.get('branch_id') else None
    if not settings:
        await update.message.reply_text("⚠️ إعدادات الفرع غير جاهزة. أبلغ المدير.")
        return
    if not settings['lunch_enabled']:
        await update.message.reply_text("🚫 استراحة الغداء متوقفة حالياً بقرار الإدارة.")
        return
    now_time = get_jordan_time().time().replace(tzinfo=None)
    lunch_start, lunch_end = settings['lunch_start_time'], settings['lunch_end_time']
    if not is_within_time_window(now_time, lunch_start, lunch_end):
        await update.message.reply_text("⛔️ طلب الغداء خارج الوقت المحدد من الإدارة.")
        return
    if has_taken_lunch_break_today(employee['id']):
        await update.message.reply_text("❌ لقد أخذت استراحة الغداء بالفعل اليوم.")
        return

    request_id = create_request(employee['id'], 'break')
    if not request_id:
        await update.message.reply_text("❌ تعذر حفظ الطلب. حاول مرة أخرى.")
        return

    await update.message.reply_text("⏳ تم حفظ طلب الاستراحة وإرساله للمدير...")
    keyboard = [[
        InlineKeyboardButton("✅ قبول", callback_data=f"approve_break_{request_id}_{user.id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_break_{request_id}_{user.id}")
    ]]
    msg = f"☕ **طلب استراحة غداء**\n👤 الموظف: {employee['full_name']}"
    await send_to_branch_admins(context, employee["branch_id"], msg, InlineKeyboardMarkup(keyboard))
    
# --- المغادرات والإجازات (لم يتم تغييرها) ---
# ... (جميع دوال المغادرات والإجازات)
async def leave_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_broadcast", None)
    user = update.message.from_user
    if not verify_employee(get_user_phone(user.id)): return ConversationHandler.END
    await update.message.reply_text("📝 اكتب سبب المغادرة:")
    return LEAVE_REASON

async def receive_leave_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    reason = (update.message.text or "").strip()
    employee = get_employee_by_telegram_id(user.id)
    if not employee or not reason:
        await update.message.reply_text("❌ تعذر تحديد الموظف أو أن سبب المغادرة فارغ.")
        return ConversationHandler.END
    request_id = create_request(employee['id'], 'leave', reason)
    if not request_id:
        await update.message.reply_text("❌ تعذر حفظ الطلب. حاول مرة أخرى.")
        return ConversationHandler.END
    name = employee['full_name']
    
    keyboard = [[
        InlineKeyboardButton("✅ قبول", callback_data=f"approve_leave_{request_id}_{user.id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_leave_{request_id}_{user.id}")
    ]]
    msg = f"🚪 **طلب مغادرة**\n👤 الموظف: {name}\n📝 السبب: {reason}"
    await send_to_branch_admins(context, employee["branch_id"], msg, InlineKeyboardMarkup(keyboard))
    await update.message.reply_text("تم إرسال الطلب.")
    return ConversationHandler.END

async def vacation_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_broadcast", None)
    user = update.message.from_user
    if not verify_employee(get_user_phone(user.id)): return ConversationHandler.END
    await update.message.reply_text("🌴 اكتب سبب العطلة وتاريخها:")
    return VACATION_REASON

async def receive_vacation_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    reason = (update.message.text or "").strip()
    employee = get_employee_by_telegram_id(user.id)
    if not employee or not reason:
        await update.message.reply_text("❌ تعذر تحديد الموظف أو أن تفاصيل العطلة فارغة.")
        return ConversationHandler.END
    request_id = create_request(employee['id'], 'vacation', reason)
    if not request_id:
        await update.message.reply_text("❌ تعذر حفظ الطلب. حاول مرة أخرى.")
        return ConversationHandler.END
    name = employee['full_name']
    
    keyboard = [[
        InlineKeyboardButton("✅ قبول", callback_data=f"approve_vacation_{request_id}_{user.id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_vacation_{request_id}_{user.id}")
    ]]
    msg = f"🌴 **طلب عطلة**\n👤 الموظف: {name}\n📝 التفاصيل: {reason}"
    await send_to_branch_admins(context, employee["branch_id"], msg, InlineKeyboardMarkup(keyboard))
    await update.message.reply_text("تم إرسال الطلب.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_broadcast", None)
    await update.message.reply_text("❌ تم الإلغاء.")
    return ConversationHandler.END
    
# --- إدارة الموظفين والمديرين (لم يتم تغييرها) ---
# ... (جميع دوال الإدارة)
async def list_employees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.effective_message.reply_text("⛔ هذا الأمر للمديرين فقط.")
        return
    employees = get_employees_for_admin(user_id)
    if not employees:
        await update.message.reply_text("لا يوجد موظفين.")
        return
    msg = "👥 **قائمة الموظفين:**\n"
    for i, e in enumerate(employees, 1):
        title = e.get("job_title") or "المسمى غير محدد"
        branch = e.get("branch_name") or "الفرع غير محدد"
        msg += f"{i}. {e['full_name']} — {title} — {branch} ({e['phone_number']})\n"
    await update.message.reply_text(msg)

async def add_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) < 3 or "|" not in " ".join(context.args[2:]):
        await update.effective_message.reply_text(
            "الاستخدام: /add_employee رقم_الفرع رقم_الهاتف الاسم_الكامل | المسمى_الوظيفي\n"
            "مثال: /add_employee 1 0791234567 محمد أحمد | كاشير"
        )
        return
    try:
        branch_id = int(context.args[0])
        if branch_id <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("رقم الفرع يجب أن يكون رقماً موجباً.")
        return
    if not (is_super_admin(user_id) or admin_can_access_branch(user_id, branch_id)):
        await update.effective_message.reply_text("⛔ لا تملك صلاحية إضافة موظفين إلى هذا الفرع.")
        return
    phone = context.args[1]
    profile = " ".join(context.args[2:]).split("|", 1)
    name = profile[0].strip()
    job_title = profile[1].strip() if len(profile) == 2 else ""
    if len(name) < 3 or not job_title:
        await update.effective_message.reply_text("أدخل الاسم الكامل والمسمى الوظيفي بعد علامة |.")
        return
    employee_id = save_employee_in_branch(phone, name, job_title, branch_id, user_id)
    if employee_id:
        await update.effective_message.reply_text(
            f"✅ تمت إضافة الموظف {name} — {job_title} إلى الفرع رقم {branch_id}.\n"
            "يمكنه الآن بدء البوت ومشاركة رقم هاتفه لربط حسابه."
        )
    else:
        await update.effective_message.reply_text(
            "❌ لم تتم الإضافة. تحقق من رقم الفرع، وأنه نشط، وأن رقم الهاتف غير مسجل مسبقاً."
        )

async def remove_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ إيقاف الموظفين متاح حالياً للمدير العام فقط.")
        return
    if not context.args:
        await update.message.reply_text("الاستخدام: /remove_employee رقم_الهاتف")
        return
    phone = context.args[0]
    if delete_employee_by_phone(phone):
        await update.message.reply_text("✅ تم إيقاف الموظف مع الاحتفاظ بسجلاته.")
    else:
        await update.message.reply_text("❌ لم يتم العثور على الموظف.")

async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ عرض قائمة المديرين متاح للمدير العام فقط.")
        return
    admins = get_all_admins()
    await update.message.reply_text(f"عدد المديرين: {len(admins)}\nالمعرفات: {admins}")

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.message.from_user.id):
        await update.message.reply_text("⛔ هذا الأمر مخصص للمدير العام.")
        return
    if not context.args:
        await update.message.reply_text("الاستخدام: /add_admin TELEGRAM_ID")
        return
    try:
        new_id = int(context.args[0])
        if new_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        await update.message.reply_text("❌ المعرف يجب أن يكون رقماً صحيحاً موجباً.")
        return

    if add_admin_to_db(new_id, update.message.from_user.id):
        await update.message.reply_text("✅ تمت إضافة المدير وحفظه في قاعدة البيانات.")
    else:
        await update.message.reply_text("❌ تعذرت إضافة المدير إلى قاعدة البيانات.")


async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.message.from_user.id):
        await update.message.reply_text("⛔ هذا الأمر مخصص للمدير العام.")
        return
    if not context.args:
        await update.message.reply_text("الاستخدام: /remove_admin TELEGRAM_ID")
        return
    try:
        target_id = int(context.args[0])
        if target_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        await update.message.reply_text("❌ المعرف يجب أن يكون رقماً صحيحاً موجباً.")
        return
    if remove_admin_from_db(target_id):
        await update.message.reply_text("✅ تم حذف صلاحية المدير.")
    else:
        await update.message.reply_text("لا يمكن حذف هذا المدير أو لم يتم العثور عليه.")

# --- المعالجة والأنيميشن ---

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if contact.user_id != update.message.from_user.id: return
    
    phone = contact.phone_number

    # Preserve the full name entered by management in PostgreSQL. Telegram's
    # contact first_name is not authoritative and must not overwrite it.
    registered_employee = get_employee_by_phone(phone)
    if not registered_employee or not registered_employee.get("is_active", True):
        await update.message.reply_text("⚠️ رقمك غير مسجل في النظام. تواصل مع المدير.")
        return
    linked_telegram_id = registered_employee.get("telegram_id")
    if linked_telegram_id and int(linked_telegram_id) != int(contact.user_id):
        await update.message.reply_text("⚠️ هذا الرقم مرتبط بحساب Telegram آخر. تواصل مع المدير لتصحيح الربط.")
        return
    registered_name = registered_employee.get("full_name") or contact.first_name or "موظف"

    if not save_employee(contact.user_id, phone, registered_name):
        await update.message.reply_text("❌ تعذر تفعيل الحساب حالياً. حاول لاحقاً أو تواصل مع المدير.")
        return

    await update.message.reply_text(f"✅ أهلاً {registered_name}، تم تفعيل حسابك بنجاح!")
    await start(update, context)

def create_progress_bar(remaining_seconds, total_seconds, length=12):
    """Render a compact, readable countdown bar for Telegram clients."""
    total = max(1, int(total_seconds))
    remaining = max(0, min(total, int(remaining_seconds)))
    filled = round((remaining / total) * length)
    return "🟩" * filled + "⬜" * (length - filled)


def _timer_db(operation, *values):
    """Small synchronous DB helper; every operation owns and closes its connection."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            if operation == "save":
                cur.execute("""
                    INSERT INTO active_timers
                        (user_id, message_id, timer_type, start_at, end_at, completed, completed_at, returned_at)
                    VALUES (%s, %s, %s, %s, %s, FALSE, NULL, NULL)
                    ON CONFLICT (user_id) DO UPDATE SET
                        message_id = EXCLUDED.message_id,
                        timer_type = EXCLUDED.timer_type,
                        start_at = EXCLUDED.start_at,
                        end_at = EXCLUDED.end_at,
                        completed = FALSE,
                        completed_at = NULL,
                        returned_at = NULL,
                        created_at = CURRENT_TIMESTAMP
                """, values)
            elif operation == "list":
                cur.execute("""
                    SELECT user_id, message_id, timer_type, start_at, end_at
                    FROM active_timers WHERE completed = FALSE
                    ORDER BY end_at
                """)
                rows = cur.fetchall()
                conn.commit()
                return rows
            elif operation == "outbox_for_request":
                cur.execute(
                    """SELECT employee_telegram_id, timer_type, duration_minutes
                       FROM timer_start_outbox WHERE request_id=%s""",
                    values,
                )
                row = cur.fetchone()
                conn.commit()
                return row
            elif operation == "outbox_list":
                cur.execute(
                    """SELECT request_id, employee_telegram_id, timer_type, duration_minutes
                       FROM timer_start_outbox ORDER BY created_at"""
                )
                rows = cur.fetchall()
                conn.commit()
                return rows
            elif operation == "save_outbox":
                user_id, message_id, timer_type, start_at, end_at, request_id = values
                cur.execute(
                    """INSERT INTO active_timers
                       (user_id, message_id, timer_type, start_at, end_at, completed, completed_at, returned_at)
                       VALUES (%s,%s,%s,%s,%s,FALSE,NULL,NULL)
                       ON CONFLICT (user_id) DO UPDATE SET
                           message_id=EXCLUDED.message_id,
                           timer_type=EXCLUDED.timer_type,
                           start_at=EXCLUDED.start_at,
                           end_at=EXCLUDED.end_at,
                           completed=FALSE,
                           completed_at=NULL,
                           returned_at=NULL,
                           created_at=CURRENT_TIMESTAMP""",
                    (user_id, message_id, timer_type, start_at, end_at),
                )
                cur.execute(
                    """DELETE FROM timer_start_outbox
                       WHERE request_id=%s AND employee_telegram_id=%s
                       RETURNING id""",
                    (request_id, user_id),
                )
                removed = cur.fetchone()
                if not removed:
                    conn.rollback()
                    return False
            elif operation == "outbox_error":
                cur.execute(
                    """UPDATE timer_start_outbox
                       SET attempts=attempts+1, last_error=%s,
                           updated_at=CURRENT_TIMESTAMP
                       WHERE request_id=%s""",
                    values,
                )
            elif operation == "complete":
                cur.execute("""
                    UPDATE active_timers
                    SET completed = TRUE, completed_at = CURRENT_TIMESTAMP
                    WHERE user_id = %s AND completed = FALSE
                """, values)
            elif operation == "is_active":
                cur.execute(
                    "SELECT 1 FROM active_timers WHERE user_id = %s AND completed = FALSE",
                    values,
                )
                result = cur.fetchone()
                conn.commit()
                return bool(result)
            elif operation == "clear":
                cur.execute("""
                    UPDATE active_timers
                    SET completed = TRUE, completed_at = CURRENT_TIMESTAMP,
                        returned_at = CURRENT_TIMESTAMP
                    WHERE user_id = %s
                """, values)
            elif operation == "has_unreturned":
                cur.execute(
                    "SELECT 1 FROM active_timers WHERE user_id=%s AND returned_at IS NULL",
                    values,
                )
                result = cur.fetchone()
                conn.commit()
                return bool(result)
            elif operation == "clear_matching":
                cur.execute("""
                    UPDATE active_timers
                    SET completed = TRUE, completed_at = CURRENT_TIMESTAMP,
                        returned_at = CURRENT_TIMESTAMP
                    WHERE user_id = %s AND timer_type = %s
                      AND message_id = %s
                    RETURNING user_id
                """, values)
                changed = cur.fetchone()
                conn.commit()
                return bool(changed)
            else:
                raise ValueError("Unknown timer database operation")
        conn.commit()
        return True
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Timer database operation failed: %s", operation)
        return False
    finally:
        if conn:
            conn.close()


def _timer_job_name(user_id):
    return f"employee-timer-{int(user_id)}"


def _remove_timer_job(context, user_id):
    if context.job_queue:
        for job in context.job_queue.get_jobs_by_name(_timer_job_name(user_id)):
            job.schedule_removal()
    active_timers.pop(int(user_id), None)


async def update_timer(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id, msg_id, start_time, end_time, type_ = job.data
    now = get_jordan_time()
    remaining = max(0, int((end_time - now).total_seconds()))
    total_seconds = max(1, int((end_time - start_time).total_seconds()))
    if type_ == "smoke":
        emoji, label = "🚬", "استراحة التدخين"
    elif type_ == "break":
        emoji, label = "☕", "استراحة الغداء"
    else:
        employee = get_employee_by_telegram_id(user_id)
        custom = get_request_type_by_code(employee.get("branch_id"), type_) if employee and employee.get("branch_id") else None
        label = custom[2] if custom else "الاستئذان"
        emoji = "🕌" if "الصلاة" in label else ("🚻" if "الحمام" in label else "⏳")

    if remaining <= 0:
        if not _timer_db("is_active", user_id):
            _remove_timer_job(context, user_id)
            return
        alert_text = (
            f"🔔 انتهت {label}!\n\n"
            "⏰ انتهى الوقت المحدد للاستراحة.\n"
            "يرجى العودة إلى العمل وتأكيد العودة بالزر أدناه."
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ عدت إلى العمل",
                callback_data=f"returned_{type_}_{user_id}_{msg_id}",
            )
        ]])
        try:
            await context.bot.edit_message_text(
                chat_id=user_id,
                message_id=msg_id,
                text=f"✅ انتهت {label}\n00:00\n\nبانتظار تأكيد عودتك.",
            )
        except Exception as exc:
            if "Message is not modified" not in str(exc):
                logger.warning("Could not finalize timer message for %s: %s", user_id, exc)
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=alert_text,
                reply_markup=keyboard,
            )
            _timer_db("complete", user_id)
            _remove_timer_job(context, user_id)
        except Exception:
            # Keep the timer active so the next tick retries a failed notification.
            logger.exception("Could not deliver timer completion alert to %s", user_id)
        return

    minutes, seconds = divmod(remaining, 60)
    bar = create_progress_bar(remaining, total_seconds)
    finish_at = end_time.astimezone(JORDAN_TZ).strftime("%H:%M")
    timer_text = (
        f"{emoji} {label}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"⏳ الوقت المتبقي\n"
        f"       {minutes:02d}:{seconds:02d}\n\n"
        f"{bar}\n"
        f"🕒 موعد الانتهاء: {finish_at}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "سيصلك تنبيه تلقائي عند انتهاء الوقت."
    )
    try:
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=msg_id,
            text=timer_text,
        )
    except Exception as exc:
        if "Message is not modified" not in str(exc):
            logger.warning("Could not refresh timer for %s: %s", user_id, exc)


async def _schedule_timer(application, user_id, msg_id, start_time, end_time, type_):
    if not application.job_queue:
        logger.error("JobQueue is unavailable; cannot schedule timer for %s", user_id)
        return
    for old_job in application.job_queue.get_jobs_by_name(_timer_job_name(user_id)):
        old_job.schedule_removal()
    delay = max(0, min(5, (end_time - get_jordan_time()).total_seconds()))
    job = application.job_queue.run_repeating(
        update_timer,
        interval=5,
        first=delay,
        data=(int(user_id), int(msg_id), start_time, end_time, type_),
        name=_timer_job_name(user_id),
    )
    active_timers[int(user_id)] = job


async def _start_timer_for_bot(bot_client, application, user_id, minutes, type_, request_id=None):
    duration_seconds = max(1, int(minutes)) * 60
    start_time = get_jordan_time()
    end_time = start_time + timedelta(seconds=duration_seconds)
    if type_ == "smoke":
        emoji, label = "🚬", "استراحة التدخين"
    elif type_ == "break":
        emoji, label = "☕", "استراحة الغداء"
    else:
        employee = get_employee_by_telegram_id(user_id)
        custom = get_request_type_by_code(employee.get("branch_id"), type_) if employee and employee.get("branch_id") else None
        label = custom[2] if custom else "الاستئذان"
        emoji = "🕌" if "الصلاة" in label else ("🚻" if "الحمام" in label else "⏳")
    try:
        message = await bot_client.send_message(
            chat_id=user_id,
            text=(
                f"{emoji} بدأت {label}\\n"
                "━━━━━━━━━━━━━━━━━━\\n"
                f"⏳ الوقت المحدد: {int(minutes)} دقيقة\\n"
                "سيظهر العد التنازلي هنا، وسيصلك تنبيه عند الانتهاء."
            ),
        )
    except Exception as exc:
        if request_id is not None:
            _timer_db("outbox_error", str(exc)[:1000], request_id)
        logger.exception("Could not send approved timer start to employee %s", user_id)
        return False

    if request_id is not None:
        saved = _timer_db(
            "save_outbox", user_id, message.message_id, type_,
            start_time, end_time, request_id,
        )
    else:
        saved = _timer_db("save", user_id, message.message_id, type_, start_time, end_time)
    if not saved:
        logger.error(
            "Timer message %s was sent to %s but durable timer state was not saved; "
            "the pending outbox will retry if this was an approved request.",
            message.message_id, user_id,
        )
        return False
    await _schedule_timer(
        application, user_id, message.message_id, start_time, end_time, type_
    )
    return True


async def start_timer(context: ContextTypes.DEFAULT_TYPE, user_id, minutes, type_, request_id=None):
    return await _start_timer_for_bot(
        context.bot, context.application, user_id, minutes, type_, request_id
    )


async def process_pending_timer_starts(application):
    rows = _timer_db("outbox_list")
    if rows is False:
        logger.error("Could not read pending approved timer starts")
        return
    for request_id, user_id, timer_type, minutes in rows:
        await _start_timer_for_bot(
            application.bot, application, user_id, minutes, timer_type, request_id
        )


async def retry_pending_timer_starts(context: ContextTypes.DEFAULT_TYPE):
    await process_pending_timer_starts(context.application)


async def restore_active_timers(application: Application):
    """Restore countdowns and retry durable notifications after a process restart."""
    rows = _timer_db("list")
    if rows is False:
        logger.error("Could not restore active timers because PostgreSQL is unavailable")
    else:
        for user_id, message_id, timer_type, start_time, end_time in rows:
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            await _schedule_timer(
                application,
                user_id,
                message_id,
                start_time.astimezone(JORDAN_TZ),
                end_time.astimezone(JORDAN_TZ),
                timer_type,
            )
        logger.info("Restored %s active employee timer(s)", len(rows))

    await restore_pending_broadcast_reminders(application)
    await process_pending_timer_starts(application)
    if application.job_queue:
        existing = application.job_queue.get_jobs_by_name("pending-timer-starts")
        if not existing:
            application.job_queue.run_repeating(
                retry_pending_timer_starts,
                interval=30,
                first=30,
                name="pending-timer-starts",
            )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Validate callback payloads before indexing or converting user-controlled data.
    data = (query.data or "").split('_')
    if not data or data[0] not in {"approve", "reject", "returned"}:
        await query.edit_message_text("⚠️ هذا الإجراء غير معروف.")
        return

    action = data[0]
    if action == "returned":
        if len(data) != 4:
            await query.edit_message_text("⚠️ بيانات العودة غير صالحة.")
            return
        type_, raw_target_id, raw_timer_message_id = data[1], data[2], data[3]
        raw_request_id = None
    else:
        if len(data) != 4:
            await query.edit_message_text("⚠️ بيانات الطلب غير صالحة.")
            return
        type_, raw_request_id, raw_target_id = data[1], data[2], data[3]

    if type_ not in {"smoke", "break", "leave", "vacation"} and not __import__("re").fullmatch(r"custom-[0-9]+", type_):
        await query.edit_message_text("⚠️ نوع الطلب غير صالح.")
        return
    try:
        target_id = int(raw_target_id)
        request_id = int(raw_request_id) if raw_request_id is not None else None
        timer_message_id = int(raw_timer_message_id) if action == "returned" else None
    except (TypeError, ValueError):
        await query.edit_message_text("⚠️ بيانات الطلب غير صالحة.")
        return

    if action == "returned":
        # Only the employee whose timer was created may confirm their return.
        if query.from_user.id != target_id:
            await query.edit_message_text("⚠️ هذا الزر مخصص للموظف صاحب الاستراحة.")
            return
        user_id = target_id
        if not _timer_db("clear_matching", user_id, type_, timer_message_id):
            await query.edit_message_text("ℹ️ هذا المؤقت انتهى أو تم استبداله بمؤقت أحدث.")
            return
        _remove_timer_job(context, user_id)
        name = get_employee_name(user_id)
        timer_label = type_
        employee = get_employee_by_telegram_id(user_id)
        if type_.startswith("custom-") and employee and employee.get("branch_id"):
            custom = get_request_type_by_code(employee["branch_id"], type_)
            if custom:
                timer_label = custom[2]
        await query.edit_message_text(f"✅ شكراً {name}، تم تسجيل عودتك للعمل.\n\n(تم إنهاء مؤقت {timer_label})")
        if employee and employee.get("branch_id"):
            await send_to_branch_admins(
                context, employee["branch_id"],
                f"🔙 الموظف **{name}** عاد من **{timer_label}**."
            )
        return

    # Approval/rejection is an administrative action, never trust the button alone.
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ غير مصرح لك بتنفيذ هذا الإجراء.")
        return

    emp = get_employee_by_telegram_id(target_id)
    if not emp:
        await query.edit_message_text("⚠️ الموظف المرتبط بهذا الطلب لم يعد موجوداً.")
        return

    if not admin_can_access_branch(query.from_user.id, emp.get("branch_id")):
        await query.edit_message_text("⛔ لا تملك صلاحية إدارة طلبات هذا الفرع.")
        return

    custom_request_type = None
    if type_.startswith("custom-"):
        try:
            custom_id = int(type_.split("-", 1)[1])
        except ValueError:
            await query.edit_message_text("⚠️ نوع الاستئذان غير صالح.")
            return
        custom_request_type = get_request_type(emp.get("branch_id"), custom_id)
        from request_types import _can_manage
        if not custom_request_type or custom_request_type[1] != type_ or not _can_manage(query.from_user.id, emp.get("branch_id")):
            await query.edit_message_text("⛔ لا تملك صلاحية إدارة هذا الاستئذان أو أنه غير موجود.")
            return

    settings = None
    if action == "approve" and type_ in {"smoke", "break"}:
        settings = get_branch_settings(emp.get('branch_id')) if emp.get('branch_id') else None
        if not settings:
            await query.edit_message_text("❌ إعدادات فرع الموظف غير متاحة؛ تعذر تنفيذ الموافقة.")
            return

    decision = "approved" if action == "approve" else "rejected"
    if not request_id or not decide_request(request_id, emp['id'], type_, decision, query.from_user.id):
        await query.edit_message_text("ℹ️ تم التعامل مع هذا الطلب مسبقاً أو لم يعد صالحاً.")
        return

    if action == "approve":
        timer_pending = False
        if type_ in {"smoke", "break"} or custom_request_type:
            timer_info = _timer_db("outbox_for_request", request_id)
            if (
                not timer_info
                or int(timer_info[0]) != target_id
                or timer_info[1] != type_
            ):
                timer_pending = True
                logger.error("Approved request %s has no matching durable timer outbox row", request_id)
            else:
                timer_minutes = int(timer_info[2])
                timer_pending = not await start_timer(
                    context, target_id, timer_minutes, type_, request_id=request_id
                )
                if type_ == "smoke":
                    new_count = get_smoke_count_db(emp["id"])
                    msg_text = (
                        f"تمت الموافقة على استراحة التدخين. "
                        f"رصيدك: {new_count}/{settings['smoke_daily_limit']}."
                    )
                elif type_ == "break":
                    msg_text = f"تمت الموافقة على استراحة الغداء ({timer_minutes} دقيقة)."
                else:
                    msg_text = f"تمت الموافقة على {custom_request_type[2]} ({timer_minutes} دقيقة)."
        else:
            msg_text = f"✅ تمت الموافقة على طلبك ({type_})."
            try:
                await context.bot.send_message(target_id, msg_text)
            except Exception:
                logger.exception("Could not notify employee %s about approval", target_id)

        status_text = (
            "\n\n✅ تمت الموافقة. إعداد المؤقت قيد إعادة المحاولة وسيبدأ تلقائياً."
            if timer_pending
            else "\n\n✅ تم القبول بواسطة المدير."
        )
        await query.edit_message_text(text=f"{query.message.text}{status_text}")

    elif action == "reject":
        rejected_label = custom_request_type[2] if custom_request_type else type_
        try:
            await context.bot.send_message(target_id, f"❌ تم رفض طلبك ({rejected_label}).")
        except Exception:
            logger.exception("Could not notify employee %s about rejected request", target_id)
        await query.edit_message_text(text=f"{query.message.text}\n\n❌ تم الرفض.")

async def my_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🆔: `{update.message.from_user.id}`", parse_mode='Markdown')

def main():
    if not DATABASE_URL:
        logger.critical("DATABASE_URL is required; refusing to start without persistent storage.")
        return
    if not initialize_database_tables():
        logger.critical("Database initialization failed; refusing to start.")
        return
    if not BOT_TOKEN:
        logger.info(
            "Base PostgreSQL tables are initialized. Set TELEGRAM_BOT_TOKEN only after "
            "applying migrations 001–006 with python -m scripts.migrate."
        )
        return
    if not verify_required_migrations():
        logger.critical("Database schema is not production-ready; refusing to start.")
        return
    if not SUPER_ADMIN_TELEGRAM_ID:
        logger.warning("Set SUPER_ADMIN_TELEGRAM_ID in the hosting environment to activate the first administrator.")

    if WEBHOOK_URL and not WEBHOOK_SECRET:
        logger.critical("WEBHOOK_SECRET is required when webhook mode is enabled.")
        return

    application = Application.builder().token(BOT_TOKEN).post_init(restore_active_timers).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("smoke", smoke_request))
    application.add_handler(CommandHandler("break", break_request))
    # Friendly reply-keyboard shortcuts map to the same validated command handlers.
    application.add_handler(MessageHandler(filters.Regex(r"^🚬 طلب استراحة التدخين$"), smoke_request))
    application.add_handler(MessageHandler(filters.Regex(r"^☕ طلب استراحة الغداء$"), break_request))
    application.add_handler(MessageHandler(filters.Regex(r"^ℹ️ المساعدة$"), help_command))
    application.add_handler(CommandHandler("my_id", my_id_command))
    
    # Admin Handlers
    application.add_handler(CommandHandler("list_employees", list_employees))
    application.add_handler(CommandHandler("add_employee", add_employee))
    application.add_handler(CommandHandler("remove_employee", remove_employee))
    application.add_handler(CommandHandler("list_admins", list_admins))
    application.add_handler(CommandHandler("add_admin", add_admin))
    application.add_handler(CommandHandler("remove_admin", remove_admin))
    
    # Conversations
    leave_conv = ConversationHandler(
        entry_points=[CommandHandler("leave", leave_request), MessageHandler(filters.Regex(r"^🚪 طلب مغادرة$"), leave_request)],
        states={LEAVE_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_leave_reason)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    vacation_conv = ConversationHandler(
        entry_points=[CommandHandler("vacation", vacation_request), MessageHandler(filters.Regex(r"^🌴 طلب إجازة$"), vacation_request)],
        states={VACATION_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_vacation_reason)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    application.add_handler(leave_conv)
    application.add_handler(vacation_conv)
    
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(CallbackQueryHandler(button_callback, pattern=r"^(approve|reject|returned)_"))
    register_settings_handlers(application)
    register_request_type_handlers(application)
    register_broadcast_handlers(application)
    
    # -----------------------------------------------
    # 🚨 التعديل لتشغيل Webhook بدلاً من Polling
    # -----------------------------------------------
    if WEBHOOK_URL:
        # وضع Webhook للتشغيل على منصات الاستضافة
        logger.info(f"Setting up Webhook on port {PORT}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,  # مسار سري
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
            secret_token=WEBHOOK_SECRET if WEBHOOK_URL else None,
        )
        logger.info("Bot started in webhook mode; webhook URL is configured.")
    else:
        # وضع Polling (للتشغيل المحلي/الاختبار)
        logger.info("Bot Started with Polling (Local Mode)...")
        application.run_polling(drop_pending_updates=False)
    # -----------------------------------------------

if __name__ == '__main__':
    main()