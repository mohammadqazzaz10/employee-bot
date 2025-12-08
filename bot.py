import os
import logging
import asyncio
import uuid
import time
from datetime import datetime, timedelta, date, time as dt_time
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes
from telegram.error import Conflict

# ===== إعدادات Logging =====
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== تعريف حالات المحادثة =====
LEAVE_REASON, VACATION_REASON, PENALTY_MENU, SELECT_PENALTY_TYPE, ENTER_PENALTY_DETAILS, \
SELECT_EMPLOYEE_FOR_PENALTY, CONFIRM_PENALTY, EDIT_PENALTY_AMOUNT, SELECT_PENALTY_TO_EDIT, \
EDIT_PENALTY_CUSTOM_AMOUNT = range(10)

# ===== إعدادات النظام =====
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_IDS = [1465191277]
JORDAN_TZ = ZoneInfo('Asia/Amman')

# ===== قائمة الهواتف المصرح بها =====
authorized_phones = [
    '+962786644106'
]

# ===== إعدادات الوقت =====
WORK_START_HOUR = 8
WORK_START_MINUTE = 0
WORK_REGULAR_HOURS = 9
WORK_REGULAR_MINUTES = WORK_REGULAR_HOURS * 60
WORK_OVERTIME_START_HOUR = 17

# ===== إعدادات السجائر =====
MAX_DAILY_SMOKES = 5
MIN_GAP_BETWEEN_SMOKES_HOURS = 1.5
SMOKE_BREAK_DURATION = 6
SMOKE_ALLOWED_AFTER_HOUR = 10
SMOKE_ALLOWED_AFTER_MINUTE = 0

# ===== إعدادات التأخير =====
class DelaySettings:
    def __init__(self):
        self.default_delay = 15  # 15 دقيقة افتراضياً
        self.current_delay = 15  # الوقت الحالي الذي يحدده المدير
        self.grace_period = 15   # فترة السماح للتأخير
        self.max_delay_minutes = 1440  # الحد الأقصى للتأخير (24 ساعة)
    
    def get_current_delay(self):
        """الحصول على وقت التأخير الحالي"""
        return self.current_delay
    
    def update_delay(self, new_delay, updated_by=None):
        """تحديث وقت التأخير"""
        if 1 <= new_delay <= self.max_delay_minutes:
            self.current_delay = new_delay
            logger.info(f"تم تحديث وقت التأخير إلى {new_delay} دقيقة")
            return True
        return False

delay_settings = DelaySettings()

# ===== إعدادات العقوبات =====
PENALTY_TYPES = {
    'late_15_30': {'name': 'تأخير 15-30 دقيقة', 'level': 1, 'default_amount': 0, 'default_ban_days': 0},
    'late_30_60': {'name': 'تأخير 30-60 دقيقة', 'level': 2, 'default_amount': 10, 'default_ban_days': 1},
    'late_over_60': {'name': 'تأخير أكثر من ساعة', 'level': 3, 'default_amount': 50, 'default_ban_days': 3},
    'no_check_in': {'name': 'عدم تسجيل حضور', 'level': 3, 'default_amount': 50, 'default_ban_days': 3},
    'no_check_out': {'name': 'عدم تسجيل انصراف', 'level': 2, 'default_amount': 10, 'default_ban_days': 1},
    'smoke_before_10': {'name': 'طلب سيجارة قبل 10 صباحاً', 'level': 1, 'default_amount': 0, 'default_ban_days': 0},
    'smoke_excess': {'name': 'تجاوز عدد السجائر المسموح', 'level': 2, 'default_amount': 10, 'default_ban_days': 1},
    'smoke_gap_violation': {'name': 'عدم احترام الفجوة بين السجائر', 'level': 1, 'default_amount': 0, 'default_ban_days': 0},
    'lunch_twice': {'name': 'طلب استراحة غداء مرتين', 'level': 1, 'default_amount': 0, 'default_ban_days': 0},
    'request_without_checkin': {'name': 'طلب بدون تسجيل حضور', 'level': 2, 'default_amount': 10, 'default_ban_days': 1},
    'early_checkout': {'name': 'انصراف مبكر', 'level': 2, 'default_amount': 20, 'default_ban_days': 2},
}

PENALTY_LEVELS = {
    1: {'name': 'إنذار شفهي', 'color': '🟡', 'description': 'تنبيه بسيط بدون خصم'},
    2: {'name': 'إنذار كتابي', 'color': '🟠', 'description': 'تنبيه رسمي مع خصم بسيط'},
    3: {'name': 'إنذار نهائي', 'color': '🔴', 'description': 'تنبيه شديد مع خصم متوسط'},
}

# ===== دوال المساعدة =====
def get_jordan_time():
    """الحصول على الوقت الحالي بتوقيت الأردن"""
    return datetime.now(JORDAN_TZ)

