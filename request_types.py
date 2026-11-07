"""Branch-scoped configurable employee request buttons with timed approvals."""
import logging
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
PREFIX = "requesttype:"


def _db():
    import bot
    return bot.get_db_connection()


def list_request_types(branch_id, active_only=False):
    conn = None
    try:
        conn = _db()
        with conn.cursor() as cur:
            sql = """SELECT id, code, label, duration_minutes, is_active
                     FROM branch_request_types WHERE branch_id=%s"""
            if active_only:
                sql += " AND is_active=TRUE"
            sql += " ORDER BY id"
            cur.execute(sql, (branch_id,))
            return cur.fetchall()
    except Exception:
        logger.exception("Could not list branch request types")
        return []
    finally:
        if conn:
            conn.close()


def get_request_type(branch_id, type_id):
    conn = None
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute("""SELECT id, code, label, duration_minutes, is_active
                           FROM branch_request_types
                           WHERE branch_id=%s AND id=%s""", (branch_id, type_id))
            return cur.fetchone()
    except Exception:
        logger.exception("Could not load request type")
        return None
    finally:
        if conn:
            conn.close()


def get_request_type_by_code(branch_id, code):
    conn = None
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute("""SELECT id, code, label, duration_minutes, is_active
                           FROM branch_request_types
                           WHERE branch_id=%s AND code=%s""", (branch_id, code))
            return cur.fetchone()
    except Exception:
        logger.exception("Could not resolve request type code")
        return None
    finally:
        if conn:
            conn.close()


def get_employee_buttons(branch_id):
    return [row[2] for row in list_request_types(branch_id, active_only=True)]


def _can_manage(user_id, branch_id):
    import bot
    if bot.is_super_admin(user_id):
        return True
    conn = None
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute("""SELECT 1 FROM admin_branch_roles
                           WHERE telegram_id=%s AND branch_id=%s
                             AND role IN ('branch_manager','supervisor')""",
                        (user_id, branch_id))
            return bool(cur.fetchone())
    except Exception:
        logger.exception("Could not verify branch manager role")
        return False
    finally:
        if conn:
            conn.close()


async def send_to_branch_admins(context, branch_id, message, reply_markup=None):
    conn = None
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute("""SELECT DISTINCT a.telegram_id
                           FROM admins a
                           LEFT JOIN admin_branch_roles r ON r.telegram_id=a.telegram_id
                           WHERE a.is_super_admin=TRUE
                              OR (r.branch_id=%s AND r.role IN ('branch_manager','supervisor'))
                           ORDER BY a.telegram_id""", (branch_id,))
            admin_ids = [row[0] for row in cur.fetchall()]
    except Exception:
        logger.exception("Could not find administrators for branch %s", branch_id)
        return
    finally:
        if conn:
            conn.close()
    for admin_id in admin_ids:
        try:
            await context.bot.send_message(
                chat_id=admin_id, text=message, reply_markup=reply_markup
            )
        except Exception:
            logger.exception("Could not notify branch administrator %s", admin_id)


def _save_new(branch_id, label, duration, user_id):
    conn = None
    try:
        conn = _db()
        with conn.cursor() as cur:
            temporary_code = f"new-{uuid.uuid4().hex[:16]}"
            cur.execute("""INSERT INTO branch_request_types
                           (branch_id, code, label, duration_minutes, is_active, created_by, updated_by)
                           VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                           RETURNING id""",
                        (branch_id, temporary_code, label, duration, user_id, user_id))
            type_id = cur.fetchone()[0]
            code = f"custom-{type_id}"
            cur.execute("UPDATE branch_request_types SET code=%s WHERE id=%s", (code, type_id))
            cur.execute("""INSERT INTO branch_request_types_audit
                           (branch_id, request_type_id, action, changed_by, new_value)
                           VALUES (%s,%s,'created',%s,%s)""",
                        (branch_id, type_id, user_id, f"{label} | {duration} minutes"))
        conn.commit()
        return type_id
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Could not create custom request type")
        return None
    finally:
        if conn:
            conn.close()


