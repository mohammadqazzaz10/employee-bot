"""Database-backed, branch-scoped operational settings for the employee bot."""
from datetime import datetime
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram import Update

logger = logging.getLogger(__name__)

LABELS = {
    "smoke_enabled": "تفعيل التدخين",
    "smoke_daily_limit": "عدد السجائر اليومي",
    "smoke_duration_minutes": "مدة التدخين بالدقائق",
    "smoke_start_time": "بداية وقت التدخين",
    "smoke_end_time": "نهاية وقت التدخين",
    "smoke_gap_minutes": "الفاصل بين السجائر بالدقائق",
    "lunch_enabled": "تفعيل الغداء",
    "lunch_duration_minutes": "مدة الغداء بالدقائق",
    "lunch_start_time": "بداية وقت الغداء",
    "lunch_end_time": "نهاية وقت الغداء",
}
NUMERIC = {
    "smoke_daily_limit": (0, 30),
    "smoke_duration_minutes": (1, 120),
    "smoke_gap_minutes": (0, 1440),
    "lunch_duration_minutes": (1, 240),
}
TIMES = {"smoke_start_time", "smoke_end_time", "lunch_start_time", "lunch_end_time"}
TOGGLES = {"smoke_enabled", "lunch_enabled"}


def get_branch_settings(branch_id):
    import bot
    if not branch_id:
        return None
    conn = None
    try:
        conn = bot.get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO branch_settings(branch_id) VALUES(%s) ON CONFLICT DO NOTHING", (branch_id,))
        cur.execute("SELECT smoke_enabled, smoke_daily_limit, smoke_duration_minutes, smoke_start_time, smoke_end_time, smoke_gap_minutes, lunch_enabled, lunch_duration_minutes, lunch_start_time, lunch_end_time FROM branch_settings WHERE branch_id=%s", (branch_id,))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        if not row:
            return None
        keys = ["smoke_enabled", "smoke_daily_limit", "smoke_duration_minutes", "smoke_start_time", "smoke_end_time", "smoke_gap_minutes", "lunch_enabled", "lunch_duration_minutes", "lunch_start_time", "lunch_end_time"]
        return dict(zip(keys, row))
    except Exception as exc:
        logger.exception("Could not load branch settings: %s", exc)
        if conn:
            conn.rollback()
            conn.close()
        return None


def _resolve_branch(user_id, requested=None):
    import bot
    conn = None
    try:
        conn = bot.get_db_connection()
        cur = conn.cursor()
        if bot.is_super_admin(user_id):
            if requested is None:
                cur.execute("SELECT id FROM branches WHERE code='main' AND is_active=TRUE")
                row = cur.fetchone()
                if not row:
                    cur.execute("SELECT id FROM branches WHERE is_active=TRUE ORDER BY id LIMIT 1")
                    row = cur.fetchone()
            else:
                cur.execute("SELECT id FROM branches WHERE id=%s AND is_active=TRUE", (requested,))
                row = cur.fetchone()
            result = row[0] if row else None
        else:
            cur.execute("SELECT branch_id FROM admin_branch_roles WHERE telegram_id=%s ORDER BY branch_id", (user_id,))
            ids = [r[0] for r in cur.fetchall()]
            result = requested if requested in ids else (ids[0] if requested is None and len(ids) == 1 else None)
        cur.close()
        conn.close()
        return result
    except Exception as exc:
        logger.error("Cannot resolve settings branch; apply migration 001: %s", exc)
        if conn:
            conn.close()
        return None


def _save(branch_id, key, value, user_id):
    import bot
    if key not in LABELS:
        return False
    conn = None
    try:
        conn = bot.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM branches WHERE id=%s AND is_active=TRUE", (branch_id,))
        if not cur.fetchone():
            conn.rollback()
            return False
        cur.execute(f"SELECT {key} FROM branch_settings WHERE branch_id=%s FOR UPDATE", (branch_id,))
        row = cur.fetchone()
        if not row:
            cur.execute("INSERT INTO branch_settings(branch_id) VALUES(%s) ON CONFLICT DO NOTHING", (branch_id,))
            cur.execute(f"SELECT {key} FROM branch_settings WHERE branch_id=%s FOR UPDATE", (branch_id,))
            row = cur.fetchone()
        old = row[0] if row else None
        cur.execute(f"UPDATE branch_settings SET {key}=%s, updated_by=%s, updated_at=CURRENT_TIMESTAMP WHERE branch_id=%s", (value, user_id, branch_id))
        cur.execute("INSERT INTO branch_settings_audit(branch_id,setting_key,old_value,new_value,changed_by) VALUES(%s,%s,%s,%s,%s)",
                    (branch_id, key, str(old) if old is not None else None, str(value) if value is not None else None, user_id))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as exc:
        logger.exception("Could not save branch setting: %s", exc)
        if conn:
            conn.rollback()
            conn.close()
        return False


