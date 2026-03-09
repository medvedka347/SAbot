"""
Модуль управления ролями пользователей.

Включает:
- Просмотр списка пользователей с пагинацией
- Назначение ролей (batch)
- Удаление пользователей
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from config import MODULE_ACCESS, ROLES
from db_utils import get_all_users, set_users_batch, delete_user, HasRole, get_user_by_id, get_user_by_username
from utils import (
    check_rate_limit, kb, inline_kb, back_kb,
    get_role_emoji, format_user, parse_users_input, escape_md
)

router = Router(name="roles")


# ==================== FSM States ====================

class RoleStates(StatesGroup):
    """Состояния для управления ролями."""
    menu = State()              # Главное меню
    input_users = State()       # Ввод списка пользователей
    selecting_role = State()    # Выбор роли для назначения
    selecting_user_to_delete = State()  # Выбор пользователя для удаления


# ==================== Constants ====================

USERS_PER_PAGE = 25  # Пагинация для списка пользователей


# ==================== Keyboards ====================

roles_menu_kb = kb(
    ["📋 Список пользователей", "➕ Назначить роль", "🗑️ Удалить пользователя"],
    "🔙 Назад"
)


def role_kb(prefix: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора роли."""
    return inline_kb([
        [InlineKeyboardButton(text="👤 User", callback_data=f"{prefix}:user")],
        [InlineKeyboardButton(text="🎓 Mentor", callback_data=f"{prefix}:mentor")],
        [InlineKeyboardButton(text="👑 Admin", callback_data=f"{prefix}:admin")],
        [InlineKeyboardButton(text="🦁 Lion (Meta-Admin)", callback_data=f"{prefix}:lion")],
    ])


