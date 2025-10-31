#!/usr/bin/env python3
"""
برنامج تهيئة قاعدة البيانات - الإصدار المعدل
"""

import os
import psycopg2
import logging
import sys

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات"""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("❌ DATABASE_URL not found in environment variables")
        print("❌ DATABASE_URL not found in environment variables")
        return None
    
    try:
        return psycopg2.connect(database_url, sslmode='require')
    except Exception as e:
        logger.error(f"❌ Failed to connect to database: {e}")
        print(f"❌ Failed to connect to database: {e}")
        return None

def initialize_database():
    """تهيئة قاعدة البيانات وإنشاء الجداول"""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return False
        
        cur = conn.cursor()
        
        # إنشاء الجداول واحداً تلو الآخر
        tables = [
            """
            CREATE TABLE IF NOT EXISTS employees (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                phone_number VARCHAR(20) UNIQUE NOT NULL,
                full_name VARCHAR(100) NOT NULL,
                age INTEGER,
                job_title VARCHAR(100),
                department VARCHAR(100),
                hire_date DATE,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP WITH TIME ZONE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS admins (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                added_by BIGINT,
                added_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                is_super_admin BOOLEAN DEFAULT FALSE,
                can_approve BOOLEAN DEFAULT TRUE,
                can_view_only BOOLEAN DEFAULT FALSE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS attendance (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                check_in_time TIMESTAMP WITH TIME ZONE,
                check_out_time TIMESTAMP WITH TIME ZONE,
                is_late BOOLEAN DEFAULT FALSE,
                late_minutes INTEGER DEFAULT 0,
                late_reason TEXT,
                total_work_hours DECIMAL(4,2),
                overtime_hours DECIMAL(4,2) DEFAULT 0,
                status VARCHAR(20) DEFAULT 'present',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, date)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS lunch_breaks (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                taken BOOLEAN DEFAULT FALSE,
                taken_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, date)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS cigarette_times (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                taken_at TIMESTAMP WITH TIME ZONE NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS warnings (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                warning_type VARCHAR(50) NOT NULL,
                warning_reason TEXT NOT NULL,
                date DATE NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS absences (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                absence_type VARCHAR(50) NOT NULL,
                reason TEXT,
                excuse TEXT,
                is_excused BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, date)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS daily_cigarettes (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                count INTEGER DEFAULT 0,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, date)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS requests (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                request_type VARCHAR(50) NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                requested_at TIMESTAMP WITH TIME ZONE,
                responded_at TIMESTAMP WITH TIME ZONE,
                notes TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS system_settings (
                id SERIAL PRIMARY KEY,
                setting_key VARCHAR(100) UNIQUE NOT NULL,
                setting_value TEXT,
                description TEXT,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        ]
        
        # تنفيذ إنشاء الجداول
        for i, table_sql in enumerate(tables, 1):
            try:
                cur.execute(table_sql)
                print(f"✅ تم إنشاء الجدول {i}/10")
            except Exception as e:
                print(f"⚠️ خطأ في الجدول {i}: {e}")
                continue
        
        # إدخال الإعدادات الافتراضية
        settings_sql = """
        INSERT INTO system_settings (setting_key, setting_value, description) VALUES
        ('work_start_time', '08:00', 'وقت بدء العمل'),
        ('work_end_time', '17:00', 'وقت انتهاء العمل'),
        ('late_grace_minutes', '15', 'دقائق السماح للتأخير'),
        ('max_daily_smokes', '6', 'الحد الأقصى للسجائر اليومي'),
        ('max_daily_smokes_friday', '3', 'الحد الأقصى للسجائر يوم الجمعة'),
        ('lunch_break_duration', '30', 'مدة استراحة الغداء بالدقائق'),
        ('smoke_break_duration', '5', 'مدة استراحة التدخين بالدقائق'),
        ('min_smoke_gap_hours', '1.5', 'الفترة الزمنية بين السجائر'),
        ('reminder_check_in_time', '07:45', 'وقت تذكير الحضور'),
        ('reminder_check_out_time', '16:45', 'وقت تذكير الانصراف')
        ON CONFLICT (setting_key) DO NOTHING
        """
        
        cur.execute(settings_sql)
        
        # إدخال المديرين الرئيسيين
        admins_sql = """
        INSERT INTO admins (telegram_id, is_super_admin, can_approve, can_view_only) VALUES
        (1465191277, TRUE, TRUE, FALSE),
        (6798279805, TRUE, TRUE, FALSE)
        ON CONFLICT (telegram_id) DO NOTHING
        """
        
        cur.execute(admins_sql)
        
        conn.commit()
        cur.close()
        
        print("🎉 تم إنشاء قاعدة البيانات والجداول بنجاح!")
        print("📊 الجداول التي تم إنشاؤها:")
        print("   ✅ employees (الموظفين)")
        print("   ✅ admins (المديرين)") 
        print("   ✅ attendance (الحضور)")
        print("   ✅ lunch_breaks (استراحات الغداء)")
        print("   ✅ cigarette_times (استراحات التدخين)")
        print("   ✅ warnings (الإنذارات)")
        print("   ✅ system_settings (الإعدادات)")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")
        print(f"❌ حدث خطأ: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    print("🚀 بدء تهيئة قاعدة البيانات...")
    print("📁 جاري إنشاء الجداول والإعدادات...")
    
    if initialize_database():
        print("\n✅ تم الانتهاء بنجاح!")
        print("🔧 يمكنك الآن تشغيل البوت باستخدام: python bot.py")
        sys.exit(0)
    else:
        print("\n❌ فشل في تهيئة قاعدة البيانات!")
        print("🔍 يرجى التحقق من إعدادات قاعدة البيانات")
        sys.exit(1)