def _parse(key, raw):
    value = (raw or "").strip()
    if key in TOGGLES:
        if value.lower() in {"yes", "true", "on", "1", "نعم", "تشغيل"}:
            return True
        if value.lower() in {"no", "false", "off", "0", "لا", "إيقاف"}:
            return False
        raise ValueError("اكتب نعم أو لا.")
    if key in NUMERIC:
        if not value.isdigit():
            raise ValueError("أدخل رقماً صحيحاً.")
        number = int(value)
        low, high = NUMERIC[key]
        if not low <= number <= high:
            raise ValueError(f"القيمة يجب أن تكون بين {low} و{high}.")
        return number
    if key in TIMES:
        if value.lower() in {"off", "none", "تعطيل", "بدون"} and key != "smoke_start_time":
            return None
        try:
            return datetime.strptime(value, "%H:%M").time()
        except ValueError:
            raise ValueError("استخدم صيغة HH:MM مثل 17:30.")
    raise ValueError("إعداد غير معروف.")


def _display(value):
    if value is None:
        return "غير محدد"
    if isinstance(value, bool):
        return "مفعّل" if value else "متوقف"
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value)


async def _show(message, branch_id):
    settings = get_branch_settings(branch_id)
    if not settings:
        await message.reply_text("تعذر تحميل إعدادات الفرع. تأكد من تطبيق migration 001.")
        return
    rows = [[InlineKeyboardButton(f"{LABELS[k]}: {_display(settings[k])}", callback_data=f"branchcfg:{branch_id}:{k}")]
            for k in LABELS]
    rows.append([InlineKeyboardButton("تحديث", callback_data=f"branchcfg:{branch_id}:refresh")])
    await message.reply_text(f"⚙️ إعدادات الفرع رقم {branch_id}\nاضغط على الإعداد لتعديله. اكتب off لإزالة وقت اختياري.", reply_markup=InlineKeyboardMarkup(rows))


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    context.user_data.pop("new_request_type_state", None)
    context.user_data.pop("editing_request_type", None)
    context.user_data.pop("new_request_type_label", None)
    user_id = update.effective_user.id
    if not bot.is_admin(user_id):
        await update.effective_message.reply_text("⛔ هذه القائمة للمديرين فقط.")
        return
    requested = None
    if context.args:
        try:
            requested = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text("الاستخدام: /settings أو /settings رقم_الفرع")
            return
    branch_id = _resolve_branch(user_id, requested)
    if not branch_id:
        await update.effective_message.reply_text("⛔ لا يوجد فرع ضمن صلاحيتك. المدير العام يستخدم /settings، ومدير الفرع يحتاج دوراً مرتبطاً بفرعه. تأكد من تطبيق migration 001.")
        return
    await _show(update.effective_message, branch_id)


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    q = update.callback_query
    await q.answer()
    parts = (q.data or "").split(":")
    if len(parts) != 3:
        await q.edit_message_text("طلب إعدادات غير صالح.")
        return
    try:
        branch_id = int(parts[1])
    except ValueError:
        await q.edit_message_text("رقم الفرع غير صالح.")
        return
    if not bot.is_admin(q.from_user.id) or _resolve_branch(q.from_user.id, branch_id) != branch_id:
        await q.edit_message_text("⛔ لا تملك صلاحية هذا الفرع.")
        return
    key = parts[2]
    if key == "refresh":
        await q.edit_message_text("تم تحديث القائمة.")
        await _show(q.message, branch_id)
        return
    if key not in LABELS:
        await q.edit_message_text("إعداد غير معروف.")
        return
    if key in TOGGLES:
        current = get_branch_settings(branch_id)
        if not _save(branch_id, key, not bool(current[key]), q.from_user.id):
            await q.edit_message_text("تعذر حفظ الإعداد.")
            return
        await q.edit_message_text("✅ تم الحفظ فوراً.")
        await _show(q.message, branch_id)
        return
    context.user_data["pending_branch_setting"] = {"branch_id": branch_id, "key": key}
    await q.message.reply_text(f"✏️ {LABELS[key]}\nأرسل القيمة برسالة. الوقت HH:MM، واكتب off لإزالة القيد الاختياري.")


