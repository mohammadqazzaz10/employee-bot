"""Branch-scoped announcements with durable employee acknowledgements."""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes,
)

logger = logging.getLogger(__name__)
STATE = "pending_broadcast"
REMINDER_INTERVAL_SECONDS = 60
EMPLOYEE_PAGE_SIZE = 8


def _employee_options(user_id, audience):
    """Return selectable employees after enforcing the manager's audience scope."""
    import bot
    conn = None
    try:
        if not bot.is_admin(user_id):
            return None
        conn = bot.get_db_connection()
        cur = conn.cursor()
        if audience == "all":
            if not bot.is_super_admin(user_id):
                return None
            cur.execute("""SELECT telegram_id, full_name, COALESCE(job_title, '')
                           FROM employees
                           WHERE is_active=TRUE AND telegram_id IS NOT NULL
                           ORDER BY full_name, id""")
        else:
            branch_id = int(audience)
            from branch_settings import _resolve_branch
            if _resolve_branch(user_id, branch_id) != branch_id:
                return None
            cur.execute("""SELECT telegram_id, full_name, COALESCE(job_title, '')
                           FROM employees
                           WHERE is_active=TRUE AND telegram_id IS NOT NULL
                             AND branch_id=%s
                           ORDER BY full_name, id""", (branch_id,))
        return [
            {"telegram_id": int(row[0]), "name": row[1], "job_title": row[2]}
            for row in cur.fetchall()
        ]
    except Exception:
        logger.exception("Could not resolve selectable announcement employees")
        return None
    finally:
        if conn:
            conn.close()


def _targets(user_id, audience, selected_ids=None):
    """Revalidate selected recipients immediately before preview and delivery."""
    options = _employee_options(user_id, audience)
    if options is None:
        return None
    allowed = [item["telegram_id"] for item in options]
    if selected_ids is None:
        return allowed
    selected = [int(uid) for uid in selected_ids]
    if len(set(selected)) != len(selected) or not set(selected).issubset(set(allowed)):
        return None
    return selected

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


def _db(operation, *values):
    """Execute one short, committed broadcast-state operation."""
    import bot
    conn = None
    try:
        conn = bot.get_db_connection()
        with conn.cursor() as cur:
            if operation == "create":
                cur.execute(
                    """INSERT INTO broadcast_logs
                       (sent_by, audience, content_type, message_preview, recipient_count,
                        sent_count, failed_count)
                       VALUES (%s,%s,%s,%s,%s,0,0) RETURNING id""",
                    values[:5],
                )
                broadcast_id = cur.fetchone()[0]
                cur.executemany(
                    """INSERT INTO broadcast_recipients
                       (broadcast_id, telegram_id, delivery_status)
                       VALUES (%s,%s,'pending')""",
                    [(broadcast_id, int(uid)) for uid in values[5]],
                )
                conn.commit()
                return broadcast_id
            if operation == "delivered":
                cur.execute(
                    """UPDATE broadcast_recipients
                       SET delivery_status='delivered', delivered_at=CURRENT_TIMESTAMP
                       WHERE broadcast_id=%s AND telegram_id=%s
                         AND delivery_status='pending'""",
                    values,
                )
            elif operation == "failed":
                cur.execute(
                    """UPDATE broadcast_recipients SET delivery_status='failed'
                       WHERE broadcast_id=%s AND telegram_id=%s
                         AND delivery_status='pending'""",
                    values,
                )
            elif operation == "counts":
                cur.execute(
                    """UPDATE broadcast_logs SET sent_count=%s, failed_count=%s
                       WHERE id=%s""",
                    values,
                )
            elif operation == "ack":
                cur.execute(
                    """UPDATE broadcast_recipients
                       SET delivery_status='acknowledged',
                           acknowledged_at=CURRENT_TIMESTAMP
                       WHERE broadcast_id=%s AND telegram_id=%s
                         AND delivery_status='delivered'
                       RETURNING broadcast_id""",
                    values,
                )
                row = cur.fetchone()
                conn.commit()
                return bool(row)
            elif operation == "pending":
                cur.execute(
                    """SELECT r.broadcast_id, r.telegram_id, l.message_preview
                       FROM broadcast_recipients r
                       JOIN broadcast_logs l ON l.id=r.broadcast_id
                       WHERE r.delivery_status='delivered'
                       ORDER BY r.delivered_at"""
                )
                rows = cur.fetchall()
                conn.commit()
                return rows
            elif operation == "is_pending":
                cur.execute(
                    """SELECT l.message_preview FROM broadcast_recipients r
                       JOIN broadcast_logs l ON l.id=r.broadcast_id
                       WHERE r.broadcast_id=%s AND r.telegram_id=%s
                         AND r.delivery_status='delivered'""",
                    values,
                )
                row = cur.fetchone()
                conn.commit()
                return row[0] if row else None
            else:
                raise ValueError("Unknown broadcast database operation")
        conn.commit()
        return True
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Broadcast database operation failed: %s", operation)
        return False
    finally:
        if conn:
            conn.close()


