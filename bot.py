import os
import logging
import sqlite3
import json
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from telegram import (
    Update, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes
)

# ==================== الإعدادات الأساسية ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# التوكن من البيئة
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# المناطق الزمنية
JORDAN_TZ = ZoneInfo('Asia/Amman')

# حالات المحادثة
LEAVE_REASON, VACATION_REASON = range(2)

# إعدادات النظام
WORK_START_TIME = "08:00"
WORK_END_TIME = "17:00"
LATE_GRACE_MINUTES = 15
MAX_DAILY_SMOKES = 6
MAX_DAILY_SMOKES_FRIDAY = 3
SMOKE_BREAK_DURATION = 5
LUNCH_BREAK_DURATION = 30

# قاعدة البيانات SQLite
DB_PATH = "/tmp/employee_bot.db"

# ==================== إدارة قاعدة البيانات ====================
def init_database():
    """تهيئة قاعدة البيانات"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # جدول الموظفين
    cur.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            phone_number TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            age INTEGER,
            job_title TEXT,
            department TEXT,
            hire_date TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # جدول الحضور
    cur.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER,
            date TEXT NOT NULL,
            check_in_time TIMESTAMP,
            check_out_time TIMESTAMP,
            is_late BOOLEAN DEFAULT 0,
            late_minutes INTEGER DEFAULT 0,
            total_work_hours REAL DEFAULT 0,
            overtime_hours REAL DEFAULT 0,
            status TEXT DEFAULT 'present',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(employee_id, date)
        )
    ''')
    
    # جدول استراحات التدخين
    cur.execute('''
        CREATE TABLE IF NOT EXISTS cigarette_times (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER,
            taken_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # جدول استراحات الغداء
    cur.execute('''
        CREATE TABLE IF NOT EXISTS lunch_breaks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER,
            date TEXT NOT NULL,
            taken BOOLEAN DEFAULT 0,
            taken_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(employee_id, date)
        )
    ''')
    
    # جدول المديرين
    cur.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            added_by INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_super_admin BOOLEAN DEFAULT 0,
            can_approve BOOLEAN DEFAULT 1,
            can_view_only BOOLEAN DEFAULT 0
        )
    ''')
    
    # جدول الطلبات
    cur.execute('''
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER,
            request_type TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            reason TEXT,
            requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            responded_at TIMESTAMP,
            notes TEXT
        )
    ''')
    
    # إدخال المديرين الرئيسيين
    cur.execute('''
        INSERT OR IGNORE INTO admins (telegram_id, is_super_admin, can_approve, can_view_only) 
        VALUES (1465191277, 1, 1, 0), (6798279805, 1, 1, 0)
    ''')
    
    conn.commit()
    conn.close()
    print("✅ تم تهيئة قاعدة البيانات بنجاح")

def get_db_connection():
    """الحصول على اتصال بقاعدة البيانات"""
    return sqlite3.connect(DB_PATH)

# ==================== دوال المساعدة ====================
def get_jordan_time():
    """الحصول على الوقت الحالي بتوقيت الأردن"""
    return datetime.now(JORDAN_TZ)

def is_friday():
    """التحقق إذا كان اليوم جمعة"""
    return get_jordan_time().weekday() == 4

def get_max_daily_smokes():
    """الحصول على الحد الأقصى للسجائر حسب اليوم"""
    return MAX_DAILY_SMOKES_FRIDAY if is_friday() else MAX_DAILY_SMOKES

def can_take_lunch_break():
    """التحقق إذا كان مسموحاً بأخذ بريك غداء"""
    return not is_friday()

def get_employee_by_telegram_id(telegram_id):
    """الحصول على بيانات الموظف"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM employees WHERE telegram_id = ?", (telegram_id,))
    employee = cur.fetchone()
    conn.close()
    
    if employee:
        return {
            'id': employee[0],
            'telegram_id': employee[1],
            'phone_number': employee[2],
            'full_name': employee[3],
            'age': employee[4],
            'job_title': employee[5],
            'department': employee[6],
            'hire_date': employee[7],
            'is_active': employee[8]
        }
    return None

def is_admin(user_id):
    """التحقق إذا كان المستخدم مدير"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM admins WHERE telegram_id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    return bool(result)

def is_super_admin(user_id):
    """التحقق إذا كان المستخدم مدير رئيسي"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_super_admin FROM admins WHERE telegram_id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    return result[0] if result else False

