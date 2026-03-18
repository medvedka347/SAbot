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

from config import ROLE_ADMIN, ROLE_MENTOR, ROLE_LION, get_max_priority, get_primary_role
from db_utils import (
    get_user_roles, get_user_roles_simple, cleanup_expired_bans, get_ban_status,
    record_failed_attempt, clear_failed_attempts,
    get_user_by_username, update_user_id_by_username,
    get_user_by_id, get_user_mentor
)
from utils import (
    check_rate_limit, kb as kb_builder, user_kb, mentor_kb, admin_kb,
    get_main_keyboard, back_kb
)

# Импорты States для STATE_MAP - статические для стабильности в systemd
from handlers.materials import MaterialStates
from handlers.events import EventStates
from handlers.roles import RoleStates
from handlers.buddy import BuddyStates, lion_assign_start

router = Router(name="common")


# ==================== /start ====================

@router.message(CommandStart())
async def start_handler(message: Message):
    """Стартовый обработчик с проверкой бана и авторизации."""
    # Игнорируем /start в группах (не в ЛС)
    if message.chat.type != "private":
        return
    
    # Показываем typing для лучшего UX
    await message.chat.do("typing")
    
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
    
    # Проверяем роли (поддержка мультиролей)
    roles = await get_user_roles(user_id=user_id, username=username)
    role_keys = [r['role_key'] for r in roles]  # Для проверок
    
    if not roles:
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
    
    # Формируем строку ролей для отображения с эмодзи
    ROLE_EMOJI = {
        'lion': '🦁',
        'admin': '👑',
        'mentor': '🎓',
        'user': '👤'
    }
    role_display = ', '.join(f"{ROLE_EMOJI.get(r['role_key'], '🔹')} {r['role_key'].capitalize()}" for r in roles) if roles else '👤 Пользователь'
    welcome = f"Привет, {message.from_user.first_name}! 👋\n\n*Ваши роли:* {role_display}"
    
    # Выбираем клавиатуру по приоритету роли (admin > mentor > user)
    max_priority = get_max_priority(role_keys)
    if max_priority >= 300:  # admin или выше
        main_kb = admin_kb
    elif max_priority >= 200:  # mentor
        main_kb = mentor_kb
    else:
        main_kb = user_kb
    
    markup = main_kb if message.chat.type == "private" else None
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
    roles = await get_user_roles(user_id=user_id, username=username)
    role_keys = [r['role_key'] for r in roles]
    
    common = (
        "📚 *Материалы* — учебные материалы по разделам\n"
        "📅 *События комьюнити* — предстоящие вебинары и митапы\n"
        "⏱️ *Записаться на мок* — запись на пробное собеседование\n"
        "🤝 *Buddy* — система взаимопомощи\n"
        "🔍 `/search <запрос>` — поиск по материалам"
    )
    
    if ROLE_ADMIN in role_keys:
        extra = (
            "\n\n👑 *Администратор:*\n"
            "📦 Управление материалами (CRUD)\n"
            "👥 Управление ролями пользователей\n"
            "📋 Управление событиями\n"
            "🚫 Управление банами — просмотр и снятие банов"
        )
    elif ROLE_MENTOR in role_keys:
        extra = "\n\n🎓 *Ментор:*\n⚙️ Панель ментора"
    elif roles:
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
    roles = await get_user_roles(user_id=message.from_user.id, username=message.from_user.username)
    role_keys = [r['role_key'] for r in roles]
    max_priority = get_max_priority(role_keys)
    
    # Определяем клавиатуру и текст по приоритету ролей
    if max_priority >= 300:  # admin или выше
        panel_kb = admin_kb if message.chat.type == "private" else None
        await message.answer("🔧 Панель администратора", reply_markup=panel_kb)
    elif max_priority >= 200:  # mentor
        panel_kb = mentor_kb if message.chat.type == "private" else None
        await message.answer("🎓 Панель ментора", reply_markup=panel_kb)
    else:
        await message.answer("❌ Нет доступа.")


# ==================== Back Button ====================

# Маппинг для навигации "Назад" к ключевым состояниям (entry points)
# Принцип хлебных крошек - возвращаемся к ключевому состоянию модуля
ENTRY_POINT_MAP = {
    # Materials - entry point это selecting_stage (выбор раздела)
    MaterialStates.input_title: MaterialStates.selecting_stage,
    MaterialStates.input_link: MaterialStates.selecting_stage,
    MaterialStates.input_desc: MaterialStates.selecting_stage,
    MaterialStates.selecting_item: MaterialStates.selecting_stage,
    MaterialStates.editing: MaterialStates.selecting_stage,
    # Публичный просмотр - остаёмся в выборе раздела
    MaterialStates.selecting_stage_public: MaterialStates.selecting_stage_public,
    
    # Events - entry point это menu (главное меню событий)
    EventStates.input_type: EventStates.menu,
    EventStates.input_datetime: EventStates.menu,
    EventStates.input_link: EventStates.menu,
    EventStates.input_announcement: EventStates.menu,
    EventStates.confirm_announce: EventStates.menu,
    EventStates.selecting_item: EventStates.menu,
    EventStates.editing: EventStates.menu,
    
    # Roles - entry point это menu
    RoleStates.input_users: RoleStates.menu,
    RoleStates.selecting_role: RoleStates.menu,
    RoleStates.selecting_user_to_delete: RoleStates.menu,
    
    # Buddy - entry point это menu
    BuddyStates.input_full_name: BuddyStates.menu,
    BuddyStates.input_telegram_tag: BuddyStates.menu,
    BuddyStates.input_assigned_date: BuddyStates.menu,
    BuddyStates.selecting_status: BuddyStates.menu,
}


