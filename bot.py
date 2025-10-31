import os
import logging
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes

# حالات المحادثة
LEAVE_REASON, VACATION_REASON, EDIT_DETAIL_SELECT, EDIT_DETAIL_INPUT, ADD_MANAGER_TYPE = range(5)

# إعدادات التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# التوكن من متغيرات البيئة
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# المناطق الزمنية
JORDAN_TZ = ZoneInfo('Asia/Amman')

# القواميس المؤقتة
active_timers = {}
timer_completed = {}
user_database = {}

class DatabaseManager:
    """مدير قاعدة البيانات"""
    
    @staticmethod
    def get_db_connection():
        """إنشاء اتصال بقاعدة البيانات"""
        return psycopg2.connect(os.environ.get("DATABASE_URL"))
    
    @staticmethod
    def get_system_setting(key, default=None):
        """الحصول على إعداد من النظام"""
        try:
            conn = DatabaseManager.get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT setting_value FROM system_settings WHERE setting_key = %s", (key,))
            result = cur.fetchone()
            cur.close()
            conn.close()
            return result[0] if result else default
        except Exception as e:
            logger.error(f"خطأ في قراءة الإعداد {key}: {e}")
            return default
    
    @staticmethod
    def is_friday():
        """التحقق إذا كان اليوم جمعة"""
        return get_jordan_time().weekday() == 4  # 4 = Friday
    
    @staticmethod
    def get_max_daily_smokes():
        """الحصول على الحد الأقصى للسجائر حسب اليوم"""
        if DatabaseManager.is_friday():
            return int(DatabaseManager.get_system_setting('max_daily_smokes_friday', 3))
        return int(DatabaseManager.get_system_setting('max_daily_smokes', 6))
    
    @staticmethod
    def can_take_lunch_break():
        """التحقق إذا كان مسموحاً بأخذ بريك غداء"""
        return not DatabaseManager.is_friday()

class EmployeeManager:
    """مدير الموظفين"""
    
    @staticmethod
    def save_employee(telegram_id, phone_number, full_name):
        """حفظ أو تحديث بيانات الموظف"""
        try:
            conn = DatabaseManager.get_db_connection()
            cur = conn.cursor()
            
            cur.execute("""
                INSERT INTO employees (telegram_id, phone_number, full_name, last_active)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (telegram_id) 
                DO UPDATE SET 
                    phone_number = EXCLUDED.phone_number,
                    full_name = EXCLUDED.full_name,
                    last_active = CURRENT_TIMESTAMP
                RETURNING id
            """, (telegram_id, phone_number, full_name))
            
            employee_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            conn.close()
            return employee_id
        except Exception as e:
            logger.error(f"خطأ في حفظ الموظف: {e}")
            return None
    
    @staticmethod
    def get_employee_by_telegram_id(telegram_id):
        """الحصول على بيانات الموظف"""
        try:
            conn = DatabaseManager.get_db_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT * FROM employees WHERE telegram_id = %s", (telegram_id,))
            employee = cur.fetchone()
            cur.close()
            conn.close()
            return dict(employee) if employee else None
        except Exception as e:
            logger.error(f"خطأ في قراءة بيانات الموظف: {e}")
            return None

class AdminManager:
    """مدير المديرين"""
    
    @staticmethod
    def is_admin(user_id):
        """التحقق إذا كان المستخدم مدير"""
        try:
            conn = DatabaseManager.get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT telegram_id FROM admins WHERE telegram_id = %s", (user_id,))
            result = cur.fetchone()
            cur.close()
            conn.close()
            return bool(result)
        except Exception as e:
            logger.error(f"خطأ في التحقق من المدير: {e}")
            return False
    
    @staticmethod
    def is_super_admin(user_id):
        """التحقق إذا كان المستخدم مدير رئيسي"""
        try:
            conn = DatabaseManager.get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT is_super_admin FROM admins WHERE telegram_id = %s", (user_id,))
            result = cur.fetchone()
            cur.close()
            conn.close()
            return result[0] if result else False
        except Exception as e:
            logger.error(f"خطأ في التحقق من المدير الرئيسي: {e}")
            return False
    
    @staticmethod
    def can_approve_requests(user_id):
        """التحقق إذا كان المدير يمكنه الموافقة على الطلبات"""
        try:
            conn = DatabaseManager.get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT can_approve FROM admins WHERE telegram_id = %s", (user_id,))
            result = cur.fetchone()
            cur.close()
            conn.close()
            return result[0] if result else False
        except Exception as e:
            logger.error(f"خطأ في التحقق من صلاحية الموافقة: {e}")
            return False
    
    @staticmethod
    def add_admin(telegram_id, added_by, admin_type="normal"):
        """إضافة مدير جديد"""
        try:
            conn = DatabaseManager.get_db_connection()
            cur = conn.cursor()
            
            is_super = admin_type == "super"
            can_approve = admin_type != "view_only"
            can_view_only = admin_type == "view_only"
            
            cur.execute("""
                INSERT INTO admins (telegram_id, added_by, is_super_admin, can_approve, can_view_only)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (telegram_id) DO UPDATE SET
                    is_super_admin = EXCLUDED.is_super_admin,
                    can_approve = EXCLUDED.can_approve,
                    can_view_only = EXCLUDED.can_view_only
            """, (telegram_id, added_by, is_super, can_approve, can_view_only))
            
            conn.commit()
            cur.close()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"خطأ في إضافة المدير: {e}")
            return False

