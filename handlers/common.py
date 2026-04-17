"""
Общие команды и обработчики.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes

from config import ROLE_ADMIN, ROLE_MENTOR, ROLE_MANAGER, ROLE_ANALYST, can_access, get_primary_role
from db_utils import (
    get_user_roles, cleanup_expired_bans, get_ban_status,
    record_failed_attempt, clear_failed_attempts,
    get_user_by_username, update_user_id_by_username,
    get_user_by_id, get_user_mentor
)
from utils import check_rate_limit, kb, user_kb, mentor_kb, admin_kb, get_main_keyboard
from handlers.conversation_utils import clear_user_state


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    await update.effective_chat.send_action(action="typing")
    user_id = update.effective_user.id
    username = update.effective_user.username

    await cleanup_expired_bans()
    ban = await get_ban_status(user_id=user_id, username=username)
    if ban:
        ban_level = ban['ban_level']
        ban_text = {1: "5 минут", 2: "10 минут", 3: "1 месяц"}.get(ban_level, "некоторое время")
        await update.message.reply_text(
            f"❌ *Доступ временно заблокирован*\n\n"
            f"Причина: превышено количество попыток авторизации\n"
            f"Длительность: {ban_text}\n\n"
            f"Попробуйте позже или обратитесь к администратору.",
            parse_mode="Markdown"
        )
        return

    roles = await get_user_roles(user_id=user_id, username=username)
    if not roles:
        new_ban = await record_failed_attempt(user_id=user_id, username=username)
        if new_ban:
            ban_until = new_ban['banned_until']
            await update.message.reply_text(
                f"❌ *Доступ запрещен*\n\n"
                f"3 неудачные попытки авторизации.\n"
                f"Вы заблокированы до: `{ban_until.strftime('%Y-%m-%d %H:%M:%S')}`",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"❌ У вас нет доступа к боту.\n\n"
                f"⚠️ После 3 неудачных попыток вы получите временный бан."
            )
        return

    await clear_failed_attempts(user_id=user_id, username=username)
    if username:
        user_from_db = await get_user_by_username(username)
        if user_from_db and user_from_db.get("user_id") is None:
            await update_user_id_by_username(username, user_id)
            logging.info(f"Подхвачен user_id {user_id} для @{username} при первой авторизации")

    ROLE_EMOJI = {
        'admin': '👑', 'analyst': '📊', 'manager': '📋',
        'mentor': '🎓', 'user': '👤'
    }
    role_display = ', '.join(
        f"{ROLE_EMOJI.get(r['role_key'], '🔹')} {r['role_key'].capitalize()}"
        for r in roles
    ) if roles else '👤 Пользователь'
    welcome = f"Привет, {update.effective_user.first_name}! 👋\n\n*Ваши роли:* {role_display}"

    # Выбираем базовую клавиатуру по самой высокой роли (для эстетики)
    primary = get_primary_role([r['role_key'] for r in roles])
    if primary == ROLE_ADMIN:
        main_kb = admin_kb
    elif primary == ROLE_MENTOR:
        main_kb = mentor_kb
    else:
        main_kb = user_kb

    markup = main_kb if update.effective_chat.type == "private" else None
    await update.message.reply_text(welcome, parse_mode="Markdown", reply_markup=markup)


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return

    user_id = update.effective_user.id
    username = update.effective_user.username
    roles = await get_user_roles(user_id=user_id, username=username)
    role_keys = [r['role_key'] for r in roles]

    common_text = (
        "📚 *Материалы* — учебные материалы по разделам\n"
        "📅 *События комьюнити* — предстоящие вебинары и митапы\n"
        "⏱️ *Записаться на мок* — запись на пробное собеседование\n"
        "🤝 *Buddy* — система взаимопомощи\n"
        "🔍 `/search <запрос>` — поиск по материалам"
    )

    extra_parts = []
    if can_access("materials_crud", role_keys) or can_access("events_crud", role_keys) or can_access("roles_crud", role_keys) or can_access("bans_crud", role_keys):
        extra_parts.append(
            "\n\n👑 *Администратор:*\n"
            "📦 Управление материалами (CRUD)\n"
            "👥 Управление ролями пользователей\n"
            "📋 Управление событиями\n"
            "🚫 Управление банами — просмотр и снятие банов"
        )
    if can_access("materials_stats", role_keys):
        extra_parts.append("\n\n📊 *Аналитик:*\n📈 Статистика по материалам\n📋 Отчеты по Buddy")
    if can_access("buddy_assign", role_keys):
        extra_parts.append("\n\n📋 *Менеджер:*\n➕ Назначение бадди менторам")
    if can_access("buddy_mentor", role_keys):
        extra_parts.append("\n\n🎓 *Ментор:*\n📋 Список менти\n➕ Добавление менти")
    if not roles:
        extra_parts.append("\n\n❌ У вас нет доступа. Обратитесь к администратору.")

    await update.message.reply_text(
        f"ℹ️ *Доступные функции:*\n\n{common_text}{''.join(extra_parts)}",
        parse_mode="Markdown"
    )


async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text != "⚙️ Админка":
        return
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return

    await clear_user_state(context)
    roles = await get_user_roles(user_id=update.effective_user.id, username=update.effective_user.username)
    role_keys = [r['role_key'] for r in roles]

    if can_access("materials_crud", role_keys) or can_access("events_crud", role_keys) or can_access("roles_crud", role_keys) or can_access("bans_crud", role_keys):
        panel_kb = admin_kb if update.effective_chat.type == "private" else None
        await update.message.reply_text("🔧 Панель администратора", reply_markup=panel_kb)
    else:
        await update.message.reply_text("❌ Нет доступа.")


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await clear_user_state(context)
    roles = await get_user_roles(user_id=update.effective_user.id, username=update.effective_user.username)
    role_display = ', '.join(r['role_key'] for r in roles) if roles else 'user'
    welcome = f"Привет, {update.effective_user.first_name}! 👋\n\nРоли: *{role_display}*"
    kb = await get_main_keyboard(update.effective_user.id) if update.effective_chat.type == "private" else None
    await update.message.reply_text(welcome, parse_mode="Markdown", reply_markup=kb)


async def buddy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return

    await clear_user_state(context)
    roles = await get_user_roles(user_id=update.effective_user.id, username=update.effective_user.username)
    role_keys = [r['role_key'] for r in roles]

    # Собираем кнопки Buddy по capability
    buddy_buttons = []
    if can_access("buddy_mentor", role_keys):
        buddy_buttons.append("📋 Список менти")
    if can_access("buddy_add", role_keys):
        buddy_buttons.append("➕ Добавить менти")
    if can_access("buddy_assign", role_keys):
        buddy_buttons.append("➕ Назначить бадди")
    if can_access("buddy_analytics", role_keys):
        buddy_buttons.extend(["📊 Список менторов", "📋 Все менти"])

    if buddy_buttons:
        await update.message.reply_text(
            "🤝 *Buddy*\n\nВыберите действие:",
            parse_mode="Markdown",
            reply_markup=kb(buddy_buttons + ["🔙 Назад"])
        )
        return

    # Обычный пользователь — показываем инфо о назначенном бадди
    user = await get_user_by_username(update.effective_user.username) if update.effective_user.username else None
    if not user:
        user = await get_user_by_id(update.effective_user.id)
    mentor = None
    if user and user.get('id'):
        mentor = await get_user_mentor(user['id'])
    if mentor:
        mentor_contact = f"@{mentor['mentor_username']}" if mentor['mentor_username'] else f"ID: {mentor['mentor_id']}"
        await update.message.reply_text(
            f"🤝 *Привет!*\n\n"
            f"Вот контакты твоего бадди: {mentor_contact}\n"
            f"Можешь обращаться к нему за помощью и поддержкой!",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "🤝 *Привет!*\n\n"
            "Тебе пока не назначен бадди.\n"
            "Ожидай назначения от администратора или ментора.",
            parse_mode="Markdown"
        )


async def admin_access_denied_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        return
    await clear_user_state(context)
    kb = await get_main_keyboard(update.effective_user.id) if update.effective_chat.type == "private" else None
    await update.message.reply_text(
        "❌ *Нет доступа*\n\n"
        "Эта функция доступна только администраторам.",
        parse_mode="Markdown",
        reply_markup=kb
    )


async def fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        return
    await clear_user_state(context)
    kb = await get_main_keyboard(update.effective_user.id) if update.effective_chat.type == "private" else None
    await update.message.reply_text(
        "❓ Не понял команду. Используйте кнопки меню или /start",
        reply_markup=kb
    )
