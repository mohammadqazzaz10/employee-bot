import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes

# --- إعدادات البوت والبيئة ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

# تعريف مراحل المحادثة
LEAVE_REASON, VACATION_REASON = range(2)

# إعدادات التسجيل (Logging)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- إعدادات النظام الإدارية ---
ADMIN_IDS = [1465191277]  # ضع معرفات المديرين هنا

# أرقام الهواتف المصرح لها (التي لا تحتاج تسجيل عبر قاعدة البيانات أول مرة)
authorized_phones = [
    '+962786644106'
]

# مخازن مؤقتة للبيانات
user_database = {}
active_timers = {}    # لتخزين وظائف العداد النشطة
timer_completed = {}  # لتتبع حالة انتهاء العداد

# --- إعدادات قوانين العمل والتدخين ---
MAX_DAILY_SMOKES = 5        # عدد السجائر المسموحة يومياً
SMOKE_DURATION_MINUTES = 6  # مدة السيجارة (دقائق)
SMOKE_START_HOUR = 10       # يبدأ التدخين الساعة 10 صباحاً
SMOKE_GAP_HOURS = 1.5       # الفجوة الزمنية (ساعة ونصف)

JORDAN_TZ = ZoneInfo('Asia/Amman')

# ==========================================
# 🗄️ قسم قاعدة البيانات (Database Section)
# ==========================================

