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
ADMIN_IDS = [1465191277,6798279805]  # أضف معرفات المديرين الإضافيين مثل: [1465191277, 987654321, 123456789]

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
        
        # جدول الموظفين - يجب أن يكون أولاً لأن الجداول الأخرى تعتمد عليه
        cur.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                phone_number VARCHAR(50) UNIQUE,
                full_name VARCHAR(255) NOT NULL,
                age INTEGER,
                position VARCHAR(100),
                department VARCHAR(100),
                hire_date DATE,
                last_active TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # جدول المديرين
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                added_by BIGINT,
                added_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                is_super_admin BOOLEAN DEFAULT FALSE,
                can_approve BOOLEAN DEFAULT TRUE
            );
        """)
        
        cur.execute("""
            ALTER TABLE admins 
            ADD COLUMN IF NOT EXISTS can_approve BOOLEAN DEFAULT TRUE;
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
                notes TEXT,
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
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, date)
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
                last_time = last_time.replace(tzinfo=ZoneInfo('UTC'))
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

def can_approve_requests(user_id):
    """التحقق من أن المدير لديه صلاحية الموافقة على الطلبات"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT can_approve FROM admins WHERE telegram_id = %s", (user_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        if not result:
            return user_id in ADMIN_IDS
        return result[0]
    except:
        return user_id in ADMIN_IDS

def add_admin_to_db(telegram_id, added_by=None, is_super=False, can_approve=True):
    """إضافة مدير إلى قاعدة البيانات"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO admins (telegram_id, added_by, is_super_admin, can_approve)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE SET 
                is_super_admin = EXCLUDED.is_super_admin,
                can_approve = EXCLUDED.can_approve
        """, (telegram_id, added_by, is_super, can_approve))
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

def get_max_daily_smokes():
    """الحصول على الحد الأقصى للسجائر بناءً على اليوم الحالي"""
    current_time = get_jordan_time()
    day_of_week = current_time.weekday()
    if day_of_week == 4:
        return 3
    else:
        return MAX_DAILY_SMOKES

def is_friday():
    """التحقق من أن اليوم هو الجمعة"""
    current_time = get_jordan_time()
    return current_time.weekday() == 4

def normalize_phone(phone):
    """توحيد تنسيق رقم الهاتف"""
    if not phone:
        return ""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+962"):
        return phone
    elif phone.startswith("962"):
        return "+" + phone
    elif phone.startswith("0"):
        return "+962" + phone[1:]
    else:
        return "+962" + phone

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر /start"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    if is_admin(user.id):
        await show_admin_menu(update, context)
        return
    
    employee = get_employee_by_telegram_id(user.id)
    
    if employee:
        await show_main_menu(update, context)
    else:
        await update.message.reply_text(
            "مرحباً! يبدو أنك لم تسجل بعد.\n\n"
            "يرجى مشاركة رقم هاتفك للتحقق من هويتك:",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("مشاركة رقم الهاتف 📞", request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True
            )
        )

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة مشاركة رقم الهاتف"""
    user = update.effective_user
    contact = update.message.contact
    
    if not contact:
        await update.message.reply_text("يرجى مشاركة رقم هاتفك باستخدام الزر أدناه.")
        return
    
    phone_number = contact.phone_number
    normalized_phone = normalize_phone(phone_number)
    
    employee = get_employee_by_phone(normalized_phone)
    
    if employee:
        if employee.get('telegram_id') and employee['telegram_id'] != user.id:
            await update.message.reply_text(
                "❌ هذا الرقم مرتبط بحساب تليجرام آخر.\n"
                "يرجى استخدام الرقم الصحيح أو التواصل مع المدير."
            )
            return
        
        save_employee(user.id, normalized_phone, employee['full_name'])
        await update.message.reply_text(
            f"✅ تم التحقق بنجاح!\n"
            f"مرحباً مرة أخرى {employee['full_name']}",
            reply_markup=ReplyKeyboardRemove()
        )
        await show_main_menu(update, context)
    else:
        await update.message.reply_text(
            "❌ رقم الهاتف غير مسجل في النظام.\n"
            "يرجى التواصل مع المدير لإضافتك إلى النظام.",
            reply_markup=ReplyKeyboardRemove()
        )

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض القائمة الرئيسية"""
    keyboard = [
        [KeyboardButton("تسجيل الحضور 🟢"), KeyboardButton("تسجيل الانصراف 🔴")],
        [KeyboardButton("طلب إذن خروج 🚪"), KeyboardButton("طلب إجازة 🏖️")],
        [KeyboardButton("تسجيل سيجارة 🚬"), KeyboardButton("بريك الغداء 🍽️")],
        [KeyboardButton("عرض الإحصائيات 📊"), KeyboardButton("المساعدة ℹ️")]
    ]
    
    if is_admin(update.effective_user.id):
        keyboard.append([KeyboardButton("لوحة التحكم 👨‍💼")])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    if update.message:
        await update.message.reply_text("اختر من القائمة:", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text("اختر من القائمة:", reply_markup=reply_markup)

async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المدير"""
    keyboard = [
        [KeyboardButton("عرض تقرير اليوم 📅"), KeyboardButton("عرض تقرير الأسبوع 📊")],
        [KeyboardButton("إدارة الموظفين 👥"), KeyboardButton("إدارة الطلبات 📋")],
        [KeyboardButton("إدارة المديرين 👨‍💼"), KeyboardButton("رجوع ↩️")]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    if update.message:
        await update.message.reply_text("👨‍💼 لوحة تحكم المدير", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text("👨‍💼 لوحة تحكم المدير", reply_markup=reply_markup)

async def handle_check_in(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة تسجيل الحضور"""
    user = update.effective_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text("❌ لم يتم العثور على بياناتك. يرجى التسجيل أولاً.")
        return
    
    result = record_check_in(employee['id'])
    
    if result['success']:
        check_in_time = result['check_in_time'].strftime("%H:%M:%S")
        if result['is_late']:
            message = (f"⚠️ تم تسجيل الحضور في {check_in_time} (متأخر {result['late_minutes']} دقيقة)\n"
                      f"يرجى تقديم سبب التأخير للمدير.")
        else:
            message = f"✅ تم تسجيل الحضور في {check_in_time}"
        await update.message.reply_text(message)
    else:
        if result.get('error') == 'already_checked_in':
            existing_time = result['check_in_time'].strftime("%H:%M:%S")
            if result['is_late']:
                message = f"⚠️ تم تسجيل الحضور مسبقاً في {existing_time} (متأخر {result['late_minutes']} دقيقة)"
            else:
                message = f"ℹ️ تم تسجيل الحضور مسبقاً في {existing_time}"
            await update.message.reply_text(message)
        else:
            await update.message.reply_text("❌ حدث خطأ في تسجيل الحضور. يرجى المحاولة لاحقاً.")

async def handle_check_out(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة تسجيل الانصراف"""
    user = update.effective_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text("❌ لم يتم العثور على بياناتك. يرجى التسجيل أولاً.")
        return
    
    result = record_check_out(employee['id'])
    
    if result['success']:
        check_out_time = result['check_out_time'].strftime("%H:%M:%S")
        total_hours = result['total_work_hours']
        overtime = result['overtime_hours']
        
        message = (f"✅ تم تسجيل الانصراف في {check_out_time}\n"
                  f"🕐 مجموع ساعات العمل: {total_hours:.2f} ساعة")
        
        if overtime > 0:
            message += f"\n⏱️ ساعات إضافية: {overtime:.2f} ساعة"
            
        await update.message.reply_text(message)
    else:
        if result.get('error') == 'already_checked_out':
            existing_time = result['check_out_time'].strftime("%H:%M:%S")
            total_hours = result['total_work_hours']
            overtime = result['overtime_hours']
            
            message = (f"ℹ️ تم تسجيل الانصراف مسبقاً في {existing_time}\n"
                      f"🕐 مجموع ساعات العمل: {total_hours:.2f} ساعة")
            
            if overtime > 0:
                message += f"\n⏱️ ساعات إضافية: {overtime:.2f} ساعة"
                
            await update.message.reply_text(message)
        elif result.get('error') == 'لم يتم تسجيل الحضور اليوم':
            await update.message.reply_text("❌ لم يتم تسجيل الحضور اليوم. يرجى تسجيل الحضور أولاً.")
        else:
            await update.message.reply_text("❌ حدث خطأ في تسجيل الانصراف. يرجى المحاولة لاحقاً.")

async def handle_smoke_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة طلب تدخين سيجارة"""
    user = update.effective_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text("❌ لم يتم العثور على بياناتك. يرجى التسجيل أولاً.")
        return
    
    current_count = get_smoke_count_db(employee['id'])
    max_allowed = get_max_daily_smokes()
    
    if current_count >= max_allowed:
        if is_friday():
            await update.message.reply_text("❌ لقد استهلكت الحد الأقصى لليوم (3 سجائر فقط في الجمعة)")
        else:
            await update.message.reply_text(f"❌ لقد استهلكت الحد الأقصى لليوم ({max_allowed} سجائر)")
        return
    
    last_smoke_time = get_last_cigarette_time(employee['id'])
    current_time = get_jordan_time()
    
    if last_smoke_time:
        time_diff = (current_time - last_smoke_time).total_seconds() / 60
        if time_diff < 30:
            remaining = 30 - int(time_diff)
            await update.message.reply_text(f"⏳ يجب الانتظار {remaining} دقيقة قبل السيجارة القادمة")
            return
    
    new_count = increment_smoke_count_db(employee['id'])
    record_cigarette_time(employee['id'])
    
    remaining_smokes = max_allowed - new_count
    
    message = (f"🚬 تم تسجيل السيجارة بنجاح\n"
              f"📊 العدد اليومي: {new_count}/{max_allowed}\n"
              f"📉 المتبقي: {remaining_smokes}")
    
    await update.message.reply_text(message)

async def handle_lunch_break(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة طلب بريك الغداء"""
    user = update.effective_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text("❌ لم يتم العثور على بياناتك. يرجى التسجيل أولاً.")
        return
    
    if has_taken_lunch_break_today(employee['id']):
        await update.message.reply_text("ℹ️ لقد أخذت بريك الغداء مسبقاً اليوم")
        return
    
    mark_lunch_break_taken(employee['id'])
    await update.message.reply_text("✅ تم تسجيل بريك الغداء بنجاح\n🍽️ استمتع بوقتك!")

async def handle_leave_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية طلب إذن خروج"""
    user = update.effective_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text("❌ لم يتم العثور على بياناتك. يرجى التسجيل أولاً.")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "يرجى كتابة سبب طلب إذن الخروج:",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("إلغاء ❌")]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )
    
    return LEAVE_REASON

async def handle_vacation_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية طلب إجازة"""
    user = update.effective_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text("❌ لم يتم العثور على بياناتك. يرجى التسجيل أولاً.")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "يرجى كتابة سبب طلب الإجازة:",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("إلغاء ❌")]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )
    
    return VACATION_REASON

async def process_leave_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة سبب إذن الخروج"""
    reason = update.message.text
    
    if reason == "إلغاء ❌":
        await update.message.reply_text("تم إلغاء طلب إذن الخروج", reply_markup=ReplyKeyboardRemove())
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    user = update.effective_user
    employee = get_employee_by_telegram_id(user.id)
    
    request_id = save_request(employee['id'], 'leave')
    
    if request_id:
        admin_text = (f"📋 طلب إذن خروج جديد\n"
                     f"👤 الموظف: {employee['full_name']}\n"
                     f"📞 الرقم: {employee['phone_number']}\n"
                     f"📝 السبب: {reason}\n"
                     f"🆔 رقم الطلب: {request_id}")
        
        keyboard = [
            [
                InlineKeyboardButton("✅ الموافقة", callback_data=f"approve_leave_{request_id}"),
                InlineKeyboardButton("❌ الرفض", callback_data=f"reject_leave_{request_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await send_to_all_admins(context, admin_text, reply_markup)
        
        await update.message.reply_text(
            "✅ تم إرسال طلب إذن الخروج للمدير\n⏳ بانتظار الموافقة...",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await update.message.reply_text(
            "❌ حدث خطأ في إرسال الطلب. يرجى المحاولة لاحقاً.",
            reply_markup=ReplyKeyboardRemove()
        )
    
    await show_main_menu(update, context)
    return ConversationHandler.END

async def process_vacation_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة سبب الإجازة"""
    reason = update.message.text
    
    if reason == "إلغاء ❌":
        await update.message.reply_text("تم إلغاء طلب الإجازة", reply_markup=ReplyKeyboardRemove())
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    user = update.effective_user
    employee = get_employee_by_telegram_id(user.id)
    
    request_id = save_request(employee['id'], 'vacation')
    
    if request_id:
        admin_text = (f"🏖️ طلب إجازة جديد\n"
                     f"👤 الموظف: {employee['full_name']}\n"
                     f"📞 الرقم: {employee['phone_number']}\n"
                     f"📝 السبب: {reason}\n"
                     f"🆔 رقم الطلب: {request_id}")
        
        keyboard = [
            [
                InlineKeyboardButton("✅ الموافقة", callback_data=f"approve_vacation_{request_id}"),
                InlineKeyboardButton("❌ الرفض", callback_data=f"reject_vacation_{request_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await send_to_all_admins(context, admin_text, reply_markup)
        
        await update.message.reply_text(
            "✅ تم إرسال طلب الإجازة للمدير\n⏳ بانتظار الموافقة...",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await update.message.reply_text(
            "❌ حدث خطأ في إرسال الطلب. يرجى المحاولة لاحقاً.",
            reply_markup=ReplyKeyboardRemove()
        )
    
    await show_main_menu(update, context)
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء المحادثة"""
    await update.message.reply_text(
        "تم الإلغاء",
        reply_markup=ReplyKeyboardRemove()
    )
    await show_main_menu(update, context)
    return ConversationHandler.END

async def handle_admin_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة عرض تقرير اليوم للمدير"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول لهذا الأمر.")
        return
    
    report_data = get_daily_attendance_report()
    today = get_today_date()
    
    if not report_data:
        await update.message.reply_text(f"📅 لا توجد بيانات حضور لليوم {today}")
        return
    
    message = f"📊 تقرير حضور اليوم {today}\n\n"
    
    present_count = 0
    late_count = 0
    absent_count = 0
    
    for record in report_data:
        status = "❌ غائب"
        details = ""
        
        if record['check_in_time']:
            check_in = record['check_in_time'].strftime("%H:%M")
            check_out = record['check_out_time'].strftime("%H:%M") if record['check_out_time'] else "لم ينصرف"
            total_hours = record['total_work_hours'] or 0
            overtime = record['overtime_hours'] or 0
            
            if record['is_late']:
                status = "⚠️ متأخر"
                late_count += 1
                details = f" ({check_in} - {check_out}) - تأخر {record['late_minutes']} دقيقة"
            else:
                status = "✅ حاضر"
                present_count += 1
                details = f" ({check_in} - {check_out})"
            
            details += f" - {total_hours:.1f}h"
            if overtime > 0:
                details += f" (+{overtime:.1f}h)"
        else:
            absent_count += 1
        
        message += f"{record['full_name']}: {status}{details}\n"
    
    message += f"\n📈 الإحصائيات:\n"
    message += f"✅ حاضر: {present_count}\n"
    message += f"⚠️ متأخر: {late_count}\n"
    message += f"❌ غائب: {absent_count}\n"
    message += f"👥 المجموع: {len(report_data)}"
    
    await update.message.reply_text(message)

async def handle_weekly_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة عرض التقرير الأسبوعي للمدير"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول لهذا الأمر.")
        return
    
    report_data = get_weekly_attendance_report()
    
    if not report_data:
        await update.message.reply_text("📊 لا توجد بيانات حضور للأسبوع الماضي")
        return
    
    message = "📊 التقرير الأسبوعي (آخر 7 أيام)\n\n"
    
    for record in report_data:
        message += f"👤 {record['full_name']}:\n"
        message += f"   ✅ أيام الحضور: {record['present_days']}\n"
        message += f"   ⚠️ أيام التأخر: {record['late_days']}\n"
        message += f"   🕐 إجمالي الساعات: {record['total_hours']:.1f}h\n"
        message += f"   ⏱️ ساعات إضافية: {record['total_overtime']:.1f}h\n"
        if record['avg_hours']:
            message += f"   📊 متوسط الساعات: {float(record['avg_hours']):.1f}h\n"
        message += "\n"
    
    await update.message.reply_text(message)

async def handle_employee_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدارة الموظفين"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول لهذا الأمر.")
        return
    
    keyboard = [
        [KeyboardButton("عرض جميع الموظفين 👥")],
        [KeyboardButton("إضافة موظف جديد ➕"), KeyboardButton("حذف موظف 🗑️")],
        [KeyboardButton("تحديث بيانات موظف ✏️")],
        [KeyboardButton("رجوع ↩️")]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("👥 إدارة الموظفين", reply_markup=reply_markup)

async def handle_show_employees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض جميع الموظفين"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول لهذا الأمر.")
        return
    
    employees = get_all_employees()
    
    if not employees:
        await update.message.reply_text("❌ لا يوجد موظفين مسجلين في النظام.")
        return
    
    message = "👥 قائمة جميع الموظفين:\n\n"
    
    for i, emp in enumerate(employees, 1):
        status = "✅ نشط" if emp.get('telegram_id') else "❌ غير نشط"
        last_active = emp.get('last_active')
        if last_active:
            if isinstance(last_active, str):
                last_active_str = last_active
            else:
                last_active_str = last_active.strftime("%Y-%m-%d %H:%M")
        else:
            last_active_str = "غير معروف"
        
        message += (f"{i}. {emp['full_name']}\n"
                   f"   📞 {emp['phone_number']}\n"
                   f"   🆔 {emp['id']}\n"
                   f"   {status}\n"
                   f"   📅 آخر نشاط: {last_active_str}\n\n")
    
    await update.message.reply_text(message)

async def handle_admin_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدارة المديرين"""
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول لهذا الأمر.")
        return
    
    keyboard = [
        [KeyboardButton("عرض جميع المديرين 👨‍💼")],
        [KeyboardButton("إضافة مدير جديد ➕"), KeyboardButton("حذف مدير 🗑️")],
        [KeyboardButton("رجوع ↩️")]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("👨‍💼 إدارة المديرين", reply_markup=reply_markup)

async def handle_show_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض جميع المديرين"""
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول لهذا الأمر.")
        return
    
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT a.*, e.full_name 
            FROM admins a 
            LEFT JOIN employees e ON a.telegram_id = e.telegram_id
            ORDER BY a.is_super_admin DESC, a.added_at
        """)
        admins = cur.fetchall()
        cur.close()
        conn.close()
        
        message = "👨‍💼 قائمة المديرين:\n\n"
        
        for i, admin in enumerate(admins, 1):
            role = "👑 مدير رئيسي" if admin['is_super_admin'] else "👨‍💼 مدير عادي"
            can_approve = "✅ نعم" if admin['can_approve'] else "❌ لا"
            name = admin['full_name'] or "غير معروف"
            
            message += (f"{i}. {name}\n"
                       f"   🆔 {admin['telegram_id']}\n"
                       f"   {role}\n"
                       f"   صلاحية الموافقة: {can_approve}\n"
                       f"   📅 تاريخ الإضافة: {admin['added_at'].strftime('%Y-%m-%d')}\n\n")
        
        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"خطأ في عرض المديرين: {e}")
        await update.message.reply_text("❌ حدث خطأ في عرض قائمة المديرين.")

async def handle_request_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدارة الطلبات"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول لهذا الأمر.")
        return
    
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT r.*, e.full_name, e.phone_number
            FROM requests r
            JOIN employees e ON r.employee_id = e.id
            WHERE r.status = 'pending'
            ORDER BY r.requested_at
        """)
        pending_requests = cur.fetchall()
        cur.close()
        conn.close()
        
        if not pending_requests:
            await update.message.reply_text("✅ لا توجد طلبات معلقة حالياً.")
            return
        
        message = "📋 الطلبات المعلقة:\n\n"
        
        for req in pending_requests:
            req_type = "إذن خروج 🚪" if req['request_type'] == 'leave' else "إجازة 🏖️'
            requested_at = req['requested_at'].strftime("%Y-%m-%d %H:%M")
            
            message += (f"🆔 رقم الطلب: {req['id']}\n"
                       f"👤 الموظف: {req['full_name']}\n"
                       f"📞 الرقم: {req['phone_number']}\n"
                       f"📋 النوع: {req_type}\n"
                       f"📝 الملاحظات: {req['notes'] or 'لا توجد'}\n"
                       f"🕒 وقت الطلب: {requested_at}\n"
                       f"────────────────────\n")
        
        message += "\nاستخدم الأزرار في الرسالة الأصلية للموافقة أو الرفض."
        
        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"خطأ في عرض الطلبات: {e}")
        await update.message.reply_text("❌ حدث خطأ في عرض الطلبات المعلقة.")

async def handle_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض إحصائيات الموظف"""
    user = update.effective_user
    employee = get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text("❌ لم يتم العثور على بياناتك. يرجى التسجيل أولاً.")
        return
    
    today_attendance = get_attendance_today(employee['id'])
    smoke_count = get_smoke_count_db(employee['id'])
    max_smokes = get_max_daily_smokes()
    lunch_taken = has_taken_lunch_break_today(employee['id'])
    weekly_report = get_employee_attendance_report(employee['id'], 7)
    
    message = f"📊 إحصائيات {employee['full_name']}\n\n"
    
    if today_attendance:
        check_in = today_attendance['check_in_time'].strftime("%H:%M:%S")
        if today_attendance['check_out_time']:
            check_out = today_attendance['check_out_time'].strftime("%H:%M:%S")
            total_hours = today_attendance['total_work_hours']
            overtime = today_attendance['overtime_hours']
            
            message += f"🟢 الحضور: {check_in}\n"
            message += f"🔴 الانصراف: {check_out}\n"
            message += f"🕐 ساعات العمل: {total_hours:.2f}h\n"
            if overtime > 0:
                message += f"⏱️ ساعات إضافية: {overtime:.2f}h\n"
            
            if today_attendance['is_late']:
                message += f"⚠️ تأخر: {today_attendance['late_minutes']} دقيقة\n"
        else:
            message += f"🟢 الحضور: {check_in}\n"
            message += f"🔴 الانصراف: لم ينصرف بعد\n"
    else:
        message += "❌ لم يسجل الحضور اليوم\n"
    
    message += f"\n🚬 السجائر اليوم: {smoke_count}/{max_smokes}\n"
    message += f"🍽️ بريك الغداء: {'✅ مأخوذ' if lunch_taken else '❌ لم يؤخذ بعد'}\n"
    
    if weekly_report:
        present_days = sum(1 for day in weekly_report if day['check_in_time'])
        late_days = sum(1 for day in weekly_report if day.get('is_late'))
        total_hours = sum(day['total_work_hours'] or 0 for day in weekly_report)
        
        message += f"\n📈 إحصائيات الأسبوع:\n"
        message += f"✅ أيام الحضور: {present_days}/7\n"
        message += f"⚠️ أيام التأخر: {late_days}\n"
        message += f"🕐 إجمالي الساعات: {total_hours:.1f}h\n"
    
    await update.message.reply_text(message)

async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض رسالة المساعدة"""
    help_text = """
🤖 دليل استخدام بوت إدارة الموظفين

🟢 **تسجيل الحضور**: اضغط عند وصولك للعمل
🔴 **تسجيل الانصراف**: اضغط عند مغادرة العمل

🚬 **تسجيل سيجارة**: اضغط عند كل سيجارة (حد أقصى 6 يومياً، 3 في الجمعة)
🍽️ **بريك الغداء**: اضغط عند أخذ استراحة الغداء

🚪 **طلب إذن خروج**: لطلب الخروج أثناء الدوام
🏖️ **طلب إجازة**: لطلب إجازة

📊 **عرض الإحصائيات**: لعرض إحصائياتك اليومية والأسبوعية

👨‍💼 **للمديرين**: 
   - عرض تقارير الحضور
   - إدارة الموظفين والطلبات
   - إدارة النظام

للاستفسارات أو المشاكل، تواصل مع المدير.
"""
    await update.message.reply_text(help_text)

async def handle_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """العودة للقائمة الرئيسية"""
    await show_main_menu(update, context)

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة استجابات الأزرار"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.message.reply_text("❌ ليس لديك صلاحية للقيام بهذا الإجراء.")
        return
    
    if not can_approve_requests(user_id):
        await query.message.reply_text("❌ ليس لديك صلاحية للموافقة على الطلبات.")
        return
    
    if data.startswith('approve_leave_'):
        request_id = int(data.split('_')[2])
        success = update_request_status(request_id, 'approved', 'تمت الموافقة على إذن الخروج')
        
        if success:
            await query.message.reply_text(f"✅ تمت الموافقة على طلب إذن الخروج رقم {request_id}")
            await query.edit_message_text(f"✅ {query.message.text}\n\n✅ تمت الموافقة من قبل المدير")
        else:
            await query.message.reply_text("❌ حدث خطأ في معالجة الطلب")
    
    elif data.startswith('reject_leave_'):
        request_id = int(data.split('_')[2])
        success = update_request_status(request_id, 'rejected', 'تم رفض إذن الخروج')
        
        if success:
            await query.message.reply_text(f"❌ تم رفض طلب إذن الخروج رقم {request_id}")
            await query.edit_message_text(f"✅ {query.message.text}\n\n❌ تم الرفض من قبل المدير")
        else:
            await query.message.reply_text("❌ حدث خطأ في معالجة الطلب")
    
    elif data.startswith('approve_vacation_'):
        request_id = int(data.split('_')[2])
        success = update_request_status(request_id, 'approved', 'تمت الموافقة على الإجازة')
        
        if success:
            await query.message.reply_text(f"✅ تمت الموافقة على طلب الإجازة رقم {request_id}")
            await query.edit_message_text(f"✅ {query.message.text}\n\n✅ تمت الموافقة من قبل المدير")
        else:
            await query.message.reply_text("❌ حدث خطأ في معالجة الطلب")
    
    elif data.startswith('reject_vacation_'):
        request_id = int(data.split('_')[2])
        success = update_request_status(request_id, 'rejected', 'تم رفض الإجازة')
        
        if success:
            await query.message.reply_text(f"❌ تم رفض طلب الإجازة رقم {request_id}")
            await query.edit_message_text(f"✅ {query.message.text}\n\n❌ تم الرفض من قبل المدير")
        else:
            await query.message.reply_text("❌ حدث خطأ في معالجة الطلب")

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    if not BOT_TOKEN:
        logger.error("لم يتم تعيين BOT_TOKEN في متغيرات البيئة")
        return
    
    if not initialize_database_tables():
        logger.error("فشل في تهيئة جداول قاعدة البيانات")
        return
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    conv_handler_leave = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^طلب إذن خروج 🚪$"), handle_leave_request)],
        states={
            LEAVE_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_leave_reason)]
        },
        fallbacks=[MessageHandler(filters.Regex("^إلغاء ❌$"), cancel_conversation)]
    )
    
    conv_handler_vacation = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^طلب إجازة 🏖️$"), handle_vacation_request)],
        states={
            VACATION_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_vacation_reason)]
        },
        fallbacks=[MessageHandler(filters.Regex("^إلغاء ❌$"), cancel_conversation)]
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(conv_handler_leave)
    application.add_handler(conv_handler_vacation)
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    
    application.add_handler(MessageHandler(filters.Regex("^تسجيل الحضور 🟢$"), handle_check_in))
    application.add_handler(MessageHandler(filters.Regex("^تسجيل الانصراف 🔴$"), handle_check_out))
    application.add_handler(MessageHandler(filters.Regex("^تسجيل سيجارة 🚬$"), handle_smoke_request))
    application.add_handler(MessageHandler(filters.Regex("^بريك الغداء 🍽️$"), handle_lunch_break))
    application.add_handler(MessageHandler(filters.Regex("^عرض الإحصائيات 📊$"), handle_stats))
    application.add_handler(MessageHandler(filters.Regex("^المساعدة ℹ️$"), handle_help))
    application.add_handler(MessageHandler(filters.Regex("^رجوع ↩️$"), handle_back))
    
    application.add_handler(MessageHandler(filters.Regex("^لوحة التحكم 👨‍💼$"), show_admin_menu))
    application.add_handler(MessageHandler(filters.Regex("^عرض تقرير اليوم 📅$"), handle_admin_report))
    application.add_handler(MessageHandler(filters.Regex("^عرض تقرير الأسبوع 📊$"), handle_weekly_report))
    application.add_handler(MessageHandler(filters.Regex("^إدارة الموظفين 👥$"), handle_employee_management))
    application.add_handler(MessageHandler(filters.Regex("^إدارة المديرين 👨‍💼$"), handle_admin_management))
    application.add_handler(MessageHandler(filters.Regex("^إدارة الطلبات 📋$"), handle_request_management))
    application.add_handler(MessageHandler(filters.Regex("^عرض جميع الموظفين 👥$"), handle_show_employees))
    application.add_handler(MessageHandler(filters.Regex("^عرض جميع المديرين 👨‍💼$"), handle_show_admins))
    
    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == '__main__':
    main()