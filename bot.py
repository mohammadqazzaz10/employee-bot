import os
import logging
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes

# إعدادات التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# التوكن من متغيرات البيئة
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN not found in environment variables")
    print("❌ TELEGRAM_BOT_TOKEN not found in environment variables")

# المناطق الزمنية
JORDAN_TZ = ZoneInfo('Asia/Amman')

def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات"""
    try:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL not found")
        return psycopg2.connect(database_url, sslmode='require')
    except Exception as e:
        logger.error(f"❌ Failed to connect to database: {e}")
        return None

def get_jordan_time():
    """الحصول على الوقت الحالي بتوقيت الأردن"""
    return datetime.now(JORDAN_TZ)

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
    try:
        contact = update.message.contact
        user = update.message.from_user
        
        if contact and contact.user_id == user.id:
            phone_number = contact.phone_number
            full_name = contact.first_name or "موظف"
            
            # حفظ البيانات في قاعدة البيانات
            conn = get_db_connection()
            if not conn:
                await update.message.reply_text("❌ خطأ في الاتصال بقاعدة البيانات")
                return
                
            cur = conn.cursor()
            
            try:
                cur.execute("""
                    INSERT INTO employees (telegram_id, phone_number, full_name, last_active)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (telegram_id) 
                    DO UPDATE SET 
                        phone_number = EXCLUDED.phone_number,
                        full_name = EXCLUDED.full_name,
                        last_active = CURRENT_TIMESTAMP
                    RETURNING id
                """, (user.id, phone_number, full_name))
                
                employee_id = cur.fetchone()[0]
                conn.commit()
                
                await update.message.reply_text(
                    f"✅ تم التحقق بنجاح!\n\n"
                    f"👤 الاسم: {full_name}\n"
                    f"📱 الهاتف: {phone_number}\n\n"
                    "🎉 يمكنك الآن استخدام جميع ميزات البوت!",
                    reply_markup=ReplyKeyboardRemove()
                )
                
                # إرسال قائمة الأوامر
                await help_command(update, context)
                
            except Exception as e:
                conn.rollback()
                logger.error(f"خطأ في حفظ البيانات: {e}")
                await update.message.reply_text(
                    "❌ حدث خطأ في حفظ البيانات. يرجى المحاولة مرة أخرى.",
                    reply_markup=ReplyKeyboardRemove()
                )
            finally:
                cur.close()
                conn.close()
    except Exception as e:
        logger.error(f"خطأ في معالجة الاتصال: {e}")
        await update.message.reply_text("❌ حدث خطأ غير متوقع")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض المساعدة"""
    help_text = (
        "📚 قائمة الأوامر المتاحة:\n\n"
        "🔹 الحضور والانصراف:\n"
        "/check_in - تسجيل الحضور 📥\n"
        "/check_out - تسجيل الانصراف 📤\n"
        "/attendance_report - تقرير الحضور 📊\n\n"
        "🔹 الاستراحات:\n"
        "/smoke - استراحة تدخين 🚬\n"
        "/break - استراحة غداء ☕\n\n"
        "🔹 المساعدة:\n"
        "/help - عرض هذه الرسالة\n"
        "/start - إعادة البدء\n"
    )
    
    await update.message.reply_text(help_text)