def _reminder_job_name(broadcast_id, telegram_id):
    return f"broadcast-reminder-{int(broadcast_id)}-{int(telegram_id)}"


def _schedule_reminder(application, broadcast_id, telegram_id, first=REMINDER_INTERVAL_SECONDS):
    if not application.job_queue:
        logger.error("JobQueue unavailable; cannot schedule broadcast reminder")
        return
    name = _reminder_job_name(broadcast_id, telegram_id)
    for old_job in application.job_queue.get_jobs_by_name(name):
        old_job.schedule_removal()
    application.job_queue.run_repeating(
        send_acknowledgement_reminder,
        interval=REMINDER_INTERVAL_SECONDS,
        first=max(1, first),
        data=(int(broadcast_id), int(telegram_id)),
        name=name,
    )


async def send_acknowledgement_reminder(context: ContextTypes.DEFAULT_TYPE):
    broadcast_id, telegram_id = context.job.data
    preview = _db("is_pending", broadcast_id, telegram_id)
    if preview is False:
        logger.error("Could not check acknowledgement state for broadcast %s", broadcast_id)
        return
    if preview is None:
        context.job.schedule_removal()
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "👁️ تم الاطلاع",
            callback_data=f"broadcast:ack:{broadcast_id}",
        )
    ]])
    try:
        await context.bot.send_message(
            chat_id=telegram_id,
            text=(
                "🔔 تذكير من الإدارة: ما زال هذا التنبيه بانتظار تأكيد اطلاعك.\\n\\n"
                f"موضوع التنبيه: {preview or 'تنبيه إداري'}\\n\\n"
                "اضغط «تم الاطلاع» لإيقاف التذكيرات."
            ),
            reply_markup=keyboard,
            disable_notification=False,
        )
    except Exception:
        logger.exception(
            "Could not send acknowledgement reminder for broadcast %s to %s",
            broadcast_id, telegram_id,
        )


async def restore_pending_broadcast_reminders(application):
    """Rebuild outstanding acknowledgement reminders after process restarts."""
    rows = _db("pending")
    if rows is False:
        logger.error("Could not restore broadcast reminders")
        return
    for broadcast_id, telegram_id, _preview in rows:
        _schedule_reminder(application, broadcast_id, telegram_id, first=5)
    logger.info("Restored %s pending broadcast acknowledgement reminder(s)", len(rows))


