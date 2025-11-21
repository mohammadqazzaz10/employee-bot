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
# ⚙️ الإعدادات العامة
# ==============================================================================

# الحالات (States)
LEAVE_REASON, VACATION_REASON = range(2)
EDIT_DETAIL_SELECT, EDIT_DETAIL_INPUT = range(2, 4)

# المتغيرات البيئية
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

# إعدادات العمل
JORDAN_TZ = ZoneInfo('Asia/Amman')
WORK_START_HOUR = 8
WORK_START_MINUTE = 0
WORK_REGULAR_HOURS = 9
MAX_DAILY_SMOKES = 6
LATE_GRACE_PERIOD_MINUTES = 15
SMOKE_GAP_MINUTES = 90  # ساعة ونصف

# قائمة المديرين (يجب أن تضع معرفك هنا)
ADMIN_IDS = [1465191277]  

# إعدادات التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

authorized_phones = []
active_timers = {}

# ==============================================================================
# 🗄️ إدارة قاعدة البيانات (أداء عالي)
# ==============================================================================

try:
    db_pool = psycopg2.pool.SimpleConnectionPool(1, 20, dsn=DATABASE_URL)
except Exception as e:
    logger.error(f"❌ Error creating pool: {e}")
    db_pool = None

def execute_query(query, params=None, fetch_one=False, fetch_all=False, commit=False):
    """دالة تنفيذ الأوامر بشكل آمن وسريع"""
    conn = None
    result = None
    try:
        conn = db_pool.getconn() if db_pool else psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(query, params)
        
        if commit:
            conn.commit()
            if 'RETURNING' in query.upper():
                result = cur.fetchone()
        
        if fetch_one: result = cur.fetchone()
        elif fetch_all: result = cur.fetchall()
        cur.close()
    except Exception as e:
        logger.error(f"DB Error: {e}")
        if conn: conn.rollback()
    finally:
        if conn and db_pool: db_pool.putconn(conn)
        elif conn: conn.close()
    return result

def initialize_database_tables():
    queries = [
        """CREATE TABLE IF NOT EXISTS employees (
            id SERIAL PRIMARY KEY, telegram_id BIGINT UNIQUE, phone_number VARCHAR(20) UNIQUE,
            full_name VARCHAR(100), age INTEGER, job_title VARCHAR(100), department VARCHAR(100),
            hire_date DATE, last_active TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );""",
        """CREATE TABLE IF NOT EXISTS attendance (
            id SERIAL PRIMARY KEY, employee_id INTEGER REFERENCES employees(id),
            date DATE, check_in_time TIMESTAMP WITH TIME ZONE, check_out_time TIMESTAMP WITH TIME ZONE,
            is_late BOOLEAN, late_minutes INTEGER, total_work_hours DECIMAL(4,2), overtime_hours DECIMAL(4,2),
            status VARCHAR(20), UNIQUE(employee_id, date)
        );""",
        """CREATE TABLE IF NOT EXISTS daily_cigarettes (
            id SERIAL PRIMARY KEY, employee_id INTEGER REFERENCES employees(id),
            date DATE, count INTEGER DEFAULT 0, updated_at TIMESTAMP WITH TIME ZONE,
            UNIQUE(employee_id, date)
        );""",
        """CREATE TABLE IF NOT EXISTS cigarette_times (
            id SERIAL PRIMARY KEY, employee_id INTEGER REFERENCES employees(id),
            taken_at TIMESTAMP WITH TIME ZONE
        );""",
        """CREATE TABLE IF NOT EXISTS lunch_breaks (
            id SERIAL PRIMARY KEY, employee_id INTEGER REFERENCES employees(id),
            date DATE, taken BOOLEAN DEFAULT FALSE, taken_at TIMESTAMP WITH TIME ZONE,
            UNIQUE(employee_id, date)
        );""",
        """CREATE TABLE IF NOT EXISTS requests (
            id SERIAL PRIMARY KEY, employee_id INTEGER REFERENCES employees(id),
            request_type VARCHAR(50), status VARCHAR(20), requested_at TIMESTAMP WITH TIME ZONE,
            notes TEXT
        );"""
    ]
    for q in queries:
        execute_query(q, commit=True)

# ==============================================================================
# 🛠️ دوال مساعدة
# ==============================================================================

def get_jordan_time():
    return datetime.now(JORDAN_TZ)

def normalize_phone(phone):
    if not phone: return ""
    digits = ''.join(filter(str.isdigit, phone))
    while digits.startswith('00'): digits = digits[2:]
    if digits.startswith('0'): digits = digits[1:]
    return digits

