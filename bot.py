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
ADMIN_IDS = [1465191277]  # أضف معرفات المديرين الإضافيين مثل: [1465191277, 987654321, 123456789]

authorized_phones = [
    '+962786644106'
]

user_database = {}
daily_smoke_count = {}

MAX_DAILY_SMOKES = 6

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

def record_check_out(employee_id):
    """تسجيل انصراف الموظف"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        now = get_jordan_time()
        today = now.date()
        
        cur.execute("""
            SELECT check_in_time, check_out_time, total_work_hours, overtime_hours FROM attendance
            WHERE employee_id = %s AND date = %s
        """, (employee_id, today))
        
        result = cur.fetchone()
        if not result:
            cur.close()
            conn.close()
            return {'success': False, 'error': 'لم يتم تسجيل الحضور اليوم'}
        
        check_in_time, existing_checkout, existing_hours, existing_overtime = result
        
        if existing_checkout:
            cur.close()
            conn.close()
            return {
                'success': False,
                'error': 'already_checked_out',
                'check_in_time': check_in_time,
                'check_out_time': existing_checkout,
                'total_work_hours': float(existing_hours) if existing_hours else 0,
                'overtime_hours': float(existing_overtime) if existing_overtime else 0
            }
        
        work_hours = (now - check_in_time).total_seconds() / 3600
        
        if work_hours >= 1.0:
            work_hours -= 0.5
        
        work_hours = max(0, work_hours)
        
        regular_hours = min(work_hours, WORK_REGULAR_HOURS)
        overtime_hours = max(0, work_hours - WORK_REGULAR_HOURS)
        
        cur.execute("""
            UPDATE attendance
            SET check_out_time = %s, total_work_hours = %s, overtime_hours = %s
            WHERE employee_id = %s AND date = %s
            RETURNING check_in_time, check_out_time, total_work_hours, overtime_hours
        """, (now, round(work_hours, 2), round(overtime_hours, 2), employee_id, today))
        
        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        return {
            'success': True,
            'check_in_time': result[0],
            'check_out_time': result[1],
            'total_work_hours': float(result[2]),
            'overtime_hours': float(result[3])
        }
    except Exception as e:
        logger.error(f"خطأ في تسجيل الانصراف: {e}")
        return {'success': False, 'error': str(e)}

def add_warning(employee_id, warning_type, reason):
    """إضافة إنذار للموظف"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        today = get_jordan_time().date()
        
        cur.execute("""
            INSERT INTO warnings (employee_id, warning_type, warning_reason, date)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (employee_id, warning_type, reason, today))
        
        warning_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        
        return {'success': True, 'warning_id': warning_id}
    except Exception as e:
        logger.error(f"خطأ في إضافة الإنذار: {e}")
        return {'success': False, 'error': str(e)}

def record_absence(employee_id, absence_type, reason=None):
    """تسجيل غياب الموظف"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        today = get_jordan_time().date()
        
        cur.execute("""
            INSERT INTO absences (employee_id, date, absence_type, reason)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (employee_id, date)
            DO UPDATE SET absence_type = EXCLUDED.absence_type, reason = EXCLUDED.reason
            RETURNING id
        """, (employee_id, today, absence_type, reason))
        
        absence_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        
        return {'success': True, 'absence_id': absence_id}
    except Exception as e:
        logger.error(f"خطأ في تسجيل الغياب: {e}")
        return {'success': False, 'error': str(e)}

def get_attendance_today(employee_id):
    """الحصول على سجل الحضور اليوم"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        today = get_jordan_time().date()
        
        cur.execute("""
            SELECT check_in_time, check_out_time, is_late, late_minutes, total_work_hours, overtime_hours
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
                'total_work_hours': float(result[4]) if result[4] else 0,
                'overtime_hours': float(result[5]) if result[5] else 0
            }
        return None
    except Exception as e:
        logger.error(f"خطأ في الحصول على سجل الحضور: {e}")
        return None

def count_missed_checkins(employee_id):
    """عدد مرات عدم تسجيل الحضور"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT COUNT(*) FROM warnings
            WHERE employee_id = %s AND warning_type = 'missed_checkin'
        """, (employee_id,))
        
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        
        return count
    except Exception as e:
        logger.error(f"خطأ في حساب مرات عدم تسجيل الحضور: {e}")
        return 0

def get_employee_attendance_report(employee_id, days=7):
    """الحصول على تقرير حضور الموظف لعدد معين من الأيام"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        end_date = get_jordan_time().date()
        start_date = end_date - timedelta(days=days-1)
        
        cur.execute("""
            SELECT date, check_in_time, check_out_time, is_late, late_minutes, 
                   total_work_hours, overtime_hours, status
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
                   a.is_late, a.late_minutes, a.total_work_hours, a.overtime_hours, a.status
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
                   SUM(COALESCE(a.total_work_hours, 0)) as total_hours,
                   SUM(COALESCE(a.overtime_hours, 0)) as total_overtime,
                   AVG(CASE WHEN a.total_work_hours > 0 THEN a.total_work_hours END) as avg_hours
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