# ==================== Handlers الأساسية ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت"""
    user = update.message.from_user
    
    keyboard = [[KeyboardButton("مشاركة رقم الهاتف 📱", request_contact=True)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    welcome_message = (
        "👋 أهلاً وسهلاً!\n\n"
        "🤖 **بوت إدارة حضور الموظفين**\n\n"
        "📍 للمتابعة، يرجى مشاركة رقم هاتفك للتحقق من هويتك:\n\n"
        "⬇️ اضغط على الزر أدناه"
    )
    
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة مشاركة رقم الهاتف"""
    try:
        contact = update.message.contact
        user = update.message.from_user
        
        if contact and contact.user_id == user.id:
            # حفظ البيانات في قاعدة البيانات
            conn = get_db_connection()
            cur = conn.cursor()
            
            cur.execute('''
                INSERT OR REPLACE INTO employees 
                (telegram_id, phone_number, full_name, last_active)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (user.id, contact.phone_number, contact.first_name or "موظف"))
            
            conn.commit()
            conn.close()
            
            await update.message.reply_text(
                f"✅ **تم التحقق بنجاح!**\n\n"
                f"👤 **الاسم:** {contact.first_name}\n"
                f"📱 **الهاتف:** {contact.phone_number}\n\n"
                "🎉 يمكنك الآن استخدام جميع ميزات البوت!",
                reply_markup=ReplyKeyboardRemove()
            )
            
            # إرسال رسالة الترحيب النهائية
            await help_command(update, context)
            
    except Exception as e:
        logger.error(f"خطأ في معالجة الاتصال: {e}")
        await update.message.reply_text("❌ حدث خطأ غير متوقع")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض رسالة المساعدة"""
    user = update.message.from_user
    
    help_text = (
        "📚 **قائمة الأوامر المتاحة:**\n\n"
        "🔹 **الحضور والانصراف:**\n"
        "/check_in - تسجيل الحضور 📥\n"
        "/check_out - تسجيل الانصراف 📤\n"
        "/attendance_report - تقرير حضورك 📊\n\n"
        "🔹 **الاستراحات:**\n"
        "/smoke - طلب استراحة تدخين 🚬\n"
        "/break - طلب استراحة غداء ☕\n\n"
        "🔹 **الإجازات:**\n"
        "/leave - طلب مغادرة العمل 🚪\n"
        "/vacation - طلب عطلة 🌴\n\n"
        "🔹 **المساعدة:**\n"
        "/help - عرض هذه الرسالة\n"
        "/my_id - عرض معرفك الشخصي\n"
    )
    
    # إضافة أوامر المدير إذا كان المستخدم مدير
    if is_admin(user.id):
        help_text += (
            "\n🔸 **أوامر المدير:**\n"
            "/admin - لوحة تحكم المدير 👨‍💼\n"
            "/daily_report - التقرير اليومي 📈\n"
            "/weekly_report - التقرير الأسبوعي 📊\n"
        )
    
    if is_super_admin(user.id):
        help_text += (
            "\n🔸 **أوامر المدير الرئيسي:**\n"
            "/add_manager - إضافة مدير جديد ➕\n"
        )
    
    await update.message.reply_text(help_text)

async def my_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض معرف المستخدم"""
    user = update.message.from_user
    employee = get_employee_by_telegram_id(user.id)
    
    message = (
        f"🆔 **معلومات حسابك:**\n\n"
        f"👤 **الاسم:** {employee['full_name'] if employee else user.first_name}\n"
        f"🔢 **معرف Telegram:** `{user.id}`\n"
    )
    
    if is_admin(user.id):
        admin_type = "👑 مدير رئيسي" if is_super_admin(user.id) else "👨‍💼 مدير"
        message += f"\n✅ **الصفة:** {admin_type}"
    
    await update.message.reply_text(message, parse_mode='Markdown')

# ==================== إدارة الحضور ====================
async def check_in_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل الحضور"""
    try:
        user = update.message.from_user
        employee = get_employee_by_telegram_id(user.id)
        
        if not employee:
            await update.message.reply_text("❌ يرجى استخدام /start أولاً للتحقق من هويتك.")
            return
        
        now = get_jordan_time()
        today = now.date().isoformat()
        
        # التحقق من التسجيل المسبق
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT check_in_time FROM attendance WHERE employee_id = ? AND date = ?", 
                   (employee['id'], today))
        existing = cur.fetchone()
        
        if existing:
            await update.message.reply_text("⚠️ لقد سجلت حضورك مسبقاً اليوم!")
            conn.close()
            return
        
        # حساب التأخير
        work_start = now.replace(hour=8, minute=0, second=0, microsecond=0)
        late_minutes = max(0, int((now - work_start).total_seconds() / 60))
        is_late = late_minutes > LATE_GRACE_MINUTES
        
        # تسجيل الحضور
        cur.execute('''
            INSERT INTO attendance 
            (employee_id, date, check_in_time, is_late, late_minutes)
            VALUES (?, ?, ?, ?, ?)
        ''', (employee['id'], today, now.isoformat(), is_late, late_minutes))
        
        conn.commit()
        conn.close()
        
        if is_late:
            message = (
                f"⚠️ **تم تسجيل الحضور مع تأخير!**\n\n"
                f"👤 **الموظف:** {employee['full_name']}\n"
                f"⏰ **الوقت:** {now.strftime('%H:%M:%S')}\n"
                f"📅 **التاريخ:** {now.strftime('%Y-%m-%d')}\n"
                f"⏱ **التأخير:** {late_minutes} دقيقة\n\n"
                f"🚨 **يرجى الالتزام بموعد الحضور!**"
            )
        else:
            if late_minutes > 0:
                time_status = f"⏱ التأخير: {late_minutes} دقيقة (ضمن الوقت المسموح)"
            else:
                time_status = "🎯 في الوقت المحدد!"
            
            message = (
                f"✅ **تم تسجيل الحضور بنجاح!**\n\n"
                f"👤 **الموظف:** {employee['full_name']}\n"
                f"⏰ **الوقت:** {now.strftime('%H:%M:%S')}\n"
                f"📅 **التاريخ:** {now.strftime('%Y-%m-%d')}\n"
                f"{time_status}\n\n"
                f"💼 **يوم عمل موفق!** 🚀"
            )
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"خطأ في تسجيل الحضور: {e}")
        await update.message.reply_text("❌ حدث خطأ في تسجيل الحضور")

