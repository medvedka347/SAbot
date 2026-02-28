"""
Общие команды и обработчики.

Включает:
- /start — стартовая команда с авторизацией
- /help — справка по командам
- ⚙️ Админка — вход в панель администратора/ментора
- 🔙 Назад — возврат в главное меню
- 🤝 Buddy — система взаимопомощи
- Fallback — обработка неизвестных команд
"""
import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

from config import ROLE_ADMIN, ROLE_MENTOR
from db_utils import (
    get_user_role, cleanup_expired_bans, get_ban_status, 
    record_failed_attempt, clear_failed_attempts, 
    get_user_by_username, update_user_id_by_username
)
from utils import (
    check_rate_limit, kb, user_kb, mentor_kb, admin_kb,
    get_main_keyboard
)

router = Router(name="common")


# ==================== /start ====================

@router.message(CommandStart())
async def start_handler(message: Message):
    """Стартовый обработчик с проверкой бана и авторизации."""
    # Игнорируем /start в группах (не в ЛС)
    if message.chat.type != "private":
        return
    
    user_id = message.from_user.id
    username = message.from_user.username
    
    # Проверяем бан (сначала чистим старые)
    await cleanup_expired_bans()
    ban = await get_ban_status(user_id=user_id, username=username)
    
    if ban:
        ban_level = ban['ban_level']
        ban_text = {1: "5 минут", 2: "10 минут", 3: "1 месяц"}.get(ban_level, "некоторое время")
        
        await message.answer(
            f"❌ *Доступ временно заблокирован*\n\n"
            f"Причина: превышено количество попыток авторизации\n"
            f"Длительность: {ban_text}\n\n"
            f"Попробуйте позже или обратитесь к администратору.",
            parse_mode="Markdown"
        )
        return
    
    # Проверяем роль
    role = await get_user_role(user_id=user_id, username=username)
    
    if not role:
        # Записываем неудачную попытку
        new_ban = await record_failed_attempt(user_id=user_id, username=username)
        
        if new_ban:
            ban_until = new_ban['banned_until']
            await message.answer(
                f"❌ *Доступ запрещен*\n\n"
                f"3 неудачные попытки авторизации.\n"
                f"Вы заблокированы до: `{ban_until.strftime('%Y-%m-%d %H:%M:%S')}`",
                parse_mode="Markdown"
            )
        else:
            await message.answer(
                f"❌ У вас нет доступа к боту.\n\n"
                f"⚠️ После 3 неудачных попыток вы получите временный бан."
            )
        return
    
    # Успешная авторизация - очищаем неудачные попытки
    await clear_failed_attempts(user_id=user_id, username=username)
    
    # Если пользователь был добавлен по username без ID - подхватываем его ID
    if username:
        user_from_db = await get_user_by_username(username)
        if user_from_db and user_from_db.get("user_id") is None:
            await update_user_id_by_username(username, user_id)
            logging.info(f"Подхвачен user_id {user_id} для @{username} при первой авторизации")
    
    welcome = f"Привет, {message.from_user.first_name}! 👋\n\nРоль: *{role}*"
    
    # Выбираем клавиатуру по роли
    if role == ROLE_ADMIN:
        kb = admin_kb
    elif role == ROLE_MENTOR:
        kb = mentor_kb
    else:
        kb = user_kb
    
    markup = kb if message.chat.type == "private" else None
    await message.answer(welcome, parse_mode="Markdown", reply_markup=markup)


# ==================== /help ====================

