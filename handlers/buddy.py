"""
Модуль Buddy - система наставничества.
"""
import logging
import re
import traceback
import aiosqlite
from datetime import datetime
from telegram import Update, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import MODULE_ACCESS, can_access
from db_utils import (
    require_any_role, get_user_by_id, get_user_by_username, get_user_by_db_id,
    add_mentorship, get_mentor_mentees, update_mentorship_status,
    delete_mentorship, get_mentorship_by_id, get_mentor_stats, get_all_mentors,
    get_all_mentorships_for_lion
)
from utils import check_rate_limit, inline_kb, back_kb, get_main_keyboard, parse_date_flexible
from handlers.conversation_utils import get_user_state, set_user_state, clear_user_state

STATE_BUDDY_MENU = "buddy_menu"
STATE_BUDDY_INPUT_FULL_NAME = "buddy_input_full_name"
STATE_BUDDY_INPUT_TELEGRAM_TAG = "buddy_input_telegram_tag"
STATE_BUDDY_INPUT_ASSIGNED_DATE = "buddy_input_assigned_date"

BUDDY_STATUSES = {
    'active': 'Активно',
    'completed': 'Завершено',
    'paused': 'На паузе',
    'dropped': 'Брошено'
}


def status_kb(mentorship_id: int):
    buttons = []
    for status_key, status_name in BUDDY_STATUSES.items():
        buttons.append([InlineKeyboardButton(
            text=status_name,
            callback_data=f"buddy_status:{mentorship_id}:{status_key}"
        )])
    return inline_kb(buttons)


def mentee_actions_kb(mentorship_id: int):
    return inline_kb([
        [InlineKeyboardButton(text="🔄 Изменить статус", callback_data=f"buddy_chstatus:{mentorship_id}")],
        [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"buddy_del:{mentorship_id}")],
        [InlineKeyboardButton(text="◀️ Назад к списку", callback_data="buddy_back_to_list")]
    ])


def format_mentee(mentee: dict, index: int = None) -> str:
    prefix = f"{index}. " if index else "• "
    name = mentee['full_name']
    tag = f" @{mentee['telegram_tag']}" if mentee['telegram_tag'] else ""
    date = mentee['assigned_date']
    status = BUDDY_STATUSES.get(mentee['status'], mentee['status'])
    status_emoji = {
        'active': '🟢',
        'completed': '✅',
        'paused': '⏸️',
        'dropped': '❌'
    }.get(mentee['status'], '⚪')
    return f"{prefix}{status_emoji} *{name}*{tag}\n   📅 {date} | 📊 {status}"


# ==================== Mentor: List & Add ====================

async def buddy_list_mentees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth = await require_any_role(update, context, MODULE_ACCESS["buddy_mentor"])
    if not auth:
        return
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.effective_message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    await set_user_state(context, STATE_BUDDY_MENU)
    user = await get_user_by_id(update.effective_user.id)
    if not user:
        await update.effective_message.reply_text("❌ Ошибка: не найден профиль ментора")
        return
    mentor_db_id = user['id']
    mentees = await get_mentor_mentees(mentor_db_id)
    if not mentees:
        await update.effective_message.reply_text(
            "📭 *У вас пока нет менти*\n\n"
            "Нажмите «➕ Добавить менти» чтобы добавить первого.",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        return
    lines = [f"👥 *Ваши менти ({len(mentees)}):*\n"]
    for i, mentee in enumerate(mentees, 1):
        lines.append(format_mentee(mentee, i))
    keyboard = []
    for mentee in mentees:
        keyboard.append([InlineKeyboardButton(
            text=f"⚙️ {mentee['full_name'][:30]}",
            callback_data=f"buddy_mentee:{mentee['id']}"
        )])
    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=inline_kb(keyboard) if keyboard else back_kb
    )