def minutes_to_hours_minutes(total_minutes):
    """تحويل الدقائق إلى ساعات ودقائق"""
    hours = int(total_minutes // 60)
    minutes = int(total_minutes % 60)
    return hours, minutes

def format_minutes_to_hours_minutes(total_minutes):
    """تنسيق الدقائق إلى نص (ساعات ودقائق)"""
    hours, minutes = minutes_to_hours_minutes(total_minutes)
    if hours > 0 and minutes > 0:
        return f"{hours} ساعة و {minutes} دقيقة"
    elif hours > 0:
        return f"{hours} ساعة"
    else:
        return f"{minutes} دقيقة"

def normalize_phone(phone_number):
    """تطبيع رقم الهاتف"""
    if not phone_number:
        return ""
    digits_only = ''.join(filter(str.isdigit, phone_number))
    while digits_only.startswith('00'):
        digits_only = digits_only[2:]
    return digits_only

def verify_employee(phone_number):
    """التحقق من صلاحية الموظف"""
    normalized_input = normalize_phone(phone_number)
    for auth_phone in authorized_phones:
        if normalize_phone(auth_phone) == normalized_input:
            return True
    return False

def can_request_smoke():
    """التحقق إذا كان الوقت مناسب لطلب السيجارة"""
    now = get_jordan_time()
    allowed_time = now.replace(hour=SMOKE_ALLOWED_AFTER_HOUR, minute=SMOKE_ALLOWED_AFTER_MINUTE, second=0, microsecond=0)
    return now >= allowed_time

# ===== نظام إدارة البيانات البسيط (بدون قاعدة بيانات) =====
class SimpleDatabase:
    def __init__(self):
        self.employees = {}  # {telegram_id: {id, phone, name, ...}}
        self.attendance = {}  # {employee_id_date: {check_in, check_out, ...}}
        self.penalties = []  # قائمة العقوبات
        self.smoke_counts = {}  # {employee_id_date: count}
        self.lunch_breaks = {}  # {employee_id_date: taken}
        self.cigarette_times = []  # {employee_id, taken_at}
        self.admins = ADMIN_IDS.copy()
        
    def get_employee_by_telegram_id(self, telegram_id):
        """الحصول على بيانات الموظف"""
        return self.employees.get(telegram_id)
    
    def save_employee(self, telegram_id, phone_number, full_name):
        """حفظ أو تحديث بيانات الموظف"""
        if telegram_id in self.employees:
            self.employees[telegram_id].update({
                'phone_number': phone_number,
                'full_name': full_name,
                'last_active': get_jordan_time()
            })
        else:
            employee_id = len(self.employees) + 1
            self.employees[telegram_id] = {
                'id': employee_id,
                'telegram_id': telegram_id,
                'phone_number': phone_number,
                'full_name': full_name,
                'last_active': get_jordan_time(),
                'created_at': get_jordan_time()
            }
        return self.employees[telegram_id]['id']
    
    def record_check_in(self, employee_id, telegram_id):
        """تسجيل حضور الموظف"""
        now = get_jordan_time()
        today = now.date()
        key = f"{employee_id}_{today}"
        
        # التحقق إذا تم تسجيل الحضور مسبقاً
        if key in self.attendance:
            return {
                'success': False,
                'error': 'already_checked_in',
                'check_in_time': self.attendance[key]['check_in_time']
            }
        
        # حساب وقت بدء العمل
        work_start = datetime.combine(today, dt_time(WORK_START_HOUR, WORK_START_MINUTE), tzinfo=JORDAN_TZ)
        
        # حساب التأخير بالدقائق
        late_minutes = max(0, int((now - work_start).total_seconds() / 60))
        is_late = late_minutes > delay_settings.grace_period
        
        # حفظ البيانات
        self.attendance[key] = {
            'employee_id': employee_id,
            'telegram_id': telegram_id,
            'date': today,
            'check_in_time': now,
            'check_out_time': None,
            'is_late': is_late,
            'late_minutes': late_minutes,
            'total_work_minutes': 0,
            'overtime_minutes': 0,
            'status': 'present'
        }
        
        # تسجيل العقوبة إذا كان التأخير كبيراً
        if is_late:
            penalty_type = None
            if 15 < late_minutes <= 30:
                penalty_type = 'late_15_30'
            elif 30 < late_minutes <= 60:
                penalty_type = 'late_30_60'
            elif late_minutes > 60:
                penalty_type = 'late_over_60'
            
            if penalty_type:
                self.add_penalty(employee_id, penalty_type, f'تأخير {late_minutes} دقيقة', telegram_id)
        
        logger.info(f"✅ تم تسجيل حضور الموظف {employee_id} في {now}")
        return {
            'success': True,
            'check_in_time': now,
            'is_late': is_late,
            'late_minutes': late_minutes
        }
    
    def record_check_out(self, employee_id, telegram_id):
        """تسجيل انصراف الموظف"""
        now = get_jordan_time()
        today = now.date()
        key = f"{employee_id}_{today}"
        
        if key not in self.attendance:
            return {'success': False, 'error': 'لم يتم تسجيل الحضور اليوم'}
        
        attendance = self.attendance[key]
        
        if attendance['check_out_time']:
            return {
                'success': False,
                'error': 'already_checked_out',
                'check_out_time': attendance['check_out_time']
            }
        
        # حساب وقت العمل
        check_in_time = attendance['check_in_time']
        work_minutes = max(0, int((now - check_in_time).total_seconds() / 60))
        
        # خصم 30 دقيقة لاستراحة الغداء إذا تجاوزت ساعة
        if work_minutes > 60:
            work_minutes -= 30
        
        # حساب الوقت الإضافي
        overtime_minutes = max(0, work_minutes - WORK_REGULAR_MINUTES)
        
        # تحديث البيانات
        attendance['check_out_time'] = now
        attendance['total_work_minutes'] = work_minutes
        attendance['overtime_minutes'] = overtime_minutes
        
        logger.info(f"✅ تم تسجيل انصراف الموظف {employee_id} في {now}")
        return {
            'success': True,
            'check_in_time': check_in_time,
            'check_out_time': now,
            'total_work_minutes': work_minutes,
            'overtime_minutes': overtime_minutes
        }
    
    def get_attendance_today(self, employee_id):
        """الحصول على سجل الحضور اليوم"""
        today = get_jordan_time().date()
        key = f"{employee_id}_{today}"
        return self.attendance.get(key)
    
    def is_employee_checked_in_today(self, employee_id):
        """التحقق إذا كان الموظف سجل حضوره اليوم"""
        attendance = self.get_attendance_today(employee_id)
        return attendance is not None and attendance['check_in_time'] is not None
    
    def add_penalty(self, employee_id, penalty_type, reason, created_by=None, amount=None, ban_days=None):
        """إضافة عقوبة جديدة"""
        if penalty_type not in PENALTY_TYPES:
            return {'success': False, 'error': 'نوع المخالفة غير موجود'}
        
        penalty_info = PENALTY_TYPES[penalty_type]
        
        # استخدام القيم المخصصة أو الافتراضية
        deduction = amount if amount is not None else penalty_info['default_amount']
        ban = ban_days if ban_days is not None else penalty_info['default_ban_days']
        
        penalty = {
            'id': len(self.penalties) + 1,
            'employee_id': employee_id,
            'penalty_type': penalty_type,
            'penalty_level': penalty_info['level'],
            'penalty_name': penalty_info['name'],
            'deduction_amount': deduction,
            'smoke_ban_days': ban,
            'reason': reason,
            'penalty_date': get_jordan_time().date(),
            'is_active': True,
            'created_by': created_by,
            'created_at': get_jordan_time()
        }
        
        self.penalties.append(penalty)
        logger.info(f"✅ تم إضافة عقوبة للموظف {employee_id}: {penalty_info['name']}")
        return {'success': True, 'penalty_id': penalty['id'], 'amount': deduction, 'ban_days': ban}
    
    def get_employee_penalties(self, employee_id, active_only=True):
        """الحصول على عقوبات الموظف"""
        if active_only:
            return [p for p in self.penalties if p['employee_id'] == employee_id and p['is_active']]
        return [p for p in self.penalties if p['employee_id'] == employee_id]
    
    def is_employee_banned_from_smoking(self, employee_id):
        """التحقق إذا كان الموظف محروم من السجائر"""
        today = get_jordan_time().date()
        for penalty in self.penalties:
            if (penalty['employee_id'] == employee_id and 
                penalty['is_active'] and 
                penalty['smoke_ban_days'] > 0):
                penalty_date = penalty['penalty_date']
                ban_end_date = penalty_date + timedelta(days=penalty['smoke_ban_days'])
                if today <= ban_end_date:
                    return True
        return False
    
    def get_smoke_count_today(self, employee_id):
        """الحصول على عدد السجائر اليومية"""
        today = get_jordan_time().date()
        key = f"{employee_id}_{today}"
        return self.smoke_counts.get(key, 0)
    
    def increment_smoke_count(self, employee_id):
        """زيادة عدد السجائر اليومية"""
        today = get_jordan_time().date()
        key = f"{employee_id}_{today}"
        current = self.smoke_counts.get(key, 0)
        self.smoke_counts[key] = current + 1
        
        # تسجيل وقت السيجارة
        self.cigarette_times.append({
            'employee_id': employee_id,
            'taken_at': get_jordan_time()
        })
        
        return self.smoke_counts[key]
    
    def get_last_cigarette_time(self, employee_id):
        """الحصول على وقت آخر سيجارة للموظف"""
        for record in reversed(self.cigarette_times):
            if record['employee_id'] == employee_id:
                return record['taken_at']
        return None
    
    def has_taken_lunch_break_today(self, employee_id):
        """التحقق من أن الموظف قد أخذ بريك غداء اليوم"""
        today = get_jordan_time().date()
        key = f"{employee_id}_{today}"
        return self.lunch_breaks.get(key, False)
    
    def mark_lunch_break_taken(self, employee_id):
        """تسجيل أن الموظف قد أخذ بريك غداء اليوم"""
        today = get_jordan_time().date()
        key = f"{employee_id}_{today}"
        self.lunch_breaks[key] = True
        return True
    
    def get_all_employees(self):
        """الحصول على جميع الموظفين"""
        return list(self.employees.values())
    
    def get_employee_by_id(self, employee_id):
        """الحصول على بيانات الموظف بالمعرف"""
        for emp in self.employees.values():
            if emp['id'] == employee_id:
                return emp
        return None
    
    def is_admin(self, user_id):
        """التحقق من أن المستخدم مدير"""
        return user_id in self.admins
    
    def add_admin(self, telegram_id):
        """إضافة مدير"""
        if telegram_id not in self.admins:
            self.admins.append(telegram_id)
            return True
        return False
    
    def get_employee_attendance_report(self, employee_id, days=7):
        """الحصول على تقرير حضور الموظف"""
        reports = []
        today = get_jordan_time().date()
        
        for i in range(days):
            report_date = today - timedelta(days=i)
            key = f"{employee_id}_{report_date}"
            
            if key in self.attendance:
                attendance = self.attendance[key]
                reports.append({
                    'date': report_date,
                    'check_in_time': attendance['check_in_time'],
                    'check_out_time': attendance['check_out_time'],
                    'is_late': attendance['is_late'],
                    'late_minutes': attendance['late_minutes'],
                    'total_work_minutes': attendance['total_work_minutes'],
                    'overtime_minutes': attendance['overtime_minutes'],
                    'status': attendance['status']
                })
        
        return reports

# إنشاء قاعدة البيانات
db = SimpleDatabase()

# ===== إعدادات لوحة المفاتيح =====
def get_main_keyboard(user_id):
    """الحصول على لوحة المفاتيح الرئيسية"""
    if db.is_admin(user_id):
        keyboard = [
            [KeyboardButton("تسجيل حضور 📝"), KeyboardButton("تسجيل انصراف 🚪")],
            [KeyboardButton("طلب سيجارة 🚬"), KeyboardButton("طلب استراحة ☕")],
            [KeyboardButton("طلب إذن خروج 🏠"), KeyboardButton("طلب إجازة 🌴")],
            [KeyboardButton("تقرير الحضور 📊"), KeyboardButton("تقريري الكامل 📈")],
            [KeyboardButton("🔧 مدير العقوبات"), KeyboardButton("⏱️ إعدادات التأخير")]
        ]
    else:
        keyboard = [
            [KeyboardButton("تسجيل حضور 📝"), KeyboardButton("تسجيل انصراف 🚪")],
            [KeyboardButton("طلب سيجارة 🚬"), KeyboardButton("طلب استراحة ☕")],
            [KeyboardButton("طلب إذن خروج 🏠"), KeyboardButton("طلب إجازة 🌴")],
            [KeyboardButton("تقرير الحضور 📊"), KeyboardButton("تقريري الكامل 📈")]
        ]
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ===== الدوال الرئيسية للبوت =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء المحادثة مع البوت"""
    user = update.message.from_user
    logger.info(f"المستخدم {user.id} بدأ المحادثة.")
    
    # التحقق إذا كان المستخدم مسجلاً مسبقاً
    employee = db.get_employee_by_telegram_id(user.id)
    
    if employee:
        employee_name = employee.get('full_name', user.first_name)
        reply_markup = get_main_keyboard(user.id)
        
        await update.message.reply_text(
            f"👋 أهلاً بعودتك {employee_name}!\n\n"
            "اختر من الخيارات أدناه:",
            reply_markup=reply_markup
        )
        return
    
    # المستخدم جديد - طلب معلومات الاتصال
    contact_button = KeyboardButton("📱 مشاركة رقم الهاتف", request_contact=True)
    reply_markup = ReplyKeyboardMarkup([[contact_button]], resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "👋 مرحباً بك في نظام إدارة حضور الموظفين!\n\n"
        "📱 للمتابعة، يرجى مشاركة رقم هاتفك للتحقق من هويتك:\n\n"
        "اضغط على الزر أدناه لمشاركة رقم هاتفك.",
        reply_markup=reply_markup
    )

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إرسال معلومات الاتصال"""
    user = update.message.from_user
    contact = update.message.contact
    
    if not contact:
        await update.message.reply_text("❌ لم يتم إرسال معلومات الاتصال.")
        return
    
    phone_number = contact.phone_number
    
    # التحقق من أن رقم الهاتف مصرح به
    if not verify_employee(phone_number):
        await update.message.reply_text(
            "❌ رقم الهاتف هذا غير مسجل في النظام.\n"
            "يرجى التواصل مع الإدارة لإضافة رقمك."
        )
        return
    
    # حفظ بيانات الموظف
    full_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip()
    if not full_name:
        full_name = user.first_name
        if user.last_name:
            full_name = f"{user.first_name} {user.last_name}"
    
    employee_id = db.save_employee(user.id, phone_number, full_name)
    
    if employee_id:
        reply_markup = get_main_keyboard(user.id)
        
        await update.message.reply_text(
            f"✅ مرحباً بك {full_name}!\n\n"
            "تم التحقق من هويتك بنجاح.\n"
            "يمكنك الآن استخدام البوت لإدارة حضورك.",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "❌ حدث خطأ في حفظ بياناتك.\n"
            "يرجى المحاولة مرة أخرى أو التواصل مع الإدارة."
        )

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل النصية (الأزرار)"""
    user = update.message.from_user
    text = update.message.text
    
    logger.info(f"📩 المستخدم {user.id} ({user.first_name}) أرسل نصاً: {text}")
    
    # التعرف على النص وتوجيهه للدالة المناسبة
    if text == "تسجيل حضور 📝":
        await check_in_command(update, context)
    elif text == "تسجيل انصراف 🚪":
        await check_out_command(update, context)
    elif text == "طلب سيجارة 🚬":
        await smoke_request(update, context)
    elif text == "طلب استراحة ☕":
        await break_request(update, context)
    elif text == "طلب إذن خروج 🏠":
        await leave_request(update, context)
    elif text == "طلب إجازة 🌴":
        await vacation_request(update, context)
    elif text == "تقرير الحضور 📊":
        await attendance_report_command(update, context)
    elif text == "تقريري الكامل 📈":
        await full_report_command(update, context)
    elif text == "🔧 مدير العقوبات":
        await penalty_manager_command(update, context)
    elif text == "⏱️ إعدادات التأخير":
        await delay_settings_command(update, context)
    else:
        await update.message.reply_text(
            "❌ لم أفهم طلبك.\n"
            "يرجى استخدام الأزرار أدناه أو الأوامر المباشرة.",
            reply_markup=get_main_keyboard(user.id)
        )

async def check_in_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل حضور الموظف"""
    user = update.message.from_user
    employee = db.get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً.",
            reply_markup=get_main_keyboard(user.id)
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', user.first_name)
    
    result = db.record_check_in(employee_id, user.id)
    
    if not result['success']:
        if result.get('error') == 'already_checked_in':
            check_in_time = result['check_in_time']
            await update.message.reply_text(
                f"⚠️ لقد سجلت حضورك مسبقاً اليوم!\n\n"
                f"⏰ وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
                f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}"
            )
        else:
            await update.message.reply_text(f"❌ خطأ في تسجيل الحضور: {result.get('error', 'خطأ غير معروف')}")
        return
    
    check_in_time = result['check_in_time']
    is_late = result['is_late']
    late_minutes = result['late_minutes']
    
    if is_late:
        message = (
            f"⚠️ تم تسجيل حضورك مع تأخير!\n\n"
            f"👤 الموظف: {employee_name}\n"
            f"⏰ وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
            f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}\n"
            f"⏱ التأخير: {late_minutes} دقيقة\n\n"
            f"🚨 تم تسجيل عقوبة بسبب التأخير بعد الـ{delay_settings.grace_period} دقيقة المسموحة!"
        )
    else:
        if late_minutes > 0:
            message = (
                f"✅ تم تسجيل حضورك بنجاح!\n\n"
                f"👤 الموظف: {employee_name}\n"
                f"⏰ وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
                f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}\n"
                f"⏱ التأخير: {late_minutes} دقيقة (ضمن الوقت المسموح)\n\n"
                f"💼 يوم عمل موفق!"
            )
        else:
            message = (
                f"✅ تم تسجيل حضورك بنجاح!\n\n"
                f"👤 الموظف: {employee_name}\n"
                f"⏰ وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
                f"📅 التاريخ: {check_in_time.strftime('%Y-%m-%d')}\n"
                f"🎯 في الوقت المحدد!\n\n"
                f"💼 يوم عمل موفق!"
            )
    
    await update.message.reply_text(message)

