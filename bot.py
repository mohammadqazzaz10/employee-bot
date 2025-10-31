import os
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# محاولة استيراد المكتبات مع معالجة الأخطاء
try:
    import psycopg2
    from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    print("✅ تم تحميل جميع المكتبات بنجاح")
except ImportError as e:
    print(f"❌ خطأ في تحميل المكتبات: {e}")
    sys.exit(1)

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

JORDAN_TZ = ZoneInfo('Asia/Amman')

def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات"""
    try:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            print("❌ DATABASE_URL not found")
            return None
        
        print("🟢 محاولة الاتصال بقاعدة البيانات...")
        conn = psycopg2.connect(database_url, sslmode='require')
        print("✅ تم الاتصال بقاعدة البيانات بنجاح")
        return conn
    except Exception as e:
        print(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت"""
    user = update.message.from_user
    print(f"🟢 استقبال أمر start من: {user.id}")
    
    keyboard = [[KeyboardButton("مشاركة رقم الهاتف 📱", request_contact=True)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        "👋 أهلاً! أنا بوت إدارة الموظفين\n\n"
        "📍 للمتابعة، شارك رقم هاتفك:",
        reply_markup=reply_markup
    )

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة مشاركة رقم الهاتف"""
    try:
        contact = update.message.contact
        user = update.message.from_user
        
        if contact and contact.user_id == user.id:
            conn = get_db_connection()
            if not conn:
                await update.message.reply_text("❌ خطأ في الاتصال")
                return
                
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO employees (telegram_id, phone_number, full_name, last_active)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (telegram_id) DO UPDATE SET 
                    phone_number = EXCLUDED.phone_number,
                    full_name = EXCLUDED.full_name,
                    last_active = CURRENT_TIMESTAMP
            """, (user.id, contact.phone_number, contact.first_name or "موظف"))
            
            conn.commit()
            cur.close()
            conn.close()
            
            await update.message.reply_text(
                f"✅ تم التحقق بنجاح!\n\n"
                f"👤 {contact.first_name}\n"
                f"📱 {contact.phone_number}\n\n"
                "🎉 يمكنك استخدام البوت الآن!",
                reply_markup=ReplyKeyboardRemove()
            )
    except Exception as e:
        print(f"❌ خطأ: {e}")
        await update.message.reply_text("❌ حدث خطأ")

async def check_in_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل الحضور"""
    try:
        user = update.message.from_user
        
        conn = get_db_connection()
        if not conn:
            await update.message.reply_text("❌ خطأ في الاتصال")
            return
            
        cur = conn.cursor()
        cur.execute("SELECT id FROM employees WHERE telegram_id = %s", (user.id,))
        employee = cur.fetchone()
        
        if not employee:
            await update.message.reply_text("❌ استخدم /start أولاً")
            cur.close()
            conn.close()
            return
        
        now = datetime.now(JORDAN_TZ)
        cur.execute("""
            INSERT INTO attendance (employee_id, date, check_in_time)
            VALUES (%s, %s, %s)
            ON CONFLICT (employee_id, date) DO NOTHING
        """, (employee[0], now.date(), now))
        
        conn.commit()
        cur.close()
        conn.close()
        
        await update.message.reply_text(
            f"✅ تم تسجيل الحضور!\n"
            f"⏰ {now.strftime('%H:%M:%S')}\n"
            f"📅 {now.strftime('%Y-%m-%d')}"
        )
        
    except Exception as e:
        print(f"❌ خطأ في الحضور: {e}")
        await update.message.reply_text("❌ حدث خطأ")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض المساعدة"""
    await update.message.reply_text(
        "📚 الأوامر:\n"
        "/start - البدء\n"
        "/check_in - الحضور\n"
        "/help - المساعدة"
    )

def main():
    """الدالة الرئيسية"""
    print("🚀 بدء تشغيل البوت...")
    
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN not found!")
        return
    
    # اختبار الاتصال
    conn = get_db_connection()
    if conn:
        conn.close()
        print("✅ الاتصال بقاعدة البيانات ناجح")
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("check_in", check_in_command))
        application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
        
        print("🤖 البوت يعمل الآن!")
        application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        print(f"❌ فشل: {e}")

if __name__ == '__main__':
    main()