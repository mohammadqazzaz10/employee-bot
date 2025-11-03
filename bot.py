# --- استيراد المكتبات اللازمة ---
import os
import logging
import psycopg2
import datetime
from dotenv import load_dotenv
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton, ParseMode
)
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, 
    CallbackContext, ConversationHandler, CallbackQueryHandler
)

# --- إعدادات أساسية ---

# تحميل متغيرات البيئة (التوكن ورابط الداتا بيس) من ملف .env (للتشغيل المحلي)
load_dotenv()

# تفعيل تسجيل الأخطاء (Logging)
# تم تعديل اسم الـ Logger ليكون `__main__` لتجنب التباسات التسمية
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# جلب التوكن ورابط قاعدة البيانات
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

# --- تعريف حالات المحادثة (لأوامر إضافة موظف أو طلب إجازة) ---
(ASK_PHONE, ASK_NAME, ASK_AGE, ASK_POSITION, ASK_DEPT, ASK_HIRE_DATE) = range(6)
(ASK_LEAVE_REASON, ASK_VACATION_REASON_DAYS) = range(6, 8)
(EDIT_EMPLOYEE_ID, EDIT_FIELD, EDIT_VALUE) = range(8, 11)

# --- 1. إدارة قاعدة البيانات (PostgreSQL) ---

def get_db_connection():
    """الاتصال بقاعدة البيانات PostgreSQL."""
    try:
        # هنا قد تحتاج لإضافة sslmode='require' إذا كانت قاعدة البيانات على Render
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except psycopg2.DatabaseError as e:
        logger.error(f"خطأ في الاتصال بقاعدة البيانات: {e}")
        return None

def setup_database():
    """إنشاء الجداول في قاعدة البيانات إذا لم تكن موجودة."""
    commands = (
        """
        CREATE TABLE IF NOT EXISTS employees (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE,
            phone_number VARCHAR(20) UNIQUE NOT NULL,
            full_name VARCHAR(100),
            age INTEGER,
            position VARCHAR(100),
            department VARCHAR(100),
            hire_date DATE,
            is_admin BOOLEAN DEFAULT FALSE,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS attendance (
            id SERIAL PRIMARY KEY,
            employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
            check_in_time TIMESTAMP,
            check_out_time TIMESTAMP,
            work_date DATE NOT NULL DEFAULT CURRENT_DATE
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS breaks (
            id SERIAL PRIMARY KEY,
            employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
            break_type VARCHAR(20) NOT NULL, -- 'smoke' أو 'break'
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            work_date DATE NOT NULL DEFAULT CURRENT_DATE
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS leaves (
            id SERIAL PRIMARY KEY,
            employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
            leave_type VARCHAR(20) NOT NULL, -- 'leave' أو 'vacation'
            reason TEXT,
            start_date TIMESTAMP NOT NULL,
            end_date TIMESTAMP,
            status VARCHAR(20) DEFAULT 'pending' -- 'pending', 'approved', 'rejected'
        );
        """
    )
    
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                for command in commands:
                    cur.execute(command)
                conn.commit()
            logger.info("تم إعداد جداول قاعدة البيانات بنجاح.")
        except Exception as e:
            logger.error(f"فشل إعداد قاعدة البيانات: {e}")
        finally:
            conn.close()
    else:
        logger.error("لم يتمكن من الاتصال بقاعدة البيانات لإنشاء الجداول.")


# --- 2. دوال مساعدة (Helper Functions) ---

def get_employee(telegram_id):
    """جلب بيانات موظف باستخدام Telegram ID."""
    conn = get_db_connection()
    if not conn: return None
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM employees WHERE telegram_id = %s", (telegram_id,))
        employee = cur.fetchone()
        
        # استرجاع أسماء الأعمدة لإنشاء قاموس
        columns = [desc[0] for desc in cur.description] if cur.description else []

    conn.close()
    if employee and columns:
        return dict(zip(columns, employee))
    return None

def is_admin(telegram_id):
    """التحقق إذا كان المستخدم مديراً."""
    employee = get_employee(telegram_id)
    return employee and employee.get('is_admin', False)

