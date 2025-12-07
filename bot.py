import os
import logging
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, date, time, timezone
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes

# تعريف حالات المحادثة
PENALTY_MENU, SELECT_EMPLOYEE_FOR_PENALTY, SELECT_PENALTY_TYPE, ENTER_PENALTY_DETAILS, CONFIRM_PENALTY, EDIT_PENALTY = range(6)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

ADMIN_IDS = [1465191277]

authorized_phones = [
    '+962786644106'
]

# نظام العقوبات الأساسي
PENALTY_TYPES = {
    'late': {'name': 'تأخير', 'amount': 10, 'ban_days': 1},
    'absent': {'name': 'غياب غير مبرر', 'amount': 50, 'ban_days': 3},
    'smoke_excess': {'name': 'تجاوز عدد السجائر', 'amount': 10, 'ban_days': 1},
    'early_checkout': {'name': 'انصراف مبكر', 'amount': 20, 'ban_days': 2},
    'no_checkin': {'name': 'عدم تسجيل حضور', 'amount': 10, 'ban_days': 1},
    'no_checkout': {'name': 'عدم تسجيل انصراف', 'amount': 10, 'ban_days': 1},
    'other': {'name': 'مخالفة أخرى', 'amount': 10, 'ban_days': 0}
}

JORDAN_TZ = ZoneInfo('Asia/Amman')

def get_db_connection():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

def get_jordan_time():
    """الحصول على الوقت الحالي بتوقيت الأردن"""
    return datetime.now(JORDAN_TZ)

def initialize_database_tables():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                phone_number VARCHAR(20) NOT NULL,
                full_name VARCHAR(100) NOT NULL,
                last_active TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS penalties (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                penalty_type VARCHAR(50) NOT NULL,
                penalty_name VARCHAR(100) NOT NULL,
                amount DECIMAL(10,2) DEFAULT 0,
                ban_days INTEGER DEFAULT 0,
                reason TEXT NOT NULL,
                penalty_date DATE NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                created_by BIGINT
            );
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Database tables initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Error initializing database tables: {e}")
        return False

# ==== دوال المساعدة ====
def get_employee_by_telegram_id(telegram_id):
    """الحصول على بيانات الموظف من قاعدة البيانات"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM employees WHERE telegram_id = %s", (telegram_id,))
        employee = cur.fetchone()
        cur.close()
        conn.close()
        return dict(employee) if employee else None
    except Exception as e:
        logger.error(f"خطأ في قراءة بيانات الموظف: {e}")
        return None

def get_employee_by_id(employee_id):
    """الحصول على بيانات الموظف بالمعرف"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM employees WHERE id = %s", (employee_id,))
        employee = cur.fetchone()
        cur.close()
        conn.close()
        return dict(employee) if employee else None
    except Exception as e:
        logger.error(f"Error getting employee by ID: {e}")
        return None

def get_employee_by_phone(phone_number):
    """الحصول على بيانات الموظف باستخدام رقم الهاتف"""
    try:
        normalized = phone_number.replace(' ', '').replace('-', '')
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM employees WHERE phone_number = %s", (normalized,))
        employee = cur.fetchone()
        cur.close()
        conn.close()
        return dict(employee) if employee else None
    except Exception as e:
        logger.error(f"خطأ في قراءة بيانات الموظف برقم الهاتف: {e}")
        return None

def is_admin(user_id):
    """التحقق من أن المستخدم مدير"""
    return user_id in ADMIN_IDS

def add_penalty_to_db(employee_id, penalty_type, reason, amount, ban_days, created_by):
    """إضافة عقوبة إلى قاعدة البيانات"""
    try:
        penalty_info = PENALTY_TYPES.get(penalty_type, PENALTY_TYPES['other'])
        
        conn = get_db_connection()
        cur = conn.cursor()
        today = get_jordan_time().date()
        
        cur.execute("""
            INSERT INTO penalties (employee_id, penalty_type, penalty_name, amount, ban_days, reason, penalty_date, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (employee_id, penalty_type, penalty_info['name'], amount, ban_days, reason, today, created_by))
        
        penalty_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"تم إضافة عقوبة للموظف {employee_id}: {penalty_info['name']} (مبلغ: {amount} دينار)")
        return {'success': True, 'penalty_id': penalty_id}
    except Exception as e:
        logger.error(f"خطأ في إضافة العقوبة: {e}")
        return {'success': False, 'error': str(e)}

def get_employee_penalties(employee_id):
    """الحصول على عقوبات الموظف"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT p.*, e.full_name, e.phone_number
            FROM penalties p
            JOIN employees e ON p.employee_id = e.id
            WHERE p.employee_id = %s AND p.is_active = TRUE
            ORDER BY p.penalty_date DESC, p.created_at DESC
        """, (employee_id,))
        
        penalties = cur.fetchall()
        cur.close()
        conn.close()
        
        return [dict(penalty) for penalty in penalties] if penalties else []
    except Exception as e:
        logger.error(f"خطأ في قراءة عقوبات الموظف: {e}")
        return []

def update_penalty_status(penalty_id, is_active):
    """تحديث حالة العقوبة"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE penalties 
            SET is_active = %s
            WHERE id = %s
            RETURNING id
        """, (is_active, penalty_id))
        
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        return {'success': bool(updated)}
    except Exception as e:
        logger.error(f"خطأ في تحديث حالة العقوبة: {e}")
        return {'success': False, 'error': str(e)}

def get_all_employees():
    """الحصول على جميع الموظفين"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM employees ORDER BY full_name")
        employees = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(emp) for emp in employees] if employees else []
    except Exception as e:
        logger.error(f"خطأ في قراءة قائمة الموظفين: {e}")
        return []

