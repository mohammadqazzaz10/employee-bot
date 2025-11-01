import os
import logging
from datetime import datetime, timedelta
import pytz
from telegram import (
    Update, 
    ReplyKeyboardMarkup, 
    ReplyKeyboardRemove,
    InlineKeyboardButton, 
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

from config import *
from database import db

# إعدادات التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# حالات المحادثة
NAME, PHONE, AGE, JOB, DEPARTMENT, HIRE_DATE = range(6)
REQUEST_REASON, VACATION_REASON, VACATION_START, VACATION_END = range(6, 10)
EDIT_EMPLOYEE, EDIT_FIELD, EDIT_VALUE = range(10, 13)

class EmployeeBot:
    def __init__(self):
        self.application = Application.builder().token(BOT_TOKEN).build()
        self.setup_handlers()
        self.jordan_tz = pytz.timezone(TIMEZONE)

    def setup_handlers(self):
        """إعداد معالجات الأوامر"""
        # أوامر الموظفين
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("my_id", self.my_id))
        self.application.add_handler(CommandHandler("check_in", self.check_in))
        self.application.add_handler(CommandHandler("check_out", self.check_out))
        self.application.add_handler(CommandHandler("attendance_report", self.attendance_report))
        self.application.add_handler(CommandHandler("smoke", self.smoke_break))
        self.application.add_handler(CommandHandler("break", self.lunch_break))
        
        # أوامر المديرين
        self.application.add_handler(CommandHandler("list_employees", self.list_employees))
        self.application.add_handler(CommandHandler("add_employee", self.add_employee))
        self.application.add_handler(CommandHandler("remove_employee", self.remove_employee))
        self.application.add_handler(CommandHandler("edit_details", self.edit_details))
        self.application.add_handler(CommandHandler("daily_report", self.daily_report))
        self.application.add_handler(CommandHandler("weekly_report", self.weekly_report))
        self.application.add_handler(CommandHandler("list_admins", self.list_admins))
        self.application.add_handler(CommandHandler("add_admin", self.add_admin))
        self.application.add_handler(CommandHandler("remove_admin", self.remove_admin))
        
        # معالجات المحادثة
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('leave', self.leave)],
            states={
                REQUEST_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.leave_reason)]
            },
            fallbacks=[CommandHandler('cancel', self.cancel)]
        )
        self.application.add_handler(conv_handler)
        
        # معالجات الاستعلامات
        self.application.add_handler(CallbackQueryHandler(self.button_handler))

    # === دوال المساعدة ===
    def format_message(self, title, content, message_type="info"):
        """تنسيق الرسائل بشكل أنيق"""
        icons = {
            "info": "ℹ️",
            "success": "✅", 
            "warning": "⚠️",
            "error": "❌",
            "question": "❓"
        }
        
        icon = icons.get(message_type, "ℹ️")
        
        message = f"""
{icon} **{title}**
────────────────
{content}
────────────────
        """
        return message

    def normalize_phone(self, phone):
        """توحيد تنسيق رقم الهاتف"""
        if phone.startswith('+'):
            return phone
        elif phone.startswith('00'):
            return '+' + phone[2:]
        elif phone.startswith('0'):
            return '+962' + phone[1:]
        else:
            return '+962' + phone

    def get_jordan_time(self):
        """الحصول على الوقت الحالي في الأردن"""
        return datetime.now(self.jordan_tz)

    # === أوامر الموظفين ===
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء البوت"""
        user = update.effective_user
        employee = db.get_employee(user.id)
        
        if employee:
            name = employee[2]  # اسم الموظف من قاعدة البيانات
            message = self.format_message(
                f"مرحباً مرة أخرى {name}!",
                "يمكنك استخدام الأوامر التالية:\n"
                "✅ /check_in - تسجيل الحضور\n"
                "🚪 /check_out - تسجيل الانصراف\n"
                "🚬 /smoke - طلب استراحة تدخين\n"
                "🍽️ /break - طلب استراحة غداء\n"
                "📊 /attendance_report - تقرير الحضور\n"
                "🆔 /my_id - عرض المعرف الخاص بك",
                "info"
            )
        else:
            message = self.format_message(
                "مرحباً! 👋",
                "يبدو أنك موظف جديد. يرجى التواصل مع المدير لإضافتك إلى النظام.",
                "info"
            )
        
        await update.message.reply_text(message, parse_mode='Markdown')

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """عرض التعليمات"""
        user = update.effective_user
        
        if db.is_admin(user.id):
            # رسالة المساعدة للمديرين
            help_text = """