def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات"""
    return psycopg2.connect(DATABASE_URL)

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
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
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
        
        # جدول أوقات السجائر (لحساب الفجوة الزمنية)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cigarette_times (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                taken_at TIMESTAMP WITH TIME ZONE NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Database tables initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Error initializing database tables: {e}")
        return False

# --- دوال التعامل مع الموظفين ---

def save_employee(telegram_id, phone_number, full_name):
    """حفظ أو تحديث بيانات الموظف"""
    try:
        normalized_phone = normalize_phone(phone_number)
        conn = get_db_connection()
        cur = conn.cursor()
        
        if telegram_id:
            # التحقق إذا كان الرقم موجوداً مسبقاً لربطه بـ Telegram ID
            cur.execute("SELECT id FROM employees WHERE phone_number = %s", (normalized_phone,))
            existing = cur.fetchone()
            
            if existing:
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
            # إضافة مدير أو موظف يدوياً بدون Telegram ID
            cur.execute("""
                INSERT INTO employees (phone_number, full_name, last_active)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (telegram_id) DO NOTHING
                RETURNING id
            """, (normalized_phone, full_name))
        
        employee_id = cur.fetchone()[0] if cur.rowcount > 0 else None
        conn.commit()
        cur.close()
        conn.close()
        return employee_id
    except Exception as e:
        logger.error(f"Error saving employee: {e}")
        return None

def get_employee_by_telegram_id(telegram_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM employees WHERE telegram_id = %s", (telegram_id,))
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
        cur.execute("SELECT * FROM employees WHERE phone_number = %s", (normalized,))
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
        cur.execute("SELECT * FROM employees ORDER BY full_name")
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
        cur.execute("DELETE FROM employees WHERE phone_number = %s RETURNING id", (normalized,))
        deleted = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return True if deleted else False
    except Exception as e:
        logger.error(f"Error deleting employee: {e}")
        return False

# --- دوال السجائر والاستراحات ---

def get_smoke_count_db(employee_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        today = date.today()
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
        today = date.today()
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
        today = date.today()
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
        today = date.today()
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

# --- دوال المديرين (Admins) ---

def get_all_admins():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM admins ORDER BY added_at")
        admins = cur.fetchall()
        cur.close()
        conn.close()
        
        admin_ids = [admin['telegram_id'] for admin in admins] if admins else []
        # التأكد من وجود المديرين الافتراضيين
        for admin_id in ADMIN_IDS:
            if admin_id not in admin_ids:
                add_admin_to_db(admin_id, is_super=True)
                admin_ids.append(admin_id)
        return admin_ids
    except Exception as e:
        logger.error(f"Error getting admins: {e}")
        return ADMIN_IDS

def is_admin(user_id):
    return user_id in get_all_admins()

def is_super_admin(user_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT is_super_admin FROM admins WHERE telegram_id = %s", (user_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return result[0] if result else (user_id in ADMIN_IDS)
    except:
        return user_id in ADMIN_IDS

def add_admin_to_db(telegram_id, added_by=None, is_super=False):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO admins (telegram_id, added_by, is_super_admin)
            VALUES (%s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE SET is_super_admin = EXCLUDED.is_super_admin
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
        if telegram_id in ADMIN_IDS: return False
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

# --- دوال مساعدة (Helpers) ---

def get_jordan_time():
    return datetime.now(JORDAN_TZ)

def normalize_phone(phone_number):
    if not phone_number: return ""
    digits_only = ''.join(filter(str.isdigit, phone_number))
    while digits_only.startswith('00'): digits_only = digits_only[2:]
    return digits_only

def verify_employee(phone_number):
    normalized_input = normalize_phone(phone_number)
    for auth_phone in authorized_phones:
        if normalize_phone(auth_phone) == normalized_input:
            return True
    return False

def get_user_phone(user_id):
    employee = get_employee_by_telegram_id(user_id)
    if employee: return employee.get('phone_number')
    return user_database.get(user_id, {}).get('phone')

def get_employee_name(user_id, default="المستخدم"):
    employee = get_employee_by_telegram_id(user_id)
    if employee and employee.get('full_name'): return employee.get('full_name')
    return default

def add_employee_to_authorized(phone_number):
    if not phone_number.startswith('+'): phone_number = '+' + phone_number
    if phone_number not in authorized_phones:
        authorized_phones.append(phone_number)
        return True
    return False

def remove_employee_from_authorized(phone_number):
    normalized = normalize_phone(phone_number)
    for auth in authorized_phones[:]:
        if normalize_phone(auth) == normalized:
            authorized_phones.remove(auth)
            return True
    return False

# ==========================================
# ⏱️ قسم العداد والأنيميشن (Timer Section)
# ==========================================

def create_progress_bar(current_seconds, total_seconds, length=15):
    """إنشاء شريط التقدم"""
    if total_seconds == 0: return ""
    percentage = max(0, min(1, current_seconds / total_seconds))
    filled = int(percentage * length)
    empty = length - filled
    bar = '█' * filled + '░' * empty
    percent_num = int(percentage * 100)
    return f"[{bar}] {percent_num}%"

async def update_timer(context: ContextTypes.DEFAULT_TYPE):
    """وظيفة تحديث العداد"""
    job = context.job
    user_id, msg_id, end_time, type_, total_duration_minutes = job.data
    
    # إذا تم إنهاء المؤقت مسبقاً، لا تفعل شيئاً
    if timer_completed.get(user_id):
        return
    
    now = get_jordan_time()
    remaining = end_time - now
    remaining_seconds = int(remaining.total_seconds())
    
    # --- حالة انتهاء الوقت ---
    if remaining_seconds <= 0:
        timer_completed[user_id] = True
        
        # تنظيف الوظائف المجدولة
        if user_id in active_timers:
            for t in active_timers[user_id]:
                try:
                    t.schedule_removal()
                except:
                    pass
            del active_timers[user_id]
            
        # إعداد رسالة التنبيه
        request_name = "استراحة التدخين" if type_ == 'smoke' else "استراحة الغداء"
        
        alert_msg = (
            "🔔🔔🔔 **تنبيه هام!** 🔔🔔🔔\n\n"
            f"🛑 **انتهى وقت {request_name}!**\n"
            "يرجى العودة للعمل فوراً.\n"
            "🔔🔔🔔🔔🔔🔔🔔🔔🔔"
        )
        
        keyboard = [[InlineKeyboardButton("✅ تم العودة للعمل", callback_data=f"returned_{type_}_{user_id}")]]
        
        try:
            # إرسال رسالة جديدة للتنبيه (لضمان الرنين)
            await context.bot.send_message(
                chat_id=user_id,
                text=alert_msg,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            # تعديل رسالة العداد القديمة
            await context.bot.edit_message_text(
                chat_id=user_id,
                message_id=msg_id,
                text="✅ **انتهى الوقت!**"
            )
        except Exception as e:
            logger.error(f"Timer finish error: {e}")
        return

    # --- تحديث الأنيميشن (أثناء العد) ---
    minutes = remaining_seconds // 60
    seconds = remaining_seconds % 60
    
    total_seconds = total_duration_minutes * 60
    bar = create_progress_bar(remaining_seconds, total_seconds)
    emoji = "🚬" if type_ == 'smoke' else "☕"
    
    status_emoji = "🟢"
    if remaining_seconds < total_seconds * 0.25:
        status_emoji = "🔴"
    elif remaining_seconds < total_seconds * 0.5:
        status_emoji = "🟡"

    text = (
        f"{emoji} **العداد التنازلي** {emoji}\n\n"
        f"{status_emoji} الحالة: جاري الاحتساب\n\n"
        f"⏱ الوقت المتبقي:\n"
        f"╔═══════════════╗\n"
        f"║  {minutes:02d}:{seconds:02d}  ║\n"
        f"╚═══════════════╝\n\n"
        f"{bar}\n\n"
        f"🕐 ينتهي في: {end_time.strftime('%H:%M:%S')}"
    )
    
    try:
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=msg_id,
            text=text
        )
    except Exception:
        # تجاهل الأخطاء إذا لم يتغير النص أو مشاكل الشبكة البسيطة
        pass

async def start_timer(context: ContextTypes.DEFAULT_TYPE, user_id: int, minutes: int, type_: str):
    """بدء العداد مع تحديث كل 5 ثواني لتجنب الحظر"""
    
    # تنظيف أي مؤقتات سابقة
    if user_id in active_timers:
        for job in active_timers[user_id]:
            try: job.schedule_removal()
            except: pass
            
    end_time = get_jordan_time() + timedelta(minutes=minutes)
    timer_completed[user_id] = False
    
    emoji = "🚬" if type_ == 'smoke' else "☕"
    
    # إرسال الرسالة الأولية
    try:
        msg = await context.bot.send_message(
            user_id, 
            f"{emoji} بدأ المؤقت: {minutes} دقائق... جاري التحميل."
        )
        
        jobs = []
        total_seconds = minutes * 60
        # التحديث كل 5 ثواني بدلاً من ثانية واحدة (الحل لمشكلة التجميد)
        update_interval = 5
        
        # جدولة التحديثات
        for i in range(0, total_seconds, update_interval):
            job = context.job_queue.run_once(
                update_timer, 
                i, 
                data=(user_id, msg.message_id, end_time, type_, minutes),
                name=f"timer_{user_id}_{i}"
            )
            jobs.append(job)
            
        # جدولة النهاية الحتمية عند الصفر
        final_job = context.job_queue.run_once(
            update_timer,
            total_seconds,
            data=(user_id, msg.message_id, end_time, type_, minutes),
            name=f"timer_final_{user_id}"
        )
        jobs.append(final_job)
        
        active_timers[user_id] = jobs
        
    except Exception as e:
        logger.error(f"Failed to start timer: {e}")

# ==========================================
# 🎮 أوامر البوت (Bot Commands)
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_name = get_employee_name(user.id)
    user_phone = get_user_phone(user.id)
    
    if user_phone and verify_employee(user_phone):
        msg = (
            f"مرحبًا {user_name}! 👋\n\n"
            "✅ هويتك مفعلة.\n\n"
            "🚬 **قوانين التدخين:**\n"
            f"- العدد المسموح: {MAX_DAILY_SMOKES} سجائر.\n"
            f"- مدة السيجارة: {SMOKE_DURATION_MINUTES} دقائق.\n"
            f"- وقت البدء: بعد الساعة {SMOKE_START_HOUR}:00 صباحاً.\n"
            f"- الفجوة الزمنية: {SMOKE_GAP_HOURS} ساعة.\n\n"
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

async def smoke_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    phone = get_user_phone(user.id)
    
    if not phone or not verify_employee(phone):
        await update.message.reply_text("❌ غير مصرح لك. شارك رقم هاتفك أولاً.")
        return

    # 1. التحقق من وقت البدء
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

    # 2. التحقق من الفجوة الزمنية
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

    # 3. التحقق من العدد
    count = get_smoke_count_db(employee['id'])
    if count >= MAX_DAILY_SMOKES:
        await update.message.reply_text(f"❌ انتهى رصيد السجائر لهذا اليوم ({MAX_DAILY_SMOKES}).")
        return

    # إرسال الطلب للمدير
    name = employee['full_name']
    
    await update.message.reply_text("⏳ تم إرسال الطلب للمدير...")
    
    keyboard = [[
        InlineKeyboardButton("✅ قبول", callback_data=f"approve_smoke_{user.id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_smoke_{user.id}")
    ]]
    markup = InlineKeyboardMarkup(keyboard)
    
    msg = (
        f"🚬 **طلب تدخين جديد**\n"
        f"👤 الموظف: {name}\n"
        f"🔢 المستهلك: {count}/{MAX_DAILY_SMOKES}\n"
        f"⏱ المدة المطلوبة: {SMOKE_DURATION_MINUTES} دقائق"
    )
    await send_to_all_admins(context, msg, markup)

async def break_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    phone = get_user_phone(user.id)
    if not phone or not verify_employee(phone): return
    
    employee = get_employee_by_telegram_id(user.id)
    if has_taken_lunch_break_today(employee['id']):
        await update.message.reply_text("❌ لقد أخذت استراحة الغداء بالفعل اليوم.")
        return

    await update.message.reply_text("⏳ جاري طلب الاستراحة...")
    keyboard = [[
        InlineKeyboardButton("✅ قبول", callback_data=f"approve_break_{user.id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_break_{user.id}")
    ]]
    msg = f"☕ **طلب استراحة غداء**\n👤 الموظف: {employee['full_name']}"
    await send_to_all_admins(context, msg, InlineKeyboardMarkup(keyboard))

async def leave_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if not verify_employee(get_user_phone(user.id)): return ConversationHandler.END
    await update.message.reply_text("📝 اكتب سبب المغادرة:")
    return LEAVE_REASON

async def receive_leave_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    reason = update.message.text
    name = get_employee_name(user.id)
    
    keyboard = [[
        InlineKeyboardButton("✅ قبول", callback_data=f"approve_leave_{user.id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_leave_{user.id}")
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
    reason = update.message.text
    name = get_employee_name(user.id)
    
    keyboard = [[
        InlineKeyboardButton("✅ قبول", callback_data=f"approve_vacation_{user.id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_vacation_{user.id}")
    ]]
    msg = f"🌴 **طلب عطلة**\n👤 الموظف: {name}\n📝 التفاصيل: {reason}"
    await send_to_all_admins(context, msg, InlineKeyboardMarkup(keyboard))
    await update.message.reply_text("تم إرسال الطلب.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء.")
    return ConversationHandler.END

# --- أوامر الإدارة ---
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
        add_employee_to_authorized(phone)
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
        remove_employee_from_authorized(phone)
        await update.message.reply_text("✅ تم الحذف.")
    else:
        await update.message.reply_text("❌ لم يتم العثور على الموظف.")

async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id): return
    admins = get_all_admins()
    await update.message.reply_text(f"عدد المديرين: {len(admins)}\nالمعرفات: {admins}")

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.message.from_user.id): return
    try:
        new_id = int(context.args[0])
        add_admin_to_db(new_id, update.message.from_user.id)
        await update.message.reply_text("✅ تم إضافة المدير.")
    except:
        await update.message.reply_text("خطأ في المعرف.")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.message.from_user.id): return
    try:
        target_id = int(context.args[0])
        if remove_admin_from_db(target_id):
            await update.message.reply_text("✅ تم حذف المدير.")
        else:
            await update.message.reply_text("لا يمكن حذف هذا المدير.")
    except:
        await update.message.reply_text("خطأ.")

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if contact.user_id != update.message.from_user.id: return
    
    phone = contact.phone_number
    name = contact.first_name
    save_employee(contact.user_id, phone, name)
    
    if verify_employee(phone):
        await update.message.reply_text("✅ تم تفعيل حسابك بنجاح! يمكنك الآن استخدام البوت.")
    else:
        await update.message.reply_text("⚠️ رقمك غير مسجل في النظام. تواصل مع المدير.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_')
    action = data[0]
    
    if action == "returned":
        user_id = int(data[2])
        type_ = data[1]
        name = get_employee_name(user_id)
        await query.edit_message_text(f"✅ شكراً {name}، تم تسجيل عودتك للعمل.")
        await send_to_all_admins(context, f"🔙 الموظف {name} عاد من {type_}.")
        return

    type_ = data[1]
    target_id = int(data[2])
    emp = get_employee_by_telegram_id(target_id)
    
    if action == "approve":
        msg_text = ""
        if type_ == 'smoke':
            new_count = increment_smoke_count_db(emp['id'])
            record_cigarette_time(emp['id'])
            msg_text = f"✅ تمت الموافقة! (رصيدك: {new_count}/{MAX_DAILY_SMOKES})\nمدة السيجارة: {SMOKE_DURATION_MINUTES} دقائق."
            # بدء العداد - هنا يتم الاستدعاء
            await start_timer(context, target_id, SMOKE_DURATION_MINUTES, 'smoke')
        
        elif type_ == 'break':
            mark_lunch_break_taken(emp['id'])
            msg_text = "✅ تمت الموافقة على الغداء (30 دقيقة)."
            await start_timer(context, target_id, 30, 'break')
            
        else:
            msg_text = "✅ تمت الموافقة على طلبك."
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

# --- Main Function ---
def main():
    if not BOT_TOKEN:
        print("Error: No Token.")
        return
        
    initialize_database_tables()
    
    # تحميل الموظفين لقائمة التصريح
    emps = get_all_employees()
    for e in emps: add_employee_to_authorized(e['phone_number'])
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
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
    
    print("Bot Started...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