def search_employees(search_term):
    """بحث عن موظفين بالاسم أو الهاتف"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        search_pattern = f"%{search_term}%"
        cur.execute("""
            SELECT * FROM employees 
            WHERE full_name ILIKE %s OR phone_number ILIKE %s
            ORDER BY full_name
            LIMIT 10
        """, (search_pattern, search_pattern))
        
        employees = cur.fetchall()
        cur.close()
        conn.close()
        
        return [dict(emp) for emp in employees]
    except Exception as e:
        logger.error(f"خطأ في البحث عن الموظفين: {e}")
        return []

# ==== نظام العقوبات المبسط ====
async def start_penalty_manager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء مدير العقوبات"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return ConversationHandler.END
    
    keyboard = [
        [InlineKeyboardButton("➕ إضافة عقوبة جديدة", callback_data="add_penalty")],
        [InlineKeyboardButton("👁️ عرض عقوبات موظف", callback_data="view_penalties")],
        [InlineKeyboardButton("❌ إلغاء عقوبة", callback_data="cancel_penalty")],
        [InlineKeyboardButton("📋 جميع العقوبات النشطة", callback_data="all_penalties")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔧 **مدير العقوبات المبسط**\n\n"
        "اختر الإجراء الذي تريد تنفيذه:",
        reply_markup=reply_markup
    )
    
    return PENALTY_MENU

async def handle_penalty_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيارات مدير العقوبات"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "add_penalty":
        await query.edit_message_text(
            "🔍 **البحث عن الموظف**\n\n"
            "أدخل اسم الموظف أو رقم هاتفه:\n"
            "مثال: أحمد أو +962791234567"
        )
        return SELECT_EMPLOYEE_FOR_PENALTY
    
    elif data == "view_penalties":
        await query.edit_message_text(
            "🔍 **البحث عن الموظف**\n\n"
            "أدخل اسم الموظف أو رقم هاتفه لعرض عقوباته:"
        )
        context.user_data['action'] = 'view'
        return SELECT_EMPLOYEE_FOR_PENALTY
    
    elif data == "cancel_penalty":
        await query.edit_message_text(
            "🔍 **البحث عن الموظف**\n\n"
            "أدخل اسم الموظف أو رقم هاتفه لإلغاء عقوبة:"
        )
        context.user_data['action'] = 'cancel'
        return SELECT_EMPLOYEE_FOR_PENALTY
    
    elif data == "all_penalties":
        await show_all_penalties(query, context)
        return ConversationHandler.END
    
    return PENALTY_MENU

async def select_employee_for_penalty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """البحث عن موظف"""
    search_term = update.message.text
    
    employees = search_employees(search_term)
    
    if not employees:
        await update.message.reply_text(
            f"❌ لم يتم العثور على موظفين يتطابقون مع: {search_term}\n\n"
            "يرجى المحاولة مرة أخرى."
        )
        return SELECT_EMPLOYEE_FOR_PENALTY
    
    # عرض قائمة الموظفين
    keyboard = []
    for emp in employees[:5]:
        name = emp['full_name']
        phone = emp['phone_number']
        button_text = f"{name} ({phone})"
        callback_data = f"select_emp_{emp['id']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    action = context.user_data.get('action', 'add')
    action_text = {
        'add': 'إضافة عقوبة',
        'view': 'عرض العقوبات',
        'cancel': 'إلغاء عقوبة'
    }.get(action, 'الإجراء')
    
    await update.message.reply_text(
        f"🔍 **نتائج البحث:**\n"
        f"الإجراء: {action_text}\n\n"
        f"اختر الموظف:",
        reply_markup=reply_markup
    )
    
    return SELECT_EMPLOYEE_FOR_PENALTY

async def handle_employee_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار الموظف"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "back_to_menu":
        await query.edit_message_text("🔙 العودة إلى القائمة الرئيسية.")
        return await start_penalty_manager(query.message, context)
    
    elif data.startswith("select_emp_"):
        employee_id = int(data.split("_")[2])
        employee = get_employee_by_id(employee_id)
        
        if not employee:
            await query.edit_message_text("❌ خطأ: الموظف غير موجود.")
            return ConversationHandler.END
        
        context.user_data['selected_employee'] = employee
        action = context.user_data.get('action', 'add')
        
        if action == 'add':
            # عرض أنواع العقوبات للإضافة
            keyboard = []
            for penalty_type, info in PENALTY_TYPES.items():
                button_text = f"{info['name']} ({info['amount']} دينار)"
                callback_data = f"penalty_type_{penalty_type}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
            
            keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_search")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"👤 **الموظف:** {employee['full_name']}\n"
                f"📱 **الهاتف:** {employee['phone_number']}\n\n"
                f"📋 **اختر نوع المخالفة:**",
                reply_markup=reply_markup
            )
            
            return SELECT_PENALTY_TYPE
        
        elif action == 'view':
            # عرض عقوبات الموظف
            penalties = get_employee_penalties(employee_id)
            await show_employee_penalties(query, employee, penalties)
            return ConversationHandler.END
        
        elif action == 'cancel':
            # عرض عقوبات الموظف للإلغاء
            penalties = get_employee_penalties(employee_id)
            await show_penalties_for_cancellation(query, employee, penalties)
            return ConversationHandler.END
    
    return PENALTY_MENU

async def back_to_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """العودة إلى البحث"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🔍 **البحث عن الموظف**\n\n"
        "أدخل اسم الموظف أو رقم هاتفه:"
    )
    return SELECT_EMPLOYEE_FOR_PENALTY

