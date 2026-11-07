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

# تعريف مراحل المحادثة
LEAVE_REASON, VACATION_REASON = range(2)

# --- إعدادات البيئة لـ Telegram و Webhook ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DATABASE_SSLMODE = os.environ.get("DATABASE_SSLMODE", "prefer").strip() or "prefer"
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_URL = (os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")

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
MAX_DAILY_SMOKES = 5        # عدد السجائر المسموحة
SMOKE_DURATION_MINUTES = 6  # مدة السيجارة بالدقائق
SMOKE_START_HOUR = 10       # بداية وقت التدخين (العاشرة صباحاً)
SMOKE_GAP_HOURS = 1.5       # الفجوة بين السجائر بالساعات

JORDAN_TZ = ZoneInfo('Asia/Amman')

active_timers = {}
timer_completed = {}

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

def initialize_database_tables():
    """إنشاء الجداول المطلوبة"""
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
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
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
    except Exception as e:
        logger.error(f"Error initializing database tables: {e}")
        return False

# --- دوال الموظفين وقاعدة البيانات (لم يتم تغييرها) ---
# ... (دوال save_employee, get_employee_by_telegram_id, get_employee_by_phone, get_all_employees, delete_employee_by_phone)
def save_employee(telegram_id, phone_number, full_name):
    """حفظ أو تحديث بيانات الموظف"""
    try:
        normalized_phone = normalize_phone(phone_number)
        conn = get_db_connection()
        cur = conn.cursor()
        
        if telegram_id:
            existing_by_phone = get_employee_by_phone(phone_number)
            if existing_by_phone and not existing_by_phone.get('telegram_id'):
                cur.execute("""
                    UPDATE employees 
                    SET telegram_id = %s, full_name = %s, last_active = CURRENT_TIMESTAMP
                    WHERE phone_number = %s
                    RETURNING id
                """, (telegram_id, full_name, normalized_phone))
            else:
                cur.execute("""
                    INSERT INTO employees (telegram_id, phone_number, full_name, last_active)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (telegram_id) 
                    DO UPDATE SET 
                        phone_number = EXCLUDED.phone_number,
                        full_name = EXCLUDED.full_name,
                        last_active = CURRENT_TIMESTAMP
                    RETURNING id
                """, (telegram_id, normalized_phone, full_name))
        else:
            existing = get_employee_by_phone(phone_number)
            if existing:
                cur.execute("""
                    UPDATE employees 
                    SET full_name = %s, last_active = CURRENT_TIMESTAMP
                    WHERE phone_number = %s
                    RETURNING id
                """, (full_name, normalized_phone))
            else:
                cur.execute("""
                    INSERT INTO employees (phone_number, full_name, last_active)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    RETURNING id
                """, (normalized_phone, full_name))
        
        employee_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return employee_id
    except Exception as e:
        logger.error(f"خطأ في حفظ بيانات الموظف: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return None

def get_employee_by_telegram_id(telegram_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM employees WHERE telegram_id = %s AND is_active = TRUE", (telegram_id,))
        employee = cur.fetchone()
        cur.close()
        conn.close()
        return dict(employee) if employee else None
    except Exception as e:
        logger.error(f"Error getting employee: {e}")
        return None

def get_employee_by_phone(phone_number):
    try:
        normalized = normalize_phone(phone_number)
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM employees WHERE phone_number = %s AND is_active = TRUE ORDER BY id DESC LIMIT 1", (normalized,))
        employee = cur.fetchone()
        cur.close()
        conn.close()
        return dict(employee) if employee else None
    except Exception as e:
        logger.error(f"Error getting employee by phone: {e}")
        return None

def get_all_employees():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM employees WHERE is_active = TRUE ORDER BY full_name")
        employees = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(emp) for emp in employees] if employees else []
    except Exception as e:
        logger.error(f"Error getting all employees: {e}")
        return []

def delete_employee_by_phone(phone_number):
    try:
        normalized = normalize_phone(phone_number)
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE employees
            SET is_active = FALSE, last_active = CURRENT_TIMESTAMP
            WHERE phone_number = %s AND is_active = TRUE
            RETURNING id
        """, (normalized,))
        deleted = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return True if deleted else False
    except Exception as e:
        logger.error(f"Error deleting employee: {e}")
        return False

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
        cur.execute("""
            UPDATE requests
            SET status = %s,
                responded_at = CURRENT_TIMESTAMP,
                decided_by = %s,
                decided_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND employee_id = %s
              AND request_type = %s AND status = 'pending'
            RETURNING id
        """, (decision, admin_id, request_id, employee_id, request_type))
        changed = cur.fetchone()
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
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        today = get_jordan_time().date()
        cur.execute("""
            SELECT count FROM daily_cigarettes 
            WHERE employee_id = %s AND date = %s
        """, (employee_id, today))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return result[0] if result else 0
    except Exception as e:
        logger.error(f"Error getting smoke count: {e}")
        return 0

def increment_smoke_count_db(employee_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        today = get_jordan_time().date()
        cur.execute("""
            INSERT INTO daily_cigarettes (employee_id, date, count, updated_at)
            VALUES (%s, %s, 1, CURRENT_TIMESTAMP)
            ON CONFLICT (employee_id, date)
            DO UPDATE SET 
                count = daily_cigarettes.count + 1,
                updated_at = CURRENT_TIMESTAMP
            RETURNING count
        """, (employee_id, today))
        new_count = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return new_count
    except Exception as e:
        logger.error(f"Error incrementing smoke count: {e}")
        return 0

def get_last_cigarette_time(employee_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT taken_at FROM cigarette_times 
            WHERE employee_id = %s
            ORDER BY taken_at DESC
            LIMIT 1
        """, (employee_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        if result:
            last_time = result[0]
            if last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=timezone.utc)
            return last_time.astimezone(JORDAN_TZ)
        return None
    except Exception as e:
        logger.error(f"Error getting last cigarette time: {e}")
        return None

def record_cigarette_time(employee_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        jordan_time = get_jordan_time()
        cur.execute("""
            INSERT INTO cigarette_times (employee_id, taken_at)
            VALUES (%s, %s)
        """, (employee_id, jordan_time))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error recording cigarette time: {e}")
        return False

def has_taken_lunch_break_today(employee_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        today = get_jordan_time().date()
        cur.execute("""
            SELECT taken FROM lunch_breaks 
            WHERE employee_id = %s AND date = %s AND taken = TRUE
        """, (employee_id, today))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return bool(result)
    except Exception as e:
        logger.error(f"Error checking lunch break: {e}")
        return False

def mark_lunch_break_taken(employee_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        today = get_jordan_time().date()
        jordan_time = get_jordan_time()
        cur.execute("""
            INSERT INTO lunch_breaks (employee_id, date, taken, taken_at)
            VALUES (%s, %s, TRUE, %s)
            ON CONFLICT (employee_id, date)
            DO UPDATE SET 
                taken = TRUE,
                taken_at = %s
        """, (employee_id, today, jordan_time, jordan_time))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error marking lunch break: {e}")
        return False
# --- دوال المديرين (لم يتم تغييرها) ---
# ... (جميع دوال المديرين)
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
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM admins WHERE telegram_id = %s", (user_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return bool(result)
    except Exception as e:
        logger.error("Error checking admin access: %s", e)
        if conn:
            conn.close()
        return False

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
    admin_ids = get_all_admins()
    for admin_id in admin_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Failed to send to admin {admin_id}: {e}")

# --- أدوات مساعدة (لم يتم تغييرها) ---
# ... (جميع الأدوات المساعدة)
def get_jordan_time():
    return datetime.now(JORDAN_TZ)

def normalize_phone(phone_number):
    if not phone_number:
        return ""
    digits_only = ''.join(filter(str.isdigit, str(phone_number)))
    while digits_only.startswith("00"):
        digits_only = digits_only[2:]
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
    if employee: return employee.get('phone_number')
    return user_database.get(user_id, {}).get('phone')

def get_employee_name(user_id, default="المستخدم"):
    employee = get_employee_by_telegram_id(user_id)
    if employee and employee.get('full_name'): return employee.get('full_name')
    return default

# --- أوامر البوت (لم يتم تغييرها) ---
# ... (جميع أوامر البوت: start, help_command)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_name = get_employee_name(user.id)
    user_phone = get_user_phone(user.id)
    
    if user_phone and verify_employee(user_phone):
        msg = (
            f"مرحبًا {user_name}! 👋\n\n"
            "✅ هويتك مفعلة.\n\n"
            "🚬 **قوانين التدخين الجديدة:**\n"
            f"- العدد المسموح: {MAX_DAILY_SMOKES} سجائر.\n"
            f"- مدة السيجارة: {SMOKE_DURATION_MINUTES} دقائق.\n"
            f"- وقت البدء: بعد الساعة {SMOKE_START_HOUR} صباحاً.\n"
            f"- الفجوة الزمنية: ساعة ونصف.\n\n"
            "📝 **الأوامر المتاحة:**\n"
            "/smoke - طلب استراحة تدخين 🚬\n"
            "/break - طلب استراحة غداء ☕\n"
            "/leave - طلب مغادرة 🚪\n"
            "/vacation - طلب عطلة 🌴\n"
        )
        if is_admin(user.id):
            msg += (
                "\n👔 **أوامر المدير:**\n"
                "/list_employees - عرض الموظفين\n"
                "/add_employee - إضافة موظف\n"
                "/remove_employee - حذف موظف\n"
                "/list_admins - عرض المديرين\n"
            )
        await update.message.reply_text(msg)
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
    user = update.message.from_user
    phone = get_user_phone(user.id)
    
    if not phone or not verify_employee(phone):
        await update.message.reply_text("❌ غير مصرح لك. شارك رقم هاتفك أولاً.")
        return

    # التحقق من الوقت (بعد الساعة 10 صباحاً)
    now = get_jordan_time()
    if now.hour < SMOKE_START_HOUR:
        await update.message.reply_text(
            f"⛔️ لا يمكن طلب سيجارة قبل الساعة {SMOKE_START_HOUR}:00 صباحاً!\n"
            f"الساعة الآن: {now.strftime('%H:%M')}"
        )
        return

    employee = get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text("❌ خطأ في البيانات.")
        return

    # التحقق من الفجوة الزمنية
    last_cig = get_last_cigarette_time(employee['id'])
    if last_cig:
        diff = now - last_cig
        hours_passed = diff.total_seconds() / 3600
        if hours_passed < SMOKE_GAP_HOURS:
            remaining_mins = int((SMOKE_GAP_HOURS - hours_passed) * 60)
            await update.message.reply_text(
                f"⏳ يرجى الانتظار!\n"
                f"يجب مرور ساعة ونصف بين السجائر.\n"
                f"المتبقي: {remaining_mins} دقيقة."
            )
            return

    # التحقق من العدد
    count = get_smoke_count_db(employee['id'])
    if count >= MAX_DAILY_SMOKES:
        await update.message.reply_text(f"❌ انتهى رصيد السجائر لهذا اليوم ({MAX_DAILY_SMOKES}).")
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
        f"🔢 المستهلك: {count}/{MAX_DAILY_SMOKES}\n"
        f"⏱ المدة المطلوبة: {SMOKE_DURATION_MINUTES} دقائق"
    )
    await send_to_all_admins(context, msg, markup)

# --- منطق الاستراحة ---
async def break_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    phone = get_user_phone(user.id)
    if not phone or not verify_employee(phone): return
    
    employee = get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text("❌ تعذر العثور على بيانات الموظف.")
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
    await send_to_all_admins(context, msg, InlineKeyboardMarkup(keyboard))
    
# --- المغادرات والإجازات (لم يتم تغييرها) ---
# ... (جميع دوال المغادرات والإجازات)
async def leave_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await send_to_all_admins(context, msg, InlineKeyboardMarkup(keyboard))
    await update.message.reply_text("تم إرسال الطلب.")
    return ConversationHandler.END

async def vacation_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await send_to_all_admins(context, msg, InlineKeyboardMarkup(keyboard))
    await update.message.reply_text("تم إرسال الطلب.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء.")
    return ConversationHandler.END
    
# --- إدارة الموظفين والمديرين (لم يتم تغييرها) ---
# ... (جميع دوال الإدارة)
async def list_employees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id): return
    employees = get_all_employees()
    if not employees:
        await update.message.reply_text("لا يوجد موظفين.")
        return
    msg = "👥 **قائمة الموظفين:**\n"
    for i, e in enumerate(employees, 1):
        msg += f"{i}. {e['full_name']} ({e['phone_number']})\n"
    await update.message.reply_text(msg)

async def add_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id): return
    if len(context.args) < 2:
        await update.message.reply_text("الاستخدام: /add_employee رقم_الهاتف الاسم")
        return
    phone = context.args[0]
    name = ' '.join(context.args[1:])
    if not phone.startswith('+'): phone = '+' + phone
    
    if save_employee(None, phone, name):
        await update.message.reply_text(f"✅ تم إضافة {name}.")
    else:
        await update.message.reply_text("❌ حدث خطأ.")

async def remove_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id): return
    if not context.args:
        await update.message.reply_text("الاستخدام: /remove_employee رقم_الهاتف")
        return
    phone = context.args[0]
    if delete_employee_by_phone(phone):
        await update.message.reply_text("✅ تم إيقاف الموظف مع الاحتفاظ بسجلاته.")
    else:
        await update.message.reply_text("❌ لم يتم العثور على الموظف.")

async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id): return
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
    name = contact.first_name

    # Never create or update an employee record until the shared phone is authorized.
    if not verify_employee(phone):
        await update.message.reply_text("⚠️ رقمك غير مسجل في النظام. تواصل مع المدير.")
        return

    if not save_employee(contact.user_id, phone, name):
        await update.message.reply_text("❌ تعذر تفعيل الحساب حالياً. حاول لاحقاً أو تواصل مع المدير.")
        return

    await update.message.reply_text("✅ تم تفعيل حسابك بنجاح! يمكنك الآن استخدام البوت.")