def get_admin_ids():
    """جلب قائمة بـ Telegram IDs لجميع المديرين."""
    conn = get_db_connection()
    ids = []
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM employees WHERE is_admin = TRUE AND telegram_id IS NOT NULL")
            rows = cur.fetchall()
            ids = [row[0] for row in rows]
        conn.close()
    return ids

def notify_admins(context: CallbackContext, message: str):
    """إرسال إشعار لجميع المديرين."""
    admin_ids = get_admin_ids()
    for admin_id in admin_ids:
        # لا ترسل لنفسك إذا كنت مديراً وبدأت الأمر
        # if admin_id == update.effective_user.id: continue 
        try:
            context.bot.send_message(chat_id=admin_id, text=message, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning(f"فشل إرسال إشعار للمدير {admin_id}: {e}")

# --- 3. أوامر الموظفين الأساسية (Check-in, Start, Help) ---

def start_command(update: Update, context: CallbackContext):
    """معالجة الأمر /start والتحقق من تسجيل الموظف."""
    user = update.effective_user
    employee = get_employee(user.id)
    
    if employee:
        update.message.reply_text(f"أهلاً بعودتك، {employee['full_name']}! 👋\nاستخدم /help لعرض الأوامر.")
    else:
        # إذا لم يكن مسجلاً، اطلب رقم الهاتف
        keyboard = [[KeyboardButton("📱 مشاركة رقم هاتفي", request_contact=True)]]
        update.message.reply_text(
            "مرحباً بك في بوت إدارة الحضور.\n"
            "للبدء، يرجى مشاركة رقم هاتفك للتحقق من هويتك.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        )

def handle_contact(update: Update, context: CallbackContext):
    """معالجة رقم الهاتف المُرسل."""
    contact = update.message.contact
    phone_number = contact.phone_number
    # توحيد صيغة الرقم (إزالة + أو 00)
    phone_number_cleaned = phone_number.lstrip('00').lstrip('+')
    
    conn = get_db_connection()
    if not conn:
        update.message.reply_text("خطأ في الاتصال بالخادم، يرجى المحاولة لاحقاً.")
        return

    with conn.cursor() as cur:
        # البحث عن الرقم في جدول الموظفين
        cur.execute("SELECT full_name FROM employees WHERE phone_number LIKE %s", (phone_number_cleaned + '%',))
        employee_name_row = cur.fetchone()
        
        if employee_name_row:
            employee_name = employee_name_row[0]
            # تم العثور على الموظف، قم بتحديث telegram_id الخاص به
            cur.execute(
                "UPDATE employees SET telegram_id = %s WHERE phone_number LIKE %s",
                (update.effective_user.id, phone_number_cleaned + '%')
            )
            conn.commit()
            update.message.reply_text(
                f"✅ تم التحقق بنجاح!\nأهلاً بك {employee_name}. يمكنك الآن استخدام أوامر البوت.",
                reply_markup=ReplyKeyboardMarkup([['/check_in', '/check_out'], ['/break', '/smoke']], resize_keyboard=True)
            )
        else:
            update.message.reply_text("عذراً، رقم هاتفك غير مسجل في النظام. يرجى مراجعة المدير لإضافتك.")
    conn.close()

def help_command(update: Update, context: CallbackContext):
    """عرض قائمة الأوامر المتاحة."""
    user_id = update.effective_user.id
    msg = "👤 **أوامر الموظفين:**\n"
    msg += "`/check_in` - تسجيل الحضور\n"
    msg += "`/check_out` - تسجيل الانصراف\n"
    msg += "`/break` - طلب استراحة غداء (30 دقيقة)\n"
    msg += "`/smoke` - طلب استراحة تدخين (5 دقائق)\n"
    msg += "`/leave` - طلب مغادرة مبكرة\n"
    msg += "`/vacation` - طلب إجازة\n"
    msg += "`/help` - عرض هذه القائمة\n"
    
    if is_admin(user_id):
        msg += "\n👑 **أوامر المديرين:**\n"
        msg += "`/add_employee` - إضافة موظف جديد\n"
        msg += "`/remove_employee` - حذف موظف\n"
        msg += "`/edit_details` - تعديل بيانات موظف\n"
        msg += "`/list_employees` - عرض جميع الموظفين\n"
        msg += "`/daily_report` - تقرير الحضور اليومي\n"
        msg += "`/weekly_report` - تقرير الحضور الأسبوعي\n"
        msg += "`/add_admin` - ترقية موظف لمدير\n"
        msg += "`/remove_admin` - إزالة صلاحيات مدير\n"
        msg += "`/list_admins` - عرض قائمة المديرين\n"

    update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# --- 4. أوامر الحضور والانصراف (Check-in / Check-out) ---

def check_in_command(update: Update, context: CallbackContext):
    """تسجيل الحضور."""
    employee = get_employee(update.effective_user.id)
    if not employee:
        update.message.reply_text("يرجى التسجيل أولاً باستخدام /start.")
        return

    conn = get_db_connection()
    if not conn:
        update.message.reply_text("خطأ في الخادم.")
        return

    with conn.cursor() as cur:
        # التحقق من عدم تسجيل الحضور مسبقاً في نفس اليوم
        cur.execute(
            "SELECT * FROM attendance WHERE employee_id = %s AND work_date = CURRENT_DATE AND check_in_time IS NOT NULL",
            (employee['id'],)
        )
        if cur.fetchone():
            update.message.reply_text("لقد قمت بتسجيل الحضور مسبقاً هذا اليوم.")
        else:
            cur.execute(
                "INSERT INTO attendance (employee_id, check_in_time, work_date) VALUES (%s, %s, CURRENT_DATE)",
                (employee['id'], datetime.datetime.now())
            )
            conn.commit()
            update.message.reply_text("✅ تم تسجيل حضورك بنجاح. نتمنى لك يوماً مثمراً!")
            # إشعار المديرين
            notify_admins(context, f"🔔 **[حضور]**\nالموظف: {employee['full_name']}\nالوقت: {datetime.datetime.now().strftime('%H:%M')}")
    conn.close()

def check_out_command(update: Update, context: CallbackContext):
    """تسجيل الانصراف."""
    employee = get_employee(update.effective_user.id)
    if not employee:
        update.message.reply_text("يرجى التسجيل أولاً باستخدام /start.")
        return

    conn = get_db_connection()
    if not conn:
        update.message.reply_text("خطأ في الخادم.")
        return

    with conn.cursor() as cur:
        # البحث عن آخر تسجيل حضور لم يتم تسجيل انصرافه
        cur.execute(
            "SELECT id FROM attendance WHERE employee_id = %s AND work_date = CURRENT_DATE AND check_in_time IS NOT NULL AND check_out_time IS NULL ORDER BY check_in_time DESC LIMIT 1",
            (employee['id'],)
        )
        attendance_record = cur.fetchone()
        
        if not attendance_record:
            update.message.reply_text("لم تقم بتسجيل الحضور اليوم، أو قمت بالانصراف مسبقاً.")
        else:
            cur.execute(
                "UPDATE attendance SET check_out_time = %s WHERE id = %s",
                (datetime.datetime.now(), attendance_record[0])
            )
            conn.commit()
            update.message.reply_text("✅ تم تسجيل انصرافك. شكراً لجهودك اليوم!")
            # إشعار المديرين
            notify_admins(context, f"🔔 **[انصراف]**\nالموظف: {employee['full_name']}\nالوقت: {datetime.datetime.now().strftime('%H:%M')}")
    conn.close()

# --- 5. أوامر الاستراحات (Breaks) ومنطق العد التنازلي ---

# 5.1 - الدوال الخاصة بالعد التنازلي (JobQueue Callbacks)

def update_countdown_message(context: CallbackContext):
    """
    (دالة الكول باك) - يتم استدعاؤها كل 15 ثانية لتحديث رسالة العد التنازلي.
    """
    job_data = context.job.context
    now = datetime.datetime.now()
    
    # استرجاع البيانات من المهمة (Job)
    start_time = job_data['start_time']
    duration = job_data['duration']
    chat_id = job_data['chat_id']
    message_id = job_data['message_id']
    break_type_emoji = job_data['emoji']
    
    elapsed = (now - start_time).total_seconds()
    remaining = duration - elapsed
    
    if remaining <= 0:
        # إذا انتهى الوقت، أوقف هذا الجوب (سيتم إرسال إشعار الإنهاء من جوب end_break_notification)
        context.job.schedule_removal()
        return

    mins, secs = divmod(int(remaining), 60)
    time_str = f"{mins:02}:{secs:02}"
    
    # تحديد الرمز التعبيري بناءً على الوقت المتبقي
    if remaining < 60:
        emoji_status = "🔴" # أقل من دقيقة
    elif remaining < 180:
        emoji_status = "🟠" # أقل من 3 دقائق
    else:
        emoji_status = "🟢" # أكثر من 3 دقائق
        
    text = f"استراحة {break_type_emoji} جارية...\n"
    text += f"**الوقت المتبقي: {time_str}** {emoji_status}"
    
    try:
        # تحرير الرسالة (هذا هو "الأنيميشن")
        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        # إذا فشل التعديل (مثلاً: حذف المستخدم للرسالة)، أوقف الجوب
        logger.warning(f"فشل تعديل الرسالة للعد التنازلي: {e}")
        context.job.schedule_removal()

def end_break_notification(context: CallbackContext):
    """
    (دالة الكول باك) - يتم استدعاؤها مرة واحدة عند انتهاء وقت الاستراحة.
    """
    job_data = context.job.context
    chat_id = job_data['chat_id']
    message_id = job_data['message_id']
    break_type_name = job_data['name']
    
    # 1. إيقاف أي جوب متبقي لتحديث الرسالة ( احترازي )
    jobs = context.job_queue.get_jobs_by_name(f"countdown_{chat_id}")
    for job in jobs:
        job.schedule_removal()
        
    # 2. تعديل الرسالة الأصلية لتعرض "انتهى الوقت"
    try:
        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"⏰ انتهت استراحة الـ {break_type_name}!"
        )
    except Exception:
        pass # لا مشكلة إذا فشل التعديل

    # 3. إرسال رسالة تذكيرية جديدة مع زر "العودة للعمل"
    keyboard = [[InlineKeyboardButton("✅ رجعت للعمل", callback_data="im_back")]]
    context.bot.send_message(
        chat_id=chat_id,
        text="يرجى العودة إلى العمل وتأكيد عودتك.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    # 4. تحديث قاعدة البيانات (تسجيل نهاية الاستراحة)
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            # تحديث نهاية الاستراحة (break_db_id)
            cur.execute(
                "UPDATE breaks SET end_time = %s WHERE id = %s",
                (datetime.datetime.now(), job_data['break_db_id'])
            )
            conn.commit()
        conn.close()

def im_back_callback(update: Update, context: CallbackContext):
    """معالجة الضغط على زر "رجعت للعمل"."""
    query = update.callback_query
    query.answer("شكراً لك، تم تسجيل عودتك.")
    
    # إخفاء الأزرار وتعديل الرسالة
    query.edit_message_text(text="✅ تم تسجيل العودة للعمل.")
    
    employee = get_employee(update.effective_user.id)
    if employee:
        # إشعار المديرين
        notify_admins(context, f"👍 **[عودة للعمل]**\nالموظف: {employee['full_name']}")


# 5.2 - الأوامر الفعلية للاستراحات (/break, /smoke)

def start_break_timer(update: Update, context: CallbackContext, break_type: str, duration_minutes: int, emoji: str, name: str):
    """دالة مركزية لبدء أي نوع من الاستراحات."""
    employee = get_employee(update.effective_user.id)
    if not employee:
        update.message.reply_text("يرجى التسجيل أولاً باستخدام /start.")
        return

    # --- تطبيق قواعد العمل (Business Logic) ---
    conn = get_db_connection()
    if not conn:
        update.message.reply_text("خطأ في الخادم.")
        return
        
    with conn.cursor() as cur:
        # التحقق من وجود استراحة جارية
        cur.execute(
            "SELECT * FROM breaks WHERE employee_id = %s AND work_date = CURRENT_DATE AND end_time IS NULL",
            (employee['id'],)
        )
        if cur.fetchone():
            update.message.reply_text("لديك استراحة جارية بالفعل! ⚠️")
            conn.close()
            return
            
        # قواعد استراحة الغداء
        if break_type == 'break':
            cur.execute(
                "SELECT COUNT(*) FROM breaks WHERE employee_id = %s AND work_date = CURRENT_DATE AND break_type = 'break'",
                (employee['id'],)
            )
            break_count = cur.fetchone()[0]
            if break_count >= 1:
                update.message.reply_text("لقد حصلت على استراحة الغداء مسبقاً (مرة واحدة يومياً).")
                conn.close()
                return

        # قواعد استراحة التدخين
        if break_type == 'smoke':
            cur.execute(
                "SELECT COUNT(*) FROM breaks WHERE employee_id = %s AND work_date = CURRENT_DATE AND break_type = 'smoke'",
                (employee['id'],)
            )
            smoke_count = cur.fetchone()[0]
            if smoke_count >= 6:
                update.message.reply_text("لقد استنفدت الحد الأقصى لاستراحات التدخين (6 مرات يومياً).")
                conn.close()
                return
                
            # التحقق من الفجوة الزمنية (1.5 ساعة)
            cur.execute(
                "SELECT start_time FROM breaks WHERE employee_id = %s AND break_type = 'smoke' ORDER BY start_time DESC LIMIT 1",
                (employee['id'],)
            )
            last_smoke = cur.fetchone()
            if last_smoke:
                time_since_last = datetime.datetime.now() - last_smoke[0]
                if time_since_last.total_seconds() < (90 * 60): # 90 دقيقة
                    remaining_gap = (90 * 60) - time_since_last.total_seconds()
                    
                    # تحويل المتبقي إلى دقائق وثواني لعرض أفضل
                    mins_left, secs_left = divmod(int(remaining_gap), 60)
                    time_left_str = f"{mins_left} دقيقة و {secs_left} ثانية"
                    
                    update.message.reply_text(f"يجب الانتظار 1.5 ساعة بين كل استراحة تدخين. متبقي: {time_left_str}.")
                    conn.close()
                    return

        # --- الموافقة وبدء العد التنازلي ---
        
        # 1. تسجيل بدء الاستراحة في الداتابيس (والحصول على ID)
        start_time = datetime.datetime.now()
        cur.execute(
            "INSERT INTO breaks (employee_id, break_type, start_time, work_date) VALUES (%s, %s, %s, CURRENT_DATE) RETURNING id",
            (employee['id'], break_type, start_time)
        )
        break_db_id = cur.fetchone()[0]
        conn.commit()
    conn.close()

    duration_seconds = duration_minutes * 60
    
    # 2. إرسال الرسالة الأولية (التي سيتم تعديلها)
    msg = update.message.reply_text(f"تمت الموافقة! استراحة {name} {emoji} لمدة {duration_minutes} دقيقة.\nيبدأ العد التنازلي...")
    
    # 3. تجميع بيانات الجوب
    job_context = {
        'chat_id': update.effective_chat.id,
        'message_id': msg.message_id,
        'start_time': start_time,
        'duration': duration_seconds,
        'emoji': emoji,
        'name': name,
        'break_db_id': break_db_id
    }
    
    # 4. جدولة جوب "الإنهاء" (يتم تشغيله مرة واحدة بعد انتهاء المدة)
    # يجب التأكد من أن job_queue متاح في context (يجب أن يكون متاحاً عبر Updater)
    context.job_queue.run_once(
        end_break_notification,
        duration_seconds,
        context=job_context
    )
    
    # 5. جدولة جوب "التحديث" (يتم تشغيله بشكل متكرر كل 15 ثانية)
    job_name = f"countdown_{update.effective_chat.id}"
    context.job_queue.run_repeating(
        update_countdown_message,
        interval=15, # تحديث كل 15 ثانية 
        first=0, # ابدأ التحديث فوراً
        context=job_context,
        name=job_name
    )

    # إشعار المديرين
    notify_admins(context, f"⏱️ **[استراحة {name}]**\nالموظف: {employee['full_name']}\nالمدة: {duration_minutes} دقيقة.")

# أوامر الاستراحة الفعلية
def break_command(update: Update, context: CallbackContext):
    """طلب استراحة غداء (30 دقيقة)."""
    start_break_timer(update, context, break_type='break', duration_minutes=30, emoji='🍔', name='غداء')

def smoke_command(update: Update, context: CallbackContext):
    """طلب استراحة تدخين (5 دقائق)."""
    start_break_timer(update, context, break_type='smoke', duration_minutes=5, emoji='🚬', name='تدخين')

# --- 6. أوامر الإجازات والمغادرة (ConversationHandler) ---

def leave_command(update: Update, context: CallbackContext):
    """بدء طلب مغادرة مبكرة."""
    employee = get_employee(update.effective_user.id)
    if not employee:
        update.message.reply_text("يرجى التسجيل أولاً باستخدام /start.")
        return ConversationHandler.END
        
    update.message.reply_text("يرجى ذكر سبب المغادرة المبكرة:")
    return ASK_LEAVE_REASON

def vacation_command(update: Update, context: CallbackContext):
    """بدء طلب إجازة."""
    employee = get_employee(update.effective_user.id)
    if not employee:
        update.message.reply_text("يرجى التسجيل أولاً باستخدام /start.")
        return ConversationHandler.END

    update.message.reply_text("يرجى ذكر سبب ومدة الإجازة (مثال: سفر، من 10/12 إلى 15/12):")
    return ASK_VACATION_REASON_DAYS

def handle_leave_reason(update: Update, context: CallbackContext):
    """استلام سبب المغادرة."""
    reason = update.message.text
    employee = get_employee(update.effective_user.id)
    
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            # افتراضياً: المغادرة من الآن وحتى نهاية اليوم
            cur.execute(
                "INSERT INTO leaves (employee_id, leave_type, reason, start_date, end_date) VALUES (%s, 'leave', %s, %s, %s)",
                (employee['id'], reason, datetime.datetime.now(), datetime.datetime.now().replace(hour=23, minute=59, second=59))
            )
            conn.commit()
        conn.close()

    update.message.reply_text("✅ تم إرسال طلبك للمغادرة. سيتم إشعارك بالموافقة.")
    notify_admins(context, f"❓ **[طلب مغادرة]**\nالموظف: {employee['full_name']}\nالسبب: {reason}")
    return ConversationHandler.END

def handle_vacation_reason(update: Update, context: CallbackContext):
    """استلام سبب ومدة الإجازة."""
    reason = update.message.text
    employee = get_employee(update.effective_user.id)
    
    # (هنا يمكن إضافة منطق معقد لتحليل التاريخ من الرسالة)
    # حالياً، نعتبر الرسالة هي السبب والتاريخ
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO leaves (employee_id, leave_type, reason, start_date, end_date) VALUES (%s, 'vacation', %s, %s, %s)",
                (employee['id'], reason, datetime.datetime.now(), datetime.datetime.now() + datetime.timedelta(days=1)) # مثال: يوم واحد
            )
            conn.commit()
        conn.close()

    update.message.reply_text("✅ تم إرسال طلبك للإجازة. سيتم إشعارك بالموافقة.")
    notify_admins(context, f"❓ **[طلب إجازة]**\nالموظف: {employee['full_name']}\nالطلب: {reason}")
    return ConversationHandler.END

def cancel_command(update: Update, context: CallbackContext):
    """إلغاء المحادثة الحالية (مثل طلب إجازة)."""
    update.message.reply_text("تم إلغاء الأمر.")
    return ConversationHandler.END


# --- 7. أوامر المديرين (Admin Commands) ---

def admin_only(handler):
    """
    (Decorator) - دالة لتغليف الأوامر
    للتأكد من أن المستخدم هو مدير قبل تنفيذ الأمر.
    """
    def wrapped(update: Update, context: CallbackContext, *args, **kwargs):
        if not is_admin(update.effective_user.id):
            update.message.reply_text("ليس لديك الصلاحية لاستخدام هذا الأمر. 🚫")
            return
        return handler(update, context, *args, **kwargs)
    return wrapped

@admin_only
def list_employees_command(update: Update, context: CallbackContext):
    """عرض قائمة بجميع الموظفين."""
    conn = get_db_connection()
    if not conn:
        update.message.reply_text("خطأ في الخادم.")
        return

    with conn.cursor() as cur:
        cur.execute("SELECT full_name, phone_number, position, is_admin FROM employees ORDER BY full_name")
        employees = cur.fetchall()
        
        if not employees:
            update.message.reply_text("لا يوجد موظفون مسجلون حالياً.")
            return

        msg = "👥 **قائمة الموظفين:**\n"
        msg += "--------------------\n"
        for emp in employees:
            admin_status = "👑" if emp[3] else "👤"
            msg += f"{admin_status} **{emp[0]}** ({emp[2]})\n  📞 {emp[1]}\n"
        
        # إرسال الرسالة (قد تكون طويلة)
        for part in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
            update.message.reply_text(part, parse_mode=ParseMode.MARKDOWN)
    conn.close()

@admin_only
def daily_report_command(update: Update, context: CallbackContext):
    """عرض تقرير الحضور اليومي."""
    conn = get_db_connection()
    if not conn:
        update.message.reply_text("خطأ في الخادم.")
        return

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.full_name, a.check_in_time, a.check_out_time
            FROM employees e
            LEFT JOIN attendance a ON e.id = a.employee_id AND a.work_date = CURRENT_DATE
            ORDER BY e.full_name;
            """
        )
        report = cur.fetchall()
        
        if not report:
            update.message.reply_text("لا توجد بيانات حضور لهذا اليوم.")
            return

        msg = f"**📊 تقرير الحضور ليوم {datetime.date.today()}**\n"
        msg += "---------------------------------\n"
        for row in report:
            name = row[0]
            check_in = row[1].strftime('%H:%M') if row[1] else "---"
            check_out = row[2].strftime('%H:%M') if row[2] else "---"
            
            if row[1] is None:
                msg += f"• **{name}**: ❌ (لم يحضر)\n"
            else:
                msg += f"• **{name}**: ✅ {check_in}  ➡️  {check_out}\n"
        
        update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    conn.close()

# أوامر إدارية (تحتاج ConversationHandler - تم وضع الهيكل)

@admin_only
def add_employee_start(update: Update, context: CallbackContext):
    """(مدير) بدء إضافة موظف جديد - طلب الاسم."""
    update.message.reply_text("أرسل الاسم الكامل للموظف الجديد:")
    return ASK_NAME

def add_employee_phone(update: Update, context: CallbackContext):
    """(مدير) استلام الاسم وطلب الهاتف."""
    context.user_data['new_emp_name'] = update.message.text
    update.message.reply_text("أرسل رقم هاتف الموظف (بصيغة دولية: 9627...):")
    return ASK_PHONE

def add_employee_save(update: Update, context: CallbackContext):
    """(مدير) استلام الهاتف وحفظ الموظف (هنا فقط نكتفي بالاسم والهاتف)."""
    phone_input = update.message.text
    # تنظيف رقم الهاتف
    phone = phone_input.lstrip('00').lstrip('+')
    name = context.user_data['new_emp_name']
    
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                # إدراج الحد الأدنى من البيانات
                cur.execute(
                    "INSERT INTO employees (full_name, phone_number) VALUES (%s, %s)",
                    (name, phone)
                )
                conn.commit()
            update.message.reply_text(f"✅ تم إضافة الموظف '{name}' برقم هاتف '{phone}' بنجاح إلى النظام.")
        except psycopg2.errors.UniqueViolation:
            update.message.reply_text("خطأ: رقم الهاتف هذا مسجل مسبقاً.")
        except Exception as e:
            logger.error(f"خطأ في إضافة موظف: {e}")
            update.message.reply_text("حدث خطأ غير متوقع أثناء الحفظ.")
        finally:
            conn.close()
            context.user_data.clear()
            return ConversationHandler.END
    else:
        update.message.reply_text("خطأ في الاتصال بالخادم.")
        return ConversationHandler.END

# --- الأوامر الإدارية المتبقية (كدوال بسيطة مؤقتة) ---
@admin_only
def remove_employee_command(update: Update, context: CallbackContext):
    update.message.reply_text("أمر `/remove_employee` (قيد الإنشاء). يرجى تحديد موظف للحذف.")
@admin_only
def edit_details_command(update: Update, context: CallbackContext):
    update.message.reply_text("أمر `/edit_details` (قيد الإنشاء).")
@admin_only
def weekly_report_command(update: Update, context: CallbackContext):
    update.message.reply_text("أمر `/weekly_report` (قيد الإنشاء).")
@admin_only
def list_admins_command(update: Update, context: CallbackContext):
    update.message.reply_text("أمر `/list_admins` (قيد الإنشاء).")
@admin_only
def add_admin_command(update: Update, context: CallbackContext):
    update.message.reply_text("أمر `/add_admin` (قيد الإنشاء).")
@admin_only
def remove_admin_command(update: Update, context: CallbackContext):
    update.message.reply_text("أمر `/remove_admin` (قيد الإنشاء).")


# --- الدالة الرئيسية (Main) ---

def main():
    """تشغيل البوت."""
    
    if not TELEGRAM_TOKEN or not DATABASE_URL:
        logger.critical("خطأ: يرجى التأكد من إعداد TELEGRAM_TOKEN و DATABASE_URL كمتغيرات بيئة.")
        return

    # إعداد قاعدة البيانات لأول مرة
    setup_database()
    
    # تهيئة البوت
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    
    # الحصول على موزع الأوامر (Dispatcher)
    dp = updater.dispatcher
    
    # --- تعريف محادثات (Conversations) ---
    
    # 1. محادثة طلب الإجازة / المغادرة
    leave_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('leave', leave_command),
            CommandHandler('vacation', vacation_command)
        ],
        states={
            ASK_LEAVE_REASON: [MessageHandler(Filters.text & ~Filters.command, handle_leave_reason)],
            ASK_VACATION_REASON_DAYS: [MessageHandler(Filters.text & ~Filters.command, handle_vacation_reason)],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)]
    )
    
    # 2. محادثة إضافة موظف
    add_emp_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('add_employee', add_employee_start)],
        states={
            ASK_NAME: [MessageHandler(Filters.text & ~Filters.command, add_employee_phone)],
            ASK_PHONE: [MessageHandler(Filters.text & ~Filters.command, add_employee_save)],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)]
    )

    # --- تسجيل الأوامر (Handlers) ---
    
    # أوامر الموظفين
    dp.add_handler(CommandHandler("start", start_command))
    dp.add_handler(MessageHandler(Filters.contact, handle_contact)) # لاستلام رقم الهاتف
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("check_in", check_in_command))
    dp.add_handler(CommandHandler("check_out", check_out_command))
    dp.add_handler(CommandHandler("break", break_command))
    dp.add_handler(CommandHandler("smoke", smoke_command))
    
    # أوامر الإجازات (كمحادثة)
    dp.add_handler(leave_conv_handler)
    
    # أوامر المديرين
    dp.add_handler(add_emp_conv_handler)
    dp.add_handler(CommandHandler("list_employees", list_employees_command))
    dp.add_handler(CommandHandler("daily_report", daily_report_command))
    # إضافة الأوامر الإدارية المتبقية
    dp.add_handler(CommandHandler("remove_employee", remove_employee_command))
    dp.add_handler(CommandHandler("edit_details", edit_details_command))
    dp.add_handler(CommandHandler("weekly_report", weekly_report_command))
    dp.add_handler(CommandHandler("list_admins", list_admins_command))
    dp.add_handler(CommandHandler("add_admin", add_admin_command))
    dp.add_handler(CommandHandler("remove_admin", remove_admin_command))

    # معالج الأزرار (CallbackQuery)
    dp.add_handler(CallbackQueryHandler(im_back_callback, pattern='^im_back$'))

    # بدء تشغيل البوت (باستخدام Polling)
    logger.info("... بدء تشغيل البوت (Polling) ...")
    updater.start_polling()
    
    # إبقاء البوت يعمل
    updater.idle()

if __name__ == '__main__':
    # لتسهيل التتبع في السجلات
    logging.getLogger('__main__').info("Starting Employee Management Bot...")
    main()