def build_users_pagination_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Создать клавиатуру пагинации для списка пользователей."""
    buttons = []
    
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"users_page:{page-1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"users_page:{page+1}"))
    
    buttons.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ==================== Menu ====================

@router.message(F.text == "👥 Управление ролями", HasRole(min_priority=MODULE_ACCESS["roles"]))
async def roles_menu(message: Message, state: FSMContext):
    """Главное меню управления ролями."""
    # Блокируем вызов через reply на чужое сообщение
    if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
        await message.answer("❌ Нет прав.")
        return
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await state.set_state(RoleStates.menu)
    await state.update_data(_prev_state="menu")
    text = (
        "👥 *Управление ролями пользователей*\n\n"
        "📋 *Список* — просмотр всех пользователей\n"
        "➕ *Назначить роль* — добавить/изменить роль\n"
        "   Поддерживается:\n"
        "   • Только ID: `123456789`\n"
        "   • Только @username: `@ivan`\n"
        "   • Оба значения: `123456789 @ivan`\n"
        "🗑️ *Удалить* — удалить пользователя"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=roles_menu_kb)


# ==================== View Users ====================

async def _show_users_page(
    message_or_callback, 
    users: list, 
    page: int, 
    total_pages: int, 
    total_users: int, 
    is_callback: bool = False
):
    """Отобразить страницу со списком пользователей."""
    start_idx = page * USERS_PER_PAGE
    end_idx = min(start_idx + USERS_PER_PAGE, len(users))
    page_users = users[start_idx:end_idx]
    
    # Группируем по ролям для отображения
    by_role = {r: [] for r in ROLES}
    for u in page_users:
        by_role[u['role']].append(u)
    
    lines = [f"👥 *Всего пользователей: {total_users}* (стр. {page+1}/{total_pages})\n"]
    
    for role in ROLES:
        emoji = get_role_emoji(role)
        role_users = by_role[role]
        lines.append(f"\n{emoji} *{role.capitalize()} ({len(role_users)} на этой стр.):*")
        if role_users:
            for u in role_users:
                lines.append(f"  {format_user(u)}")
        else:
            lines.append("  _нет_")
    
    keyboard = build_users_pagination_keyboard(page, total_pages)
    text = "\n".join(lines)
    
    if is_callback:
        try:
            await message_or_callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            pass
        await message_or_callback.answer()
    else:
        await message_or_callback.answer(text, parse_mode="Markdown", reply_markup=keyboard)


@router.message(F.text == "📋 Список пользователей", RoleStates.menu, HasRole(min_priority=MODULE_ACCESS["roles"]))
async def roles_show(message: Message, state: FSMContext):
    """Показать список всех пользователей (с пагинацией)."""
    # Блокируем вызов через reply на чужое сообщение
    if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
        await message.answer("❌ Нет прав.")
        return
    
    # Показываем typing пока загружаем пользователей
    await message.chat.do("typing")
    
    users = await get_all_users()
    if not users:
        await message.answer("📭 Пользователей нет")
        await roles_menu(message, state)
        return
    
    # Группируем по ролям
    by_role = {r: [] for r in ROLES}
    for u in users:
        by_role[u['role']].append(u)
    
    # Плоский список для пагинации
    all_users_flat = []
    for role in ROLES:
        for u in by_role[role]:
            all_users_flat.append(u)
    
    total_users = len(all_users_flat)
    total_pages = (total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE
    
    await _show_users_page(message, all_users_flat, 0, total_pages, total_users, is_callback=False)
    await roles_menu(message, state)


@router.callback_query(F.data.startswith("users_page:"), HasRole(min_priority=MODULE_ACCESS["roles"]))
async def users_page_callback(callback: CallbackQuery):
    """Callback для переключения страниц списка пользователей."""
    page = int(callback.data.split(":")[1])
    
    users = await get_all_users()
    
    # Группируем по ролям
    by_role = {r: [] for r in ROLES}
    for u in users:
        by_role[u['role']].append(u)
    
    all_users_flat = []
    for role in ROLES:
        for u in by_role[role]:
            all_users_flat.append(u)
    
    total_users = len(all_users_flat)
    total_pages = (total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE
    
    await _show_users_page(callback, all_users_flat, page, total_pages, total_users, is_callback=True)


# ==================== Assign Role ====================

@router.message(F.text == "➕ Назначить роль", RoleStates.menu, HasRole(min_priority=MODULE_ACCESS["roles"]))
async def role_add_start(message: Message, state: FSMContext):
    """Начало добавления/изменения роли."""
    # Блокируем вызов через reply на чужое сообщение
    if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
        await message.answer("❌ Нет прав.")
        return
    # Инициализируем историю состояний (пустая, т.к. это первый шаг)
    await state.update_data(_prev_state="input_users", _state_history=[])
    await state.set_state(RoleStates.input_users)
    text = (
        "Введите пользователей для назначения роли:\n\n"
        "*Форматы:*\n"
        "• `123456789` — только ID\n"
        "• `@ivan_petrov` — только username\n"
        "• `123456789 @ivan_petrov` — оба значения\n"
        "• Несколько: `@ivan, @petr, 123456789`\n\n"
        "Бот свяжет ID и username если они указаны вместе."
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=back_kb)


@router.message(RoleStates.input_users, HasRole(min_priority=MODULE_ACCESS["roles"]))
async def role_receive_users(message: Message, state: FSMContext):
    """Обработка ввода пользователей."""
    if not message.text:
        return
    
    users, errors = parse_users_input(message.text)
    
    if not users:
        await message.answer("❌ Не найдено корректных данных. Попробуйте снова:", reply_markup=back_kb)
        return
    
    if errors:
        await message.answer(f"⚠️ Пропущены некорректные данные: {', '.join(errors[:5])}")
    
    # Показываем что распарсили
    preview = []
    for i, u in enumerate(users[:5], 1):
        parts = []
        if u.get("user_id"):
            parts.append(f"ID:{u['user_id']}")
        if u.get("username"):
            parts.append(f"@{u['username']}")
        preview.append(f"{i}. {' + '.join(parts)}")
    
    if len(users) > 5:
        preview.append(f"... и ещё {len(users) - 5}")
    
    # Сохраняем историю для навигации назад
    history = ["input_users"]
    
    await state.update_data(users_to_assign=users, _prev_state="selecting_role", _state_history=history)
    await state.set_state(RoleStates.selecting_role)
    
    await message.answer(
        f"Найдено *{len(users)}* пользователей:\n" + "\n".join(preview) + "\n\nВыберите роль:",
        parse_mode="Markdown",
        reply_markup=role_kb("set_role")
    )


@router.callback_query(F.data.startswith("set_role:"), HasRole(min_priority=MODULE_ACCESS["roles"]))
async def role_set_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение назначения роли."""
    await callback.answer()
    role = callback.data.split(":")[1]
    data = await state.get_data()
    users = data.get("users_to_assign", [])
    
    if not users:
        await callback.message.edit_text("❌ Ошибка: список пуст")
        await state.clear()
        return
    
    # Сохраняем выбранную роль
    await state.update_data(selected_role=role)
    
    # Формируем превью пользователей
    preview = []
    for i, u in enumerate(users[:5], 1):
        parts = []
        if u.get("user_id"):
            parts.append(f"ID:{u['user_id']}")
        if u.get("username"):
            parts.append(f"@{u['username']}")
        preview.append(f"{i}. {' + '.join(parts)}")
    
    if len(users) > 5:
        preview.append(f"... и ещё {len(users) - 5}")
    
    # Показываем подтверждение
    role_emoji = {"user": "👤", "mentor": "🎓", "admin": "👑", "lion": "🦁"}
    await callback.message.edit_text(
        f"🎯 *Назначить роль?*\n\n"
        f"Роль: {role_emoji.get(role, '👤')} `{role}`\n"
        f"Пользователей: *{len(users)}*\n\n"
        f"Список:\n" + "\n".join(preview) + "\n\n"
        f"Подтвердите назначение:",
        parse_mode="Markdown",
        reply_markup=inline_kb([
            [InlineKeyboardButton(text="✅ Да, назначить", callback_data="conf_set_role")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_set_role")]
        ])
    )


@router.callback_query(F.data == "conf_set_role", HasRole(min_priority=MODULE_ACCESS["roles"]))
async def role_set_execute(callback: CallbackQuery, state: FSMContext):
    """Выполнение назначения роли."""
    await callback.answer()
    
    data = await state.get_data()
    users = data.get("users_to_assign", [])
    role = data.get("selected_role")
    
    if not users or not role:
        await callback.message.edit_text("❌ Ошибка: данные не найдены")
        await state.clear()
        return
    
    await set_users_batch(users, role)
    
    await callback.message.edit_text(
        f"✅ Роль `{role}` назначена для *{len(users)}* пользователей!"
    )
    await state.clear()


@router.callback_query(F.data == "cancel_set_role", HasRole(min_priority=MODULE_ACCESS["roles"]))
async def role_set_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена назначения роли."""
    await callback.answer("❌ Назначение отменено")
    await callback.message.edit_text("❌ Назначение роли отменено")
    await state.clear()


# ==================== Delete User ====================

@router.message(F.text == "🗑️ Удалить пользователя", RoleStates.menu, HasRole(min_priority=MODULE_ACCESS["roles"]))
async def role_delete_start(message: Message, state: FSMContext):
    """Начало удаления пользователя."""
    # Блокируем вызов через reply на чужое сообщение
    if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
        await message.answer("❌ Нет прав.")
        return
    users = await get_all_users()
    if not users:
        await message.answer("📭 Нет пользователей", reply_markup=roles_menu_kb)
        return
    
    await state.set_state(RoleStates.selecting_user_to_delete)
    
    # Группируем по ролям
    keyboard = []
    by_role = {r: [] for r in ROLES}
    for u in users:
        by_role[u['role']].append(u)
    
    for role in ROLES:
        if by_role[role]:
            keyboard.append([InlineKeyboardButton(text=f"—— {role.upper()} ——", callback_data="noop")])
            for u in by_role[role][:10]:
                user_text = format_user(u)
                if u.get('user_id'):
                    callback_data = f"del_user:id:{u['user_id']}"
                elif u.get('username'):
                    callback_data = f"del_user:un:{u['username']}"
                else:
                    continue
                keyboard.append([InlineKeyboardButton(text=user_text, callback_data=callback_data)])
    
    await message.answer("🗑️ Выберите пользователя для удаления:", reply_markup=inline_kb(keyboard))


@router.callback_query(F.data.startswith("del_user:"), HasRole(min_priority=MODULE_ACCESS["roles"]))
async def role_delete_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение удаления пользователя."""
    await callback.answer()
    
    if callback.data == "noop":
        return
    
    # Формат: del_user:id:123456789 или del_user:un:username
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.message.edit_text("❌ Некорректные данные")
        return
    
    key_type = parts[1]
    key_value = parts[2]
    
    # Получаем информацию о пользователе для отображения
    if key_type == "id":
        user = await get_user_by_id(int(key_value))
    else:
        user = await get_user_by_username(key_value)
    
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден")
        await state.clear()
        return
    
    # Сохраняем данные для удаления
    await state.update_data(del_user_type=key_type, del_user_value=key_value)
    
    # Показываем подтверждение
    user_text = format_user(user)
    await callback.message.edit_text(
        f"🗑️ *Удалить пользователя?*\n\n"
        f"{user_text}\n\n"
        f"⚠️ Это действие нельзя отменить.",
        parse_mode="Markdown",
        reply_markup=inline_kb([
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data="conf_del_user")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_del_user")]
        ])
    )


@router.callback_query(F.data == "conf_del_user", HasRole(min_priority=MODULE_ACCESS["roles"]))
async def role_delete_execute(callback: CallbackQuery, state: FSMContext):
    """Выполнение удаления пользователя."""
    await callback.answer()
    
    data = await state.get_data()
    key_type = data.get("del_user_type")
    key_value = data.get("del_user_value")
    
    if not key_type or not key_value:
        await callback.message.edit_text("❌ Ошибка: данные не найдены")
        await state.clear()
        return
    
    if key_type == "id":
        success = await delete_user(user_id=int(key_value))
    else:
        success = await delete_user(username=key_value)
    
    if success:
        await callback.message.edit_text("✅ Пользователь удалён")
    else:
        await callback.message.edit_text("❌ Не удалось удалить")
    
    await state.clear()


@router.callback_query(F.data == "cancel_del_user", HasRole(min_priority=MODULE_ACCESS["roles"]))
async def role_delete_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена удаления пользователя."""
    await callback.answer("❌ Удаление отменено")
    await callback.message.edit_text("❌ Удаление отменено")
    await state.clear()
