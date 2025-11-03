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

# الحصول على التوكن من متغيرات البيئة
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# إعدادات التسجيل (Logging)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# قائمة معرفات المديرين - يمكنك إضافة أكثر من مدير هنا
ADMIN_IDS = [1465191277, 6798279805] 

# قائمة أرقام الهواتف المصرح لها (يجب إضافة أرقام الموظفين هنا)
# يتم تخزين الرقم مع البادئة + في هذه القائمة للمقارنة
authorized_phones = [
    '+962786644106'
    # أضف أرقاماً مصرحاً بها أخرى
]

# ... (بقية الثوابت: user_database, daily_smoke_count, MAX_DAILY_SMOKES, JORDAN_TZ, إلخ) ...

# -----------------------------------------------------------
# 🛠️ دوال المساعدة العامة (تأكد من وجودها في ملفك)
# -----------------------------------------------------------

def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات"""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL environment variable is not set.")
        raise ConnectionError("DATABASE_URL is missing.")
    return psycopg2.connect(db_url)

def normalize_phone(phone_number):
    """تطبيع رقم الهاتف للتخزين أو المقارنة (إزالة + إذا كان موجوداً)"""
    return phone_number.lstrip('+')

def get_jordan_time():
    """الحصول على الوقت الحالي بتوقيت الأردن"""
    return datetime.now(JORDAN_TZ)

# -----------------------------------------------------------
# 🎯 الدوال التي تحتاجها للتحقق من الموظف (يجب أن تكون معرفة في الكود)
# -----------------------------------------------------------

# ⚠️ افتراض وجود هذه الدوال في ملفك الأصلي:
# def initialize_database_tables(): ...
# def save_employee(telegram_id, phone_number, full_name): ...
# def get_employee_by_telegram_id(telegram_id): ...
# def get_employee_by_phone(phone_number): ...
# def is_admin(user_id): ...
# def verify_employee(phone_number): ... # للتحقق من وجود الرقم في قائمة المصرح لهم
# def get_user_phone(user_id): ... # للحصول على رقم الهاتف من قاعدة البيانات أو user_database
# ... (بقية الدوال: record_check_in, record_check_out, إلخ)

# -----------------------------------------------------------
# 🛠️ دالة مساعدة جديدة لإنشاء وعرض قائمة الأوامر (مستخلصة من دالة start)
# -----------------------------------------------------------

async def send_command_list(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, user_first_name: str, user_phone: str):
    """تنشئ وترسل قائمة الأوامر بعد التحقق من الهوية."""
    
    # ⚠️ ملاحظة: قمت بتعديل النص ليناسب التنسيق MarkdownV2 ولإضافة الترحيب
    message = (
        f"مرحبًا {user_first_name}! 👋\\n\\n"
        f"✅ تم التحقق من هويتك بنجاح!\\n"
        f"📱 رقم الهاتف المسجل: {user_phone}\\n\\n"
        "━━━━━━━━━━━━━━━━━\\n"
        "┏━━━━━━━━━━━━━━━━━━━━━┓\\n"
        "┃   📚 قائمة الأوامر   ┃\\n"
        "┗━━━━━━━━━━━━━━━━━━━━━┛\\n\\n"
        "🔹 أوامر الحضور والانصراف:\\n"
        "━━━━━━━━━━━━━━━━━\\n"
        "/check_in - تسجيل الحضور 📥\\n"
        "  (إلزامي في بداية الدوام)\\n\\n"
        "/check_out - تسجيل الانصراف 📤\\n"
        "  (إلزامي في نهاية الدوام)\\n\\n"
        "/attendance_report - تقرير حضورك 📊\\n"
        "  (آخر 7 أيام)\\n\\n"
        "🔹 أوامر الاستراحات:\\n"
        "━━━━━━━━━━━━━━━━━\\n"
        "/smoke - طلب استراحة تدخين 🚬\\n"
        "  (5 دقائق، حد أقصى 6 سجائر/يوم، فجوة 1.5 ساعة)\\n\n"
        "/break - طلب استراحة غداء ☕\\n"
        "  (30 دقيقة، مرة واحدة في اليوم)\\n\n"
        "🔹 أوامر الإجازات:\\n"
        "━━━━━━━━━━━━━━━━━\\n"
        "/leave - طلب مغادرة العمل 🚪\\n"
        "  (مع سبب المغادرة)\\n\n"
        "/vacation - طلب عطلة 🌴\\n"
        "  (مع سبب وعذر)\\n\n"
        "/help - عرض المساعدة 📖\\n\n"
    )

    # إضافة أوامر المدير فقط إذا كان المستخدم مديراً
    if is_admin(user_id):
        message += (
            "🔸 أوامر المدير:\\n"
            "━━━━━━━━━━━━━━━━━\\n"
            "/list_employees - عرض جميع الموظفين 👥\\n"
            "/add_employee - إضافة موظف جديد ➕\\n"
            "/remove_employee - حذف موظف ❌\\n"
            "/edit_details - تعديل تفاصيل موظف 📋\\n\\n"
        )
    
    message += "━━━━━━━━━━━━━━━━━\\n✨ يمكنك الآن استخدام جميع الأوامر!"

    # إرسال القائمة وإزالة لوحة مفاتيح "مشاركة جهة الاتصال"
    await update.message.reply_text(
        message, 
        parse_mode="MarkdownV2", 
        reply_markup=ReplyKeyboardRemove()
    )


# -----------------------------------------------------------
# 🎯 المعالج الجديد لرسالة جهة الاتصال
# -----------------------------------------------------------

async def contact_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج يستقبل جهة الاتصال المرسلة للتحقق من هوية الموظف."""
    
    contact = update.message.contact
    user = update.message.from_user
    user_id = contact.user_id
    
    # تنظيف الرقم للمقارنة مع قائمة authorized_phones
    # يُفضّل أن تكون قائمة authorized_phones تحتوي على '+' لتجنب الأخطاء
    phone_number_full = contact.phone_number
    
    # 1. تحقق أمان: هل المستخدم قام بمشاركة جهة اتصاله الخاصة؟
    if user_id != user.id:
        await update.message.reply_text("الرجاء مشاركة جهة اتصالك الخاصة، وليس جهة اتصال شخص آخر.")
        return

    # 2. التحقق من الرقم مقابل القائمة المصرح بها (نستخدم قائمة authorized_phones)
    if phone_number_full in authorized_phones:
        
        # حفظ بيانات الموظف في قاعدة البيانات وتحديث telegram_id
        full_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip() or "موظف جديد"
        save_employee(user_id, phone_number_full, full_name)
        
        # 3. إرسال قائمة الأوامر (تم حل المشكلة هنا)
        await send_command_list(
            update, 
            context, 
            user_id,
            contact.first_name or "موظف", 
            phone_number_full 
        )
        
    else:
        # إذا لم يكن الرقم مصرحاً به
        await update.message.reply_text(
            f"🚫 عذراً، رقم الهاتف {phone_number_full} غير مسجل في النظام. الرجاء التواصل مع الإدارة."
        )