async def select_penalty_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختيار نوع العقوبة"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "back_to_search":
        await back_to_search(update, context)
        return SELECT_EMPLOYEE_FOR_PENALTY
    
    elif data.startswith("penalty_type_"):
        penalty_type = data.split("_")[2]
        
        if penalty_type not in PENALTY_TYPES:
            await query.edit_message_text("❌ خطأ: نوع المخالفة غير موجود.")
            return ConversationHandler.END
        
        penalty_info = PENALTY_TYPES[penalty_type]
        context.user_data['selected_penalty_type'] = penalty_type
        
        await query.edit_message_text(
            f"📝 **نوع المخالفة:** {penalty_info['name']}\n"
            f"💰 **المبلغ الافتراضي:** {penalty_info['amount']} دينار\n"
            f"🚬 **حظر سجائر:** {penalty_info['ban_days']} يوم\n\n"
            f"✏️ **الآن، أدخل سبب المخالفة:**\n\n"
            f"💡 مثال:\n"
            f"• تأخير 30 دقيقة يوم 2024-01-15\n"
            f"• عدم تسجيل الحضور بتاريخ 2024-01-14\n"
            f"• تجاوز عدد السجائر المسموح به"
        )
        
        return ENTER_PENALTY_DETAILS

async def enter_penalty_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إدخال تفاصيل العقوبة"""
    reason = update.message.text
    
    if len(reason) < 5:
        await update.message.reply_text(
            "❌ السبب قصير جداً. يرجى كتابة سبب مفصل.\n"
            "أعد إدخال السبب:"
        )
        return ENTER_PENALTY_DETAILS
    
    context.user_data['penalty_reason'] = reason
    
    # الحصول على المعلومات
    employee = context.user_data.get('selected_employee', {})
    penalty_type = context.user_data.get('selected_penalty_type', '')
    
    if not employee or not penalty_type:
        await update.message.reply_text("❌ خطأ: بيانات غير كاملة.")
        return ConversationHandler.END
    
    penalty_info = PENALTY_TYPES[penalty_type]
    
    # عرض تأكيد العقوبة
    keyboard = [
        [InlineKeyboardButton("✅ تأكيد وإضافة العقوبة", callback_data="confirm_add")],
        [InlineKeyboardButton("✏️ تعديل المبلغ", callback_data="edit_amount")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_types")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📋 **ملخص العقوبة**\n\n"
        f"👤 **الموظف:** {employee['full_name']}\n"
        f"📱 **الهاتف:** {employee['phone_number']}\n\n"
        f"⚖️ **المخالفة:** {penalty_info['name']}\n"
        f"💰 **المبلغ:** {penalty_info['amount']} دينار\n"
        f"🚬 **حظر سجائر:** {penalty_info['ban_days']} يوم\n\n"
        f"📝 **السبب:**\n{reason}\n\n"
        f"⏰ **التاريخ:** {get_jordan_time().strftime('%Y-%m-%d')}\n\n"
        f"💡 **اختر الإجراء:**",
        reply_markup=reply_markup
    )
    
    return CONFIRM_PENALTY

async def back_to_types(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """العودة إلى أنواع العقوبات"""
    query = update.callback_query
    await query.answer()
    
    employee = context.user_data.get('selected_employee', {})
    
    keyboard = []
    for penalty_type, info in PENALTY_TYPES.items():
        button_text = f"{info['name']} ({info['amount']} دينار)"
        callback_data = f"penalty_type_{penalty_type}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_search")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"👤 **الموظف:** {employee['full_name']}\n"
        f"📱 **الهاتف:** {employee['phone_number']}\n\n"
        f"📋 **اختر نوع المخالفة:**",
        reply_markup=reply_markup
    )
    
    return SELECT_PENALTY_TYPE

async def edit_penalty_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعديل مبلغ العقوبة"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "💰 **تعديل المبلغ**\n\n"
        "أدخل المبلغ الجديد (بالدينار):\n"
        "مثال: 15 أو 25.5"
    )
    
    context.user_data['awaiting_input'] = 'amount'
    return CONFIRM_PENALTY

async def process_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة المبلغ المخصص"""
    try:
        amount = float(update.message.text)
        if amount < 0 or amount > 1000:
            await update.message.reply_text(
                "❌ المبلغ يجب أن يكون بين 0 و 1000 دينار.\n"
                "أعد إدخال المبلغ:"
            )
            return CONFIRM_PENALTY
        
        context.user_data['custom_amount'] = amount
        
        # إعادة عرض التأكيد مع المبلغ الجديد
        employee = context.user_data.get('selected_employee', {})
        penalty_type = context.user_data.get('selected_penalty_type', '')
        reason = context.user_data.get('penalty_reason', '')
        
        penalty_info = PENALTY_TYPES[penalty_type]
        
        keyboard = [
            [InlineKeyboardButton("✅ تأكيد وإضافة العقوبة", callback_data="confirm_add_custom")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_confirm")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📋 **ملخص العقوبة (محدث)**\n\n"
            f"👤 **الموظف:** {employee['full_name']}\n"
            f"📱 **الهاتف:** {employee['phone_number']}\n\n"
            f"⚖️ **المخالفة:** {penalty_info['name']}\n"
            f"💰 **المبلغ:** {amount} دينار (مخصص)\n"
            f"🚬 **حظر سجائر:** {penalty_info['ban_days']} يوم\n\n"
            f"📝 **السبب:**\n{reason}\n\n"
            f"⏰ **التاريخ:** {get_jordan_time().strftime('%Y-%m-%d')}\n\n"
            f"💡 **تأكيد الإضافة:**",
            reply_markup=reply_markup
        )
        
    except ValueError:
        await update.message.reply_text(
            "❌ إدخال غير صالح. يرجى إدخال رقم.\n"
            "أعد إدخال المبلغ:"
        )
        return CONFIRM_PENALTY

async def back_to_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """العودة إلى تأكيد العقوبة"""
    query = update.callback_query
    await query.answer()
    
    employee = context.user_data.get('selected_employee', {})
    penalty_type = context.user_data.get('selected_penalty_type', '')
    reason = context.user_data.get('penalty_reason', '')
    
    penalty_info = PENALTY_TYPES[penalty_type]
    
    keyboard = [
        [InlineKeyboardButton("✅ تأكيد وإضافة العقوبة", callback_data="confirm_add")],
        [InlineKeyboardButton("✏️ تعديل المبلغ", callback_data="edit_amount")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_types")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📋 **ملخص العقوبة**\n\n"
        f"👤 **الموظف:** {employee['full_name']}\n"
        f"📱 **الهاتف:** {employee['phone_number']}\n\n"
        f"⚖️ **المخالفة:** {penalty_info['name']}\n"
        f"💰 **المبلغ:** {penalty_info['amount']} دينار\n"
        f"🚬 **حظر سجائر:** {penalty_info['ban_days']} يوم\n\n"
        f"📝 **السبب:**\n{reason}\n\n"
        f"⏰ **التاريخ:** {get_jordan_time().strftime('%Y-%m-%d')}\n\n"
        f"💡 **اختر الإجراء:**",
        reply_markup=reply_markup
    )
    
    return CONFIRM_PENALTY

async def confirm_add_penalty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تأكيد إضافة العقوبة"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    employee = context.user_data.get('selected_employee', {})
    penalty_type = context.user_data.get('selected_penalty_type', '')
    reason = context.user_data.get('penalty_reason', '')
    
    if not employee or not penalty_type or not reason:
        await query.edit_message_text("❌ خطأ: بيانات غير كاملة.")
        return ConversationHandler.END
    
    penalty_info = PENALTY_TYPES[penalty_type]
    
    # تحديد المبلغ (الافتراضي أو المخصص)
    if data == "confirm_add_custom":
        amount = context.user_data.get('custom_amount', penalty_info['amount'])
    else:
        amount = penalty_info['amount']
    
    ban_days = penalty_info['ban_days']
    
    # إضافة العقوبة
    result = add_penalty_to_db(
        employee_id=employee['id'],
        penalty_type=penalty_type,
        reason=reason,
        amount=amount,
        ban_days=ban_days,
        created_by=query.from_user.id
    )
    
    if result['success']:
        # إشعار المدير
        await query.edit_message_text(
            f"✅ **تم إضافة العقوبة بنجاح!**\n\n"
            f"🆔 معرف العقوبة: {result['penalty_id']}\n"
            f"👤 الموظف: {employee['full_name']}\n"
            f"📱 الهاتف: {employee['phone_number']}\n"
            f"📝 المخالفة: {penalty_info['name']}\n"
            f"💰 المبلغ: {amount} دينار\n"
            f"🚬 حظر سجائر: {ban_days} يوم\n\n"
            f"📅 التاريخ: {get_jordan_time().strftime('%Y-%m-%d')}\n"
            f"👤 المدير: {query.from_user.first_name}"
        )
        
        # إشعار الموظف إذا كان مسجلاً في تيليجرام
        telegram_id = employee.get('telegram_id')
        if telegram_id:
            try:
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text=f"⚠️ **تم إضافة عقوبة جديدة لك**\n\n"
                         f"📝 المخالفة: {penalty_info['name']}\n"
                         f"💰 المبلغ: {amount} دينار\n"
                         f"🚬 حظر سجائر: {ban_days} يوم\n"
                         f"📋 السبب: {reason}\n\n"
                         f"📅 التاريخ: {get_jordan_time().strftime('%Y-%m-%d')}\n"
                         f"👤 المدير: {query.from_user.first_name}"
                )
            except Exception as e:
                logger.error(f"Failed to notify employee: {e}")
    else:
        await query.edit_message_text(
            f"❌ **خطأ في إضافة العقوبة:**\n{result.get('error', 'خطأ غير معروف')}"
        )
    
    # تنظيف البيانات
    context.user_data.clear()
    return ConversationHandler.END

async def show_employee_penalties(query, employee, penalties):
    """عرض عقوبات الموظف"""
    if not penalties:
        await query.edit_message_text(
            f"📋 **عقوبات الموظف:** {employee['full_name']}\n\n"
            f"✅ لا توجد عقوبات نشطة لهذا الموظف."
        )
        return
    
    message = (
        f"📋 **عقوبات الموظف:** {employee['full_name']}\n"
        f"📱 **الهاتف:** {employee['phone_number']}\n"
        f"📅 **تاريخ التقرير:** {get_jordan_time().strftime('%Y-%m-%d')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    total_amount = 0
    
    for i, penalty in enumerate(penalties, 1):
        message += f"🔹 **العقوبة #{i}**\n"
        message += f"   🆔 المعرف: {penalty['id']}\n"
        message += f"   📛 النوع: {penalty['penalty_name']}\n"
        message += f"   📅 التاريخ: {penalty['penalty_date'].strftime('%Y-%m-%d')}\n"
        message += f"   📝 السبب: {penalty['reason']}\n"
        message += f"   💰 المبلغ: {float(penalty['amount']):.2f} دينار\n"
        if penalty['ban_days'] > 0:
            message += f"   🚬 حظر سجائر: {penalty['ban_days']} يوم\n"
        message += "\n"
        
        total_amount += float(penalty['amount'])
    
    message += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **الملخص:**\n"
        f"   📋 عدد العقوبات: {len(penalties)}\n"
        f"   💰 إجمالي المبالغ: {total_amount:.2f} دينار\n\n"
        f"💡 **للإدارة:**\n"
        f"استخدم /penalty_manager للإجراءات الأخرى"
    )
    
    await query.edit_message_text(message)

async def show_penalties_for_cancellation(query, employee, penalties):
    """عرض العقوبات للإلغاء"""
    if not penalties:
        await query.edit_message_text(
            f"📋 **عقوبات الموظف:** {employee['full_name']}\n\n"
            f"✅ لا توجد عقوبات نشطة لهذا الموظف."
        )
        return
    
    keyboard = []
    for penalty in penalties[:10]:  # عرض أول 10 عقوبات فقط
        penalty_date = penalty['penalty_date'].strftime('%Y-%m-%d')
        button_text = f"{penalty['penalty_name']} - {penalty_date} - {float(penalty['amount']):.2f} دينار"
        callback_data = f"cancel_pen_{penalty['id']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🗑️ **إلغاء عقوبة**\n\n"
        f"👤 **الموظف:** {employee['full_name']}\n"
        f"📱 **الهاتف:** {employee['phone_number']}\n\n"
        f"اختر العقوبة التي تريد إلغاءها:",
        reply_markup=reply_markup
    )