def _update_type(branch_id, type_id, user_id, *, duration=None, active=None):
    conn = None
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM branch_request_types WHERE id=%s AND branch_id=%s FOR UPDATE",
                        (type_id, branch_id))
            if not cur.fetchone():
                return False
            if duration is not None:
                cur.execute("""UPDATE branch_request_types SET duration_minutes=%s,
                               updated_by=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                            (duration, user_id, type_id))
            if active is not None:
                cur.execute("""UPDATE branch_request_types SET is_active=%s,
                               updated_by=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                            (active, user_id, type_id))
            cur.execute("""INSERT INTO branch_request_types_audit
                           (branch_id, request_type_id, action, changed_by, new_value)
                           VALUES (%s,%s,%s,%s,%s)""",
                        (branch_id, type_id, "duration" if duration is not None else "active",
                         user_id, str(duration if duration is not None else active)))
        conn.commit()
        return True
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Could not update request type")
        return False
    finally:
        if conn:
            conn.close()


async def request_types_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    from branch_settings import _resolve_branch
    user_id = update.effective_user.id
    if not bot.is_admin(user_id):
        await update.effective_message.reply_text("⛔ هذه الإدارة للمديرين فقط.")
        return
    requested = None
    if context.args:
        try:
            requested = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text("الاستخدام: /request_types أو /request_types BRANCH_ID")
            return
    branch_id = _resolve_branch(user_id, requested)
    if not branch_id or not _can_manage(user_id, branch_id):
        await update.effective_message.reply_text("⛔ لا تملك صلاحية إدارة أزرار هذا الفرع. تأكد من تطبيق migration 001 و002.")
        return
    context.user_data["managed_request_branch"] = branch_id
    await _show_types(update.effective_message, branch_id)


async def _show_types(message, branch_id):
    rows = list_request_types(branch_id)
    keyboard = []
    for type_id, code, label, duration, active in rows:
        state = "مفعّل" if active else "معطّل"
        keyboard.append([
            InlineKeyboardButton(f"{label} · {duration} د · {state}", callback_data=f"{PREFIX}{branch_id}:{type_id}:duration"),
            InlineKeyboardButton("تعطيل" if active else "تفعيل", callback_data=f"{PREFIX}{branch_id}:{type_id}:toggle"),
        ])
    keyboard.append([InlineKeyboardButton("➕ إنشاء زر استئذان", callback_data=f"{PREFIX}{branch_id}:new:create")])
    await message.reply_text("⚙️ أنواع الاستئذان في هذا الفرع\nاختر نوعاً لتعديل مدته أو تفعيله/تعطيله.", reply_markup=InlineKeyboardMarkup(keyboard))


async def request_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    q = update.callback_query
    await q.answer()
    try:
        _, branch_raw, type_raw, action = q.data.split(":")
        branch_id = int(branch_raw)
    except (ValueError, AttributeError):
        await q.edit_message_text("بيانات الزر غير صالحة.")
        return
    if not bot.is_admin(q.from_user.id) or not _can_manage(q.from_user.id, branch_id):
        await q.edit_message_text("⛔ لا تملك صلاحية هذا الفرع.")
        return
    context.user_data["managed_request_branch"] = branch_id
    if type_raw == "new" and action == "create":
        context.user_data["new_request_type_state"] = "label"
        await q.message.reply_text("✏️ أرسل اسم زر الاستئذان الجديد (مثال: استئذان شخصي).")
        return
    try:
        type_id = int(type_raw)
    except ValueError:
        await q.edit_message_text("نوع الاستئذان غير صالح.")
        return
    current = get_request_type(branch_id, type_id)
    if not current:
        await q.edit_message_text("نوع الاستئذان غير موجود.")
        return
    if action == "toggle":
        if not _update_type(branch_id, type_id, q.from_user.id, active=not current[4]):
            await q.edit_message_text("تعذر تحديث النوع.")
            return
        await q.edit_message_text("✅ تم تحديث حالة الزر.")
        await _show_types(q.message, branch_id)
    elif action == "duration":
        context.user_data["editing_request_type"] = type_id
        context.user_data["new_request_type_state"] = "duration"
        await q.message.reply_text(f"⏱ أرسل المدة الجديدة بالدقائق لزر «{current[2]}» (من 1 إلى 240).")


