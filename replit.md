# بوت إدارة حضور الموظفين / Employee Management Bot

## Overview
This Telegram bot aims to streamline employee attendance management, specifically for tracking breaks and leave requests. It enhances operational efficiency by providing a clear system for employees to log requests and for administrators to manage these requests and employee data. Key features include real-time notifications, a dynamic countdown timer for breaks, and a robust employee management system. The project seeks to improve workplace organization and communication regarding employee movements.

## User Preferences
The user wants the agent to:
- Be an interactive Telegram bot.
- Manage employee attendance, breaks, and leave requests.
- Provide real-time notifications to both employees and administrators.
- Feature an animated countdown timer with visual and dynamic colored indicators for breaks.
- Include a reminder bell/alert upon break expiration.
- Offer a comprehensive employee management system via a database.
- Use elegant and improved message formatting with icons and frames.
- Support Arabic language interface.

## System Architecture
The system is implemented as a Telegram bot using the `python-telegram-bot` library, designed for robust employee attendance and request management.

**UI/UX Decisions:**
- **Animated Countdown Timer & Progress Bar:** Visual, second-by-second updates for breaks with dynamic color changes (🟢🟡🟠🔴) based on remaining time.
- **Aesthetic Messaging:** Improved and elegant message formatting utilizing icons, frames, and boxes.
- **Interactive Elements:** Features like a "✅ رجعت للعمل" (Returned to Work) button to confirm employee return, triggering admin notifications.
- **Reminder System:** Audio/visual alerts upon break expiration.

**Technical Implementations & Feature Specifications:**
- **Employee Commands:** `/start`, `/help`, `/check_in`, `/check_out`, `/attendance_report`, `/smoke`, `/break`, `/leave`, `/vacation`, `/cancel`, `/my_id`.
- **Admin Commands:** `/list_employees`, `/add_employee`, `/remove_employee`, `/edit_details`, `/daily_report`, `/weekly_report`, `/list_admins`, `/add_admin` (super admin), `/remove_admin` (super admin).
- **Business Rules:**
    - **Work Hours:** 8:00 AM - 7:00 PM (9 regular hours + up to 2 hours overtime).
    - **Late Tolerance:** 15-minute grace period for check-in, followed by an automatic warning.
    - **Cigarette Breaks:** Max 6 per day, with a minimum 1.5-hour gap between each.
    - **Lunch Break:** One 30-minute break per day, deducted if work hours exceed 1 hour.
    - **Leave/Vacation Requests:** Require textual reasons; vacation requests also need an excuse and admin approval.
    - **Check-in/Check-out Prevention:** Duplicate entries on the same day are not allowed.
- **Admin Approval System:** Interactive accept/reject buttons for all types of employee requests, with real-time notifications.
- **Conversation Handlers:** Utilized for multi-step interactions (e.g., collecting reasons for leave/vacation).
- **Employee Verification:** Employees are verified by phone number, supporting various formats and share contact functionality.
- **Time Logging:** All timestamps are recorded in Jordan time (UTC+3) with DST compatibility.
- **Phone Number Normalization:** A `normalize_phone` function ensures consistent phone number formats across the system for unified search and management.
- **Admin Protection:** All administrative commands are restricted to authorized administrators.

**System Design Choices:**
- **Project Structure:** Clear separation of concerns with `bot.py` for core logic, `pyproject.toml` for dependencies, and `.gitignore` for version control.
- **Database Integration:** PostgreSQL is used for persistent storage across multiple tables: `employees`, `requests`, `daily_cigarettes`, `lunch_breaks`, `cigarette_times`, `attendance`, `warnings`, `absences`, and `admins`.
- **Admin Management:** Dynamic multi-admin system stored in database with two levels: Super Admins (hardcoded in ADMIN_IDS, cannot be removed) and Regular Admins (added via bot, can be removed).
- **Security:** API tokens are stored as secure environment variables, and SQL injection is prevented through parameterized queries.

## External Dependencies
- **Telegram Bot API:** Interfaced through the `python-telegram-bot` library for all bot functionalities.
- **PostgreSQL Database:** Utilized for all persistent data storage, including employee records, attendance logs, and request histories.