@router.message(Command("help"))
async def help_handler(message: Message):
    """Обработчик /help — список доступных функций по роли."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    user_id = message.from_user.id
    username = message.from_user.username
    role = await get_user_role(user_id=user_id, username=username)
    
    common = (
        "📚 *Материалы* — учебные материалы по разделам\n"
        "📅 *События комьюнити* — предстоящие вебинары и митапы\n"
        "⏱️ *Записаться на мок* — запись на пробное собеседование\n"
        "🤝 *Buddy* — система взаимопомощи\n"
        "🔍 `/search <запрос>` — поиск по материалам"
    )
    
    if role == ROLE_ADMIN:
        extra = (
            "\n\n👑 *Администратор:*\n"
            "📦 Управление материалами (CRUD)\n"
            "👥 Управление ролями пользователей\n"
            "📋 Управление событиями\n"
            "🚫 Управление банами — просмотр и снятие банов"
        )
    elif role == ROLE_MENTOR:
        extra = "\n\n🎓 *Ментор:*\n⚙️ Панель ментора"
    elif role:
        extra = ""
    else:
        extra = "\n\n❌ У вас нет доступа. Обратитесь к администратору."
    
    await message.answer(
        f"ℹ️ *Доступные функции:*\n\n{common}{extra}",
        parse_mode="Markdown"
    )


# ==================== Admin Panel ====================

@router.message(F.text == "⚙️ Админка")
async def admin_handler(message: Message, state: FSMContext):
    """Обработчик админки (⚙️ Админка)."""
    if message.text != "⚙️ Админка":
        return
    
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await state.clear()
    role = await get_user_role(user_id=message.from_user.id)
    kb_map = {ROLE_ADMIN: admin_kb, ROLE_MENTOR: mentor_kb}
    text_map = {ROLE_ADMIN: "🔧 Панель администратора", ROLE_MENTOR: "🎓 Панель ментора"}
    
    if role in kb_map:
        await message.answer(text_map[role], reply_markup=kb_map[role])
    else:
        await message.answer("❌ Нет доступа.")


# ==================== Back Button ====================

@router.message(F.text.in_(["🔙 Назад", "Назад"]))
async def back_handler(message: Message, state: FSMContext):
    """Универсальный обработчик 'Назад' - возвращает на предыдущий шаг или в главное меню."""
    from handlers.materials import MaterialStates
    from handlers.events import EventStates
    from handlers.roles import RoleStates
    
    current_state = await state.get_state()
    
    # Обработка состояний материалов
    if current_state == MaterialStates.input_title.state:
        await state.set_state(MaterialStates.selecting_stage)
        from utils import stage_kb
        await message.answer("➕ Выберите раздел для добавления:", reply_markup=stage_kb)
        return
    
    if current_state == MaterialStates.input_link.state:
        await state.set_state(MaterialStates.input_title)
        from utils import back_kb
        await message.answer("Введите название:", reply_markup=back_kb)
        return
    
    if current_state == MaterialStates.input_desc.state:
        await state.set_state(MaterialStates.input_link)
        from utils import back_kb
        await message.answer("Введите ссылку (https://...):", reply_markup=back_kb)
        return
    
    if current_state == MaterialStates.editing.state:
        from handlers.materials import materials_menu
        await materials_menu(message, state)
        return
    
    if current_state == MaterialStates.selecting_stage.state:
        from handlers.materials import materials_menu
        await materials_menu(message, state)
        return
    
    if current_state == MaterialStates.selecting_stage_public.state:
        # Для публичного просмотра - в главное меню
        await state.clear()
        role = await get_user_role(user_id=message.from_user.id, username=message.from_user.username)
        welcome = f"Привет, {message.from_user.first_name}! 👋\n\nРоль: *{role}*"
        kb = await get_main_keyboard(message.from_user.id)
        await message.answer(welcome, parse_mode="Markdown", reply_markup=kb)
        return
    
    # Обработка состояний событий
    if current_state == EventStates.input_type.state:
        from handlers.events import events_menu
        await events_menu(message, state)
        return
    
    if current_state == EventStates.input_datetime.state:
        await state.set_state(EventStates.input_type)
        from utils import back_kb
        await message.answer("Введите тип (Вебинар, Митап, Квиз):", reply_markup=back_kb)
        return
    
    if current_state == EventStates.input_link.state:
        await state.set_state(EventStates.input_datetime)
        from utils import back_kb
        await message.answer("Введите дату `2024-12-31 18:00:00`:", parse_mode="Markdown", reply_markup=back_kb)
        return
    
    if current_state == EventStates.input_announcement.state:
        await state.set_state(EventStates.input_link)
        from utils import back_kb
        await message.answer("Введите ссылку (или 'нет'):", reply_markup=back_kb)
        return
    
    if current_state == EventStates.confirm_announce.state:
        await state.set_state(EventStates.input_announcement)
        from utils import back_kb
        await message.answer("Введите анонс:", reply_markup=back_kb)
        return
    
    if current_state == EventStates.editing.state:
        from handlers.events import events_menu
        await events_menu(message, state)
        return
    
    # Обработка состояний ролей
    if current_state == RoleStates.input_users.state:
        from handlers.roles import roles_menu
        await roles_menu(message, state)
        return
    
    # По умолчанию - в главное меню
    await state.clear()
    role = await get_user_role(user_id=message.from_user.id, username=message.from_user.username)
    welcome = f"Привет, {message.from_user.first_name}! 👋\n\nРоль: *{role}*"
    kb = await get_main_keyboard(message.from_user.id)
    await message.answer(welcome, parse_mode="Markdown", reply_markup=kb)


# ==================== Buddy ====================

@router.message(F.text.in_(["🤝 Buddy", "Buddy"]))
async def buddy_handler(message: Message):
    """Обработчик раздела Buddy."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await message.answer(
        "🤝 *Buddy*\n\n"
        "тут будет анонс системы бадди",
        parse_mode="Markdown"
    )


# ==================== Access Denied Handlers ====================
# Эти хендлеры ловят админские команды от пользователей без прав
# и сообщают о недостатке доступа вместо fallback "непонятно"

@router.message(F.text.in_([
    "📦 Управление материалами",
    "📋 Управление событиями", 
    "👥 Управление ролями",
    "🚫 Управление банами"
]))
async def admin_access_denied_handler(message: Message, state: FSMContext):
    """Сообщает о недостатке прав для админских функций."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        return
    
    await state.clear()
    await message.answer(
        "❌ *Нет доступа*\n\n"
        "Эта функция доступна только администраторам.",
        parse_mode="Markdown",
        reply_markup=await get_main_keyboard(message.from_user.id)
    )


# ==================== Fallback ====================

@router.message()
async def fallback_handler(message: Message, state: FSMContext):
    """Fallback handler - ловит все неизвестные сообщения от авторизованных пользователей."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        return  # Тихо игнорируем если rate limit
    
    # Сбрасываем состояние
    await state.clear()
    
    # Отправляем подсказку
    await message.answer(
        "❓ Не понял команду. Используйте кнопки меню или /start",
        reply_markup=await get_main_keyboard(message.from_user.id)
    )