async def request_type_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    state = context.user_data.get("new_request_type_state")
    if not state:
        return
    branch_id = context.user_data.get("managed_request_branch")
    user_id = update.effective_user.id
    if not branch_id or not _can_manage(user_id, branch_id):
        context.user_data.pop("new_request_type_state", None)
        context.user_data.pop("editing_request_type", None)
        await update.effective_message.reply_text("⛔ انتهت صلاحيتك أو الجلسة. أعد فتح /request_types.")
        return
    raw = (update.effective_message.text or "").strip()
    if state == "label":
        if not 2 <= len(raw) <= 32:
            await update.effective_message.reply_text("اسم الزر يجب أن يكون بين حرفين و32 حرفاً.")
            return
        context.user_data["new_request_type_label"] = raw
        context.user_data["new_request_type_state"] = "new_duration"
        await update.effective_message.reply_text("أرسل مدة الاستئذان بالدقائق (1 إلى 240).")
        return
    if not raw.isdigit() or not 1 <= int(raw) <= 240:
        await update.effective_message.reply_text("أدخل رقماً صحيحاً من 1 إلى 240 دقيقة.")
        return
    duration = int(raw)
    if state == "duration":
        type_id = context.user_data.get("editing_request_type")
        ok = _update_type(branch_id, type_id, user_id, duration=duration)
    else:
        label = context.user_data.get("new_request_type_label")
        ok = bool(_save_new(branch_id, label, duration, user_id))
    context.user_data.pop("new_request_type_state", None)
    context.user_data.pop("editing_request_type", None)
    context.user_data.pop("new_request_type_label", None)
    if not ok:
        await update.effective_message.reply_text("❌ تعذر حفظ التعديل في قاعدة البيانات.")
        return
    await update.effective_message.reply_text("✅ تم الحفظ. سيظهر الزر للموظفين في هذا الفرع عند تحديث القائمة بالأمر /start.")
    await _show_types(update.effective_message, branch_id)


async def custom_request_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    if context.user_data.get("new_request_type_state"):
        return
    employee = bot.get_employee_by_telegram_id(update.effective_user.id)
    if not employee or not employee.get("branch_id"):
        return
    label = (update.effective_message.text or "").strip()
    for type_id, code, type_label, duration, active in list_request_types(employee["branch_id"], active_only=True):
        if label != type_label:
            continue
        if bot._timer_db("is_active", update.effective_user.id):
            await update.effective_message.reply_text("⏳ لديك استراحة أو استئذان جارٍ بالفعل. أكمل العودة أولاً.")
            return
        request_type = f"custom-{type_id}"
        request_id = bot.create_request(employee["id"], request_type)
        if not request_id:
            await update.effective_message.reply_text("❌ تعذر حفظ طلبك. حاول مرة أخرى.")
            return
        name = employee.get("full_name") or "موظف"
        keyboard = [[
            InlineKeyboardButton("✅ موافقة وبدء المؤقت", callback_data=f"approve_{request_type}_{request_id}_{update.effective_user.id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject_{request_type}_{request_id}_{update.effective_user.id}"),
        ]]
        await update.effective_message.reply_text("⏳ تم إرسال طلب الاستئذان للمدير.")
        await send_to_branch_admins(
            context,
            employee["branch_id"],
            f"📝 طلب استئذان جديد\n👤 الموظف: {name}\n📌 النوع: {type_label}\n⏱ المدة بعد الموافقة: {duration} دقائق",
            InlineKeyboardMarkup(keyboard),
        )
        return


def register_request_type_handlers(application):
    application.add_handler(CommandHandler("request_types", request_types_command))
    application.add_handler(CallbackQueryHandler(request_type_callback, pattern=r"^requesttype:"), group=-1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, request_type_text_handler), group=3)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, custom_request_handler), group=2)
