from employee_management_bot.bot import run_bot
from employee_management_bot.db import initialize_database_tables

# عند تشغيل هذا الملف، سيتم أولاً تهيئة الجداول ثم تشغيل البوت
if __name__ == '__main__':
    run_bot()

# ملاحظة: يمكنك استخدام هذا الملف لتشغيل خادم Flask (إذا كنت تخطط لذلك)
# بدلاً من run_bot() إذا كنت تنشر كخدمة ويب
