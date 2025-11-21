import os
import logging
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes
import asyncio
from functools import partial

# ==============================================================================
# ⚙️ الإعدادات العامة (Configuration)
# ==============================================================================

# الحالات الخاصة بالمحادثات (Conversation States)
LEAVE_REASON, VACATION_REASON = range(2)
EDIT_SELECT_EMPLOYEE, EDIT_SELECT_FIELD, EDIT_INPUT_VALUE = range(2, 5)

# التوكن ورابط قاعدة البيانات من متغيرات البيئة
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

# إعدادات الوقت والمنطقة
JORDAN_TZ = ZoneInfo('Asia/Amman')
WORK_START_HOUR = 8
WORK_START_MINUTE = 0
WORK_REGULAR_HOURS = 9
MAX_DAILY_SMOKES = 6
LATE_GRACE_PERIOD_MINUTES = 15

# قائمة المديرين (يمكنك تعديلها هنا أو إضافتها عبر البوت)
ADMIN_IDS = [1465191277]  

# إعدادات التسجيل (Logging)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# متغيرات الذاكرة المؤقتة (Caching)
authorized_phones = []  # سيتم تحميلها من قاعدة البيانات عند التشغيل
active_timers = {}
timer_completed = {}

# ==============================================================================
# 🗄️ إدارة قاعدة البيانات (Database Management)
# ==============================================================================

# إنشاء مجمع اتصالات (Connection Pool) للأداء العالي
try:
    db_pool = psycopg2.pool.SimpleConnectionPool(
        1, 20,  # minconn, maxconn
        dsn=DATABASE_URL
    )
    if db_pool:
        logger.info("✅ Database connection pool created successfully")
except Exception as e:
    logger.error(f"❌ Error creating connection pool: {e}")
    db_pool = None

def get_db_connection():
    """الحصول على اتصال من المجمع"""
    try:
        return db_pool.getconn()
    except Exception as e:
        logger.error(f"Error getting connection from pool: {e}")
        # محاولة إنشاء اتصال جديد إذا فشل المجمع
        return psycopg2.connect(DATABASE_URL)

def release_db_connection(conn):
    """إعادة الاتصال إلى المجمع"""
    try:
        if db_pool:
            db_pool.putconn(conn)
        else:
            conn.close()
    except Exception as e:
        logger.error(f"Error releasing connection: {e}")

