"""
Модуль управления событиями.

Включает:
- Публичный просмотр предстоящих событий
- Админский CRUD (создание, чтение, обновление, удаление)
- Интеграцию с группой для анонсов
"""
import logging
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from config import ROLE_ADMIN, ANNOUNCEMENT_GROUP_ID, ANNOUNCEMENT_TOPIC_ID
from db_utils import get_events, add_event, update_event, delete_event, HasRole
from utils import check_rate_limit, kb, inline_kb, back_kb, escape_md, safe_edit_text, get_main_keyboard

router = Router(name="events")


# ==================== FSM States ====================

class EventStates(StatesGroup):
    """Состояния для работы с событиями."""
    menu = State()              # Главное меню управления событиями
    selecting_item = State()    # Выбор события для редактирования/удаления
    input_type = State()        # Ввод типа события
    input_datetime = State()    # Ввод даты/времени
    input_link = State()        # Ввод ссылки
    input_announcement = State()  # Ввод анонса
    confirm_announce = State()  # Подтверждение размещения анонса
    editing = State()           # Редактирование события


# ==================== Keyboards ====================

events_menu_kb = kb(
    ["📖 Просмотреть", "➕ Добавить", "✏️ Редактировать", "🗑️ Удалить"],
    "🔙 Назад"
)


# ==================== Helper Functions ====================

def format_event(ev: dict) -> str:
    """Форматирование события для отображения."""
    status = "✅" if ev['datetime'] > datetime.now().isoformat() else "⏰"
    link = f"[🔗]({ev['link']})" if ev['link'] else ""
    return f"{status} *ID:{ev['id']}* {escape_md(ev['type'])} ({ev['datetime'][:10]}) {link}"


# ==================== Admin: Menu ====================

