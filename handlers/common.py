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

from config import ROLE_ADMIN, ROLE_MENTOR, ROLE_LION
from db_utils import (
    get_user_roles, cleanup_expired_bans, get_ban_status, 
    record_failed_attempt, clear_failed_attempts, 
    get_user_by_username, update_user_id_by_username,
    get_user_by_id, get_user_mentor
)
from utils import (
    check_rate_limit, kb as kb_builder, user_kb, mentor_kb, admin_kb,
    get_main_keyboard, back_kb
)

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
    
    # Формируем строку ролей для отображения
    role_display = ', '.join(roles) if roles else 'user'
    welcome = f"Привет, {message.from_user.first_name}! 👋\n\nРоли: *{role_display}*"
    
    # Выбираем клавиатуру по роли (приоритет: admin > mentor > user)
    if ROLE_ADMIN in roles:
        main_kb = admin_kb
    elif ROLE_MENTOR in roles:
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
    
    common = (
        "📚 *Материалы* — учебные материалы по разделам\n"
        "📅 *События комьюнити* — предстоящие вебинары и митапы\n"
        "⏱️ *Записаться на мок* — запись на пробное собеседование\n"
        "🤝 *Buddy* — система взаимопомощи\n"
        "🔍 `/search <запрос>` — поиск по материалам"
    )
    
    if ROLE_ADMIN in roles:
        extra = (
            "\n\n👑 *Администратор:*\n"
            "📦 Управление материалами (CRUD)\n"
            "👥 Управление ролями пользователей\n"
            "📋 Управление событиями\n"
            "🚫 Управление банами — просмотр и снятие банов"
        )
    elif ROLE_MENTOR in roles:
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
    
    # Определяем клавиатуру и текст по приоритету ролей
    if ROLE_ADMIN in roles:
        panel_kb = admin_kb if message.chat.type == "private" else None
        await message.answer("🔧 Панель администратора", reply_markup=panel_kb)
    elif ROLE_MENTOR in roles:
        panel_kb = mentor_kb if message.chat.type == "private" else None
        await message.answer("🎓 Панель ментора", reply_markup=panel_kb)
    else:
        await message.answer("❌ Нет доступа.")


# ==================== Back Button ====================

# Маппинг строковых ключей состояний для навигации назад
STATE_MAP = {
    # Materials
    "selecting_stage": lambda: __import__('handlers.materials', fromlist=['MaterialStates']).MaterialStates.selecting_stage,
    "selecting_stage_public": lambda: __import__('handlers.materials', fromlist=['MaterialStates']).MaterialStates.selecting_stage_public,
    "input_title": lambda: __import__('handlers.materials', fromlist=['MaterialStates']).MaterialStates.input_title,
    "input_link": lambda: __import__('handlers.materials', fromlist=['MaterialStates']).MaterialStates.input_link,
    "input_desc": lambda: __import__('handlers.materials', fromlist=['MaterialStates']).MaterialStates.input_desc,
    "selecting_item": lambda: __import__('handlers.materials', fromlist=['MaterialStates']).MaterialStates.selecting_item,
    "editing": lambda: __import__('handlers.materials', fromlist=['MaterialStates']).MaterialStates.editing,
    # Events
    "input_type": lambda: __import__('handlers.events', fromlist=['EventStates']).EventStates.input_type,
    "input_datetime": lambda: __import__('handlers.events', fromlist=['EventStates']).EventStates.input_datetime,
    "input_link_evt": lambda: __import__('handlers.events', fromlist=['EventStates']).EventStates.input_link,
    "input_announcement": lambda: __import__('handlers.events', fromlist=['EventStates']).EventStates.input_announcement,
    "confirm_announce": lambda: __import__('handlers.events', fromlist=['EventStates']).EventStates.confirm_announce,
    # Roles
    "input_users": lambda: __import__('handlers.roles', fromlist=['RoleStates']).RoleStates.input_users,
    "selecting_role": lambda: __import__('handlers.roles', fromlist=['RoleStates']).RoleStates.selecting_role,
    "selecting_user_to_delete": lambda: __import__('handlers.roles', fromlist=['RoleStates']).RoleStates.selecting_user_to_delete,
    # Buddy
    "input_full_name": lambda: __import__('handlers.buddy', fromlist=['BuddyStates']).BuddyStates.input_full_name,
    "input_telegram_tag": lambda: __import__('handlers.buddy', fromlist=['BuddyStates']).BuddyStates.input_telegram_tag,
    "input_assigned_date": lambda: __import__('handlers.buddy', fromlist=['BuddyStates']).BuddyStates.input_assigned_date,
}