async def check_out_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل انصراف الموظف"""
    user = update.message.from_user
    employee = db.get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً.",
            reply_markup=get_main_keyboard(user.id)
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', user.first_name)
    
    result = db.record_check_out(employee_id, user.id)
    
    if not result['success']:
        if result.get('error') == 'already_checked_out':
            check_out_time = result['check_out_time']
            total_minutes = result.get('total_work_minutes', 0)
            work_hours = total_minutes / 60
            await update.message.reply_text(
                f"⚠️ لقد سجلت انصرافك مسبقاً اليوم!\n\n"
                f"🕐 وقت الانصراف: {check_out_time.strftime('%H:%M:%S')}\n"
                f"⏱ ساعات العمل: {work_hours:.2f} ساعة\n"
                f"📅 التاريخ: {check_out_time.strftime('%Y-%m-%d')}"
            )
        else:
            await update.message.reply_text(f"❌ {result.get('error', 'خطأ في تسجيل الانصراف')}")
        return
    
    check_in_time = result['check_in_time']
    check_out_time = result['check_out_time']
    total_minutes = result['total_work_minutes']
    overtime_minutes = result['overtime_minutes']
    
    message = (
        f"✅ تم تسجيل انصرافك بنجاح!\n\n"
        f"👤 الموظف: {employee_name}\n"
        f"🕐 وقت الحضور: {check_in_time.strftime('%H:%M:%S')}\n"
        f"🕐 وقت الانصراف: {check_out_time.strftime('%H:%M:%S')}\n"
        f"📅 التاريخ: {check_out_time.strftime('%Y-%m-%d')}\n\n"
    )
    
    work_hours, work_minutes = minutes_to_hours_minutes(total_minutes)
    
    if work_hours > 0 and work_minutes > 0:
        message += f"⏱ وقت العمل: {work_hours} ساعة و {work_minutes} دقيقة\n"
    elif work_hours > 0:
        message += f"⏱ وقت العمل: {work_hours} ساعة\n"
    else:
        message += f"⏱ وقت العمل: {work_minutes} دقيقة\n"
    
    if overtime_minutes > 0:
        overtime_hours, overtime_mins = minutes_to_hours_minutes(overtime_minutes)
        if overtime_hours > 0 and overtime_mins > 0:
            message += f"⭐ وقت إضافي: {overtime_hours} ساعة و {overtime_mins} دقيقة\n\n"
        elif overtime_hours > 0:
            message += f"⭐ وقت إضافي: {overtime_hours} ساعة\n\n"
        else:
            message += f"⭐ وقت إضافي: {overtime_mins} دقيقة\n\n"
        message += "🎉 شكراً على العمل الإضافي!"
    else:
        if total_minutes < WORK_REGULAR_MINUTES:
            shortfall_minutes = WORK_REGULAR_MINUTES - total_minutes
            shortfall_hours, shortfall_mins = minutes_to_hours_minutes(shortfall_minutes)
            if shortfall_hours > 0 and shortfall_mins > 0:
                message += f"\n⚠️ ملاحظة: نقص في وقت العمل بمقدار {shortfall_hours} ساعة و {shortfall_mins} دقيقة"
            elif shortfall_hours > 0:
                message += f"\n⚠️ ملاحظة: نقص في وقت العمل بمقدار {shortfall_hours} ساعة"
            else:
                message += f"\n⚠️ ملاحظة: نقص في وقت العمل بمقدار {shortfall_mins} دقيقة"
        else:
            message += "\n💼 شكراً لك! نراك غداً بإذن الله"
    
    await update.message.reply_text(message)

async def smoke_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب سيجارة"""
    user = update.message.from_user
    employee = db.get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً.",
            reply_markup=get_main_keyboard(user.id)
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', user.first_name)
    
    # التحقق إذا كان الموظف قد سجل حضوره اليوم
    if not db.is_employee_checked_in_today(employee_id):
        db.add_penalty(employee_id, 'request_without_checkin', 'طلب سيجارة بدون تسجيل حضور', user.id)
        await update.message.reply_text(
            f"❌ {employee_name}، لم تسجل حضورك اليوم!\n\n"
            f"⚠️ تم تسجيل مخالفة: طلب بدون تسجيل حضور\n"
            f"🚫 لن تتم الموافقة على طلبك حتى تسجل الحضور."
        )
        return
    
    # التحقق من حظر السجائر
    if db.is_employee_banned_from_smoking(employee_id):
        await update.message.reply_text(
            f"🚫 {employee_name}، أنت محروم من طلب السجائر حالياً.\n\n"
            f"⚠️ لديك عقوبة سارية تمنعك من طلب السجائر.\n"
            f"📋 يمكنك مراجعة عقوباتك باستخدام /my_penalties"
        )
        return
    
    # التحقق من الوقت (بعد الساعة 10 صباحاً)
    if not can_request_smoke():
        db.add_penalty(employee_id, 'smoke_before_10', 'طلب سيجارة قبل الساعة 10 صباحاً', user.id)
        await update.message.reply_text(
            f"❌ {employee_name}، الوقت غير مناسب لطلب السيجارة!\n\n"
            f"🚬 السجائر مسموحة بعد الساعة {SMOKE_ALLOWED_AFTER_HOUR}:00 صباحاً.\n"
            f"⚠️ تم تسجيل مخالفة: طلب سيجارة قبل الوقت المسموح"
        )
        return
    
    # التحقق من عدد السجائر اليومية
    smoke_count = db.get_smoke_count_today(employee_id)
    if smoke_count >= MAX_DAILY_SMOKES:
        db.add_penalty(employee_id, 'smoke_excess', f'تجاوز عدد السجائر المسموح ({MAX_DAILY_SMOKES})', user.id)
        await update.message.reply_text(
            f"❌ {employee_name}، لقد استهلكت جميع السجائر المسموحة اليوم!\n\n"
            f"🚬 الحد الأقصى: {MAX_DAILY_SMOKES} سجائر/يوم\n"
            f"📊 عدد سجائرك اليوم: {smoke_count}\n"
            f"⚠️ تم تسجيل مخالفة: تجاوز عدد السجائر المسموح"
        )
        return
    
    # التحقق من الفجوة الزمنية بين السجائر
    last_cigarette = db.get_last_cigarette_time(employee_id)
    if last_cigarette:
        time_since_last = (get_jordan_time() - last_cigarette).total_seconds() / 3600  # بالساعات
        if time_since_last < MIN_GAP_BETWEEN_SMOKES_HOURS:
            db.add_penalty(employee_id, 'smoke_gap_violation', 
                         f'عدم احترام الفجوة بين السجائر ({MIN_GAP_BETWEEN_SMOKES_HOURS} ساعة)', user.id)
            await update.message.reply_text(
                f"❌ {employee_name}، لم يمر وقت كافٍ منذ آخر سيجارة!\n\n"
                f"⏰ يجب الانتظار {MIN_GAP_BETWEEN_SMOKES_HOURS} ساعة بين السجائر.\n"
                f"⏱️ الوقت المتبقي: {MIN_GAP_BETWEEN_SMOKES_HOURS - time_since_last:.1f} ساعة\n"
                f"⚠️ تم تسجيل مخالفة: عدم احترام الفجوة بين السجائر"
            )
            return
    
    # زيادة عداد السجائر
    new_count = db.increment_smoke_count(employee_id)
    
    await update.message.reply_text(
        f"✅ تمت الموافقة على طلب السيجارة!\n\n"
        f"👤 الموظف: {employee_name}\n"
        f"🚬 سجائر اليوم: {new_count}/{MAX_DAILY_SMOKES}\n"
        f"⏰ مدة السيجارة: {SMOKE_BREAK_DURATION} دقيقة\n\n"
        f"⏱️ سيتم إشعارك بانتهاء الوقت تلقائياً."
    )

