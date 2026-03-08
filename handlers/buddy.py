"""
Модуль Buddy - система наставничества.

Включает:
- Просмотр списка менти (только для менторов)
- Добавление менти
- Обновление статуса менти
- Удаление менти
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from config import ROLE_MENTOR, ROLE_ADMIN
from db_utils import (
    HasRole, get_user_by_id, get_user_by_username,
    add_mentorship, get_mentor_mentees, update_mentorship_status,
    delete_mentorship, get_mentorship_by_id
)
from utils import check_rate_limit, inline_kb, back_kb, get_main_keyboard

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

@router.message(F.text == "📋 Список менти", HasRole(ROLE_MENTOR))
async def buddy_list_mentees(message: Message, state: FSMContext):
    """Показать список менти ментора."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await state.set_state(BuddyStates.menu)
    
    # Получаем ID ментора из БД
    user = await get_user_by_id(message.from_user.id)
    if not user:
        await message.answer("❌ Ошибка: не найден профиль ментора")
        return
    
    mentor_db_id = user['id']
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

@router.message(F.text == "➕ Добавить менти", HasRole(ROLE_MENTOR))
async def buddy_add_start(message: Message, state: FSMContext):
    """Начало добавления менти."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await state.set_state(BuddyStates.input_full_name)
    await state.update_data(_prev_state="menu")
    
    await message.answer(
        "➕ *Добавление нового менти*\n\n"
        "Введите ФИО менти (или ФИ без отчества):",
        parse_mode="Markdown",
        reply_markup=back_kb
    )


@router.message(BuddyStates.input_full_name, HasRole(ROLE_MENTOR))
async def buddy_add_full_name(message: Message, state: FSMContext):
    """Получение ФИО менти."""
    if not message.text:
        return
    
    full_name = message.text.strip()
    if len(full_name) < 2 or len(full_name) > 100:
        await message.answer("❌ ФИО должно быть от 2 до 100 символов")
        return
    
    await state.update_data(full_name=full_name, _prev_state="input_full_name")
    await state.set_state(BuddyStates.input_telegram_tag)
    
    await message.answer(
        "Введите тег в Telegram (@username):\n"
        "(или отправьте «пропустить» если нет)",
        reply_markup=back_kb
    )


@router.message(BuddyStates.input_telegram_tag, HasRole(ROLE_MENTOR))
async def buddy_add_telegram_tag(message: Message, state: FSMContext):
    """Получение @username менти."""
    if not message.text:
        return
    
    tag = message.text.strip()
    if tag.lower() in ['пропустить', 'нет', '-']:
        tag = None
    elif not tag.startswith('@'):
        tag = '@' + tag
    
    await state.update_data(telegram_tag=tag, _prev_state="input_telegram_tag")
    await state.set_state(BuddyStates.input_assigned_date)
    
    from datetime import datetime
    today = datetime.now().strftime("%d.%m.%y")
    
    await message.answer(
        f"Введите дату назначения (ДД.ММ.ГГ):\n"
        f"(по умолчанию: {today})",
        reply_markup=back_kb
    )


@router.message(BuddyStates.input_assigned_date, HasRole(ROLE_MENTOR))
async def buddy_add_date(message: Message, state: FSMContext):
    """Получение даты и сохранение менти."""
    if not message.text:
        return
    
    date_str = message.text.strip()
    if date_str.lower() in ['сегодня', 'now', '-']:
        from datetime import datetime
        date_str = datetime.now().strftime("%d.%m.%y")
    
    # Простая валидация формата ДД.ММ.ГГ
    import re
    if not re.match(r'^\d{2}\.\d{2}\.\d{2}$', date_str):
        await message.answer("❌ Неверный формат. Используйте ДД.ММ.ГГ (например: 15.03.25)")
        return
    
    data = await state.get_data()
    
    # Получаем ID ментора
    user = await get_user_by_id(message.from_user.id)
    if not user:
        await message.answer("❌ Ошибка: не найден профиль ментора")
        await state.clear()
        return
    
    try:
        mentorship_id = await add_mentorship(
            mentor_id=user['id'],
            mentee_full_name=data['full_name'],
            mentee_telegram_tag=data.get('telegram_tag'),
            assigned_date=date_str,
            status='active'
        )
        
        await message.answer(
            f"✅ *Менти добавлен!*\n\n"
            f"👤 {data['full_name']}\n"
            f"📅 {date_str}\n"
            f"📊 Статус: Активно",
            parse_mode="Markdown",
            reply_markup=back_kb
        )
        await state.clear()
        
    except Exception as e:
        logging.error(f"Ошибка добавления менти: {e}")
        await message.answer("❌ Ошибка при сохранении. Попробуйте позже.")
        await state.clear()


# ==================== Callback Handlers ====================

@router.callback_query(F.data.startswith("buddy_mentee:"), HasRole(ROLE_MENTOR))
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
    
    text = format_mentee(mentee)
    text = f"📋 *Детали менти:*\n\n{text}"
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=mentee_actions_kb(mentorship_id)
    )


@router.callback_query(F.data.startswith("buddy_chstatus:"), HasRole(ROLE_MENTOR))
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


@router.callback_query(F.data.startswith("buddy_status:"), HasRole(ROLE_MENTOR))
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


@router.callback_query(F.data.startswith("buddy_del:"), HasRole(ROLE_MENTOR))
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


@router.callback_query(F.data.startswith("buddy_conf_del:"), HasRole(ROLE_MENTOR))
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


@router.callback_query(F.data == "buddy_back_to_list", HasRole(ROLE_MENTOR))
async def buddy_back_to_list(callback: CallbackQuery, state: FSMContext):
    """Вернуться к списку менти."""
    await callback.answer()
    
    # Вызываем тот же обработчик что и для списка
    from aiogram.types import Message
    message = callback.message
    message.from_user = callback.from_user
    
    await buddy_list_mentees(message, state)