🎯 **أوامر المديرين:**
────────────────
👥 /list_employees - عرض قائمة الموظفين
➕ /add_employee - إضافة موظف جديد
➖ /remove_employee - حذف موظف
✏️ /edit_details - تعديل بيانات الموظفين
📊 /daily_report - تقرير الحضور اليومي
📈 /weekly_report - تقرير الحضور الأسبوعي
👨‍💼 /list_admins - عرض قائمة المديرين
🔼 /add_admin - إضافة مدير جديد
🔽 /remove_admin - حذف مدير

🎯 **أوامر الموظفين:**
────────────────
✅ /check_in - تسجيل الحضور
🚪 /check_out - تسجيل الانصراف  
🚬 /smoke - طلب استراحة تدخين
🍽️ /break - طلب استراحة غداء
📅 /leave - طلب إجازة
📊 /attendance_report - تقرير الحضور
🆔 /my_id - عرض المعرف
            """
        else:
            # رسالة المساعدة للموظفين
            help_text = """
🎯 **أوامر الموظفين:**
────────────────
✅ /check_in - تسجيل الحضور
🚪 /check_out - تسجيل الانصراف
🚬 /smoke - طلب استراحة تدخين
🍽️ /break - طلب استراحة غداء
📅 /leave - طلب إجازة
📊 /attendance_report - تقرير الحضور
🆔 /my_id - عرض المعرف
            """
        
        await update.message.reply_text(
            self.format_message("قائمة الأوامر", help_text, "info"),
            parse_mode='Markdown'
        )

    async def my_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """عرض معرف المستخدم"""
        user = update.effective_user
        employee_name = db.get_employee_name(user.id)
        
        message = self.format_message(
            "المعلومات الشخصية",
            f"🆔 معرفك: `{user.id}`\n"
            f"👤 اسمك: {employee_name or 'غير مسجل'}",
            "info"
        )
        
        await update.message.reply_text(message, parse_mode='Markdown')

    async def check_in(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """تسجيل الحضور"""
        user = update.effective_user
        employee = db.get_employee(user.id)
        
        if not employee:
            await update.message.reply_text(
                self.format_message("خطأ", "لم يتم العثور على بياناتك في النظام", "error"),
                parse_mode='Markdown'
            )
            return
        
        employee_id = employee[0]
        current_time = self.get_jordan_time()
        
        success, message = db.check_in(employee_id, current_time)
        
        if success:
            # إرسال إشعار للمديرين
            admins = db.list_admins()
            for admin in admins:
                try:
                    await context.bot.send_message(
                        admin[1],  # telegram_id
                        self.format_message(
                            "تسجيل حضور جديد",
                            f"👤 الموظف: {employee[2]}\n"
                            f"⏰ الوقت: {current_time.strftime('%H:%M:%S')}",
                            "info"
                        ),
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    print(f"خطأ في إرسال إشعار للمدير: {e}")
        
        await update.message.reply_text(
            self.format_message("تسجيل الحضور", message, "success" if success else "error"),
            parse_mode='Markdown'
        )

    async def check_out(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """تسجيل الانصراف"""
        user = update.effective_user
        employee = db.get_employee(user.id)
        
        if not employee:
            await update.message.reply_text(
                self.format_message("خطأ", "لم يتم العثور على بياناتك في النظام", "error"),
                parse_mode='Markdown'
            )
            return
        
        employee_id = employee[0]
        current_time = self.get_jordan_time()
        
        success, message = db.check_out(employee_id, current_time)
        
        if success:
            # إرسال إشعار للمديرين
            admins = db.list_admins()
            for admin in admins:
                try:
                    await context.bot.send_message(
                        admin[1],
                        self.format_message(
                            "تسجيل انصراف جديد",
                            f"👤 الموظف: {employee[2]}\n"
                            f"⏰ الوقت: {current_time.strftime('%H:%M:%S')}",
                            "info"
                        ),
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    print(f"خطأ في إرسال إشعار للمدير: {e}")
        
        await update.message.reply_text(
            self.format_message("تسجيل الانصراف", message, "success" if success else "error"),
            parse_mode='Markdown'
        )

    async def attendance_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """تقرير حضور الموظف"""
        user = update.effective_user
        employee = db.get_employee(user.id)
        
        if not employee:
            await update.message.reply_text(
                self.format_message("خطأ", "لم يتم العثور على بياناتك في النظام", "error"),
                parse_mode='Markdown'
            )
            return
        
        employee_id = employee[0]
        report = db.get_attendance_report(employee_id)
        
        if not report:
            await update.message.reply_text(
                self.format_message("تقرير الحضور", "لا توجد سجلات حضور في الأسبوع الماضي", "info"),
                parse_mode='Markdown'
            )
            return
        
        report_text = "📊 **تقرير حضورك للأسبوع الماضي:**\n\n"
        for record in report:
            date, check_in, check_out, work_hours, late_minutes, overtime_minutes = record
            
            check_in_str = check_in.strftime('%H:%M') if check_in else "---"
            check_out_str = check_out.strftime('%H:%M') if check_out else "---"
            
            report_text += f"📅 **{date.strftime('%Y-%m-%d')}:**\n"
            report_text += f"   ⏰ دخول: {check_in_str}\n"
            report_text += f"   🚪 خروج: {check_out_str}\n"
            report_text += f"   ⏱️ ساعات: {work_hours:.1f}\n"
            
            if late_minutes > 0:
                report_text += f"   ⚠️ تأخير: {late_minutes:.0f} دقيقة\n"
            if overtime_minutes > 0:
                report_text += f"   💪 إضافي: {overtime_minutes:.0f} دقيقة\n"
            
            report_text += "\n"
        
        await update.message.reply_text(
            self.format_message("تقرير الحضور الشخصي", report_text, "info"),
            parse_mode='Markdown'
        )

    async def smoke_break(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """طلب استراحة تدخين"""
        user = update.effective_user
        employee = db.get_employee(user.id)
        
        if not employee:
            await update.message.reply_text(
                self.format_message("خطأ", "لم يتم العثور على بياناتك في النظام", "error"),
                parse_mode='Markdown'
            )
            return
        
        employee_id = employee[0]
        can_smoke, message = db.can_take_smoke_break(employee_id)
        
        if not can_smoke:
            await update.message.reply_text(
                self.format_message("طلب استراحة تدخين", message, "error"),
                parse_mode='Markdown'
            )
            return
        
        # الموافقة التلقائية على استراحات التدخين
        current_time = self.get_jordan_time()
        break_end = current_time + timedelta(minutes=SMOKE_BREAK_DURATION)
        
        success = db.add_smoke_break(employee_id, current_time, break_end)
        
        if success:
            # إرسال إشعار للمديرين
            admins = db.list_admins()
            for admin in admins:
                try:
                    await context.bot.send_message(
                        admin[1],
                        self.format_message(
                            "استراحة تدخين جديدة",
                            f"👤 الموظف: {employee[2]}\n"
                            f"⏰ البداية: {current_time.strftime('%H:%M:%S')}\n"
                            f"⏰ النهاية: {break_end.strftime('%H:%M:%S')}",
                            "info"
                        ),
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    print(f"خطأ في إرسال إشعار للمدير: {e}")
            
            await update.message.reply_text(
                self.format_message(
                    "استراحة تدخين",
                    f"✅ تمت الموافقة على استراحة التدخين\n"
                    f"⏰ المدة: {SMOKE_BREAK_DURATION} دقيقة\n"
                    f"⏰ العودة: {break_end.strftime('%H:%M:%S')}",
                    "success"
                ),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                self.format_message("خطأ", "حدث خطأ في تسجيل الاستراحة", "error"),
                parse_mode='Markdown'
            )

    async def lunch_break(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """طلب استراحة غداء"""
        user = update.effective_user
        employee = db.get_employee(user.id)
        
        if not employee:
            await update.message.reply_text(
                self.format_message("خطأ", "لم يتم العثور على بياناتك في النظام", "error"),
                parse_mode='Markdown'
            )
            return
        
        # الموافقة التلقائية على استراحات الغداء
        current_time = self.get_jordan_time()
        break_end = current_time + timedelta(minutes=LUNCH_BREAK_DURATION)
        
        # إرسال إشعار للمديرين
        admins = db.list_admins()
        for admin in admins:
            try:
                await context.bot.send_message(
                    admin[1],
                    self.format_message(
                        "استراحة غداء جديدة",
                        f"👤 الموظف: {employee[2]}\n"
                        f"⏰ البداية: {current_time.strftime('%H:%M:%S')}\n"
                        f"⏰ النهاية: {break_end.strftime('%H:%M:%S')}",
                        "info"
                    ),
                    parse_mode='Markdown'
                )
            except Exception as e:
                print(f"خطأ في إرسال إشعار للمدير: {e}")
        
        await update.message.reply_text(
            self.format_message(
                "استراحة غداء",
                f"✅ تمت الموافقة على استراحة الغداء\n"
                f"⏰ المدة: {LUNCH_BREAK_DURATION} دقيقة\n"
                f"⏰ العودة: {break_end.strftime('%H:%M:%S')}",
                "success"
            ),
            parse_mode='Markdown'
        )

    # === أوامر المديرين ===
    async def list_employees(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """عرض قائمة الموظفين"""
        user = update.effective_user
        
        if not db.is_admin(user.id):
            await update.message.reply_text(
                self.format_message("خطأ", "ليس لديك صلاحية الوصول لهذا الأمر", "error"),
                parse_mode='Markdown'
            )
            return
        
        employees = db.list_employees()
        
        if not employees:
            await update.message.reply_text(
                self.format_message("قائمة الموظفين", "لا يوجد موظفين مسجلين", "info"),
                parse_mode='Markdown'
            )
            return
        
        employees_text = "👥 **قائمة الموظفين:**\n\n"
        for emp in employees:
            employees_text += f"🆔 **{emp[0]}** - {emp[2]}\n"
            employees_text += f"   📞 {emp[3] or 'لا يوجد'}\n"
            employees_text += f"   👨‍💼 {emp[5] or 'غير محدد'}\n"
            employees_text += f"   🏢 {emp[6] or 'غير محدد'}\n\n"
        
        await update.message.reply_text(
            self.format_message("قائمة الموظفين", employees_text, "info"),
            parse_mode='Markdown'
        )

    async def add_employee(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """إضافة موظف جديد"""
        user = update.effective_user
        
        if not db.is_admin(user.id):
            await update.message.reply_text(
                self.format_message("خطأ", "ليس لديك صلاحية الوصول لهذا الأمر", "error"),
                parse_mode='Markdown'
            )
            return
        
        if not context.args:
            await update.message.reply_text(
                self.format_message(
                    "إضافة موظف",
                    "استخدم الأمر بالشكل التالي:\n"
                    "`/add_employee معرف_التليجرام الاسم رقم_الهاتف العمر الوظيفة القسم`\n\n"
                    "مثال:\n"
                    "`/add_employee 123456789 أحمد 0791234567 25 مبرمج IT`",
                    "info"
                ),
                parse_mode='Markdown'
            )
            return
        
        if len(context.args) < 3:
            await update.message.reply_text(
                self.format_message("خطأ", "يجب إدخال المعرف والاسم ورقم الهاتف على الأقل", "error"),
                parse_mode='Markdown'
            )
            return
        
        try:
            telegram_id = int(context.args[0])
            name = context.args[1]
            phone = self.normalize_phone(context.args[2])
            age = int(context.args[3]) if len(context.args) > 3 else None
            job_title = context.args[4] if len(context.args) > 4 else None
            department = context.args[5] if len(context.args) > 5 else None
            
            # التحقق من عدم وجود الموظف مسبقاً
            if db.get_employee(telegram_id):
                await update.message.reply_text(
                    self.format_message("خطأ", "الموظف مسجل مسبقاً في النظام", "error"),
                    parse_mode='Markdown'
                )
                return
            
            employee_id = db.add_employee(telegram_id, name, phone, age, job_title, department)
            
            if employee_id:
                # إرسال رسالة ترحيب للموظف الجديد
                try:
                    await context.bot.send_message(
                        telegram_id,
                        self.format_message(
                            "مرحباً بك في النظام! 🎉",
                            f"تم إضافتك إلى نظام الحضور بنجاح\n\n"
                            f"👤 الاسم: {name}\n"
                            f"📞 الهاتف: {phone}\n"
                            f"👨‍💼 الوظيفة: {job_title or 'غير محدد'}\n"
                            f"🏢 القسم: {department or 'غير محدد'}\n\n"
                            f"استخدم /start لبدء استخدام البوت",
                            "success"
                        ),
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    print(f"خطأ في إرسال رسالة الترحيب: {e}")
                
                await update.message.reply_text(
                    self.format_message(
                        "تمت الإضافة بنجاح ✅",
                        f"تم إضافة الموظف بنجاح:\n\n"
                        f"👤 الاسم: {name}\n"
                        f"🆔 المعرف: {telegram_id}\n"
                        f"📞 الهاتف: {phone}",
                        "success"
                    ),
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    self.format_message("خطأ", "حدث خطأ في إضافة الموظف", "error"),
                    parse_mode='Markdown'
                )
                
        except ValueError:
            await update.message.reply_text(
                self.format_message("خطأ", "المعرف يجب أن يكون رقماً", "error"),
                parse_mode='Markdown'
            )
        except Exception as e:
            await update.message.reply_text(
                self.format_message("خطأ", f"حدث خطأ: {e}", "error"),
                parse_mode='Markdown'
            )

    async def remove_employee(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """حذف موظف"""
        user = update.effective_user
        
        if not db.is_admin(user.id):
            await update.message.reply_text(
                self.format_message("خطأ", "ليس لديك صلاحية الوصول لهذا الأمر", "error"),
                parse_mode='Markdown'
            )
            return
        
        if not context.args:
            await update.message.reply_text(
                self.format_message(
                    "حذف موظف",
                    "استخدم الأمر بالشكل التالي:\n"
                    "`/remove_employee معرف_التليجرام`\n\n"
                    "مثال:\n"
                    "`/remove_employee 123456789`",
                    "info"
                ),
                parse_mode='Markdown'
            )
            return
        
        try:
            telegram_id = int(context.args[0])
            employee = db.get_employee(telegram_id)
            
            if not employee:
                await update.message.reply_text(
                    self.format_message("خطأ", "لم يتم العثور على الموظف", "error"),
                    parse_mode='Markdown'
                )
                return
            
            success = db.remove_employee(telegram_id)
            
            if success:
                await update.message.reply_text(
                    self.format_message(
                        "تم الحذف بنجاح ✅",
                        f"تم حذف الموظف:\n{employee[2]}",
                        "success"
                    ),
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    self.format_message("خطأ", "حدث خطأ في حذف الموظف", "error"),
                    parse_mode='Markdown'
                )
                
        except ValueError:
            await update.message.reply_text(
                self.format_message("خطأ", "المعرف يجب أن يكون رقماً", "error"),
                parse_mode='Markdown'
            )

    async def daily_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """تقرير الحضور اليومي"""
        user = update.effective_user
        
        if not db.is_admin(user.id):
            await update.message.reply_text(
                self.format_message("خطأ", "ليس لديك صلاحية الوصول لهذا الأمر", "error"),
                parse_mode='Markdown'
            )
            return
        
        report = db.get_daily_report()
        
        if not report:
            await update.message.reply_text(
                self.format_message("التقرير اليومي", "لا توجد سجلات حضور اليوم", "info"),
                parse_mode='Markdown'
            )
            return
        
        report_text = "📊 **تقرير الحضور اليومي:**\n\n"
        present_count = 0
        late_count = 0
        
        for record in report:
            name, check_in, check_out, work_hours, late_minutes, overtime_minutes = record
            
            check_in_str = check_in.strftime('%H:%M') if check_in else "---"
            check_out_str = check_out.strftime('%H:%M') if check_out else "---"
            
            report_text += f"👤 **{name}:**\n"
            report_text += f"   ⏰ دخول: {check_in_str}\n"
            report_text += f"   🚪 خروج: {check_out_str}\n"
            report_text += f"   ⏱️ ساعات: {work_hours:.1f}\n"
            
            if check_in:
                present_count += 1
            
            if late_minutes > 0:
                report_text += f"   ⚠️ تأخير: {late_minutes:.0f} دقيقة\n"
                late_count += 1
            
            if overtime_minutes > 0:
                report_text += f"   💪 إضافي: {overtime_minutes:.0f} دقيقة\n"
            
            report_text += "\n"
        
        # الإحصائيات
        stats = f"📈 **الإحصائيات:**\n"
        stats += f"   ✅ الحاضرين: {present_count}\n"
        stats += f"   ⚠️ المتأخرين: {late_count}\n"
        
        await update.message.reply_text(
            self.format_message("التقرير اليومي", report_text + stats, "info"),
            parse_mode='Markdown'
        )

    async def weekly_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """تقرير الحضور الأسبوعي"""
        user = update.effective_user
        
        if not db.is_admin(user.id):
            await update.message.reply_text(
                self.format_message("خطأ", "ليس لديك صلاحية الوصول لهذا الأمر", "error"),
                parse_mode='Markdown'
            )
            return
        
        report = db.get_weekly_report()
        
        if not report:
            await update.message.reply_text(
                self.format_message("التقرير الأسبوعي", "لا توجد سجلات حضور هذا الأسبوع", "info"),
                parse_mode='Markdown'
            )
            return
        
        report_text = "📈 **تقرير الحضور الأسبوعي:**\n\n"
        
        for record in report:
            name, days_worked, avg_hours, total_late, total_overtime = record
            
            report_text += f"👤 **{name}:**\n"
            report_text += f"   📅 أيام العمل: {days_worked or 0}\n"
            report_text += f"   ⏱️ متوسط الساعات: {avg_hours or 0:.1f}\n"
            
            if total_late:
                report_text += f"   ⚠️ إجمالي التأخير: {total_late:.0f} دقيقة\n"
            
            if total_overtime:
                report_text += f"   💪 إجمالي الإضافي: {total_overtime:.0f} دقيقة\n"
            
            report_text += "\n"
        
        await update.message.reply_text(
            self.format_message("التقرير الأسبوعي", report_text, "info"),
            parse_mode='Markdown'
        )

    async def list_admins(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """عرض قائمة المديرين"""
        user = update.effective_user
        
        if not db.is_admin(user.id):
            await update.message.reply_text(
                self.format_message("خطأ", "ليس لديك صلاحية الوصول لهذا الأمر", "error"),
                parse_mode='Markdown'
            )
            return
        
        admins = db.list_admins()
        
        if not admins:
            await update.message.reply_text(
                self.format_message("قائمة المديرين", "لا يوجد مديرين مسجلين", "info"),
                parse_mode='Markdown'
            )
            return
        
        admins_text = "👨‍💼 **قائمة المديرين:**\n\n"
        for admin in admins:
            admin_type = "🟢 مدير رئيسي" if admin[4] else "🔵 مدير عادي"
            approve_status = "✅ يمتلك صلاحية الموافقة" if admin[3] else "❌ لا يمتلك صلاحية الموافقة"
            
            admins_text += f"{admin_type} - {admin[2]}\n"
            admins_text += f"   🆔 المعرف: {admin[1]}\n"
            admins_text += f"   {approve_status}\n\n"
        
        await update.message.reply_text(
            self.format_message("قائمة المديرين", admins_text, "info"),
            parse_mode='Markdown'
        )

    async def add_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """إضافة مدير جديد"""
        user = update.effective_user
        
        if not db.is_super_admin(user.id):
            await update.message.reply_text(
                self.format_message("خطأ", "ليس لديك صلاحية إضافة مديرين", "error"),
                parse_mode='Markdown'
            )
            return
        
        if not context.args:
            await update.message.reply_text(
                self.format_message(
                    "إضافة مدير",
                    "استخدم الأمر بالشكل التالي:\n"
                    "`/add_admin معرف_التليجرام الاسم`\n\n"
                    "مثال:\n"
                    "`/add_admin 123456789 أحمد`",
                    "info"
                ),
                parse_mode='Markdown'
            )
            return
        
        try:
            telegram_id = int(context.args[0])
            name = ' '.join(context.args[1:]) if len(context.args) > 1 else "مدير"
            
            # التحقق من عدم وجود المدير مسبقاً
            if db.is_admin(telegram_id):
                await update.message.reply_text(
                    self.format_message("خطأ", "المدير مسجل مسبقاً في النظام", "error"),
                    parse_mode='Markdown'
                )
                return
            
            success = db.add_admin(telegram_id, name, added_by=user.id)
            
            if success:
                await update.message.reply_text(
                    self.format_message(
                        "تمت الإضافة بنجاح ✅",
                        f"تم إضافة المدير بنجاح:\n\n"
                        f"👤 الاسم: {name}\n"
                        f"🆔 المعرف: {telegram_id}",
                        "success"
                    ),
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    self.format_message("خطأ", "حدث خطأ في إضافة المدير", "error"),
                    parse_mode='Markdown'
                )
                
        except ValueError:
            await update.message.reply_text(
                self.format_message("خطأ", "المعرف يجب أن يكون رقماً", "error"),
                parse_mode='Markdown'
            )

    async def remove_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """حذف مدير"""
        user = update.effective_user
        
        if not db.is_super_admin(user.id):
            await update.message.reply_text(
                self.format_message("خطأ", "ليس لديك صلاحية حذف مديرين", "error"),
                parse_mode='Markdown'
            )
            return
        
        if not context.args:
            await update.message.reply_text(
                self.format_message(
                    "حذف مدير",
                    "استخدم الأمر بالشكل التالي:\n"
                    "`/remove_admin معرف_التليجرام`\n\n"
                    "مثال:\n"
                    "`/remove_admin 123456789`",
                    "info"
                ),
                parse_mode='Markdown'
            )
            return
        
        try:
            telegram_id = int(context.args[0])
            
            # منع حذف المدير الرئيسي
            if db.is_super_admin(telegram_id):
                await update.message.reply_text(
                    self.format_message("خطأ", "لا يمكن حذف المدير الرئيسي", "error"),
                    parse_mode='Markdown'
                )
                return
            
            success = db.remove_admin(telegram_id)
            
            if success:
                await update.message.reply_text(
                    self.format_message("تم الحذف بنجاح ✅", "تم حذف المدير بنجاح", "success"),
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    self.format_message("خطأ", "حدث خطأ في حذف المدير", "error"),
                    parse_mode='Markdown'
                )
                
        except ValueError:
            await update.message.reply_text(
                self.format_message("خطأ", "المعرف يجب أن يكون رقماً", "error"),
                parse_mode='Markdown'
            )

    # === دوال المحادثة ===
    async def leave(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء طلب إجازة"""
        user = update.effective_user
        employee = db.get_employee(user.id)
        
        if not employee:
            await update.message.reply_text(
                self.format_message("خطأ", "لم يتم العثور على بياناتك في النظام", "error"),
                parse_mode='Markdown'
            )
            return ConversationHandler.END
        
        await update.message.reply_text(
            self.format_message(
                "طلب إجازة",
                "يرجى كتابة سبب طلب الإجازة:",
                "question"
            ),
            parse_mode='Markdown'
        )
        
        return REQUEST_REASON

    async def leave_reason(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة سبب الإجازة"""
        reason = update.message.text
        user = update.effective_user
        employee = db.get_employee(user.id)
        
        # إضافة طلب الإجازة
        request_id = db.add_request(employee[0], "leave", reason)
        
        if request_id:
            # إرسال إشعار للمديرين
            admins = db.list_admins()
            for admin in admins:
                if db.can_approve_requests(admin[1]):
                    try:
                        keyboard = [
                            [
                                InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{request_id}"),
                                InlineKeyboardButton("❌ رفض", callback_data=f"reject_{request_id}")
                            ]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        
                        await context.bot.send_message(
                            admin[1],
                            self.format_message(
                                "طلب إجازة جديد",
                                f"👤 الموظف: {employee[2]}\n"
                                f"📝 السبب: {reason}",
                                "info"
                            ),
                            reply_markup=reply_markup,
                            parse_mode='Markdown'
                        )
                    except Exception as e:
                        print(f"خطأ في إرسال إشعار للمدير: {e}")
            
            await update.message.reply_text(
                self.format_message(
                    "تم إرسال الطلب ✅",
                    "تم إرسال طلب الإجازة للمديرين للموافقة عليه",
                    "success"
                ),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                self.format_message("خطأ", "حدث خطأ في إرسال الطلب", "error"),
                parse_mode='Markdown'
            )
        
        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """إلغاء المحادثة"""
        await update.message.reply_text(
            self.format_message("تم الإلغاء", "تم إلغاء العملية", "info"),
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    # === معالج الأزرار ===
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة ضغطات الأزرار"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data.startswith('approve_'):
            request_id = int(data.split('_')[1])
            await self.approve_request(query, context, request_id)
        elif data.startswith('reject_'):
            request_id = int(data.split('_')[1])
            await self.reject_request(query, context, request_id)

    async def approve_request(self, query, context, request_id):
        """الموافقة على طلب"""
        user = query.from_user
        
        if not db.can_approve_requests(user.id):
            await query.edit_message_text(
                self.format_message("خطأ", "ليس لديك صلاحية الموافقة على الطلبات", "error"),
                parse_mode='Markdown'
            )
            return
        
        success = db.update_request_status(request_id, "approved", user.id)
        
        if success:
            await query.edit_message_text(
                self.format_message("تمت الموافقة ✅", "تمت الموافقة على الطلب بنجاح", "success"),
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                self.format_message("خطأ", "حدث خطأ في الموافقة على الطلب", "error"),
                parse_mode='Markdown'
            )

    async def reject_request(self, query, context, request_id):
        """رفض طلب"""
        user = query.from_user
        
        if not db.can_approve_requests(user.id):
            await query.edit_message_text(
                self.format_message("خطأ", "ليس لديك صلاحية رفض الطلبات", "error"),
                parse_mode='Markdown'
            )
            return
        
        success = db.update_request_status(request_id, "rejected", user.id)
        
        if success:
            await query.edit_message_text(
                self.format_message("تم الرفض ❌", "تم رفض الطلب بنجاح", "info"),
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                self.format_message("خطأ", "حدث خطأ في رفض الطلب", "error"),
                parse_mode='Markdown'
            )

    async def edit_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """تعديل بيانات الموظف"""
        user = update.effective_user
        
        if not db.is_admin(user.id):
            await update.message.reply_text(
                self.format_message("خطأ", "ليس لديك صلاحية الوصول لهذا الأمر", "error"),
                parse_mode='Markdown'
            )
            return
        
        if not context.args:
            await update.message.reply_text(
                self.format_message(
                    "تعديل بيانات الموظف",
                    "استخدم الأمر بالشكل التالي:\n"
                    "`/edit_details معرف_التليجرام الحقل القيمة`\n\n"
                    "الحقول المتاحة:\n"
                    "• name - الاسم\n"
                    "• phone_number - رقم الهاتف\n"
                    "• age - العمر\n"
                    "• job_title - الوظيفة\n"
                    "• department - القسم\n\n"
                    "مثال:\n"
                    "`/edit_details 123456789 job_title مدير مشاريع`",
                    "info"
                ),
                parse_mode='Markdown'
            )
            return
        
        if len(context.args) < 3:
            await update.message.reply_text(
                self.format_message("خطأ", "يجب إدخال المعرف والحقل والقيمة", "error"),
                parse_mode='Markdown'
            )
            return
        
        try:
            telegram_id = int(context.args[0])
            field = context.args[1]
            value = ' '.join(context.args[2:])
            
            # التحقق من صحة الحقل
            valid_fields = ['name', 'phone_number', 'age', 'job_title', 'department']
            if field not in valid_fields:
                await update.message.reply_text(
                    self.format_message("خطأ", f"الحقل {field} غير صالح", "error"),
                    parse_mode='Markdown'
                )
                return
            
            # التحقق من وجود الموظف
            employee = db.get_employee(telegram_id)
            if not employee:
                await update.message.reply_text(
                    self.format_message("خطأ", "لم يتم العثور على الموظف", "error"),
                    parse_mode='Markdown'
                )
                return
            
            success = db.update_employee(telegram_id, field, value)
            
            if success:
                await update.message.reply_text(
                    self.format_message(
                        "تم التعديل بنجاح ✅",
                        f"تم تعديل {field} للموظف {employee[2]}",
                        "success"
                    ),
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    self.format_message("خطأ", "حدث خطأ في التعديل", "error"),
                    parse_mode='Markdown'
                )
                
        except ValueError:
            await update.message.reply_text(
                self.format_message("خطأ", "المعرف يجب أن يكون رقماً", "error"),
                parse_mode='Markdown'
            )

    def run(self):
        """تشغيل البوت"""
        print("🚀 بدأ تشغيل بوت إدارة الحضور...")
        self.application.run_polling()

if __name__ == '__main__':
    bot = EmployeeBot()
    bot.run()