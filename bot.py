import os
import logging
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes

LEAVE_REASON, VACATION_REASON, EDIT_DETAIL_SELECT, EDIT_DETAIL_INPUT = range(4)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# قائمة معرفات المديرين - يمكنك إضافة أكثر من مدير هنا
ADMIN_IDS = [1465191277, 6798279805]

authorized_phones = [
    '+962786644106'
]

user_database = {}
daily_smoke_count = {}

MAX_DAILY_SMOKES = 6
MAX_SMOKES_FRIDAY = 3

JORDAN_TZ = ZoneInfo('Asia/Amman')

WORK_START_HOUR = 8
WORK_START_MINUTE = 0
WORK_END_HOUR = 17
WORK_REGULAR_HOURS = 9
WORK_OVERTIME_HOURS = 2
WORK_END_WITH_OVERTIME_HOUR = 19
LATE_GRACE_PERIOD_MINUTES = 15

active_timers = {}
timer_completed = {}

SMOKE_DATA_FILE = 'smoke_data.json'

def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات"""
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

def initialize_database_tables():
    """إنشاء الجداول المطلوبة إذا لم تكن موجودة"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cigarette_times (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                taken_at TIMESTAMP WITH TIME ZONE NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                check_in_time TIMESTAMP WITH TIME ZONE,
                check_out_time TIMESTAMP WITH TIME ZONE,
                is_late BOOLEAN DEFAULT FALSE,
                late_minutes INTEGER DEFAULT 0,
                late_reason TEXT,
                total_work_hours DECIMAL(4,2),
                overtime_hours DECIMAL(4,2) DEFAULT 0,
                status VARCHAR(20) DEFAULT 'present',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, date)
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                warning_type VARCHAR(50) NOT NULL,
                warning_reason TEXT NOT NULL,
                date DATE NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
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
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Database tables initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Error initializing database tables: {e}")
        return False

def get_jordan_time():
    """الحصول على الوقت الحالي بتوقيت الأردن"""
    return datetime.now(JORDAN_TZ)

def record_check_in(employee_id):
    """تسجيل حضور الموظف"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        now = get_jordan_time()
        today = now.date()
        
        cur.execute("""
            SELECT check_in_time, is_late, late_minutes FROM attendance
            WHERE employee_id = %s AND date = %s
        """, (employee_id, today))
        
        existing = cur.fetchone()
        if existing:
            cur.close()
            conn.close()
            return {
                'success': False,
                'error': 'already_checked_in',
                'check_in_time': existing[0],
                'is_late': existing[1],
                'late_minutes': existing[2]
            }
        
        work_start = now.replace(hour=WORK_START_HOUR, minute=WORK_START_MINUTE, second=0, microsecond=0)
        late_minutes = max(0, int((now - work_start).total_seconds() / 60))
        is_late = late_minutes > LATE_GRACE_PERIOD_MINUTES
        
        cur.execute("""
            INSERT INTO attendance (employee_id, date, check_in_time, is_late, late_minutes, status)
            VALUES (%s, %s, %s, %s, %s, 'present')
            RETURNING id, is_late, late_minutes
        """, (employee_id, today, now, is_late, late_minutes))
        
        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        return {
            'success': True,
            'check_in_time': now,
            'is_late': result[1] if result else is_late,
            'late_minutes': result[2] if result else late_minutes
        }
    except Exception as e:
        logger.error(f"خطأ في تسجيل الحضور: {e}")
        return {'success': False, 'error': str(e)}

def increment_smoke_count_db(employee_id):
    """زيادة عدد السجائر اليومية في قاعدة البيانات"""
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
        logger.info(f"تم زيادة عداد السجائر للموظف {employee_id} في {today}: {new_count}")
        return new_count
    except Exception as e:
        logger.error(f"خطأ في زيادة عداد السجائر: {e}")
        return 0

def check_smoke_limit(employee_id, is_friday=False):
    """التحقق من عدد السجائر المسموح بها اليوم"""
    today = date.today()
    current_smoke_count = get_smoke_count_db(employee_id)
    
    if is_friday:
        max_smokes_today = MAX_SMOKES_FRIDAY
    else:
        max_smokes_today = MAX_DAILY_SMOKES
    
    if current_smoke_count >= max_smokes_today:
        return False
    
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دالة البداية - طلب التحقق من هوية المستخدم"""
    user = update.message.from_user
    user_first_name = get_employee_name(user.id)
    
    user_phone = get_user_phone(user.id)
    
    # إضافة تحقق من اليوم الجمعة
    today = get_jordan_time().weekday()  # 4 يعني يوم الجمعة
    is_friday = today == 4
    
    if user_phone and verify_employee(user_phone):
        welcome_message = (
            f"مرحبًا {user_first_name}! 👋\n\n"
            "✅ تم التحقق من هويتك بنجاح!\n\n"
            f"📱 رقم الهاتف المسجل: {user_phone}\n\n"
            "┏━━━━━━━━━━━━━━━━━━━━━┓\n"
            "┃   📚 قائمة الأوامر   ┃\n"
            "┗━━━━━━━━━━━━━━━━━━━━━┛\n\n"
            "🔹 أوامر الحضور والانصراف:\n"
            "━━━━━━━━━━━━━━━━━\n"
            "/check_in - تسجيل الحضور 📥\n"
            "  (إلزامي في بداية الدوام)\n\n"
            "/check_out - تسجيل الانصراف 📤\n"
            "  (إلزامي في نهاية الدوام)\n\n"
            "🔹 أوامر الاستراحات:\n"
            "━━━━━━━━━━━━━━━━━\n"
            "/smoke - طلب استراحة تدخين 🚬\n"
            f"  (5 دقائق، حد أقصى {MAX_SMOKES_FRIDAY if is_friday else MAX_DAILY_SMOKES} سجائر/يوم)\n\n"
            "/break - طلب استراحة غداء ☕\n"
            "  (30 دقيقة، مرة واحدة في اليوم)\n\n"
            "🔹 أوامر الإجازات:\n"
            "━━━━━━━━━━━━━━━━━\n"
            "/leave - طلب مغادرة العمل 🚪\n"
            "  (مع سبب المغادرة)\n\n"
            "/vacation - طلب عطلة 🌴\n"
            "  (مع سبب وعذر)\n\n"
            "/help - عرض المساعدة 📖\n\n"
        )
        
        if is_admin(user.id):
            welcome_message += (
                "🔸 أوامر المدير:\n"
                "━━━━━━━━━━━━━━━━━\n"
                "/list_employees - عرض جميع الموظفين 👥\n"
                "/add_employee - إضافة موظف جديد ➕\n"
                "/remove_employee - حذف موظف ❌\n"
                "/edit_employee - تعديل بيانات موظف ✏️\n"
                "/daily_report - التقرير اليومي 📊\n"
                "/weekly_report - التقرير الأسبوعي 📈\n\n"
            )
        
        await update.message.reply_text(welcome_message)
    else:
        keyboard = [[KeyboardButton("مشاركة رقم الهاتف 📱", request_contact=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        welcome_message = (
            f"مرحبًا {user_first_name}! 👋\n\n"
            "أنا بوت إدارة حضور الموظفين.\n\n"
            "⚠️ للبدء، يرجى مشاركة رقم هاتفك للتحقق من هويتك كموظف.\n\n"
            "اضغط على الزر أدناه لمشاركة رقم الهاتف:"
        )
        
        await update.message.reply_text(welcome_message, reply_markup=reply_markup)