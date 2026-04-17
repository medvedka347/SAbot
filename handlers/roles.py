"""
Модуль управления ролями пользователей.
"""
import logging
from telegram import Update, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import MODULE_ACCESS, ROLES, ROLE_BUNDLES
from db_utils import require_any_role, get_all_users, set_users_batch, delete_user, get_user_by_id, get_user_by_username, add_or_update_user, set_user_roles
from utils import check_rate_limit, kb, inline_kb, back_kb, get_role_emoji, format_user, parse_users_input, escape_md
from handlers.conversation_utils import get_user_state, set_user_state, clear_user_state

STATE_ROLES_MENU = "roles_menu"
STATE_ROLES_INPUT_USERS = "roles_input_users"
STATE_ROLES_SELECTING_ROLE = "roles_selecting_role"
STATE_ROLES_SELECTING_USER_TO_DELETE = "roles_selecting_user_to_delete"

roles_menu_kb = kb(["📋 Список пользователей", "➕ Назначить роль", "🗑️ Удалить пользователя"], "🔙 Назад")
USERS_PER_PAGE = 25


def role_kb(prefix: str):
    return inline_kb([
        [InlineKeyboardButton(text="👤 User", callback_data=f"{prefix}:user")],
        [InlineKeyboardButton(text="🎓 Mentor", callback_data=f"{prefix}:mentor")],
        [InlineKeyboardButton(text="📋 Manager (набор)", callback_data=f"{prefix}:manager")],
        [InlineKeyboardButton(text="📊 Analyst", callback_data=f"{prefix}:analyst")],
        [InlineKeyboardButton(text="👑 Admin", callback_data=f"{prefix}:admin")],
    ])


def build_users_pagination_keyboard(page: int, total_pages: int):
    buttons = []
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"users_page:{page-1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"users_page:{page+1}"))
    buttons.append(nav_row)
    return inline_kb(buttons)


async def roles_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth = await require_any_role(update, context, MODULE_ACCESS["roles_crud"])
    if not auth:
        return
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Нет прав.")
        return
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    await set_user_state(context, STATE_ROLES_MENU)
    text = (
        "👥 *Управление ролями пользователей*\n\n"
        "📋 *Список* — просмотр всех пользователей\n"
        "➕ *Назначить роль* — добавить/изменить роль\n"
        "   Поддерживается:\n"
        "   • Только ID: `123456789`\n"
        "   • Только @username: `@ivan`\n"
        "   • Оба значения: `123456789 @ivan`\n"
        "   • Несколько: `@ivan, @petr, 123456789`\n\n"
        "🗑️ *Удалить* — удалить пользователя"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=roles_menu_kb)