async def break_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب استراحة غداء"""
    user = update.message.from_user
    employee = db.get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً.",
            reply_markup=get_main_keyboard(user.id)
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', user.first_name)
    
    # التحقق إذا كان الموظف قد سجل حضوره اليوم
    if not db.is_employee_checked_in_today(employee_id):
        await update.message.reply_text(
            f"❌ {employee_name}، لم تسجل حضورك اليوم!\n\n"
            f"🚫 لن تتم الموافقة على طلبك حتى تسجل الحضور."
        )
        return
    
    # التحقق إذا أخذ استراحة غداء من قبل
    if db.has_taken_lunch_break_today(employee_id):
        db.add_penalty(employee_id, 'lunch_twice', 'طلب استراحة غداء مرتين', user.id)
        await update.message.reply_text(
            f"❌ {employee_name}، لقد أخذت استراحة الغداء مسبقاً!\n\n"
            f"⚠️ تم تسجيل مخالفة: طلب استراحة غداء مرتين"
        )
        return
    
    # تسجيل استراحة الغداء
    db.mark_lunch_break_taken(employee_id)
    
    await update.message.reply_text(
        f"✅ تمت الموافقة على استراحة الغداء!\n\n"
        f"👤 الموظف: {employee_name}\n"
        f"⏰ المدة: 30 دقيقة\n"
        f"🍽️ استمتع بوجبتك!"
    )

async def leave_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب إذن خروج"""
    user = update.message.from_user
    employee = db.get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً.",
            reply_markup=get_main_keyboard(user.id)
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', user.first_name)
    
    # التحقق إذا كان الموظف قد سجل حضوره اليوم
    if not db.is_employee_checked_in_today(employee_id):
        await update.message.reply_text(
            f"❌ {employee_name}، لم تسجل حضورك اليوم!\n\n"
            f"🚫 لن تتم الموافقة على طلبك حتى تسجل الحضور."
        )
        return
    
    await update.message.reply_text(
        f"📝 طلب إذن خروج\n\n"
        f"👤 الموظف: {employee_name}\n\n"
        f"يرجى كتابة سبب الخروج:\n"
        f"(مثال: زيارة طبيب، أمر عائلي، ...)"
    )
    
    return LEAVE_REASON

