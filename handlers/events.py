"""
Модуль управления событиями.
"""
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import MODULE_ACCESS, ANNOUNCEMENT_GROUP_ID, ANNOUNCEMENT_TOPIC_ID
from db_utils import require_any_role, get_events, add_event, update_event, delete_event
from utils import check_rate_limit, kb, inline_kb, back_kb, escape_md, get_main_keyboard, parse_datetime_flexible
from handlers.conversation_utils import get_user_state, set_user_state, clear_user_state

STATE_EVENTS_MENU = "events_menu"
STATE_EVENTS_SELECTING_ITEM = "events_selecting_item"
STATE_EVENTS_INPUT_TYPE = "events_input_type"
STATE_EVENTS_INPUT_DATETIME = "events_input_datetime"
STATE_EVENTS_INPUT_LINK = "events_input_link"
STATE_EVENTS_INPUT_ANNOUNCEMENT = "events_input_announcement"
STATE_EVENTS_CONFIRM_ANNOUNCE = "events_confirm_announce"
STATE_EVENTS_EDITING = "events_editing"

events_menu_kb = kb(["📖 Просмотреть", "➕ Добавить", "✏️ Редактировать", "🗑️ Удалить"], "🔙 Назад")


def format_event(ev: dict) -> str:
    status = "✅" if ev['datetime'] > datetime.now().isoformat() else "⏰"
    link = f"[🔗]({ev['link']})" if ev['link'] else ""
    return f"{status} *ID:{ev['id']}* {escape_md(ev['type'])} ({ev['datetime'][:10]}) {link}"


async def events_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth = await require_any_role(update, context, MODULE_ACCESS["events_crud"])
    if not auth:
        return
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Нет прав.")
        return
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    await set_user_state(context, STATE_EVENTS_MENU)
    await update.message.reply_text("📋 *Управление событиями*", parse_mode="Markdown", reply_markup=events_menu_kb)


async def events_show_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_EVENTS_MENU:
        return
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Нет прав.")
        return
    events = await get_events()
    if not events:
        text = "📭 Нет событий"
    else:
        text = "📅 *Все события:*\n\n" + "\n\n".join(format_event(e) for e in events)
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=events_menu_kb)


async def event_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_EVENTS_MENU:
        return
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Нет прав.")
        return
    await set_user_state(context, STATE_EVENTS_INPUT_TYPE)
    await update.message.reply_text("Введите тип (Вебинар, Митап, Квиз):", reply_markup=back_kb)


async def event_add_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_EVENTS_INPUT_TYPE:
        return
    if not update.message.text:
        return
    if len(update.message.text) > 100:
        await update.message.reply_text("❌ Тип события слишком длинный (макс 100 символов)")
        return
    context.user_data["event_type"] = update.message.text
    await set_user_state(context, STATE_EVENTS_INPUT_DATETIME)
    await update.message.reply_text(
        "📅 *Введите дату и время*\n\n"
        "Поддерживаемые форматы:\n"
        "• `2024-12-31 18:00:00` — ISO формат\n"
        "• `31.12.2024 18:00` — точки\n"
        "• `сегодня 18:00` — сегодня\n"
        "• `завтра 18:00` — завтра",
        parse_mode="Markdown",
        reply_markup=back_kb
    )


async def event_add_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_EVENTS_INPUT_DATETIME:
        return
    if not update.message.text:
        return
    dt_iso, error = parse_datetime_flexible(update.message.text)
    if error:
        await update.message.reply_text(error, parse_mode="Markdown")
        return
    context.user_data["event_datetime"] = dt_iso
    await set_user_state(context, STATE_EVENTS_INPUT_LINK)
    await update.message.reply_text("Введите ссылку (или 'нет'):", reply_markup=back_kb)


async def event_add_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_EVENTS_INPUT_LINK:
        return
    if not update.message.text:
        return
    link = update.message.text.strip()
    if link.lower() == "нет":
        link = ""
    elif not (link.startswith('http://') or link.startswith('https://')):
        await update.message.reply_text("❌ Некорректная ссылка. Используйте формат: https://example.com/page")
        return
    context.user_data["event_link"] = link
    await set_user_state(context, STATE_EVENTS_INPUT_ANNOUNCEMENT)
    await update.message.reply_text("Введите анонс:", reply_markup=back_kb)