class AttendanceManager:
    """مدير الحضور"""
    
    @staticmethod
    def record_check_in(employee_id):
        """تسجيل حضور الموظف"""
        try:
            conn = DatabaseManager.get_db_connection()
            cur = conn.cursor()
            
            now = get_jordan_time()
            today = now.date()
            
            # التحقق من التسجيل المسبق
            cur.execute("""
                SELECT check_in_time FROM attendance 
                WHERE employee_id = %s AND date = %s
            """, (employee_id, today))
            
            if cur.fetchone():
                cur.close()
                conn.close()
                return {'success': False, 'error': 'already_checked_in'}
            
            # حساب التأخير
            work_start = now.replace(hour=8, minute=0, second=0, microsecond=0)
            late_minutes = max(0, int((now - work_start).total_seconds() / 60))
            is_late = late_minutes > 15  # 15 دقيقة سماح
            
            # تسجيل الحضور
            cur.execute("""
                INSERT INTO attendance (employee_id, date, check_in_time, is_late, late_minutes)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (employee_id, today, now, is_late, late_minutes))
            
            conn.commit()
            cur.close()
            conn.close()
            
            return {
                'success': True,
                'check_in_time': now,
                'is_late': is_late,
                'late_minutes': late_minutes
            }
        except Exception as e:
            logger.error(f"خطأ في تسجيل الحضور: {e}")
            return {'success': False, 'error': str(e)}

# الدوال المساعدة
def get_jordan_time():
    """الحصول على الوقت الحالي بتوقيت الأردن"""
    return datetime.now(JORDAN_TZ)

def create_progress_bar(current_seconds, total_seconds, length=20):
    """إنشاء شريط تقدم"""
    percentage = current_seconds / total_seconds
    filled = int(percentage * length)
    empty = length - filled
    bar = '█' * filled + '░' * empty
    percent = int(percentage * 100)
    return f"[{bar}] {percent}%"

# handlers الأساسية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت"""
    user = update.message.from_user
    
    keyboard = [[KeyboardButton("مشاركة رقم الهاتف 📱", request_contact=True)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    welcome_message = (
        "👋 أهلاً وسهلاً!\n\n"
        "🤖 أنا بوت إدارة حضور الموظفين\n\n"
        "📍 للمتابعة، يرجى مشاركة رقم هاتفك للتحقق من هويتك:\n\n"
        "⬇️ اضغط على الزر أدناه"
    )
    
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة مشاركة رقم الهاتف"""
    contact = update.message.contact
    user = update.message.from_user
    
    if contact and contact.user_id == user.id:
        phone_number = contact.phone_number
        full_name = contact.first_name or "موظف"
        
        # حفظ البيانات
        employee_id = EmployeeManager.save_employee(user.id, phone_number, full_name)
        
        if employee_id:
            await update.message.reply_text(
                f"✅ تم التحقق بنجاح!\n\n"
                f"👤 الاسم: {full_name}\n"
                f"📱 الهاتف: {phone_number}\n\n"
                "🎉 يمكنك الآن استخدام جميع ميزات البوت!",
                reply_markup=ReplyKeyboardRemove()
            )
            
            # إرسال قائمة الأوامر
            await help_command(update, context)
        else:
            await update.message.reply_text(
                "❌ حدث خطأ في حفظ البيانات. يرجى المحاولة مرة أخرى.",
                reply_markup=ReplyKeyboardRemove()
            )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض المساعدة"""
    user = update.message.from_user
    
    help_text = (
        "📚 قائمة الأوامر المتاحة:\n\n"
        "🔹 الحضور والانصراف:\n"
        "/check_in - تسجيل الحضور 📥\n"
        "/check_out - تسجيل الانصراف 📤\n"
        "/attendance_report - تقرير الحضور 📊\n\n"
        "🔹 الاستراحات:\n"
        "/smoke - استراحة تدخين 🚬\n"
        "/break - استراحة غداء ☕\n\n"
    )
    
    if AdminManager.is_admin(user.id):
        help_text += (
            "🔸 أوامر المدير:\n"
            "/admin - لوحة التحكم 👨‍💼\n"
            "/daily_report - التقرير اليومي 📈\n"
            "/weekly_report - التقرير الأسبوعي 📊\n"
        )
    
    if AdminManager.is_super_admin(user.id):
        help_text += (
            "🔸 أوامر المدير الرئيسي:\n"
            "/add_manager - إضافة مدير جديد ➕\n"
        )
    
    await update.message.reply_text(help_text)

async def check_in_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل الحضور"""
    user = update.message.from_user
    
    employee = EmployeeManager.get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text("❌ يرجى استخدام /start أولاً للتحقق من هويتك.")
        return
    
    result = AttendanceManager.record_check_in(employee['id'])
    
    if result['success']:
        check_in_time = result['check_in_time']
        
        if result['is_late']:
            message = (
                f"⚠️ تم تسجيل الحضور مع تأخير!\n\n"
                f"⏰ الوقت: {check_in_time.strftime('%H:%M:%S')}\n"
                f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}\n"
                f"⏱ التأخير: {result['late_minutes']} دقيقة\n\n"
                f"🚨 يرجى الالتزام بموعد الحضور!"
            )
        else:
            message = (
                f"✅ تم تسجيل الحضور بنجاح!\n\n"
                f"⏰ الوقت: {check_in_time.strftime('%H:%M:%S')}\n"
                f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}\n"
                f"💼 يوم عمل موفق! 🚀"
            )
    else:
        if result.get('error') == 'already_checked_in':
            message = "⚠️ لقد سجلت حضورك مسبقاً اليوم!"
        else:
            message = f"❌ خطأ في تسجيل الحضور: {result.get('error')}"
    
    await update.message.reply_text(message)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة تحكم المدير"""
    user = update.message.from_user
    
    if not AdminManager.is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر للمديرين فقط.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📊 التقرير اليومي", callback_data="admin_daily_report")],
        [InlineKeyboardButton("📈 التقرير الأسبوعي", callback_data="admin_weekly_report")],
    ]
    
    if AdminManager.can_approve_requests(user.id):
        keyboard.append([InlineKeyboardButton("✅ الطلبات المنتظرة", callback_data="admin_pending_requests")])
    
    if AdminManager.is_super_admin(user.id):
        keyboard.append([InlineKeyboardButton("👥 إدارة المديرين", callback_data="admin_manage_admins")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    admin_type = "👑 مدير رئيسي" if AdminManager.is_super_admin(user.id) else "👨‍💼 مدير عادي"
    if not AdminManager.can_approve_requests(user.id):
        admin_type = "👀 مدير مشاهد فقط"
    
    await update.message.reply_text(
        f"👨‍💼 لوحة تحكم المدير\n\n"
        f"🎯 صلاحياتك: {admin_type}\n\n"
        f"اختر الإجراء المطلوب:",
        reply_markup=reply_markup
    )

# الدوال الجديدة للتذكيرات
async def send_reminder(context: ContextTypes.DEFAULT_TYPE, reminder_type):
    """إرسال تذكير للموظفين"""
    try:
        conn = DatabaseManager.get_db_connection()
        cur = conn.cursor()
        
        # جلب جميع الموظفين النشطين
        cur.execute("SELECT telegram_id FROM employees WHERE is_active = TRUE")
        employees = cur.fetchall()
        cur.close()
        conn.close()
        
        if reminder_type == "check_in":
            message = "⏰ تذكير: وقت الحضور!\n\n🕗 يرجى تسجيل الحضور باستخدام /check_in"
        else:
            message = "⏰ تذكير: وقت الانصراف!\n\n🕔 يرجى تسجيل الانصراف باستخدام /check_out"
        
        for employee in employees:
            try:
                await context.bot.send_message(
                    chat_id=employee[0],
                    text=message
                )
            except Exception as e:
                logger.debug(f"Failed to send reminder to {employee[0]}: {e}")
        
        logger.info(f"تم إرسال تذكير {reminder_type} لجميع الموظفين")
        
    except Exception as e:
        logger.error(f"خطأ في إرسال التذكير: {e}")

async def reminder_check_in(context: ContextTypes.DEFAULT_TYPE):
    """تذكير الحضور"""
    await send_reminder(context, "check_in")

async def reminder_check_out(context: ContextTypes.DEFAULT_TYPE):
    """تذكير الانصراف"""
    await send_reminder(context, "check_out")

def main():
    """الدالة الرئيسية"""
    if not BOT_TOKEN:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN not found!")
        return
    
    print("🚀 بدء تشغيل بوت إدارة الموظفين...")
    print("📅 النظام المحدث يشمل:")
    print("   ✅ نظام الجمعة المميز (3 سجائر، لا بريك غداء)")
    print("   ✅ 3 أنواع من المديرين")
    print("   ✅ تذكيرات تلقائية يومية")
    print("   ✅ تصميم احترافي وجذاب")
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # إضافة handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check_in", check_in_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    
    # إعداد التذكيرات اليومية
    job_queue = application.job_queue
    if job_queue:
        # تذكير الحضور 7:45 ص
        job_queue.run_daily(
            reminder_check_in,
            time=datetime.strptime("07:45", "%H:%M").time(),
            days=(0, 1, 2, 3, 4, 5, 6),
            name="check_in_reminder"
        )
        
        # تذكير الانصراف 4:45 م
        job_queue.run_daily(
            reminder_check_out,
            time=datetime.strptime("16:45", "%H:%M").time(),
            days=(0, 1, 2, 3, 4, 5, 6),
            name="check_out_reminder"
        )
        
        print("✅ تم جدولة التذكيرات اليومية")
    
    print("🤖 البوت يعمل الآن! اضغط Ctrl+C لإيقافه")
    
    # تشغيل البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()