async def cancel_penalty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء عقوبة"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("cancel_pen_"):
        penalty_id = int(data.split("_")[2])
        
        # الحصول على تفاصيل العقوبة
        try:
            conn = get_db_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT p.*, e.full_name, e.phone_number 
                FROM penalties p
                JOIN employees e ON p.employee_id = e.id
                WHERE p.id = %s
            """, (penalty_id,))
            
            penalty = cur.fetchone()
            cur.close()
            conn.close()
            
            if not penalty:
                await query.edit_message_text("❌ العقوبة غير موجودة.")
                return ConversationHandler.END
            
            # عرض تأكيد الإلغاء
            keyboard = [
                [InlineKeyboardButton("✅ نعم، تأكيد الإلغاء", callback_data=f"confirm_cancel_{penalty_id}")],
                [InlineKeyboardButton("❌ لا، إلغاء الأمر", callback_data="cancel_action")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"🗑️ **تأكيد إلغاء العقوبة**\n\n"
                f"🆔 المعرف: {penalty['id']}\n"
                f"👤 الموظف: {penalty['full_name']}\n"
                f"📱 الهاتف: {penalty['phone_number']}\n"
                f"📝 المخالفة: {penalty['penalty_name']}\n"
                f"💰 المبلغ: {float(penalty['amount']):.2f} دينار\n"
                f"📅 التاريخ: {penalty['penalty_date'].strftime('%Y-%m-%d')}\n\n"
                f"📝 **السبب:**\n{penalty['reason']}\n\n"
                f"⚠️ **هل أنت متأكد من إلغاء هذه العقوبة؟**",
                reply_markup=reply_markup
            )
            
        except Exception as e:
            logger.error(f"خطأ في قراءة العقوبة: {e}")
            await query.edit_message_text("❌ خطأ في قراءة العقوبة.")
    
    elif data.startswith("confirm_cancel_"):
        penalty_id = int(data.split("_")[2])
        
        result = update_penalty_status(penalty_id, False)
        
        if result['success']:
            await query.edit_message_text(
                f"✅ **تم إلغاء العقوبة بنجاح!**\n\n"
                f"🆔 المعرف: {penalty_id}\n"
                f"👤 المدير: {query.from_user.first_name}\n"
                f"⏰ الوقت: {get_jordan_time().strftime('%Y-%m-%d %H:%M')}"
            )
        else:
            await query.edit_message_text(
                f"❌ **خطأ في إلغاء العقوبة:**\n{result.get('error', 'خطأ غير معروف')}"
            )
    
    elif data == "cancel_action":
        await query.edit_message_text("❌ تم إلغاء العملية.")
    
    return ConversationHandler.END

async def show_all_penalties(query, context):
    """عرض جميع العقوبات النشطة"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT COUNT(*) as total, SUM(amount) as total_amount
            FROM penalties 
            WHERE is_active = TRUE
        """)
        
        stats = cur.fetchone()
        
        cur.execute("""
            SELECT p.id, e.full_name, p.penalty_name, p.amount, p.penalty_date
            FROM penalties p
            JOIN employees e ON p.employee_id = e.id
            WHERE p.is_active = TRUE
            ORDER BY p.penalty_date DESC
            LIMIT 20
        """)
        
        penalties = cur.fetchall()
        cur.close()
        conn.close()
        
        message = f"📋 **جميع العقوبات النشطة**\n\n"
        message += f"📊 **الإحصائيات:**\n"
        message += f"• عدد العقوبات: {stats[0] or 0}\n"
        message += f"• إجمالي المبالغ: {float(stats[1] or 0):.2f} دينار\n\n"
        message += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        if penalties:
            for penalty in penalties:
                message += (
                    f"🆔 **{penalty[0]}** - {penalty[1]}\n"
                    f"📝 {penalty[2]}\n"
                    f"💰 {float(penalty[3]):.2f} دينار\n"
                    f"📅 {penalty[4].strftime('%Y-%m-%d')}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                )
        else:
            message += "✅ لا توجد عقوبات نشطة حالياً.\n"
        
        message += "🔧 **للتعديل أو الإزالة، استخدم:**\n"
        message += "/penalty_manager ثم اختر 'إلغاء عقوبة'"
        
        await query.edit_message_text(message)
    except Exception as e:
        logger.error(f"Error getting all penalties: {e}")
        await query.edit_message_text("❌ حدث خطأ في جلب العقوبات.")

async def penalty_manager_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر مدير العقوبات"""
    return await start_penalty_manager(update, context)