def save_employee(telegram_id, phone_number, full_name):
    """حفظ أو تحديث بيانات الموظف في قاعدة البيانات"""
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
        logger.info(f"تم حفظ بيانات الموظف: {full_name} ({phone_number}) - ID: {employee_id}")
        return employee_id
    except Exception as e:
        logger.error(f"خطأ في حفظ بيانات الموظف: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return None

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

def update_employee_by_phone(old_phone, new_phone, new_name):
    """تحديث بيانات موظف في قاعدة البيانات باستخدام رقم الهاتف القديم"""
    try:
        old_normalized = normalize_phone(old_phone)
        new_normalized = normalize_phone(new_phone)
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE employees 
            SET phone_number = %s, full_name = %s, last_active = CURRENT_TIMESTAMP
            WHERE phone_number = %s
            RETURNING id
        """, (new_normalized, new_name, old_normalized))
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        if updated:
            logger.info(f"تم تحديث بيانات الموظف: {old_phone} → {new_phone}")
            return True
        return False
    except Exception as e:
        logger.error(f"خطأ في تحديث بيانات الموظف: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return False

def add_employee_to_authorized(phone_number):
    """إضافة رقم هاتف إلى قائمة الموظفين المصرح لهم"""
    normalized_phone = normalize_phone(phone_number)
    if not phone_number.startswith('+'):
        phone_number = '+' + phone_number
    if phone_number not in authorized_phones:
        authorized_phones.append(phone_number)
        logger.info(f"تم إضافة رقم الهاتف إلى القائمة: {phone_number}")
        return True
    return False

def remove_employee_from_authorized(phone_number):
    """حذف رقم هاتف من قائمة الموظفين المصرح لهم"""
    normalized_input = normalize_phone(phone_number)
    for auth_phone in authorized_phones[:]:
        if normalize_phone(auth_phone) == normalized_input:
            authorized_phones.remove(auth_phone)
            logger.info(f"تم حذف رقم الهاتف من القائمة: {auth_phone}")
            return True
    return False

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

def update_request_status(request_id, status, notes=None):
    """تحديث حالة الطلب في قاعدة البيانات"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE requests 
            SET status = %s, responded_at = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Amman', notes = %s
            WHERE id = %s
        """, (status, notes, request_id))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"تم تحديث حالة الطلب {request_id} إلى: {status}")
        return True
    except Exception as e:
        logger.error(f"خطأ في تحديث حالة الطلب: {e}")
        return False

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

def get_jordan_time():
    """الحصول على الوقت الحالي بتوقيت الأردن"""
    return datetime.now(JORDAN_TZ)

def get_today_date():
    """الحصول على تاريخ اليوم بتوقيت الأردن"""
    return get_jordan_time().strftime("%Y-%m-%d")

def get_smoke_count(user_id):
    """الحصول على عدد السجائر المستخدمة اليوم"""
    today = get_today_date()
    user_id_str = str(user_id)
    if user_id_str not in daily_smoke_count:
        daily_smoke_count[user_id_str] = {}
    count = daily_smoke_count[user_id_str].get(today, 0)
    logger.info(f"قراءة عداد السجائر للمستخدم {user_id_str} في {today}: {count}")
    return count

def increment_smoke_count(user_id):
    """زيادة عدد السجائر المستخدمة اليوم"""
    today = get_today_date()
    user_id_str = str(user_id)
    if user_id_str not in daily_smoke_count:
        daily_smoke_count[user_id_str] = {}
    daily_smoke_count[user_id_str][today] = daily_smoke_count[user_id_str].get(today, 0) + 1
    save_smoke_data()
    logger.info(f"تم زيادة عداد السجائر للمستخدم {user_id_str} في {today}: {daily_smoke_count[user_id_str][today]}")

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

def get_user_phone(user_id):
    """الحصول على رقم هاتف المستخدم من قاعدة البيانات"""
    employee = get_employee_by_telegram_id(user_id)
    if employee:
        return employee.get('phone_number')
    return user_database.get(user_id, {}).get('phone')

def get_employee_name(user_id, default_name="المستخدم"):
    """الحصول على اسم الموظف من قاعدة البيانات بدلاً من Telegram"""
    employee = get_employee_by_telegram_id(user_id)
    if employee and employee.get('full_name'):
        return employee.get('full_name')
    return default_name

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دالة البداية - طلب التحقق من هوية المستخدم"""
    user = update.message.from_user
    user_first_name = get_employee_name(user.id)
    
    user_phone = get_user_phone(user.id)
    
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
            "  (5 دقائق، حد أقصى 6 سجائر/يوم، فجوة 1.5 ساعة)\n\n"
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
                "/edit_details - تعديل تفاصيل موظف 📋\n\n"
            )
        
        welcome_message += "━━━━━━━━━━━━━━━━━\n✨ يمكنك الآن استخدام جميع الأوامر!"
        
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

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض رسالة المساعدة"""
    user = update.message.from_user
    
    help_text = (
        "📚 قائمة الأوامر:\n\n"
        "🔹 الحضور والانصراف:\n"
        "/check_in - تسجيل الحضور (إلزامي في بداية الدوام)\n"
        "/check_out - تسجيل الانصراف (إلزامي في نهاية الدوام)\n"
        "/attendance_report - عرض تقرير حضورك (آخر 7 أيام)\n\n"
        "🔹 الاستراحات:\n"
        "/smoke - طلب استراحة تدخين (5 دقائق، حد أقصى 6 سجائر/يوم، فجوة 1.5 ساعة)\n"
        "/break - طلب استراحة غداء (30 دقيقة، مرة واحدة في اليوم)\n\n"
        "🔹 الإجازات:\n"
        "/leave - طلب مغادرة العمل (مع سبب المغادرة)\n"
        "/vacation - طلب عطلة (مع سبب وعذر)\n\n"
        "🔹 أوامر مساعدة:\n"
        "/start - بدء البوت\n"
        "/help - عرض هذه الرسالة\n"
        "/my_id - عرض معرف Telegram الخاص بك\n\n"
    )
    
    if is_admin(user.id):
        help_text += (
            "🔸 أوامر المدير:\n"
            "/list_employees - عرض جميع الموظفين المسجلين\n"
            "/add_employee - إضافة موظف جديد\n"
            "/remove_employee - حذف موظف من النظام\n"
            "/edit_details - تعديل تفاصيل موظف (الاسم، الهاتف، العمر، الوظيفة، القسم، التاريخ)\n"
            "/daily_report - تقرير الحضور اليومي لجميع الموظفين\n"
            "/weekly_report - تقرير الحضور الأسبوعي لجميع الموظفين\n"
            "/list_admins - عرض قائمة المديرين الحاليين\n"
            "/add_admin - إضافة مدير جديد (للمدير الرئيسي)\n"
            "/remove_admin - حذف مدير (للمدير الرئيسي)\n\n"
        )
    
    help_text += (
        "ملاحظة: يجب أن يكون رقم هاتفك مسجلاً في النظام لاستخدام الطلبات.\n"
        "استخدم /start لمشاركة رقم هاتفك."
    )
    await update.message.reply_text(help_text)

async def my_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض معرف Telegram للمستخدم"""
    user = update.message.from_user
    user_first_name = get_employee_name(user.id)
    
    message = (
        f"🆔 معلومات حسابك:\n\n"
        f"👤 الاسم: {user_first_name}\n"
        f"🔢 معرف Telegram: `{user.id}`\n\n"
        "📋 نسخ المعرف:\n"
        "اضغط على الرقم أعلاه لنسخه\n\n"
    )
    
    if is_admin(user.id):
        message += "✅ أنت مسجل كمدير في النظام"
    else:
        message += "💼 حسابك: موظف"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المديرين الحاليين (للمدير فقط)"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM admins ORDER BY added_at")
        admins = cur.fetchall()
        cur.close()
        conn.close()
        
        message = "👨‍💼 قائمة المديرين المسجلين في النظام:\n\n"
        
        for i, admin in enumerate(admins, 1):
            is_current = "← (أنت)" if admin['telegram_id'] == user.id else ""
            admin_type = "⭐ مدير رئيسي" if admin['is_super_admin'] else "👤 مدير"
            message += f"{i}. {admin_type}\n"
            message += f"   معرف Telegram: {admin['telegram_id']} {is_current}\n"
            if admin['added_at']:
                message += f"   📅 تاريخ الإضافة: {admin['added_at'].strftime('%Y-%m-%d')}\n"
            message += "\n"
        
        message += (
            "━━━━━━━━━━━━━━━━━\n"
            "💡 لإضافة مدير جديد:\n"
            "استخدم: /add_admin معرف_المدير\n\n"
            "مثال: /add_admin 123456789\n\n"
            f"📊 إجمالي المديرين: {len(admins)}"
        )
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"خطأ في عرض قائمة المديرين: {e}")
        await update.message.reply_text("❌ حدث خطأ أثناء عرض قائمة المديرين.")

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة مدير جديد (للمدير الرئيسي فقط)"""
    user = update.message.from_user
    
    if not is_super_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير الرئيسي فقط.")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text(
            "❌ استخدام خاطئ. الصيغة الصحيحة:\n"
            "/add_admin معرف_المدير\n\n"
            "مثال:\n"
            "/add_admin 123456789\n\n"
            "💡 يمكن للشخص الحصول على معرفه بإرسال /my_id للبوت"
        )
        return
    
    try:
        new_admin_id = int(context.args[0])
        
        if is_admin(new_admin_id):
            await update.message.reply_text("⚠️ هذا الشخص مدير بالفعل!")
            return
        
        if add_admin_to_db(new_admin_id, added_by=user.id):
            await update.message.reply_text(
                f"✅ تم إضافة المدير بنجاح!\n\n"
                f"معرف المدير الجديد: {new_admin_id}\n"
                f"تمت الإضافة بواسطة: {user.first_name or user.id}\n\n"
                f"🎉 الآن يمكن للمدير الجديد استخدام جميع الأوامر الإدارية!"
            )
            logger.info(f"تم إضافة مدير جديد {new_admin_id} بواسطة {user.id}")
            
            # إرسال إشعار للمدير الجديد
            try:
                await context.bot.send_message(
                    chat_id=new_admin_id,
                    text=f"🎉 مبروك!\n\nتمت إضافتك كمدير في بوت إدارة حضور الموظفين.\n\n"
                         f"يمكنك الآن استخدام /help لعرض الأوامر الإدارية المتاحة لك."
                )
            except:
                pass
        else:
            await update.message.reply_text("❌ حدث خطأ أثناء إضافة المدير.")
    
    except ValueError:
        await update.message.reply_text("❌ المعرف غير صحيح. يجب أن يكون رقماً.")

async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف مدير (للمدير الرئيسي فقط)"""
    user = update.message.from_user
    
    if not is_super_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير الرئيسي فقط.")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text(
            "❌ استخدام خاطئ. الصيغة الصحيحة:\n"
            "/remove_admin معرف_المدير\n\n"
            "مثال:\n"
            "/remove_admin 123456789"
        )
        return
    
    try:
        admin_id_to_remove = int(context.args[0])
        
        if admin_id_to_remove == user.id:
            await update.message.reply_text("❌ لا يمكنك حذف نفسك!")
            return
        
        if admin_id_to_remove in ADMIN_IDS:
            await update.message.reply_text("❌ لا يمكن حذف المديرين الرئيسيين!")
            return
        
        if remove_admin_from_db(admin_id_to_remove):
            await update.message.reply_text(
                f"✅ تم حذف المدير بنجاح!\n\n"
                f"معرف المدير المحذوف: {admin_id_to_remove}"
            )
            logger.info(f"تم حذف المدير {admin_id_to_remove} بواسطة {user.id}")
            
            # إرسال إشعار للمدير المحذوف
            try:
                await context.bot.send_message(
                    chat_id=admin_id_to_remove,
                    text="⚠️ تم إزالة صلاحياتك الإدارية من بوت إدارة حضور الموظفين."
                )
            except:
                pass
        else:
            await update.message.reply_text("❌ لم يتم العثور على المدير أو لا يمكن حذفه.")
    
    except ValueError:
        await update.message.reply_text("❌ المعرف غير صحيح. يجب أن يكون رقماً.")