## التغييرات الأخيرة / Recent Changes
- 2025-10-31: **تحديثات كبيرة على نظام الدخان والصلاحيات**
  - **نظام الدخان الذكي بحسب اليوم:**
    - يوم الجمعة: 3 سجائر فقط (يوم عمل إضافي)
    - باقي الأيام: 6 سجائر
  - **الموافقة التلقائية على الدخان:** لم تعد هناك حاجة لموافقة المدير - الموافقة تلقائية مع الالتزام بالحد الأقصى اليومي وفترة 1.5 ساعة بين كل سيجارة
  - **إلغاء بريك الغداء في يوم الجمعة:** لا يُسمح بطلب بريك غداء في يوم الجمعة
  - **نظام صلاحيات المديرين المتقدم:**
    - مدير رئيسي (Super Admin): جميع الصلاحيات
    - مدير كامل الصلاحيات: مشاهدة + موافقة + إضافة/حذف
    - مدير مشاهدة فقط: مشاهدة التقارير والسجلات فقط بدون صلاحية الموافقة أو التعديل
  - **أمر /add_admin محدّث:** دعم إضافة مديرين بصلاحيات محدودة `/add_admin معرف view`
  - **أمر /list_admins محدّث:** عرض الصلاحيات التفصيلية لكل مدير
  - **إضافة حقل can_approve في جدول admins**
- 2025-10-30: **نظام إدارة المديرين الديناميكي**
  - إضافة جدول admins في قاعدة البيانات لتخزين قائمة المديرين
  - إضافة نظام المديرين الرئيسيين (Super Admins) والمديرين العاديين
  - إضافة أمر /add_admin للمديرين الرئيسيين لإضافة مديرين جدد
  - إضافة أمر /remove_admin للمديرين الرئيسيين لحذف المديرين العاديين
  - تحديث أمر /list_admins ليعرض المديرين من قاعدة البيانات مع نوع كل مدير
  - جميع المديرين الذين يُضافون عبر البوت يحصلون على إشعار فوري
  - الحماية: المديرين الرئيسيين لا يمكن حذفهم من قاعدة البيانات
- 2025-10-30: **حذف أمر /edit_employee القديم**
  - إزالة الأمر القديم /edit_employee الذي كان يتطلب إدخال يدوي معقد
  - الآن /edit_details هو الأمر الوحيد لتعديل تفاصيل الموظفين بشكل تفاعلي
- 2025-10-30: **نظام تعديل تفاصيل الموظف الكامل**
  - إضافة أمر جديد /edit_details لتعديل تفاصيل الموظفين بشكل تفاعلي
  - إضافة حقول جديدة: العمر، الوظيفة، القسم، تاريخ التوظيف
  - واجهة تفاعلية بالأزرار لاختيار الموظف ثم اختيار التفصيل المراد تعديله
  - إمكانية تعديل: الاسم، رقم الهاتف، العمر، الوظيفة، القسم، تاريخ التوظيف
  - التحقق من صحة البيانات (العمر بين 16-100، التاريخ بصيغة YYYY-MM-DD)
- 2025-10-30: **عرض اسم الموظف من قاعدة البيانات**
  - إضافة دالة get_employee_name() لجلب اسم الموظف من قاعدة البيانات
  - جميع الرسائل والإشعارات تعرض الآن اسم الموظف كما هو مخزن في قاعدة البيانات (الذي تم إدخاله عند الإضافة) بدلاً من اسم Telegram
  - تحديث جميع الأوامر: /start, /my_id, /smoke, /break, /leave, /vacation
  - إضافة أمرين جديدين: /my_id لعرض معرف Telegram و /list_admins لعرض المديرين
- 2025-10-30: **دعم عدة مديرين** / **Multi-Admin Support**
  - تحديث النظام ليدعم أكثر من مدير واحد في قائمة ADMIN_IDS
  - جميع الرسائل الإدارية والإشعارات تُرسل لجميع المديرين تلقائياً
  - جميع المديرين يمكنهم استخدام الأوامر الإدارية والموافقة على الطلبات
  - إصلاح شرط 1.5 ساعة بين السجائر (كان يوجد خلل في timezone)
- 2025-10-30: **إضافة نظام التقارير والجدولة التلقائية (المرحلة 2)**
  - إضافة أمر /attendance_report للموظفين لعرض تقرير حضورهم (آخر 7 أيام)
  - إضافة أمر /daily_report للمدير لعرض تقرير الحضور اليومي
  - إضافة أمر /weekly_report للمدير لعرض تقرير الحضور الأسبوعي
  - إضافة تقرير يومي تلقائي يُرسل لجميع المديرين في الساعة 7:00 مساءً (توقيت الأردن)
  - جميع التقارير تتضمن إحصائيات شاملة
- 2025-10-30: **إضافة نظام الحضور والانصراف الكامل (المرحلة 1)**
  - إضافة 3 جداول جديدة: attendance, warnings, absences
  - إضافة أمر /check_in لتسجيل الحضور مع كشف التأخير التلقائي
  - إضافة أمر /check_out لتسجيل الانصراف وحساب ساعات العمل والإضافي
  - نظام إنذارات تلقائي للتأخير بعد 15 دقيقة