async def event_add_announcement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_EVENTS_INPUT_ANNOUNCEMENT:
        return
    if not update.message.text:
        return
    ann = update.message.text.strip()
    if len(ann) > 2000:
        await update.message.reply_text("❌ Анонс слишком длинный (макс 2000 символов)")
        return
    data = context.user_data
    event_type = data.get('event_type')
    event_datetime = data.get('event_datetime')
    if not all([event_type, event_datetime]):
        await clear_user_state(context)
        await update.message.reply_text(
            "⚠️ Сессия истекла. Начните сначала.",
            reply_markup=await get_main_keyboard(update.effective_user.id)
        )
        return
    context.user_data["event_announcement"] = ann
    if not ANNOUNCEMENT_GROUP_ID:
        try:
            await add_event(event_type, event_datetime, data.get('event_link'), ann)
            await update.message.reply_text("✅ Событие добавлено!")
        except Exception as e:
            logging.error(e)
            await update.message.reply_text("❌ Ошибка сохранения")
        await events_menu(update, context)
        return
    await set_user_state(context, STATE_EVENTS_CONFIRM_ANNOUNCE)
    preview = (
        f"📅 *{event_type}*\n"
        f"🕐 {event_datetime}\n"
        f"🔗 {data.get('event_link') or '—'}\n\n"
        f"{ann[:500]}{'...' if len(ann) > 500 else ''}"
    )
    await update.message.reply_text(
        f"{preview}\n\n📢 Разместить анонс в группе?",
        parse_mode="Markdown",
        reply_markup=kb(["✅ Да", "❌ Нет", "🏠 Главное меню"])
    )


async def event_confirm_announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_EVENTS_CONFIRM_ANNOUNCE:
        return
    if not update.message.text:
        return
    data = context.user_data
    event_type = data.get('event_type')
    event_datetime = data.get('event_datetime')
    event_link = data.get('event_link')
    event_announcement = data.get('event_announcement')
    if not all([event_type, event_datetime, event_announcement]):
        await clear_user_state(context)
        await update.message.reply_text(
            "⚠️ Сессия истекла. Начните сначала.",
            reply_markup=await get_main_keyboard(update.effective_user.id)
        )
        return
    try:
        await add_event(event_type, event_datetime, event_link, event_announcement)
        await update.message.reply_text("✅ Событие добавлено!")
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка сохранения")
        await events_menu(update, context)
        return
    if update.message.text and "Да" in update.message.text and "Нет" not in update.message.text and ANNOUNCEMENT_GROUP_ID:
        try:
            group_text = f"📅 *{event_type}*\n🕐 {event_datetime}\n"
            if event_link:
                group_text += f"🔗 [Ссылка на событие]({event_link})\n"
            group_text += f"\n{event_announcement}"
            rsvp_kb = inline_kb([
                [InlineKeyboardButton(text="✅ Иду", callback_data="noop"),
                 InlineKeyboardButton(text="❌ Не иду", callback_data="noop")]
            ])
            kwargs = {
                "chat_id": ANNOUNCEMENT_GROUP_ID,
                "text": group_text,
                "parse_mode": "Markdown",
                "reply_markup": rsvp_kb
            }
            if ANNOUNCEMENT_TOPIC_ID:
                kwargs["message_thread_id"] = ANNOUNCEMENT_TOPIC_ID
            await context.bot.send_message(**kwargs)
            await update.message.reply_text("📢 Анонс размещён в группе!")
        except Exception as e:
            logging.error(f"Ошибка отправки в группу: {e}")
            await update.message.reply_text("⚠️ Событие сохранено, но не удалось разместить анонс в группе.")
    await events_menu(update, context)


async def event_edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_EVENTS_MENU:
        return
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Нет прав.")
        return
    events = await get_events()
    if not events:
        await update.message.reply_text("📭 *Нет событий*\n\n💡 Скоро будут новые мероприятия!", parse_mode="Markdown", reply_markup=events_menu_kb)
        return
    await set_user_state(context, STATE_EVENTS_SELECTING_ITEM)
    kb_inline = inline_kb([
        [InlineKeyboardButton(
            text=f"✏️ {e['id']}. {e['type'][:20]} ({e['datetime'][:10]})",
            callback_data=f"edit_ev:{e['id']}"
        )] for e in events
    ])
    await update.message.reply_text("Выберите событие:", reply_markup=kb_inline)