async def buddy_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth = await require_any_role(update, context, MODULE_ACCESS["buddy_add"])
    if not auth:
        return
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.effective_message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    # Если это менеджер/админ назначает бадди — mentor_id берется позже из state
    # Если ментор — сразу свой id
    user = await get_user_by_id(update.effective_user.id)
    if not user:
        await update.effective_message.reply_text("❌ Ошибка: не найден профиль")
        return
    context.user_data["buddy_mentor_id"] = user['id']
    context.user_data["buddy_is_lion_assigning"] = False
    await set_user_state(context, STATE_BUDDY_INPUT_FULL_NAME)
    await update.effective_message.reply_text(
        "➕ *Добавление нового менти*\n\n"
        "*Шаг 1/3: ФИО*\n"
        "Введите ФИО менти (или ФИ без отчества):\n\n"
        "💡 Пример: `Иванов Иван Иванович`",
        parse_mode="Markdown",
        reply_markup=back_kb
    )


async def _process_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text:
        await update.message.reply_text(
            "❌ *Ошибка:* ФИО не может быть пустым\n\n"
            "Введите ФИО менти (от 2 до 100 символов):",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        return STATE_BUDDY_INPUT_FULL_NAME
    full_name = update.message.text.strip()
    if len(full_name) < 2:
        await update.message.reply_text(
            "❌ *Ошибка:* ФИО слишком короткое\n\n"
            "Минимум 2 символа. Попробуйте ещё раз:",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        return STATE_BUDDY_INPUT_FULL_NAME
    if len(full_name) > 100:
        await update.message.reply_text(
            "❌ *Ошибка:* ФИО слишком длинное\n\n"
            "Максимум 100 символов. Попробуйте ещё раз:",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        return STATE_BUDDY_INPUT_FULL_NAME
    context.user_data["buddy_full_name"] = full_name
    await set_user_state(context, STATE_BUDDY_INPUT_TELEGRAM_TAG)
    await update.message.reply_text(
        "📱 *Шаг 2/3: Telegram тег*\n\n"
        "Введите тег в Telegram (@username):\n"
        "Примеры: `@ivanov` или `@ivan_ivanov`\n\n"
        "💡 Если тега нет — отправьте «пропустить»",
        parse_mode="Markdown",
        reply_markup=back_kb
    )
    return STATE_BUDDY_INPUT_TELEGRAM_TAG


async def buddy_add_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_BUDDY_INPUT_FULL_NAME:
        return
    await _process_full_name(update, context)


async def _process_telegram_tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text:
        await update.message.reply_text(
            "❌ *Ошибка:* пустое сообщение\n\n"
            "Введите тег в Telegram (@username) или «пропустить»:",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        return STATE_BUDDY_INPUT_TELEGRAM_TAG
    tag = update.message.text.strip()
    if tag.lower() in ['пропустить', 'нет', '-', 'skip']:
        tag = None
    else:
        if not tag.startswith('@'):
            tag = '@' + tag
        username_part = tag[1:]
        if not username_part:
            await update.message.reply_text(
                "❌ *Ошибка:* пустой тег\n\n"
                "Введите тег в формате `@username` или «пропустить»:",
                parse_mode="Markdown",
                reply_markup=back_kb
            )
            return STATE_BUDDY_INPUT_TELEGRAM_TAG
        if len(username_part) < 5:
            await update.message.reply_text(
                "❌ *Ошибка:* тег слишком короткий\n\n"
                f"`{tag}` — минимум 5 символов после @\n"
                "Введите корректный тег или «пропустить»:",
                parse_mode="Markdown",
                reply_markup=back_kb
            )
            return STATE_BUDDY_INPUT_TELEGRAM_TAG
        if len(username_part) > 32:
            await update.message.reply_text(
                "❌ *Ошибка:* тег слишком длинный\n\n"
                f"Максимум 32 символа. Введите корректный тег или «пропустить»:",
                parse_mode="Markdown",
                reply_markup=back_kb
            )
            return STATE_BUDDY_INPUT_TELEGRAM_TAG
    context.user_data["buddy_telegram_tag"] = tag
    await set_user_state(context, STATE_BUDDY_INPUT_ASSIGNED_DATE)
    today = datetime.now().strftime("%d.%m.%y")
    await update.message.reply_text(
        f"Введите дату назначения (ДД.ММ.ГГ):\n"
        f"(по умолчанию: {today})",
        reply_markup=back_kb
    )
    return STATE_BUDDY_INPUT_ASSIGNED_DATE


async def buddy_add_telegram_tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_BUDDY_INPUT_TELEGRAM_TAG:
        return
    await _process_telegram_tag(update, context)


async def buddy_add_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_BUDDY_INPUT_ASSIGNED_DATE:
        return
    if not update.message.text:
        await update.message.reply_text(
            "❌ *Ошибка:* введите дату\n\n"
            "Примеры: `07.03.26` или `сегодня`",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        return STATE_BUDDY_INPUT_ASSIGNED_DATE
    date_str = parse_date_flexible(update.message.text)
    if date_str is None:
        await update.message.reply_text(
            "❌ *Неверный формат даты*\n\n"
            "Используйте один из форматов:\n"
            "• `07.03.26` — день.месяц.год\n"
            "• `07.03.2026` — с полным годом\n"
            "• `07,03,26` — через запятую\n"
            "• `сегодня` — сегодняшняя дата\n\n"
            "Попробуйте ещё раз:",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        return STATE_BUDDY_INPUT_ASSIGNED_DATE
    full_name = context.user_data.get('buddy_full_name')
    if not full_name:
        await update.message.reply_text(
            "❌ *Ошибка:* данные потеряны\n\n"
            "Сессия устарела или данные не сохранились.\n"
            "Пожалуйста, начните добавление менти заново.",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        await clear_user_state(context)
        return None
    mentor_id = context.user_data.get('buddy_mentor_id')
    is_assigning = context.user_data.get('buddy_is_lion_assigning', False)
    try:
        await add_mentorship(
            mentor_id=mentor_id,
            mentee_full_name=full_name,
            mentee_telegram_tag=context.user_data.get('buddy_telegram_tag'),
            assigned_date=date_str,
            status='active'
        )
        tag_info = f"\n📱 Тег: {context.user_data.get('buddy_telegram_tag')}" if context.user_data.get('buddy_telegram_tag') else ""
        if is_assigning:
            await update.message.reply_text(
                f"✅ *Менти назначен ментору!*\n\n"
                f"👤 {full_name}{tag_info}\n"
                f"📅 Дата назначения: {date_str}\n"
                f"📊 Статус: Активно",
                parse_mode="Markdown",
                reply_markup=back_kb
            )
        else:
            await update.message.reply_text(
                f"✅ *Менти добавлен!*\n\n"
                f"👤 {full_name}{tag_info}\n"
                f"📅 Дата назначения: {date_str}\n"
                f"📊 Статус: Активно",
                parse_mode="Markdown",
                reply_markup=back_kb
            )
        await clear_user_state(context)
        return None
    except ValueError as e:
        logging.error(f"Ошибка данных при добавлении менти: {e}")
        await update.message.reply_text(
            f"❌ *Ошибка данных:*\n\n{str(e)}\n\nОбратитесь к администратору.",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        await clear_user_state(context)
        return None
    except aiosqlite.IntegrityError as e:
        logging.error(f"Ошибка целостности БД: {e}")
        await update.message.reply_text(
            "❌ *Ошибка базы данных*\n\n"
            "Нарушение целостности данных.\n"
            "Возможно, этот менти уже существует или ID ментора некорректен.\n\n"
            "Обратитесь к администратору.",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        await clear_user_state(context)
        return None
    except Exception as e:
        error_details = str(e)[:200]
        logging.error(f"Неизвестная ошибка при добавлении менти: {e}")
        logging.error(traceback.format_exc())
        error_clean = re.sub(r'[_*`\[\]()]', '', error_details)
        await update.message.reply_text(
            f"❌ Системная ошибка\n\n"
            f"Не удалось сохранить данные.\n\n"
            f"Детали:\n{error_clean}\n\n"
            f"Пожалуйста, сообщите администратору.",
            reply_markup=back_kb
        )
        await clear_user_state(context)
        return None


# ==================== Callbacks ====================

async def buddy_show_mentee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    auth = await require_any_role(update, context, MODULE_ACCESS["buddy_mentor"] | MODULE_ACCESS["buddy_assign"])
    if not auth:
        return
    try:
        mentorship_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    mentee = await get_mentorship_by_id(mentorship_id)
    if not mentee:
        await query.edit_message_text("❌ Менти не найден")
        return
    current_user = await get_user_by_id(query.from_user.id)
    is_owner = current_user and mentee['mentor_id'] == current_user['id']
    is_manager_or_admin = can_access("buddy_assign", auth['role_keys']) or can_access("materials_crud", auth['role_keys'])
    if not is_owner and not is_manager_or_admin:
        await query.edit_message_text("❌ У вас нет доступа к этому менти")
        return
    text = format_mentee(mentee)
    text = f"📋 *Детали менти:*\n\n{text}"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=mentee_actions_kb(mentorship_id))


async def buddy_change_status_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    auth = await require_any_role(update, context, MODULE_ACCESS["buddy_mentor"] | MODULE_ACCESS["buddy_assign"])
    if not auth:
        return
    try:
        mentorship_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    await query.edit_message_text(
        "🔄 *Выберите новый статус:*",
        parse_mode="Markdown",
        reply_markup=status_kb(mentorship_id)
    )


async def buddy_set_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    auth = await require_any_role(update, context, MODULE_ACCESS["buddy_mentor"] | MODULE_ACCESS["buddy_assign"])
    if not auth:
        return
    try:
        parts = query.data.split(":")
        mentorship_id = int(parts[1])
        new_status = parts[2]
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    if await update_mentorship_status(mentorship_id, new_status):
        status_name = BUDDY_STATUSES.get(new_status, new_status)
        await query.edit_message_text(
            f"✅ Статус обновлён: *{status_name}*",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text("❌ Ошибка обновления статуса")


async def buddy_delete_mentee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    auth = await require_any_role(update, context, MODULE_ACCESS["buddy_mentor"] | MODULE_ACCESS["buddy_assign"])
    if not auth:
        return
    try:
        mentorship_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    mentee = await get_mentorship_by_id(mentorship_id)
    if not mentee:
        await query.edit_message_text("❌ Менти не найден")
        return
    await query.edit_message_text(
        f"🗑️ *Удалить менти?*\n\n"
        f"👤 {mentee['full_name']}\n\n"
        f"Это действие нельзя отменить.",
        parse_mode="Markdown",
        reply_markup=inline_kb([
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"buddy_conf_del:{mentorship_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"buddy_mentee:{mentorship_id}")]
        ])
    )


async def buddy_confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    auth = await require_any_role(update, context, MODULE_ACCESS["buddy_mentor"] | MODULE_ACCESS["buddy_assign"])
    if not auth:
        return
    try:
        mentorship_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    if await delete_mentorship(mentorship_id):
        await query.edit_message_text("✅ Менти удалён")
    else:
        await query.edit_message_text("❌ Ошибка удаления")


async def buddy_back_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await buddy_list_mentees(update, context)


# ==================== Analytics (Analyst / Admin) ====================

async def buddy_analytics_mentors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth = await require_any_role(update, context, MODULE_ACCESS["buddy_analytics"])
    if not auth:
        return
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.effective_message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    mentors = await get_all_mentors()
    if not mentors:
        await update.effective_message.reply_text("📭 Нет менторов в системе", reply_markup=back_kb)
        return
    lines = [f"👥 *Менторы ({len(mentors)}):*\n"]
    keyboard = []
    for i, mentor in enumerate(mentors, 1):
        stats = await get_mentor_stats(mentor['id'])
        contact = f"@{mentor['username']}" if mentor['username'] else f"ID:{mentor['user_id']}"
        lines.append(
            f"{i}. {contact}\n"
            f"   🟢 {stats['active']} | ✅ {stats['completed']} | ❌ {stats['dropped']}"
        )
        keyboard.append([InlineKeyboardButton(
            text=f"📊 {contact[:20]}",
            callback_data=f"buddy_report:{mentor['id']}"
        )])
    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=inline_kb(keyboard)
    )


async def buddy_analytics_mentor_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    auth = await require_any_role(update, context, MODULE_ACCESS["buddy_analytics"])
    if not auth:
        return
    try:
        mentor_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    mentor = await get_user_by_db_id(mentor_id)
    mentees = await get_mentor_mentees(mentor_id)
    stats = await get_mentor_stats(mentor_id)
    mentor_contact = f"@{mentor['username']}" if mentor and mentor['username'] else "Неизвестно"
    lines = [
        f"📊 *Отчет по ментору*\n",
        f"👤 Ментор: {mentor_contact}\n",
        f"*Статистика:*",
        f"🟢 Активно: {stats['active']}",
        f"✅ Завершено: {stats['completed']}",
        f"❌ Брошено: {stats['dropped']}",
        f"📊 Всего: {stats['total']}\n",
        f"*Список менти:*"
    ]
    for mentee in mentees:
        lines.append(format_mentee(mentee))
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=inline_kb([
            [InlineKeyboardButton(text="◀️ Назад к списку", callback_data="buddy_report_back")]
        ])
    )


async def buddy_analytics_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await buddy_analytics_mentors(update, context)


async def buddy_analytics_all_mentees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth = await require_any_role(update, context, MODULE_ACCESS["buddy_analytics"])
    if not auth:
        return
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.effective_message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    mentorships = await get_all_mentorships_for_lion()
    if not mentorships:
        await update.effective_message.reply_text("📭 Нет менти в системе", reply_markup=back_kb)
        return
    lines = [f"📋 *Все менти ({len(mentorships)}):*\n"]
    for m in mentorships:
        mentor_contact = f"@{m['mentor_username']}" if m['mentor_username'] else f"ID:{m['mentor_user_id']}"
        status_emoji = {
            'active': '🟢',
            'completed': '✅',
            'paused': '⏸️',
            'dropped': '❌'
        }.get(m['status'], '⚪')
        lines.append(
            f"• *{m['mentee_name']}*\n"
            f"  👤 Ментор: {mentor_contact}\n"
            f"  {status_emoji} {BUDDY_STATUSES.get(m['status'], m['status'])} | 📅 {m['assigned_date']}"
        )
    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=back_kb
    )


# ==================== Manager (Manager / Admin) ====================

async def buddy_manager_assign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth = await require_any_role(update, context, MODULE_ACCESS["buddy_assign"])
    if not auth:
        return
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.effective_message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    mentors = await get_all_mentors()
    if not mentors:
        await update.effective_message.reply_text("❌ Нет доступных менторов", reply_markup=back_kb)
        return
    keyboard = []
    for mentor in mentors:
        contact = f"@{mentor['username']}" if mentor['username'] else f"ID:{mentor['user_id']}"
        keyboard.append([InlineKeyboardButton(
            text=f"👤 {contact[:30]}",
            callback_data=f"buddy_mgr_sel:{mentor['id']}"
        )])
    await update.effective_message.reply_text(
        "🦁 *Назначение бадди*\n\n"
        "Выберите ментора:",
        parse_mode="Markdown",
        reply_markup=inline_kb(keyboard)
    )


async def buddy_manager_select_mentor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    auth = await require_any_role(update, context, MODULE_ACCESS["buddy_assign"])
    if not auth:
        return
    try:
        mentor_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    logging.info(f"MANAGER_SELECT_MENTOR: mentor_id={mentor_id}")
    context.user_data["buddy_mentor_id"] = mentor_id
    context.user_data["buddy_is_lion_assigning"] = True
    await set_user_state(context, STATE_BUDDY_INPUT_FULL_NAME)
    await query.edit_message_text(
        "✏️ *Назначение бадди*\n\n"
        "Введите ФИО менти:",
        parse_mode="Markdown"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Введите ФИО менти:", reply_markup=back_kb)
