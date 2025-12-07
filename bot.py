import os
import logging
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, date, time, timezone
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes

# تعريف حالات المحادثة
LEAVE_REASON, VACATION_REASON, PENALTY_MENU, SELECT_PENALTY_TYPE, ENTER_PENALTY_DETAILS, SELECT_EMPLOYEE_FOR_PENALTY, CONFIRM_PENALTY, EDIT_PENALTY_AMOUNT, SELECT_PENALTY_TO_EDIT, EDIT_PENALTY_CUSTOM_AMOUNT, SELECT_EMPLOYEE_FOR_VIEW = range(11)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

ADMIN_IDS = [1465191277]

authorized_phones = [
    '+962786644106'
]

user_database = {}
daily_smoke_count = {}

# نظام العقوبات الجديد
MAX_DAILY_SMOKES = 5
MIN_GAP_BETWEEN_SMOKES_HOURS = 1.5
SMOKE_BREAK_DURATION = 6
SMOKE_ALLOWED_AFTER_HOUR = 10
SMOKE_ALLOWED_AFTER_MINUTE = 0

JORDAN_TZ = ZoneInfo('Asia/Amman')

WORK_START_HOUR = 8
WORK_START_MINUTE = 0
WORK_REGULAR_HOURS = 9
WORK_REGULAR_MINUTES = WORK_REGULAR_HOURS * 60  # 540 دقيقة (9 ساعات)
WORK_OVERTIME_START_HOUR = 17
LATE_GRACE_PERIOD_MINUTES = 15

# إضافة ساعات العمل القياسية بالدقائق
WORK_STANDARD_MINUTES_PER_DAY = WORK_REGULAR_HOURS * 60  # 540 دقيقة = 9 ساعات

active_timers = {}
timer_completed = {}

# تعريف أنواع المخالفات
PENALTY_TYPES = {
    'late_15_30': {'name': 'تأخير 15-30 دقيقة', 'level': 1, 'default_amount': 0, 'default_ban_days': 0},
    'late_30_60': {'name': 'تأخير 30-60 دقيقة', 'level': 2, 'default_amount': 10, 'default_ban_days': 1},
    'late_over_60': {'name': 'تأخير أكثر من ساعة', 'level': 3, 'default_amount': 50, 'default_ban_days': 3},
    'no_check_in': {'name': 'عدم تسجيل حضور', 'level': 3, 'default_amount': 50, 'default_ban_days': 3},
    'no_check_out': {'name': 'عدم تسجيل انصراف', 'level': 2, 'default_amount': 10, 'default_ban_days': 1},
    'smoke_before_10': {'name': 'طلب سيجارة قبل 10 صباحاً', 'level': 1, 'default_amount': 0, 'default_ban_days': 0},
    'smoke_excess': {'name': 'تجاوز عدد السجائر المسموح', 'level': 2, 'default_amount': 10, 'default_ban_days': 1},
    'smoke_gap_violation': {'name': 'عدم احترام الفجوة بين السجائر', 'level': 1, 'default_amount': 0, 'default_ban_days': 0},
    'lunch_twice': {'name': 'طلب استراحة غداء مرتين', 'level': 1, 'default_amount': 0, 'default_ban_days': 0},
    'request_without_checkin': {'name': 'طلب بدون تسجيل حضور', 'level': 2, 'default_amount': 10, 'default_ban_days': 1},
    'early_checkout': {'name': 'انصراف مبكر', 'level': 2, 'default_amount': 20, 'default_ban_days': 2},
    'unauthorized_absence': {'name': 'غياب غير مبرر', 'level': 4, 'default_amount': 100, 'default_ban_days': 7},
    'insubordination': {'name': 'عدم الانصياع للتوجيهات', 'level': 3, 'default_amount': 50, 'default_ban_days': 3},
    'damage_property': {'name': 'إتلاف ممتلكات العمل', 'level': 4, 'default_amount': 200, 'default_ban_days': 14},
    'customer_complaint': {'name': 'شكوى عميل', 'level': 2, 'default_amount': 15, 'default_ban_days': 1}
}

# إضافة مستويات العقوبات
PENALTY_LEVELS = {
    1: {'name': 'إنذار شفهي', 'color': '🟡', 'description': 'تنبيه بسيط بدون خصم'},
    2: {'name': 'إنذار كتابي', 'color': '🟠', 'description': 'تنبيه رسمي مع خصم بسيط'},
    3: {'name': 'إنذار نهائي', 'color': '🔴', 'description': 'تنبيه شديد مع خصم متوسط'},
    4: {'name': 'خصم يوم', 'color': '💸', 'description': 'خصم يوم كامل مع حظر'},
    5: {'name': 'خصم أسبوع', 'color': '🚫', 'description': 'خصم أسبوع مع حظر طويل'}
}

# نصائح للعقوبات
PENALTY_TIPS = {
    'late_15_30': "💡 يمكن توجيه الموظف لتحسين مواعيد الحضور بدلاً من الخصم المباشر.",
    'late_30_60': "⚠️ التأخير المتوسط قد يحتاج لخصم بسيط مع توجيه.",
    'late_over_60': "🚨 التأخير الكبير يتطلب عقوبة رادعة لتجنب التكرار.",
    'no_check_in': "📋 من المهم التأكد من نسيان تسجيل الحضور قبل الخصم.",
    'no_check_out': "⏰ يمكن تذكير الموظفين بتسجيل الانصراف يومياً.",
    'smoke_excess': "🚬 مراجعة نظام السجائر ومراعاة الظروف الخاصة.",
    'unauthorized_absence': "🏠 التأكد من أسباب الغياب قبل تطبيق العقوبة.",
    'customer_complaint': "👥 التحقق من الشكوى أولاً وسماع رأي الموظف."
}

# إعدادات قابلة للتعديل
PENALTY_SETTINGS = {
    'auto_apply_late_penalties': True,
    'notify_employee_on_penalty': True,
    'allow_penalty_appeal': True,
    'max_penalties_per_month': 5,
    'penalty_grace_period_days': 7
}

def get_db_connection():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

def get_jordan_time():
    """الحصول على الوقت الحالي بتوقيت الأردن"""
    return datetime.now(JORDAN_TZ)

def minutes_to_hours_minutes(total_minutes):
    """تحويل الدقائق إلى ساعات ودقائق"""
    hours = int(total_minutes // 60)
    minutes = int(total_minutes % 60)
    return hours, minutes

def format_minutes_to_hours_minutes(total_minutes):
    """تنسيق الدقائق إلى نص (ساعات ودقائق)"""
    hours, minutes = minutes_to_hours_minutes(total_minutes)
    if hours > 0 and minutes > 0:
        return f"{hours} ساعة و {minutes} دقيقة"
    elif hours > 0:
        return f"{hours} ساعة"
    else:
        return f"{minutes} دقيقة"

def calculate_work_time_in_minutes(check_in_time, check_out_time):
    """حساب وقت العمل بالدقائق مع خصم استراحة الغداء"""
    if not check_in_time or not check_out_time:
        return 0
    
    if check_in_time.tzinfo is None:
        check_in_time = check_in_time.replace(tzinfo=timezone.utc).astimezone(JORDAN_TZ)
    if check_out_time.tzinfo is None:
        check_out_time = check_out_time.replace(tzinfo=timezone.utc).astimezone(JORDAN_TZ)
    
    total_minutes = int((check_out_time - check_in_time).total_seconds() / 60)
    
    if total_minutes > 60:
        total_minutes -= 30
    
    return max(0, total_minutes)

def calculate_overtime_in_minutes(work_minutes):
    """حساب الوقت الإضافي بالدقائق"""
    regular_minutes = WORK_REGULAR_MINUTES
    overtime = max(0, work_minutes - regular_minutes)
    return overtime

def initialize_database_tables():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                phone_number VARCHAR(20) NOT NULL,
                full_name VARCHAR(100) NOT NULL,
                last_active TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
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
                total_work_minutes INTEGER DEFAULT 0,
                overtime_minutes INTEGER DEFAULT 0,
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
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS penalties (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                penalty_type VARCHAR(50) NOT NULL,
                penalty_level INTEGER NOT NULL,
                penalty_name VARCHAR(100) NOT NULL,
                deduction_amount DECIMAL(10,2) DEFAULT 0,
                smoke_ban_days INTEGER DEFAULT 0,
                reason TEXT NOT NULL,
                penalty_date DATE NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                is_appealed BOOLEAN DEFAULT FALSE,
                appeal_reason TEXT,
                resolved_at TIMESTAMP WITH TIME ZONE,
                resolved_by BIGINT,
                created_by BIGINT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS penalty_settings (
                id SERIAL PRIMARY KEY,
                setting_key VARCHAR(100) UNIQUE NOT NULL,
                setting_value TEXT NOT NULL,
                description TEXT,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_by BIGINT
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

# ==== نظام التحقق من الحضور =====
def is_employee_checked_in_today(employee_id):
    """التحقق إذا كان الموظف سجل حضوره اليوم"""
    attendance = get_attendance_today(employee_id)
    return attendance and attendance.get('check_in_time') is not None

def get_attendance_today(employee_id):
    """الحصول على سجل الحضور اليوم"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        today = get_jordan_time().date()
        
        cur.execute("""
            SELECT check_in_time, check_out_time, is_late, late_minutes, 
                   total_work_minutes, overtime_minutes
            FROM attendance
            WHERE employee_id = %s AND date = %s
        """, (employee_id, today))
        
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        if result:
            return {
                'check_in_time': result[0],
                'check_out_time': result[1],
                'is_late': result[2],
                'late_minutes': result[3],
                'total_work_minutes': result[4] if result[4] else 0,
                'overtime_minutes': result[5] if result[5] else 0
            }
        return None
    except Exception as e:
        logger.error(f"خطأ في الحصول على سجل الحضور: {e}")
        return None

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
        
        # حساب وقت بدء العمل بتوقيت الأردن
        work_start = datetime.combine(today, time(WORK_START_HOUR, WORK_START_MINUTE), tzinfo=JORDAN_TZ)
        
        # حساب التأخير بالدقائق
        late_minutes = max(0, int((now - work_start).total_seconds() / 60))
        is_late = late_minutes > LATE_GRACE_PERIOD_MINUTES
        
        # تطبيق العقوبات حسب درجة التأخير
        if is_late:
            if 15 < late_minutes <= 30:
                add_penalty(employee_id, 'late_15_30', f'تأخير {late_minutes} دقيقة')
            elif 30 < late_minutes <= 60:
                add_penalty(employee_id, 'late_30_60', f'تأخير {late_minutes} دقيقة')
            elif late_minutes > 60:
                add_penalty(employee_id, 'late_over_60', f'تأخير {late_minutes} دقيقة')
        
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

def record_check_out(employee_id):
    """تسجيل انصراف الموظف"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        now = get_jordan_time()
        today = now.date()
        
        cur.execute("""
            SELECT check_in_time, check_out_time, total_work_minutes, overtime_minutes FROM attendance
            WHERE employee_id = %s AND date = %s
        """, (employee_id, today))
        
        result = cur.fetchone()
        if not result:
            cur.close()
            conn.close()
            return {'success': False, 'error': 'لم يتم تسجيل الحضور اليوم'}
        
        check_in_time, existing_checkout, existing_minutes, existing_overtime = result
        
        if existing_checkout:
            cur.close()
            conn.close()
            return {
                'success': False,
                'error': 'already_checked_out',
                'check_in_time': check_in_time,
                'check_out_time': existing_checkout,
                'total_work_minutes': existing_minutes if existing_minutes else 0,
                'overtime_minutes': existing_overtime if existing_overtime else 0
            }
        
        # حساب وقت العمل بالدقائق
        work_minutes = calculate_work_time_in_minutes(check_in_time, now)
        
        # حساب الوقت الإضافي
        overtime_minutes = calculate_overtime_in_minutes(work_minutes)
        
        cur.execute("""
            UPDATE attendance
            SET check_out_time = %s, total_work_minutes = %s, overtime_minutes = %s
            WHERE employee_id = %s AND date = %s
            RETURNING check_in_time, check_out_time, total_work_minutes, overtime_minutes
        """, (now, work_minutes, overtime_minutes, employee_id, today))
        
        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        return {
            'success': True,
            'check_in_time': result[0],
            'check_out_time': result[1],
            'total_work_minutes': result[2] if result[2] else 0,
            'overtime_minutes': result[3] if result[3] else 0
        }
    except Exception as e:
        logger.error(f"خطأ في تسجيل الانصراف: {e}")
        return {'success': False, 'error': str(e)}

# ==== دوال المساعدة الأساسية ====
def get_employee_by_telegram_id(telegram_id):
    """الحصول على بيانات الموظف من قاعدة البيانات"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM employees WHERE telegram_id = %s", (telegram_id,))
        employee = cur.fetchone()
        cur.close()
        conn.close()
        return dict(employee) if employee else None
    except Exception as e:
        logger.error(f"خطأ في قراءة بيانات الموظف: {e}")
        return None

def get_employee_by_id(employee_id):
    """الحصول على بيانات الموظف بالمعرف"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM employees WHERE id = %s", (employee_id,))
        employee = cur.fetchone()
        cur.close()
        conn.close()
        return dict(employee) if employee else None
    except Exception as e:
        logger.error(f"Error getting employee by ID: {e}")
        return None

def get_employee_name_from_db(user):
    """الحصول على اسم الموظف من قاعدة البيانات"""
    employee = get_employee_by_telegram_id(user.id) if hasattr(user, 'id') else None
    if employee and employee.get('full_name'):
        return employee.get('full_name')
    # استخدام الاسم من تيليجرام كبديل
    if hasattr(user, 'first_name'):
        if user.last_name:
            return f"{user.first_name} {user.last_name}"
        return user.first_name
    return "المستخدم"

def normalize_phone(phone_number):
    """تطبيع رقم الهاتف بإزالة جميع الرموز غير الرقمية والأصفار البادئة"""
    if not phone_number:
        return ""
    digits_only = ''.join(filter(str.isdigit, phone_number))
    while digits_only.startswith('00'):
        digits_only = digits_only[2:]
    return digits_only

def verify_employee(phone_number):
    """التحقق من صلاحية الموظف باستخدام رقم الهاتف"""
    normalized_input = normalize_phone(phone_number)
    for auth_phone in authorized_phones:
        if normalize_phone(auth_phone) == normalized_input:
            return True
    return False

def save_employee(telegram_id, phone_number, full_name):
    """حفظ أو تحديث بيانات الموظف في قاعدة البيانات"""
    try:
        normalized_phone = normalize_phone(phone_number)
        conn = get_db_connection()
        cur = conn.cursor()
        
        if telegram_id:
            existing_by_phone = get_employee_by_phone(normalized_phone)
            
            if existing_by_phone and not existing_by_phone.get('telegram_id'):
                cur.execute("""
                    UPDATE employees 
                    SET telegram_id = %s, full_name = %s, last_active = CURRENT_TIMESTAMP
                    WHERE phone_number = %s
                    RETURNING id
                """, (telegram_id, full_name, normalized_phone))
                logger.info(f"تم تحديث telegram_id للموظف الموجود: {phone_number}")
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
            cur.execute("""
                INSERT INTO employees (phone_number, full_name, last_active)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (phone_number) 
                DO UPDATE SET 
                    full_name = EXCLUDED.full_name,
                    last_active = CURRENT_TIMESTAMP
                    RETURNING id
            """, (normalized_phone, full_name))
        
        employee_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"تم حفظ بيانات الموظف: {full_name} ({phone_number}) - ID: {employee_id}")
        return employee_id
    except Exception as e:
        logger.error(f"خطأ في حفظ بيانات الموظف: {e}")
        return None

def get_employee_by_phone(phone_number):
    """الحصول على بيانات الموظف باستخدام رقم الهاتف"""
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
        logger.error(f"خطأ في قراءة بيانات الموظف برقم الهاتف: {e}")
        return None

# ==== نظام إدارة العقوبات الذكي ====
def get_intelligent_penalty_suggestion(employee_id, penalty_type, context_data=None):
    """تقديم اقتراحات ذكية للعقوبات بناءً على السجلات السابقة"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # الحصول على سجل العقوبات السابقة للموظف
        cur.execute("""
            SELECT penalty_type, COUNT(*) as count, 
                   AVG(deduction_amount) as avg_amount,
                   MAX(penalty_date) as last_date
            FROM penalties 
            WHERE employee_id = %s 
            GROUP BY penalty_type
            ORDER BY count DESC
        """, (employee_id,))
        
        previous_penalties = cur.fetchall()
        cur.close()
        conn.close()
        
        suggestions = []
        
        # تحليل السجلات السابقة
        for penalty in previous_penalties:
            if penalty[0] == penalty_type:
                count = penalty[1]
                avg_amount = float(penalty[2] or 0)
                days_since_last = (get_jordan_time().date() - penalty[3]).days if penalty[3] else 999
                
                if count >= 3:
                    suggestions.append(f"⚠️ هذا الموظف لديه {count} عقوبات سابقة من نفس النوع")
                    suggestions.append(f"📅 آخر عقوبة كانت منذ {days_since_last} يوم")
                    
                    if days_since_last < 30:
                        suggestions.append("🚨 **نصيحة:** فكر في زيادة العقوبة بسبب التكرار المتقارب")
                    elif avg_amount < 20:
                        suggestions.append("💡 **نصيحة:** العقوبات السابقة كانت خفيفة، يمكن زيادة المبلغ")
                
                break
        
        # إضافة نصائح عامة
        if penalty_type in PENALTY_TIPS:
            suggestions.append(PENALTY_TIPS[penalty_type])
        
        # اقتراحات حسب نوع المخالفة
        if penalty_type.startswith('late'):
            suggestions.append("⏰ **تلميح:** يمكن تخفيف العقوبة إذا كان الموظف لديه سجل جيد في الأيام السابقة")
        elif penalty_type.startswith('smoke'):
            suggestions.append("🚬 **تلميح:** مراعاة الظروف النفسية للموظف عند تطبيق عقوبة السجائر")
        
        return suggestions
    except Exception as e:
        logger.error(f"Error getting penalty suggestions: {e}")
        return []

def get_employee_penalty_summary(employee_id):
    """ملخص العقوبات مع تحليل ذكي"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT 
                COUNT(*) as total_penalties,
                COUNT(CASE WHEN is_active = TRUE THEN 1 END) as active_penalties,
                SUM(CASE WHEN is_active = TRUE THEN deduction_amount ELSE 0 END) as total_deduction,
                MAX(penalty_date) as last_penalty_date
            FROM penalties 
            WHERE employee_id = %s
        """, (employee_id,))
        
        stats = cur.fetchone()
        
        cur.execute("""
            SELECT penalty_type, penalty_name, deduction_amount, penalty_date
            FROM penalties 
            WHERE employee_id = %s 
            ORDER BY created_at DESC 
            LIMIT 5
        """, (employee_id,))
        
        recent_penalties = cur.fetchall()
        
        cur.close()
        conn.close()
        
        summary = {
            'total_penalties': stats[0] or 0,
            'active_penalties': stats[1] or 0,
            'total_deduction': float(stats[2] or 0),
            'last_penalty_date': stats[3],
            'recent_penalties': recent_penalties
        }
        
        # إضافة تحليل ذكي
        if summary['total_penalties'] > 0:
            days_since_last = (get_jordan_time().date() - summary['last_penalty_date']).days if summary['last_penalty_date'] else 0
            
            if days_since_last > 90:
                summary['analysis'] = "✅ سجل نظيف منذ 3 أشهر - يمكن التخفيف"
            elif days_since_last > 30:
                summary['analysis'] = "🟡 سجل مقبول - يمكن التوجيه بدلاً من العقوبة"
            else:
                summary['analysis'] = "🔴 تكرار متقارب - يحتاج عقوبة رادعة"
        
        return summary
    except Exception as e:
        logger.error(f"خطأ في حساب ملخص العقوبات: {e}")
        return {'total_penalties': 0, 'active_penalties': 0, 'total_deduction': 0, 'recent_penalties': []}

def add_penalty(employee_id, penalty_type, reason, created_by=None, custom_amount=None, custom_ban_days=None):
    """إضافة عقوبة جديدة مع خيارات مخصصة"""
    try:
        if penalty_type not in PENALTY_TYPES:
            return {'success': False, 'error': 'نوع المخالفة غير موجود'}
        
        penalty_info = PENALTY_TYPES[penalty_type]
        level = penalty_info['level']
        
        # استخدام القيم المخصصة أو الافتراضية
        deduction = custom_amount if custom_amount is not None else penalty_info['default_amount']
        ban_days = custom_ban_days if custom_ban_days is not None else penalty_info['default_ban_days']
        
        conn = get_db_connection()
        cur = conn.cursor()
        today = get_jordan_time().date()
        
        cur.execute("""
            INSERT INTO penalties (employee_id, penalty_type, penalty_level, penalty_name, 
                                  deduction_amount, smoke_ban_days, reason, penalty_date, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (employee_id, penalty_type, level, penalty_info['name'],
              deduction, ban_days, reason, today, created_by))
        
        penalty_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"تم إضافة عقوبة للموظف {employee_id}: {penalty_info['name']} (مبلغ: {deduction} دينار)")
        return {'success': True, 'penalty_id': penalty_id, 'amount': deduction, 'ban_days': ban_days}
    except Exception as e:
        logger.error(f"خطأ في إضافة العقوبة: {e}")
        return {'success': False, 'error': str(e)}