async def _render_employee_selection(query, context, page=0):
    pending = context.user_data.get(STATE) or {}
    employees = pending.get("employees", [])
    selected = set(pending.get("selected_ids", []))
    pages = max(1, (len(employees) + EMPLOYEE_PAGE_SIZE - 1) // EMPLOYEE_PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    pending["page"] = page
    start = page * EMPLOYEE_PAGE_SIZE
    current = employees[start:start + EMPLOYEE_PAGE_SIZE]
    rows = []
    for employee in current:
        telegram_id = employee["telegram_id"]
        mark = "☑️" if telegram_id in selected else "⬜"
        name = (employee["name"] or "موظف").strip()
        if len(name) > 34:
            name = name[:31] + "..."
        job = (employee["job_title"] or "").strip()
        label = f"{mark} {name}" + (f" — {job[:16]}" if job else "")
        rows.append([InlineKeyboardButton(label, callback_data=f"broadcast:employee:{telegram_id}")])
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"broadcast:page:{page - 1}"))
    navigation.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="broadcast:page:0"))
    if page + 1 < pages:
        navigation.append(InlineKeyboardButton("التالي ➡️", callback_data=f"broadcast:page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([
        InlineKeyboardButton("☑️ تحديد الجميع", callback_data="broadcast:select_all"),
        InlineKeyboardButton("🧹 إلغاء التحديد", callback_data="broadcast:clear"),
    ])
    rows.append([InlineKeyboardButton(
        f"متابعة ({len(selected)} محدد)", callback_data="broadcast:continue"
    )])
    await query.edit_message_text(
        f"👥 اختر الموظفين الذين سيصلهم التنبيه.\n"
        f"الموظفون المتاحون: {len(employees)} | المحددون: {len(selected)}\n"
        f"الصفحة {page + 1} من {pages}\n\n"
        "اضغط على اسم الموظف لتحديده أو إلغاء تحديده. يمكنك التنقل بين الصفحات.",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def broadcast_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    query = update.callback_query
    pending = context.user_data.get(STATE)
    if not bot.is_admin(query.from_user.id) or not pending or "employees" not in pending:
        await query.answer("انتهت جلسة الاختيار. ابدأ من جديد عبر /broadcast.", show_alert=True)
        return
    data = (query.data or "").split(":")
    employees = pending["employees"]
    allowed = {item["telegram_id"] for item in employees}
    selected = set(pending.get("selected_ids", []))
    if data[1] == "employee" and len(data) == 3 and data[2].isdigit():
        telegram_id = int(data[2])
        if telegram_id not in allowed:
            await query.answer("هذا الموظف ليس ضمن الجمهور المسموح.", show_alert=True)
            return
        if telegram_id in selected:
            selected.remove(telegram_id)
        else:
            selected.add(telegram_id)
        pending["selected_ids"] = sorted(selected)
        await query.answer()
        await _render_employee_selection(query, context, pending.get("page", 0))
        return
    if data[1] == "page" and len(data) == 3 and data[2].isdigit():
        await query.answer()
        await _render_employee_selection(query, context, int(data[2]))
        return
    if data[1] == "select_all":
        pending["selected_ids"] = sorted(allowed)
        await query.answer("تم تحديد جميع الموظفين.")
        await _render_employee_selection(query, context, pending.get("page", 0))
        return
    if data[1] == "clear":
        pending["selected_ids"] = []
        await query.answer("تم إلغاء تحديد الجميع.")
        await _render_employee_selection(query, context, pending.get("page", 0))
        return
    if data[1] == "continue":
        if not selected:
            await query.answer("حدد موظفاً واحداً على الأقل.", show_alert=True)
            return
        pending["selected_ids"] = sorted(selected)
        await query.answer()
        await query.edit_message_text(
            f"📢 الجمهور: {pending.get('audience_label', 'الجمهور المحدد')}\n"
            f"👥 عدد الموظفين المحددين: {len(selected)}\n\n"
            "أرسل الآن نص الإعلان أو رسالة صوتية/ملفاً صوتياً."
        )
        return
    await query.answer("اختيار غير صالح.", show_alert=True)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    user_id = update.effective_user.id
    if not bot.is_admin(user_id):
        await update.effective_message.reply_text("⛔ هذا الأمر للمديرين فقط.")
        return
    import bot
    bot.clear_interactive_drafts(context)
    rows = []
    if bot.is_super_admin(user_id):
        rows.append([InlineKeyboardButton("📢 جميع الموظفين في كل الفروع", callback_data="broadcast:all")])
    for branch_id, name in _branches(user_id):
        rows.append([InlineKeyboardButton(f"🏬 فرع: {name}", callback_data=f"broadcast:branch:{branch_id}")])
    if not rows:
        await update.effective_message.reply_text("⛔ لا يوجد فرع مرتبط بصلاحيتك.")
        return
    await update.effective_message.reply_text(
        "اختر الفرع أو الجمهور الذي تريد إرسال الإعلان إليه:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


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
        audience_label = next(
            (name for bid, name in _branches(q.from_user.id) if bid == int(audience)),
            f"الفرع {audience}",
        )
    else:
        await q.edit_message_text("اختيار غير صالح.")
        return
    employees = _employee_options(q.from_user.id, audience)
    if employees is None:
        await q.edit_message_text("⛔ تعذر التحقق من صلاحيات الجمهور.")
        return
    if not employees:
        await q.edit_message_text("لا يوجد موظفون نشطون لديهم حساب Telegram في هذا الجمهور.")
        return
    context.user_data[STATE] = {
        "audience": audience,
        "audience_label": audience_label,
        "employees": employees,
        "selected_ids": [],
        "page": 0,
    }
    await _render_employee_selection(q, context, page=0)


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
    else:
        await message.reply_text("أرسل نصاً أو تسجيلاً صوتياً أو ملفاً صوتياً فقط.")
        return
    pending["content"] = content
    recipients = _targets(
        update.effective_user.id, pending["audience"], pending.get("selected_ids", [])
    )
    if recipients is None or not recipients:
        context.user_data.pop(STATE, None)
        await message.reply_text("⛔ تغيرت صلاحيات الجمهور أو لم يعد الموظفون المحددون متاحين. ابدأ من جديد.")
        return
    pending["recipients"] = recipients
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("👁️ تم الاطلاع", callback_data="broadcast:ack:0"),
        InlineKeyboardButton("❌ إلغاء", callback_data="broadcast:cancel"),
    ]])
    await message.reply_text(
        f"راجع الإعلان قبل الإرسال:\n👥 المستهدفون: {len(recipients)} موظفاً\n"
        f"🎯 الجمهور: {pending.get('audience_label', 'فرعك')}\n"
        "سيُرسل لكل موظف زر «تم الاطلاع»، وتستمر التذكيرات له حتى يضغطه.\n"
        "هل تؤكد الإرسال؟",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأكيد الإرسال", callback_data="broadcast:send"),
            InlineKeyboardButton("❌ إلغاء", callback_data="broadcast:cancel"),
        ]]),
    )


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
    recipients = _targets(
        q.from_user.id, pending["audience"], pending.get("recipients", [])
    )
    if recipients is None or not recipients:
        context.user_data.pop(STATE, None)
        await q.edit_message_text("⛔ تغيرت الصلاحيات أو لم يعد الموظفون المحددون متاحين. لم يتم الإرسال.")
        return
    content = pending["content"]
    preview = (
        content["value"] if content["kind"] == "text"
        else content.get("caption", "") or "رسالة صوتية من الإدارة"
    )
    broadcast_id = _db(
        "create", q.from_user.id, pending["audience"], content["kind"],
        preview[:500], len(recipients), recipients,
    )
    if not broadcast_id:
        await q.edit_message_text("⛔ تعذر حفظ سجل التنبيه. لم يتم الإرسال.")
        context.user_data.pop(STATE, None)
        return
    sent = failed = 0
    for employee_id in recipients:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "👁️ تم الاطلاع",
                callback_data=f"broadcast:ack:{broadcast_id}",
            )
        ]])
        try:
            if content["kind"] == "text":
                await context.bot.send_message(
                    chat_id=employee_id,
                    text="🚨 تنبيه من الإدارة\n\n" + content["value"],
                    reply_markup=keyboard,
                    disable_notification=False,
                )
            elif content["kind"] == "voice":
                await context.bot.send_voice(
                    chat_id=employee_id, voice=content["value"],
                    caption=content["caption"] or "🚨 تنبيه من الإدارة",
                    reply_markup=keyboard,
                    disable_notification=False,
                )
            else:
                await context.bot.send_audio(
                    chat_id=employee_id, audio=content["value"],
                    caption=content["caption"] or "🚨 تنبيه من الإدارة",
                    reply_markup=keyboard,
                    disable_notification=False,
                )
            if _db("delivered", broadcast_id, employee_id):
                sent += 1
                _schedule_reminder(context.application, broadcast_id, employee_id)
            else:
                failed += 1
        except Exception:
            failed += 1
            _db("failed", broadcast_id, employee_id)
            logger.exception("Announcement delivery failed for employee Telegram ID %s", employee_id)
    _db("counts", sent, failed, broadcast_id)
    context.user_data.pop(STATE, None)
    await q.edit_message_text(
        f"انتهى الإرسال.\n✅ وصل: {sent}\n⚠️ تعذر إرساله: {failed}\n"
        f"🔔 التذكير يتكرر كل {REMINDER_INTERVAL_SECONDS} ثانية حتى يؤكد كل موظف الاطلاع."
    )