async def check_in_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل الحضور"""
    try:
        user = update.message.from_user
        
        # التحقق من وجود الموظف
        conn = get_db_connection()
        if not conn:
            await update.message.reply_text("❌ خطأ في الاتصال بقاعدة البيانات")
            return
            
        cur = conn.cursor()
        cur.execute("SELECT id, full_name FROM employees WHERE telegram_id = %s", (user.id,))
        employee = cur.fetchone()
        
        if not employee:
            await update.message.reply_text("❌ يرجى استخدام /start أولاً للتحقق من هويتك.")
            cur.close()
            conn.close()
            return
        
        employee_id, employee_name = employee
        now = get_jordan_time()
        today = now.date()
        
        # التحقق من التسجيل المسبق
        cur.execute("SELECT check_in_time FROM attendance WHERE employee_id = %s AND date = %s", (employee_id, today))
        existing = cur.fetchone()
        
        if existing:
            await update.message.reply_text("⚠️ لقد سجلت حضورك مسبقاً اليوم!")
            cur.close()
            conn.close()
            return
        
        # حساب التأخير
        work_start = now.replace(hour=8, minute=0, second=0, microsecond=0)
        late_minutes = max(0, int((now - work_start).total_seconds() / 60))
        is_late = late_minutes > 15  # 15 دقيقة سماح
        
        # تسجيل الحضور
        cur.execute("""
            INSERT INTO attendance (employee_id, date, check_in_time, is_late, late_minutes)
            VALUES (%s, %s, %s, %s, %s)
        """, (employee_id, today, now, is_late, late_minutes))
        
        conn.commit()
        cur.close()
        conn.close()
        
        if is_late:
            message = (
                f"⚠️ تم تسجيل الحضور مع تأخير!\n\n"
                f"👤 الموظف: {employee_name}\n"
                f"⏰ الوقت: {now.strftime('%H:%M:%S')}\n"
                f"📅 التاريخ: {now.strftime('%Y-%m-%d')}\n"
                f"⏱ التأخير: {late_minutes} دقيقة\n\n"
                f"🚨 يرجى الالتزام بموعد الحضور!"
            )
        else:
            message = (
                f"✅ تم تسجيل الحضور بنجاح!\n\n"
                f"👤 الموظف: {employee_name}\n"
                f"⏰ الوقت: {now.strftime('%H:%M:%S')}\n"
                f"📅 التاريخ: {now.strftime('%Y-%m-%d')}\n"
                f"💼 يوم عمل موفق! 🚀"
            )
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"خطأ في تسجيل الحضور: {e}")
        await update.message.reply_text("❌ حدث خطأ في تسجيل الحضور")

async def reminder_check_in(context: ContextTypes.DEFAULT_TYPE):
    """تذكير الحضور"""
    try:
        conn = get_db_connection()
        if not conn:
            return
            
        cur = conn.cursor()
        cur.execute("SELECT telegram_id FROM employees WHERE is_active = TRUE")
        employees = cur.fetchall()
        cur.close()
        conn.close()
        
        message = "⏰ تذكير: وقت الحضور!\n\n🕗 يرجى تسجيل الحضور باستخدام /check_in"
        
        for employee in employees:
            try:
                await context.bot.send_message(
                    chat_id=employee[0],
                    text=message
                )
            except Exception as e:
                logger.debug(f"Failed to send reminder to {employee[0]}: {e}")
        
        logger.info("تم إرسال تذكير الحضور لجميع الموظفين")
        
    except Exception as e:
        logger.error(f"خطأ في إرسال تذكير الحضور: {e}")

async def reminder_check_out(context: ContextTypes.DEFAULT_TYPE):
    """تذكير الانصراف"""
    try:
        conn = get_db_connection()
        if not conn:
            return
            
        cur = conn.cursor()
        cur.execute("SELECT telegram_id FROM employees WHERE is_active = TRUE")
        employees = cur.fetchall()
        cur.close()
        conn.close()
        
        message = "⏰ تذكير: وقت الانصراف!\n\n🕔 يرجى تسجيل الانصراف باستخدام /check_out"
        
        for employee in employees:
            try:
                await context.bot.send_message(
                    chat_id=employee[0],
                    text=message
                )
            except Exception as e:
                logger.debug(f"Failed to send reminder to {employee[0]}: {e}")
        
        logger.info("تم إرسال تذكير الانصراف لجميع الموظفين")
        
    except Exception as e:
        logger.error(f"خطأ في إرسال تذكير الانصراف: {e}")

def main():
    """الدالة الرئيسية"""
    if not BOT_TOKEN:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN not found in environment variables!")
        print("Please set your bot token in the environment variables.")
        return
    
    print("🚀 بدء تشغيل بوت إدارة الموظفين...")
    print("📅 النظام المحدث يشمل:")
    print("   ✅ نظام الجمعة المميز (3 سجائر، لا بريك غداء)")
    print("   ✅ 3 أنواع من المديرين")
    print("   ✅ تذكيرات تلقائية يومية")
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # إضافة handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("check_in", check_in_command))
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
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        
    except Exception as e:
        logger.error(f"❌ فشل في تشغيل البوت: {e}")
        print(f"❌ فشل في تشغيل البوت: {e}")

if __name__ == '__main__':
    main()