async def save_setting_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    pending = context.user_data.get("pending_branch_setting")
    if not pending:
        return
    branch_id, key = pending["branch_id"], pending["key"]
    user_id = update.effective_user.id
    if not bot.is_admin(user_id) or _resolve_branch(user_id, branch_id) != branch_id:
        context.user_data.pop("pending_branch_setting", None)
        await update.effective_message.reply_text("⛔ لم تعد تملك صلاحية تعديل هذا الفرع.")
        return
    try:
        value = _parse(key, update.effective_message.text)
    except ValueError as exc:
        await update.effective_message.reply_text(f"⚠️ {exc} حاول مرة أخرى.")
        return
    if not _save(branch_id, key, value, user_id):
        await update.effective_message.reply_text("❌ تعذر حفظ الإعداد.")
        return
    context.user_data.pop("pending_branch_setting", None)
    await update.effective_message.reply_text("✅ تم حفظ الإعداد وتطبيقه فورياً.")
    await _show(update.effective_message, branch_id)



async def assign_branch_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    user_id = update.effective_user.id
    if not bot.is_super_admin(user_id):
        await update.effective_message.reply_text("⛔ هذا الأمر للمدير العام فقط.")
        return
    if len(context.args) not in {2, 3}:
        await update.effective_message.reply_text(
            "الاستخدام: /assign_branch_admin TELEGRAM_ID BRANCH_ID [branch_manager|supervisor]"
        )
        return
    try:
        admin_id, branch_id = int(context.args[0]), int(context.args[1])
        role = context.args[2].strip().lower() if len(context.args) == 3 else "branch_manager"
        if admin_id <= 0 or branch_id <= 0 or role not in {"branch_manager", "supervisor"}:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text(
            "أدخل معرفات موجبة، والدور branch_manager أو supervisor."
        )
        return
    conn = None
    try:
        conn = bot.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM admins WHERE telegram_id=%s", (admin_id,))
        if not cur.fetchone():
            await update.effective_message.reply_text("أضف حساب المدير أولاً باستخدام /add_admin TELEGRAM_ID.")
            conn.close()
            return
        cur.execute("SELECT name FROM branches WHERE id=%s AND is_active=TRUE", (branch_id,))
        branch = cur.fetchone()
        if not branch:
            await update.effective_message.reply_text("رقم الفرع غير موجود أو غير نشط.")
            conn.close()
            return
        cur.execute("""
            INSERT INTO admin_branch_roles(telegram_id, branch_id, role, granted_by)
            VALUES(%s, %s, %s, %s)
            ON CONFLICT(telegram_id, branch_id)
            DO UPDATE SET role=EXCLUDED.role, granted_by=EXCLUDED.granted_by, granted_at=CURRENT_TIMESTAMP
        """, (admin_id, branch_id, role, user_id))
        conn.commit()
        cur.close()
        conn.close()
        role_label = "مدير فرع" if role == "branch_manager" else "مشرف فرع"
        await update.effective_message.reply_text(
            f"✅ تم تعيين الحساب {admin_id} بصفة {role_label} في {branch[0]} (رقم {branch_id}) فقط."
        )
    except Exception as exc:
        logger.exception("Could not assign branch manager: %s", exc)
        if conn:
            conn.rollback()
            conn.close()
        await update.effective_message.reply_text("❌ تعذر منح الصلاحية. تأكد من تطبيق migration 001.")


async def cancel_settings_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = context.user_data.pop("pending_branch_setting", None)
    request_type_state = context.user_data.pop("new_request_type_state", None)
    context.user_data.pop("editing_request_type", None)
    context.user_data.pop("new_request_type_label", None)
    if pending or request_type_state:
        await update.effective_message.reply_text("تم إلغاء تعديل الإعدادات.")
    else:
        await update.effective_message.reply_text("لا يوجد تعديل إعدادات قيد الانتظار.")


def register_settings_handlers(application):
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("cancel", cancel_settings_edit))
    application.add_handler(CommandHandler("assign_branch_admin", assign_branch_admin_command))
    application.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^branchcfg:"), group=-1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_setting_value), group=1)