async def list_employees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض جميع الموظفين المسجلين (للمدير فقط)"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    employees = get_all_employees()
    
    if not employees:
        await update.message.reply_text("📭 لا يوجد موظفين مسجلين في النظام حالياً.")
        return
    
    message = "👥 قائمة الموظفين المسجلين:\n\n"
    for i, emp in enumerate(employees, 1):
        message += (
            f"{i}. {emp['full_name']}\n"
            f"   📱 الهاتف: {emp['phone_number']}\n"
            f"   🆔 معرف Telegram: {emp['telegram_id']}\n"
            f"   📅 آخر نشاط: {emp.get('last_active', 'غير متوفر')}\n\n"
        )
    
    await update.message.reply_text(message)

async def add_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة موظف جديد (للمدير فقط)"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ استخدام خاطئ. الصيغة الصحيحة:\n"
            "/add_employee رقم_الهاتف الاسم_الكامل\n\n"
            "مثال:\n"
            "/add_employee +962791234567 أحمد محمد"
        )
        return
    
    phone_number = context.args[0]
    full_name = ' '.join(context.args[1:])
    
    if not phone_number.startswith('+'):
        phone_number = '+' + phone_number
    
    existing = get_employee_by_phone(phone_number)
    if existing:
        if not verify_employee(phone_number):
            add_employee_to_authorized(phone_number)
            await update.message.reply_text(
                f"✅ تم تفعيل الموظف!\n\n"
                f"👤 الاسم: {existing['full_name']}\n"
                f"📱 الهاتف: {existing['phone_number']}\n\n"
                f"الموظف كان مسجلاً في قاعدة البيانات، تم إضافته الآن إلى قائمة الموظفين المصرح لهم.\n"
                f"يمكنه الآن استخدام جميع أوامر البوت! ✨"
            )
            logger.info(f"تم تفعيل موظف موجود: {existing['full_name']} - {phone_number}")
        else:
            await update.message.reply_text(
                f"⚠️ هذا الموظف مسجل ومفعّل بالفعل!\n\n"
                f"👤 الاسم: {existing['full_name']}\n"
                f"📱 الهاتف: {existing['phone_number']}\n\n"
                f"✅ يمكنه استخدام البوت بشكل طبيعي."
            )
        return
    
    employee_id = save_employee(None, phone_number, full_name)
    
    if employee_id:
        add_employee_to_authorized(phone_number)
        await update.message.reply_text(
            f"✅ تم إضافة الموظف بنجاح!\n\n"
            f"👤 الاسم: {full_name}\n"
            f"📱 الهاتف: {phone_number}\n"
            f"🆔 معرف قاعدة البيانات: {employee_id}\n\n"
            f"سيتم تحديث معرف Telegram الخاص به عند استخدامه للبوت لأول مرة."
        )
        logger.info(f"تم إضافة موظف جديد إلى قاعدة البيانات: {full_name} - {phone_number} (ID: {employee_id})")
    else:
        await update.message.reply_text("❌ حدث خطأ أثناء إضافة الموظف إلى قاعدة البيانات. يرجى المحاولة مرة أخرى.")

async def remove_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف موظف من النظام (للمدير فقط)"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    if len(context.args) != 1:
        await update.message.reply_text(
            "❌ استخدام خاطئ. الصيغة الصحيحة:\n"
            "/remove_employee رقم_الهاتف\n\n"
            "مثال:\n"
            "/remove_employee +962791234567"
        )
        return
    
    phone_number = context.args[0]
    
    if not phone_number.startswith('+'):
        phone_number = '+' + phone_number
    
    employee = get_employee_by_phone(phone_number)
    
    if not employee:
        await update.message.reply_text(
            f"⚠️ لم يتم العثور على موظف برقم الهاتف: {phone_number}"
        )
        return
    
    if delete_employee_by_phone(phone_number):
        remove_employee_from_authorized(phone_number)
        await update.message.reply_text(
            f"✅ تم حذف الموظف بنجاح!\n\n"
            f"الاسم: {employee['full_name']}\n"
            f"الهاتف: {employee['phone_number']}"
        )
        logger.info(f"تم حذف الموظف: {employee['full_name']} - {phone_number}")
    else:
        await update.message.reply_text("❌ حدث خطأ أثناء حذف الموظف. يرجى المحاولة مرة أخرى.")