def update_penalty(penalty_id, updates):
    """تحديث العقوبة (مبلغ، حالة، إلخ)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # بناء استعلام التحديث ديناميكياً
        set_clause = []
        params = []
        
        for key, value in updates.items():
            if key in ['deduction_amount', 'smoke_ban_days', 'is_active', 'is_appealed', 'appeal_reason', 'resolved_by']:
                set_clause.append(f"{key} = %s")
                params.append(value)
        
        if not set_clause:
            return {'success': False, 'error': 'لا توجد تحديثات'}
        
        set_clause.append("resolved_at = CURRENT_TIMESTAMP")
        params.append(penalty_id)
        
        query = f"UPDATE penalties SET {', '.join(set_clause)} WHERE id = %s RETURNING id"
        
        cur.execute(query, params)
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        if updated:
            return {'success': True, 'penalty_id': penalty_id}
        else:
            return {'success': False, 'error': 'لم يتم العثور على العقوبة'}
    except Exception as e:
        logger.error(f"خطأ في تحديث العقوبة: {e}")
        return {'success': False, 'error': str(e)}

def get_penalty_details(penalty_id):
    """الحصول على تفاصيل العقوبة"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT p.*, e.full_name, e.phone_number, e.telegram_id
            FROM penalties p
            JOIN employees e ON p.employee_id = e.id
            WHERE p.id = %s
        """, (penalty_id,))
        
        penalty = cur.fetchone()
        cur.close()
        conn.close()
        
        return dict(penalty) if penalty else None
    except Exception as e:
        logger.error(f"خطأ في قراءة تفاصيل العقوبة: {e}")
        return None

def search_employees_by_name_or_phone(search_term):
    """بحث عن موظفين بالاسم أو رقم الهاتف"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        search_pattern = f"%{search_term}%"
        cur.execute("""
            SELECT * FROM employees 
            WHERE full_name ILIKE %s OR phone_number ILIKE %s
            ORDER BY full_name
            LIMIT 10
        """, (search_pattern, search_pattern))
        
        employees = cur.fetchall()
        cur.close()
        conn.close()
        
        return [dict(emp) for emp in employees]
    except Exception as e:
        logger.error(f"خطأ في البحث عن الموظفين: {e}")
        return []

def get_penalty_settings():
    """الحصول على إعدادات العقوبات"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT setting_key, setting_value FROM penalty_settings")
        settings = cur.fetchall()
        cur.close()
        conn.close()
        
        # دمج مع الإعدادات الافتراضية
        all_settings = PENALTY_SETTINGS.copy()
        for key, value in settings:
            if key in all_settings:
                # تحويل القيم حسب النوع
                if isinstance(all_settings[key], bool):
                    all_settings[key] = value.lower() == 'true'
                elif isinstance(all_settings[key], int):
                    all_settings[key] = int(value)
                elif isinstance(all_settings[key], float):
                    all_settings[key] = float(value)
                else:
                    all_settings[key] = value
        
        return all_settings
    except Exception as e:
        logger.error(f"خطأ في قراءة إعدادات العقوبات: {e}")
        return PENALTY_SETTINGS

def update_penalty_setting(key, value, updated_by=None):
    """تحديث إعداد العقوبة"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO penalty_settings (setting_key, setting_value, updated_by)
            VALUES (%s, %s, %s)
            ON CONFLICT (setting_key) 
            DO UPDATE SET 
                setting_value = EXCLUDED.setting_value,
                updated_by = EXCLUDED.updated_by,
                updated_at = CURRENT_TIMESTAMP
        """, (key, str(value), updated_by))
        
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"خطأ في تحديث إعداد العقوبة: {e}")
        return False

def get_employee_penalties(employee_id, active_only=True):
    """الحصول على عقوبات الموظف"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        query = "SELECT * FROM penalties WHERE employee_id = %s"
        params = [employee_id]
        
        if active_only:
            query += " AND is_active = TRUE"
        
        query += " ORDER BY penalty_date DESC, created_at DESC"
        
        cur.execute(query, params)
        penalties = cur.fetchall()
        cur.close()
        conn.close()
        
        return [dict(penalty) for penalty in penalties] if penalties else []
    except Exception as e:
        logger.error(f"خطأ في قراءة عقوبات الموظف: {e}")
        return []

def is_employee_banned_from_smoking(employee_id):
    """التحقق إذا كان الموظف محروم من السجائر"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        today = get_jordan_time().date()
        
        # البحث عن عقوبات حرمان السجائر التي لا تزال سارية
        cur.execute("""
            SELECT smoke_ban_days, penalty_date 
            FROM penalties 
            WHERE employee_id = %s 
                AND is_active = TRUE 
                AND smoke_ban_days > 0
            ORDER BY created_at DESC 
            LIMIT 1
        """, (employee_id,))
        
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        if result:
            smoke_ban_days, penalty_date = result
            ban_end_date = penalty_date + timedelta(days=smoke_ban_days)
            return today <= ban_end_date
        
        return False
    except Exception as e:
        logger.error(f"خطأ في التحقق من حظر السجائر: {e}")
        return False

# ==== دوال التحقق من الصلاحيات ====
def get_all_admins():
    """الحصول على جميع المديرين من قاعدة البيانات"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM admins ORDER BY added_at")
        admins = cur.fetchall()
        cur.close()
        conn.close()
        
        admin_ids = [admin['telegram_id'] for admin in admins] if admins else []
        
        # إضافة المديرين الافتراضيين إذا لم يكونوا موجودين
        for admin_id in ADMIN_IDS:
            if admin_id not in admin_ids:
                add_admin_to_db(admin_id, is_super=True)
                admin_ids.append(admin_id)
        
        return admin_ids
    except Exception as e:
        logger.error(f"خطأ في قراءة المديرين: {e}")
        return ADMIN_IDS

def is_admin(user_id):
    """التحقق من أن المستخدم مدير"""
    admin_ids = get_all_admins()
    return user_id in admin_ids

def is_super_admin(user_id):
    """التحقق من أن المستخدم مدير رئيسي"""
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
    """إضافة مدير إلى قاعدة البيانات"""
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
        logger.error(f"خطأ في إضافة المدير: {e}")
        return False

def remove_admin_from_db(telegram_id):
    """حذف مدير من قاعدة البيانات"""
    try:
        if telegram_id in ADMIN_IDS:
            return False
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM admins WHERE telegram_id = %s AND is_super_admin = FALSE", (telegram_id,))
        rows_deleted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        return rows_deleted > 0
    except Exception as e:
        logger.error(f"خطأ في حذف المدير: {e}")
        return False

async def send_to_all_admins(context, text, reply_markup=None):
    """إرسال رسالة لجميع المديرين"""
    admin_ids = get_all_admins()
    for admin_id in admin_ids:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Failed to send message to admin {admin_id}: {e}")

# ==== دوال السجائر ====
def get_smoke_count_db(employee_id):
    """الحصول على عدد السجائر اليومية من قاعدة البيانات"""
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
        count = result[0] if result else 0
        logger.info(f"قراءة عداد السجائر للموظف {employee_id} في {today}: {count}")
        return count
    except Exception as e:
        logger.error(f"خطأ في قراءة عداد السجائر: {e}")
        return 0

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

def has_taken_lunch_break_today(employee_id):
    """التحقق من أن الموظف قد أخذ بريك غداء اليوم"""
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
        has_taken = bool(result)
        logger.info(f"التحقق من بريك الغداء للموظف {employee_id} في {today}: {has_taken}")
        return has_taken
    except Exception as e:
        logger.error(f"خطأ في التحقق من بريك الغداء: {e}")
        return False

def mark_lunch_break_taken(employee_id):
    """تسجيل أن الموظف قد أخذ بريك غداء اليوم"""
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
        logger.info(f"تم تسجيل بريك الغداء للموظف {employee_id} في {today}")
        return True
    except Exception as e:
        logger.error(f"خطأ في تسجيل بريك الغداء: {e}")
        return False

def get_last_cigarette_time(employee_id):
    """الحصول على وقت آخر سيجارة للموظف"""
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
            last_time_jordan = last_time.astimezone(JORDAN_TZ)
            logger.info(f"آخر سيجارة للموظف {employee_id}: {last_time_jordan}")
            return last_time_jordan
        return None
    except Exception as e:
        logger.error(f"خطأ في الحصول على آخر وقت سيجارة: {e}")
        return None

def record_cigarette_time(employee_id):
    """تسجيل وقت السيجارة"""
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
        logger.info(f"تم تسجيل وقت السيجارة للموظف {employee_id} في {jordan_time}")
        return True
    except Exception as e:
        logger.error(f"خطأ في تسجيل وقت السيجارة: {e}")
        return False

def can_request_smoke():
    """التحقق إذا كان الوقت مناسب لطلب السيجارة (بعد الساعة 10 صباحاً)"""
    now = get_jordan_time()
    allowed_time = now.replace(hour=SMOKE_ALLOWED_AFTER_HOUR, minute=SMOKE_ALLOWED_AFTER_MINUTE, second=0, microsecond=0)
    return now >= allowed_time

# ==== دوال التقارير ====
def get_employee_attendance_report(employee_id, days=7):
    """الحصول على تقرير حضور الموظف لعدد معين من الأيام"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        end_date = get_jordan_time().date()
        start_date = end_date - timedelta(days=days-1)
        
        cur.execute("""
            SELECT date, check_in_time, check_out_time, is_late, late_minutes, 
                   total_work_minutes, overtime_minutes, status
            FROM attendance
            WHERE employee_id = %s AND date >= %s AND date <= %s
            ORDER BY date DESC
        """, (employee_id, start_date, end_date))
        
        records = cur.fetchall()
        cur.close()
        conn.close()
        
        return records
    except Exception as e:
        logger.error(f"خطأ في الحصول على تقرير حضور الموظف: {e}")
        return []

def get_daily_attendance_report(target_date=None):
    """الحصول على تقرير حضور جميع الموظفين لليوم المحدد"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        if target_date is None:
            target_date = get_jordan_time().date()
        
        cur.execute("""
            SELECT e.full_name, e.phone_number, a.check_in_time, a.check_out_time, 
                   a.is_late, a.late_minutes, a.total_work_minutes, a.overtime_minutes, a.status
            FROM employees e
            LEFT JOIN attendance a ON e.id = a.employee_id AND a.date = %s
            ORDER BY e.full_name
        """, (target_date,))
        
        records = cur.fetchall()
        cur.close()
        conn.close()
        
        return records
    except Exception as e:
        logger.error(f"خطأ في الحصول على التقرير اليومي: {e}")
        return []