async def check_out_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل الانصراف"""
    try:
        user = update.message.from_user
        employee = get_employee_by_telegram_id(user.id)
        
        if not employee:
            await update.message.reply_text("❌ يرجى استخدام /start أولاً للتحقق من هويتك.")
            return
        
        now = get_jordan_time()
        today = now.date().isoformat()
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # جلب بيانات الحضور
        cur.execute('''
            SELECT check_in_time, check_out_time FROM attendance 
            WHERE employee_id = ? AND date = ?
        ''', (employee['id'], today))
        
        record = cur.fetchone()
        
        if not record or not record[0]:
            await update.message.reply_text("❌ لم يتم تسجيل الحضور اليوم!")
            conn.close()
            return
        
        if record[1]:  # إذا كان الانصراف مسجل مسبقاً
            await update.message.reply_text("⚠️ لقد سجلت انصرافك مسبقاً اليوم!")
            conn.close()
            return
        
        # حساب ساعات العمل
        check_in_time = datetime.fromisoformat(record[0])
        work_hours = (now - check_in_time).total_seconds() / 3600
        
        # خصم ساعة الغداء إذا عمل أكثر من 6 ساعات
        if work_hours > 6:
            work_hours -= 0.5
        
        work_hours = max(0, round(work_hours, 2))
        
        # تحديث الانصراف
        cur.execute('''
            UPDATE attendance 
            SET check_out_time = ?, total_work_hours = ?
            WHERE employee_id = ? AND date = ?
        ''', (now.isoformat(), work_hours, employee['id'], today))
        
        conn.commit()
        conn.close()
        
        message = (
            f"✅ **تم تسجيل الانصراف بنجاح!**\n\n"
            f"👤 **الموظف:** {employee['full_name']}\n"
            f"🕐 **ساعات العمل:** {work_hours} ساعة\n"
            f"📅 **التاريخ:** {now.strftime('%Y-%m-%d')}\n\n"
            f"🌙 **نراك غداً بإذن الله**"
        )
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"خطأ في تسجيل الانصراف: {e}")
        await update.message.reply_text("❌ حدث خطأ في تسجيل الانصراف")

# ==================== إدارة الاستراحات ====================
async def smoke_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب استراحة تدخين"""
    try:
        user = update.message.from_user
        employee = get_employee_by_telegram_id(user.id)
        
        if not employee:
            await update.message.reply_text("❌ يرجى استخدام /start أولاً للتحقق من هويتك.")
            return
        
        now = get_jordan_time()
        
        # التحقق من الحد الأقصى للسجائر
        conn = get_db_connection()
        cur = conn.cursor()
        today = now.date().isoformat()
        
        cur.execute('''
            SELECT COUNT(*) FROM cigarette_times 
            WHERE employee_id = ? AND DATE(taken_at) = ?
        ''', (employee['id'], today))
        
        smoke_count = cur.fetchone()[0]
        max_smokes = get_max_daily_smokes()
        
        if smoke_count >= max_smokes:
            await update.message.reply_text(
                f"❌ **وصلت للحد الأقصى اليومي!**\n\n"
                f"🚬 **السجائر المستخدمة:** {smoke_count}/{max_smokes}\n"
                f"📅 **اليوم:** {'جمعة' if is_friday() else 'عادي'}\n\n"
                f"⏳ يمكنك المحاولة غداً 😊"
            )
            conn.close()
            return
        
        # تسجيل السيجارة
        cur.execute('''
            INSERT INTO cigarette_times (employee_id, taken_at)
            VALUES (?, ?)
        ''', (employee['id'], now.isoformat()))
        
        conn.commit()
        conn.close()
        
        remaining = max_smokes - (smoke_count + 1)
        
        message = (
            f"✅ **تم تسجيل استراحة التدخين!**\n\n"
            f"👤 **الموظف:** {employee['full_name']}\n"
            f"⏰ **الوقت:** {now.strftime('%H:%M:%S')}\n"
            f"🚬 **المتبقي اليوم:** {remaining}/{max_smokes}\n"
            f"⏱ **المدة:** {SMOKE_BREAK_DURATION} دقائق\n\n"
            f"😊 **استمتع بوقتك!**"
        )
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"خطأ في طلب التدخين: {e}")
        await update.message.reply_text("❌ حدث خطأ في طلب الاستراحة")