async def edit_details_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة الموظفين لتعديل تفاصيلهم (للمدير فقط)"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    employees = get_all_employees()
    
    if not employees:
        await update.message.reply_text("📭 لا يوجد موظفين مسجلين في النظام حالياً.")
        return
    
    keyboard = []
    for emp in employees:
        keyboard.append([InlineKeyboardButton(
            f"👤 {emp['full_name']} - {emp['phone_number']}", 
            callback_data=f"editdetail_{emp['id']}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "✏️ اختر الموظف الذي تريد تعديل تفاصيله:\n\n"
        "اضغط على اسم الموظف لعرض تفاصيله:",
        reply_markup=reply_markup
    )

async def show_employee_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تفاصيل الموظف مع أزرار التعديل"""
    query = update.callback_query
    await query.answer()
    
    try:
        # استخراج employee_id من callback_data بشكل آمن
        callback_data = query.data
        if not callback_data.startswith('editdetail_'):
            await query.edit_message_text("❌ بيانات غير صالحة.")
            return ConversationHandler.END
        
        employee_id = int(callback_data.split('_')[1])
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # إضافة الحقول الجديدة إذا لم تكن موجودة
        try:
            cur.execute("""
                ALTER TABLE employees 
                ADD COLUMN IF NOT EXISTS age INTEGER,
                ADD COLUMN IF NOT EXISTS job_title VARCHAR(100),
                ADD COLUMN IF NOT EXISTS department VARCHAR(100),
                ADD COLUMN IF NOT EXISTS hire_date DATE
            """)
            conn.commit()
        except Exception as alter_error:
            logger.warning(f"قد تكون الحقول موجودة بالفعل: {alter_error}")
        
        cur.execute("SELECT * FROM employees WHERE id = %s", (employee_id,))
        employee = cur.fetchone()
        cur.close()
        conn.close()
        
        if not employee:
            await query.edit_message_text("❌ خطأ: لم يتم العثور على الموظف.")
            return ConversationHandler.END
        
        context.user_data['editing_employee_id'] = employee_id
        
        # تنسيق البيانات لعرضها
        age = employee.get('age') or 'غير محدد'
        job_title = employee.get('job_title') or 'غير محددة'
        department = employee.get('department') or 'غير محدد'
        hire_date = employee.get('hire_date') or 'غير محدد'
        
        if hire_date != 'غير محدد':
            hire_date = hire_date.strftime('%Y-%m-%d')
        
        # عرض التفاصيل الحالية
        message = (
            f"📋 تفاصيل الموظف:\n\n"
            f"👤 الاسم: {employee['full_name']}\n"
            f"📱 الهاتف: {employee['phone_number']}\n"
            f"🎂 العمر: {age}\n"
            f"💼 الوظيفة: {job_title}\n"
            f"🏢 القسم: {department}\n"
            f"📅 تاريخ التوظيف: {hire_date}\n\n"
            f"اختر التفصيل الذي تريد تعديله:"
        )
        
        keyboard = [
            [InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"editfield_name_{employee_id}")],
            [InlineKeyboardButton("📱 تعديل رقم الهاتف", callback_data=f"editfield_phone_{employee_id}")],
            [InlineKeyboardButton("🎂 تعديل العمر", callback_data=f"editfield_age_{employee_id}")],
            [InlineKeyboardButton("💼 تعديل الوظيفة", callback_data=f"editfield_job_{employee_id}")],
            [InlineKeyboardButton("🏢 تعديل القسم", callback_data=f"editfield_dept_{employee_id}")],
            [InlineKeyboardButton("📅 تعديل تاريخ التوظيف", callback_data=f"editfield_hire_{employee_id}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_edit")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup)
        
        return EDIT_DETAIL_SELECT
        
    except Exception as e:
        logger.error(f"خطأ في عرض تفاصيل الموظف: {e}")
        await query.edit_message_text("❌ حدث خطأ أثناء عرض التفاصيل.")
        return ConversationHandler.END

async def select_field_to_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار الحقل المراد تعديله"""
    query = update.callback_query
    await query.answer()
    
    try:
        if query.data == "cancel_edit":
            await query.edit_message_text("❌ تم إلغاء العملية.")
            context.user_data.clear()
            return ConversationHandler.END
        
        parts = query.data.split('_')
        if len(parts) < 3:
            await query.edit_message_text("❌ بيانات غير صالحة.")
            return ConversationHandler.END
        
        field_type = parts[1]
        employee_id = int(parts[2])
        
        context.user_data['editing_field'] = field_type
        context.user_data['editing_employee_id'] = employee_id
        
        field_names = {
            'name': 'الاسم الكامل',
            'phone': 'رقم الهاتف',
            'age': 'العمر',
            'job': 'الوظيفة',
            'dept': 'القسم',
            'hire': 'تاريخ التوظيف (YYYY-MM-DD)'
        }
        
        field_name = field_names.get(field_type, 'التفصيل')
        
        await query.edit_message_text(
            f"✏️ تعديل {field_name}\n\n"
            f"📝 أرسل القيمة الجديدة:\n\n"
            f"أرسل /cancel للإلغاء."
        )
        
        return EDIT_DETAIL_INPUT
        
    except Exception as e:
        logger.error(f"خطأ في اختيار الحقل: {e}")
        await query.edit_message_text("❌ حدث خطأ أثناء معالجة الطلب.")
        return ConversationHandler.END

async def receive_new_detail_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال القيمة الجديدة وتحديثها"""
    try:
        new_value = update.message.text.strip()
        field_type = context.user_data.get('editing_field')
        employee_id = context.user_data.get('editing_employee_id')
        
        if not field_type or not employee_id:
            await update.message.reply_text("❌ جلسة العمل انتهت. يرجى البدء من جديد.")
            context.user_data.clear()
            return ConversationHandler.END
        
        # التحقق من صحة القيمة
        if field_type == 'age':
            try:
                age = int(new_value)
                if age < 16 or age > 100:
                    await update.message.reply_text(
                        "⚠️ العمر غير صالح. يجب أن يكون بين 16 و 100.\n\n"
                        "أرسل العمر الجديد أو /cancel للإلغاء:"
                    )
                    return EDIT_DETAIL_INPUT
                new_value = age
            except ValueError:
                await update.message.reply_text(
                    "⚠️ العمر يجب أن يكون رقماً.\n\n"
                    "أرسل العمر الجديد أو /cancel للإلغاء:"
                )
                return EDIT_DETAIL_INPUT
        
        elif field_type == 'hire':
            try:
                from datetime import datetime
                hire_date = datetime.strptime(new_value, '%Y-%m-%d').date()
                new_value = hire_date
            except ValueError:
                await update.message.reply_text(
                    "⚠️ التاريخ غير صالح. يجب أن يكون بصيغة YYYY-MM-DD\n"
                    "مثال: 2024-01-15\n\n"
                    "أرسل التاريخ الجديد أو /cancel للإلغاء:"
                )
                return EDIT_DETAIL_INPUT
        
        elif field_type == 'phone':
            if not new_value.startswith('+'):
                new_value = '+' + new_value
        
        # تحديث قاعدة البيانات
        conn = get_db_connection()
        cur = conn.cursor()
        
        field_mapping = {
            'name': 'full_name',
            'phone': 'phone_number',
            'age': 'age',
            'job': 'job_title',
            'dept': 'department',
            'hire': 'hire_date'
        }
        
        db_field = field_mapping.get(field_type)
        
        if not db_field:
            await update.message.reply_text("❌ نوع الحقل غير صالح.")
            return ConversationHandler.END
        
        cur.execute(
            f"UPDATE employees SET {db_field} = %s WHERE id = %s",
            (new_value, employee_id)
        )
        conn.commit()
        
        # جلب التفاصيل المحدثة
        cur.execute("SELECT * FROM employees WHERE id = %s", (employee_id,))
        employee = cur.fetchone()
        cur.close()
        conn.close()
        
        field_names_ar = {
            'name': 'الاسم',
            'phone': 'رقم الهاتف',
            'age': 'العمر',
            'job': 'الوظيفة',
            'dept': 'القسم',
            'hire': 'تاريخ التوظيف'
        }
        
        await update.message.reply_text(
            f"✅ تم تحديث {field_names_ar.get(field_type)} بنجاح!\n\n"
            f"القيمة الجديدة: {new_value}"
        )
        
        logger.info(f"تم تحديث {field_names_ar.get(field_type)} للموظف ID {employee_id}: {new_value}")
        
        context.user_data.clear()
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"خطأ في تحديث التفاصيل: {e}")
        await update.message.reply_text(
            "❌ حدث خطأ أثناء تحديث التفاصيل. يرجى المحاولة مرة أخرى."
        )
        context.user_data.clear()
        return ConversationHandler.END

async def smoke_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب استراحة تدخين"""
    user = update.message.from_user
    user_phone = get_user_phone(user.id)
    user_first_name = get_employee_name(user.id, "الموظف")
    current_time = get_jordan_time().strftime("%Y-%m-%d %H:%M:%S")
    
    if not user_phone:
        await update.message.reply_text(
            "⚠️ يجب أن تشارك رقم هاتفك أولاً.\n"
            "استخدم /start ثم اضغط على 'مشاركة رقم الهاتف'."
        )
        return
    
    if not verify_employee(user_phone):
        await update.message.reply_text(
            f"❌ عذراً، رقم الهاتف {user_phone} غير مسجل في النظام.\n"
            "يرجى التواصل مع الإدارة لإضافة رقمك."
        )
        return
    
    employee = get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text(
            "❌ خطأ: لم يتم العثور على بياناتك في النظام.\n"
            "يرجى استخدام /start لتسجيل بياناتك."
        )
        return
    
    last_cigarette_time = get_last_cigarette_time(employee['id'])
    if last_cigarette_time:
        time_since_last = get_jordan_time() - last_cigarette_time
        hours_since_last = time_since_last.total_seconds() / 3600
        min_gap_hours = 1.5
        
        if hours_since_last < min_gap_hours:
            remaining_minutes = int((min_gap_hours - hours_since_last) * 60)
            remaining_hours = remaining_minutes // 60
            remaining_mins = remaining_minutes % 60
            
            time_text = ""
            if remaining_hours > 0:
                time_text = f"{remaining_hours} ساعة و {remaining_mins} دقيقة"
            else:
                time_text = f"{remaining_mins} دقيقة"
            
            await update.message.reply_text(
                f"⏰ يجب الانتظار ساعة ونصف بين كل سيجارة!\n\n"
                f"⏳ الوقت المتبقي: {time_text}\n"
                f"يرجى الانتظار قليلاً. 😊"
            )
            return
    
    current_smoke_count = get_smoke_count_db(employee['id'])
    remaining = MAX_DAILY_SMOKES - current_smoke_count
    
    if current_smoke_count >= MAX_DAILY_SMOKES:
        await update.message.reply_text(
            f"❌ عذراً، لقد وصلت للحد الأقصى اليومي!\n\n"
            f"🚬 السجائر المستخدمة اليوم: {current_smoke_count}/{MAX_DAILY_SMOKES}\n"
            f"يمكنك المحاولة غداً. 😊"
        )
        return
    
    await update.message.reply_text(
        f"⏳ تم إرسال طلب استراحة تدخين للمدير...\n"
        f"الموظف: {user_first_name}\n"
        f"الوقت: {current_time}\n"
        f"🚬 السجائر المتبقية اليوم: {remaining}/{MAX_DAILY_SMOKES}"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✅ قبول", callback_data=f"approve_smoke_{user.id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject_smoke_{user.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    admin_message = (
        f"📩 طلب جديد: استراحة تدخين 🚬\n\n"
        f"الموظف: {user_first_name}\n"
        f"رقم الهاتف: {user_phone}\n"
        f"المعرف: {user.id}\n"
        f"الوقت: {current_time}\n"
        f"المدة: 5 دقائق\n"
        f"🚬 السجائر المستخدمة اليوم: {current_smoke_count}/{MAX_DAILY_SMOKES}\n"
        f"السجائر المتبقية: {remaining}\n\n"
        "اختر الإجراء:"
    )
    
    await send_to_all_admins(context, admin_message, reply_markup)
    logger.info(f"Smoke request sent to admins from {user_first_name} ({user_phone})")

async def break_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب استراحة غداء"""
    user = update.message.from_user
    user_phone = get_user_phone(user.id)
    user_first_name = get_employee_name(user.id, "الموظف")
    current_time = get_jordan_time().strftime("%Y-%m-%d %H:%M:%S")
    
    if not user_phone:
        await update.message.reply_text(
            "⚠️ يجب أن تشارك رقم هاتفك أولاً.\n"
            "استخدم /start ثم اضغط على 'مشاركة رقم الهاتف'."
        )
        return
    
    if not verify_employee(user_phone):
        await update.message.reply_text(
            f"❌ عذراً، رقم الهاتف {user_phone} غير مسجل في النظام.\n"
            "يرجى التواصل مع الإدارة لإضافة رقمك."
        )
        return
    
    employee = get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text(
            "❌ خطأ: لم يتم العثور على بياناتك في النظام.\n"
            "يرجى استخدام /start لتسجيل بياناتك."
        )
        return
    
    if has_taken_lunch_break_today(employee['id']):
        await update.message.reply_text(
            "❌ عذراً، لقد أخذت استراحة غداء اليوم بالفعل!\n\n"
            "📅 يمكنك الحصول على استراحة غداء واحدة فقط في اليوم (30 دقيقة).\n"
            "يمكنك المحاولة غداً. 😊"
        )
        return
    
    await update.message.reply_text(
        f"⏳ تم إرسال طلب استراحة للمدير...\n"
        f"الموظف: {user_first_name}\n"
        f"الوقت: {current_time}"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✅ قبول", callback_data=f"approve_break_{user.id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject_break_{user.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    admin_message = (
        f"📩 طلب جديد: استراحة غداء ☕\n\n"
        f"الموظف: {user_first_name}\n"
        f"رقم الهاتف: {user_phone}\n"
        f"المعرف: {user.id}\n"
        f"الوقت: {current_time}\n"
        f"المدة: 30 دقيقة\n\n"
        "اختر الإجراء:"
    )
    
    await send_to_all_admins(context, admin_message, reply_markup)
    logger.info(f"Break request sent to admins from {user_first_name} ({user_phone})")

async def leave_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب مغادرة العمل - الخطوة 1: طلب السبب"""
    user = update.message.from_user
    user_phone = get_user_phone(user.id)
    
    if not user_phone:
        await update.message.reply_text(
            "⚠️ يجب أن تشارك رقم هاتفك أولاً.\n"
            "استخدم /start ثم اضغط على 'مشاركة رقم الهاتف'."
        )
        return ConversationHandler.END
    
    if not verify_employee(user_phone):
        await update.message.reply_text(
            f"❌ عذراً، رقم الهاتف {user_phone} غير مسجل في النظام.\n"
            "يرجى التواصل مع الإدارة لإضافة رقمك."
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        "📝 من فضلك، أرسل سبب المغادرة كرسالة نصية.\n\n"
        "مثال: موعد طبيب\n\n"
        "أرسل /cancel للإلغاء."
    )
    
    return LEAVE_REASON

async def receive_leave_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب مغادرة العمل - الخطوة 2: استقبال السبب وإرساله للمدير"""
    user = update.message.from_user
    user_phone = get_user_phone(user.id)
    user_first_name = get_employee_name(user.id, "الموظف")
    current_time = get_jordan_time().strftime("%Y-%m-%d %H:%M:%S")
    leave_reason = update.message.text
    
    await update.message.reply_text(
        f"⏳ تم إرسال طلب مغادرة العمل للمدير...\n"
        f"الموظف: {user_first_name}\n"
        f"الوقت: {current_time}\n"
        f"السبب: {leave_reason}"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✅ قبول", callback_data=f"approve_leave_{user.id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject_leave_{user.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    admin_message = (
        f"📩 طلب جديد: مغادرة العمل 🚪\n\n"
        f"الموظف: {user_first_name}\n"
        f"رقم الهاتف: {user_phone}\n"
        f"المعرف: {user.id}\n"
        f"الوقت: {current_time}\n"
        f"السبب: {leave_reason}\n\n"
        "اختر الإجراء:"
    )
    
    await send_to_all_admins(context, admin_message, reply_markup)
    logger.info(f"Leave request sent to admins from {user_first_name} ({user_phone}): {leave_reason}")
    
    return ConversationHandler.END

async def vacation_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب عطلة - الخطوة 1: طلب السبب والعذر"""
    user = update.message.from_user
    user_phone = get_user_phone(user.id)
    
    if not user_phone:
        await update.message.reply_text(
            "⚠️ يجب أن تشارك رقم هاتفك أولاً.\n"
            "استخدم /start ثم اضغط على 'مشاركة رقم الهاتف'."
        )
        return ConversationHandler.END
    
    if not verify_employee(user_phone):
        await update.message.reply_text(
            f"❌ عذراً، رقم الهاتف {user_phone} غير مسجل في النظام.\n"
            "يرجى التواصل مع الإدارة لإضافة رقمك."
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        "🌴 طلب عطلة\n\n"
        "📝 من فضلك، أرسل سبب طلب العطلة والعذر كرسالة نصية.\n\n"
        "مثال: مريض - موعد زيارة طبيب\n\n"
        "أرسل /cancel للإلغاء."
    )
    
    return VACATION_REASON

async def receive_vacation_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب عطلة - الخطوة 2: استقبال السبب وإرساله للمدير"""
    user = update.message.from_user
    user_phone = get_user_phone(user.id)
    user_first_name = get_employee_name(user.id, "الموظف")
    current_time = get_jordan_time().strftime("%Y-%m-%d %H:%M:%S")
    vacation_reason = update.message.text
    
    await update.message.reply_text(
        f"⏳ تم إرسال طلب العطلة للمدير...\n"
        f"الموظف: {user_first_name}\n"
        f"الوقت: {current_time}\n"
        f"السبب والعذر: {vacation_reason}\n\n"
        "سيتم إخطارك عند الرد على الطلب."
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✅ قبول", callback_data=f"approve_vacation_{user.id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject_vacation_{user.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    admin_message = (
        f"📩 طلب جديد: طلب عطلة 🌴\n\n"
        f"الموظف: {user_first_name}\n"
        f"رقم الهاتف: {user_phone}\n"
        f"المعرف: {user.id}\n"
        f"الوقت: {current_time}\n"
        f"السبب والعذر: {vacation_reason}\n\n"
        "اختر الإجراء:"
    )
    
    await send_to_all_admins(context, admin_message, reply_markup)
    logger.info(f"Vacation request sent to admins from {user_first_name} ({user_phone}): {vacation_reason}")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء العملية الحالية"""
    await update.message.reply_text(
        "❌ تم إلغاء العملية.\n"
        "يمكنك استخدام /help لعرض الأوامر المتاحة."
    )
    return ConversationHandler.END

async def check_in_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل حضور الموظف"""
    user = update.message.from_user
    user_phone = get_user_phone(user.id)
    
    if not user_phone or not verify_employee(user_phone):
        await update.message.reply_text(
            "❌ غير مصرح لك باستخدام هذا الأمر.\n"
            "يرجى التواصل مع الإدارة لإضافة رقمك."
        )
        return
    
    employee = get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text("❌ خطأ: لم يتم العثور على بيانات الموظف")
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', 'الموظف')
    
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
        add_warning(employee_id, 'late_arrival', f'تأخير {late_minutes} دقيقة')
        
        message = (
            f"⚠️ تم تسجيل حضورك مع تأخير!\n\n"
            f"👤 الموظف: {employee_name}\n"
            f"⏰ وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
            f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}\n"
            f"⏱ التأخير: {late_minutes} دقيقة\n\n"
            f"🚨 تم إصدار إنذار بسبب التأخير بعد الـ15 دقيقة المسموحة!"
        )
        
        await send_to_all_admins(
            context,
            f"⚠️ إنذار تأخير موظف\n\n"
            f"👤 الموظف: {employee_name}\n"
            f"📱 الهاتف: {user_phone}\n"
            f"⏰ وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
            f"⏱ التأخير: {late_minutes} دقيقة\n"
            f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}\n\n"
            f"🚨 تم إصدار إنذار تلقائي!"
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
    user_phone = get_user_phone(user.id)
    
    if not user_phone or not verify_employee(user_phone):
        await update.message.reply_text(
            "❌ غير مصرح لك باستخدام هذا الأمر.\n"
            "يرجى التواصل مع الإدارة لإضافة رقمك."
        )
        return
    
    employee = get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text("❌ خطأ: لم يتم العثور على بيانات الموظف")
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', 'الموظف')
    
    result = record_check_out(employee_id)
    
    if not result['success']:
        if result.get('error') == 'already_checked_out':
            check_out_time = result['check_out_time']
            total_hours = result['total_work_hours']
            await update.message.reply_text(
                f"⚠️ لقد سجلت انصرافك مسبقاً اليوم!\n\n"
                f"🕐 وقت الانصراف: {check_out_time.strftime('%H:%M:%S')}\n"
                f"⏱ ساعات العمل: {total_hours:.2f} ساعة\n"
                f"📅 التاريخ: {check_out_time.strftime('%Y-%m-%d')}"
            )
        else:
            await update.message.reply_text(f"❌ {result.get('error', 'خطأ في تسجيل الانصراف')}")
        return
    
    check_in_time = result['check_in_time']
    check_out_time = result['check_out_time']
    total_hours = result['total_work_hours']
    overtime_hours = result['overtime_hours']
    
    message = (
        f"✅ تم تسجيل انصرافك بنجاح!\n\n"
        f"👤 الموظف: {employee_name}\n"
        f"🕐 وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
        f"🕐 وقت الانصراف: {check_out_time.strftime('%H:%M:%S')}\n"
        f"📅 التاريخ: {check_out_time.strftime('%Y-%m-%d')}\n\n"
        f"⏱ ساعات العمل الكلية: {total_hours:.2f} ساعة\n"
    )
    
    if overtime_hours > 0:
        message += f"⭐ ساعات إضافية: {overtime_hours:.2f} ساعة\n\n"
        message += "🎉 شكراً على العمل الإضافي!"
    else:
        regular_expected = WORK_REGULAR_HOURS
        if total_hours < regular_expected:
            shortfall = regular_expected - total_hours
            message += f"\n⚠️ ملاحظة: نقص في ساعات العمل بمقدار {shortfall:.2f} ساعة"
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
            f"⏱ ساعات العمل: {total_hours:.2f} ساعة\n"
        )
        
        if overtime_hours > 0:
            admin_message += f"⭐ ساعات إضافية: {overtime_hours:.2f} ساعة\n"
        
        await send_to_all_admins(context, admin_message)
    except Exception as e:
        logger.error(f"Failed to notify admin about check-out: {e}")

async def attendance_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تقرير حضور الموظف"""
    user = update.message.from_user
    user_phone = get_user_phone(user.id)
    
    if not user_phone or not verify_employee(user_phone):
        await update.message.reply_text(
            "❌ غير مصرح لك باستخدام هذا الأمر.\n"
            "يرجى التواصل مع الإدارة لإضافة رقمك."
        )
        return
    
    employee = get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text("❌ خطأ: لم يتم العثور على بيانات الموظف")
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', 'الموظف')
    
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
    total_hours = 0
    total_overtime = 0
    late_days = 0
    
    for record in records:
        date = record['date']
        check_in = record['check_in_time']
        check_out = record['check_out_time']
        is_late = record['is_late']
        work_hours = float(record['total_work_hours']) if record['total_work_hours'] else 0
        overtime = float(record['overtime_hours']) if record['overtime_hours'] else 0
        
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
            message += f"⏱ ساعات العمل: {work_hours:.2f}\n"
            if overtime > 0:
                message += f"⭐ إضافي: {overtime:.2f}\n"
            total_days += 1
            total_hours += work_hours
            total_overtime += overtime
        
        message += "\n"
    
    message += (
        f"━━━━━━━━━━━━━━━━━\n"
        f"📈 الإحصائيات:\n"
        f"📅 أيام العمل: {total_days}\n"
        f"⏱ إجمالي ساعات العمل: {total_hours:.2f}\n"
    )
    
    if total_overtime > 0:
        message += f"⭐ إجمالي الإضافي: {total_overtime:.2f}\n"
    
    if late_days > 0:
        message += f"⚠️ أيام التأخير: {late_days}\n"
    
    if total_days > 0:
        avg_hours = total_hours / total_days
        message += f"📊 متوسط ساعات اليوم: {avg_hours:.2f}\n"
    
    await update.message.reply_text(message)

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
    total_hours = 0
    total_overtime = 0
    
    for record in records:
        name = record['full_name']
        check_in = record['check_in_time']
        check_out = record['check_out_time']
        is_late = record['is_late']
        work_hours = float(record['total_work_hours']) if record['total_work_hours'] else 0
        overtime = float(record['overtime_hours']) if record['overtime_hours'] else 0
        
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
                message += f"⏱ {work_hours:.2f} ساعة"
                if overtime > 0:
                    message += f" (⭐ {overtime:.2f})"
                message += "\n"
                total_hours += work_hours
                total_overtime += overtime
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
    
    message += f"⏱ إجمالي ساعات العمل: {total_hours:.2f}\n"
    
    if total_overtime > 0:
        message += f"⭐ إجمالي الإضافي: {total_overtime:.2f}\n"
    
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
    grand_total_hours = 0
    grand_total_overtime = 0
    
    for record in records:
        name = record['full_name']
        present_days = int(record['present_days']) if record['present_days'] else 0
        late_days = int(record['late_days']) if record['late_days'] else 0
        total_hours = float(record['total_hours']) if record['total_hours'] else 0
        total_overtime = float(record['total_overtime']) if record['total_overtime'] else 0
        avg_hours = float(record['avg_hours']) if record['avg_hours'] else 0
        
        message += f"━━━━━━━━━━━━━━━━━\n"
        message += f"👤 {name}\n"
        message += f"📅 أيام الحضور: {present_days}/7\n"
        
        if late_days > 0:
            message += f"⚠️ أيام التأخير: {late_days}\n"
        
        message += f"⏱ إجمالي الساعات: {total_hours:.2f}\n"
        
        if avg_hours > 0:
            message += f"📊 متوسط اليوم: {avg_hours:.2f}\n"
        
        if total_overtime > 0:
            message += f"⭐ إضافي: {total_overtime:.2f}\n"
        
        message += "\n"
        
        total_present += present_days
        total_late += late_days
        grand_total_hours += total_hours
        grand_total_overtime += total_overtime
    
    total_employees = len(records)
    message += (
        f"━━━━━━━━━━━━━━━━━\n"
        f"📈 ملخص الأسبوع:\n"
        f"👥 عدد الموظفين: {total_employees}\n"
        f"📅 إجمالي أيام الحضور: {total_present}\n"
    )
    
    if total_late > 0:
        message += f"⚠️ إجمالي أيام التأخير: {total_late}\n"
    
    message += f"⏱ إجمالي ساعات العمل: {grand_total_hours:.2f}\n"
    
    if grand_total_overtime > 0:
        message += f"⭐ إجمالي الإضافي: {grand_total_overtime:.2f}\n"
    
    if total_employees > 0 and total_present > 0:
        avg_attendance = total_present / total_employees
        message += f"📊 متوسط الحضور: {avg_attendance:.1f} أيام/موظف\n"
    
    await update.message.reply_text(message)

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة مشاركة رقم الهاتف"""
    contact = update.message.contact
    user = update.message.from_user
    
    if contact and contact.user_id == user.id:
        phone_number = contact.phone_number
        full_name = contact.first_name or "موظف"
        
        existing_by_phone = get_employee_by_phone(phone_number)
        
        if existing_by_phone and not existing_by_phone.get('telegram_id'):
            full_name = existing_by_phone['full_name']
            logger.info(f"تحديث معرف Telegram للموظف الموجود: {full_name} ({phone_number})")
        
        save_employee(user.id, phone_number, full_name)
        
        user_database[user.id] = {
            'phone': phone_number,
            'first_name': full_name,
            'registered_at': get_jordan_time().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        if verify_employee(phone_number):
            message = (
                f"✅ تم التحقق بنجاح!\n\n"
                f"👤 الاسم: {full_name}\n"
                f"📱 الهاتف: {phone_number}\n\n"
                "━━━━━━━━━━━━━━━━━\n"
                "✅ رقمك مسجل في النظام!\n\n"
                "┏━━━━━━━━━━━━━━━━━━━━━┓\n"
                "┃   📚 قائمة الأوامر   ┃\n"
                "┗━━━━━━━━━━━━━━━━━━━━━┛\n\n"
                "🔹 أوامر الحضور والانصراف:\n"
                "━━━━━━━━━━━━━━━━━\n"
                "/check_in - تسجيل الحضور 📥\n"
                "  (إلزامي في بداية الدوام)\n\n"
                "/check_out - تسجيل الانصراف 📤\n"
                "  (إلزامي في نهاية الدوام)\n\n"
                "/attendance_report - تقرير حضورك 📊\n"
                "  (آخر 7 أيام)\n\n"
                "🔹 أوامر الاستراحات:\n"
                "━━━━━━━━━━━━━━━━━\n"
                "/smoke - طلب استراحة تدخين 🚬\n"
                "  (5 دقائق، حد أقصى 6 سجائر/يوم، فجوة 1.5 ساعة)\n\n"
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
                message += (
                    "🔸 أوامر المدير:\n"
                    "━━━━━━━━━━━━━━━━━\n"
                    "/list_employees - عرض جميع الموظفين 👥\n"
                    "/add_employee - إضافة موظف جديد ➕\n"
                    "/remove_employee - حذف موظف ❌\n"
                    "/edit_employee - تعديل بيانات موظف ✏️\n"
                    "/daily_report - التقرير اليومي 📊\n"
                    "/weekly_report - التقرير الأسبوعي 📈\n\n"
                )
            
            message += "━━━━━━━━━━━━━━━━━\n✨ يمكنك الآن استخدام جميع الأوامر!"
        else:
            message = (
                f"شكراً لمشاركة معلومات الاتصال! ✅\n\n"
                f"👤 الاسم: {full_name}\n"
                f"📱 الهاتف: {phone_number}\n\n"
                "⚠️ رقم هاتفك غير مسجل في النظام حالياً.\n\n"
                "يرجى التواصل مع الإدارة لإضافة رقمك إلى النظام."
            )
        
        logger.info(f"Contact registered: {full_name} - {phone_number} (ID: {user.id})")
        await update.message.reply_text(message)
    else:
        await update.message.reply_text(
            "⚠️ يرجى مشاركة رقم هاتفك الشخصي فقط."
        )

def create_progress_bar(current_seconds: int, total_seconds: int, length: int = 20) -> str:
    """إنشاء شريط تقدم متحرك"""
    percentage = current_seconds / total_seconds
    filled = int(percentage * length)
    empty = length - filled
    
    bar = '█' * filled + '░' * empty
    percent = int(percentage * 100)
    
    return f"[{bar}] {percent}%"

def get_time_emoji(remaining_seconds: int, total_seconds: int) -> str:
    """الحصول على رمز متحرك حسب الوقت المتبقي"""
    percentage = remaining_seconds / total_seconds
    
    if percentage > 0.75:
        return '🟢'
    elif percentage > 0.50:
        return '🟡'
    elif percentage > 0.25:
        return '🟠'
    else:
        return '🔴'

async def update_timer(context: ContextTypes.DEFAULT_TYPE):
    """تحديث العداد التنازلي"""
    job = context.job
    user_id, message_id, end_time, request_type, total_duration = job.data
    
    if user_id in timer_completed and timer_completed[user_id]:
        return
    
    now = get_jordan_time()
    remaining = end_time - now
    
    if remaining.total_seconds() <= 0:
        if user_id in timer_completed and timer_completed[user_id]:
            return
            
        timer_completed[user_id] = True
        
        if user_id in active_timers:
            for active_job in active_timers[user_id]:
                try:
                    active_job.schedule_removal()
                except:
                    pass
            del active_timers[user_id]
        
        request_names = {
            'smoke': 'استراحة التدخين',
            'break': 'استراحة الغداء'
        }
        request_name = request_names.get(request_type, 'الاستراحة')
        
        completion_message = (
            f"🔔🔔🔔 تنبيه! ⏰\n\n"
            f"⏱ انتهت {request_name}!\n"
            f"🕐 الوقت: {now.strftime('%H:%M:%S')}\n\n"
            f"💼 يرجى العودة للعمل فوراً!"
        )
        
        keyboard = [[InlineKeyboardButton("✅ رجعت للعمل", callback_data=f"returned_{request_type}_{user_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=completion_message,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Failed to send timer completion message: {e}")
        return
    
    minutes = int(remaining.total_seconds() // 60)
    seconds = int(remaining.total_seconds() % 60)
    remaining_secs = int(remaining.total_seconds())
    
    request_emoji = {
        'smoke': '🚬',
        'break': '☕'
    }
    emoji = request_emoji.get(request_type, '⏱')
    
    status_emoji = get_time_emoji(remaining_secs, total_duration * 60)
    progress_bar = create_progress_bar(remaining_secs, total_duration * 60)
    
    timer_text = (
        f"┏━━━━━━━━━━━━━━━━━┓\n"
        f"┃ {emoji}  العداد التنازلي  {emoji} ┃\n"
        f"┗━━━━━━━━━━━━━━━━━┛\n\n"
        f"{status_emoji} الحالة: {'جيد' if remaining_secs > total_duration * 60 * 0.5 else 'انتبه!'}\n\n"
        f"⏱ الوقت المتبقي:\n"
        f"╔═══════════════╗\n"
        f"║  {minutes:02d}:{seconds:02d}  ║\n"
        f"╚═══════════════╝\n\n"
        f"{progress_bar}\n\n"
        f"🕐 ينتهي في: {end_time.strftime('%H:%M:%S')}"
    )
    
    try:
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text=timer_text
        )
    except Exception as e:
        logger.debug(f"Timer update skipped: {e}")

async def start_countdown_timer(context: ContextTypes.DEFAULT_TYPE, user_id: int, duration_minutes: int, request_type: str):
    """بدء عداد تنازلي"""
    if user_id in active_timers:
        try:
            for job in active_timers[user_id]:
                job.schedule_removal()
        except:
            pass
    
    timer_completed[user_id] = False
    
    end_time = get_jordan_time() + timedelta(minutes=duration_minutes)
    
    request_emoji = {
        'smoke': '🚬',
        'break': '☕'
    }
    emoji = request_emoji.get(request_type, '⏱')
    
    progress_bar = create_progress_bar(duration_minutes * 60, duration_minutes * 60)
    
    timer_text = (
        f"┏━━━━━━━━━━━━━━━━━┓\n"
        f"┃ {emoji}  العداد التنازلي  {emoji} ┃\n"
        f"┗━━━━━━━━━━━━━━━━━┛\n\n"
        f"🟢 الحالة: جيد\n\n"
        f"⏱ الوقت المتبقي:\n"
        f"╔═══════════════╗\n"
        f"║  {duration_minutes:02d}:00  ║\n"
        f"╚═══════════════╝\n\n"
        f"{progress_bar}\n\n"
        f"🕐 ينتهي في: {end_time.strftime('%H:%M:%S')}"
    )
    
    try:
        sent_message = await context.bot.send_message(
            chat_id=user_id,
            text=timer_text
        )
        
        jobs = []
        for i in range(duration_minutes * 60 + 1):
            job = context.job_queue.run_once(
                update_timer,
                when=i,
                data=(user_id, sent_message.message_id, end_time, request_type, duration_minutes),
                name=f"timer_{user_id}_{i}"
            )
            jobs.append(job)
        
        active_timers[user_id] = jobs
        
    except Exception as e:
        logger.error(f"Failed to start countdown timer: {e}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الضغط على أزرار الموافقة/الرفض والعودة"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    parts = data.split('_')
    action = parts[0]
    
    if action == 'returned':
        request_type = parts[1]
        user_id = int(parts[2])
        
        employee = get_employee_by_telegram_id(user_id)
        if not employee:
            await query.edit_message_text(text=query.message.text + "\n\n❌ خطأ: لم يتم العثور على بيانات الموظف")
            return
        
        employee_name = employee.get('full_name', 'الموظف')
        return_time = get_jordan_time()
        
        request_names = {
            'smoke': 'استراحة التدخين',
            'break': 'استراحة الغداء'
        }
        request_name = request_names.get(request_type, 'الاستراحة')
        
        await query.edit_message_text(
            text=query.message.text + "\n\n✅ تم تأكيد عودتك للعمل!"
        )
        
        try:
            await send_to_all_admins(
                context,
                (
                    f"✅ تأكيد عودة موظف\n\n"
                    f"👤 الموظف: {employee_name}\n"
                    f"📱 الهاتف: {employee.get('phone_number', 'غير متوفر')}\n"
                    f"⏱ نوع الاستراحة: {request_name}\n"
                    f"🕐 وقت العودة: {return_time.strftime('%H:%M:%S')}\n"
                    f"📅 التاريخ: {return_time.strftime('%Y-%m-%d')}\n\n"
                    f"💼 الموظف عاد للعمل!"
                )
            )
        except Exception as e:
            logger.error(f"Failed to notify admin about employee return: {e}")
        
        return
    
    request_type = parts[1]
    telegram_id_str = parts[2]
    telegram_id = int(telegram_id_str)
    
    employee = get_employee_by_telegram_id(telegram_id)
    if not employee:
        await query.edit_message_text(text=query.message.text + "\n\n❌ خطأ: لم يتم العثور على بيانات الموظف")
        return
    
    employee_db_id = employee['id']
    employee_phone = employee.get('phone_number', 'غير متوفر')
    employee_name = employee.get('full_name', 'الموظف')
    
    request_types_ar = {
        'smoke': 'استراحة تدخين',
        'break': 'استراحة غداء',
        'leave': 'مغادرة العمل',
        'vacation': 'طلب عطلة'
    }
    
    request_name = request_types_ar.get(request_type, request_type)
    
    if action == 'approve':
        if request_type == 'smoke':
            current_count_before = get_smoke_count_db(employee_db_id)
            
            if current_count_before >= MAX_DAILY_SMOKES:
                admin_response = (
                    f"⚠️ تحذير: تم قبول الطلب لكن الموظف وصل للحد الأقصى!\n"
                    f"🚬 السجائر المستخدمة: {current_count_before}/{MAX_DAILY_SMOKES}\n"
                    f"السجائر المتبقية: 0\n\n"
                    f"لن يتم زيادة العداد."
                )
                employee_message = (
                    f"✅ تم قبول طلبك!\n\n"
                    f"نوع الطلب: {request_name}\n"
                    f"المدة: 5 دقائق\n"
                    f"الوقت: {get_jordan_time().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"⚠️ ملاحظة: وصلت للحد الأقصى اليومي!\n"
                    f"🚬 السجائر المستخدمة اليوم: {current_count_before}/{MAX_DAILY_SMOKES}\n\n"
                    f"استمتع بوقتك! 😊"
                )
            else:
                current_count = increment_smoke_count_db(employee_db_id)
                record_cigarette_time(employee_db_id)
                remaining = max(0, MAX_DAILY_SMOKES - current_count)
                admin_response = (
                    f"✅ تم قبول طلب {request_name}\n"
                    f"🚬 السجائر المستخدمة الآن: {current_count}/{MAX_DAILY_SMOKES}\n"
                    f"السجائر المتبقية: {remaining}"
                )
                employee_message = (
                    f"✅ تم قبول طلبك!\n\n"
                    f"نوع الطلب: {request_name}\n"
                    f"المدة: 5 دقائق\n"
                    f"الوقت: {get_jordan_time().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"🚬 السجائر المستخدمة اليوم: {current_count}/{MAX_DAILY_SMOKES}\n\n"
                    f"استمتع بوقتك! 😊"
                )
        elif request_type == 'break':
            mark_lunch_break_taken(employee_db_id)
            admin_response = f"✅ تم قبول طلب {request_name}"
            employee_message = (
                f"✅ تم قبول طلبك!\n\n"
                f"نوع الطلب: {request_name}\n"
                f"المدة: 30 دقيقة\n"
                f"الوقت: {get_jordan_time().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"استمتع بوقتك! 😊"
            )
        else:
            admin_response = f"✅ تم قبول طلب {request_name}"
            employee_message = (
                f"✅ تم قبول طلبك!\n\n"
                f"نوع الطلب: {request_name}\n"
                f"الوقت: {get_jordan_time().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"استمتع بوقتك! 😊"
            )
        logger.info(f"Request approved: {request_type} for employee {telegram_id}")
    else:
        admin_response = f"❌ تم رفض طلب {request_name}"
        employee_message = (
            f"❌ عذراً، تم رفض طلبك.\n\n"
            f"نوع الطلب: {request_name}\n"
            f"الوقت: {get_jordan_time().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            "يرجى التواصل مع المدير للمزيد من المعلومات."
        )
        logger.info(f"Request rejected: {request_type} for employee {telegram_id}")
    
    await query.edit_message_text(
        text=query.message.text + f"\n\n{admin_response}",
    )
    
    try:
        await context.bot.send_message(
            chat_id=telegram_id,
            text=employee_message
        )
        
        if action == 'approve' and request_type in ['smoke', 'break']:
            duration = 5 if request_type == 'smoke' else 30
            await start_countdown_timer(context, telegram_id, duration, request_type)
            
    except Exception as e:
        logger.error(f"Failed to send response to employee {telegram_id}: {e}")

async def send_auto_daily_report(context: ContextTypes.DEFAULT_TYPE):
    """إرسال تقرير يومي تلقائي للمدير عند نهاية الدوام"""
    try:
        today = get_jordan_time().date()
        records = get_daily_attendance_report(today)
        
        if not records:
            message = f"📊 التقرير اليومي التلقائي - {today.strftime('%Y-%m-%d')}\n\n⚠️ لا توجد سجلات حضور لليوم."
        else:
            message = (
                f"📊 التقرير اليومي التلقائي\n"
                f"📅 {today.strftime('%Y-%m-%d')}\n\n"
            )
            
            present_count = 0
            absent_count = 0
            late_count = 0
            total_hours = 0
            total_overtime = 0
            
            for record in records:
                name = record['full_name']
                check_in = record['check_in_time']
                check_out = record['check_out_time']
                is_late = record['is_late']
                work_hours = float(record['total_work_hours']) if record['total_work_hours'] else 0
                overtime = float(record['overtime_hours']) if record['overtime_hours'] else 0
                
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
                        message += f"⏱ {work_hours:.2f} ساعة"
                        if overtime > 0:
                            message += f" (⭐ {overtime:.2f})"
                        message += "\n"
                        total_hours += work_hours
                        total_overtime += overtime
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
            
            message += f"⏱ إجمالي ساعات العمل: {total_hours:.2f}\n"
            
            if total_overtime > 0:
                message += f"⭐ إجمالي الإضافي: {total_overtime:.2f}\n"
        
        await send_to_all_admins(context, message)
        logger.info(f"تم إرسال التقرير اليومي التلقائي لجميع المديرين - {today}")
        
    except Exception as e:
        logger.error(f"خطأ في إرسال التقرير اليومي التلقائي: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الأخطاء"""
    logger.error(f"Update {update} caused error {context.error}")

def load_employees_from_database():
    """تحميل جميع الموظفين من قاعدة البيانات إلى قائمة الموظفين المصرح لهم"""
    try:
        employees = get_all_employees()
        loaded_count = 0
        for employee in employees:
            phone = employee.get('phone_number')
            if phone:
                normalized = normalize_phone(phone)
                phone_with_plus = '+' + normalized if not phone.startswith('+') else phone
                if phone_with_plus not in authorized_phones:
                    authorized_phones.append(phone_with_plus)
                    loaded_count += 1
        
        if loaded_count > 0:
            logger.info(f"تم تحميل {loaded_count} موظف من قاعدة البيانات إلى قائمة الموظفين المصرح لهم")
            print(f"✅ تم تحميل {loaded_count} موظف من قاعدة البيانات")
        return loaded_count
    except Exception as e:
        logger.error(f"خطأ في تحميل الموظفين من قاعدة البيانات: {e}")
        return 0

def main():
    """بدء البوت"""
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not found in environment variables!")
        print("Please set your bot token in the Secrets tab.")
        return
    
    print("Starting Employee Management Bot...")
    print("بدء بوت إدارة الموظفين...")
    print(f"\nعدد المديرين المسجلين: {len(ADMIN_IDS)}")
    print(f"Number of registered admins: {len(ADMIN_IDS)}")
    print(f"لإضافة مديرين إضافيين، قم بتحديث قائمة ADMIN_IDS في الكود")
    print(f"To add more admins, update the ADMIN_IDS list in the code")
    
    initialize_database_tables()
    load_employees_from_database()
    
    application = Application.builder().token(BOT_TOKEN).build()

    # الإضافة الجديدة: مسح جميع الـ Webhooks والرسائل العالقة لضمان الإطلاق النظيف
    try:
        # إيقاف أي Webhook قديم
        application.bot.delete_webhook()
        # مسح أي تحديثات عالقة
        application.bot.get_updates(offset=-1, timeout=1) 
        logger.info("تم مسح الـ Webhook والرسائل العالقة بنجاح.")
    except Exception as e:
        logger.warning(f"لم نتمكن من مسح الـ Webhook/الرسائل العالقة: {e}") 

    
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
    
    edit_details_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("edit_details", edit_details_command),
        ],
        states={
            EDIT_DETAIL_SELECT: [
                CallbackQueryHandler(show_employee_details, pattern=r"^editdetail_\d+$"),
                CallbackQueryHandler(select_field_to_edit, pattern=r"^editfield_\w+_\d+$"),
                CallbackQueryHandler(select_field_to_edit, pattern=r"^cancel_edit$"),
            ],
            EDIT_DETAIL_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_detail_value),
                CommandHandler("cancel", cancel)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("my_id", my_id_command))
    application.add_handler(CommandHandler("check_in", check_in_command))
    application.add_handler(CommandHandler("check_out", check_out_command))
    application.add_handler(CommandHandler("attendance_report", attendance_report_command))
    application.add_handler(CommandHandler("smoke", smoke_request))
    application.add_handler(CommandHandler("break", break_request))
    application.add_handler(leave_conv_handler)
    application.add_handler(vacation_conv_handler)
    application.add_handler(edit_details_conv_handler)
    
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
    
    job_queue = application.job_queue
    if job_queue:
        daily_report_time = datetime.now(JORDAN_TZ).replace(hour=19, minute=0, second=0, microsecond=0)
        job_queue.run_daily(
            send_auto_daily_report,
            time=daily_report_time.time(),
            days=(0, 1, 2, 3, 4, 5, 6),
            name="daily_attendance_report"
        )
        logger.info("تم جدولة التقرير اليومي التلقائي للساعة 7:00 مساءً (توقيت الأردن)")
        print("✅ تم جدولة التقرير اليومي التلقائي للساعة 7:00 مساءً")
    
    print("Bot is running! Press Ctrl+C to stop.")
    print("البوت يعمل الآن!")
    
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