def get_weekly_attendance_report():
    """الحصول على تقرير حضور جميع الموظفين للأسبوع الماضي"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        end_date = get_jordan_time().date()
        start_date = end_date - timedelta(days=6)
        
        cur.execute("""
            SELECT e.full_name, e.phone_number,
                   COUNT(CASE WHEN a.status = 'present' THEN 1 END) as present_days,
                   COUNT(CASE WHEN a.is_late = TRUE THEN 1 END) as late_days,
                   SUM(COALESCE(a.total_work_minutes, 0)) as total_minutes,
                   SUM(COALESCE(a.overtime_minutes, 0)) as total_overtime_minutes
            FROM employees e
            LEFT JOIN attendance a ON e.id = a.employee_id 
                AND a.date >= %s AND a.date <= %s
            GROUP BY e.id, e.full_name, e.phone_number
            ORDER BY e.full_name
        """, (start_date, end_date))
        
        records = cur.fetchall()
        cur.close()
        conn.close()
        
        return records
    except Exception as e:
        logger.error(f"خطأ في الحصول على التقرير الأسبوعي: {e}")
        return []

# ==== واجهة المدير الذكية ====
async def start_penalty_manager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء مدير العقوبات"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return ConversationHandler.END
    
    keyboard = [
        [InlineKeyboardButton("➕ إضافة عقوبة جديدة", callback_data="add_penalty")],
        [InlineKeyboardButton("✏️ تعديل/إزالة عقوبة", callback_data="edit_penalty")],
        [InlineKeyboardButton("📋 عرض عقوبات موظف", callback_data="view_employee_penalties")],
        [InlineKeyboardButton("📊 إحصائيات العقوبات", callback_data="penalty_stats")],
        [InlineKeyboardButton("⚙️ إعدادات العقوبات", callback_data="penalty_settings")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔧 **مدير العقوبات الذكي**\n\n"
        "اختر الإجراء الذي تريد تنفيذه:\n\n"
        "💡 **النصائح الذكية:**\n"
        "• سأقدم لك اقتراحات بناءً على سجل الموظف\n"
        "• سأحلل تكرار المخالفات\n"
        "• سأقترح عقوبات مناسبة\n"
        "• سأذكرك بالإجراءات السابقة",
        reply_markup=reply_markup
    )
    
    return PENALTY_MENU

async def handle_penalty_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيارات مدير العقوبات"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "add_penalty":
        await query.edit_message_text(
            "🔍 **البحث عن الموظف**\n\n"
            "أدخل اسم الموظف أو رقم هاتفه للبحث:\n\n"
            "💡 يمكنك البحث بـ:\n"
            "• الاسم الكامل أو جزء منه\n"
            "• رقم الهاتف الكامل أو جزئي\n"
            "• المعرف إذا كنت تعرفه"
        )
        return SELECT_EMPLOYEE_FOR_PENALTY
    
    elif data == "edit_penalty":
        await query.edit_message_text(
            "🔍 **البحث عن الموظف لتعديل العقوبة**\n\n"
            "أدخل اسم الموظف أو رقم هاتفه للبحث:\n\n"
            "💡 يمكنك البحث بـ:\n"
            "• الاسم الكامل أو جزء منه\n"
            "• رقم الهاتف الكامل أو جزئي"
        )
        context.user_data['penalty_action'] = 'edit_search'
        return SELECT_EMPLOYEE_FOR_PENALTY
    
    elif data == "view_employee_penalties":
        await query.edit_message_text(
            "🔍 **البحث عن موظف لعرض عقوباته**\n\n"
            "أدخل اسم الموظف أو رقم هاتفه للبحث:\n\n"
            "💡 يمكنك البحث بـ:\n"
            "• الاسم الكامل أو جزء منه\n"
            "• رقم الهاتف الكامل أو جزئي"
        )
        context.user_data['penalty_action'] = 'view'
        return SELECT_EMPLOYEE_FOR_PENALTY
    
    elif data == "penalty_settings":
        settings = get_penalty_settings()
        await show_penalty_settings(query, settings)
        return PENALTY_MENU
    
    elif data == "penalty_stats":
        stats = await get_penalty_statistics()
        await query.edit_message_text(stats, parse_mode='Markdown')
        return ConversationHandler.END
    
    elif data == "cancel":
        await query.edit_message_text("❌ تم إغلاق مدير العقوبات.")
        return ConversationHandler.END
    
    return PENALTY_MENU

async def select_employee_for_penalty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """البحث عن موظف لإضافة عقوبة"""
    if update.message:
        search_term = update.message.text
        user = update.message.from_user
        context.user_data['search_term'] = search_term
    else:
        query = update.callback_query
        await query.answer()
        search_term = context.user_data.get('search_term', '')
        user = query.from_user
    
    employees = search_employees_by_name_or_phone(search_term)
    
    if not employees:
        if update.message:
            await update.message.reply_text(
                f"❌ لم يتم العثور على موظفين يتطابقون مع: {search_term}\n\n"
                "يرجى المحاولة مرة أخرى بإدخال اسم أو رقم هاتف مختلف."
            )
        else:
            await query.edit_message_text(
                f"❌ لم يتم العثور على موظفين يتطابقون مع: {search_term}\n\n"
                "يرجى المحاولة مرة أخرى بإدخال اسم أو رقم هاتف مختلف."
            )
        return SELECT_EMPLOYEE_FOR_PENALTY
    
    # عرض قائمة الموظفين
    keyboard = []
    for emp in employees[:5]:  # عرض أول 5 نتائج فقط
        name = emp['full_name']
        phone = emp['phone_number']
        button_text = f"{name} ({phone})"
        callback_data = f"select_emp_{emp['id']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("🔍 بحث جديد", callback_data="new_search")])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(
            f"🔍 **نتائج البحث عن: {search_term}**\n\n"
            "اختر الموظف من القائمة:",
            reply_markup=reply_markup
        )
    else:
        await query.edit_message_text(
            f"🔍 **نتائج البحث عن: {search_term}**\n\n"
            "اختر الموظف من القائمة:",
            reply_markup=reply_markup
        )
    
    return SELECT_EMPLOYEE_FOR_PENALTY

async def select_employee_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختيار الموظف من القائمة"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    action = context.user_data.get('penalty_action', 'add')
    
    if data == "new_search":
        await query.edit_message_text(
            "🔍 **البحث عن الموظف**\n\n"
            "أدخل اسم الموظف أو رقم هاتفه للبحث:"
        )
        return SELECT_EMPLOYEE_FOR_PENALTY
    
    elif data == "cancel":
        await query.edit_message_text("❌ تم إلغاء العملية.")
        return ConversationHandler.END
    
    elif data.startswith("select_emp_"):
        employee_id = int(data.split("_")[2])
        employee = get_employee_by_id(employee_id)
        
        if not employee:
            await query.edit_message_text("❌ خطأ: الموظف غير موجود.")
            return ConversationHandler.END
        
        context.user_data['selected_employee'] = employee
        
        if action == 'add':
            # عرض أنواع المخالفات
            keyboard = []
            for penalty_type, info in PENALTY_TYPES.items():
                level_info = PENALTY_LEVELS.get(info['level'], {})
                button_text = f"{level_info.get('color', '⚫')} {info['name']}"
                callback_data = f"penalty_type_{penalty_type}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
            
            keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"👤 **الموظف المحدد:** {employee['full_name']}\n"
                f"📱 **الهاتف:** {employee['phone_number']}\n\n"
                "📋 **اختر نوع المخالفة:**\n\n"
                "💡 سأقدم لك اقتراحات ذكية بناءً على سجل الموظف بعد اختيار النوع.",
                reply_markup=reply_markup
            )
            
            return SELECT_PENALTY_TYPE
            
        elif action == 'edit_search':
            # عرض عقوبات الموظف للتعديل
            penalties = get_employee_penalties(employee['id'], active_only=False)
            
            if not penalties:
                await query.edit_message_text(
                    f"👤 **الموظف:** {employee['full_name']}\n"
                    f"📱 **الهاتف:** {employee['phone_number']}\n\n"
                    "📋 لا توجد عقوبات مسجلة لهذا الموظف.\n\n"
                    "🔙 الرجوع إلى القائمة الرئيسية."
                )
                return await start_penalty_manager(query.message, context)
            
            # عرض قائمة العقوبات
            keyboard = []
            for penalty in penalties[:10]:  # عرض أول 10 عقوبات
                status = "✅" if penalty['is_active'] else "❌"
                penalty_date = penalty['penalty_date'].strftime('%Y-%m-%d')
                button_text = f"{status} {penalty['penalty_name']} - {penalty_date}"
                callback_data = f"select_pen_{penalty['id']}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
            
            keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_menu")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"👤 **الموظف:** {employee['full_name']}\n"
                f"📱 **الهاتف:** {employee['phone_number']}\n\n"
                "📋 **اختر العقوبة لتعديلها:**\n\n"
                "✅ = عقوبة نشطة\n"
                "❌ = عقوبة معطلة",
                reply_markup=reply_markup
            )
            
            return SELECT_PENALTY_TO_EDIT
            
        elif action == 'view':
            # عرض عقوبات الموظف
            penalties = get_employee_penalties(employee['id'], active_only=False)
            
            if not penalties:
                message = (
                    f"📋 **عقوبات الموظف:** {employee['full_name']}\n"
                    f"📱 **الهاتف:** {employee['phone_number']}\n\n"
                    "✅ لا توجد عقوبات مسجلة لهذا الموظف."
                )
            else:
                message = (
                    f"📋 **عقوبات الموظف:** {employee['full_name']}\n"
                    f"📱 **الهاتف:** {employee['phone_number']}\n"
                    f"📅 **تاريخ التقرير:** {get_jordan_time().strftime('%Y-%m-%d')}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                )
                
                for i, penalty in enumerate(penalties, 1):
                    status = "✅ نشطة" if penalty['is_active'] else "❌ معطلة"
                    message += f"🔹 **العقوبة #{i}** ({status})\n"
                    message += f"   🆔 المعرف: {penalty['id']}\n"
                    message += f"   📛 النوع: {penalty.get('penalty_name', 'غير محدد')}\n"
                    message += f"   📅 التاريخ: {penalty.get('penalty_date').strftime('%Y-%m-%d')}\n"
                    message += f"   📝 السبب: {penalty.get('reason', 'غير محدد')}\n"
                    deduction = float(penalty.get('deduction_amount', 0))
                    if deduction > 0:
                        message += f"   💰 الخصم: {deduction:.2f} دينار\n"
                    ban_days = penalty.get('smoke_ban_days', 0)
                    if ban_days > 0:
                        message += f"   🚬 حظر سجائر: {ban_days} يوم\n"
                    message += "\n"
                
                summary = get_employee_penalty_summary(employee['id'])
                message += (
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 **ملخص العقوبات:**\n"
                    f"   ⚖️ إجمالي العقوبات: {summary['total_penalties']}\n"
                    f"   ✅ عقوبات نشطة: {summary['active_penalties']}\n"
                    f"   💰 إجمالي الخصومات: {summary['total_deduction']:.2f} دينار\n"
                )
            
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(message, reply_markup=reply_markup)
            return PENALTY_MENU
    
    return SELECT_EMPLOYEE_FOR_PENALTY

async def select_penalty_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختيار نوع المخالفة"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "cancel":
        await query.edit_message_text("❌ تم إلغاء العملية.")
        return ConversationHandler.END
    
    elif data.startswith("penalty_type_"):
        penalty_type = data.split("_")[2]
        
        if penalty_type not in PENALTY_TYPES:
            await query.edit_message_text("❌ خطأ: نوع المخالفة غير موجود.")
            return ConversationHandler.END
        
        penalty_info = PENALTY_TYPES[penalty_type]
        level_info = PENALTY_LEVELS.get(penalty_info['level'], {})
        
        # حفظ البيانات
        context.user_data['selected_penalty_type'] = penalty_type
        
        # الحصول على اقتراحات ذكية
        employee = context.user_data.get('selected_employee', {})
        suggestions = get_intelligent_penalty_suggestion(employee.get('id'), penalty_type)
        
        # عرض تفاصيل المخالفة مع اقتراحات
        message = (
            f"📋 **تفاصيل المخالفة:**\n\n"
            f"📝 **النوع:** {penalty_info['name']}\n"
            f"⚖️ **المستوى:** {penalty_info['level']} - {level_info.get('name', '')}\n"
            f"💰 **الخصم الافتراضي:** {penalty_info['default_amount']} دينار\n"
            f"🚬 **حظر السجائر:** {penalty_info['default_ban_days']} يوم\n\n"
        )
        
        if suggestions:
            message += "💡 **الاقتراحات الذكية:**\n"
            for suggestion in suggestions:
                message += f"• {suggestion}\n"
            message += "\n"
        
        message += (
            "📝 **الآن، أدخل سبب المخالفة:**\n\n"
            "💡 **نصائح لكتابة السبب:**\n"
            "• كن واضحاً ومحدداً\n"
            "• اذكر التاريخ والوقت إذا أمكن\n"
            "• اذكر الشهود إذا وجدوا\n"
            "• تجنب التعميم والغموض"
        )
        
        await query.edit_message_text(message)
        
        return ENTER_PENALTY_DETAILS

async def enter_penalty_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إدخال سبب المخالفة"""
    reason = update.message.text
    
    if len(reason) < 5:
        await update.message.reply_text(
            "❌ السبب قصير جداً. يرجى كتابة سبب مفصل (على الأقل 10 أحرف).\n"
            "أعد إدخال السبب:"
        )
        return ENTER_PENALTY_DETAILS
    
    context.user_data['penalty_reason'] = reason
    
    # الحصول على المعلومات المطلوبة
    employee = context.user_data.get('selected_employee', {})
    penalty_type = context.user_data.get('selected_penalty_type', '')
    
    if not employee or not penalty_type:
        await update.message.reply_text("❌ خطأ: بيانات غير كاملة.")
        return ConversationHandler.END
    
    penalty_info = PENALTY_TYPES[penalty_type]
    
    # عرض خيارات التخصيص
    keyboard = [
        [InlineKeyboardButton(f"✅ استخدام المبلغ الافتراضي ({penalty_info['default_amount']} دينار)", 
                             callback_data="use_default_amount")],
        [InlineKeyboardButton("✏️ تحديد مبلغ مخصص", callback_data="custom_amount")],
        [InlineKeyboardButton(f"🚫 استخدام حظر الافتراضي ({penalty_info['default_ban_days']} يوم)", 
                             callback_data="use_default_ban")],
        [InlineKeyboardButton("✏️ تحديد حظر مخصص", callback_data="custom_ban")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ **تم حفظ السبب:**\n{reason}\n\n"
        f"👤 **الموظف:** {employee['full_name']}\n"
        f"📝 **المخالفة:** {penalty_info['name']}\n\n"
        "🔧 **خيارات التخصيص:**\n"
        "يمكنك تعديل قيمة الخصم وأيام الحظر:",
        reply_markup=reply_markup
    )
    
    return CONFIRM_PENALTY

async def handle_penalty_customization(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة تخصيص العقوبة"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "use_default_amount":
        context.user_data['penalty_amount'] = None  # استخدام الافتراضي
        await query.edit_message_text(
            "✅ سيتم استخدام المبلغ الافتراضي.\n\n"
            "الآن اختر خيار حظر السجائر:"
        )
        # إعادة عرض خيارات الحظر فقط
        penalty_type = context.user_data.get('selected_penalty_type', '')
        penalty_info = PENALTY_TYPES.get(penalty_type, {})
        
        keyboard = [
            [InlineKeyboardButton(f"🚫 استخدام حظر الافتراضي ({penalty_info.get('default_ban_days', 0)} يوم)", 
                                 callback_data="use_default_ban")],
            [InlineKeyboardButton("✏️ تحديد حظر مخصص", callback_data="custom_ban")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text("اختر خيار حظر السجائر:", reply_markup=reply_markup)
        return CONFIRM_PENALTY
        
    elif data == "custom_amount":
        await query.edit_message_text(
            "💰 **تحديد المبلغ المخصص**\n\n"
            "أدخل المبلغ الذي تريد خصمه (بالدينار):\n\n"
            "💡 **نصائح:**\n"
            "• اكتب الرقم فقط (مثال: 25)\n"
            "• يمكن أن يكون الرقم عشري (مثال: 12.5)\n"
            "• الحد الأدنى: 0 دينار\n"
            "• الحد الأقصى: 500 دينار"
        )
        context.user_data['awaiting_input'] = 'amount'
        return EDIT_PENALTY_CUSTOM_AMOUNT
    
    elif data == "use_default_ban":
        context.user_data['penalty_ban_days'] = None  # استخدام الافتراضي
        await show_penalty_summary(query, context)
        return CONFIRM_PENALTY
    
    elif data == "custom_ban":
        await query.edit_message_text(
            "🚫 **تحديد أيام الحظر المخصصة**\n\n"
            "أدخل عدد أيام حظر السجائر:\n\n"
            "💡 **نصائح:**\n"
            "• اكتب الرقم فقط (مثال: 7)\n"
            "• 0 يعني لا يوجد حظر\n"
            "• الحد الأقصى: 30 يوم"
        )
        context.user_data['awaiting_input'] = 'ban_days'
        return EDIT_PENALTY_CUSTOM_AMOUNT

async def edit_penalty_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدخال المبلغ أو الحظر المخصص"""
    user_input = update.message.text
    awaiting_input = context.user_data.get('awaiting_input')
    
    if awaiting_input == 'amount':
        try:
            amount = float(user_input)
            if amount < 0 or amount > 500:
                await update.message.reply_text(
                    "❌ المبلغ يجب أن يكون بين 0 و 500 دينار.\n"
                    "أعد إدخال المبلغ:"
                )
                return EDIT_PENALTY_CUSTOM_AMOUNT
            
            context.user_data['penalty_amount'] = amount
            
            # الانتقال لخيارات الحظر
            penalty_type = context.user_data.get('selected_penalty_type', '')
            penalty_info = PENALTY_TYPES.get(penalty_type, {})
            
            keyboard = [
                [InlineKeyboardButton(f"🚫 استخدام حظر الافتراضي ({penalty_info.get('default_ban_days', 0)} يوم)", 
                                     callback_data="use_default_ban")],
                [InlineKeyboardButton("✏️ تحديد حظر مخصص", callback_data="custom_ban")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✅ تم تعيين المبلغ إلى {amount} دينار.\n\n"
                "الآن اختر خيار حظر السجائر:",
                reply_markup=reply_markup
            )
            
        except ValueError:
            await update.message.reply_text(
                "❌ إدخال غير صالح. يرجى إدخال رقم صحيح أو عشري.\n"
                "أعد إدخال المبلغ:"
            )
            return EDIT_PENALTY_CUSTOM_AMOUNT
    
    elif awaiting_input == 'ban_days':
        try:
            ban_days = int(user_input)
            if ban_days < 0 or ban_days > 30:
                await update.message.reply_text(
                    "❌ أيام الحظر يجب أن تكون بين 0 و 30 يوم.\n"
                    "أعد إدخال عدد الأيام:"
                )
                return EDIT_PENALTY_CUSTOM_AMOUNT
            
            context.user_data['penalty_ban_days'] = ban_days
            await show_penalty_summary(update, context)
            
        except ValueError:
            await update.message.reply_text(
                "❌ إدخال غير صالح. يرجى إدخال رقم صحيح.\n"
                "أعد إدخال عدد الأيام:"
            )
            return EDIT_PENALTY_CUSTOM_AMOUNT
    
    return CONFIRM_PENALTY

async def show_penalty_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض ملخص العقوبة للتأكيد"""
    employee = context.user_data.get('selected_employee', {})
    penalty_type = context.user_data.get('selected_penalty_type', '')
    reason = context.user_data.get('penalty_reason', '')
    
    penalty_info = PENALTY_TYPES.get(penalty_type, {})
    level_info = PENALTY_LEVELS.get(penalty_info.get('level', 1), {})
    
    # الحصول على القيم المخصصة أو الافتراضية
    amount = context.user_data.get('penalty_amount')
    if amount is None:
        amount = penalty_info.get('default_amount', 0)
    
    ban_days = context.user_data.get('penalty_ban_days')
    if ban_days is None:
        ban_days = penalty_info.get('default_ban_days', 0)
    
    summary = (
        f"📋 **ملخص العقوبة**\n\n"
        f"👤 **الموظف:** {employee.get('full_name', 'غير معروف')}\n"
        f"📱 **الهاتف:** {employee.get('phone_number', 'غير معروف')}\n\n"
        f"⚖️ **المخالفة:** {penalty_info.get('name', 'غير معروف')}\n"
        f"🎯 **المستوى:** {level_info.get('name', '')}\n"
        f"💰 **المبلغ:** {amount} دينار\n"
        f"🚬 **حظر السجائر:** {ban_days} يوم\n\n"
        f"📝 **السبب:**\n{reason}\n\n"
        f"🔍 **التحليل الذكي:**\n"
    )
    
    # إضافة تحليل ذكي
    employee_id = employee.get('id')
    if employee_id:
        penalty_summary = get_employee_penalty_summary(employee_id)
        
        if penalty_summary['total_penalties'] > 0:
            summary += f"• عدد العقوبات السابقة: {penalty_summary['total_penalties']}\n"
            summary += f"• العقوبات النشطة: {penalty_summary['active_penalties']}\n"
            summary += f"• إجمالي الخصومات: {penalty_summary['total_deduction']:.2f} دينار\n"
            
            if 'analysis' in penalty_summary:
                summary += f"• التحليل: {penalty_summary['analysis']}\n"
        
        # الحصول على اقتراحات
        suggestions = get_intelligent_penalty_suggestion(employee_id, penalty_type)
        if suggestions:
            summary += "\n💡 **الاقتراحات:**\n"
            for suggestion in suggestions[:3]:  # عرض أول 3 اقتراحات فقط
                summary += f"• {suggestion}\n"
    
    keyboard = [
        [InlineKeyboardButton("✅ تأكيد وإضافة العقوبة", callback_data="confirm_penalty")],
        [InlineKeyboardButton("✏️ تعديل السبب", callback_data="edit_reason")],
        [InlineKeyboardButton("💰 تعديل المبلغ", callback_data="edit_amount")],
        [InlineKeyboardButton("🚫 تعديل الحظر", callback_data="edit_ban")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_penalty")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if hasattr(update, 'message'):
        await update.message.reply_text(summary, reply_markup=reply_markup)
    else:
        query = update.callback_query
        await query.edit_message_text(summary, reply_markup=reply_markup)

async def confirm_penalty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تأكيد وإضافة العقوبة"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "confirm_penalty":
        # جمع البيانات
        employee = context.user_data.get('selected_employee', {})
        penalty_type = context.user_data.get('selected_penalty_type', '')
        reason = context.user_data.get('penalty_reason', '')
        
        penalty_info = PENALTY_TYPES.get(penalty_type, {})
        
        # الحصول على القيم المخصصة أو الافتراضية
        amount = context.user_data.get('penalty_amount')
        if amount is None:
            amount = penalty_info.get('default_amount', 0)
        
        ban_days = context.user_data.get('penalty_ban_days')
        if ban_days is None:
            ban_days = penalty_info.get('default_ban_days', 0)
        
        # إضافة العقوبة
        result = add_penalty(
            employee_id=employee.get('id'),
            penalty_type=penalty_type,
            reason=reason,
            created_by=query.from_user.id,
            custom_amount=amount,
            custom_ban_days=ban_days
        )
        
        if result['success']:
            # إشعار الموظف إذا كان مسجلاً في تيليجرام
            telegram_id = employee.get('telegram_id')
            if telegram_id and PENALTY_SETTINGS['notify_employee_on_penalty']:
                try:
                    await context.bot.send_message(
                        chat_id=telegram_id,
                        text=f"⚠️ **تم إضافة عقوبة جديدة لك**\n\n"
                             f"📝 المخالفة: {penalty_info.get('name', 'غير معروف')}\n"
                             f"💰 المبلغ: {amount} دينار\n"
                             f"🚬 حظر السجائر: {ban_days} يوم\n"
                             f"📋 السبب: {reason}\n\n"
                             f"⏰ التاريخ: {get_jordan_time().strftime('%Y-%m-%d %H:%M')}\n"
                             f"👤 المدير: {query.from_user.first_name}"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify employee about penalty: {e}")
            
            await query.edit_message_text(
                f"✅ **تم إضافة العقوبة بنجاح!**\n\n"
                f"🆔 معرف العقوبة: {result['penalty_id']}\n"
                f"👤 الموظف: {employee.get('full_name', 'غير معروف')}\n"
                f"📝 المخالفة: {penalty_info.get('name', 'غير معروف')}\n"
                f"💰 المبلغ: {amount} دينار\n"
                f"🚬 حظر السجائر: {ban_days} يوم\n\n"
                f"📋 تم الإرسال للموظف: {'✅' if telegram_id else '❌'}\n"
                f"👤 المدير: {query.from_user.first_name}"
            )
        else:
            await query.edit_message_text(
                f"❌ **خطأ في إضافة العقوبة:**\n{result.get('error', 'خطأ غير معروف')}"
            )
        
        # تنظيف البيانات المؤقتة
        context.user_data.clear()
        return ConversationHandler.END
    
    elif data == "edit_reason":
        await query.edit_message_text(
            "📝 **تعديل السبب**\n\n"
            "أدخل السبب الجديد للمخالفة:"
        )
        return ENTER_PENALTY_DETAILS
    
    elif data == "edit_amount":
        await query.edit_message_text(
            "💰 **تعديل المبلغ**\n\n"
            "أدخل المبلغ الجديد (بالدينار):"
        )
        context.user_data['awaiting_input'] = 'amount'
        return EDIT_PENALTY_CUSTOM_AMOUNT
    
    elif data == "edit_ban":
        await query.edit_message_text(
            "🚫 **تعديل الحظر**\n\n"
            "أدخل عدد أيام حظر السجائر الجديد:"
        )
        context.user_data['awaiting_input'] = 'ban_days'
        return EDIT_PENALTY_CUSTOM_AMOUNT
    
    elif data == "cancel_penalty":
        await query.edit_message_text("❌ تم إلغاء إضافة العقوبة.")
        context.user_data.clear()
        return ConversationHandler.END

async def select_penalty_to_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختيار العقوبة للتعديل"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "back_to_menu":
        await start_penalty_manager(query.message, context)
        return PENALTY_MENU
    
    elif data.startswith("select_pen_"):
        penalty_id = int(data.split("_")[2])
        penalty = get_penalty_details(penalty_id)
        
        if not penalty:
            await query.edit_message_text("❌ خطأ: العقوبة غير موجودة.")
            return ConversationHandler.END
        
        context.user_data['selected_penalty'] = penalty
        
        # عرض خيارات التعديل
        keyboard = [
            [InlineKeyboardButton("💰 تعديل المبلغ", callback_data="edit_penalty_amount")],
            [InlineKeyboardButton("🚫 تعديل حظر السجائر", callback_data="edit_penalty_ban_days")],
            [InlineKeyboardButton("📝 تعديل السبب", callback_data="edit_penalty_reason")],
            [InlineKeyboardButton("✅ تفعيل العقوبة", callback_data="activate_penalty")],
            [InlineKeyboardButton("❌ تعطيل العقوبة", callback_data="deactivate_penalty")],
            [InlineKeyboardButton("🗑️ حذف العقوبة", callback_data="delete_penalty")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_employee")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        status = "✅ نشطة" if penalty['is_active'] else "❌ معطلة"
        
        await query.edit_message_text(
            f"✏️ **تعديل العقوبة**\n\n"
            f"🆔 المعرف: {penalty['id']}\n"
            f"👤 الموظف: {penalty['full_name']}\n"
            f"📝 المخالفة: {penalty['penalty_name']}\n"
            f"💰 المبلغ الحالي: {penalty['deduction_amount']} دينار\n"
            f"🚬 حظر السجائر: {penalty['smoke_ban_days']} يوم\n"
            f"📅 التاريخ: {penalty['penalty_date'].strftime('%Y-%m-%d')}\n"
            f"📊 الحالة: {status}\n\n"
            f"📝 **السبب:**\n{penalty['reason']}\n\n"
            f"اختر الإجراء الذي تريد تنفيذه:",
            reply_markup=reply_markup
        )
        
        return EDIT_PENALTY_AMOUNT

async def handle_penalty_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة تعديل العقوبة"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    penalty = context.user_data.get('selected_penalty', {})
    
    if data == "back_to_employee":
        # الرجوع إلى قائمة عقوبات الموظف
        employee = {'id': penalty['employee_id']}
        context.user_data['selected_employee'] = employee
        context.user_data['penalty_action'] = 'edit_search'
        
        penalties = get_employee_penalties(employee['id'], active_only=False)
        
        if not penalties:
            await query.edit_message_text(
                f"👤 **الموظف:** {penalty['full_name']}\n"
                f"📱 **الهاتف:** {penalty['phone_number']}\n\n"
                "📋 لا توجد عقوبات مسجلة لهذا الموظف.\n\n"
                "🔙 الرجوع إلى القائمة الرئيسية."
            )
            return await start_penalty_manager(query.message, context)
        
        # عرض قائمة العقوبات
        keyboard = []
        for pen in penalties[:10]:  # عرض أول 10 عقوبات
            status = "✅" if pen['is_active'] else "❌"
            penalty_date = pen['penalty_date'].strftime('%Y-%m-%d')
            button_text = f"{status} {pen['penalty_name']} - {penalty_date}"
            callback_data = f"select_pen_{pen['id']}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"👤 **الموظف:** {penalty['full_name']}\n"
            f"📱 **الهاتف:** {penalty['phone_number']}\n\n"
            "📋 **اختر العقوبة لتعديلها:**\n\n"
            "✅ = عقوبة نشطة\n"
            "❌ = عقوبة معطلة",
            reply_markup=reply_markup
        )
        
        return SELECT_PENALTY_TO_EDIT
    
    elif data == "edit_penalty_amount":
        await query.edit_message_text(
            f"💰 **تعديل مبلغ العقوبة**\n\n"
            f"المبلغ الحالي: {penalty['deduction_amount']} دينار\n\n"
            f"أدخل المبلغ الجديد (بالدينار):\n\n"
            f"💡 **نصائح:**\n"
            f"• المبلغ الحالي معقول: {'✅ نعم' if float(penalty['deduction_amount'] or 0) <= 50 else '⚠️ مرتفع'}\n"
            f"• متوسط عقوبات الموظف: {get_employee_penalty_summary(penalty['employee_id']).get('total_deduction', 0):.2f} دينار"
        )
        context.user_data['edit_field'] = 'amount'
        return EDIT_PENALTY_AMOUNT
    
    elif data == "edit_penalty_ban_days":
        await query.edit_message_text(
            f"🚫 **تعديل حظر السجائر**\n\n"
            f"الحظر الحالي: {penalty['smoke_ban_days']} يوم\n\n"
            f"أدخل عدد أيام الحظر الجديد (0-30):"
        )
        context.user_data['edit_field'] = 'ban_days'
        return EDIT_PENALTY_AMOUNT
    
    elif data == "edit_penalty_reason":
        await query.edit_message_text(
            f"📝 **تعديل سبب العقوبة**\n\n"
            f"السبب الحالي:\n{penalty['reason']}\n\n"
            f"أدخل السبب الجديد:"
        )
        context.user_data['edit_field'] = 'reason'
        return EDIT_PENALTY_AMOUNT
    
    elif data == "activate_penalty":
        result = update_penalty(penalty['id'], {'is_active': True})
        if result['success']:
            await query.edit_message_text(f"✅ تم تفعيل العقوبة {penalty['id']} بنجاح!")
        else:
            await query.edit_message_text(f"❌ خطأ: {result.get('error', 'خطأ غير معروف')}")
        return ConversationHandler.END
    
    elif data == "deactivate_penalty":
        result = update_penalty(penalty['id'], {'is_active': False, 'resolved_by': query.from_user.id})
        if result['success']:
            await query.edit_message_text(f"✅ تم تعطيل العقوبة {penalty['id']} بنجاح!")
        else:
            await query.edit_message_text(f"❌ خطأ: {result.get('error', 'خطأ غير معروف')}")
        return ConversationHandler.END
    
    elif data == "delete_penalty":
        keyboard = [
            [InlineKeyboardButton("✅ نعم، حذف العقوبة", callback_data="confirm_delete")],
            [InlineKeyboardButton("❌ لا، إلغاء", callback_data="cancel_delete")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🗑️ **تأكيد حذف العقوبة**\n\n"
            f"🆔 المعرف: {penalty['id']}\n"
            f"👤 الموظف: {penalty['full_name']}\n"
            f"📝 المخالفة: {penalty['penalty_name']}\n"
            f"💰 المبلغ: {penalty['deduction_amount']} دينار\n"
            f"📅 التاريخ: {penalty['penalty_date'].strftime('%Y-%m-%d')}\n\n"
            f"⚠️ **هل أنت متأكد من حذف هذه العقوبة؟**\n\n"
            f"💡 **ملاحظة:** الحذف نهائي ولا يمكن استرجاع البيانات.",
            reply_markup=reply_markup
        )
        return EDIT_PENALTY_AMOUNT
    
    elif data == "confirm_delete":
        # حذف العقوبة من قاعدة البيانات
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM penalties WHERE id = %s", (penalty['id'],))
            conn.commit()
            cur.close()
            conn.close()
            
            await query.edit_message_text(
                f"✅ **تم حذف العقوبة بنجاح!**\n\n"
                f"🆔 المعرف: {penalty['id']}\n"
                f"👤 الموظف: {penalty['full_name']}\n"
                f"📝 المخالفة: {penalty['penalty_name']}\n\n"
                f"👤 تم الحذف بواسطة: {query.from_user.first_name}"
            )
            
            # إشعار الموظف
            if penalty['telegram_id'] and PENALTY_SETTINGS['notify_employee_on_penalty']:
                try:
                    await context.bot.send_message(
                        chat_id=penalty['telegram_id'],
                        text=f"✅ **تم حذف عقوبة**\n\n"
                             f"🆔 المعرف: {penalty['id']}\n"
                             f"📝 المخالفة: {penalty['penalty_name']}\n"
                             f"💰 المبلغ: {penalty['deduction_amount']} دينار\n\n"
                             f"👤 تم الحذف بواسطة: {query.from_user.first_name}"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify employee about penalty deletion: {e}")
        except Exception as e:
            await query.edit_message_text(f"❌ خطأ في حذف العقوبة: {str(e)}")
        
        context.user_data.clear()
        return ConversationHandler.END
    
    elif data == "cancel_delete":
        # إعادة عرض خيارات التعديل
        keyboard = [
            [InlineKeyboardButton("💰 تعديل المبلغ", callback_data="edit_penalty_amount")],
            [InlineKeyboardButton("🚫 تعديل حظر السجائر", callback_data="edit_penalty_ban_days")],
            [InlineKeyboardButton("📝 تعديل السبب", callback_data="edit_penalty_reason")],
            [InlineKeyboardButton("✅ تفعيل العقوبة", callback_data="activate_penalty")],
            [InlineKeyboardButton("❌ تعطيل العقوبة", callback_data="deactivate_penalty")],
            [InlineKeyboardButton("🗑️ حذف العقوبة", callback_data="delete_penalty")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_employee")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        status = "✅ نشطة" if penalty['is_active'] else "❌ معطلة"
        
        await query.edit_message_text(
            f"✏️ **تعديل العقوبة**\n\n"
            f"🆔 المعرف: {penalty['id']}\n"
            f"👤 الموظف: {penalty['full_name']}\n"
            f"📝 المخالفة: {penalty['penalty_name']}\n"
            f"💰 المبلغ الحالي: {penalty['deduction_amount']} دينار\n"
            f"🚬 حظر السجائر: {penalty['smoke_ban_days']} يوم\n"
            f"📅 التاريخ: {penalty['penalty_date'].strftime('%Y-%m-%d')}\n"
            f"📊 الحالة: {status}\n\n"
            f"📝 **السبب:**\n{penalty['reason']}\n\n"
            f"اختر الإجراء الذي تريد تنفيذه:",
            reply_markup=reply_markup
        )
        
        return EDIT_PENALTY_AMOUNT

async def process_penalty_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة التعديلات على العقوبة"""
    user_input = update.message.text
    edit_field = context.user_data.get('edit_field')
    penalty = context.user_data.get('selected_penalty', {})
    
    updates = {}
    
    if edit_field == 'amount':
        try:
            amount = float(user_input)
            if amount < 0 or amount > 1000:
                await update.message.reply_text("❌ المبلغ يجب أن يكون بين 0 و 1000 دينار. أعد الإدخال:")
                return EDIT_PENALTY_AMOUNT
            updates['deduction_amount'] = amount
            
            # تقديم اقتراح ذكي
            old_amount = float(penalty['deduction_amount'] or 0)
            if amount > old_amount * 1.5:
                await update.message.reply_text(
                    f"⚠️ **تنبيه:** أنت تزيد المبلغ بنسبة {((amount/old_amount)-1)*100:.0f}%\n"
                    f"💡 هل أنت متأكد من هذا التغيير الكبير؟\n"
                    f"اضغط /confirm لتأكيد أو أدخل مبلغاً جديداً."
                )
                context.user_data['pending_update'] = updates
                return EDIT_PENALTY_AMOUNT
                
        except ValueError:
            await update.message.reply_text("❌ إدخال غير صالح. يرجى إدخال رقم. أعد الإدخال:")
            return EDIT_PENALTY_AMOUNT
    
    elif edit_field == 'ban_days':
        try:
            ban_days = int(user_input)
            if ban_days < 0 or ban_days > 30:
                await update.message.reply_text("❌ أيام الحظر يجب أن تكون بين 0 و 30 يوم. أعد الإدخال:")
                return EDIT_PENALTY_AMOUNT
            updates['smoke_ban_days'] = ban_days
        except ValueError:
            await update.message.reply_text("❌ إدخال غير صالح. يرجى إدخال رقم صحيح. أعد الإدخال:")
            return EDIT_PENALTY_AMOUNT
    
    elif edit_field == 'reason':
        if len(user_input) < 10:
            await update.message.reply_text("❌ السبب قصير جداً. يرجى كتابة سبب مفصل (10 أحرف على الأقل). أعد الإدخال:")
            return EDIT_PENALTY_AMOUNT
        updates['reason'] = user_input
    
    # تطبيق التحديثات
    if updates:
        result = update_penalty(penalty['id'], updates)
        if result['success']:
            message = f"✅ **تم تعديل العقوبة بنجاح!**\n\n"
            message += f"🆔 المعرف: {penalty['id']}\n"
            message += f"👤 الموظف: {penalty['full_name']}\n"
            message += f"📝 التعديلات:\n"
            
            for field, value in updates.items():
                field_name = {
                    'deduction_amount': 'المبلغ',
                    'smoke_ban_days': 'حظر السجائر',
                    'reason': 'السبب'
                }.get(field, field)
                
                old_value = penalty.get(field, '')
                message += f"• {field_name}: {old_value} → {value}\n"
            
            message += f"\n👤 تم التعديل بواسطة: {update.message.from_user.first_name}"
            
            await update.message.reply_text(message)
            
            # إشعار الموظف
            if penalty['telegram_id'] and PENALTY_SETTINGS['notify_employee_on_penalty']:
                try:
                    await context.bot.send_message(
                        chat_id=penalty['telegram_id'],
                        text=f"✏️ **تم تعديل عقوبة**\n\n"
                             f"🆔 المعرف: {penalty['id']}\n"
                             f"📝 المخالفة: {penalty['penalty_name']}\n"
                             f"📋 التعديلات:\n"
                    )
                    for field, value in updates.items():
                        field_name = {
                            'deduction_amount': 'المبلغ',
                            'smoke_ban_days': 'حظر السجائر',
                            'reason': 'السبب'
                        }.get(field, field)
                        old_value = penalty.get(field, '')
                        await context.bot.send_message(
                            chat_id=penalty['telegram_id'],
                            text=f"• {field_name}: {old_value} → {value}"
                        )
                except Exception as e:
                    logger.error(f"Failed to notify employee about penalty update: {e}")
        else:
            await update.message.reply_text(f"❌ خطأ في التعديل: {result.get('error', 'خطأ غير معروف')}")
    
    context.user_data.clear()
    return ConversationHandler.END

async def show_penalty_settings(query, settings):
    """عرض إعدادات العقوبات"""
    keyboard = []
    
    for key, value in settings.items():
        emoji = "✅" if value else "❌" if isinstance(value, bool) else "⚙️"
        button_text = f"{emoji} {key.replace('_', ' ').title()}: {value}"
        callback_data = f"toggle_{key}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "⚙️ **إعدادات نظام العقوبات**\n\n"
        "قم بتعديل الإعدادات حسب الحاجة:\n\n"
        "💡 **تفسير الإعدادات:**\n"
        "• التطبيق التلقائي: تطبيق العقوبات تلقائياً عند المخالفة\n"
        "• إشعار الموظف: إرسال إشعار للموظف عند إضافة عقوبة\n"
        "• السماح بالاستئناف: السماح للموظف باستئناف العقوبة\n"
        "• الحد الأقصى: أقصى عدد عقوبات شهرياً\n"
        "• فترة السماح: أيام السماح قبل تطبيق العقوبة",
        reply_markup=reply_markup
    )

async def toggle_penalty_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تبديل إعداد العقوبة"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "back_to_menu":
        await start_penalty_manager(query.message, context)
        return PENALTY_MENU
    
    elif data.startswith("toggle_"):
        setting_key = data[7:]  # إزالة "toggle_"
        settings = get_penalty_settings()
        
        if setting_key in settings:
            current_value = settings[setting_key]
            
            if isinstance(current_value, bool):
                new_value = not current_value
            elif isinstance(current_value, int):
                # طلب قيمة جديدة للأرقام
                await query.edit_message_text(
                    f"✏️ **تعديل الإعداد: {setting_key.replace('_', ' ').title()}**\n\n"
                    f"القيمة الحالية: {current_value}\n\n"
                    f"أدخل القيمة الجديدة:"
                )
                context.user_data['editing_setting'] = setting_key
                return PENALTY_MENU
            else:
                await query.edit_message_text("❌ لا يمكن تعديل هذا الإعداد تلقائياً.")
                return PENALTY_MENU
            
            # تحديث الإعداد
            update_penalty_setting(setting_key, new_value, query.from_user.id)
            
            # إعادة عرض الإعدادات
            settings = get_penalty_settings()
            await show_penalty_settings(query, settings)
    
    return PENALTY_MENU

async def get_all_active_penalties():
    """الحصول على جميع العقوبات النشطة"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT COUNT(*) as total, 
                   SUM(deduction_amount) as total_amount
            FROM penalties 
            WHERE is_active = TRUE
        """)
        
        stats = cur.fetchone()
        
        cur.execute("""
            SELECT p.id, e.full_name, p.penalty_name, 
                   p.deduction_amount, p.penalty_date
            FROM penalties p
            JOIN employees e ON p.employee_id = e.id
            WHERE p.is_active = TRUE
            ORDER BY p.penalty_date DESC
            LIMIT 20
        """)
        
        penalties = cur.fetchall()
        cur.close()
        conn.close()
        
        message = f"📋 **جميع العقوبات النشطة**\n\n"
        message += f"📊 **الإحصائيات:**\n"
        message += f"• عدد العقوبات: {stats[0] or 0}\n"
        message += f"• إجمالي المبالغ: {float(stats[1] or 0):.2f} دينار\n\n"
        message += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        if penalties:
            for penalty in penalties:
                message += (
                    f"🆔 **{penalty[0]}** - {penalty[1]}\n"
                    f"📝 {penalty[2]}\n"
                    f"💰 {penalty[3]} دينار\n"
                    f"📅 {penalty[4].strftime('%Y-%m-%d')}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                )
        else:
            message += "✅ لا توجد عقوبات نشطة حالياً.\n"
        
        message += "🔧 **للتعديل أو الإزالة، استخدم:**\n"
        message += "`/penalty_manager` ثم اختر 'تعديل/إزالة عقوبة'"
        
        return message
    except Exception as e:
        logger.error(f"Error getting all penalties: {e}")
        return "❌ حدث خطأ في جلب العقوبات."

async def get_penalty_statistics():
    """الحصول على إحصائيات العقوبات"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # إحصائيات عامة
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN is_active = TRUE THEN 1 END) as active,
                SUM(CASE WHEN is_active = TRUE THEN deduction_amount ELSE 0 END) as total_amount,
                AVG(CASE WHEN is_active = TRUE THEN deduction_amount ELSE 0 END) as avg_amount,
                MAX(penalty_date) as last_penalty
            FROM penalties
        """)
        
        general_stats = cur.fetchone()
        
        # أكثر أنواع المخالفات تكراراً
        cur.execute("""
            SELECT penalty_name, COUNT(*) as count
            FROM penalties
            WHERE is_active = TRUE
            GROUP BY penalty_name
            ORDER BY count DESC
            LIMIT 5
        """)
        
        top_penalties = cur.fetchall()
        
        # العقوبات حسب الشهر
        today = get_jordan_time().date()
        first_day_month = today.replace(day=1)
        
        cur.execute("""
            SELECT 
                COUNT(*) as monthly_count,
                SUM(deduction_amount) as monthly_amount
            FROM penalties
            WHERE penalty_date >= %s AND is_active = TRUE
        """, (first_day_month,))
        
        monthly_stats = cur.fetchone()
        
        cur.close()
        conn.close()
        
        message = "📊 **إحصائيات نظام العقوبات**\n\n"
        message += f"📅 التاريخ: {today.strftime('%Y-%m-%d')}\n"
        message += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        message += "🔹 **الإحصائيات العامة:**\n"
        message += f"• إجمالي العقوبات: {general_stats[0] or 0}\n"
        message += f"• العقوبات النشطة: {general_stats[1] or 0}\n"
        message += f"• إجمالي المبالغ: {float(general_stats[2] or 0):.2f} دينار\n"
        message += f"• متوسط العقوبة: {float(general_stats[3] or 0):.2f} دينار\n"
        
        if general_stats[4]:
            days_since = (today - general_stats[4]).days
            message += f"• آخر عقوبة: منذ {days_since} يوم\n"
        
        message += "\n🔹 **هذا الشهر:**\n"
        message += f"• عدد العقوبات: {monthly_stats[0] or 0}\n"
        message += f"• إجمالي المبالغ: {float(monthly_stats[1] or 0):.2f} دينار\n"
        
        if top_penalties:
            message += "\n🔹 **أكثر المخالفات تكراراً:**\n"
            for penalty in top_penalties:
                message += f"• {penalty[0]}: {penalty[1]} مرة\n"
        
        message += "\n💡 **تحليل ذكي:**\n"
        if monthly_stats[0] and monthly_stats[0] > 10:
            message += "⚠️ عدد العقوبات هذا الشهر مرتفع، قد يحتاج النظام للمراجعة.\n"
        elif monthly_stats[0] and monthly_stats[0] < 3:
            message += "✅ عدد العقوبات معتدل، النظام يعمل بشكل جيد.\n"
        
        avg_amount = float(general_stats[3] or 0)
        if avg_amount > 50:
            message += "💰 متوسط العقوبات مرتفع، قد يحتاج للتخفيف.\n"
        elif avg_amount < 10:
            message += "💸 متوسط العقوبات منخفض، قد يكون النظام متساهلاً.\n"
        
        return message
    except Exception as e:
        logger.error(f"Error getting penalty statistics: {e}")
        return "❌ حدث خطأ في جلب الإحصائيات."

# ==== الدوال الرئيسية للبوت ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء المحادثة مع البوت"""
    user = update.message.from_user
    logger.info(f"المستخدم {user.id} بدأ المحادثة.")
    
    # التحقق إذا كان المستخدم مسجلاً مسبقاً
    employee = get_employee_by_telegram_id(user.id)
    if employee:
        # المستخدم مسجل مسبقاً - عرض القائمة الرئيسية
        employee_name = employee.get('full_name', get_employee_name_from_db(user))
        
        if is_admin(user.id):
            keyboard = [
                [KeyboardButton("تسجيل حضور 📝"), KeyboardButton("تسجيل انصراف 🚪")],
                [KeyboardButton("طلب سيجارة 🚬"), KeyboardButton("طلب استراحة ☕")],
                [KeyboardButton("طلب إذن خروج 🏠"), KeyboardButton("طلب إجازة 🌴")],
                [KeyboardButton("تقرير الحضور 📊"), KeyboardButton("تقريري الكامل 📈")],
                [KeyboardButton("🔧 مدير العقوبات")]
            ]
        else:
            keyboard = [
                [KeyboardButton("تسجيل حضور 📝"), KeyboardButton("تسجيل انصراف 🚪")],
                [KeyboardButton("طلب سيجارة 🚬"), KeyboardButton("طلب استراحة ☕")],
                [KeyboardButton("طلب إذن خروج 🏠"), KeyboardButton("طلب إجازة 🌴")],
                [KeyboardButton("تقرير الحضور 📊"), KeyboardButton("تقريري الكامل 📈")]
            ]
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"👋 أهلاً بعودتك {employee_name}!\n\n"
            "اختر من الخيارات أدناه:",
            reply_markup=reply_markup
        )
        return
    
    # المستخدم جديد - طلب معلومات الاتصال
    contact_button = KeyboardButton("📱 مشاركة رقم الهاتف", request_contact=True)
    reply_markup = ReplyKeyboardMarkup([[contact_button]], resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "👋 مرحباً بك في نظام إدارة حضور الموظفين!\n\n"
        "📱 للمتابعة، يرجى مشاركة رقم هاتفك للتحقق من هويتك:\n\n"
        "اضغط على الزر أدناه لمشاركة رقم هاتفك.",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض رسالة المساعدة"""
    user = update.message.from_user
    
    help_text = """
🤖 **أوامر بوت إدارة الموظفين:**

📊 **أوامر الحضور:**
/check_in - تسجيل دخول
/check_out - تسجيل خروج
/attendance_report - تقرير الحضور اليومي
/full_report - تقريري الكامل
/my_penalties - عرض عقوباتي

🚬 **أوامر الطلبات:**
/smoke - طلب سيجارة
/break - طلب استراحة غداء
/leave - طلب إذن خروج
/vacation - طلب إجازة

⚖️ **نظام العقوبات الذكي:**
/penalty_manager - مدير العقوبات (للمديرين)
/penalty_help - مساعدة نظام العقوبات
/penalty_settings - إعدادات العقوبات
/penalty_stats - إحصائيات العقوبات
/list_penalties <رقم_الهاتف> - عرض عقوبات موظف
/all_penalties - عرض جميع العقوبات النشطة

👑 **أوامر الإدارة (للمديرين فقط):**
/daily_report - تقرير الحضور اليومي
/weekly_report - تقرير الحضور الأسبوعي
/list_employees - عرض قائمة الموظفين
/add_employee - إضافة موظف جديد
/remove_employee - حذف موظف
/list_admins - عرض المديرين
/add_admin - إضافة مدير
/remove_admin - حذف مدير

⏰ **مواعيد العمل:**
• بداية الدوام: 8:00 صباحاً
• ساعات العمل الأساسية: 9 ساعات
• فترة السماح للتأخير: 15 دقيقة

🚬 **قواعد السجائر:**
• عدد السجائر اليومي: 5 سجائر
• الفجوة بين السجائر: 1.5 ساعة
• السماح بالسجائر بعد: 10:00 صباحاً
• مدة السيجارة: 6 دقائق

💡 **النصائح الذكية:**
• نظام العقوبات يقدم اقتراحات ذكية بناءً على سجل الموظف
• يتم تحليل تكرار المخالفات تلقائياً
• يمكن تخصيص العقوبات حسب كل حالة
• يمكن تحديد مبلغ العقوبة المالية يدوياً
"""
    
    await update.message.reply_text(help_text)

async def my_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض معرف المستخدم"""
    user = update.message.from_user
    employee = get_employee_by_telegram_id(user.id)
    
    if employee:
        employee_name = employee.get('full_name', get_employee_name_from_db(user))
        await update.message.reply_text(
            f"👤 **معلوماتك الشخصية:**\n\n"
            f"🆔 معرف تيليجرام: `{user.id}`\n"
            f"👤 الاسم: {employee_name}\n"
            f"📱 رقم الهاتف: {employee.get('phone_number', 'غير مسجل')}\n"
            f"📅 تاريخ التسجيل: {employee.get('created_at', 'غير معروف')}\n"
            f"⏰ آخر نشاط: {employee.get('last_active', 'غير معروف')}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"🆔 معرف تيليجرام: `{user.id}`\n"
            f"👤 الاسم: {user.first_name}\n"
            f"📱 الحالة: غير مسجل في النظام\n\n"
            f"يرجى استخدام /start لتسجيل حسابك.",
            parse_mode='Markdown'
        )

async def check_in_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل حضور الموظف"""
    user = update.message.from_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً باستخدام /start"
        )
        return
    
    user_phone = employee.get('phone_number')
    if not user_phone or not verify_employee(user_phone):
        await update.message.reply_text(
            "❌ غير مصرح لك باستخدام هذا الأمر.\n"
            "يرجى التواصل مع الإدارة لإضافة رقمك."
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', get_employee_name_from_db(user))
    
    result = record_check_in(employee_id)
    
    if not result['success']:
        if result.get('error') == 'already_checked_in':
            check_in_time = result['check_in_time']
            await update.message.reply_text(
                f"⚠️ لقد سجلت حضورك مسبقاً اليوم!\n\n"
                f"⏰ وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
                f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}"
            )
        else:
            await update.message.reply_text(f"❌ خطأ في تسجيل الحضور: {result.get('error', 'خطأ غير معروف')}")
        return
    
    check_in_time = result['check_in_time']
    is_late = result['is_late']
    late_minutes = result['late_minutes']
    
    if is_late:
        message = (
            f"⚠️ تم تسجيل حضورك مع تأخير!\n\n"
            f"👤 الموظف: {employee_name}\n"
            f"⏰ وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
            f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}\n"
            f"⏱ التأخير: {late_minutes} دقيقة\n\n"
            f"🚨 تم تسجيل عقوبة بسبب التأخير بعد الـ{LATE_GRACE_PERIOD_MINUTES} دقيقة المسموحة!"
        )
        
        await send_to_all_admins(
            context,
            f"⚠️ تأخير موظف\n\n"
            f"👤 الموظف: {employee_name}\n"
            f"📱 الهاتف: {user_phone}\n"
            f"⏰ وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
            f"⏱ التأخير: {late_minutes} دقيقة\n"
            f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}\n\n"
            f"🚨 تم تسجيل عقوبة تلقائية!"
        )
    else:
        if late_minutes > 0:
            message = (
                f"✅ تم تسجيل حضورك بنجاح!\n\n"
                f"👤 الموظف: {employee_name}\n"
                f"⏰ وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
                f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}\n"
                f"⏱ التأخير: {late_minutes} دقيقة (ضمن الوقت المسموح)\n\n"
                f"💼 يوم عمل موفق!"
            )
        else:
            message = (
                f"✅ تم تسجيل حضورك بنجاح!\n\n"
                f"👤 الموظف: {employee_name}\n"
                f"⏰ وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
                f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}\n"
                f"🎯 في الوقت المحدد!\n\n"
                f"💼 يوم عمل موفق!"
            )
        
        await send_to_all_admins(
            context,
            f"✅ تسجيل حضور موظف\n\n"
            f"👤 الموظف: {employee_name}\n"
            f"📱 الهاتف: {user_phone}\n"
            f"⏰ وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
            f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}\n"
            f"{'⏱ التأخير: ' + str(late_minutes) + ' دقيقة' if late_minutes > 0 else '🎯 في الوقت المحدد!'}"
        )
    
    await update.message.reply_text(message)

async def check_out_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل انصراف الموظف"""
    user = update.message.from_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً باستخدام /start"
        )
        return
    
    user_phone = employee.get('phone_number')
    if not user_phone or not verify_employee(user_phone):
        await update.message.reply_text(
            "❌ غير مصرح لك باستخدام هذا الأمر.\n"
            "يرجى التواصل مع الإدارة لإضافة رقمك."
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', get_employee_name_from_db(user))
    
    result = record_check_out(employee_id)
    
    if not result['success']:
        if result.get('error') == 'already_checked_out':
            check_out_time = result['check_out_time']
            total_minutes = result['total_work_minutes']
            work_hours = total_minutes / 60
            await update.message.reply_text(
                f"⚠️ لقد سجلت انصرافك مسبقاً اليوم!\n\n"
                f"🕐 وقت الانصراف: {check_out_time.strftime('%H:%M:%S')}\n"
                f"⏱ ساعات العمل: {work_hours:.2f} ساعة\n"
                f"📅 التاريخ: {check_out_time.strftime('%Y-%m-%d')}"
            )
        else:
            await update.message.reply_text(f"❌ {result.get('error', 'خطأ في تسجيل الانصراف')}")
        return
    
    check_in_time = result['check_in_time']
    check_out_time = result['check_out_time']
    total_minutes = result['total_work_minutes']
    overtime_minutes = result['overtime_minutes']
    
    # تحويل الدقائق إلى ساعات ودقائق للعرض
    total_hours = total_minutes / 60
    overtime_hours = overtime_minutes / 60
    
    message = (
        f"✅ تم تسجيل انصرافك بنجاح!\n\n"
        f"👤 الموظف: {employee_name}\n"
        f"🕐 وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
        f"🕐 وقت الانصراف: {check_out_time.strftime('%H:%M:%S')}\n"
        f"📅 التاريخ: {check_out_time.strftime('%Y-%m-%d')}\n\n"
    )
    
    # حساب وقت العمل المفصل
    work_hours, work_minutes = minutes_to_hours_minutes(total_minutes)
    
    if work_hours > 0 and work_minutes > 0:
        message += f"⏱ وقت العمل: {work_hours} ساعة و {work_minutes} دقيقة\n"
    elif work_hours > 0:
        message += f"⏱ وقت العمل: {work_hours} ساعة\n"
    else:
        message += f"⏱ وقت العمل: {work_minutes} دقيقة\n"
    
    if overtime_minutes > 0:
        overtime_hours, overtime_mins = minutes_to_hours_minutes(overtime_minutes)
        if overtime_hours > 0 and overtime_mins > 0:
            message += f"⭐ وقت إضافي: {overtime_hours} ساعة و {overtime_mins} دقيقة\n\n"
        elif overtime_hours > 0:
            message += f"⭐ وقت إضافي: {overtime_hours} ساعة\n\n"
        else:
            message += f"⭐ وقت إضافي: {overtime_mins} دقيقة\n\n"
        message += "🎉 شكراً على العمل الإضافي!"
    else:
        regular_minutes = WORK_REGULAR_MINUTES
        if total_minutes < regular_minutes:
            shortfall_minutes = regular_minutes - total_minutes
            shortfall_hours, shortfall_mins = minutes_to_hours_minutes(shortfall_minutes)
            if shortfall_hours > 0 and shortfall_mins > 0:
                message += f"\n⚠️ ملاحظة: نقص في وقت العمل بمقدار {shortfall_hours} ساعة و {shortfall_mins} دقيقة"
            elif shortfall_hours > 0:
                message += f"\n⚠️ ملاحظة: نقص في وقت العمل بمقدار {shortfall_hours} ساعة"
            else:
                message += f"\n⚠️ ملاحظة: نقص في وقت العمل بمقدار {shortfall_mins} دقيقة"
        else:
            message += "\n💼 شكراً لك! نراك غداً بإذن الله"
    
    await update.message.reply_text(message)
    
    try:
        admin_message = (
            f"🚪 تسجيل انصراف موظف\n\n"
            f"👤 الموظف: {employee_name}\n"
            f"📱 الهاتف: {user_phone}\n"
            f"🕐 وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
            f"🕐 وقت الانصراف: {check_out_time.strftime('%H:%M:%S')}\n"
            f"⏱ وقت العمل: {format_minutes_to_hours_minutes(total_minutes)}\n"
        )
        
        if overtime_minutes > 0:
            admin_message += f"⭐ وقت إضافي: {format_minutes_to_hours_minutes(overtime_minutes)}\n"
        
        await send_to_all_admins(context, admin_message)
    except Exception as e:
        logger.error(f"Failed to notify admin about check-out: {e}")

async def attendance_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تقرير حضور الموظف"""
    user = update.message.from_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً باستخدام /start"
        )
        return
    
    user_phone = employee.get('phone_number')
    if not user_phone or not verify_employee(user_phone):
        await update.message.reply_text(
            "❌ غير مصرح لك باستخدام هذا الأمر.\n"
            "يرجى التواصل مع الإدارة لإضافة رقمك."
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', get_employee_name_from_db(user))
    
    records = get_employee_attendance_report(employee_id, days=7)
    
    if not records:
        await update.message.reply_text(
            f"📊 تقرير الحضور - {employee_name}\n\n"
            "⚠️ لا توجد سجلات حضور للأيام السبعة الماضية."
        )
        return
    
    message = (
        f"📊 تقرير الحضور - {employee_name}\n"
        f"📅 آخر 7 أيام\n\n"
    )
    
    total_days = 0
    total_minutes = 0
    total_overtime_minutes = 0
    late_days = 0
    
    for record in records:
        date = record['date']
        check_in = record['check_in_time']
        check_out = record['check_out_time']
        is_late = record['is_late']
        work_minutes = int(record['total_work_minutes']) if record['total_work_minutes'] else 0
        overtime = int(record['overtime_minutes']) if record['overtime_minutes'] else 0
        
        message += f"━━━━━━━━━━━━━━━━━\n"
        message += f"📅 {date.strftime('%Y-%m-%d')}\n"
        
        if check_in:
            message += f"🕐 حضور: {check_in.strftime('%H:%M')}"
            if is_late:
                late_days += 1
                message += f" ⚠️ متأخر"
            message += "\n"
        else:
            message += "❌ لم يتم تسجيل الحضور\n"
        
        if check_out:
            message += f"🕐 انصراف: {check_out.strftime('%H:%M')}\n"
            message += f"⏱ وقت العمل: {format_minutes_to_hours_minutes(work_minutes)}\n"
            if overtime > 0:
                message += f"⭐ إضافي: {format_minutes_to_hours_minutes(overtime)}\n"
            total_days += 1
            total_minutes += work_minutes
            total_overtime_minutes += overtime
        
        message += "\n"
    
    message += (
        f"━━━━━━━━━━━━━━━━━\n"
        f"📈 الإحصائيات:\n"
        f"📅 أيام العمل: {total_days}\n"
        f"⏱ إجمالي وقت العمل: {format_minutes_to_hours_minutes(total_minutes)}\n"
    )
    
    if total_overtime_minutes > 0:
        message += f"⭐ إجمالي الإضافي: {format_minutes_to_hours_minutes(total_overtime_minutes)}\n"
    
    if late_days > 0:
        message += f"⚠️ أيام التأخير: {late_days}\n"
    
    if total_days > 0:
        avg_minutes = total_minutes / total_days
        message += f"📊 متوسط وقت اليوم: {format_minutes_to_hours_minutes(avg_minutes)}\n"
    
    await update.message.reply_text(message)

async def full_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تقرير كامل للموظف"""
    user = update.message.from_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً باستخدام /start"
        )
        return
    
    user_phone = employee.get('phone_number')
    if not user_phone or not verify_employee(user_phone):
        await update.message.reply_text(
            "❌ غير مصرح لك باستخدام هذا الأمر.\n"
            "يرجى التواصل مع الإدارة لإضافة رقمك."
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', get_employee_name_from_db(user))
    
    # الحصول على جميع البيانات
    attendance_records = get_employee_attendance_report(employee_id, days=30)
    penalties_summary = get_employee_penalty_summary(employee_id)
    penalties = get_employee_penalties(employee_id, active_only=False)
    
    # حساب الإحصائيات بالدقائق
    total_days = len(attendance_records)
    present_days = sum(1 for r in attendance_records if r['check_in_time'])
    late_days = sum(1 for r in attendance_records if r['is_late'])
    total_minutes = sum(int(r['total_work_minutes'] or 0) for r in attendance_records)
    total_overtime_minutes = sum(int(r['overtime_minutes'] or 0) for r in attendance_records)
    
    # حساب السجائر لهذا الشهر
    today = get_jordan_time().date()
    first_day_month = today.replace(day=1)
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT SUM(count) FROM daily_cigarettes 
            WHERE employee_id = %s AND date >= %s
        """, (employee_id, first_day_month))
        monthly_smokes = cur.fetchone()[0] or 0
        cur.close()
        conn.close()
    except:
        monthly_smokes = 0
    
    message = (
        f"📊 التقرير الكامل - {employee_name}\n"
        f"📅 شهر: {today.strftime('%Y-%m')}\n"
        f"⏰ تاريخ التقرير: {today.strftime('%Y-%m-%d')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    # قسم الحضور والانصراف
    message += "🔹 الحضور والانصراف:\n"
    message += f"   📅 أيام العمل: {total_days} يوم\n"
    message += f"   ✅ أيام الحضور: {present_days} يوم\n"
    message += f"   ⏰ أيام التأخير: {late_days} يوم\n"
    message += f"   ⏱ إجمالي وقت العمل: {format_minutes_to_hours_minutes(total_minutes)}\n"
    message += f"   ⭐ وقت إضافي: {format_minutes_to_hours_minutes(total_overtime_minutes)}\n\n"
    
    # قسم السجائر
    message += "🔹 السجائر:\n"
    message += f"   🚬 سجائر هذا الشهر: {monthly_smokes}\n"
    if total_days > 0:
        avg_daily_smokes = monthly_smokes / total_days
        message += f"   📊 المعدل اليومي: {avg_daily_smokes:.1f} سيجارة/يوم\n"
    message += f"   ⚠️ الحالة: {'🚫 محروم' if is_employee_banned_from_smoking(employee_id) else '✅ مسموح'}\n\n"
    
    # قسم العقوبات
    message += "🔹 العقوبات:\n"
    message += f"   ⚖️ عدد العقوبات النشطة: {penalties_summary['active_penalties']}\n"
    message += f"   💰 إجمالي الخصومات: {penalties_summary['total_deduction']:.2f} دينار\n"
    
    if penalties_summary['recent_penalties']:
        message += "   📋 آخر العقوبات:\n"
        for penalty in penalties_summary['recent_penalties']:
            message += f"      • {penalty[0]} - {penalty[1]} - {penalty[2]} دينار\n"
    
    message += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    message += "📈 التقييم العام: "
    
    # حساب النقاط
    score = 100
    
    # خصم نقاط التأخير
    if total_days > 0:
        late_percentage = (late_days / total_days) * 100
        if late_percentage > 20:
            score -= 30
        elif late_percentage > 10:
            score -= 15
        elif late_percentage > 5:
            score -= 5
    
    # خصم نقاط العقوبات
    score -= penalties_summary['active_penalties'] * 5
    
    # خصم نقاط حظر السجائر
    if is_employee_banned_from_smoking(employee_id):
        score -= 20
    
    # تحديد التقييم
    if score >= 90:
        message += "⭐ ممتاز ⭐"
    elif score >= 80:
        message += "👍 جيد جداً"
    elif score >= 70:
        message += "✅ جيد"
    elif score >= 60:
        message += "⚠️ مقبول"
    else:
        message += "❌ يحتاج تحسين"
    
    message += f" ({score}/100)\n\n"
    
    # نصائح حسب التقييم
    if score < 70:
        message += "💡 نصائح للتحسين:\n"
        if late_days > 0:
            message += "   • حاول الحضور في الوقت المحدد\n"
        if penalties_summary['active_penalties'] > 0:
            message += "   • التزم بالأنظمة والقوانين\n"
        if is_employee_banned_from_smoking(employee_id):
            message += "   • التزم بمواعيد السجائر المسموحة\n"
    
    await update.message.reply_text(message)

async def my_penalties_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض عقوبات الموظف"""
    user = update.message.from_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً باستخدام /start"
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', get_employee_name_from_db(user))
    
    penalties = get_employee_penalties(employee_id, active_only=True)
    
    if not penalties:
        await update.message.reply_text(
            f"📋 العقوبات - {employee_name}\n\n"
            "✅ لا توجد عقوبات نشطة حالياً.\n"
            "👏 أحسنت! استمر في الحفاظ على التزامك."
        )
        return
    
    message = (
        f"📋 العقوبات النشطة - {employee_name}\n"
        f"📅 تاريخ التقرير: {get_jordan_time().strftime('%Y-%m-%d')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    for i, penalty in enumerate(penalties, 1):
        message += f"🔹 العقوبة #{i}\n"
        message += f"   📛 النوع: {penalty.get('penalty_name', 'غير محدد')}\n"
        message += f"   📅 التاريخ: {penalty.get('penalty_date').strftime('%Y-%m-%d')}\n"
        message += f"   📝 السبب: {penalty.get('reason', 'غير محدد')}\n"
        deduction = float(penalty.get('deduction_amount', 0))
        if deduction > 0:
            message += f"   💰 الخصم: {deduction:.2f} دينار\n"
        ban_days = penalty.get('smoke_ban_days', 0)
        if ban_days > 0:
            message += f"   🚬 حظر سجائر: {ban_days} يوم\n"
        message += "\n"
    
    summary = get_employee_penalty_summary(employee_id)
    message += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 ملخص العقوبات:\n"
        f"   ⚖️ عدد العقوبات النشطة: {summary['active_penalties']}\n"
        f"   💰 إجمالي الخصومات: {summary['total_deduction']:.2f} دينار\n\n"
    )
    
    if is_employee_banned_from_smoking(employee_id):
        message += "🚫 حالة السجائر: محروم حالياً\n"
    else:
        message += "✅ حالة السجائر: مسموح\n"
    
    message += "\n💡 نصائح:\n"
    message += "• التزم بالمواعيد لتجنب عقوبات التأخير\n"
    message += "• احترم قواعد السجائر اليومية\n"
    message += "• سجل الحضور والانصراف يومياً\n"
    
    await update.message.reply_text(message)

async def smoke_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب سيجارة"""
    user = update.message.from_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً باستخدام /start"
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', get_employee_name_from_db(user))
    
    # التحقق إذا كان الموظف قد سجل حضوره اليوم
    if not is_employee_checked_in_today(employee_id):
        add_penalty(employee_id, 'request_without_checkin', 'طلب سيجارة بدون تسجيل حضور')
        await update.message.reply_text(
            f"❌ {employee_name}، لم تسجل حضورك اليوم!\n\n"
            f"⚠️ تم تسجيل مخالفة: طلب بدون تسجيل حضور\n"
            f"🚫 لن تتم الموافقة على طلبك حتى تسجل الحضور."
        )
        return
    
    # التحقق من حظر السجائر
    if is_employee_banned_from_smoking(employee_id):
        await update.message.reply_text(
            f"🚫 {employee_name}، أنت محروم من طلب السجائر حالياً.\n\n"
            f"⚠️ لديك عقوبة سارية تمنعك من طلب السجائر.\n"
            f"📋 يمكنك مراجعة عقوباتك باستخدام /my_penalties"
        )
        return
    
    # التحقق من الوقت (بعد الساعة 10 صباحاً)
    if not can_request_smoke():
        add_penalty(employee_id, 'smoke_before_10', 'طلب سيجارة قبل الساعة 10 صباحاً')
        await update.message.reply_text(
            f"❌ {employee_name}، الوقت غير مناسب لطلب السيجارة!\n\n"
            f"🚬 السجائر مسموحة بعد الساعة {SMOKE_ALLOWED_AFTER_HOUR}:00 صباحاً.\n"
            f"⚠️ تم تسجيل مخالفة: طلب سيجارة قبل الوقت المسموح"
        )
        return
    
    # التحقق من عدد السجائر اليومية
    smoke_count = get_smoke_count_db(employee_id)
    if smoke_count >= MAX_DAILY_SMOKES:
        add_penalty(employee_id, 'smoke_excess', f'تجاوز عدد السجائر المسموح ({MAX_DAILY_SMOKES})')
        await update.message.reply_text(
            f"❌ {employee_name}، لقد استهلكت جميع السجائر المسموحة اليوم!\n\n"
            f"🚬 الحد الأقصى: {MAX_DAILY_SMOKES} سجائر/يوم\n"
            f"📊 عدد سجائرك اليوم: {smoke_count}\n"
            f"⚠️ تم تسجيل مخالفة: تجاوز عدد السجائر المسموح"
        )
        return
    
    # التحقق من الفجوة الزمنية بين السجائر
    last_cigarette = get_last_cigarette_time(employee_id)
    if last_cigarette:
        time_since_last = (get_jordan_time() - last_cigarette).total_seconds() / 3600  # بالساعات
        if time_since_last < MIN_GAP_BETWEEN_SMOKES_HOURS:
            add_penalty(employee_id, 'smoke_gap_violation', 
                       f'عدم احترام الفجوة بين السجائر ({MIN_GAP_BETWEEN_SMOKES_HOURS} ساعة)')
            await update.message.reply_text(
                f"❌ {employee_name}، لم يمر وقت كافٍ منذ آخر سيجارة!\n\n"
                f"⏰ يجب الانتظار {MIN_GAP_BETWEEN_SMOKES_HOURS} ساعة بين السجائر.\n"
                f"⏱️ الوقت المتبقي: {MIN_GAP_BETWEEN_SMOKES_HOURS - time_since_last:.1f} ساعة\n"
                f"⚠️ تم تسجيل مخالفة: عدم احترام الفجوة بين السجائر"
            )
            return
    
    # زيادة عداد السجائر
    new_count = increment_smoke_count_db(employee_id)
    record_cigarette_time(employee_id)
    
    # بدء مؤقت السيجارة
    from datetime import timedelta
    await start_countdown_timer(
        context,
        user.id,
        'smoke',
        SMOKE_BREAK_DURATION * 60,
        'سيجارة 🚬'
    )
    
    await update.message.reply_text(
        f"✅ تمت الموافقة على طلب السيجارة!\n\n"
        f"👤 الموظف: {employee_name}\n"
        f"🚬 سجائر اليوم: {new_count}/{MAX_DAILY_SMOKES}\n"
        f"⏰ مدة السيجارة: {SMOKE_BREAK_DURATION} دقيقة\n\n"
        f"⏱️ سيتم إشعارك بانتهاء الوقت تلقائياً."
    )
    
    # إرسال إشعار للمديرين
    await send_to_all_admins(
        context,
        f"🚬 طلب سيجارة\n\n"
        f"👤 الموظف: {employee_name}\n"
        f"📱 الهاتف: {employee.get('phone_number')}\n"
        f"🚬 عدد السجائر اليوم: {new_count}/{MAX_DAILY_SMOKES}\n"
        f"🕐 الوقت: {get_jordan_time().strftime('%H:%M:%S')}\n"
        f"⏰ المدة: {SMOKE_BREAK_DURATION} دقيقة"
    )

async def break_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب استراحة غداء"""
    user = update.message.from_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً باستخدام /start"
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', get_employee_name_from_db(user))
    
    # التحقق إذا كان الموظف قد سجل حضوره اليوم
    if not is_employee_checked_in_today(employee_id):
        await update.message.reply_text(
            f"❌ {employee_name}، لم تسجل حضورك اليوم!\n\n"
            f"🚫 لن تتم الموافقة على طلبك حتى تسجل الحضور."
        )
        return
    
    # التحقق إذا أخذ استراحة غداء من قبل
    if has_taken_lunch_break_today(employee_id):
        add_penalty(employee_id, 'lunch_twice', 'طلب استراحة غداء مرتين')
        await update.message.reply_text(
            f"❌ {employee_name}، لقد أخذت استراحة الغداء مسبقاً!\n\n"
            f"⚠️ تم تسجيل مخالفة: طلب استراحة غداء مرتين"
        )
        return
    
    # تسجيل استراحة الغداء
    mark_lunch_break_taken(employee_id)
    
    await update.message.reply_text(
        f"✅ تمت الموافقة على استراحة الغداء!\n\n"
        f"👤 الموظف: {employee_name}\n"
        f"⏰ المدة: 30 دقيقة\n"
        f"🍽️ استمتع بوجبتك!"
    )
    
    # إرسال إشعار للمديرين
    await send_to_all_admins(
        context,
        f"☕ طلب استراحة غداء\n\n"
        f"👤 الموظف: {employee_name}\n"
        f"📱 الهاتف: {employee.get('phone_number')}\n"
        f"🕐 الوقت: {get_jordan_time().strftime('%H:%M:%S')}\n"
        f"⏰ المدة: 30 دقيقة"
    )

async def leave_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب إذن خروج"""
    user = update.message.from_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً باستخدام /start"
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', get_employee_name_from_db(user))
    
    # التحقق إذا كان الموظف قد سجل حضوره اليوم
    if not is_employee_checked_in_today(employee_id):
        await update.message.reply_text(
            f"❌ {employee_name}، لم تسجل حضورك اليوم!\n\n"
            f"🚫 لن تتم الموافقة على طلبك حتى تسجل الحضور."
        )
        return
    
    await update.message.reply_text(
        f"📝 طلب إذن خروج\n\n"
        f"👤 الموظف: {employee_name}\n\n"
        f"يرجى كتابة سبب الخروج:\n"
        f"(مثال: زيارة طبيب، أمر عائلي، ...)"
    )
    
    return LEAVE_REASON

async def receive_leave_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال سبب الخروج"""
    user = update.message.from_user
    reason = update.message.text
    
    employee = get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text("❌ خطأ: لم يتم العثور على بيانات الموظف")
        return ConversationHandler.END
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', get_employee_name_from_db(user))
    
    # حفظ الطلب في قاعدة البيانات
    request_id = save_request(employee_id, 'leave')
    
    if request_id:
        # لوحة المفاتيح للمديرين
        keyboard = [
            [
                InlineKeyboardButton("✅ الموافقة", callback_data=f"approve_{request_id}"),
                InlineKeyboardButton("❌ الرفض", callback_data=f"reject_{request_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # إرسال الطلب للمديرين
        await send_to_all_admins(
            context,
            f"🏠 طلب إذن خروج جديد\n\n"
            f"👤 الموظف: {employee_name}\n"
            f"📱 الهاتف: {employee.get('phone_number')}\n"
            f"📝 السبب: {reason}\n"
            f"🕐 الوقت: {get_jordan_time().strftime('%H:%M:%S')}\n"
            f"📅 التاريخ: {get_jordan_time().strftime('%Y-%m-%d')}\n"
            f"🆔 رقم الطلب: {request_id}",
            reply_markup=reply_markup
        )
        
        await update.message.reply_text(
            f"✅ تم إرسال طلبك للإدارة!\n\n"
            f"🆔 رقم الطلب: {request_id}\n"
            f"📝 السبب: {reason}\n\n"
            f"⏳ سيتم إشعارك بقرار الإدارة قريباً."
        )
    else:
        await update.message.reply_text(
            "❌ حدث خطأ في حفظ طلبك.\n"
            "يرجى المحاولة مرة أخرى."
        )
    
    return ConversationHandler.END

async def vacation_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب إجازة"""
    user = update.message.from_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً باستخدام /start"
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', get_employee_name_from_db(user))
    
    await update.message.reply_text(
        f"🌴 طلب إجازة\n\n"
        f"👤 الموظف: {employee_name}\n\n"
        f"يرجى كتابة سبب طلب الإجازة:\n"
        f"(مثال: إجازة سنوية، ظروف عائلية، ...)"
    )
    
    return VACATION_REASON

async def receive_vacation_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال سبب الإجازة"""
    user = update.message.from_user
    reason = update.message.text
    
    employee = get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text("❌ خطأ: لم يتم العثور على بيانات الموظف")
        return ConversationHandler.END
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', get_employee_name_from_db(user))
    
    # حفظ الطلب في قاعدة البيانات
    request_id = save_request(employee_id, 'vacation')
    
    if request_id:
        # لوحة المفاتيح للمديرين
        keyboard = [
            [
                InlineKeyboardButton("✅ الموافقة", callback_data=f"approve_{request_id}"),
                InlineKeyboardButton("❌ الرفض", callback_data=f"reject_{request_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # إرسال الطلب للمديرين
        await send_to_all_admins(
            context,
            f"🌴 طلب إجازة جديد\n\n"
            f"👤 الموظف: {employee_name}\n"
            f"📱 الهاتف: {employee.get('phone_number')}\n"
            f"📝 السبب: {reason}\n"
            f"🕐 الوقت: {get_jordan_time().strftime('%H:%M:%S')}\n"
            f"📅 التاريخ: {get_jordan_time().strftime('%Y-%m-%d')}\n"
            f"🆔 رقم الطلب: {request_id}",
            reply_markup=reply_markup
        )
        
        await update.message.reply_text(
            f"✅ تم إرسال طلبك للإدارة!\n\n"
            f"🆔 رقم الطلب: {request_id}\n"
            f"📝 السبب: {reason}\n\n"
            f"⏳ سيتم إشعارك بقرار الإدارة قريباً."
        )
    else:
        await update.message.reply_text(
            "❌ حدث خطأ في حفظ طلبك.\n"
            "يرجى المحاولة مرة أخرى."
        )
    
    return ConversationHandler.END

async def daily_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تقرير الحضور اليومي (للمدير فقط)"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    today = get_jordan_time().date()
    records = get_daily_attendance_report(today)
    
    if not records:
        await update.message.reply_text(
            f"📊 التقرير اليومي - {today.strftime('%Y-%m-%d')}\n\n"
            "⚠️ لا توجد سجلات حضور لليوم."
        )
        return
    
    message = (
        f"📊 تقرير الحضور اليومي\n"
        f"📅 {today.strftime('%Y-%m-%d')}\n\n"
    )
    
    present_count = 0
    absent_count = 0
    late_count = 0
    total_minutes = 0
    total_overtime_minutes = 0
    
    for record in records:
        name = record['full_name']
        check_in = record['check_in_time']
        check_out = record['check_out_time']
        is_late = record['is_late']
        work_minutes = int(record['total_work_minutes']) if record['total_work_minutes'] else 0
        overtime = int(record['overtime_minutes']) if record['overtime_minutes'] else 0
        
        message += f"━━━━━━━━━━━━━━━━━\n"
        message += f"👤 {name}\n"
        
        if check_in:
            present_count += 1
            message += f"🕐 حضور: {check_in.strftime('%H:%M')}"
            if is_late:
                late_count += 1
                message += " ⚠️"
            message += "\n"
            
            if check_out:
                message += f"🕐 انصراف: {check_out.strftime('%H:%M')}\n"
                message += f"⏱ {format_minutes_to_hours_minutes(work_minutes)}"
                if overtime > 0:
                    message += f" (⭐ {format_minutes_to_hours_minutes(overtime)})"
                message += "\n"
                total_minutes += work_minutes
                total_overtime_minutes += overtime
            else:
                message += "⏳ لم ينصرف بعد\n"
        else:
            absent_count += 1
            message += "❌ غائب\n"
        
        message += "\n"
    
    total_employees = len(records)
    message += (
        f"━━━━━━━━━━━━━━━━━\n"
        f"📈 ملخص اليوم:\n"
        f"👥 إجمالي الموظفين: {total_employees}\n"
        f"✅ حاضر: {present_count}\n"
        f"❌ غائب: {absent_count}\n"
    )
    
    if late_count > 0:
        message += f"⚠️ متأخرين: {late_count}\n"
    
    message += f"⏱ إجمالي وقت العمل: {format_minutes_to_hours_minutes(total_minutes)}\n"
    
    if total_overtime_minutes > 0:
        message += f"⭐ إجمالي الإضافي: {format_minutes_to_hours_minutes(total_overtime_minutes)}\n"
    
    await update.message.reply_text(message)

async def weekly_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تقرير الحضور الأسبوعي (للمدير فقط)"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    records = get_weekly_attendance_report()
    
    if not records:
        await update.message.reply_text(
            "📊 التقرير الأسبوعي\n\n"
            "⚠️ لا توجد سجلات حضور للأسبوع الماضي."
        )
        return
    
    end_date = get_jordan_time().date()
    start_date = end_date - timedelta(days=6)
    
    message = (
        f"📊 تقرير الحضور الأسبوعي\n"
        f"📅 {start_date.strftime('%Y-%m-%d')} إلى {end_date.strftime('%Y-%m-%d')}\n\n"
    )
    
    total_present = 0
    total_late = 0
    grand_total_minutes = 0
    grand_total_overtime_minutes = 0
    
    for record in records:
        name = record['full_name']
        present_days = int(record['present_days']) if record['present_days'] else 0
        late_days = int(record['late_days']) if record['late_days'] else 0
        total_minutes = int(record['total_minutes']) if record['total_minutes'] else 0
        total_overtime = int(record['total_overtime_minutes']) if record['total_overtime_minutes'] else 0
        
        message += f"━━━━━━━━━━━━━━━━━\n"
        message += f"👤 {name}\n"
        message += f"📅 أيام الحضور: {present_days}/7\n"
        
        if late_days > 0:
            message += f"⚠️ أيام التأخير: {late_days}\n"
        
        message += f"⏱ إجمالي وقت العمل: {format_minutes_to_hours_minutes(total_minutes)}\n"
        
        if total_minutes > 0 and present_days > 0:
            avg_minutes = total_minutes / present_days
            message += f"📊 متوسط اليوم: {format_minutes_to_hours_minutes(avg_minutes)}\n"
        
        if total_overtime > 0:
            message += f"⭐ إضافي: {format_minutes_to_hours_minutes(total_overtime)}\n"
        
        message += "\n"
        
        total_present += present_days
        total_late += late_days
        grand_total_minutes += total_minutes
        grand_total_overtime_minutes += total_overtime
    
    total_employees = len(records)
    message += (
        f"━━━━━━━━━━━━━━━━━\n"
        f"📈 ملخص الأسبوع:\n"
        f"👥 عدد الموظفين: {total_employees}\n"
        f"📅 إجمالي أيام الحضور: {total_present}\n"
    )
    
    if total_late > 0:
        message += f"⚠️ إجمالي أيام التأخير: {total_late}\n"
    
    message += f"⏱ إجمالي وقت العمل: {format_minutes_to_hours_minutes(grand_total_minutes)}\n"
    
    if grand_total_overtime_minutes > 0:
        message += f"⭐ إجمالي الإضافي: {format_minutes_to_hours_minutes(grand_total_overtime_minutes)}\n"
    
    if total_employees > 0 and total_present > 0:
        avg_attendance = total_present / total_employees
        message += f"📊 متوسط الحضور: {avg_attendance:.1f} أيام/موظف\n"
    
    await update.message.reply_text(message)

async def list_employees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة الموظفين (للمدير فقط)"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    employees = get_all_employees()
    
    if not employees:
        await update.message.reply_text("📋 لا يوجد موظفين مسجلين في النظام.")
        return
    
    message = "👥 **قائمة الموظفين:**\n\n"
    
    for i, emp in enumerate(employees, 1):
        name = emp.get('full_name', 'غير معروف')
        phone = emp.get('phone_number', 'غير معروف')
        telegram_id = emp.get('telegram_id')
        status = "✅ مسجل في تيليجرام" if telegram_id else "📱 مسجل برقم الهاتف فقط"
        
        message += f"{i}. **{name}**\n"
        message += f"   📱 الهاتف: {phone}\n"
        message += f"   🆔 تيليجرام: `{telegram_id or 'غير مرتبط'}`\n"
        message += f"   📅 التسجيل: {emp.get('created_at').strftime('%Y-%m-%d')}\n"
        message += f"   ⏰ آخر نشاط: {emp.get('last_active').strftime('%Y-%m-%d %H:%M')}\n"
        message += f"   📍 الحالة: {status}\n\n"
    
    message += f"📊 **الإجمالي:** {len(employees)} موظف"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def add_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة موظف جديد (للمدير فقط)"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "📝 استخدام الأمر:\n"
            "`/add_employee \"الاسم الكامل\" رقم_الهاتف`\n\n"
            "مثال:\n"
            "`/add_employee \"أحمد محمد\" +962791234567`\n\n"
            "ملاحظة:\n"
            "• ضع الاسم بين علامتي اقتباس\n"
            "• رقم الهاتف يجب أن يبدأ بعلامة +"
        )
        return
    
    # استخراج الاسم والهاتف
    full_name = context.args[0]
    phone_number = context.args[1]
    
    # إزالة علامات الاقتباس إذا كانت موجودة
    if full_name.startswith('"') and full_name.endswith('"'):
        full_name = full_name[1:-1]
    
    # التحقق من رقم الهاتف
    if not phone_number.startswith('+'):
        await update.message.reply_text("❌ رقم الهاتف يجب أن يبدأ بعلامة +")
        return
    
    # حفظ الموظف
    employee_id = save_employee(None, phone_number, full_name)
    
    if employee_id:
        # إضافة رقم الهاتف إلى القائمة المصرح بها
        from . import authorized_phones
        if phone_number not in authorized_phones:
            authorized_phones.append(phone_number)
        
        await update.message.reply_text(
            f"✅ تم إضافة الموظف بنجاح!\n\n"
            f"👤 الاسم: {full_name}\n"
            f"📱 الهاتف: {phone_number}\n"
            f"🆔 المعرف: {employee_id}\n"
            f"👤 تمت الإضافة بواسطة: {user.first_name}\n"
            f"⏰ الوقت: {get_jordan_time().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    else:
        await update.message.reply_text("❌ حدث خطأ في إضافة الموظف.")

async def remove_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف موظف (للمدير فقط)"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "📝 استخدام الأمر:\n"
            "`/remove_employee رقم_الهاتف`\n\n"
            "مثال:\n"
            "`/remove_employee +962791234567`"
        )
        return
    
    phone_number = context.args[0]
    
    if not phone_number.startswith('+'):
        await update.message.reply_text("❌ رقم الهاتف يجب أن يبدأ بعلامة +")
        return
    
    # حذف الموظف
    if delete_employee_by_phone(phone_number):
        # حذف رقم الهاتف من القائمة المصرح بها
        from . import authorized_phones
        if phone_number in authorized_phones:
            authorized_phones.remove(phone_number)
        
        await update.message.reply_text(
            f"✅ تم حذف الموظف بنجاح!\n\n"
            f"📱 الهاتف: {phone_number}\n"
            f"👤 تم الحذف بواسطة: {user.first_name}\n"
            f"⏰ الوقت: {get_jordan_time().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    else:
        await update.message.reply_text("❌ لم يتم العثور على موظف بهذا الرقم.")

async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المديرين (للمدير فقط)"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    admin_ids = get_all_admins()
    
    if not admin_ids:
        await update.message.reply_text("📋 لا يوجد مديرين في النظام.")
        return
    
    message = "👑 **قائمة المديرين:**\n\n"
    
    for i, admin_id in enumerate(admin_ids, 1):
        try:
            chat = await context.bot.get_chat(admin_id)
            name = chat.first_name or "مجهول"
            if chat.last_name:
                name = f"{chat.first_name} {chat.last_name}"
            
            is_super = is_super_admin(admin_id)
            super_status = "👑 (مدير رئيسي)" if is_super else "👤 (مدير عادي)"
            
            message += f"{i}. {name} {super_status}\n"
            message += f"   🆔 المعرف: `{admin_id}`\n\n"
        except:
            message += f"{i}. 🆔 المعرف: `{admin_id}` (غير متاح)\n\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة مدير جديد (للمدير الرئيسي فقط)"""
    user = update.message.from_user
    
    if not is_super_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير الرئيسي فقط.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "📝 استخدام الأمر:\n"
            "`/add_admin <معرف_تيليجرام>`\n\n"
            "مثال:\n"
            "`/add_admin 123456789`",
            parse_mode='Markdown'
        )
        return
    
    try:
        new_admin_id = int(context.args[0])
        
        # التحقق من عدم إضافة نفسه
        if new_admin_id == user.id:
            await update.message.reply_text("❌ لا يمكنك إضافة نفسك!")
            return
        
        # التحقق إذا كان المدير موجوداً بالفعل
        if new_admin_id in get_all_admins():
            await update.message.reply_text("⚠️ هذا المستخدم مدير بالفعل.")
            return
        
        # إضافة المدير
        if add_admin_to_db(new_admin_id, added_by=user.id):
            await update.message.reply_text(
                f"✅ تم إضافة المدير بنجاح!\n\n"
                f"🆔 المعرف: `{new_admin_id}`\n"
                f"👤 تمت الإضافة بواسطة: {user.first_name}\n"
                f"⏰ الوقت: {get_jordan_time().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode='Markdown'
            )
            
            # إرسال رسالة ترحيبية للمدير الجديد
            try:
                await context.bot.send_message(
                    chat_id=new_admin_id,
                    text=f"👑 تم تعيينك كمدير في نظام إدارة الحضور!\n\n"
                         f"🎉 مبارك! يمكنك الآن الوصول إلى أوامر الإدارة.\n\n"
                         f"📋 الأوامر المتاحة:\n"
                         f"/daily_report - التقرير اليومي\n"
                         f"/weekly_report - التقرير الأسبوعي\n"
                         f"/list_employees - قائمة الموظفين\n"
                         f"/add_employee - إضافة موظف\n"
                         f"/remove_employee - حذف موظف\n"
                         f"/list_admins - عرض المديرين\n\n"
                         f"🆔 تمت الإضافة بواسطة: {user.first_name}"
                )
            except:
                pass
        else:
            await update.message.reply_text("❌ حدث خطأ في إضافة المدير.")
    except ValueError:
        await update.message.reply_text("❌ معرف غير صالح. يرجى إدخال رقم معرف صحيح.")

async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف مدير (للمدير الرئيسي فقط)"""
    user = update.message.from_user
    
    if not is_super_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير الرئيسي فقط.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "📝 استخدام الأمر:\n"
            "`/remove_admin <معرف_تيليجرام>`\n\n"
            "مثال:\n"
            "`/remove_admin 123456789`",
            parse_mode='Markdown'
        )
        return
    
    try:
        admin_id_to_remove = int(context.args[0])
        
        # التحقق من عدم حذف نفسه
        if admin_id_to_remove == user.id:
            await update.message.reply_text("❌ لا يمكنك حذف نفسك!")
            return
        
        # التحقق من عدم حذف مدير رئيسي
        if admin_id_to_remove in ADMIN_IDS:
            await update.message.reply_text("❌ لا يمكن حذف المدير الرئيسي!")
            return
        
        # حذف المدير
        if remove_admin_from_db(admin_id_to_remove):
            await update.message.reply_text(
                f"✅ تم حذف المدير بنجاح!\n\n"
                f"🆔 المعرف: `{admin_id_to_remove}`\n"
                f"👤 تم الحذف بواسطة: {user.first_name}\n"
                f"⏰ الوقت: {get_jordan_time().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ لم يتم العثور على هذا المدير.")
    except ValueError:
        await update.message.reply_text("❌ معرف غير صالح. يرجى إدخال رقم معرف صحيح.")

async def penalty_manager_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء مدير العقوبات"""
    return await start_penalty_manager(update, context)

async def smart_penalty_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مساعدة نظام العقوبات الذكي"""
    user = update.message.from_user
    
    help_text = """
🤖 **نظام العقوبات الذكي - دليل الاستخدام**

🔹 **الميزات الذكية:**
• اقتراحات عقوبات مناسبة بناءً على السجل السابق
• تحليل تكرار المخالفات وتقديم نصائح
• تذكير بالإجراءات السابقة للموظف
• اقتراحات لتخفيف أو تشديد العقوبة

🔹 **الأوامر الرئيسية:**
`/penalty_manager` - فتح مدير العقوبات الذكي
`/penalty_settings` - عرض وتعديل إعدادات العقوبات
`/penalty_stats` - إحصائيات مفصلة عن العقوبات
`/list_penalties <رقم_الهاتف>` - عرض عقوبات موظف
`/all_penalties` - عرض جميع العقوبات النشطة

🔹 **داخل مدير العقوبات:**
1. **إضافة عقوبة:** البحث → اختيار موظف → اختيار مخالفة → إدخال سبب → تحديد المبلغ → تأكيد
2. **تعديل عقوبة:** البحث → اختيار موظف → اختيار العقوبة → التعديل (المبلغ، السبب، الحظر)
3. **إزالة عقوبة:** البحث → اختيار موظف → اختيار العقوبة → حذف
4. **الإعدادات:** تبديل خيارات النظام

💰 **تحديد قيمة العقوبة المالية:**
• يمكنك استخدام المبلغ الافتراضي للنوع
• أو تحديد مبلغ مخصص يدوياً
• الحد الأدنى: 0 دينار
• الحد الأقصى: 500 دينار

💡 **النصائح الذكية ستظهر تلقائياً عند:**
• تكرار المخالفة لنفس الموظف
• اختيار عقوبة قد تكون قاسية
• وجود سجل نظيف للموظف
• اقتراب الموظف من الحد الأقصى للعقوبات

👑 **للمديرين فقط:** هذا النظام متاح للمديرين المسجلين.
"""
    
    if is_admin(user.id):
        help_text += "\n✅ **أنت مدير، يمكنك استخدام جميع الأوامر.**"
    else:
        help_text += "\n❌ **أنت لست مديراً، لا يمكنك استخدام هذه الأوامر.**"
    
    await update.message.reply_text(help_text)

async def penalty_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض إعدادات العقوبات"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    settings = get_penalty_settings()
    message = "⚙️ **إعدادات نظام العقوبات الحالية:**\n\n"
    
    for key, value in settings.items():
        emoji = "✅" if value else "❌" if isinstance(value, bool) else "🔢"
        message += f"{emoji} **{key.replace('_', ' ').title()}:** {value}\n"
    
    message += "\n🔧 **لتعديل الإعدادات، استخدم:**\n"
    message += "`/penalty_manager` ثم اختر 'إعدادات العقوبات'"
    
    await update.message.reply_text(message)

async def list_penalties_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض عقوبات موظف"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "📝 استخدام الأمر:\n"
            "`/list_penalties <رقم_الهاتف>`\n\n"
            "مثال:\n"
            "`/list_penalties +962791234567`"
        )
        return
    
    phone_number = context.args[0]
    employee = get_employee_by_phone(phone_number)
    
    if not employee:
        await update.message.reply_text(f"❌ لم يتم العثور على موظف برقم الهاتف: {phone_number}")
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', 'غير معروف')
    
    penalties = get_employee_penalties(employee_id, active_only=False)
    
    if not penalties:
        await update.message.reply_text(
            f"📋 عقوبات الموظف: {employee_name}\n\n"
            "✅ لا توجد عقوبات مسجلة لهذا الموظف."
        )
        return
    
    message = (
        f"📋 **عقوبات الموظف:** {employee_name}\n"
        f"📱 **الهاتف:** {phone_number}\n"
        f"📅 **تاريخ التقرير:** {get_jordan_time().strftime('%Y-%m-%d')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    for i, penalty in enumerate(penalties, 1):
        status = "✅ نشطة" if penalty['is_active'] else "❌ معطلة"
        message += f"🔹 **العقوبة #{i}** ({status})\n"
        message += f"   🆔 المعرف: {penalty['id']}\n"
        message += f"   📛 النوع: {penalty.get('penalty_name', 'غير محدد')}\n"
        message += f"   📅 التاريخ: {penalty.get('penalty_date').strftime('%Y-%m-%d')}\n"
        message += f"   📝 السبب: {penalty.get('reason', 'غير محدد')}\n"
        deduction = float(penalty.get('deduction_amount', 0))
        if deduction > 0:
            message += f"   💰 الخصم: {deduction:.2f} دينار\n"
        ban_days = penalty.get('smoke_ban_days', 0)
        if ban_days > 0:
            message += f"   🚬 حظر سجائر: {ban_days} يوم\n"
        message += "\n"
    
    summary = get_employee_penalty_summary(employee_id)
    message += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **ملخص العقوبات:**\n"
        f"   ⚖️ إجمالي العقوبات: {summary['total_penalties']}\n"
        f"   ✅ عقوبات نشطة: {summary['active_penalties']}\n"
        f"   💰 إجمالي الخصومات: {summary['total_deduction']:.2f} دينار\n"
    )
    
    if 'analysis' in summary:
        message += f"   📊 التحليل: {summary['analysis']}\n"
    
    await update.message.reply_text(message)

async def all_penalties_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض جميع العقوبات النشطة"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    penalties = await get_all_active_penalties()
    await update.message.reply_text(penalties, parse_mode='Markdown')

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إرسال معلومات الاتصال"""
    user = update.message.from_user
    contact = update.message.contact
    
    if not contact:
        await update.message.reply_text("❌ لم يتم إرسال معلومات الاتصال.")
        return
    
    phone_number = contact.phone_number
    
    # التحقق من أن رقم الهاتف مصرح به
    if not verify_employee(phone_number):
        await update.message.reply_text(
            "❌ رقم الهاتف هذا غير مسجل في النظام.\n"
            "يرجى التواصل مع الإدارة لإضافة رقمك."
        )
        return
    
    # حفظ بيانات الموظف
    full_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip()
    if not full_name:
        full_name = user.first_name
        if user.last_name:
            full_name = f"{user.first_name} {user.last_name}"
    
    employee_id = save_employee(user.id, phone_number, full_name)
    
    if employee_id:
        # حفظ رقم الهاتف مؤقتاً للجلسة
        user_database[user.id] = {'phone': phone_number, 'name': full_name}
        
        # لوحة المفاتيح الرئيسية
        if is_admin(user.id):
            keyboard = [
                [KeyboardButton("تسجيل حضور 📝"), KeyboardButton("تسجيل انصراف 🚪")],
                [KeyboardButton("طلب سيجارة 🚬"), KeyboardButton("طلب استراحة ☕")],
                [KeyboardButton("طلب إذن خروج 🏠"), KeyboardButton("طلب إجازة 🌴")],
                [KeyboardButton("تقرير الحضور 📊"), KeyboardButton("تقريري الكامل 📈")],
                [KeyboardButton("🔧 مدير العقوبات")]
            ]
        else:
            keyboard = [
                [KeyboardButton("تسجيل حضور 📝"), KeyboardButton("تسجيل انصراف 🚪")],
                [KeyboardButton("طلب سيجارة 🚬"), KeyboardButton("طلب استراحة ☕")],
                [KeyboardButton("طلب إذن خروج 🏠"), KeyboardButton("طلب إجازة 🌴")],
                [KeyboardButton("تقرير الحضور 📊"), KeyboardButton("تقريري الكامل 📈")]
            ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"✅ مرحباً بك {full_name}!\n\n"
            "تم التحقق من هويتك بنجاح.\n"
            "يمكنك الآن استخدام البوت لإدارة حضورك.\n\n"
            "🔸 **الأوامر المتاحة:**\n"
            "- تسجيل حضور 📝\n"
            "- تسجيل انصراف 🚪\n"
            "- طلب سيجارة 🚬\n"
            "- طلب استراحة ☕\n"
            "- طلب إذن خروج 🏠\n"
            "- طلب إجازة 🌴\n"
            "- تقرير الحضور 📊\n"
            "- تقريري الكامل 📈\n\n"
            "أو استخدم الأوامر مباشرة:\n"
            "/check_in - تسجيل حضور\n"
            "/check_out - تسجيل انصراف\n"
            "/smoke - طلب سيجارة\n"
            "/break - طلب استراحة\n"
            "/leave - طلب إذن خروج\n"
            "/vacation - طلب إجازة\n"
            "/attendance_report - تقرير الحضور\n"
            "/full_report - تقريري الكامل",
            reply_markup=reply_markup
        )
        
        # إرسال إشعار للمديرين
        admin_message = (
            f"📱 تسجيل دخول جديد\n\n"
            f"👤 الموظف: {full_name}\n"
            f"📱 رقم الهاتف: {phone_number}\n"
            f"🆔 معرف تيليجرام: {user.id}\n"
            f"⏰ الوقت: {get_jordan_time().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await send_to_all_admins(context, admin_message)
    else:
        await update.message.reply_text(
            "❌ حدث خطأ في حفظ بياناتك.\n"
            "يرجى المحاولة مرة أخرى أو التواصل مع الإدارة."
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة ضغطات الأزرار"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("approve_"):
        request_id = int(data.split("_")[1])
        # هنا يمكنك إضافة منطق الموافقة على الطلب
        await query.edit_message_text(f"✅ تمت الموافقة على الطلب #{request_id}")
    
    elif data.startswith("reject_"):
        request_id = int(data.split("_")[1])
        # هنا يمكنك إضافة منطق رفض الطلب
        await query.edit_message_text(f"❌ تم رفض الطلب #{request_id}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء المحادثة"""
    await update.message.reply_text(
        "❌ تم إلغاء العملية.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# ==== دوال المؤقت ====
def create_progress_bar(percentage, length=10):
    """إنشاء شريط تقدم"""
    filled = int(length * percentage / 100)
    empty = length - filled
    return '█' * filled + '░' * empty

def get_time_emoji():
    """الحصول على إيموجي الوقت الحالي"""
    now = get_jordan_time()
    hour = now.hour
    
    if 5 <= hour < 12:
        return "☀️"
    elif 12 <= hour < 17:
        return "🌤️"
    elif 17 <= hour < 20:
        return "🌇"
    else:
        return "🌙"

async def update_timer(context: ContextTypes.DEFAULT_TYPE):
    """تحديث المؤقت"""
    job = context.job
    user_id, timer_type = job.data
    
    if user_id not in active_timers:
        return
    
    timer_info = active_timers[user_id]
    if timer_info['type'] != timer_type:
        return
    
    elapsed = (get_jordan_time() - timer_info['start_time']).total_seconds()
    remaining = timer_info['duration'] - elapsed
    
    if remaining <= 0:
        # إنهاء المؤقت
        await context.bot.send_message(
            chat_id=user_id,
            text=f"⏰ انتهى وقت {timer_info['name']}!\n\n"
                 f"✅ يمكنك الآن العودة للعمل."
        )
        
        # إرسال إشعار للمديرين
        employee = get_employee_by_telegram_id(user_id)
        if employee:
            employee_name = employee.get('full_name', "الموظف")
            await send_to_all_admins(
                context,
                f"⏰ انتهاء وقت {timer_info['name']}\n\n"
                f"👤 الموظف: {employee_name}\n"
                f"🕐 الوقت: {get_jordan_time().strftime('%H:%M:%S')}\n"
                f"📅 التاريخ: {get_jordan_time().strftime('%Y-%m-%d')}"
            )
        
        # حذف المؤقت
        del active_timers[user_id]
        timer_completed[user_id] = True
        return
    
    # تحويل الباقي إلى دقائق وثواني
    minutes = int(remaining // 60)
    seconds = int(remaining % 60)
    
    # تحديث الرسالة
    try:
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=timer_info['message_id'],
            text=f"⏰ {timer_info['name']}\n\n"
                 f"⏱️ الوقت المتبقي: {minutes:02d}:{seconds:02d}\n"
                 f"📊 {create_progress_bar((elapsed / timer_info['duration']) * 100)}\n\n"
                 f"{get_time_emoji()} يتم احتساب الوقت..."
        )
    except:
        pass

async def start_countdown_timer(context: ContextTypes.DEFAULT_TYPE, user_id, timer_type, duration_seconds, timer_name):
    """بدء مؤتمر عد تنازلي"""
    # إلغاء أي مؤقت موجود
    if user_id in active_timers:
        old_timer = active_timers[user_id]
        try:
            await context.bot.delete_message(user_id, old_timer['message_id'])
        except:
            pass
    
    # إرسال رسالة المؤقت الجديدة
    message = await context.bot.send_message(
        chat_id=user_id,
        text=f"⏰ {timer_name}\n\n"
             f"⏱️ الوقت المتبقي: {int(duration_seconds // 60):02d}:{int(duration_seconds % 60):02d}\n"
             f"📊 {create_progress_bar(0)}\n\n"
             f"{get_time_emoji()} يتم احتساب الوقت..."
    )
    
    # حفظ معلومات المؤقت
    active_timers[user_id] = {
        'type': timer_type,
        'start_time': get_jordan_time(),
        'duration': duration_seconds,
        'message_id': message.message_id,
        'name': timer_name
    }
    
    timer_completed[user_id] = False
    
    # جدولة تحديثات المؤقت كل ثانية
    context.job_queue.run_repeating(
        update_timer,
        interval=1,
        first=1,
        data=(user_id, timer_type),
        name=f"timer_{user_id}_{timer_type}"
    )

def save_request(employee_id, request_type):
    """حفظ طلب جديد في قاعدة البيانات"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO requests (employee_id, request_type, status, requested_at)
            VALUES (%s, %s, 'pending', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Amman')
            RETURNING id
        """, (employee_id, request_type))
        request_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"تم حفظ الطلب: نوع={request_type}, موظف_id={employee_id}, طلب_id={request_id}")
        return request_id
    except Exception as e:
        logger.error(f"خطأ في حفظ الطلب: {e}")
        return None

def get_all_employees():
    """الحصول على جميع الموظفين من قاعدة البيانات"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM employees ORDER BY full_name")
        employees = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(emp) for emp in employees] if employees else []
    except Exception as e:
        logger.error(f"خطأ في قراءة قائمة الموظفين: {e}")
        return []

def delete_employee_by_phone(phone_number):
    """حذف موظف من قاعدة البيانات باستخدام رقم الهاتف"""
    try:
        normalized = normalize_phone(phone_number)
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM employees WHERE phone_number = %s RETURNING id", (normalized,))
        deleted = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        if deleted:
            logger.info(f"تم حذف الموظف برقم الهاتف: {phone_number}")
            return True
        return False
    except Exception as e:
        logger.error(f"خطأ في حذف الموظف: {e}")
        return False

async def send_auto_daily_report(context: ContextTypes.DEFAULT_TYPE):
    """إرسال التقرير اليومي التلقائي"""
    try:
        today = get_jordan_time().date()
        records = get_daily_attendance_report(today)
        
        if not records:
            return
        
        message = (
            f"📊 التقرير اليومي التلقائي\n"
            f"📅 {today.strftime('%Y-%m-%d')}\n\n"
        )
        
        present_count = 0
        absent_count = 0
        late_count = 0
        
        for record in records:
            name = record['full_name']
            check_in = record['check_in_time']
            status = record['status']
            
            message += f"• {name}: "
            
            if check_in:
                present_count += 1
                message += f"حضر {check_in.strftime('%H:%M')}"
                if record['is_late']:
                    late_count += 1
                    message += " ⚠️"
            elif status == 'absent':
                absent_count += 1
                message += "❌ غائب"
            else:
                absent_count += 1
                message += "❌ غائب"
            
            message += "\n"
        
        message += f"\n📊 الإحصائيات:\n"
        message += f"✅ حاضر: {present_count}\n"
        message += f"❌ غائب: {absent_count}\n"
        if late_count > 0:
            message += f"⚠️ متأخرين: {late_count}\n"
        
        await send_to_all_admins(context, message)
        logger.info(f"تم إرسال التقرير اليومي التلقائي لليوم {today}")
    except Exception as e:
        logger.error(f"خطأ في إرسال التقرير اليومي التلقائي: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الأخطاء"""
    logger.error(f"حدث خطأ: {context.error}")
    
    try:
        if update and update.message:
            await update.message.reply_text(
                "❌ حدث خطأ غير متوقع.\n"
                "يرجى المحاولة مرة أخرى لاحقاً."
            )
    except:
        pass

def load_employees_from_database():
    """تحميل الموظفين المصرح لهم من قاعدة البيانات"""
    try:
        employees = get_all_employees()
        for emp in employees:
            phone = emp.get('phone_number')
            if phone and phone not in authorized_phones:
                authorized_phones.append(phone)
        logger.info(f"تم تحميل {len(employees)} موظف من قاعدة البيانات")
        return len(employees)
    except Exception as e:
        logger.error(f"خطأ في تحميل الموظفين من قاعدة البيانات: {e}")
        return 0

# ==== دالة main الرئيسية ====
def main():
    """بدء البوت"""
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not found in environment variables!")
        print("Please set your bot token in the Secrets tab.")
        return
    
    print("🚀 بدء بوت إدارة حضور الموظفين مع نظام العقوبات الذكي...")
    print("=" * 50)
    print(f"👑 عدد المديرين الرئيسيين: {len(ADMIN_IDS)}")
    
    print(f"\n🔹 إعدادات السجائر:")
    print(f"   • عدد السجائر اليومية: {MAX_DAILY_SMOKES}")
    print(f"   • الفجوة بين السجائر: {MIN_GAP_BETWEEN_SMOKES_HOURS} ساعة")
    print(f"   • مدة السيجارة: {SMOKE_BREAK_DURATION} دقائق")
    print(f"   • وقت السماح بالسيجارة: بعد الساعة {SMOKE_ALLOWED_AFTER_HOUR}:00 صباحاً")
    
    print(f"\n🔹 إعدادات ساعات العمل:")
    print(f"   • بداية الدوام: {WORK_START_HOUR}:{WORK_START_MINUTE:02d}")
    print(f"   • ساعات العمل الأساسية: {WORK_REGULAR_HOURS} ساعة ({WORK_REGULAR_MINUTES} دقيقة)")
    print(f"   • الإضافي يبدأ بعد: {WORK_OVERTIME_START_HOUR}:00")
    print(f"   • فترة السماح للتأخير: {LATE_GRACE_PERIOD_MINUTES} دقيقة")
    
    print(f"\n⚖️ نظام العقوبات الذكي:")
    print(f"   • مستويات العقوبات: {len(PENALTY_LEVELS)} مستوى")
    print(f"   • أنواع المخالفات: {len(PENALTY_TYPES)} نوع")
    print(f"   • نصائح ذكية: {len(PENALTY_TIPS)} نصيحة")
    print("=" * 50)
    
    initialize_database_tables()
    loaded_count = load_employees_from_database()
    print(f"✅ تم تحميل {loaded_count} موظف من قاعدة البيانات")
    
    application = Application.builder().token(BOT_TOKEN).build()

    try:
        application.bot.delete_webhook()
        application.bot.get_updates(offset=-1, timeout=1) 
        logger.info("تم مسح الـ Webhook والرسائل العالقة بنجاح.")
    except Exception as e:
        logger.warning(f"لم نتمكن من مسح الـ Webhook/الرسائل العالقة: {e}") 

    # محادثة إدارة العقوبات
    penalty_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("penalty_manager", penalty_manager_command)],
        states={
            PENALTY_MENU: [CallbackQueryHandler(handle_penalty_menu)],
            SELECT_EMPLOYEE_FOR_PENALTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_employee_for_penalty),
                CallbackQueryHandler(select_employee_callback)
            ],
            SELECT_PENALTY_TYPE: [CallbackQueryHandler(select_penalty_type)],
            ENTER_PENALTY_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_penalty_details)],
            CONFIRM_PENALTY: [
                CallbackQueryHandler(handle_penalty_customization),
                CallbackQueryHandler(confirm_penalty)
            ],
            EDIT_PENALTY_CUSTOM_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_penalty_custom_amount)],
            SELECT_PENALTY_TO_EDIT: [CallbackQueryHandler(select_penalty_to_edit)],
            EDIT_PENALTY_AMOUNT: [
                CallbackQueryHandler(handle_penalty_edit),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_penalty_edit)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # محادثات أخرى
    leave_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("leave", leave_request)],
        states={
            LEAVE_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_leave_reason)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    vacation_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("vacation", vacation_request)],
        states={
            VACATION_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_vacation_reason)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    # إضافة جميع المعالجات
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("my_id", my_id_command))
    application.add_handler(CommandHandler("check_in", check_in_command))
    application.add_handler(CommandHandler("check_out", check_out_command))
    application.add_handler(CommandHandler("attendance_report", attendance_report_command))
    application.add_handler(CommandHandler("full_report", full_report_command))
    application.add_handler(CommandHandler("my_penalties", my_penalties_command))
    application.add_handler(CommandHandler("smoke", smoke_request))
    application.add_handler(CommandHandler("break", break_request))
    application.add_handler(leave_conv_handler)
    application.add_handler(vacation_conv_handler)
    
    # أوامر إدارة العقوبات
    application.add_handler(penalty_conv_handler)
    application.add_handler(CommandHandler("penalty_help", smart_penalty_help))
    application.add_handler(CommandHandler("penalty_settings", penalty_settings_command))
    application.add_handler(CommandHandler("penalty_stats", get_penalty_statistics))
    application.add_handler(CommandHandler("list_penalties", list_penalties_command))
    application.add_handler(CommandHandler("all_penalties", all_penalties_command))
    
    # أوامر الإدارة الأخرى
    application.add_handler(CommandHandler("list_employees", list_employees))
    application.add_handler(CommandHandler("add_employee", add_employee))
    application.add_handler(CommandHandler("remove_employee", remove_employee))
    application.add_handler(CommandHandler("daily_report", daily_report_command))
    application.add_handler(CommandHandler("weekly_report", weekly_report_command))
    application.add_handler(CommandHandler("list_admins", list_admins_command))
    application.add_handler(CommandHandler("add_admin", add_admin_command))
    application.add_handler(CommandHandler("remove_admin", remove_admin_command))
    
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    application.add_error_handler(error_handler)
    
    # جدولة التقارير التلقائية
    job_queue = application.job_queue
    if job_queue:
        daily_report_time = get_jordan_time().replace(hour=19, minute=0, second=0, microsecond=0)
        job_queue.run_daily(
            send_auto_daily_report,
            time=daily_report_time.time(),
            days=(0, 1, 2, 3, 4, 5, 6),
            name="daily_attendance_report"
        )
        logger.info("تم جدولة التقرير اليومي التلقائي للساعة 7:00 مساءً")
        print("✅ تم جدولة التقرير اليومي التلقائي")
    
    print("\n✅ البوت يعمل الآن مع نظام العقوبات الذكي!")
    print("📱 أرسل /start للبوت للبدء")
    print("👑 المديرين يمكنهم استخدام:")
    print("   /penalty_manager - لفتح مدير العقوبات")
    print("   /penalty_help - للمساعدة التفصيلية")
    print("=" * 50)
    
    while True:
        try:
            application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        except KeyboardInterrupt:
            print("\n⏹️  إيقاف البوت...")
            print("⏹️  Bot stopped by user")
            break
        except Exception as e:
            logger.error(f"⚠️  خطأ في البوت: {e}")
            logger.error("🔄 إعادة تشغيل البوت بعد 5 ثواني...")
            print(f"\n⚠️  حدث خطأ: {e}")
            print("🔄 سيتم إعادة تشغيل البوت تلقائياً بعد 5 ثواني...")
            import time
            time.sleep(5)
            print("🚀 إعادة تشغيل البوت...")
            continue

if __name__ == '__main__':
    main()