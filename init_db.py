
---

## 🔧 خامساً: ملف **init_db.py**

```python
#!/usr/bin/env python3
"""
برنامج تهيئة قاعدة البيانات
Database Initialization Script
"""

import os
import psycopg2
import logging

# إعداد التسجيل
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات"""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("❌ DATABASE_URL not found in environment variables")
        raise ValueError("Please set DATABASE_URL environment variable")
    
    return psycopg2.connect(database_url)

def initialize_database():
    """تهيئة قاعدة البيانات وإنشاء الجداول"""
    try:
        # قراءة ملف schema.sql
        with open('schema.sql', 'r', encoding='utf-8') as f:
            schema_sql = f.read()
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # تنفيذ SQL
        cur.execute(schema_sql)
        
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info("✅ تم إنشاء قاعدة البيانات والجداول بنجاح!")
        print("🎉 تم تهيئة النظام بنجاح!")
        print("📊 الجداول التي تم إنشاؤها:")
        print("   - employees (الموظفين)")
        print("   - admins (المديرين)")
        print("   - attendance (الحضور)")
        print("   - lunch_breaks (استراحات الغداء)")
        print("   - cigarette_times (استراحات التدخين)")
        print("   - warnings (الإنذارات)")
        print("   - system_settings (الإعدادات)")
        
    except Exception as e:
        logger.error(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")
        print(f"❌ حدث خطأ: {e}")
        return False
    
    return True

if __name__ == '__main__':
    print("🚀 بدء تهيئة قاعدة البيانات...")
    print("📁 جاري إنشاء الجداول والإعدادات...")
    
    if initialize_database():
        print("\n✅ تم الانتهاء بنجاح!")
        print("🔧 يمكنك الآن تشغيل البوت باستخدام: python bot.py")
    else:
        print("\n❌ فشل في تهيئة قاعدة البيانات!")
        print("🔍 يرجى التحقق من إعدادات قاعدة البيانات")