def execute_query(query, params=None, fetch_one=False, fetch_all=False, commit=False):
    """دالة مساعدة لتنفيذ الاستعلامات بأمان"""
    conn = None
    result = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(query, params)
        
        if commit:
            conn.commit()
            # لعمليات الإدخال التي تعيد ID
            if 'RETURNING' in query.upper():
                result = cur.fetchone()
        
        if fetch_one:
            result = cur.fetchone()
        elif fetch_all:
            result = cur.fetchall()
            
        cur.close()
    except Exception as e:
        logger.error(f"Database query error: {e} | Query: {query}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            release_db_connection(conn)
    return result

def initialize_database_tables():
    """إنشاء الجداول المطلوبة إذا لم تكن موجودة"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # جدول الموظفين
        cur.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                phone_number VARCHAR(20) NOT NULL UNIQUE,
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
        
        # جدول السجائر
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
        
        # جدول الحضور
        cur.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                check_in_time TIMESTAMP WITH TIME ZONE,
                check_out_time TIMESTAMP WITH TIME ZONE,
                is_late BOOLEAN DEFAULT FALSE,
                late_minutes INTEGER DEFAULT 0,
                total_work_hours DECIMAL(4,2),
                overtime_hours DECIMAL(4,2) DEFAULT 0,
                status VARCHAR(20) DEFAULT 'present',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, date)
            );
        """)

        # الجداول الأخرى
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lunch_breaks (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                taken BOOLEAN DEFAULT FALSE,
                taken_at TIMESTAMP WITH TIME ZONE,
                UNIQUE(employee_id, date)
            );
            CREATE TABLE IF NOT EXISTS cigarette_times (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                taken_at TIMESTAMP WITH TIME ZONE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS warnings (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                warning_type VARCHAR(50),
                warning_reason TEXT,
                date DATE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS absences (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                absence_type VARCHAR(50),
                reason TEXT,
                UNIQUE(employee_id, date)
            );
        """)
        
        conn.commit()
        cur.close()
        logger.info("✅ Database tables initialized successfully")
    except Exception as e:
        logger.error(f"❌ Error initializing database tables: {e}")
    finally:
        if conn:
            release_db_connection(conn)

# ==============================================================================
# 🛠️ دوال مساعدة (Helpers & Logic)
# ==============================================================================

def get_jordan_time():
    return datetime.now(JORDAN_TZ)

def normalize_phone(phone_number):
    if not phone_number: return ""
    digits = ''.join(filter(str.isdigit, phone_number))
    while digits.startswith('00'): digits = digits[2:]
    if digits.startswith('0'): digits = digits[1:] # Remove leading zero for standardizing
    return digits

def get_all_admins():
    """جلب قائمة المديرين"""
    query = "SELECT telegram_id, is_super_admin FROM admins"
    results = execute_query(query, fetch_all=True)
    admin_ids = [row['telegram_id'] for row in results] if results else []
    
    # دمج المديرين من الكود ومن قاعدة البيانات
    return list(set(ADMIN_IDS + admin_ids))

def is_admin(user_id):
    return user_id in get_all_admins()

def verify_employee(phone_number):
    """التحقق من أن الموظف مصرح له"""
    norm_input = normalize_phone(phone_number)
    for auth_phone in authorized_phones:
        if normalize_phone(auth_phone) == norm_input:
            return True
    return False

# --- دوال الموظفين ---

def save_employee(telegram_id, phone_number, full_name):
    norm_phone = normalize_phone(phone_number)
    
    # التحقق مما إذا كان موجوداً برقم الهاتف
    existing = execute_query("SELECT * FROM employees WHERE phone_number = %s", (phone_number,), fetch_one=True)
    
    if existing:
        execute_query(
            "UPDATE employees SET telegram_id = %s, full_name = %s, last_active = CURRENT_TIMESTAMP WHERE id = %s",
            (telegram_id, full_name, existing['id']), commit=True
        )
        return existing['id']
    else:
        res = execute_query(
            "INSERT INTO employees (telegram_id, phone_number, full_name) VALUES (%s, %s, %s) RETURNING id",
            (telegram_id, phone_number, full_name), commit=True
        )
        return res['id'] if res else None

def get_employee_by_telegram_id(telegram_id):
    return execute_query("SELECT * FROM employees WHERE telegram_id = %s", (telegram_id,), fetch_one=True)

def get_employee_by_phone(phone):
    return execute_query("SELECT * FROM employees WHERE phone_number = %s", (phone,), fetch_one=True)

def get_all_employees():
    return execute_query("SELECT * FROM employees ORDER BY full_name", fetch_all=True)

# --- دوال الحضور والانصراف ---

def record_check_in(employee_id):
    now = get_jordan_time()
    today = now.date()
    
    existing = execute_query(
        "SELECT check_in_time, is_late, late_minutes FROM attendance WHERE employee_id = %s AND date = %s",
        (employee_id, today), fetch_one=True
    )
    
    if existing:
        return {'success': False, 'error': 'already_checked_in', 'data': existing}
    
    work_start = now.replace(hour=WORK_START_HOUR, minute=WORK_START_MINUTE, second=0)
    late_minutes = max(0, int((now - work_start).total_seconds() / 60))
    is_late = late_minutes > LATE_GRACE_PERIOD_MINUTES
    
    res = execute_query(
        """
        INSERT INTO attendance (employee_id, date, check_in_time, is_late, late_minutes, status)
        VALUES (%s, %s, %s, %s, %s, 'present')
        RETURNING check_in_time, is_late, late_minutes
        """,
        (employee_id, today, now, is_late, late_minutes), commit=True
    )
    
    if res:
        return {'success': True, 'check_in_time': res['check_in_time'], 'is_late': res['is_late'], 'late_minutes': res['late_minutes']}
    return {'success': False, 'error': 'Database error'}

def record_check_out(employee_id):
    now = get_jordan_time()
    today = now.date()
    
    att = execute_query(
        "SELECT check_in_time, check_out_time, total_work_hours FROM attendance WHERE employee_id = %s AND date = %s",
        (employee_id, today), fetch_one=True
    )
    
    if not att:
        return {'success': False, 'error': 'لم تقم بتسجيل الحضور اليوم'}
    if att['check_out_time']:
        return {'success': False, 'error': 'already_checked_out', 'data': att}
    
    check_in_time = att['check_in_time']
    # تحويل للتوقيت المحلي للحساب
    if check_in_time.tzinfo is None:
        check_in_time = check_in_time.replace(tzinfo=JORDAN_TZ)
    else:
        check_in_time = check_in_time.astimezone(JORDAN_TZ)
        
    work_hours = (now - check_in_time).total_seconds() / 3600
    
    # خصم نصف ساعة غداء إذا عمل أكثر من ساعة
    if work_hours >= 1.0: work_hours -= 0.5
    work_hours = max(0, work_hours)
    
    overtime = max(0, work_hours - WORK_REGULAR_HOURS)
    
    res = execute_query(
        """
        UPDATE attendance
        SET check_out_time = %s, total_work_hours = %s, overtime_hours = %s
        WHERE employee_id = %s AND date = %s
        RETURNING check_out_time, total_work_hours, overtime_hours
        """,
        (now, round(work_hours, 2), round(overtime, 2), employee_id, today), commit=True
    )
    
    if res:
        return {
            'success': True, 
            'check_in_time': check_in_time,
            'check_out_time': res['check_out_time'], 
            'total_work_hours': res['total_work_hours'],
            'overtime_hours': res['overtime_hours']
        }
    return {'success': False, 'error': 'Database Update Error'}

# ==============================================================================
# 🤖 معالجات البوت (Bot Handlers)
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    
    # تنفيذ استعلام قاعدة البيانات بشكل غير متزامن
    loop = asyncio.get_running_loop()
    employee = await loop.run_in_executor(None, get_employee_by_telegram_id, user.id)
    
    user_phone = employee['phone_number'] if employee else None
    user_name = employee['full_name'] if employee else user.first_name
    
    if user_phone and verify_employee(user_phone):
        msg = (
            f"مرحبًا {user_name}! 👋\n\n"
            "✅ تم التحقق من هويتك بنجاح!\n\n"
            "💼 **القائمة الرئيسية:**\n"
            "/check_in - تسجيل حضور 📥\n"
            "/check_out - تسجيل انصراف 📤\n"
            "/smoke - استراحة تدخين 🚬\n"
            "/break - استراحة غداء ☕\n"
            "/leave - طلب مغادرة 🚪\n"
            "/attendance_report - تقريري 📊"
        )
        if is_admin(user.id):
            msg += "\n\n👑 **أوامر المدير:**\n/edit_details - تعديل بيانات موظف\n/list_employees - قائمة الموظفين\n/daily_report - تقرير يومي"
            
        await update.message.reply_text(msg, parse_mode='Markdown')
    else:
        keyboard = [[KeyboardButton("مشاركة رقم الهاتف 📱", request_contact=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text(
            "👋 مرحبًا بك في نظام الحضور.\n⚠️ للبدء، يرجى مشاركة رقم هاتفك للتحقق من الهوية.",
            reply_markup=reply_markup
        )

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    user = update.message.from_user
    
    if contact.user_id != user.id:
        await update.message.reply_text("⚠️ يرجى مشاركة رقمك الخاص.")
        return
        
    phone_number = contact.phone_number
    if not phone_number.startswith('+'): phone_number = '+' + phone_number
    full_name = f"{contact.first_name} {contact.last_name or ''}".strip()
    
    loop = asyncio.get_running_loop()
    
    # حفظ البيانات
    await loop.run_in_executor(None, save_employee, user.id, phone_number, full_name)
    
    # التحقق من الصلاحية (مؤقتاً نضيف أي شخص يشارك رقمه لقائمة المصرح لهم للتجربة)
    # في الإنتاج، يجب أن يكون الرقم مضافاً مسبقاً من قبل المدير
    norm_phone = normalize_phone(phone_number)
    found = False
    for p in authorized_phones:
        if normalize_phone(p) == norm_phone:
            found = True
            break
            
    if not found:
        authorized_phones.append(phone_number) # إضافة تلقائية للتسهيل عليك
        found = True

    if found:
        await update.message.reply_text(
            f"✅ تم تسجيلك بنجاح يا {full_name}!\nرقم الهاتف: {phone_number}\n\nاستخدم /start لعرض القائمة."
        )
    else:
        await update.message.reply_text("⚠️ رقمك غير مسجل في النظام. راجع المدير.")

# --- أوامر الحضور ---

async def check_in_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    loop = asyncio.get_running_loop()
    
    # Get employee ID async
    emp = await loop.run_in_executor(None, get_employee_by_telegram_id, user.id)
    if not emp:
        await update.message.reply_text("❌ يجب تسجيل بياناتك أولاً عبر /start")
        return

    # Record check-in async
    res = await loop.run_in_executor(None, record_check_in, emp['id'])
    
    if not res['success']:
        if res.get('error') == 'already_checked_in':
            await update.message.reply_text(f"⚠️ لقد سجلت دخول مسبقاً عند {res['data']['check_in_time'].strftime('%H:%M')}")
        else:
            await update.message.reply_text("❌ حدث خطأ في النظام.")
        return

    msg = f"✅ تم تسجيل الحضور: {res['check_in_time'].strftime('%H:%M')}"
    if res['is_late']:
        msg += f"\n⚠️ **متأخر** بمقدار {res['late_minutes']} دقيقة!"
        # Notify Admins logic here
        
    await update.message.reply_text(msg, parse_mode='Markdown')

async def check_out_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    loop = asyncio.get_running_loop()
    
    emp = await loop.run_in_executor(None, get_employee_by_telegram_id, user.id)
    if not emp:
        await update.message.reply_text("❌ غير مسجل.")
        return

    res = await loop.run_in_executor(None, record_check_out, emp['id'])
    
    if not res['success']:
        await update.message.reply_text(f"⚠️ {res['error']}")
        return

    msg = (
        f"🚪 **تسجيل انصراف**\n"
        f"✅ وقت الانصراف: {res['check_out_time'].strftime('%H:%M')}\n"
        f"⏱ ساعات العمل: {res['total_work_hours']} ساعة"
    )
    if res['overtime_hours'] > 0:
        msg += f"\n🌟 **إضافي:** {res['overtime_hours']} ساعة"
        
    await update.message.reply_text(msg, parse_mode='Markdown')

# ==============================================================================
# ✏️ نظام تعديل بيانات الموظفين (Conversation Handler)
# ==============================================================================

async def edit_details_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر للمديرين فقط.")
        return ConversationHandler.END
        
    loop = asyncio.get_running_loop()
    employees = await loop.run_in_executor(None, get_all_employees)
    
    if not employees:
        await update.message.reply_text("📭 لا يوجد موظفين.")
        return ConversationHandler.END

    keyboard = []
    for emp in employees:
        keyboard.append([InlineKeyboardButton(f"{emp['full_name']}", callback_data=f"sel_emp_{emp['id']}")])
    
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel_edit")])
    
    await update.message.reply_text(
        "👥 اختر الموظف لتعديل بياناته:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EDIT_SELECT_EMPLOYEE

async def edit_select_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data == "cancel_edit":
        await query.edit_message_text("❌ تم الإلغاء.")
        return ConversationHandler.END
        
    if data.startswith("sel_emp_"):
        emp_id = int(data.split("_")[2])
        context.user_data['edit_emp_id'] = emp_id
        
        # أزرار الحقول
        keyboard = [
            [InlineKeyboardButton("👤 الاسم", callback_data="field_full_name")],
            [InlineKeyboardButton("📱 الهاتف", callback_data="field_phone_number")],
            [InlineKeyboardButton("💼 الوظيفة", callback_data="field_job_title")],
            [InlineKeyboardButton("🎂 العمر", callback_data="field_age")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_list")]
        ]
        
        await query.edit_message_text(
            "📝 ماذا تريد أن تعدل؟",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return EDIT_SELECT_FIELD

async def edit_ask_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data == "back_to_list":
        # العودة للقائمة (تحتاج استدعاء الدالة الأولى مرة أخرى، للأبسط سنلغي)
        await query.edit_message_text("🔙 أعد الأمر /edit_details للبدء من جديد.")
        return ConversationHandler.END
        
    field_map = {
        "field_full_name": "الاسم الكامل",
        "field_phone_number": "رقم الهاتف",
        "field_job_title": "المسمى الوظيفي",
        "field_age": "العمر"
    }
    
    field_db_name = data.replace("field_", "")
    context.user_data['edit_field'] = field_db_name
    
    await query.edit_message_text(f"✍️ أرسل القيمة الجديدة لـ ({field_map.get(data, field_db_name)}):")
    return EDIT_INPUT_VALUE

async def edit_save_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_value = update.message.text
    emp_id = context.user_data.get('edit_emp_id')
    field = context.user_data.get('edit_field')
    
    if not emp_id or not field:
        await update.message.reply_text("❌ حدث خطأ في الجلسة. حاول مجدداً.")
        return ConversationHandler.END
    
    # معالجة خاصة للأرقام
    if field == 'age':
        if not new_value.isdigit():
            await update.message.reply_text("⚠️ العمر يجب أن يكون رقماً. حاول مرة أخرى.")
            return EDIT_INPUT_VALUE
        new_value = int(new_value)

    loop = asyncio.get_running_loop()
    query = f"UPDATE employees SET {field} = %s WHERE id = %s"
    
    await loop.run_in_executor(None, execute_query, query, (new_value, emp_id), False, False, True)
    
    await update.message.reply_text("✅ تم تحديث البيانات بنجاح!")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم إلغاء العملية.")
    return ConversationHandler.END

# ==============================================================================
# 🚬 استراحات التدخين (Smoke Logic)
# ==============================================================================

async def smoke_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    
    # منطق التدخين هنا (تحقق من العدد، إرسال للمدير)
    # للاختصار في هذا المثال، سنقوم بالموافقة المباشرة وإظهار المؤقت
    # في الإنتاج، اربطها بنظام الموافقة
    
    # 1. التحقق من الموظف
    loop = asyncio.get_running_loop()
    emp = await loop.run_in_executor(None, get_employee_by_telegram_id, user.id)
    if not emp: return
    
    # 2. التحقق من العدد (من قاعدة البيانات)
    today = datetime.now().date()
    smoke_record = await loop.run_in_executor(None, execute_query, 
        "SELECT count FROM daily_cigarettes WHERE employee_id = %s AND date = %s", 
        (emp['id'], today), True)
    
    count = smoke_record['count'] if smoke_record else 0
    
    if count >= MAX_DAILY_SMOKES:
        await update.message.reply_text("❌ وصلت للحد الأقصى اليوم!")
        return
        
    # 3. تسجيل (زيادة العداد)
    await loop.run_in_executor(None, execute_query,
        """
        INSERT INTO daily_cigarettes (employee_id, date, count) VALUES (%s, %s, 1)
        ON CONFLICT (employee_id, date) DO UPDATE SET count = daily_cigarettes.count + 1
        """, (emp['id'], today), False, False, True)

    # 4. بدء المؤقت
    await start_countdown(update, context, 5, "🚬 استراحة تدخين")

async def start_countdown(update, context, minutes, title):
    end_time = datetime.now(JORDAN_TZ) + timedelta(minutes=minutes)
    msg = await update.message.reply_text(f"⏳ {title} بدأت!\nالوقت: {minutes} دقيقة.")
    
    # في الإنتاج نستخدم JobQueue للتحديث
    # هنا محاكاة بسيطة
    context.job_queue.run_once(alarm, minutes * 60, chat_id=update.effective_chat.id, data=title)

async def alarm(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.send_message(job.chat_id, text=f"🔔 انتهى وقت {job.data}! عد للعمل.")


# ==============================================================================
# 🚀 التشغيل الرئيسي (Main Execution)
# ==============================================================================

def main():
    if not BOT_TOKEN or not DATABASE_URL:
        print("❌ ERROR: Missing TELEGRAM_BOT_TOKEN or DATABASE_URL env vars.")
        return

    print("🚀 Starting Bot with Connection Pooling...")
    
    # تهيئة الجداول
    initialize_database_tables()
    
    # تحميل الموظفين للقائمة المسموحة (اختياري للسرعة)
    employees = get_all_employees()
    if employees:
        for e in employees:
            if e['phone_number'] not in authorized_phones:
                authorized_phones.append(e['phone_number'])
    
    # بناء التطبيق
    application = Application.builder().token(BOT_TOKEN).build()

    # المحادثات (Conversations)
    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit_details", edit_details_start)],
        states={
            EDIT_SELECT_EMPLOYEE: [CallbackQueryHandler(edit_select_field)],
            EDIT_SELECT_FIELD: [CallbackQueryHandler(edit_ask_value)],
            EDIT_INPUT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_save_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(edit_select_field, pattern="^cancel")]
    )

    # إضافة المعالجات (Handlers)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(CommandHandler("check_in", check_in_command))
    application.add_handler(CommandHandler("check_out", check_out_command))
    application.add_handler(CommandHandler("smoke", smoke_request))
    application.add_handler(edit_conv)
    
    # تشغيل البوت
    print("✅ Bot is running...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