async def broadcast_ack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = (q.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await q.answer("بيانات التأكيد غير صالحة.", show_alert=True)
        return
    broadcast_id = int(parts[2])
    if broadcast_id == 0:
        await q.answer("هذا زر معاينة فقط. استخدم زر التأكيد لإرسال التنبيه.", show_alert=True)
        return
    if not _db("ack", broadcast_id, q.from_user.id):
        await q.answer("هذا التنبيه غير مرتبط بحسابك أو سبق تأكيده.", show_alert=True)
        return
    await q.answer()
    for job in context.job_queue.get_jobs_by_name(
        _reminder_job_name(broadcast_id, q.from_user.id)
    ) if context.job_queue else []:
        job.schedule_removal()
    try:
        if q.message and q.message.caption is not None:
            await q.edit_message_caption(
                caption=(q.message.caption or "") + "\n\n✅ تم تأكيد اطلاعك.",
                reply_markup=None,
            )
        else:
            await q.edit_message_text(
                (q.message.text if q.message and q.message.text else "تنبيه الإدارة")
                + "\n\n✅ تم تأكيد اطلاعك.",
                reply_markup=None,
            )
    except Exception:
        logger.exception("Could not update acknowledged broadcast message")


def register_broadcast_handlers(application):
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CallbackQueryHandler(
        broadcast_ack_callback, pattern=r"^broadcast:ack:[0-9]+$"
    ), group=-1)
    application.add_handler(CallbackQueryHandler(
        broadcast_callback, pattern=r"^broadcast:(all|branch:[0-9]+)$"
    ), group=-1)
    application.add_handler(CallbackQueryHandler(
        broadcast_selection_callback,
        pattern=r"^broadcast:(employee:[0-9]+|page:[0-9]+|select_all|clear|continue)$",
    ), group=-1)
    application.add_handler(CallbackQueryHandler(
        broadcast_send_callback, pattern=r"^broadcast:(send|cancel)$"
    ), group=-1)
    application.add_handler(MessageHandler(
        (filters.TEXT | filters.VOICE | filters.AUDIO) & ~filters.COMMAND,
        broadcast_content,
    ), group=4)
