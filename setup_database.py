import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import os
from config import DATABASE_URL

def create_database():
    """إنشاء قاعدة البيانات إذا لم تكن موجودة"""
    try:
        # الاتصال بقاعدة البيانات الافتراضية
        conn = psycopg2.connect(
            dbname='postgres',
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', 'password'),
            host=os.getenv('DB_HOST', 'localhost'),
            port=os.getenv('DB_PORT', '5432')
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        
        # اسم قاعدة البيانات من الرابط
        db_name = DATABASE_URL.split('/')[-1]
        
        # التحقق من وجود قاعدة البيانات
        cur.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s", (db_name,))
        exists = cur.fetchone()
        
        if not exists:
            print(f"📦 جاري إنشاء قاعدة البيانات: {db_name}")
            cur.execute(f'CREATE DATABASE "{db_name}"')
            print("✅ تم إنشاء قاعدة البيانات بنجاح")
        else:
            print(f"✅ قاعدة البيانات {db_name} موجودة مسبقاً")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"❌ خطأ في إنشاء قاعدة البيانات: {e}")

def setup_initial_data():
    """إعداد البيانات الأولية"""
    from database import db
    
    try:
        # إضافة المدراء الرئيسيين من الإعدادات
        from config import SUPER_ADMIN_IDS
        
        for admin_id in SUPER_ADMIN_IDS:
            if not db.is_admin(admin_id):
                db.add_admin(
                    admin_id, 
                    "المدير الرئيسي", 
                    can_approve=True, 
                    is_super_admin=True
                )
                print(f"✅ تم إضافة المدير الرئيسي: {admin_id}")
        
        print("🎉 تم إعداد البيانات الأولية بنجاح")
        
    except Exception as e:
        print(f"❌ خطأ في إعداد البيانات الأولية: {e}")

if __name__ == '__main__':
    print("🚀 بدء إعداد قاعدة البيانات...")
    create_database()
    
    # الانتظار قليلاً ثم إعداد البيانات
    import time
    time.sleep(2)
    
    setup_initial_data()
    print("✅ تم الانتهاء من الإعداد بنجاح!")