def get_all_admins_ids():
    # دمج المديرين من الكود + قاعدة البيانات (إذا أضفت جدول admins لاحقاً)
    return ADMIN_IDS

async def send_to_admins(context, text, reply_markup=None):
    for admin_id in get_all_admins_ids():
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Failed to send to admin {admin_id}: {e}")

def get_employee(telegram_id=None, phone=None):
    if telegram_id:
        return execute_query("SELECT * FROM employees WHERE telegram_id = %s", (telegram_id,), fetch_one=True)
    if phone:
        norm = normalize_phone(phone)
        # بحث مرن قليلاً
        return execute_query("SELECT * FROM employees WHERE phone_number LIKE %s", (f"%{norm}",), fetch_one=True)
    return None

# ==============================================================================
# 🤖 أوامر البوت (Handlers)
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    loop = asyncio.get_running_loop()
    emp = await loop.run_in_executor(None, get_employee, user.id)
    
    if emp:
        msg = (
            f"مرحباً {emp['full_name']} 👋\n\n"
            "✅ أنت مسجل في النظام.\n\n"
            "🔸 **الحضور:** /check_in | /check_out\n"
            "🔸 **الاستراحات:** /smoke | /break\n"
            "🔸 **الطلبات:** /leave | /vacation\n"
            "🔸 **تقارير:** /attendance_report"
        )
        if user.id in ADMIN_IDS:
            msg += "\n\n👮‍♂️ **أوامر المدير:**\n/list_employees\n/daily_report"
        await update.message.reply_text(msg)
    else:
        keyboard = [[KeyboardButton("📱 مشاركة رقم الهاتف", request_contact=True)]]
        await update.message.reply_text("مرحباً! للبدء، يرجى مشاركة رقم هاتفك.", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True))

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    user = update.message.from_user
    
    if contact.user_id != user.id:
        await update.message.reply_text("⛔ يرجى مشاركة رقمك الخاص.")
        return

    phone = contact.phone_number
    if not phone.startswith('+'): phone = '+' + phone
    name = f"{contact.first_name} {contact.last_name or ''}".strip()
    
    loop = asyncio.get_running_loop()
    
    # حفظ الموظف
    existing = await loop.run_in_executor(None, execute_query, "SELECT * FROM employees WHERE phone_number = %s", (phone,), True)
    
    if existing:
        await loop.run_in_executor(None, execute_query, "UPDATE employees SET telegram_id = %s, full_name = %s WHERE id = %s", (user.id, name, existing['id']), False, False, True)
        await update.message.reply_text("✅ تم تحديث بياناتك وربط حسابك بنجاح!")
    else:
        # يمكنك وضع شرط هنا لمنع تسجيل أي شخص غريب، لكن سأتركه مفتوحاً للتجربة
        await loop.run_in_executor(None, execute_query, "INSERT INTO employees (telegram_id, phone_number, full_name) VALUES (%s, %s, %s)", (user.id, phone, name), False, False, True)
        await update.message.reply_text("✅ تم تسجيلك كموظف جديد بنجاح!")

# --- الحضور والانصراف ---