def create_progress_bar(current, total, length=15):
    pct = current / total if total > 0 else 0
    # عكس النسبة لأننا نعد تنازلياً
    pct_remaining = current / total if total > 0 else 0
    filled = int(pct_remaining * length)
    bar = '█' * (length - filled) + '░' * filled # تم عكس الألوان لتناسب العداد التنازلي
    return f"[{bar}]"

async def update_timer(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id, msg_id, start_time, duration_seconds, type_ = job.data
    
    if timer_completed.get(user_id): return
    
    now = get_jordan_time()
    elapsed = (now - start_time).total_seconds()
    remaining = duration_seconds - elapsed
    secs = int(max(0, remaining))
    
    mins = secs // 60
    s = secs % 60
    
    total_secs = duration_seconds
    
    if secs <= 0:
        # هذه هي آخر تحديث (الصفر) قبل إرسال التنبيه
        timer_completed[user_id] = True
        
        # إرسال التنبيه
        alert_msg = (
            "🔔🔔🔔 **RIIIIIIING!!!** 🔔🔔🔔\n\n"
            "🛑 **انتهى الوقت المحدد!**\n"
            "يرجى العودة للعمل فوراً.\n"
            "🔔🔔🔔🔔🔔🔔🔔🔔🔔"
        )
        key = [[InlineKeyboardButton("✅ تم العودة", callback_data=f"returned_{type_}_{user_id}")]]
        try:
            # تحديث الرسالة الأخيرة للمستخدم بانتهاء الوقت
            await context.bot.edit_message_text(
                chat_id=user_id, 
                message_id=msg_id, 
                text=f"**{type_.capitalize()}** انتهت: 00:00"
            )
            await context.bot.send_message(user_id, alert_msg, reply_markup=InlineKeyboardMarkup(key))
        except Exception as e:
            logger.error(f"Error sending final alert: {e}")
            
        # تنظيف المؤقتات
        if user_id in active_timers:
            for t in active_timers[user_id]: t.schedule_removal()
            del active_timers[user_id]
        return

    # تحديث الأنيميشن
    bar = create_progress_bar(secs, total_secs)
    emoji = "🚬" if type_ == 'smoke' else "☕"
    
    text = (
        f"{emoji} **العداد التنازلي** {emoji}\n\n"
        f"⏳ المتبقي: {mins:02d}:{s:02d}\n"
        f"{bar}\n"
        f"ينتهي في: {(start_time + timedelta(seconds=duration_seconds)).strftime('%H:%M:%S')}"
    )
    
    try:
        await context.bot.edit_message_text(chat_id=user_id, message_id=msg_id, text=text, parse_mode='Markdown')
    except Exception as e: 
        # يتجاهل الأخطاء العادية المتعلقة بعدم وجود تغيير في الرسالة (Message Not Modified)
        if "Message is not modified" not in str(e):
             logger.error(f"Error editing timer message: {e}")

async def start_timer(context, user_id, minutes, type_):
    duration_seconds = minutes * 60
    start_time = get_jordan_time()
    end_time = start_time + timedelta(seconds=duration_seconds)
    timer_completed[user_id] = False
    
    emoji = "🚬" if type_ == 'smoke' else "☕"
    
    msg = await context.bot.send_message(
        user_id, 
        f"{emoji} بدأ المؤقت: {minutes} دقائق."
    )
    
    jobs = []
    # نجدول المهام لكل ثانية. الإضافة +2 تضمن الوصول إلى الصفر لحظياً
    for i in range(duration_seconds + 2):
        j = context.job_queue.run_once(
            update_timer, 
            i, 
            data=(user_id, msg.message_id, start_time, duration_seconds, type_)
        )
        jobs.append(j)
    active_timers[user_id] = jobs

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
        if len(data) != 3:
            await query.edit_message_text("⚠️ بيانات العودة غير صالحة.")
            return
        type_, raw_target_id = data[1], data[2]
        raw_request_id = None
    else:
        if len(data) != 4:
            await query.edit_message_text("⚠️ بيانات الطلب غير صالحة.")
            return
        type_, raw_request_id, raw_target_id = data[1], data[2], data[3]

    if type_ not in {"smoke", "break", "leave", "vacation"}:
        await query.edit_message_text("⚠️ نوع الطلب غير صالح.")
        return
    try:
        target_id = int(raw_target_id)
        request_id = int(raw_request_id) if raw_request_id is not None else None
    except (TypeError, ValueError):
        await query.edit_message_text("⚠️ بيانات الطلب غير صالحة.")
        return

    if action == "returned":
        # Only the employee whose timer was created may confirm their return.
        if query.from_user.id != target_id:
            await query.edit_message_text("⚠️ هذا الزر مخصص للموظف صاحب الاستراحة.")
            return
        user_id = target_id
        name = get_employee_name(user_id)
        # إزالة زر "تم العودة" بعد الضغط عليه
        await query.edit_message_text(f"✅ شكراً {name}، تم تسجيل عودتك للعمل.\n\n(تم إنهاء مؤقت {type_})")
        await send_to_all_admins(context, f"🔙 الموظف **{name}** عاد من **{type_}**.")
        return

    # Approval/rejection is an administrative action, never trust the button alone.
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ غير مصرح لك بتنفيذ هذا الإجراء.")
        return

    emp = get_employee_by_telegram_id(target_id)
    if not emp:
        await query.edit_message_text("⚠️ الموظف المرتبط بهذا الطلب لم يعد موجوداً.")
        return

    decision = "approved" if action == "approve" else "rejected"
    if not request_id or not decide_request(request_id, emp['id'], type_, decision, query.from_user.id):
        await query.edit_message_text("ℹ️ تم التعامل مع هذا الطلب مسبقاً أو لم يعد صالحاً.")
        return

    if action == "approve":
        msg_text = ""
        timer_minutes = 0
        if type_ == 'smoke':
            new_count = increment_smoke_count_db(emp['id'])
            record_cigarette_time(emp['id'])
            timer_minutes = SMOKE_DURATION_MINUTES
            msg_text = f"✅ تمت الموافقة! (رصيدك: {new_count}/{MAX_DAILY_SMOKES})\nمدة السيجارة: {timer_minutes} دقائق."
            await start_timer(context, target_id, timer_minutes, 'smoke')
        
        elif type_ == 'break':
            mark_lunch_break_taken(emp['id'])
            timer_minutes = 30
            msg_text = "✅ تمت الموافقة على الغداء (30 دقيقة)."
            await start_timer(context, target_id, timer_minutes, 'break')
            
        else:
            msg_text = f"✅ تمت الموافقة على طلبك ({type_})."
            try:
                await context.bot.send_message(target_id, msg_text)
            except: pass

        await query.edit_message_text(text=f"{query.message.text}\n\n✅ تم القبول بواسطة المدير.")
        
    elif action == "reject":
        try:
            await context.bot.send_message(target_id, f"❌ تم رفض طلبك ({type_}).")
        except: pass
        await query.edit_message_text(text=f"{query.message.text}\n\n❌ تم الرفض.")

async def my_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🆔: `{update.message.from_user.id}`", parse_mode='Markdown')

def main():
    if not BOT_TOKEN:
        print("Error: No Token.")
        return
        
    if not DATABASE_URL:
        logger.critical("DATABASE_URL is required; refusing to start without persistent storage.")
        return
    if not initialize_database_tables():
        logger.critical("Database initialization failed; refusing to start.")
        return
    if not SUPER_ADMIN_TELEGRAM_ID:
        logger.warning("Set SUPER_ADMIN_TELEGRAM_ID in the hosting environment to activate the first administrator.")

    application = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("smoke", smoke_request))
    application.add_handler(CommandHandler("break", break_request))
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
        entry_points=[CommandHandler("leave", leave_request)],
        states={LEAVE_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_leave_reason)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    vacation_conv = ConversationHandler(
        entry_points=[CommandHandler("vacation", vacation_request)],
        states={VACATION_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_vacation_reason)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    application.add_handler(leave_conv)
    application.add_handler(vacation_conv)
    
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(CallbackQueryHandler(button_callback))
    
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
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
        logger.info("Bot started in webhook mode; webhook URL is configured.")
    else:
        # وضع Polling (للتشغيل المحلي/الاختبار)
        logger.info("Bot Started with Polling (Local Mode)...")
        application.run_polling(drop_pending_updates=True)
    # -----------------------------------------------

if __name__ == '__main__':
    main()
