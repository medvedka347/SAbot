"""
Модуль Buddy - система наставничества.

Включает:
- Просмотр списка менти (только для менторов)
- Добавление менти
- Обновление статуса менти
- Удаление менти
- Панель Льва (мета-админ): просмотр всех менторов, отчеты, назначение менти
"""
import logging
import re
import traceback
import aiosqlite
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from config import MODULE_ACCESS, ROLE_MENTOR, ROLE_ADMIN, ROLE_LION
from db_utils import (
    HasRole, get_user_by_id, get_user_by_username, get_all_users,
    add_mentorship, get_mentor_mentees, update_mentorship_status,
    delete_mentorship, get_mentorship_by_id
)
from utils import check_rate_limit, inline_kb, back_kb, get_main_keyboard, parse_date_flexible

router = Router(name="buddy")


# ==================== FSM States ====================

class BuddyStates(StatesGroup):
    """Состояния для работы с Buddy."""
    menu = State()                    # Главное меню
    input_full_name = State()         # Ввод ФИО менти
    input_telegram_tag = State()      # Ввод @username менти
    input_assigned_date = State()     # Ввод даты назначения
    selecting_status = State()        # Выбор статуса


# ==================== Constants ====================

BUDDY_STATUSES = {
    'active': 'Активно',
    'completed': 'Завершено',
    'paused': 'На паузе',
    'dropped': 'Брошено'
}


# ==================== Keyboards ====================

def status_kb(mentorship_id: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора статуса."""
    buttons = []
    for status_key, status_name in BUDDY_STATUSES.items():
        buttons.append([InlineKeyboardButton(
            text=status_name,
            callback_data=f"buddy_status:{mentorship_id}:{status_key}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def mentee_actions_kb(mentorship_id: int) -> InlineKeyboardMarkup:
    """Клавиатура действий с менти."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Изменить статус", callback_data=f"buddy_chstatus:{mentorship_id}")],
        [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"buddy_del:{mentorship_id}")],
        [InlineKeyboardButton(text="◀️ Назад к списку", callback_data="buddy_back_to_list")]
    ])


# ==================== Helper Functions ====================

def format_mentee(mentee: dict, index: int = None) -> str:
    """Форматирование информации о менти."""
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


# ==================== Menu: List Mentees ====================

@router.message(F.text == "📋 Список менти", HasRole(min_priority=MODULE_ACCESS["buddy_mentor"]))
async def buddy_list_mentees(message: Message, state: FSMContext):
    """Показать список менти ментора."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await state.set_state(BuddyStates.menu)
    await state.set_state(BuddyStates.menu)
    
    # Получаем внутренний ID ментора из таблицы user_roles
    # Это тот же пользователь, но нам нужен его ID в БД (user_roles.id)
    user = await get_user_by_id(message.from_user.id)
    if not user:
        await message.answer("❌ Ошибка: не найден профиль ментора")
        return
    
    mentor_db_id = user['id']  # Внутренний ID из user_roles для связи с buddy_mentorships
    mentees = await get_mentor_mentees(mentor_db_id)
    
    if not mentees:
        await message.answer(
            "📭 *У вас пока нет менти*\n\n"
            "Нажмите «➕ Добавить менти» чтобы добавить первого.",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        return
    
    lines = [f"👥 *Ваши менти ({len(mentees)}):*\n"]
    for i, mentee in enumerate(mentees, 1):
        lines.append(format_mentee(mentee, i))
    
    # Добавляем кнопки для каждого менти
    keyboard = []
    for mentee in mentees:
        keyboard.append([InlineKeyboardButton(
            text=f"⚙️ {mentee['full_name'][:30]}",
            callback_data=f"buddy_mentee:{mentee['id']}"
        )])
    
    await message.answer(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else back_kb
    )


# ==================== Add Mentee ====================

@router.message(F.text == "➕ Добавить менти", HasRole(min_priority=MODULE_ACCESS["buddy_mentor"]))
async def buddy_add_start(message: Message, state: FSMContext):
    """Начало добавления менти."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await state.set_state(BuddyStates.input_full_name)
    pass
    
    await message.answer(
        "➕ *Добавление нового менти*\n\n"
        "*Шаг 1/3: ФИО*\n"
        "Введите ФИО менти (или ФИ без отчества):\n\n"
        "💡 Пример: `Иванов Иван Иванович`",
        parse_mode="Markdown",
        reply_markup=back_kb
    )