async def list_penalties_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر عرض عقوبات موظف"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "📝 استخدام الأمر:\n"
            "`/list_penalties <رقم_الهاتف>`\n\n"
            "مثال:\n"
            "`/list_penalties +962791234567`"
        )
        return
    
    phone_number = context.args[0]
    employee = get_employee_by_phone(phone_number)
    
    if not employee:
        await update.message.reply_text(f"❌ لم يتم العثور على موظف برقم الهاتف: {phone_number}")
        return
    
    employee_id = employee['id']
    penalties = get_employee_penalties(employee_id)
    
    if not penalties:
        await update.message.reply_text(
            f"📋 **عقوبات الموظف:** {employee['full_name']}\n\n"
            f"✅ لا توجد عقوبات نشطة لهذا الموظف."
        )
        return
    
    message = (
        f"📋 **عقوبات الموظف:** {employee['full_name']}\n"
        f"📱 **الهاتف:** {phone_number}\n"
        f"📅 **تاريخ التقرير:** {get_jordan_time().strftime('%Y-%m-%d')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    total_amount = 0
    
    for i, penalty in enumerate(penalties, 1):
        message += f"🔹 **العقوبة #{i}**\n"
        message += f"   🆔 المعرف: {penalty['id']}\n"
        message += f"   📛 النوع: {penalty['penalty_name']}\n"
        message += f"   📅 التاريخ: {penalty['penalty_date'].strftime('%Y-%m-%d')}\n"
        message += f"   📝 السبب: {penalty['reason']}\n"
        message += f"   💰 المبلغ: {float(penalty['amount']):.2f} دينار\n"
        if penalty['ban_days'] > 0:
            message += f"   🚬 حظر سجائر: {penalty['ban_days']} يوم\n"
        message += "\n"
        
        total_amount += float(penalty['amount'])
    
    message += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **الملخص:**\n"
        f"   📋 عدد العقوبات: {len(penalties)}\n"
        f"   💰 إجمالي المبالغ: {total_amount:.2f} دينار\n"
    )
    
    await update.message.reply_text(message)

