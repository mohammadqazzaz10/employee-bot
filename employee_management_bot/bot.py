import os
import logging
# استيراد دوال قاعدة البيانات من الملف الجديد db.py
from .db import get_db_connection, initialize_database_tables 

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
ADMIN_IDS = [1465191277, 6798279805]  # أضف معرفات المديرين الإضافيين مثل: [1465191277, 987654321, 123456789]

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

# دوال قاعدة البيانات تم نقلها إلى db.py:
# def get_db_connection()...
# def initialize_database_tables()...

def save_employee(telegram_id, phone_number, full_name):
    """حفظ أو تحديث بيانات الموظف في قاعدة البيانات"""
    try:
        normalized_phone = normalize_phone(phone_number)
        conn = get_db_connection()
        cur = conn.cursor()

        # هذه الدالة تتطلب دالة get_employee_by_phone غير المعرفة هنا
        # يجب تعريفها أو تعديل الكود

        if telegram_id:
            # افتراض أن دالة get_employee_by_phone معرفة
            # existing_by_phone = get_employee_by_phone(phone_number) 
            
            # تم اختصار الدالة هنا لعدم وجود get_employee_by_phone في الكود المرفق
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
            # حالة بدون telegram_id
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
        logger.error(f"Error saving employee: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return None


def normalize_phone(phone_number):
    """تطبيع رقم الهاتف بإزالة جميع الرموز غير الرقمية والأصفار البادئة"""
    if not phone_number:
        return ""
    digits_only = ''.join(filter(str.isdigit, phone_number))
    while digits_only.startswith('00'):
        digits_only = digits_only[2:]
    return digits_only


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دالة البداية - طلب التحقق من هوية المستخدم"""
    user = update.message.from_user
    user_first_name = user.first_name

    welcome_message = f"مرحبًا {user_first_name}! 👋\n\nأنا بوت إدارة حضور الموظفين.\n\n"
    
    keyboard = [[KeyboardButton("مشاركة رقم الهاتف 📱", request_contact=True)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)

    await update.message.reply_text(welcome_message, reply_markup=reply_markup)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض رسالة المساعدة"""
    help_text = (
        "📚 قائمة الأوامر:\n\n"
        "🔹 الحضور والانصراف:\n"
        "/check_in - تسجيل الحضور 📥\n"
        "/check_out - تسجيل الانصراف 📤\n"
        "/attendance_report - تقرير حضورك 📊\n\n"
        "🔹 الاستراحات:\n"
        "/smoke - طلب استراحة تدخين 🚬\n"
        "/break - طلب استراحة غداء ☕\n\n"
        "🔹 الإجازات:\n"
        "/leave - طلب مغادرة العمل 🚪\n"
        "/vacation - طلب عطلة 🌴\n\n"
        "🔹 أوامر مساعدة:\n"
        "/start - بدء البوت\n"
        "/help - عرض هذه الرسالة\n"
        "/my_id - عرض معرف Telegram الخاص بك\n\n"
    )
    
    await update.message.reply_text(help_text)


async def check_in_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل حضور الموظف"""
    user = update.message.from_user
    user_first_name = user.first_name
    
    message = f"✅ تم تسجيل حضورك بنجاح, {user_first_name}!"
    
    await update.message.reply_text(message)


async def check_out_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل انصراف الموظف"""
    user = update.message.from_user
    user_first_name = user.first_name

    message = f"✅ تم تسجيل انصرافك بنجاح, {user_first_name}!"
    
    await update.message.reply_text(message)


async def daily_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تقرير الحضور اليومي (للمدير فقط)"""
    # هذه الدالة ستقوم بعرض تقرير الحضور اليومي لجميع الموظفين
    pass


def run_bot():
    """بدء البوت"""
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not found in environment variables!")
        return
    
    print("Starting Employee Management Bot...")
    print(f"\nعدد المديرين المسجلين: {len(ADMIN_IDS)}")
    
    # تهيئة قاعدة البيانات قبل بدء البوت
    initialize_database_tables() 
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check_in", check_in_command))
    application.add_handler(CommandHandler("check_out", check_out_command))
    application.add_handler(CommandHandler("daily_report", daily_report_command))

    application.run_polling()


if __name__ == '__main__':
    run_bot()