async def break_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب استراحة غداء"""
    try:
        user = update.message.from_user
        employee = get_employee_by_telegram_id(user.id)
        
        if not employee:
            await update.message.reply_text("❌ يرجى استخدام /start أولاً للتحقق من هويتك.")
            return
        
        # التحقق من يوم الجمعة
        if not can_take_lunch_break():
            await update.message.reply_text(
                "❌ **غير مسموح ببريك الغداء يوم الجمعة!**\n\n"
                "📅 **ملاحظة:** يوم الجمعة هو يوم عمل إضافي\n"
                "🍽 يمكنك تناول الغداء خلال العمل"
            )
            return
        
        now = get_jordan_time()
        today = now.date().isoformat()
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # التحقق من أخذ بريك الغداء مسبقاً
        cur.execute('''
            SELECT taken FROM lunch_breaks 
            WHERE employee_id = ? AND date = ?
        ''', (employee['id'], today))
        
        existing = cur.fetchone()
        
        if existing and existing[0]:
            await update.message.reply_text(
                "❌ **لقد أخذت استراحة غداء اليوم بالفعل!**\n\n"
                "📅 **ملاحظة:** يمكنك الحصول على استراحة غداء واحدة فقط في اليوم\n"
                "⏳ يمكنك المحاولة غداً 😊"
            )
            conn.close()
            return
        
        # تسجيل بريك الغداء
        cur.execute('''
            INSERT OR REPLACE INTO lunch_breaks 
            (employee_id, date, taken, taken_at)
            VALUES (?, ?, 1, ?)
        ''', (employee['id'], today, now.isoformat()))
        
        conn.commit()
        conn.close()
        
        message = (
            f"✅ **تم تسجيل استراحة الغداء!**\n\n"
            f"👤 **الموظف:** {employee['full_name']}\n"
            f"⏰ **الوقت:** {now.strftime('%H:%M:%S')}\n"
            f"⏱ **المدة:** {LUNCH_BREAK_DURATION} دقيقة\n\n"
            f"🍽 **استمتع بغدائك!** 😊"
        )
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"خطأ في طلب الغداء: {e}")
        await update.message.reply_text("❌ حدث خطأ في طلب الاستراحة")

# ==================== التقارير ====================
async def attendance_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تقرير حضور الموظف"""
    try:
        user = update.message.from_user
        employee = get_employee_by_telegram_id(user.id)
        
        if not employee:
            await update.message.reply_text("❌ يرجى استخدام /start أولاً للتحقق من هويتك.")
            return
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # جلب بيانات آخر 7 أيام
        cur.execute('''
            SELECT date, check_in_time, check_out_time, is_late, total_work_hours
            FROM attendance 
            WHERE employee_id = ? 
            ORDER BY date DESC 
            LIMIT 7
        ''', (employee['id'],))
        
        records = cur.fetchall()
        conn.close()
        
        if not records:
            await update.message.reply_text(
                f"📊 **تقرير الحضور - {employee['full_name']}**\n\n"
                "⚠️ لا توجد سجلات حضور للأيام الماضية"
            )
            return
        
        message = (
            f"📊 **تقرير الحضور**\n"
            f"👤 **الموظف:** {employee['full_name']}\n"
            f"📅 **آخر 7 أيام**\n\n"
        )
        
        total_hours = 0
        present_days = 0
        
        for record in records:
            date_str, check_in, check_out, is_late, hours = record
            
            message += f"📅 **{date_str}**\n"
            
            if check_in:
                check_in_time = datetime.fromisoformat(check_in).strftime('%H:%M')
                message += f"   🕐 حضور: {check_in_time}"
                if is_late:
                    message += " ⚠️\n"
                else:
                    message += " ✅\n"
                
                if check_out:
                    check_out_time = datetime.fromisoformat(check_out).strftime('%H:%M')
                    message += f"   🕐 انصراف: {check_out_time}\n"
                    message += f"   ⏱ ساعات: {hours}\n"
                    total_hours += hours if hours else 0
                    present_days += 1
                else:
                    message += "   ⏳ لم ينصرف بعد\n"
            else:
                message += "   ❌ لم يحضر\n"
            
            message += "\n"
        
        message += (
            f"📈 **الإحصائيات:**\n"
            f"📅 أيام الحضور: {present_days}\n"
            f"⏱ إجمالي الساعات: {total_hours:.1f}\n"
        )
        
        if present_days > 0:
            avg_hours = total_hours / present_days
            message += f"📊 متوسط اليوم: {avg_hours:.1f} ساعة\n"
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"خطأ في التقرير: {e}")
        await update.message.reply_text("❌ حدث خطأ في عرض التقرير")