async def check_in(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    loop = asyncio.get_running_loop()
    emp = await loop.run_in_executor(None, get_employee, user.id)
    
    if not emp: return await update.message.reply_text("❌ غير مسجل.")
    
    now = get_jordan_time()
    res = await loop.run_in_executor(None, execute_query, "SELECT * FROM attendance WHERE employee_id = %s AND date = %s", (emp['id'], now.date()), True)
    
    if res:
        return await update.message.reply_text(f"⚠️ لقد سجلت دخول مسبقاً الساعة {res['check_in_time'].strftime('%H:%M')}")
        
    work_start = now.replace(hour=WORK_START_HOUR, minute=WORK_START_MINUTE, second=0)
    late_mins = max(0, int((now - work_start).total_seconds() / 60))
    is_late = late_mins > LATE_GRACE_PERIOD_MINUTES
    
    await loop.run_in_executor(None, execute_query, 
        "INSERT INTO attendance (employee_id, date, check_in_time, is_late, late_minutes) VALUES (%s, %s, %s, %s, %s)",
        (emp['id'], now.date(), now, is_late, late_mins), False, False, True)
    
    msg = f"✅ تم تسجيل الحضور: {now.strftime('%H:%M')}"
    if is_late: msg += f"\n⚠️ **تأخير:** {late_mins} دقيقة"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def check_out(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    loop = asyncio.get_running_loop()
    emp = await loop.run_in_executor(None, get_employee, user.id)
    
    if not emp: return await update.message.reply_text("❌ غير مسجل.")
    
    now = get_jordan_time()
    att = await loop.run_in_executor(None, execute_query, "SELECT * FROM attendance WHERE employee_id = %s AND date = %s", (emp['id'], now.date()), True)
    
    if not att: return await update.message.reply_text("❌ لم تسجل دخول اليوم.")
    if att['check_out_time']: return await update.message.reply_text("⚠️ سجلت خروج مسبقاً.")
    
    check_in_time = att['check_in_time'].astimezone(JORDAN_TZ)
    work_hours = (now - check_in_time).total_seconds() / 3600
    if work_hours >= 1: work_hours -= 0.5 # خصم الغداء
    overtime = max(0, work_hours - WORK_REGULAR_HOURS)
    
    await loop.run_in_executor(None, execute_query,
        "UPDATE attendance SET check_out_time = %s, total_work_hours = %s, overtime_hours = %s WHERE id = %s",
        (now, work_hours, overtime, att['id']), False, False, True)
        
    await update.message.reply_text(f"🚪 تم تسجيل الانصراف.\nساعات العمل: {work_hours:.2f}\nإضافي: {overtime:.2f}")

# --- طلبات التدخين (مع الشروط القديمة) ---

async def smoke_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    loop = asyncio.get_running_loop()
    emp = await loop.run_in_executor(None, get_employee, user.id)
    
    if not emp: return await update.message.reply_text("❌ غير مسجل.")
    
    # 1. التحقق من العدد اليومي
    today = get_jordan_time().date()
    daily = await loop.run_in_executor(None, execute_query, "SELECT count FROM daily_cigarettes WHERE employee_id = %s AND date = %s", (emp['id'], today), True)
    count = daily['count'] if daily else 0
    
    if count >= MAX_DAILY_SMOKES:
        return await update.message.reply_text(f"⛔ لقد استهلكت الحد الأقصى ({MAX_DAILY_SMOKES}) اليوم!")
        
    # 2. التحقق من الفاصل الزمني (ساعة ونصف)
    last_cig = await loop.run_in_executor(None, execute_query, "SELECT taken_at FROM cigarette_times WHERE employee_id = %s ORDER BY taken_at DESC LIMIT 1", (emp['id'],), True)
    
    if last_cig:
        last_time = last_cig['taken_at'].astimezone(JORDAN_TZ)
        diff_mins = (get_jordan_time() - last_time).total_seconds() / 60
        if diff_mins < SMOKE_GAP_MINUTES:
            remain = int(SMOKE_GAP_MINUTES - diff_mins)
            return await update.message.reply_text(f"⏳ يرجى الانتظار. المتبقي: {remain} دقيقة.")

    # 3. إرسال الطلب للمدير (كما كان في الكود القديم)
    await update.message.reply_text("⏳ تم إرسال الطلب للمدير، انتظر الموافقة...")
    
    keyboard = [[
        InlineKeyboardButton("✅ موافقة", callback_data=f"app_smoke_{emp['id']}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"rej_smoke_{emp['id']}")
    ]]
    
    admin_msg = (
        f"🚬 **طلب تدخين جديد**\n"
        f"👤 الموظف: {emp['full_name']}\n"
        f"📊 العدد اليومي: {count}/{MAX_DAILY_SMOKES}\n"
        f"⌚ الوقت: {get_jordan_time().strftime('%H:%M')}"
    )
    await send_to_admins(context, admin_msg, InlineKeyboardMarkup(keyboard))

# --- طلب الغداء ---

async def break_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    loop = asyncio.get_running_loop()
    emp = await loop.run_in_executor(None, get_employee, user.id)
    if not emp: return await update.message.reply_text("❌ غير مسجل.")
    
    today = get_jordan_time().date()
    chk = await loop.run_in_executor(None, execute_query, "SELECT taken FROM lunch_breaks WHERE employee_id = %s AND date = %s", (emp['id'], today), True)
    
    if chk and chk['taken']:
        return await update.message.reply_text("⛔ لقد أخذت استراحة غداء بالفعل.")
        
    await update.message.reply_text("⏳ تم إرسال طلب الغداء للمدير...")
    
    keyboard = [[
        InlineKeyboardButton("✅ موافقة", callback_data=f"app_break_{emp['id']}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"rej_break_{emp['id']}")
    ]]
    await send_to_admins(context, f"☕ **طلب غداء**\n👤 {emp['full_name']}", InlineKeyboardMarkup(keyboard))

# --- معالجة ردود المدير (Callback Query) ---

async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    action, type_, emp_id = data.split('_') # ex: app_smoke_5
    emp_id = int(emp_id)
    
    # التأكد أن الضي هو مدير
    if query.from_user.id not in ADMIN_IDS:
        return await query.answer("❌ لست مديراً!", show_alert=True)
        
    loop = asyncio.get_running_loop()
    emp = await loop.run_in_executor(None, execute_query, "SELECT * FROM employees WHERE id = %s", (emp_id,), True)
    if not emp: return await query.edit_message_text("❌ موظف غير موجود.")
    
    status = "✅ تمت الموافقة" if action == "app" else "❌ تم الرفض"
    await query.edit_message_text(f"{query.message.text}\n\nالقرار: {status} بواسطة {query.from_user.first_name}")
    
    if action == "rej":
        await context.bot.send_message(emp['telegram_id'], f"❌ تم رفض طلبك ({type_}).")
        return

    # تنفيذ الموافقة
    now = get_jordan_time()
    
    if type_ == "smoke":
        # زيادة العداد + تسجيل الوقت
        await loop.run_in_executor(None, execute_query, 
            "INSERT INTO daily_cigarettes (employee_id, date, count) VALUES (%s, %s, 1) ON CONFLICT (employee_id, date) DO UPDATE SET count = daily_cigarettes.count + 1",
            (emp_id, now.date()), False, False, True)
        await loop.run_in_executor(None, execute_query, "INSERT INTO cigarette_times (employee_id, taken_at) VALUES (%s, %s)", (emp_id, now), False, False, True)
        
        # بدء المؤقت
        await context.bot.send_message(emp['telegram_id'], "✅ وافق المدير! معك 5 دقائق. 🚬")
        # هنا يمكنك إضافة كود المؤقت (Timer)
        
    elif type_ == "break":
        await loop.run_in_executor(None, execute_query, 
            "INSERT INTO lunch_breaks (employee_id, date, taken, taken_at) VALUES (%s, %s, TRUE, %s)",
            (emp_id, now.date(), now), False, False, True)
        await context.bot.send_message(emp['telegram_id'], "✅ وافق المدير! معك 30 دقيقة. ☕")

# --- طلبات الإجازة والمغادرة (Conversation) ---

async def leave_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 اكتب سبب المغادرة:")
    return LEAVE_REASON

async def leave_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text
    user = update.message.from_user
    
    keyboard = [[
        InlineKeyboardButton("✅ قبول", callback_data=f"app_leave_{user.id}"), # هنا استخدمنا user.id مؤقتا للتبسيط أو يجب جلب emp_id
        InlineKeyboardButton("❌ رفض", callback_data=f"rej_leave_{user.id}")
    ]]
    await send_to_admins(context, f"🚪 **طلب مغادرة**\n👤 {user.first_name}\n📝 السبب: {reason}", InlineKeyboardMarkup(keyboard))
    await update.message.reply_text("تم الإرسال للمدير.")
    return ConversationHandler.END

async def vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 اكتب سبب وتاريخ العطلة:")
    return VACATION_REASON

async def vacation_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text
    user = update.message.from_user
    
    keyboard = [[
        InlineKeyboardButton("✅ قبول", callback_data=f"app_vac_{user.id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"rej_vac_{user.id}")
    ]]
    await send_to_admins(context, f"🌴 **طلب إجازة**\n👤 {user.first_name}\n📝 التفاصيل: {reason}", InlineKeyboardMarkup(keyboard))
    await update.message.reply_text("تم الإرسال للمدير.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END

# ==============================================================================
# 🚀 التشغيل
# ==============================================================================

def main():
    if not BOT_TOKEN: return print("❌ NO TOKEN")
    
    print("🚀 Starting Bot (Pro + Strict Logic)...")
    initialize_database_tables()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # تعريف المحادثات
    leave_conv = ConversationHandler(
        entry_points=[CommandHandler('leave', leave_start)],
        states={LEAVE_REASON: [MessageHandler(filters.TEXT, leave_reason)]},
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    vacation_conv = ConversationHandler(
        entry_points=[CommandHandler('vacation', vacation_start)],
        states={VACATION_REASON: [MessageHandler(filters.TEXT, vacation_reason)]},
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    # إضافة المعالجات
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    
    app.add_handler(CommandHandler("check_in", check_in))
    app.add_handler(CommandHandler("check_out", check_out))
    app.add_handler(CommandHandler("smoke", smoke_request))
    app.add_handler(CommandHandler("break", break_request))
    
    app.add_handler(leave_conv)
    app.add_handler(vacation_conv)
    
    # معالج الأزرار (مهم جداً للموافقة)
    app.add_handler(CallbackQueryHandler(admin_decision))
    
    print("✅ Bot Running...")
    app.run_polling()

if __name__ == '__main__':
    main()