async def event_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if get_user_state(context) != STATE_EVENTS_SELECTING_ITEM:
        await query.edit_message_text("❌ Сессия устарела")
        return
    try:
        ev_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    events = await get_events()
    ev = next((e for e in events if e['id'] == ev_id), None)
    if not ev:
        await query.edit_message_text("❌ Не найдено")
        return
    context.user_data["edit_id"] = ev_id
    context.user_data["edit_ev"] = ev
    await set_user_state(context, STATE_EVENTS_EDITING)
    await query.edit_message_text(
        f"✏️ Редактирование события *{ev_id}*\n\n"
        f"Отправьте: `тип\n\nдата\n\nссылка\n\nописание`\n\n"
        f"(используйте '.' для пропуска)",
        parse_mode="Markdown"
    )


async def event_edit_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_EVENTS_EDITING:
        return
    if not update.message.text:
        return
    ev_id = context.user_data.get('edit_id')
    if not ev_id:
        await clear_user_state(context)
        await update.message.reply_text(
            "⚠️ Сессия истекла. Начните сначала.",
            reply_markup=await get_main_keyboard(update.effective_user.id)
        )
        return
    parts = [p.strip() for p in update.message.text.split('\n\n') if p.strip()]
    updates = {}
    if parts and parts[0] != '.':
        updates['event_type'] = parts[0]
    if len(parts) > 1 and parts[1] != '.':
        try:
            datetime.fromisoformat(parts[1])
            updates['event_datetime'] = parts[1]
        except ValueError:
            await update.message.reply_text("❌ Неверный формат даты")
            return
    if len(parts) > 2 and parts[2] != '.':
        updates['link'] = "" if parts[2].lower() == "нет" else parts[2]
    if len(parts) > 3 and parts[3] != '.':
        updates['announcement'] = parts[3]
    if updates:
        await update_event(ev_id, **updates)
        await update.message.reply_text("✅ Обновлено!")
    else:
        await update.message.reply_text("❌ Ничего не изменено")
    await events_menu(update, context)


async def event_delete_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_EVENTS_MENU:
        return
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Нет прав.")
        return
    events = await get_events()
    if not events:
        await update.message.reply_text("📭 Нет событий", reply_markup=events_menu_kb)
        return
    await set_user_state(context, STATE_EVENTS_SELECTING_ITEM)
    kb_inline = inline_kb([
        [InlineKeyboardButton(
            text=f"🗑️ {e['id']}. {e['type'][:20]}",
            callback_data=f"del_ev:{e['id']}"
        )] for e in events
    ])
    await update.message.reply_text("Выберите для удаления:", reply_markup=kb_inline)


async def event_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if get_user_state(context) != STATE_EVENTS_SELECTING_ITEM:
        await query.edit_message_text("❌ Сессия устарела")
        return
    try:
        ev_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    events = await get_events()
    ev = next((e for e in events if e['id'] == ev_id), None)
    if not ev:
        await query.edit_message_text("❌ Событие не найдено")
        return
    await query.edit_message_text(
        f"🗑️ *Удалить событие?*\n\n"
        f"📅 {ev['type']}\n"
        f"🕐 {ev['datetime'][:16]}\n\n"
        f"⚠️ Это действие нельзя отменить.",
        parse_mode="Markdown",
        reply_markup=inline_kb([
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"conf_del_ev:{ev_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_del_ev")]
        ])
    )


async def event_delete_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        ev_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    if await delete_event(ev_id):
        await query.edit_message_text(f"✅ Событие {ev_id} удалено")
    else:
        await query.edit_message_text("❌ Ошибка")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📋 *Управление событиями*",
        parse_mode="Markdown",
        reply_markup=events_menu_kb
    )
    await set_user_state(context, STATE_EVENTS_MENU)


async def event_delete_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("❌ Удаление отменено")
    await query.edit_message_text("❌ Удаление отменено")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📋 *Управление событиями*",
        parse_mode="Markdown",
        reply_markup=events_menu_kb
    )
    await set_user_state(context, STATE_EVENTS_MENU)


# ==================== Public ====================

async def public_events_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    await update.effective_chat.send_action(action="typing")
    events = await get_events(upcoming_only=True)
    if not events:
        await update.message.reply_text("📭 Нет предстоящих событий")
        return
    text = "📅 *Предстоящие события:*\n\n" + "\n\n".join(
        f"*{e['type']}* ({e['datetime'][:10]})\n{e['announcement'][:100]}..."
        for e in events
    )
    await update.message.reply_text(text, parse_mode="Markdown")