# ==================== المديرين ====================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة تحكم المدير"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر للمديرين فقط.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📊 التقرير اليومي", callback_data="admin_daily")],
        [InlineKeyboardButton("📈 التقرير الأسبوعي", callback_data="admin_weekly")],
        [InlineKeyboardButton("👥 قائمة الموظفين", callback_data="admin_employees")],
    ]
    
    if is_super_admin(user.id):
        keyboard.append([InlineKeyboardButton("➕ إضافة مدير", callback_data="admin_add_manager")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    admin_type = "👑 مدير رئيسي" if is_super_admin(user.id) else "👨‍💼 مدير"
    
    await update.message.reply_text(
        f"👨‍💼 **لوحة تحكم المدير**\n\n"
        f"🎯 **صلاحياتك:** {admin_type}\n\n"
        f"اختر الإجراء المطلوب:",
        reply_markup=reply_markup
    )

async def daily_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التقرير اليومي للمدير"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر للمديرين فقط.")
        return
    
    try:
        today = get_jordan_time().date().isoformat()
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute('''
            SELECT e.full_name, a.check_in_time, a.check_out_time, a.is_late, a.total_work_hours
            FROM employees e
            LEFT JOIN attendance a ON e.id = a.employee_id AND a.date = ?
            WHERE e.is_active = 1
            ORDER BY e.full_name
        ''', (today,))
        
        records = cur.fetchall()
        conn.close()
        
        message = (
            f"📊 **التقرير اليومي**\n"
            f"📅 **التاريخ:** {today}\n\n"
        )
        
        present_count = 0
        absent_count = 0
        late_count = 0
        
        for record in records:
            name, check_in, check_out, is_late, hours = record
            
            message += f"👤 **{name}**\n"
            
            if check_in:
                present_count += 1
                check_in_time = datetime.fromisoformat(check_in).strftime('%H:%M')
                message += f"   🕐 {check_in_time}"
                if is_late:
                    late_count += 1
                    message += " ⚠️\n"
                else:
                    message += " ✅\n"
                
                if check_out:
                    check_out_time = datetime.fromisoformat(check_out).strftime('%H:%M')
                    message += f"   🕐 {check_out_time}\n"
                    if hours:
                        message += f"   ⏱ {hours} ساعة\n"
                else:
                    message += "   ⏳ لم ينصرف\n"
            else:
                absent_count += 1
                message += "   ❌ غائب\n"
            
            message += "\n"
        
        total_employees = len(records)
        message += (
            f"📈 **ملخص اليوم:**\n"
            f"👥 الإجمالي: {total_employees}\n"
            f"✅ حاضر: {present_count}\n"
            f"❌ غائب: {absent_count}\n"
        )
        
        if late_count > 0:
            message += f"⚠️ متأخر: {late_count}\n"
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"خطأ في التقرير اليومي: {e}")
        await update.message.reply_text("❌ حدث خطأ في عرض التقرير")