async def cancel_penalty_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر إلغاء عقوبة مباشر"""
    user = update.message.from_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ هذا الأمر متاح للمدير فقط.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "📝 استخدام الأمر:\n"
            "`/cancel_penalty <معرف_العقوبة>`\n\n"
            "مثال:\n"
            "`/cancel_penalty 123`\n\n"
            "💡 للحصول على معرف العقوبة، استخدم:\n"
            "`/list_penalties <رقم_الهاتف>`"
        )
        return
    
    try:
        penalty_id = int(context.args[0])
        
        result = update_penalty_status(penalty_id, False)
        
        if result['success']:
            await update.message.reply_text(
                f"✅ **تم إلغاء العقوبة بنجاح!**\n\n"
                f"🆔 المعرف: {penalty_id}\n"
                f"👤 المدير: {user.first_name}\n"
                f"⏰ الوقت: {get_jordan_time().strftime('%Y-%m-%d %H:%M')}"
            )
        else:
            await update.message.reply_text(
                f"❌ **خطأ في إلغاء العقوبة:**\n{result.get('error', 'خطأ غير معروف')}"
            )
    except ValueError:
        await update.message.reply_text("❌ معرف العقوبة يجب أن يكون رقمًا.")

# ==== الدوال الرئيسية للبوت ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء المحادثة مع البوت"""
    user = update.message.from_user
    logger.info(f"المستخدم {user.id} بدأ المحادثة.")
    
    employee = get_employee_by_telegram_id(user.id)
    if employee:
        employee_name = employee.get('full_name', user.first_name)
        
        if is_admin(user.id):
            keyboard = [
                [KeyboardButton("تسجيل حضور 📝"), KeyboardButton("تسجيل انصراف 🚪")],
                [KeyboardButton("طلب سيجارة 🚬"), KeyboardButton("تقرير الحضور 📊")],
                [KeyboardButton("🔧 مدير العقوبات")]
            ]
        else:
            keyboard = [
                [KeyboardButton("تسجيل حضور 📝"), KeyboardButton("تسجيل انصراف 🚪")],
                [KeyboardButton("طلب سيجارة 🚬"), KeyboardButton("تقرير الحضور 📊")]
            ]
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"👋 أهلاً بعودتك {employee_name}!\n\n"
            "اختر من الخيارات أدناه:",
            reply_markup=reply_markup
        )
        return
    
    contact_button = KeyboardButton("📱 مشاركة رقم الهاتف", request_contact=True)
    reply_markup = ReplyKeyboardMarkup([[contact_button]], resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "👋 مرحباً بك في نظام إدارة حضور الموظفين!\n\n"
        "📱 للمتابعة، يرجى مشاركة رقم هاتفك للتحقق من هويتك:\n\n"
        "اضغط على الزر أدناه لمشاركة رقم هاتفك.",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض رسالة المساعدة"""
    help_text = """
🤖 **أوامر بوت إدارة الموظفين:**

📊 **أوامر الحضور:**
/check_in - تسجيل دخول
/check_out - تسجيل خروج
/attendance_report - تقرير الحضور

🚬 **أوامر الطلبات:**
/smoke - طلب سيجارة

⚖️ **نظام العقوبات (للمديرين فقط):**
/penalty_manager - فتح مدير العقوبات
/list_penalties <رقم_الهاتف> - عرض عقوبات موظف
/cancel_penalty <معرف_العقوبة> - إلغاء عقوبة

⏰ **مواعيد العمل:**
• بداية الدوام: 8:00 صباحاً
• ساعات العمل الأساسية: 9 ساعات
• فترة السماح للتأخير: 15 دقيقة

🚬 **قواعد السجائر:**
• عدد السجائر اليومي: 5 سجائر
• الفجوة بين السجائر: 1.5 ساعة
• السماح بالسجائر بعد: 10:00 صباحاً

👑 **للمديرين فقط:** يمكن استخدام أوامر الإدارة.
"""
    
    await update.message.reply_text(help_text)

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إرسال معلومات الاتصال"""
    user = update.message.from_user
    contact = update.message.contact
    
    if not contact:
        await update.message.reply_text("❌ لم يتم إرسال معلومات الاتصال.")
        return
    
    phone_number = contact.phone_number
    
    # التحقق من رقم الهاتف
    normalized = phone_number.replace(' ', '').replace('-', '')
    is_authorized = any(auth_phone.replace(' ', '').replace('-', '') == normalized for auth_phone in authorized_phones)
    
    if not is_authorized:
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
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO employees (telegram_id, phone_number, full_name, last_active)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (telegram_id) 
            DO UPDATE SET 
                phone_number = EXCLUDED.phone_number,
                full_name = EXCLUDED.full_name,
                last_active = CURRENT_TIMESTAMP
            RETURNING id
        """, (user.id, normalized, full_name))
        
        employee_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"تم حفظ بيانات الموظف: {full_name} ({phone_number}) - ID: {employee_id}")
        
        # لوحة المفاتيح الرئيسية
        if is_admin(user.id):
            keyboard = [
                [KeyboardButton("تسجيل حضور 📝"), KeyboardButton("تسجيل انصراف 🚪")],
                [KeyboardButton("طلب سيجارة 🚬"), KeyboardButton("تقرير الحضور 📊")],
                [KeyboardButton("🔧 مدير العقوبات")]
            ]
        else:
            keyboard = [
                [KeyboardButton("تسجيل حضور 📝"), KeyboardButton("تسجيل انصراف 🚪")],
                [KeyboardButton("طلب سيجارة 🚬"), KeyboardButton("تقرير الحضور 📊")]
            ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"✅ مرحباً بك {full_name}!\n\n"
            "تم التحقق من هويتك بنجاح.\n"
            "يمكنك الآن استخدام البوت لإدارة حضورك.",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"خطأ في حفظ بيانات الموظف: {e}")
        await update.message.reply_text(
            "❌ حدث خطأ في حفظ بياناتك.\n"
            "يرجى المحاولة مرة أخرى أو التواصل مع الإدارة."
        )

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء المحادثة"""
    await update.message.reply_text(
        "❌ تم إلغاء العملية.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

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

def main():
    """بدء البوت"""
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not found in environment variables!")
        print("Please set your bot token in the Secrets tab.")
        return
    
    print("🚀 بدء بوت إدارة حضور الموظفين مع نظام العقوبات المبسط...")
    print("=" * 50)
    
    initialize_database_tables()
    
    application = Application.builder().token(BOT_TOKEN).build()

    # محادثة إدارة العقوبات
    penalty_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("penalty_manager", penalty_manager_command),
                      MessageHandler(filters.Text("🔧 مدير العقوبات"), start_penalty_manager)],
        states={
            PENALTY_MENU: [CallbackQueryHandler(handle_penalty_menu)],
            SELECT_EMPLOYEE_FOR_PENALTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_employee_for_penalty),
                CallbackQueryHandler(handle_employee_selection)
            ],
            SELECT_PENALTY_TYPE: [
                CallbackQueryHandler(select_penalty_type),
                CallbackQueryHandler(back_to_search, pattern="^back_to_search$")
            ],
            ENTER_PENALTY_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_penalty_details)],
            CONFIRM_PENALTY: [
                CallbackQueryHandler(edit_penalty_amount, pattern="^edit_amount$"),
                CallbackQueryHandler(back_to_types, pattern="^back_to_types$"),
                CallbackQueryHandler(back_to_confirm, pattern="^back_to_confirm$"),
                CallbackQueryHandler(confirm_add_penalty, pattern="^confirm_add"),
                CallbackQueryHandler(cancel_penalty, pattern="^cancel_pen_|^confirm_cancel_|^cancel_action$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_custom_amount)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )

    # إضافة المعالجات
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(penalty_conv_handler)
    application.add_handler(CommandHandler("list_penalties", list_penalties_command))
    application.add_handler(CommandHandler("cancel_penalty", cancel_penalty_command))
    
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    
    # معالجات للأزرار
    application.add_handler(CallbackQueryHandler(handle_employee_selection, pattern="^select_emp_"))
    application.add_handler(CallbackQueryHandler(cancel_penalty, pattern="^cancel_pen_|^confirm_cancel_|^cancel_action$"))
    
    application.add_error_handler(error_handler)
    
    print("\n✅ البوت يعمل الآن مع نظام العقوبات المبسط!")
    print("📱 أرسل /start للبوت للبدء")
    print("👑 المديرين يمكنهم استخدام:")
    print("   /penalty_manager - لفتح مدير العقوبات")
    print("   /list_penalties <رقم_الهاتف> - لعرض عقوبات موظف")
    print("   /cancel_penalty <معرف_العقوبة> - لإلغاء عقوبة")
    print("=" * 50)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()