@router.message(F.text.in_(["🔙 Назад", "Назад"]))
async def back_handler(message: Message, state: FSMContext):
    """Обработчик 'Назад' - возвращает на предыдущий шаг или в главное меню.
    
    Использует стек истории _state_history для поддержки многоуровневого возврата.
    """
    data = await state.get_data()
    
    # Пробуем сначала новый стек истории
    state_history = data.get("_state_history", [])
    prev_state_key = data.get("_prev_state")
    
    # DEBUG: логируем что пришло
    logging.info(f"BACK_HANDLER: state_history={state_history}, prev_state_key={prev_state_key!r}")
    
    # Специальная обработка для Льва при назначении бадди
    if prev_state_key == "menu" and data.get("lion_action") == "assign_mentee":
        # Возврат к выбору ментора для льва
        from handlers.buddy import lion_assign_start
        await lion_assign_start(message, state)
        return
    
    # Общая обработка для "menu" - возврат в главное меню
    if prev_state_key == "menu":
        await state.clear()
        roles = await get_user_roles(user_id=message.from_user.id, username=message.from_user.username)
        role_display = ', '.join(roles) if roles else 'user'
        welcome = f"Привет, {message.from_user.first_name}! 👋\n\nРоли: *{role_display}*"
        kb = await get_main_keyboard(message.from_user.id) if message.chat.type == "private" else None
        await message.answer(welcome, parse_mode="Markdown", reply_markup=kb)
        return
    
    # Используем стек истории если он есть
    if state_history:
        # Берём последнее состояние из стека
        prev_state_key = state_history.pop()
        
        if prev_state_key in STATE_MAP:
            try:
                prev_state = STATE_MAP[prev_state_key]()
                await state.set_state(prev_state)
                # Сохраняем обновлённый стек
                await state.update_data(_state_history=state_history)
                
                # Формируем сообщение в зависимости от состояния
                back_messages = {
                    "selecting_stage": "Выберите раздел:",
                    "selecting_stage_public": "Выберите раздел:",
                    "input_title": "Введите название:",
                    "input_link": "Введите ссылку (https://...):",
                    "input_desc": "Введите описание (или 'пропустить'):",
                    "selecting_item": "Выберите из списка:",
                    "editing": "Отправьте новые данные (используйте '.' для пропуска):",
                    "input_type": "Введите тип (Вебинар, Митап, Квиз):",
                    "input_datetime": "Введите дату `2024-12-31 18:00:00`:",
                    "input_link_evt": "Введите ссылку (или 'нет'):",
                    "input_announcement": "Введите анонс:",
                    "input_users": "Введите пользователей (ID или @username):",
                    "selecting_role": "Выберите роль:",
                    # Buddy
                    "input_full_name": "Введите ФИО менти:",
                    "input_telegram_tag": "Введите тег в Telegram (@username):",
                    "input_assigned_date": "Введите дату назначения (ДД.ММ.ГГ):",
                }
                msg_text = back_messages.get(prev_state_key, "Вернулся на шаг назад.")
                await message.answer(f"🔙 {msg_text}", reply_markup=back_kb)
                return
            except Exception as e:
                logging.error(f"BACK_HANDLER ERROR (stack): {e}", exc_info=True)
                # При ошибке продолжаем к fallback
    
    # Fallback на старую систему _prev_state/_prev_chain
    if prev_state_key and prev_state_key in STATE_MAP:
        try:
            prev_state = STATE_MAP[prev_state_key]()
            await state.set_state(prev_state)
            # Восстанавливаем цепочку для следующего назад (если есть)
            prev_chain = data.get("_prev_chain")
            await state.update_data(_prev_state=prev_chain, _prev_chain=None)
            
            # Формируем сообщение в зависимости от состояния
            back_messages = {
                "selecting_stage": "Выберите раздел:",
                "selecting_stage_public": "Выберите раздел:",
                "input_title": "Введите название:",
                "input_link": "Введите ссылку (https://...):",
                "input_desc": "Введите описание (или 'пропустить'):",
                "selecting_item": "Выберите из списка:",
                "editing": "Отправьте новые данные (используйте '.' для пропуска):",
                "input_type": "Введите тип (Вебинар, Митап, Квиз):",
                "input_datetime": "Введите дату `2024-12-31 18:00:00`:",
                "input_link_evt": "Введите ссылку (или 'нет'):",
                "input_announcement": "Введите анонс:",
                "input_users": "Введите пользователей (ID или @username):",
                "selecting_role": "Выберите роль:",
                # Buddy
                "input_full_name": "Введите ФИО менти:",
                "input_telegram_tag": "Введите тег в Telegram (@username):",
                "input_assigned_date": "Введите дату назначения (ДД.ММ.ГГ):",
            }
            msg_text = back_messages.get(prev_state_key, "Вернулся на шаг назад.")
            await message.answer(f"🔙 {msg_text}", reply_markup=back_kb)
            return
        except Exception as e:
            # При ошибке - в главное меню
            logging.error(f"BACK_HANDLER ERROR: {e}", exc_info=True)
            pass
    
    # Нет истории или ошибка - в главное меню
    await state.clear()
    roles = await get_user_roles(user_id=message.from_user.id, username=message.from_user.username)
    role_display = ', '.join(roles) if roles else 'user'
    welcome = f"Привет, {message.from_user.first_name}! 👋\n\nРоли: *{role_display}*"
    kb = await get_main_keyboard(message.from_user.id) if message.chat.type == "private" else None
    await message.answer(welcome, parse_mode="Markdown", reply_markup=kb)