async def vacation_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب إجازة"""
    user = update.message.from_user
    employee = db.get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً.",
            reply_markup=get_main_keyboard(user.id)
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', user.first_name)
    
    await update.message.reply_text(
        f"🌴 طلب إجازة\n\n"
        f"👤 الموظف: {employee_name}\n\n"
        f"يرجى كتابة سبب طلب الإجازة:\n"
        f"(مثال: إجازة سنوية، ظروف عائلية، ...)"
    )
    
    return VACATION_REASON

async def receive_leave_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال سبب الخروج"""
    user = update.message.from_user
    reason = update.message.text
    
    employee = db.get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text("❌ خطأ: لم يتم العثور على بيانات الموظف")
        return ConversationHandler.END
    
    employee_name = employee.get('full_name', user.first_name)
    
    keyboard = [
        [
            InlineKeyboardButton("✅ الموافقة", callback_data=f"approve_leave_{user.id}"),
            InlineKeyboardButton("❌ الرفض", callback_data=f"reject_leave_{user.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # إرسال للجميع (بدلاً من المديرين فقط للاختبار)
    await update.message.reply_text(
        f"✅ تم إرسال طلبك!\n\n"
        f"📝 السبب: {reason}\n\n"
        f"⏳ سيتم إشعارك بقرار الإدارة قريباً."
    )
    
    return ConversationHandler.END

async def receive_vacation_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال سبب الإجازة"""
    user = update.message.from_user
    reason = update.message.text
    
    employee = db.get_employee_by_telegram_id(user.id)
    if not employee:
        await update.message.reply_text("❌ خطأ: لم يتم العثور على بيانات الموظف")
        return ConversationHandler.END
    
    employee_name = employee.get('full_name', user.first_name)
    
    await update.message.reply_text(
        f"✅ تم إرسال طلبك!\n\n"
        f"📝 السبب: {reason}\n\n"
        f"⏳ سيتم إشعارك بقرار الإدارة قريباً."
    )
    
    return ConversationHandler.END

async def attendance_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تقرير حضور الموظف"""
    user = update.message.from_user
    employee = db.get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً.",
            reply_markup=get_main_keyboard(user.id)
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', user.first_name)
    
    records = db.get_employee_attendance_report(employee_id, days=7)
    
    if not records:
        await update.message.reply_text(
            f"📊 تقرير الحضور - {employee_name}\n\n"
            "⚠️ لا توجد سجلات حضور للأيام السبعة الماضية."
        )
        return
    
    message = (
        f"📊 تقرير الحضور - {employee_name}\n"
        f"📅 آخر 7 أيام\n\n"
    )
    
    total_days = 0
    total_minutes = 0
    total_overtime_minutes = 0
    late_days = 0
    
    for record in records:
        date = record['date']
        check_in = record['check_in_time']
        check_out = record['check_out_time']
        is_late = record['is_late']
        work_minutes = record['total_work_minutes']
        overtime = record['overtime_minutes']
        
        message += f"━━━━━━━━━━━━━━━━━\n"
        message += f"📅 {date.strftime('%Y-%m-%d')}\n"
        
        if check_in:
            message += f"🕐 حضور: {check_in.strftime('%H:%M')}"
            if is_late:
                late_days += 1
                message += f" ⚠️ متأخر"
            message += "\n"
        else:
            message += "❌ لم يتم تسجيل الحضور\n"
        
        if check_out:
            message += f"🕐 انصراف: {check_out.strftime('%H:%M')}\n"
            message += f"⏱ وقت العمل: {format_minutes_to_hours_minutes(work_minutes)}\n"
            if overtime > 0:
                message += f"⭐ إضافي: {format_minutes_to_hours_minutes(overtime)}\n"
            total_days += 1
            total_minutes += work_minutes
            total_overtime_minutes += overtime
        
        message += "\n"
    
    message += (
        f"━━━━━━━━━━━━━━━━━\n"
        f"📈 الإحصائيات:\n"
        f"📅 أيام العمل: {total_days}\n"
        f"⏱ إجمالي وقت العمل: {format_minutes_to_hours_minutes(total_minutes)}\n"
    )
    
    if total_overtime_minutes > 0:
        message += f"⭐ إجمالي الإضافي: {format_minutes_to_hours_minutes(total_overtime_minutes)}\n"
    
    if late_days > 0:
        message += f"⚠️ أيام التأخير: {late_days}\n"
    
    if total_days > 0:
        avg_minutes = total_minutes / total_days
        message += f"📊 متوسط وقت اليوم: {format_minutes_to_hours_minutes(avg_minutes)}\n"
    
    await update.message.reply_text(message)

async def full_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تقرير كامل للموظف"""
    user = update.message.from_user
    employee = db.get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً.",
            reply_markup=get_main_keyboard(user.id)
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', user.first_name)
    
    # الحصول على جميع البيانات
    attendance_records = db.get_employee_attendance_report(employee_id, days=30)
    penalties = db.get_employee_penalties(employee_id, active_only=False)
    
    # حساب الإحصائيات
    total_days = len(attendance_records)
    present_days = sum(1 for r in attendance_records if r['check_in_time'])
    late_days = sum(1 for r in attendance_records if r['is_late'])
    total_minutes = sum(r['total_work_minutes'] for r in attendance_records)
    total_overtime_minutes = sum(r['overtime_minutes'] for r in attendance_records)
    
    # حساب السجائر
    smoke_count = db.get_smoke_count_today(employee_id)
    
    message = (
        f"📊 التقرير الكامل - {employee_name}\n"
        f"📅 شهر: {get_jordan_time().strftime('%Y-%m')}\n"
        f"⏰ تاريخ التقرير: {get_jordan_time().strftime('%Y-%m-%d')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    # قسم الحضور والانصراف
    message += "🔹 الحضور والانصراف:\n"
    message += f"   📅 أيام العمل: {total_days} يوم\n"
    message += f"   ✅ أيام الحضور: {present_days} يوم\n"
    message += f"   ⏰ أيام التأخير: {late_days} يوم\n"
    message += f"   ⏱ إجمالي وقت العمل: {format_minutes_to_hours_minutes(total_minutes)}\n"
    message += f"   ⭐ وقت إضافي: {format_minutes_to_hours_minutes(total_overtime_minutes)}\n\n"
    
    # قسم السجائر
    message += "🔹 السجائر:\n"
    message += f"   🚬 سجائر اليوم: {smoke_count}/{MAX_DAILY_SMOKES}\n"
    message += f"   ⚠️ الحالة: {'🚫 محروم' if db.is_employee_banned_from_smoking(employee_id) else '✅ مسموح'}\n\n"
    
    # قسم العقوبات
    message += "🔹 العقوبات:\n"
    active_penalties = [p for p in penalties if p['is_active']]
    message += f"   ⚖️ عدد العقوبات النشطة: {len(active_penalties)}\n"
    
    if active_penalties:
        total_deduction = sum(p['deduction_amount'] for p in active_penalties)
        message += f"   💰 إجمالي الخصومات: {total_deduction:.2f} دينار\n"
        message += "   📋 آخر العقوبات:\n"
        for penalty in active_penalties[:3]:  # عرض أول 3 عقوبات
            message += f"      • {penalty['penalty_name']} - {penalty['deduction_amount']} دينار\n"
    
    message += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    message += "📈 التقييم العام: "
    
    # حساب النقاط
    score = 100
    
    # خصم نقاط التأخير
    if total_days > 0:
        late_percentage = (late_days / total_days) * 100
        if late_percentage > 20:
            score -= 30
        elif late_percentage > 10:
            score -= 15
        elif late_percentage > 5:
            score -= 5
    
    # خصم نقاط العقوبات
    score -= len(active_penalties) * 5
    
    # خصم نقاط حظر السجائر
    if db.is_employee_banned_from_smoking(employee_id):
        score -= 20
    
    # تحديد التقييم
    if score >= 90:
        message += "⭐ ممتاز ⭐"
    elif score >= 80:
        message += "👍 جيد جداً"
    elif score >= 70:
        message += "✅ جيد"
    elif score >= 60:
        message += "⚠️ مقبول"
    else:
        message += "❌ يحتاج تحسين"
    
    message += f" ({score}/100)\n"
    
    await update.message.reply_text(message)

async def delay_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إعدادات وقت التأخير"""
    user = update.message.from_user
    
    if not db.is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    current_delay = delay_settings.get_current_delay()
    
    keyboard = [
        [InlineKeyboardButton("⏱️ تعديل وقت التأخير", callback_data="edit_delay")],
        [InlineKeyboardButton("📋 عرض الإعدادات الحالية", callback_data="view_delay")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"⏱️ **إعدادات وقت التأخير**\n\n"
        f"الوقت الحالي: {current_delay} دقيقة\n"
        f"فترة السماح: {delay_settings.grace_period} دقيقة\n\n"
        f"اختر الإجراء الذي تريد تنفيذه:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    return PENALTY_MENU

async def penalty_manager_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء مدير العقوبات"""
    user = update.message.from_user
    
    if not db.is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    keyboard = [
        [InlineKeyboardButton("➕ إضافة عقوبة جديدة", callback_data="add_penalty")],
        [InlineKeyboardButton("📋 عرض عقوبات موظف", callback_data="view_employee_penalties")],
        [InlineKeyboardButton("⏱️ إعدادات التأخير", callback_data="delay_settings")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔧 **مدير العقوبات**\n\n"
        "اختر الإجراء الذي تريد تنفيذه:",
        reply_markup=reply_markup
    )
    
    return PENALTY_MENU

async def handle_penalty_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيارات مدير العقوبات"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "add_penalty":
        await query.edit_message_text(
            "🔍 **البحث عن الموظف**\n\n"
            "أدخل اسم الموظف أو رقم الهاتف للبحث:"
        )
        return SELECT_EMPLOYEE_FOR_PENALTY
    
    elif data == "view_employee_penalties":
        await query.edit_message_text(
            "🔍 **البحث عن موظف لعرض عقوباته**\n\n"
            "أدخل اسم الموظف أو رقم الهاتف للبحث:"
        )
        context.user_data['penalty_action'] = 'view'
        return SELECT_EMPLOYEE_FOR_PENALTY
    
    elif data == "delay_settings":
        current_delay = delay_settings.get_current_delay()
        await query.edit_message_text(
            f"⏱️ **إعدادات وقت التأخير**\n\n"
            f"الوقت الحالي: {current_delay} دقيقة\n\n"
            f"أدخل وقت التأخير الجديد بالدقائق:"
        )
        context.user_data['awaiting_input'] = 'delay'
        return EDIT_PENALTY_CUSTOM_AMOUNT
    
    elif data == "edit_delay":
        current_delay = delay_settings.get_current_delay()
        await query.edit_message_text(
            f"⏱️ **تعديل وقت التأخير**\n\n"
            f"الوقت الحالي: {current_delay} دقيقة\n\n"
            f"أدخل وقت التأخير الجديد بالدقائق (1-1440):"
        )
        context.user_data['awaiting_input'] = 'delay'
        return EDIT_PENALTY_CUSTOM_AMOUNT
    
    elif data == "view_delay":
        current_delay = delay_settings.get_current_delay()
        await query.edit_message_text(
            f"⏱️ **الإعدادات الحالية**\n\n"
            f"• وقت التأخير: {current_delay} دقيقة\n"
            f"• فترة السماح: {delay_settings.grace_period} دقيقة\n"
            f"• الحد الأقصى: {delay_settings.max_delay_minutes} دقيقة (24 ساعة)\n\n"
            f"🔧 لتعديل الإعدادات، اختر 'تعديل وقت التأخير'"
        )
        return PENALTY_MENU
    
    elif data == "back_to_main":
        await query.edit_message_text("تم العودة للقائمة الرئيسية.")
        return ConversationHandler.END
    
    elif data == "cancel":
        await query.edit_message_text("❌ تم إغلاق مدير العقوبات.")
        return ConversationHandler.END
    
    return PENALTY_MENU

async def edit_penalty_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدخال المبلغ أو الوقت المخصص"""
    if update.message:
        user_input = update.message.text
    else:
        query = update.callback_query
        await query.answer()
        return
    
    awaiting_input = context.user_data.get('awaiting_input')
    
    if awaiting_input == 'delay':
        try:
            new_delay = int(user_input)
            if 1 <= new_delay <= delay_settings.max_delay_minutes:
                if delay_settings.update_delay(new_delay, update.message.from_user.id):
                    await update.message.reply_text(
                        f"✅ تم تحديث وقت التأخير إلى {new_delay} دقيقة.\n\n"
                        f"⏱️ سيتم تطبيق الوقت الجديد على جميع الموظفين فوراً."
                    )
                else:
                    await update.message.reply_text("❌ حدث خطأ في تحديث وقت التأخير.")
            else:
                await update.message.reply_text(
                    f"❌ وقت التأخير يجب أن يكون بين 1 و {delay_settings.max_delay_minutes} دقيقة.\n"
                    f"أعد إدخال الوقت:"
                )
                return EDIT_PENALTY_CUSTOM_AMOUNT
        except ValueError:
            await update.message.reply_text(
                "❌ إدخال غير صالح. يرجى إدخال رقم صحيح.\n"
                "أعد إدخال الوقت:"
            )
            return EDIT_PENALTY_CUSTOM_AMOUNT
    
    context.user_data.clear()
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض رسالة المساعدة"""
    help_text = """
🤖 **أوامر بوت إدارة الموظفين:**

📊 **أوامر الحضور:**
تسجيل حضور 📝 - تسجيل دخول
تسجيل انصراف 🚪 - تسجيل خروج
تقرير الحضور 📊 - تقرير الحضور
تقريري الكامل 📈 - تقريري الكامل

🚬 **أوامر الطلبات:**
طلب سيجارة 🚬 - طلب سيجارة
طلب استراحة ☕ - طلب استراحة غداء
طلب إذن خروج 🏠 - طلب إذن خروج
طلب إجازة 🌴 - طلب إجازة

⚖️ **نظام العقوبات:**
🔧 مدير العقوبات - مدير العقوبات (للمديرين)
⏱️ إعدادات التأخير - تعديل وقت التأخير (للمديرين)

⏰ **مواعيد العمل:**
• بداية الدوام: 8:00 صباحاً
• ساعات العمل الأساسية: 9 ساعات
• فترة السماح للتأخير: متغيرة حسب إعدادات المدير

🚬 **قواعد السجائر:**
• عدد السجائر اليومي: 5 سجائر
• الفجوة بين السجائر: 1.5 ساعة
• السماح بالسجائر بعد: 10:00 صباحاً
• مدة السيجارة: 6 دقائق

👑 **للمديرين فقط:** 
- مدير العقوبات
- إعدادات وقت التأخير
"""
    
    await update.message.reply_text(help_text)

async def my_penalties_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض عقوبات الموظف"""
    user = update.message.from_user
    employee = db.get_employee_by_telegram_id(user.id)
    
    if not employee:
        await update.message.reply_text(
            "❌ لم يتم العثور على بياناتك.\n"
            "يرجى إرسال معلومات الاتصال أولاً."
        )
        return
    
    employee_id = employee['id']
    employee_name = employee.get('full_name', user.first_name)
    
    penalties = db.get_employee_penalties(employee_id, active_only=True)
    
    if not penalties:
        await update.message.reply_text(
            f"📋 العقوبات - {employee_name}\n\n"
            "✅ لا توجد عقوبات نشطة حالياً.\n"
            "👏 أحسنت! استمر في الحفاظ على التزامك."
        )
        return
    
    message = (
        f"📋 العقوبات النشطة - {employee_name}\n"
        f"📅 تاريخ التقرير: {get_jordan_time().strftime('%Y-%m-%d')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    for i, penalty in enumerate(penalties, 1):
        message += f"🔹 العقوبة #{i}\n"
        message += f"   📛 النوع: {penalty.get('penalty_name', 'غير محدد')}\n"
        message += f"   📅 التاريخ: {penalty.get('penalty_date').strftime('%Y-%m-%d')}\n"
        message += f"   📝 السبب: {penalty.get('reason', 'غير محدد')}\n"
        deduction = penalty.get('deduction_amount', 0)
        if deduction > 0:
            message += f"   💰 الخصم: {deduction:.2f} دينار\n"
        ban_days = penalty.get('smoke_ban_days', 0)
        if ban_days > 0:
            message += f"   🚬 حظر سجائر: {ban_days} يوم\n"
        message += "\n"
    
    total_deduction = sum(p.get('deduction_amount', 0) for p in penalties)
    message += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 ملخص العقوبات:\n"
        f"   ⚖️ عدد العقوبات النشطة: {len(penalties)}\n"
        f"   💰 إجمالي الخصومات: {total_deduction:.2f} دينار\n\n"
    )
    
    if db.is_employee_banned_from_smoking(employee_id):
        message += "🚫 حالة السجائر: محروم حالياً\n"
    else:
        message += "✅ حالة السجائر: مسموح\n"
    
    await update.message.reply_text(message)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء المحادثة"""
    await update.message.reply_text(
        "❌ تم إلغاء العملية.",
        reply_markup=get_main_keyboard(update.message.from_user.id)
    )
    return ConversationHandler.END

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة ضغطات الأزرار"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("approve_leave_"):
        user_id = int(data.split("_")[2])
        await query.edit_message_text(f"✅ تمت الموافقة على طلب إذن الخروج للمستخدم {user_id}")
    
    elif data.startswith("reject_leave_"):
        user_id = int(data.split("_")[2])
        await query.edit_message_text(f"❌ تم رفض طلب إذن الخروج للمستخدم {user_id}")
    
    elif data.startswith("approve_vacation_"):
        user_id = int(data.split("_")[2])
        await query.edit_message_text(f"✅ تمت الموافقة على طلب الإجازة للمستخدم {user_id}")
    
    elif data.startswith("reject_vacation_"):
        user_id = int(data.split("_")[2])
        await query.edit_message_text(f"❌ تم رفض طلب الإجازة للمستخدم {user_id}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الأخطاء"""
    logger.error(f"حدث خطأ: {context.error}")
    
    try:
        if update and update.message:
            await update.message.reply_text(
                "❌ حدث خطأ غير متوقع.\n"
                "يرجى المحاولة مرة أخرى لاحقاً."
            )
    except:
        pass

# ===== الدالة الرئيسية للبوت =====
async def run_bot():
    """تشغيل البوت بشكل صحيح"""
    print("🚀 بدء بوت إدارة حضور الموظفين...")
    print("=" * 50)
    print(f"👑 عدد المديرين: {len(db.admins)}")
    print(f"⏱️ وقت التأخير الحالي: {delay_settings.get_current_delay()} دقيقة")
    print(f"🚬 عدد السجائر اليومية: {MAX_DAILY_SMOKES}")
    print("=" * 50)
    
    # إنشاء Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # إضافة Handlers بالترتيب الصحيح
    
    # 1. Conversation Handlers أولاً
    leave_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Text(["طلب إذن خروج 🏠"]), leave_request)],
        states={
            LEAVE_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_leave_reason)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    vacation_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Text(["طلب إجازة 🌴"]), vacation_request)],
        states={
            VACATION_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_vacation_reason)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    penalty_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Text(["🔧 مدير العقوبات"]), penalty_manager_command)],
        states={
            PENALTY_MENU: [CallbackQueryHandler(handle_penalty_menu)],
            SELECT_EMPLOYEE_FOR_PENALTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: None),  # سيملأ لاحقاً
                CallbackQueryHandler(lambda u, c: None)
            ],
            EDIT_PENALTY_CUSTOM_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_penalty_custom_amount)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    delay_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Text(["⏱️ إعدادات التأخير"]), delay_settings_command)],
        states={
            PENALTY_MENU: [CallbackQueryHandler(handle_penalty_menu)],
            EDIT_PENALTY_CUSTOM_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_penalty_custom_amount)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    application.add_handler(leave_conv_handler)
    application.add_handler(vacation_conv_handler)
    application.add_handler(penalty_conv_handler)
    application.add_handler(delay_conv_handler)
    
    # 2. Message Handler للنصوص (يجب أن يأتي بعد المحادثات)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    
    # 3. Command Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("my_penalties", my_penalties_command))
    
    # 4. معالج جهات الاتصال
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    
    # 5. Callback Query Handler
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # 6. Error Handler
    application.add_error_handler(error_handler)
    
    # بدء البوت
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    
    print("\n✅ البوت يعمل الآن بنجاح!")
    print("📱 أرسل /start للبوت للبدء")
    print("=" * 50)
    
    # الانتظار حتى إيقاف البوت
    stop_event = asyncio.Event()
    await stop_event.wait()

def main():
    """بدء تشغيل البوت"""
    if not BOT_TOKEN:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN not found!")
        print("يرجى تعيين التوكن في متغيرات البيئة.")
        return
    
    # إدارة النسخة
    bot_manager = BotInstanceManager()
    print(f"🆔 معرف نسخة البوت: {bot_manager.instance_id}")
    
    try:
        # تشغيل البوت
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        print("\n⏹️  إيقاف البوت...")
    except Exception as e:
        logger.error(f"خطأ في البوت: {e}")
        print(f"\n❌ خطأ جسيم: {e}")
        print("🔴 يرجى مراجعة المبرمج")
    finally:
        print("✅ تم إيقاف البوت بنجاح.")

if __name__ == '__main__':
    main()