# -----------------------------------------------------------
# 🔄 تعديل دالة start لتستخدم الدالة الجديدة
# -----------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال رسالة ترحيبية أو قائمة الأوامر إذا كان مسجلاً."""
    user = update.message.from_user
    user_phone = get_user_phone(user.id) # افتراض وجود هذه الدالة في كودك
    user_first_name = user.first_name

    # إذا كان المستخدم مسجلاً ومصرحاً له
    if user_phone and verify_employee(user_phone): # افتراض وجود هذه الدالة في كودك
        # إرسال قائمة الأوامر مباشرة
        await send_command_list(update, context, user.id, user_first_name, user_phone)
    else:
        # طلب مشاركة جهة الاتصال
        keyboard = [[KeyboardButton("مشاركة رقم الهاتف 📱", request_contact=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        welcome_message = ( 
            f"مرحبًا {user_first_name}! 👋\n\n"
            "أنا بوت إدارة حضور الموظفين.\n\n"
            "⚠️ للبدء، يرجى مشاركة رقم هاتفك للتحقق من هويتك كموظف.\n\n"
            "اضغط على الزر أدناه لمشاركة رقم الهاتف:" 
        )
        await update.message.reply_text(welcome_message, reply_markup=reply_markup)

# -----------------------------------------------------------
# ⚙️ دالة main الجديدة (لحل مشكلة 409 Conflict)
# -----------------------------------------------------------

# ⚠️ احتفظ بجميع الدوال الأخرى (مثل help_command, check_in_command, إلخ) كما هي
# تأكد من نقل جميع الدوال المساعدة (مثل get_db_connection, save_employee) إلى الأعلى

def main():
    """بدء البوت باستخدام Webhook في بيئة الإنتاج (Render) أو Polling للتطوير المحلي."""
    if not BOT_TOKEN:
        logger.error("ERROR: TELEGRAM_BOT_TOKEN not found in environment variables!")
        return

    # ⚠️ إعداد متغيرات Webhook
    # Render يخصص المنفذ تلقائياً، وغالباً ما يكون 10000
    PORT = int(os.environ.get("PORT", "5000")) 
    # يجب تعيين هذا المتغير في إعدادات Render
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL") 
    
    logger.info("Starting Employee Management Bot...")
    
    # 1. تهيئة قاعدة البيانات (افتراض وجود initialize_database_tables في ملفك)
    try:
        initialize_database_tables()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        return
    
    # 2. بناء التطبيق
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 3. إضافة جميع المعالجات (Handlers)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check_in", check_in_command))
    application.add_handler(CommandHandler("check_out", check_out_command))
    application.add_handler(CommandHandler("daily_report", daily_report_command))
    
    # 🎯 إضافة المعالج الجديد لجهة الاتصال (الذي يحل مشكلة القائمة)
    application.add_handler(MessageHandler(filters.CONTACT, contact_received)) 
    
    # ... (أضف باقي المعالجات الأخرى مثل ConversationHandler for leave/vacation/edit_details) ...

    # 4. تشغيل البوت بناءً على البيئة
    if WEBHOOK_URL:
        # وضع الإنتاج (Render) - استخدام Webhook
        logger.info(f"Running in Webhook mode on port {PORT}. Webhook URL: {WEBHOOK_URL}{BOT_TOKEN}")
        
        application.run_webhook(
            listen="0.0.0.0",               # الاستماع على جميع الواجهات
            port=PORT,                      # المنفذ المحدد من Render
            url_path=BOT_TOKEN,             # استخدام التوكن كمسار URL للحماية
            webhook_url=f"{WEBHOOK_URL}{BOT_TOKEN}" # مسار الويب هوك الكامل
        )
    else:
        # وضع التطوير المحلي - استخدام Polling (كما كان الكود الأصلي)
        logger.warning("WEBHOOK_URL not set. Running with Polling (for local development).")
        application.run_polling(poll_interval=10) # يمكنك ترك فترة الاستعلام كما تريد


if __name__ == '__main__':
    main()