# ==================== Buddy ====================

@router.message(F.text.in_(["🤝 Buddy", "Buddy"]))
async def buddy_handler(message: Message, state: FSMContext):
    """Обработчик раздела Buddy - разная логика для менторов и пользователей."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await state.clear()
    
    # Если пользователь был добавлен по username без ID - подхватываем его ID
    if message.from_user.username:
        user_from_db = await get_user_by_username(message.from_user.username)
        if user_from_db and user_from_db.get("user_id") is None:
            await update_user_id_by_username(message.from_user.username, message.from_user.id)
            logging.info(f"Подхвачен user_id {message.from_user.id} для @{message.from_user.username} в buddy_handler")
    
    # Получаем роли пользователя (поддержка мультиролей)
    roles = await get_user_roles(user_id=message.from_user.id, username=message.from_user.username)
    
    # DEBUG: логируем роли
    logging.info(f"BUDDY_HANDLER: user_id={message.from_user.id}, username={message.from_user.username}, roles={roles}")
    
    if ROLE_LION in roles:
        # Для Льва (мета-админа) - показываем панель управления всей системой
        from utils import kb
        lion_kb = kb(["🦁 Панель Льва", "🔙 Назад"])
        await message.answer(
            "🤝 *Buddy*\n\n"
            "Вы имеете права Льва. Используйте панель для управления системой Buddy.",
            parse_mode="Markdown",
            reply_markup=lion_kb if message.chat.type == "private" else None
        )
    elif ROLE_MENTOR in roles:
        # Для менторов - показываем меню с кнопкой "Список менти"
        from utils import kb
        buddy_kb = kb(["📋 Список менти", "➕ Добавить менти", "🔙 Назад"])
        await message.answer(
            "🤝 *Buddy - Панель ментора*\n\n"
            "Управляйте своими менти и отслеживайте их прогресс.",
            parse_mode="Markdown",
            reply_markup=buddy_kb if message.chat.type == "private" else None
        )
    else:
        # Для обычных пользователей - проверяем есть ли у них ментор
        from db_utils import get_user_mentor
        
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
