#!/usr/bin/env python3
"""
ملف التشغيل الرئيسي للبوت
"""

import logging
import sys
import time
from database import db

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

def check_database_connection():
    """التحقق من اتصال قاعدة البيانات"""
    try:
        # محاولة إجراء استعلام بسيط
        db.list_employees()
        logger.info("✅ الاتصال بقاعدة البيانات ناجح")
        return True
    except Exception as e:
        logger.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        return False

def main():
    """الدالة الرئيسية"""
    logger.info("🚀 بدء تشغيل بوت إدارة الحضور...")
    
    # التحقق من اتصال قاعدة البيانات
    if not check_database_connection():
        logger.error("❌ فشل في الاتصال بقاعدة البيانات. إيقاف التشغيل.")
        return
    
    # استيراد وتشغيل البوت
    from bot import EmployeeBot
    
    try:
        bot = EmployeeBot()
        logger.info("✅ تم تحميل البوت بنجاح")
        bot.run()
    except Exception as e:
        logger.error(f"❌ خطأ في تشغيل البوت: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()