@router.message(F.text == "🏠 Главное меню")
async def main_menu_handler(message: Message, state: FSMContext):
    """Обработчик 'Главное меню' - всегда возвращает в стартовое меню.
    
    Отменяет текущую операцию и очищает все состояния.
    """
    # Очищаем состояние полностью
    await state.clear()
    
    # Получаем роли пользователя
    roles = await get_user_roles(user_id=message.from_user.id, username=message.from_user.username)
    role_display = ', '.join(r['role_key'] for r in roles) if roles else 'user'
    
    welcome = f"Привет, {message.from_user.first_name}! 👋\n\nРоли: *{role_display}*"
    kb = await get_main_keyboard(message.from_user.id) if message.chat.type == "private" else None
    
    await message.answer(welcome, parse_mode="Markdown", reply_markup=kb)


@router.message(F.text.in_(["🔙 Назад", "Назад"]))
async def back_handler(message: Message, state: FSMContext):
    """Обработчик 'Назад' - возвращает к ключевому состоянию (entry point).
    
    Принцип хлебных крошек - возвращаемся к ключевому состоянию модуля:
    - Для Materials: возвращаемся к selecting_stage (выбор раздела)
    - Для Events/Roles/Buddy: возвращаемся к menu (главное меню модуля)
    
    Если уже в ключевом состоянии - возвращаемся в главное меню бота.
    """
    # Получаем текущее состояние
    current_state = await state.get_state()
    
    if not current_state:
        # Нет активного состояния - просто показываем главное меню
        await main_menu_handler(message, state)
        return
    
    # Ищем entry point для текущего состояния
    # current_state приходит как строка "ModuleStates:state_name"
    entry_point = None
    for state_obj, entry_obj in ENTRY_POINT_MAP.items():
        if state_obj.state == current_state:
            entry_point = entry_obj
            break
    
    if entry_point:
        # Переходим к entry point
        await state.set_state(entry_point)
        
        # Определяем сообщение в зависимости от entry point
        entry_messages = {
            MaterialStates.selecting_stage: "📦 Управление материалами\n\nВыберите раздел:",
            EventStates.menu: "📋 Управление событиями",
            RoleStates.menu: "👥 Управление ролями",
            BuddyStates.menu: "🤝 Buddy - панель ментора",
        }
        
        msg_text = entry_messages.get(entry_point, "Выберите действие:")
        await message.answer(f"🔙 {msg_text}", reply_markup=back_kb)
    else:
        # Уже в entry point или неизвестное состояние - возвращаемся в главное меню
        await main_menu_handler(message, state)


# ==================== Buddy ====================

@router.message(F.text.in_(["🤝 Buddy", "Buddy"]))
async def buddy_handler(message: Message, state: FSMContext):
    """Обработчик раздела Buddy - разная логика для менторов и пользователей."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await state.clear()
    
    # Получаем роли пользователя (поддержка мультиролей)
    roles = await get_user_roles(user_id=message.from_user.id, username=message.from_user.username)
    role_keys = [r['role_key'] for r in roles]
    max_priority = get_max_priority(role_keys)
    
    # Приоритет ролей для Buddy: LION > MENTOR/ADMIN > USER
    is_lion = max_priority >= 400  # lion приоритет
    is_mentor = max_priority >= 200  # mentor или выше
    
    if is_lion:
        # Для Льва (мета-админа) - показываем панель управления всей системой
        lion_kb = kb_builder(["🦁 Панель Льва", "🔙 Назад"])
        await message.answer(
            "🤝 *Buddy*\n\n"
            "Вы имеете права Льва. Используйте панель для управления системой Buddy.",
            parse_mode="Markdown",
            reply_markup=lion_kb if message.chat.type == "private" else None
        )
    elif is_mentor:
        # Для менторов - показываем меню с кнопкой "Список менти"
        buddy_kb = kb_builder(["📋 Список менти", "➕ Добавить менти", "🔙 Назад"])
        await message.answer(
            "🤝 *Buddy - Панель ментора*\n\n"
            "Управляйте своими менти и отслеживайте их прогресс.",
            parse_mode="Markdown",
            reply_markup=buddy_kb if message.chat.type == "private" else None
        )
    else:
        # Для обычных пользователей - проверяем есть ли у них ментор
        # Получаем user_id из БД
        user = await get_user_by_username(message.from_user.username) if message.from_user.username else None
        if not user:
            user = await get_user_by_id(message.from_user.id)
        
        mentor = None
        if user and user.get('id'):
            mentor = await get_user_mentor(user['id'])
        
        if mentor:
            mentor_contact = f"@{mentor['mentor_username']}" if mentor['mentor_username'] else f"ID: {mentor['mentor_id']}"
            await message.answer(
                f"🤝 *Привет!*\n\n"
                f"Вот контакты твоего бадди: {mentor_contact}\n"
                f"Можешь обращаться к нему за помощью и поддержкой!",
                parse_mode="Markdown"
            )
        else:
            await message.answer(
                "🤝 *Привет!*\n\n"
                "Тебе пока не назначен бадди.\n"
                "Ожидай назначения от администратора или ментора.",
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
    kb = await get_main_keyboard(message.from_user.id) if message.chat.type == "private" else None
    await message.answer(
        "❌ *Нет доступа*\n\n"
        "Эта функция доступна только администраторам.",
        parse_mode="Markdown",
        reply_markup=kb
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
    
    # Отправляем подсказку (клавиатура только в ЛС)
    kb = await get_main_keyboard(message.from_user.id) if message.chat.type == "private" else None
    await message.answer(
        "❓ Не понял команду. Используйте кнопки меню или /start",
        reply_markup=kb
    )
