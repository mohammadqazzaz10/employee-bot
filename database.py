import psycopg2
import pytz
from datetime import datetime
from config import DATABASE_URL, TIMEZONE

class Database:
    def __init__(self):
        self.conn = None
        self.connect()

    def connect(self):
        """الاتصال بقاعدة البيانات"""
        try:
            self.conn = psycopg2.connect(DATABASE_URL)
            self.create_tables()
        except Exception as e:
            print(f"خطأ في الاتصال بقاعدة البيانات: {e}")

    def create_tables(self):
        """إنشاء الجداول التلقائية"""
        commands = [
            # جدول الموظفين
            """
            CREATE TABLE IF NOT EXISTS employees (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                name VARCHAR(100) NOT NULL,
                phone_number VARCHAR(20),
                age INTEGER,
                job_title VARCHAR(100),
                department VARCHAR(100),
                hire_date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # جدول المديرين
            """
            CREATE TABLE IF NOT EXISTS admins (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                name VARCHAR(100),
                can_approve BOOLEAN DEFAULT TRUE,
                is_super_admin BOOLEAN DEFAULT FALSE,
                added_by BIGINT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # جدول الحضور
            """
            CREATE TABLE IF NOT EXISTS attendance (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id),
                check_in TIMESTAMP,
                check_out TIMESTAMP,
                late_minutes INTEGER DEFAULT 0,
                overtime_minutes INTEGER DEFAULT 0,
                work_hours DECIMAL(5,2) DEFAULT 0,
                date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # جدول طلبات الإجازة
            """
            CREATE TABLE IF NOT EXISTS requests (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id),
                request_type VARCHAR(20) NOT NULL,
                reason TEXT,
                start_date DATE,
                end_date DATE,
                status VARCHAR(20) DEFAULT 'pending',
                approved_by INTEGER REFERENCES admins(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # جدول فترات التدخين
            """
            CREATE TABLE IF NOT EXISTS cigarette_breaks (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id),
                break_start TIMESTAMP,
                break_end TIMESTAMP,
                duration_minutes INTEGER DEFAULT 10,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # جدول استراحات الغداء
            """
            CREATE TABLE IF NOT EXISTS lunch_breaks (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id),
                break_start TIMESTAMP,
                break_end TIMESTAMP,
                duration_minutes INTEGER DEFAULT 30,
                date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # جدول الإنذارات
            """
            CREATE TABLE IF NOT EXISTS warnings (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id),
                warning_type VARCHAR(50),
                reason TEXT,
                created_by INTEGER REFERENCES admins(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # جدول الغياب
            """
            CREATE TABLE IF NOT EXISTS absences (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id),
                absence_date DATE,
                reason TEXT,
                excused BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        ]
        
        try:
            cur = self.conn.cursor()
            for command in commands:
                cur.execute(command)
            self.conn.commit()
            cur.close()
        except Exception as e:
            print(f"خطأ في إنشاء الجداول: {e}")

    # === دوال الموظفين ===
    def add_employee(self, telegram_id, name, phone_number, age=None, job_title=None, department=None, hire_date=None):
        """إضافة موظف جديد"""
        try:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO employees (telegram_id, name, phone_number, age, job_title, department, hire_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (telegram_id, name, phone_number, age, job_title, department, hire_date))
            employee_id = cur.fetchone()[0]
            self.conn.commit()
            cur.close()
            return employee_id
        except Exception as e:
            print(f"خطأ في إضافة الموظف: {e}")
            return None

    def get_employee(self, telegram_id):
        """الحصول على بيانات الموظف"""
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM employees WHERE telegram_id = %s", (telegram_id,))
            employee = cur.fetchone()
            cur.close()
            return employee
        except Exception as e:
            print(f"خطأ في جلب بيانات الموظف: {e}")
            return None

    def get_employee_name(self, telegram_id):
        """الحصول على اسم الموظف"""
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT name FROM employees WHERE telegram_id = %s", (telegram_id,))
            result = cur.fetchone()
            cur.close()
            return result[0] if result else None
        except Exception as e:
            print(f"خطأ في جلب اسم الموظف: {e}")
            return None

    def list_employees(self):
        """قائمة جميع الموظفين"""
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM employees ORDER BY name")
            employees = cur.fetchall()
            cur.close()
            return employees
        except Exception as e:
            print(f"خطأ في جلب قائمة الموظفين: {e}")
            return []

    def update_employee(self, telegram_id, field, value):
        """تحديث بيانات الموظف"""
        try:
            cur = self.conn.cursor()
            cur.execute(f"UPDATE employees SET {field} = %s WHERE telegram_id = %s", (value, telegram_id))
            self.conn.commit()
            cur.close()
            return True
        except Exception as e:
            print(f"خطأ في تحديث بيانات الموظف: {e}")
            return False

    def remove_employee(self, telegram_id):
        """حذف موظف"""
        try:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM employees WHERE telegram_id = %s", (telegram_id,))
            self.conn.commit()
            cur.close()
            return True
        except Exception as e:
            print(f"خطأ في حذف الموظف: {e}")
            return False

    # === دوال المديرين ===
    def add_admin(self, telegram_id, name, can_approve=True, is_super_admin=False, added_by=None):
        """إضافة مدير"""
        try:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO admins (telegram_id, name, can_approve, is_super_admin, added_by)
                VALUES (%s, %s, %s, %s, %s)
            """, (telegram_id, name, can_approve, is_super_admin, added_by))
            self.conn.commit()
            cur.close()
            return True
        except Exception as e:
            print(f"خطأ في إضافة المدير: {e}")
            return False

    def is_admin(self, telegram_id):
        """التحقق إذا كان المستخدم مدير"""
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM admins WHERE telegram_id = %s", (telegram_id,))
            admin = cur.fetchone()
            cur.close()
            return admin is not None
        except Exception as e:
            print(f"خطأ في التحقق من المدير: {e}")
            return False

    def is_super_admin(self, telegram_id):
        """التحقق إذا كان المستخدم مدير رئيسي"""
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM admins WHERE telegram_id = %s AND is_super_admin = TRUE", (telegram_id,))
            admin = cur.fetchone()
            cur.close()
            return admin is not None
        except Exception as e:
            print(f"خطأ في التحقق من المدير الرئيسي: {e}")
            return False

    def can_approve_requests(self, telegram_id):
        """التحقق إذا كان المدير يمتلك صلاحية الموافقة"""
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT can_approve FROM admins WHERE telegram_id = %s", (telegram_id,))
            result = cur.fetchone()
            cur.close()
            return result[0] if result else False
        except Exception as e:
            print(f"خطأ في التحقق من صلاحية الموافقة: {e}")
            return False

    def list_admins(self):
        """قائمة جميع المديرين"""
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM admins ORDER BY is_super_admin DESC, name")
            admins = cur.fetchall()
            cur.close()
            return admins
        except Exception as e:
            print(f"خطأ في جلب قائمة المديرين: {e}")
            return []

    def remove_admin(self, telegram_id):
        """حذف مدير"""
        try:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM admins WHERE telegram_id = %s AND is_super_admin = FALSE", (telegram_id,))
            self.conn.commit()
            cur.close()
            return True
        except Exception as e:
            print(f"خطأ في حذف المدير: {e}")
            return False

    # === دوال الحضور ===
    def check_in(self, employee_id, check_in_time):
        """تسجيل الحضور"""
        try:
            cur = self.conn.cursor()
            today = check_in_time.date()
            
            # التحقق من عدم تسجيل حضور مسبق
            cur.execute("""
                SELECT id FROM attendance 
                WHERE employee_id = %s AND date = %s
            """, (employee_id, today))
            
            if cur.fetchone():
                cur.close()
                return False, "لقد سجلت الحضور مسبقاً اليوم"
            
            cur.execute("""
                INSERT INTO attendance (employee_id, check_in, date)
                VALUES (%s, %s, %s)
                RETURNING id
            """, (employee_id, check_in_time, today))
            
            self.conn.commit()
            cur.close()
            return True, "تم تسجيل الحضور بنجاح"
        except Exception as e:
            print(f"خطأ في تسجيل الحضور: {e}")
            return False, f"خطأ في تسجيل الحضور: {e}"

    def check_out(self, employee_id, check_out_time):
        """تسجيل الانصراف"""
        try:
            cur = self.conn.cursor()
            today = check_out_time.date()
            
            # الحصول على سجل الحضور اليوم
            cur.execute("""
                SELECT id, check_in FROM attendance 
                WHERE employee_id = %s AND date = %s AND check_out IS NULL
            """, (employee_id, today))
            
            record = cur.fetchone()
            if not record:
                cur.close()
                return False, "لم يتم تسجيل الحضور اليوم أو تم تسجيل الانصراف مسبقاً"
            
            attendance_id, check_in = record
            
            # حساب ساعات العمل
            work_seconds = (check_out_time - check_in).total_seconds()
            work_hours = work_seconds / 3600
            
            # حساب الدقائق المتأخرة والإضافية
            from datetime import datetime, time
            work_start = datetime.combine(today, time(8, 0))  # 8:00 ص
            work_end = datetime.combine(today, time(19, 0))   # 7:00 م
            
            late_minutes = max(0, (check_in - work_start).total_seconds() / 60 - 15)  # 15 دقيقة تسامح
            overtime_minutes = max(0, (check_out_time - work_end).total_seconds() / 60)
            
            cur.execute("""
                UPDATE attendance 
                SET check_out = %s, work_hours = %s, late_minutes = %s, overtime_minutes = %s
                WHERE id = %s
            """, (check_out_time, work_hours, late_minutes, overtime_minutes, attendance_id))
            
            self.conn.commit()
            cur.close()
            return True, "تم تسجيل الانصراف بنجاح"
        except Exception as e:
            print(f"خطأ في تسجيل الانصراف: {e}")
            return False, f"خطأ في تسجيل الانصراف: {e}"

    # === دوال التقارير ===
    def get_attendance_report(self, employee_id, days=7):
        """تقرير حضور الموظف"""
        try:
            cur = self.conn.cursor()
            cur.execute("""
                SELECT date, check_in, check_out, work_hours, late_minutes, overtime_minutes
                FROM attendance 
                WHERE employee_id = %s AND date >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY date DESC
            """, (employee_id, days))
            
            report = cur.fetchall()
            cur.close()
            return report
        except Exception as e:
            print(f"خطأ في جلب تقرير الحضور: {e}")
            return []

    def get_daily_report(self, date=None):
        """تقرير الحضور اليومي"""
        try:
            cur = self.conn.cursor()
            if date is None:
                date = datetime.now().date()
            
            cur.execute("""
                SELECT e.name, a.check_in, a.check_out, a.work_hours, a.late_minutes, a.overtime_minutes
                FROM attendance a
                JOIN employees e ON a.employee_id = e.id
                WHERE a.date = %s
                ORDER BY e.name
            """, (date,))
            
            report = cur.fetchall()
            cur.close()
            return report
        except Exception as e:
            print(f"خطأ في جلب التقرير اليومي: {e}")
            return []

    def get_weekly_report(self):
        """تقرير الحضور الأسبوعي"""
        try:
            cur = self.conn.cursor()
            cur.execute("""
                SELECT 
                    e.name,
                    COUNT(a.id) as days_worked,
                    AVG(a.work_hours) as avg_hours,
                    SUM(a.late_minutes) as total_late,
                    SUM(a.overtime_minutes) as total_overtime
                FROM employees e
                LEFT JOIN attendance a ON e.id = a.employee_id 
                    AND a.date >= CURRENT_DATE - INTERVAL '7 days'
                GROUP BY e.id, e.name
                ORDER BY e.name
            """)
            
            report = cur.fetchall()
            cur.close()
            return report
        except Exception as e:
            print(f"خطأ في جلب التقرير الأسبوعي: {e}")
            return []

    # === دوال طلبات التدخين ===
    def can_take_smoke_break(self, employee_id):
        """التحقق من إمكانية أخذ استراحة تدخين"""
        try:
            cur = self.conn.cursor()
            today = datetime.now().date()
            
            # تحديد الحد الأقصى للسجائر حسب اليوم
            is_friday = datetime.now().weekday() == 4  # 4 = الجمعة
            max_smokes = 3 if is_friday else 6
            
            # عدد السجائر اليوم
            cur.execute("""
                SELECT COUNT(*) FROM cigarette_breaks 
                WHERE employee_id = %s AND DATE(break_start) = %s
            """, (employee_id, today))
            
            today_smokes = cur.fetchone()[0]
            
            if today_smokes >= max_smokes:
                cur.close()
                return False, f"وصلت للحد الأقصى لاستراحات التدخين اليوم ({max_smokes})"
            
            # التحقق من الفترة بين السجائر
            cur.execute("""
                SELECT break_start FROM cigarette_breaks 
                WHERE employee_id = %s AND DATE(break_start) = %s
                ORDER BY break_start DESC 
                LIMIT 1
            """, (employee_id, today))
            
            last_smoke = cur.fetchone()
            if last_smoke:
                last_time = last_smoke[0]
                time_diff = (datetime.now(pytz.timezone(TIMEZONE)) - last_time).total_seconds()
                
                if time_diff < MIN_SMOKE_INTERVAL:
                    remaining = (MIN_SMOKE_INTERVAL - time_diff) / 60
                    cur.close()
                    return False, f"يجب الانتظار {remaining:.1f} دقائق قبل أخذ سيجارة أخرى"
            
            cur.close()
            return True, "يمكنك أخذ استراحة تدخين"
            
        except Exception as e:
            print(f"خطأ في التحقق من استراحة التدخين: {e}")
            return False, f"خطأ في النظام: {e}"

    def add_smoke_break(self, employee_id, break_start, break_end):
        """إضافة استراحة تدخين"""
        try:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO cigarette_breaks (employee_id, break_start, break_end)
                VALUES (%s, %s, %s)
            """, (employee_id, break_start, break_end))
            
            self.conn.commit()
            cur.close()
            return True
        except Exception as e:
            print(f"خطأ في إضافة استراحة التدخين: {e}")
            return False

    # === دوال طلبات الإجازة ===
    def add_request(self, employee_id, request_type, reason, start_date=None, end_date=None):
        """إضافة طلب إجازة"""
        try:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO requests (employee_id, request_type, reason, start_date, end_date)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (employee_id, request_type, reason, start_date, end_date))
            
            request_id = cur.fetchone()[0]
            self.conn.commit()
            cur.close()
            return request_id
        except Exception as e:
            print(f"خطأ في إضافة الطلب: {e}")
            return None

    def get_pending_requests(self):
        """الحصول على الطلبات المعلقة"""
        try:
            cur = self.conn.cursor()
            cur.execute("""
                SELECT r.*, e.name 
                FROM requests r
                JOIN employees e ON r.employee_id = e.id
                WHERE r.status = 'pending'
                ORDER BY r.created_at
            """)
            
            requests = cur.fetchall()
            cur.close()
            return requests
        except Exception as e:
            print(f"خطأ في جلب الطلبات المعلقة: {e}")
            return []

    def update_request_status(self, request_id, status, approved_by):
        """تحديث حالة الطلب"""
        try:
            cur = self.conn.cursor()
            cur.execute("""
                UPDATE requests 
                SET status = %s, approved_by = %s 
                WHERE id = %s
            """, (status, approved_by, request_id))
            
            self.conn.commit()
            cur.close()
            return True
        except Exception as e:
            print(f"خطأ في تحديث حالة الطلب: {e}")
            return False

    # === دوال إضافية ===
    def get_employee_by_id(self, employee_id):
        """الحصول على موظف بواسطة ID"""
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM employees WHERE id = %s", (employee_id,))
            employee = cur.fetchone()
            cur.close()
            return employee
        except Exception as e:
            print(f"خطأ في جلب بيانات الموظف: {e}")
            return None

    def get_today_attendance(self, employee_id):
        """الحصول على سجل الحضور اليوم"""
        try:
            cur = self.conn.cursor()
            today = datetime.now().date()
            cur.execute("""
                SELECT * FROM attendance 
                WHERE employee_id = %s AND date = %s
            """, (employee_id, today))
            attendance = cur.fetchone()
            cur.close()
            return attendance
        except Exception as e:
            print(f"خطأ في جلب الحضور اليوم: {e}")
            return None

    def get_employee_smokes_today(self, employee_id):
        """عدد سجائر اليوم للموظف"""
        try:
            cur = self.conn.cursor()
            today = datetime.now().date()
            cur.execute("""
                SELECT COUNT(*) FROM cigarette_breaks 
                WHERE employee_id = %s AND DATE(break_start) = %s
            """, (employee_id, today))
            count = cur.fetchone()[0]
            cur.close()
            return count
        except Exception as e:
            print(f"خطأ في جلب عدد السجائر: {e}")
            return 0

# إنشاء كائن قاعدة البيانات العالمي
db = Database()