@router.message(BuddyStates.input_full_name, HasRole(min_priority=MODULE_ACCESS["buddy_mentor"]))
async def buddy_add_full_name(message: Message, state: FSMContext):
    """Получение ФИО менти (для ментора)."""
    await _process_full_name(message, state)


@router.message(BuddyStates.input_full_name, HasRole(min_priority=MODULE_ACCESS["buddy_lion"]))
async def lion_add_full_name(message: Message, state: FSMContext):
    """Получение ФИО менти (для льва)."""
    await _process_full_name(message, state)


async def _process_full_name(message: Message, state: FSMContext):
    """Общая логика получения ФИО."""
    if not message.text:
        await message.answer(
            "❌ *Ошибка:* ФИО не может быть пустым\n\n"
            "Введите ФИО менти (от 2 до 100 символов):",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        return
    
    full_name = message.text.strip()
    if len(full_name) < 2:
        await message.answer(
            "❌ *Ошибка:* ФИО слишком короткое\n\n"
            "Минимум 2 символа. Попробуйте ещё раз:",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        return
    
    if len(full_name) > 100:
        await message.answer(
            "❌ *Ошибка:* ФИО слишком длинное\n\n"
            "Максимум 100 символов. Попробуйте ещё раз:",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        return
    
    await state.update_data(full_name=full_name)
    await state.set_state(BuddyStates.input_telegram_tag)
    
    await message.answer(
        "📱 *Шаг 2/3: Telegram тег*\n\n"
        "Введите тег в Telegram (@username):\n"
        "Примеры: `@ivanov` или `@ivan_ivanov`\n\n"
        "💡 Если тега нет — отправьте «пропустить»",
        parse_mode="Markdown",
        reply_markup=back_kb
    )


@router.message(BuddyStates.input_telegram_tag, HasRole(min_priority=MODULE_ACCESS["buddy_mentor"]))
async def buddy_add_telegram_tag(message: Message, state: FSMContext):
    """Получение @username менти (для ментора)."""
    await _process_telegram_tag(message, state)


@router.message(BuddyStates.input_telegram_tag, HasRole(min_priority=MODULE_ACCESS["buddy_lion"]))
async def lion_add_telegram_tag(message: Message, state: FSMContext):
    """Получение @username менти (для льва)."""
    await _process_telegram_tag(message, state)


async def _process_telegram_tag(message: Message, state: FSMContext):
    """Общая логика получения @username."""
    if not message.text:
        await message.answer(
            "❌ *Ошибка:* пустое сообщение\n\n"
            "Введите тег в Telegram (@username) или «пропустить»:",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        return
    
    tag = message.text.strip()
    
    # Проверка на пропуск
    if tag.lower() in ['пропустить', 'нет', '-', 'skip']:
        tag = None
    else:
        # Валидация формата username
        if not tag.startswith('@'):
            tag = '@' + tag
        
        # Проверка на допустимые символы (a-z, 0-9, _)
        username_part = tag[1:]  # без @
        if not username_part:
            await message.answer(
                "❌ *Ошибка:* пустой тег\n\n"
                "Введите тег в формате `@username` или «пропустить»:",
                parse_mode="Markdown",
                reply_markup=back_kb
            )
            return
        
        # Telegram username: 5-32 символа, a-z, 0-9, underscore
        if len(username_part) < 5:
            await message.answer(
                "❌ *Ошибка:* тег слишком короткий\n\n"
                f"`{tag}` — минимум 5 символов после @\n"
                "Введите корректный тег или «пропустить»:",
                parse_mode="Markdown",
                reply_markup=back_kb
            )
            return
        
        if len(username_part) > 32:
            await message.answer(
                "❌ *Ошибка:* тег слишком длинный\n\n"
                f"Максимум 32 символа. Введите корректный тег или «пропустить»:",
                parse_mode="Markdown",
                reply_markup=back_kb
            )
            return
    
    await state.update_data(telegram_tag=tag)
    await state.set_state(BuddyStates.input_assigned_date)
    
    today = datetime.now().strftime("%d.%m.%y")
    
    await message.answer(
        f"Введите дату назначения (ДД.ММ.ГГ):\n"
        f"(по умолчанию: {today})",
        reply_markup=back_kb
    )


@router.message(BuddyStates.input_assigned_date, HasRole(min_priority=MODULE_ACCESS["buddy_mentor"]))
async def buddy_add_date(message: Message, state: FSMContext):
    """Получение даты и сохранение менти (для ментора или льва, назначающего бадди)."""
    if not message.text:
        await message.answer(
            "❌ *Ошибка:* введите дату\n\n"
            "Примеры: `07.03.26` или `сегодня`",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        return
    
    logging.info(f"buddy_add_date: получена дата '{message.text}' от user_id={message.from_user.id}")
    date_str = parse_date_flexible(message.text)
    
    if date_str is None:
        await message.answer(
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
        return
    
    data = await state.get_data()
    
    # Проверяем наличие обязательных данных
    full_name = data.get('full_name')
    if not full_name:
        logging.error(f"buddy_add_date: full_name не найден в данных состояния: {data}")
        await message.answer(
            "❌ *Ошибка:* данные потеряны\n\n"
            "Сессия устарела или данные не сохранились.\n"
            "Пожалуйста, начните добавление менти заново.",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        await state.clear()
        return
    
    # Проверяем, есть ли selected_mentor_id - это значит, что лев назначает бадди другому ментору
    mentor_id = data.get('selected_mentor_id')
    is_lion_assigning = mentor_id is not None
    
    if not mentor_id:
        # Обычный ментор добавляет менти себе
        user = await get_user_by_id(message.from_user.id)
        if not user:
            await message.answer(
                "❌ *Ошибка:* ваш профиль не найден в системе\n\n"
                "Обратитесь к администратору. Возможно, вам нужно перезайти в бот (/start)",
                parse_mode="Markdown",
                reply_markup=back_kb
            )
            await state.clear()
            return
        mentor_id = user['id']
    
    try:
        logging.info(f"buddy_add_date: сохранение менти. mentor_id={mentor_id}, full_name={full_name}, date={date_str}")
        mentorship_id = await add_mentorship(
            mentor_id=mentor_id,
            mentee_full_name=full_name,
            mentee_telegram_tag=data.get('telegram_tag'),
            assigned_date=date_str,
            status='active'
        )
        
        tag_info = f"\n📱 Тег: {data.get('telegram_tag')}" if data.get('telegram_tag') else ""
        
        if is_lion_assigning:
            await message.answer(
                f"✅ *Менти назначен ментору!*\n\n"
                f"👤 {full_name}{tag_info}\n"
                f"📅 Дата назначения: {date_str}\n"
                f"📊 Статус: Активно",
                parse_mode="Markdown",
                reply_markup=back_kb
            )
        else:
            await message.answer(
                f"✅ *Менти добавлен!*\n\n"
                f"👤 {full_name}{tag_info}\n"
                f"📅 Дата назначения: {date_str}\n"
                f"📊 Статус: Активно",
                parse_mode="Markdown",
                reply_markup=back_kb
            )
        await state.clear()
        
    except ValueError as e:
        # Ошибка данных (например, ментор не найден)
        error_msg = str(e)
        logging.error(f"Ошибка данных при добавлении менти: {error_msg}")
        await message.answer(
            f"❌ *Ошибка данных:*\n\n"
            f"{error_msg}\n\n"
            f"Обратитесь к администратору.",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        await state.clear()
        
    except aiosqlite.IntegrityError as e:
        # Ошибка целостности БД (constraint violation)
        logging.error(f"Ошибка целостности БД: {e}")
        await message.answer(
            "❌ *Ошибка базы данных*\n\n"
            "Нарушение целостности данных.\n"
            "Возможно, этот менти уже существует или ID ментора некорректен.\n\n"
            "Обратитесь к администратору.",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        await state.clear()
        
    except Exception as e:
        # Неизвестная ошибка - выводим детали для диагностики
        error_details = str(e)[:200]  # Обрезаем длинные сообщения
        logging.error(f"Неизвестная ошибка при добавлении менти: {e}")
        logging.error(traceback.format_exc())

        # Убираем все потенциально опасные символы для Telegram
        error_clean = re.sub(r'[_*`\[\]()]', '', error_details)
        await message.answer(
            f"❌ Системная ошибка\n\n"
            f"Не удалось сохранить данные.\n\n"
            f"Детали:\n{error_clean}\n\n"
            f"Пожалуйста, сообщите администратору.",
            reply_markup=back_kb
        )
        await state.clear()


@router.message(BuddyStates.input_assigned_date, HasRole(min_priority=MODULE_ACCESS["buddy_lion"]))
async def lion_add_date(message: Message, state: FSMContext):
    """Получение даты и сохранение менти (для Льва)."""
    if not message.text:
        return
    
    date_str = parse_date_flexible(message.text)
    
    if date_str is None:
        await message.answer(
            "❌ Неверный формат даты.\n\n"
            "Используйте:\n"
            "• 15.03.26 или 15,03,26\n"
            "• 15.03.2026 или 15,03,2026\n"
            "• сегодня"
        )
        return
    
    data = await state.get_data()
    logging.info(f"LION_ADD_DATE: data={data}")
    
    # Проверяем все необходимые поля
    mentor_id = data.get('selected_mentor_id')
    full_name = data.get('full_name')
    
    if not mentor_id:
        logging.error("LION_ADD_DATE: selected_mentor_id not found in state")
        await message.answer("❌ Ошибка: ментор не выбран. Начните сначала.")
        await state.clear()
        return
    
    if not full_name:
        logging.error("LION_ADD_DATE: full_name not found in state")
        await message.answer("❌ Ошибка: ФИО не заполнено. Начните сначала.")
        await state.clear()
        return
    
    try:
        mentorship_id = await add_mentorship(
            mentor_id=mentor_id,
            mentee_full_name=full_name,
            mentee_telegram_tag=data.get('telegram_tag'),
            assigned_date=date_str,
            status='active'
        )
        
        logging.info(f"LION_ADD_DATE: Successfully created mentorship {mentorship_id}")
        
        await message.answer(
            f"✅ *Менти назначен ментору!*\n\n"
            f"👤 {full_name}\n"
            f"📅 {date_str}\n"
            f"📊 Статус: Активно",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        await state.clear()
        
    except Exception as e:
        logging.error(f"LION_ADD_DATE: Exception - {e}", exc_info=True)
        await message.answer("❌ Ошибка при сохранении. Попробуйте позже.")
        await state.clear()


# ==================== Callback Handlers ====================

@router.callback_query(F.data.startswith("buddy_mentee:"), HasRole(min_priority=MODULE_ACCESS["buddy_mentor"]))
async def buddy_show_mentee(callback: CallbackQuery, state: FSMContext):
    """Показать детали менти."""
    await callback.answer()
    
    try:
        mentorship_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.edit_text("❌ Некорректные данные")
        return
    
    mentee = await get_mentorship_by_id(mentorship_id)
    if not mentee:
        await callback.message.edit_text("❌ Менти не найден")
        return
    
    # Проверяем что менти принадлежит текущему ментору ИЛИ пользователь — Лев
    from db_utils import get_user_roles, get_user_max_priority
    from config import MODULE_ACCESS
    current_user = await get_user_by_id(callback.from_user.id)
    user_max_priority = await get_user_max_priority(user_id=callback.from_user.id, username=callback.from_user.username)
    
    is_owner = current_user and mentee['mentor_id'] == current_user['id']
    is_lion = user_max_priority >= MODULE_ACCESS["buddy_lion"]
    
    if not is_owner and not is_lion:
        await callback.message.edit_text("❌ У вас нет доступа к этому менти")
        return
    
    text = format_mentee(mentee)
    text = f"📋 *Детали менти:*\n\n{text}"
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=mentee_actions_kb(mentorship_id)
    )


@router.callback_query(F.data.startswith("buddy_chstatus:"), HasRole(min_priority=MODULE_ACCESS["buddy_mentor"]))
async def buddy_change_status_start(callback: CallbackQuery, state: FSMContext):
    """Начать изменение статуса."""
    await callback.answer()
    
    try:
        mentorship_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.edit_text("❌ Некорректные данные")
        return
    
    await callback.message.edit_text(
        "🔄 *Выберите новый статус:*",
        parse_mode="Markdown",
        reply_markup=status_kb(mentorship_id)
    )


@router.callback_query(F.data.startswith("buddy_status:"), HasRole(min_priority=MODULE_ACCESS["buddy_mentor"]))
async def buddy_set_status(callback: CallbackQuery, state: FSMContext):
    """Установить статус менти."""
    await callback.answer()
    
    try:
        parts = callback.data.split(":")
        mentorship_id = int(parts[1])
        new_status = parts[2]
    except (ValueError, IndexError):
        await callback.message.edit_text("❌ Некорректные данные")
        return
    
    if await update_mentorship_status(mentorship_id, new_status):
        status_name = BUDDY_STATUSES.get(new_status, new_status)
        await callback.message.edit_text(
            f"✅ Статус обновлён: *{status_name}*",
            parse_mode="Markdown"
        )
    else:
        await callback.message.edit_text("❌ Ошибка обновления статуса")


@router.callback_query(F.data.startswith("buddy_del:"), HasRole(min_priority=MODULE_ACCESS["buddy_mentor"]))
async def buddy_delete_mentee(callback: CallbackQuery, state: FSMContext):
    """Удалить менти."""
    await callback.answer()
    
    try:
        mentorship_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.edit_text("❌ Некорректные данные")
        return
    
    mentee = await get_mentorship_by_id(mentorship_id)
    if not mentee:
        await callback.message.edit_text("❌ Менти не найден")
        return
    
    # Подтверждение удаления
    await callback.message.edit_text(
        f"🗑️ *Удалить менти?*\n\n"
        f"👤 {mentee['full_name']}\n\n"
        f"Это действие нельзя отменить.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"buddy_conf_del:{mentorship_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"buddy_mentee:{mentorship_id}")]
        ])
    )


@router.callback_query(F.data.startswith("buddy_conf_del:"), HasRole(min_priority=MODULE_ACCESS["buddy_mentor"]))
async def buddy_confirm_delete(callback: CallbackQuery, state: FSMContext):
    """Подтверждение удаления менти."""
    await callback.answer()
    
    try:
        mentorship_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.edit_text("❌ Некорректные данные")
        return
    
    if await delete_mentorship(mentorship_id):
        await callback.message.edit_text("✅ Менти удалён")
    else:
        await callback.message.edit_text("❌ Ошибка удаления")


@router.callback_query(F.data == "buddy_back_to_list", HasRole(min_priority=MODULE_ACCESS["buddy_mentor"]))
async def buddy_back_to_list(callback: CallbackQuery, state: FSMContext):
    """Вернуться к списку менти."""
    await callback.answer()
    
    # Вызываем тот же обработчик что и для списка
    from aiogram.types import Message
    message = callback.message
    message.from_user = callback.from_user
    
    await buddy_list_mentees(message, state)


# ==================== LION PANEL (META ADMIN) ====================

@router.message(F.text == "🦁 Панель Льва", HasRole(min_priority=MODULE_ACCESS["buddy_lion"]))
async def lion_panel(message: Message, state: FSMContext):
    """Панель Льва - управление Buddy системой."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await state.clear()
    
    from utils import kb
    lion_kb = kb([
        "📊 Список менторов",
        "📋 Все менти",
        "➕ Назначить бадди",
        "🔙 Назад"
    ])
    
    await message.answer(
        "🦁 *Панель Льва*\n\n"
        "Управление системой Buddy:\n"
        "• Просмотр всех менторов\n"
        "• Отчеты по менти\n"
        "• Назначение бадди менторам",
        parse_mode="Markdown",
        reply_markup=lion_kb if message.chat.type == "private" else None
    )


@router.message(F.text == "📊 Список менторов", HasRole(min_priority=MODULE_ACCESS["buddy_lion"]))
async def lion_list_mentors(message: Message, state: FSMContext):
    """Показать список всех менторов (для Льва)."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    from db_utils import get_all_mentors, get_mentor_stats
    
    mentors = await get_all_mentors()
    
    if not mentors:
        await message.answer("📭 Нет менторов в системе", reply_markup=back_kb)
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
            callback_data=f"lion_mentor:{mentor['id']}"
        )])
    
    await message.answer(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@router.callback_query(F.data.startswith("lion_mentor:"), HasRole(min_priority=MODULE_ACCESS["buddy_lion"]))
async def lion_show_mentor_details(callback: CallbackQuery, state: FSMContext):
    """Показать детали ментора (отчет)."""
    await callback.answer()
    
    try:
        mentor_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.edit_text("❌ Некорректные данные")
        return
    
    from db_utils import get_mentor_stats, get_user_by_db_id
    
    # Получаем данные ментора по внутреннему ID (не текущего пользователя!)
    mentor = await get_user_by_db_id(mentor_id)
    mentees = await get_mentor_mentees(mentor_id)
    stats = await get_mentor_stats(mentor_id)
    
    # Формируем контактную информацию ментора
    if mentor:
        mentor_contact = f"@{mentor['username']}" if mentor['username'] else f"ID:{mentor['user_id']}"
    else:
        mentor_contact = "Неизвестно"
    
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
    
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к списку", callback_data="lion_back_to_mentors")]
        ])
    )


@router.callback_query(F.data == "lion_back_to_mentors", HasRole(min_priority=MODULE_ACCESS["buddy_lion"]))
async def lion_back_to_mentors(callback: CallbackQuery, state: FSMContext):
    """Вернуться к списку менторов."""
    await callback.answer()
    
    from aiogram.types import Message
    message = callback.message
    message.from_user = callback.from_user
    
    await lion_list_mentors(message, state)


@router.message(F.text == "📋 Все менти", HasRole(min_priority=MODULE_ACCESS["buddy_lion"]))
async def lion_all_mentees(message: Message, state: FSMContext):
    """Показать всех менти во всех системе."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    from db_utils import get_all_mentorships_for_lion
    
    mentorships = await get_all_mentorships_for_lion()
    
    if not mentorships:
        await message.answer("📭 Нет менти в системе", reply_markup=back_kb)
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
    
    await message.answer(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=back_kb
    )


@router.message(F.text == "➕ Назначить бадди", HasRole(min_priority=MODULE_ACCESS["buddy_lion"]))
async def lion_assign_start(message: Message, state: FSMContext):
    """Начать назначение бадди ментору."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    from db_utils import get_all_mentors
    
    mentors = await get_all_mentors()
    
    if not mentors:
        await message.answer("❌ Нет доступных менторов", reply_markup=back_kb)
        return
    
    await state.set_state(BuddyStates.menu)
    # Для Lion assign используем специальную логику в back_handler
    await state.update_data(lion_action="assign_mentee")
    
    keyboard = []
    for mentor in mentors:
        contact = f"@{mentor['username']}" if mentor['username'] else f"ID:{mentor['user_id']}"
        keyboard.append([InlineKeyboardButton(
            text=f"👤 {contact[:30]}",
            callback_data=f"lion_assign:{mentor['id']}"
        )])
    
    await message.answer(
        "🦁 *Назначение бадди*\n\n"
        "Выберите ментора:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@router.callback_query(F.data.startswith("lion_assign:"), HasRole(min_priority=MODULE_ACCESS["buddy_lion"]))
async def lion_select_mentor_for_assign(callback: CallbackQuery, state: FSMContext):
    """Выбрали ментора для назначения бадди."""
    await callback.answer()
    
    try:
        mentor_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.edit_text("❌ Некорректные данные")
        return
    
    logging.info(f"LION_SELECT_MENTOR: mentor_id={mentor_id}")
    # Для Lion assign используем специальную логику в back_handler
    await state.update_data(selected_mentor_id=mentor_id)
    await state.set_state(BuddyStates.input_full_name)
    
    # Проверяем, что сохранилось
    data = await state.get_data()
    logging.info(f"LION_SELECT_MENTOR: saved data={data}")
    
    await callback.message.edit_text(
        "✏️ *Назначение бадди*\n\n"
        "Введите ФИО менти:",
        parse_mode="Markdown"
    )
    await callback.message.answer("Введите ФИО менти:", reply_markup=back_kb)