@router.message(F.text == "📋 Управление событиями", HasRole(ROLE_ADMIN))
async def events_menu(message: Message, state: FSMContext):
    """Главное меню управления событиями."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    await state.set_state(EventStates.menu)
    await message.answer("📋 *Управление событиями*", parse_mode="Markdown", reply_markup=events_menu_kb)


# ==================== Admin: View ====================

@router.message(F.text == "📖 Просмотреть", EventStates.menu, HasRole(ROLE_ADMIN))
async def events_show_all(message: Message, state: FSMContext):
    """Показать все события."""
    events = await get_events()
    if not events:
        text = "📭 Нет событий"
    else:
        text = "📅 *Все события:*\n\n" + "\n\n".join(format_event(e) for e in events)
    await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=events_menu_kb)


# ==================== Admin: Create ====================

@router.message(F.text == "➕ Добавить", EventStates.menu, HasRole(ROLE_ADMIN))
async def event_add_start(message: Message, state: FSMContext):
    """Начало добавления события."""
    await state.set_state(EventStates.input_type)
    await message.answer("Введите тип (Вебинар, Митап, Квиз):", reply_markup=back_kb)


@router.message(EventStates.input_type, HasRole(ROLE_ADMIN))
async def event_add_type(message: Message, state: FSMContext):
    """Получение типа события."""
    if not message.text:
        return
    # Проверяем кнопку "Назад" — возврат в меню событий
    if "Назад" in message.text:
        await events_menu(message, state)
        return
    if len(message.text) > 100:
        await message.answer("❌ Тип события слишком длинный (макс 100 символов)")
        return
    await state.update_data(event_type=message.text)
    await state.set_state(EventStates.input_datetime)
    await message.answer("Введите дату `2024-12-31 18:00:00`:", parse_mode="Markdown", reply_markup=back_kb)


@router.message(EventStates.input_datetime, HasRole(ROLE_ADMIN))
async def event_add_datetime(message: Message, state: FSMContext):
    """Получение даты события."""
    if not message.text:
        return
    # Проверяем кнопку "Назад" — возврат к вводу типа
    if "Назад" in message.text:
        await state.set_state(EventStates.input_type)
        await message.answer("Введите тип (Вебинар, Митап, Квиз):", reply_markup=back_kb)
        return
    dt = message.text.strip()
    try:
        if datetime.fromisoformat(dt) <= datetime.now():
            await message.answer("❌ Дата должна быть в будущем!")
            return
    except ValueError:
        await message.answer("❌ Формат: `2024-12-31 18:00:00`", parse_mode="Markdown")
        return
    await state.update_data(event_datetime=dt)
    await state.set_state(EventStates.input_link)
    await message.answer("Введите ссылку (или 'нет'):", reply_markup=back_kb)


@router.message(EventStates.input_link, HasRole(ROLE_ADMIN))
async def event_add_link(message: Message, state: FSMContext):
    """Получение ссылки на событие."""
    if not message.text:
        return
    # Проверяем кнопку "Назад" — возврат к вводу даты
    if "Назад" in message.text:
        await state.set_state(EventStates.input_datetime)
        await message.answer("Введите дату `2024-12-31 18:00:00`:", parse_mode="Markdown", reply_markup=back_kb)
        return
    link = message.text.strip()
    if link.lower() == "нет":
        link = ""
    elif not (link.startswith('http://') or link.startswith('https://')):
        await message.answer("❌ Некорректная ссылка. Используйте формат: https://example.com/page")
        return
    await state.update_data(event_link=link)
    await state.set_state(EventStates.input_announcement)
    await message.answer("Введите анонс:", reply_markup=back_kb)


@router.message(EventStates.input_announcement, HasRole(ROLE_ADMIN))
async def event_add_announcement(message: Message, state: FSMContext):
    """Получение анонса и подготовка к сохранению."""
    if not message.text:
        return
    # Проверяем кнопку "Назад" — возврат к вводу ссылки
    if "Назад" in message.text:
        await state.set_state(EventStates.input_link)
        await message.answer("Введите ссылку (или 'нет'):", reply_markup=back_kb)
        return
    ann = message.text.strip()
    if len(ann) > 2000:
        await message.answer("❌ Анонс слишком длинный (макс 2000 символов)")
        return
    
    data = await state.get_data()
    event_type = data.get('event_type')
    event_datetime = data.get('event_datetime')
    event_link = data.get('event_link')
    
    if not all([event_type, event_datetime]):
        await state.clear()
        await message.answer(
            "⚠️ Сессия истекла. Начните сначала.",
            reply_markup=await get_main_keyboard(message.from_user.id)
        )
        return
    
    await state.update_data(event_announcement=ann)
    
    # Если не настроена группа для анонсов - сразу сохраняем
    if not ANNOUNCEMENT_GROUP_ID:
        try:
            await add_event(event_type, event_datetime, event_link, ann)
            await message.answer("✅ Событие добавлено!")
        except Exception as e:
            logging.error(e)
            await message.answer("❌ Ошибка сохранения")
        await events_menu(message, state)
        return
    
    # Спрашиваем про размещение анонса
    await state.set_state(EventStates.confirm_announce)
    preview = (
        f"📅 *{event_type}*\n"
        f"🕐 {event_datetime}\n"
        f"🔗 {event_link or '—'}\n\n"
        f"{ann[:500]}{'...' if len(ann) > 500 else ''}"
    )
    await message.answer(
        f"{preview}\n\n📢 Разместить анонс в группе?",
        parse_mode="Markdown",
        reply_markup=kb(["✅ Да", "❌ Нет"])
    )


@router.message(EventStates.confirm_announce, HasRole(ROLE_ADMIN))
async def event_confirm_announce(message: Message, state: FSMContext, bot: Bot):
    """Подтверждение размещения анонса в группе."""
    if not message.text:
        return
    # Проверяем кнопку "Назад" — возврат к вводу анонса
    if "Назад" in message.text:
        await state.set_state(EventStates.input_announcement)
        await message.answer("Введите анонс:", reply_markup=back_kb)
        return
    
    data = await state.get_data()
    event_type = data.get('event_type')
    event_datetime = data.get('event_datetime')
    event_link = data.get('event_link')
    event_announcement = data.get('event_announcement')
    
    if not all([event_type, event_datetime, event_announcement]):
        await state.clear()
        await message.answer(
            "⚠️ Сессия истекла. Начните сначала.",
            reply_markup=await get_main_keyboard(message.from_user.id)
        )
        return
    
    # Сохраняем событие в БД
    try:
        await add_event(event_type, event_datetime, event_link, event_announcement)
        await message.answer("✅ Событие добавлено!")
    except Exception as e:
        logging.error(e)
        await message.answer("❌ Ошибка сохранения")
        await events_menu(message, state)
        return
    
    # Если выбрано "Да" - постим в группу
    if message.text == "✅ Да" and ANNOUNCEMENT_GROUP_ID:
        try:
            group_text = (
                f"📅 *{event_type}*\n"
                f"🕐 {event_datetime}\n"
            )
            if event_link:
                group_text += f"🔗 [Ссылка на событие]({event_link})\n"
            group_text += f"\n{event_announcement}"
            
            rsvp_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Иду", callback_data="noop"),
                    InlineKeyboardButton(text="❌ Не иду", callback_data="noop")
                ]
            ])
            
            kwargs = {
                "chat_id": ANNOUNCEMENT_GROUP_ID,
                "text": group_text,
                "parse_mode": "Markdown",
                "reply_markup": rsvp_kb
            }
            if ANNOUNCEMENT_TOPIC_ID:
                kwargs["message_thread_id"] = ANNOUNCEMENT_TOPIC_ID
            
            await bot.send_message(**kwargs)
            await message.answer("📢 Анонс размещён в группе!")
        except Exception as e:
            logging.error(f"Ошибка отправки в группу: {e}")
            await message.answer("⚠️ Событие сохранено, но не удалось разместить анонс в группе.")
    
    await events_menu(message, state)


# ==================== Admin: Update ====================

@router.message(F.text == "✏️ Редактировать", EventStates.menu, HasRole(ROLE_ADMIN))
async def event_edit_select(message: Message, state: FSMContext):
    """Выбор события для редактирования."""
    events = await get_events()
    if not events:
        await message.answer("📭 Нет событий", reply_markup=events_menu_kb)
        return
    await state.set_state(EventStates.selecting_item)
    kb_inline = inline_kb([
        [InlineKeyboardButton(
            text=f"✏️ {e['id']}. {e['type'][:20]} ({e['datetime'][:10]})",
            callback_data=f"edit_ev:{e['id']}"
        )] for e in events
    ])
    await message.answer("Выберите событие:", reply_markup=kb_inline)


@router.callback_query(F.data.startswith("edit_ev:"), HasRole(ROLE_ADMIN))
async def event_edit_callback(callback: CallbackQuery, state: FSMContext):
    """Callback для выбора события на редактирование."""
    await callback.answer()
    try:
        ev_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await safe_edit_text(callback, "❌ Некорректные данные")
        return
    
    events = await get_events()
    ev = next((e for e in events if e['id'] == ev_id), None)
    if not ev:
        await safe_edit_text(callback, "❌ Не найдено")
        return
    
    await state.update_data(edit_id=ev_id, edit_ev=ev)
    await state.set_state(EventStates.editing)
    await safe_edit_text(
        callback,
        f"✏️ Редактирование события *{ev_id}*\n\n"
        f"Отправьте: `тип\\n\\nдата\\n\\nссылка\\n\\nописание`\n\n"
        f"(используйте '.' для пропуска)",
        parse_mode="Markdown"
    )


@router.message(EventStates.editing, HasRole(ROLE_ADMIN))
async def event_edit_process(message: Message, state: FSMContext):
    """Обработка редактирования события."""
    if not message.text:
        return
    # Проверяем кнопку "Назад" — возврат в меню событий
    if "Назад" in message.text:
        await events_menu(message, state)
        return
    
    data = await state.get_data()
    ev_id = data.get('edit_id')
    if not ev_id:
        await state.clear()
        await message.answer(
            "⚠️ Сессия истекла. Начните сначала.",
            reply_markup=await get_main_keyboard(message.from_user.id)
        )
        return
    
    parts = [p.strip() for p in message.text.split('\n\n') if p.strip()]
    updates = {}
    
    if parts and parts[0] != '.':
        updates['event_type'] = parts[0]
    if len(parts) > 1 and parts[1] != '.':
        try:
            datetime.fromisoformat(parts[1])
            updates['event_datetime'] = parts[1]
        except ValueError:
            await message.answer("❌ Неверный формат даты")
            return
    if len(parts) > 2 and parts[2] != '.':
        updates['link'] = "" if parts[2].lower() == "нет" else parts[2]
    if len(parts) > 3 and parts[3] != '.':
        updates['announcement'] = parts[3]
    
    if updates:
        await update_event(ev_id, **updates)
        await message.answer("✅ Обновлено!")
    else:
        await message.answer("❌ Ничего не изменено")
    
    await events_menu(message, state)


# ==================== Admin: Delete ====================

@router.message(F.text == "🗑️ Удалить", EventStates.menu, HasRole(ROLE_ADMIN))
async def event_delete_select(message: Message, state: FSMContext):
    """Выбор события для удаления."""
    events = await get_events()
    if not events:
        await message.answer("📭 Нет событий", reply_markup=events_menu_kb)
        return
    await state.set_state(EventStates.selecting_item)
    kb_inline = inline_kb([
        [InlineKeyboardButton(
            text=f"🗑️ {e['id']}. {e['type'][:20]}",
            callback_data=f"del_ev:{e['id']}"
        )] for e in events
    ])
    await message.answer("Выберите для удаления:", reply_markup=kb_inline)


@router.callback_query(F.data.startswith("del_ev:"), HasRole(ROLE_ADMIN))
async def event_delete_callback(callback: CallbackQuery, state: FSMContext):
    """Callback для удаления события."""
    await callback.answer()
    try:
        ev_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await safe_edit_text(callback, "❌ Некорректные данные")
        return
    if await delete_event(ev_id):
        await safe_edit_text(callback, f"✅ Событие {ev_id} удалено")
    else:
        await safe_edit_text(callback, "❌ Ошибка")
    await state.clear()


# ==================== Public: View ====================

@router.message(F.text.in_(["📅 События комьюнити", "События комьюнити"]))
async def public_events_show(message: Message):
    """Публичный просмотр предстоящих событий."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    events = await get_events(upcoming_only=True)
    if not events:
        await message.answer("📭 Нет предстоящих событий")
        return
    
    text = "📅 *Предстоящие события:*\n\n" + "\n\n".join(
        f"*{e['type']}* ({e['datetime'][:10]})\n{e['announcement'][:100]}..."
        for e in events
    )
    await message.answer(text, parse_mode="Markdown")