async def _show_users_page(message_or_callback, users: list, page: int, total_pages: int, total_users: int, is_callback: bool = False):
    start_idx = page * USERS_PER_PAGE
    end_idx = min(start_idx + USERS_PER_PAGE, len(users))
    page_users = users[start_idx:end_idx]
    by_role = {r: [] for r in ROLES}
    for u in page_users:
        role = u.get('role') or 'user'
        if role in by_role:
            by_role[role].append(u)
        else:
            by_role['user'].append(u)
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
        await message_or_callback.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def roles_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_ROLES_MENU:
        return
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Нет прав.")
        return
    await update.effective_chat.send_action(action="typing")
    users = await get_all_users()
    if not users:
        await update.message.reply_text("📭 Пользователей нет")
        await roles_menu(update, context)
        return
    by_role = {r: [] for r in ROLES}
    for u in users:
        by_role[u['role']].append(u)
    all_users_flat = []
    for role in ROLES:
        for u in by_role[role]:
            all_users_flat.append(u)
    total_users = len(all_users_flat)
    total_pages = (total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE
    await _show_users_page(update, all_users_flat, 0, total_pages, total_users, is_callback=False)
    await roles_menu(update, context)


async def users_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        page = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        return
    users = await get_all_users()
    by_role = {r: [] for r in ROLES}
    for u in users:
        by_role[u['role']].append(u)
    all_users_flat = []
    for role in ROLES:
        for u in by_role[role]:
            all_users_flat.append(u)
    total_users = len(all_users_flat)
    total_pages = (total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE
    await _show_users_page(query, all_users_flat, page, total_pages, total_users, is_callback=True)


async def role_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_ROLES_MENU:
        return
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Нет прав.")
        return
    await set_user_state(context, STATE_ROLES_INPUT_USERS)
    text = (
        "Введите пользователей для назначения роли:\n\n"
        "*Форматы:*\n"
        "• `123456789` — только ID\n"
        "• `@ivan_petrov` — только username\n"
        "• `123456789 @ivan_petrov` — оба значения\n"
        "• Несколько: `@ivan, @petr, 123456789`\n\n"
        "Бот свяжет ID и username если они указаны вместе."
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=back_kb)


async def role_receive_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_ROLES_INPUT_USERS:
        return
    if not update.message.text:
        return
    users, errors = parse_users_input(update.message.text)
    if not users:
        await update.message.reply_text("❌ Не найдено корректных данных. Попробуйте снова:", reply_markup=back_kb)
        return
    if errors:
        await update.message.reply_text(f"⚠️ Пропущены некорректные данные: {', '.join(errors[:5])}")
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
    context.user_data["users_to_assign"] = users
    await set_user_state(context, STATE_ROLES_SELECTING_ROLE)
    await update.message.reply_text(
        f"Найдено *{len(users)}* пользователей:\n" + "\n".join(preview) + "\n\nВыберите роль:",
        parse_mode="Markdown",
        reply_markup=role_kb("set_role")
    )


async def role_set_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if get_user_state(context) != STATE_ROLES_SELECTING_ROLE:
        await query.edit_message_text("❌ Сессия устарела")
        return
    role = query.data.split(":")[1]
    users = context.user_data.get("users_to_assign", [])
    if not users:
        await query.edit_message_text("❌ Ошибка: список пуст")
        await clear_user_state(context)
        return
    context.user_data["selected_role"] = role
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
    role_emoji = {"user": "👤", "mentor": "🎓", "manager": "📋", "analyst": "📊", "admin": "👑"}
    bundle_label = ""
    if role in ROLE_BUNDLES:
        bundle_parts = ", ".join(sorted(ROLE_BUNDLES[role]))
        bundle_label = f"\n_Набор: {bundle_parts}_"
    await query.edit_message_text(
        f"🎯 *Назначить роль?*\n\n"
        f"Роль: {role_emoji.get(role, '👤')} `{role}`{bundle_label}\n"
        f"Пользователей: *{len(users)}*\n\n"
        f"Список:\n" + "\n".join(preview) + "\n\n"
        f"Подтвердите назначение:",
        parse_mode="Markdown",
        reply_markup=inline_kb([
            [InlineKeyboardButton(text="✅ Да, назначить", callback_data="conf_set_role")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_set_role")]
        ])
    )


async def role_set_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if get_user_state(context) != STATE_ROLES_SELECTING_ROLE:
        await query.edit_message_text("❌ Сессия устарела")
        return
    users = context.user_data.get("users_to_assign", [])
    role = context.user_data.get("selected_role")
    if not users or not role:
        await query.edit_message_text("❌ Ошибка: данные не найдены")
        await clear_user_state(context)
        return
    if role in ROLE_BUNDLES:
        bundle_roles = list(ROLE_BUNDLES[role])
        for user in users:
            await add_or_update_user(user_id=user.get("user_id"), username=user.get("username"), role=None)
            uid = user.get("user_id")
            if uid:
                await set_user_roles(uid, bundle_roles)
        await query.edit_message_text(f"✅ Набор `{role}` назначен для *{len(users)}* пользователей!")
    else:
        await set_users_batch(users, role)
        await query.edit_message_text(f"✅ Роль `{role}` назначена для *{len(users)}* пользователей!")
    await clear_user_state(context)


async def role_set_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("❌ Назначение отменено")
    await query.edit_message_text("❌ Назначение роли отменено")
    await clear_user_state(context)


async def role_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_ROLES_MENU:
        return
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Нет прав.")
        return
    users = await get_all_users()
    if not users:
        await update.message.reply_text("📭 Нет пользователей", reply_markup=roles_menu_kb)
        return
    await set_user_state(context, STATE_ROLES_SELECTING_USER_TO_DELETE)
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
    await update.message.reply_text("🗑️ Выберите пользователя для удаления:", reply_markup=inline_kb(keyboard))


async def role_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if get_user_state(context) != STATE_ROLES_SELECTING_USER_TO_DELETE:
        await query.edit_message_text("❌ Сессия устарела")
        return
    if query.data == "noop":
        return
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.edit_message_text("❌ Некорректные данные")
        return
    key_type = parts[1]
    key_value = parts[2]
    if key_type == "id":
        user = await get_user_by_id(int(key_value))
    else:
        user = await get_user_by_username(key_value)
    if not user:
        await query.edit_message_text("❌ Пользователь не найден")
        await clear_user_state(context)
        return
    context.user_data["del_user_type"] = key_type
    context.user_data["del_user_value"] = key_value
    user_text = format_user(user)
    await query.edit_message_text(
        f"🗑️ *Удалить пользователя?*\n\n"
        f"{user_text}\n\n"
        f"⚠️ Это действие нельзя отменить.",
        parse_mode="Markdown",
        reply_markup=inline_kb([
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data="conf_del_user")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_del_user")]
        ])
    )


async def role_delete_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key_type = context.user_data.get("del_user_type")
    key_value = context.user_data.get("del_user_value")
    if not key_type or not key_value:
        await query.edit_message_text("❌ Ошибка: данные не найдены")
        await clear_user_state(context)
        return
    if key_type == "id":
        success = await delete_user(user_id=int(key_value))
    else:
        success = await delete_user(username=key_value)
    if success:
        await query.edit_message_text("✅ Пользователь удалён")
    else:
        await query.edit_message_text("❌ Не удалось удалить")
    await clear_user_state(context)


async def role_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("❌ Удаление отменено")
    await query.edit_message_text("❌ Удаление отменено")
    await clear_user_state(context)
