"""Branch-scoped manager announcements and attention alerts."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

logger = logging.getLogger(__name__)
STATE = "pending_broadcast"


def _targets(user_id, audience):
    import bot
    conn = None
    try:
        conn = bot.get_db_connection()
        cur = conn.cursor()
        if audience == "all":
            if not bot.is_super_admin(user_id):
                return None
            cur.execute("""SELECT telegram_id FROM employees
                           WHERE is_active=TRUE AND telegram_id IS NOT NULL
                           ORDER BY id""")
        else:
            branch_id = int(audience)
            from branch_settings import _resolve_branch
            if not bot.is_admin(user_id) or _resolve_branch(user_id, branch_id) != branch_id:
                return None
            cur.execute("""SELECT telegram_id FROM employees
                           WHERE is_active=TRUE AND telegram_id IS NOT NULL
                             AND branch_id=%s ORDER BY id""", (branch_id,))
        return [int(row[0]) for row in cur.fetchall()]
    except Exception:
        logger.exception("Could not resolve announcement recipients")
        return None
    finally:
        if conn:
            conn.close()


def _branches(user_id):
    import bot
    conn = None
    try:
        conn = bot.get_db_connection()
        cur = conn.cursor()
        if bot.is_super_admin(user_id):
            cur.execute("SELECT id, name FROM branches WHERE is_active=TRUE ORDER BY name")
        else:
            cur.execute("""SELECT b.id, b.name FROM branches b
                           JOIN admin_branch_roles r ON r.branch_id=b.id
                           WHERE r.telegram_id=%s AND b.is_active=TRUE
                           ORDER BY b.name""", (user_id,))
        return cur.fetchall()
    except Exception:
        logger.exception("Could not list announcement branches")
        return []
    finally:
        if conn:
            conn.close()


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    user_id = update.effective_user.id
    if not bot.is_admin(user_id):
        await update.effective_message.reply_text("⛔ هذا الأمر للمديرين فقط.")
        return
    context.user_data.pop(STATE, None)
    rows = []
    if bot.is_super_admin(user_id):
        rows.append([InlineKeyboardButton("📢 جميع الموظفين في كل الفروع", callback_data="broadcast:all")])
    for branch_id, name in _branches(user_id):
        rows.append([InlineKeyboardButton(f"🏬 فرع: {name}", callback_data=f"broadcast:branch:{branch_id}")])
    if not rows:
        await update.effective_message.reply_text("⛔ لا يوجد فرع مرتبط بصلاحيتك.")
        return
    await update.effective_message.reply_text(
        "اختر الفرع أو الجمهور الذي تريد إرسال الإعلان إليه:", reply_markup=InlineKeyboardMarkup(rows))


async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    q = update.callback_query
    await q.answer()
    if not bot.is_admin(q.from_user.id):
        await q.edit_message_text("⛔ غير مصرح لك.")
        return
    parts = (q.data or "").split(":")
    if len(parts) == 2 and parts[1] == "all":
        if not bot.is_super_admin(q.from_user.id):
            await q.edit_message_text("⛔ الإرسال لكل الفروع متاح للمدير العام فقط.")
            return
        audience = "all"
        audience_label = "جميع الفروع"
    elif len(parts) == 3 and parts[1] == "branch" and parts[2].isdigit():
        audience = parts[2]
        from branch_settings import _resolve_branch
        if _resolve_branch(q.from_user.id, int(audience)) != int(audience):
            await q.edit_message_text("⛔ لا تملك صلاحية هذا الفرع.")
            return
        audience_label = next((name for bid, name in _branches(q.from_user.id) if bid == int(audience)), f"الفرع {audience}")
    else:
        await q.edit_message_text("اختيار غير صالح.")
        return
    context.user_data[STATE] = {"audience": audience, "audience_label": audience_label}
    await q.edit_message_text(
        f"📢 الجمهور: {audience_label}\nأرسل نص الإعلان أو رسالة صوتية/ملفاً صوتياً.\nاكتب /cancel للإلغاء.")


async def broadcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = context.user_data.get(STATE)
    if not pending:
        return
    message = update.effective_message
    content = {"kind": None, "value": None, "caption": message.caption or ""}
    if message.text:
        content.update(kind="text", value=message.text)
    elif message.voice:
        content.update(kind="voice", value=message.voice.file_id)
    elif message.audio:
        content.update(kind="audio", value=message.audio.file_id)
    elif message.document and (message.document.mime_type or "").startswith("audio/"):
        content.update(kind="audio", value=message.document.file_id)
    else:
        await message.reply_text("أرسل نصاً أو تسجيلاً صوتياً أو ملفاً صوتياً فقط.")
        return
    pending["content"] = content
    recipients = _targets(update.effective_user.id, pending["audience"])
    if recipients is None:
        context.user_data.pop(STATE, None)
        await message.reply_text("⛔ تعذر التحقق من صلاحيات الجمهور.")
        return
    if not recipients:
        await message.reply_text("لا يوجد موظفون مسجلون لديهم حساب Telegram في هذا الجمهور.")
        return
    pending["recipient_count"] = len(recipients)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تأكيد الإرسال", callback_data="broadcast:send"),
        InlineKeyboardButton("❌ إلغاء", callback_data="broadcast:cancel"),
    ]])
    await message.reply_text(
        f"راجع الإعلان قبل الإرسال:\n👥 المستهدفون: {len(recipients)} موظفاً\n"
        f"🎯 الجمهور: {pending.get('audience_label', 'فرعك')}\n"
        "هل تؤكد الإرسال؟", reply_markup=keyboard)


async def broadcast_send_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    q = update.callback_query
    await q.answer()
    pending = context.user_data.get(STATE)
    if not bot.is_admin(q.from_user.id) or not pending:
        await q.edit_message_text("انتهت جلسة الإرسال أو لا تملك الصلاحية. ابدأ من جديد عبر /broadcast.")
        return
    if q.data == "broadcast:cancel":
        context.user_data.pop(STATE, None)
        await q.edit_message_text("تم إلغاء الإعلان.")
        return
    recipients = _targets(q.from_user.id, pending["audience"])
    if recipients is None:
        context.user_data.pop(STATE, None)
        await q.edit_message_text("⛔ تغيرت الصلاحيات أو تعذر التحقق منها. لم يتم الإرسال.")
        return
    content = pending["content"]
    sent = failed = 0
    for employee_id in recipients:
        try:
            if content["kind"] == "text":
                await context.bot.send_message(chat_id=employee_id, text="📢 إعلان من الإدارة\n\n" + content["value"])
            elif content["kind"] == "voice":
                await context.bot.send_voice(chat_id=employee_id, voice=content["value"], caption=content["caption"] or "🔔 تنبيه من الإدارة")
            else:
                await context.bot.send_audio(chat_id=employee_id, audio=content["value"], caption=content["caption"] or "🔔 تنبيه من الإدارة")
            sent += 1
        except Exception:
            failed += 1
            logger.exception("Announcement delivery failed for employee Telegram ID %s", employee_id)
    conn = None
    try:
        conn = bot.get_db_connection()
        cur = conn.cursor()
        cur.execute("""INSERT INTO broadcast_logs
            (sent_by, audience, content_type, message_preview, recipient_count, sent_count, failed_count)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (q.from_user.id, pending["audience"], content["kind"],
             (content.get("value") if content["kind"] == "text" else content.get("caption", ""))[:500],
             len(recipients), sent, failed))
        conn.commit()
    except Exception:
        logger.exception("Could not persist announcement audit record")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
    context.user_data.pop(STATE, None)
    await q.edit_message_text(f"انتهى الإرسال.\n✅ وصل: {sent}\n⚠️ تعذر إرساله: {failed}")


async def cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.pop(STATE, None):
        await update.effective_message.reply_text("تم إلغاء تجهيز الإعلان.")
    else:
        await update.effective_message.reply_text("لا يوجد إعلان قيد التجهيز.")


def register_broadcast_handlers(application):
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CallbackQueryHandler(broadcast_callback, pattern=r"^broadcast:(all|branch:[0-9]+)$"), group=-1)
    application.add_handler(CallbackQueryHandler(broadcast_send_callback, pattern=r"^broadcast:(send|cancel)$"), group=-1)
    application.add_handler(MessageHandler(
        (filters.TEXT | filters.VOICE | filters.AUDIO | filters.Document.AUDIO) & ~filters.COMMAND,
        broadcast_content), group=4)