# ==================== التذكيرات التلقائية ====================
async def send_reminder(context: ContextTypes.DEFAULT_TYPE, reminder_type):
    """إرسال تذكير للموظفين"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT telegram_id FROM employees WHERE is_active = 1")
        employees = cur.fetchall()
        conn.close()
        
        if reminder_type == "check_in":
            message = (
                "⏰ **تذكير: وقت الحضور!**\n\n"
                "🕗 يرجى تسجيل الحضور باستخدام:\n"
                "📥 /check_in"
            )
        else:
            message = (
                "⏰ **تذكير: وقت الانصراف!**\n\n"
                "🕔 يرجى تسجيل الانصراف باستخدام:\n"
                "📤 /check_out"
            )
        
        for employee in employees:
            try:
                await context.bot.send_message(
                    chat_id=employee[0],
                    text=message
                )
            except Exception as e:
                logger.debug(f"فشل إرسال تذكير لـ {employee[0]}: {e}")
        
        logger.info(f"تم إرسال تذكير {reminder_type}")
        
    except Exception as e:
        logger.error(f"خطأ في إرسال التذكير: {e}")

async def reminder_check_in(context: ContextTypes.DEFAULT_TYPE):
    """تذكير الحضور"""
    await send_reminder(context, "check_in")

async def reminder_check_out(context: ContextTypes.DEFAULT_TYPE):
    """تذكير الانصراف"""
    await send_reminder(context, "check_out")

# ==================== الدالة الرئيسية ====================
def main():
    """الدالة الرئيسية لتشغيل البوت"""
    if not BOT_TOKEN:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN not found!")
        return
    
    print("🚀 بدء تشغيل بوت إدارة الموظفين...")
    
    # تهيئة قاعدة البيانات
    init_database()
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # إضافة handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("my_id", my_id_command))
        application.add_handler(CommandHandler("check_in", check_in_command))
        application.add_handler(CommandHandler("check_out", check_out_command))
        application.add_handler(CommandHandler("smoke", smoke_request))
        application.add_handler(CommandHandler("break", break_request))
        application.add_handler(CommandHandler("attendance_report", attendance_report_command))
        application.add_handler(CommandHandler("admin", admin_command))
        application.add_handler(CommandHandler("daily_report", daily_report_command))
        application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
        
        # إعداد التذكيرات اليومية
        job_queue = application.job_queue
        if job_queue:
            # تذكير الحضور 7:45 ص
            job_queue.run_daily(
                reminder_check_in,
                time=datetime.strptime("07:45", "%H:%M").time(),
                days=(0, 1, 2, 3, 4, 5, 6)
            )
            
            # تذكير الانصراف 4:45 م
            job_queue.run_daily(
                reminder_check_out,
                time=datetime.strptime("16:45", "%H:%M").time(),
                days=(0, 1, 2, 3, 4, 5, 6)
            )
            
            print("✅ تم جدولة التذكيرات اليومية")
        
        print("🤖 البوت يعمل الآن! اضغط Ctrl+C لإيقافه")
        
        # تشغيل البوت
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
    except Exception as e:
        logger.error(f"فشل في تشغيل البوت: {e}")
        print(f"❌ فشل في تشغيل البوت: {e}")

if __name__ == '__main__':
    main()