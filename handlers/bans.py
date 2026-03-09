"""
Модуль управления банами.

Включает:
- Просмотр активных банов
- Снятие банов
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from config import MODULE_ACCESS
from db_utils import get_active_bans, unban_user, db as _db, HasRole
from utils import check_rate_limit, inline_kb

router = Router(name="bans")


# ==================== View Bans ====================

@router.message(F.text == "🚫 Управление банами", HasRole(min_priority=MODULE_ACCESS["bans"]))
async def bans_menu(message: Message, state: FSMContext):
    """Меню управления банами — показывает активные баны."""
    # Блокируем вызов через reply на чужое сообщение
    if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
        await message.answer("❌ Нет прав.")
        return
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    bans = await get_active_bans()
    if not bans:
        await message.answer("✅ Активных банов нет.")
        return
    
    lines = [f"🚫 *Активные баны ({len(bans)}):*\n"]
    for b in bans:
        who = f"@{b['username']}" if b['username'] else f"ID:{b['user_id']}"
        until = b['banned_until'][:16]
        lines.append(f"• {who} | уровень {b['ban_level']} | до {until}")
    
    kb_inline = inline_kb([
        [InlineKeyboardButton(
            text=f"🔓 {('@' + b['username']) if b['username'] else ('ID:' + str(b['user_id']))}",
            callback_data=f"unban:{b['id']}"
        )]
        for b in bans
    ])
    await message.answer("\n".join(lines), parse_mode="Markdown", reply_markup=kb_inline)


# ==================== Unban ====================

@router.callback_query(F.data.startswith("unban:"), HasRole(min_priority=MODULE_ACCESS["bans"]))
async def ban_unban_callback(callback: CallbackQuery, state: FSMContext):
    """Callback снятия бана по ID записи бана."""
    await callback.answer()
    
    ok, wait = check_rate_limit(callback.from_user.id)
    if not ok:
        await callback.message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    try:
        ban_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.edit_text("❌ Некорректные данные")
        return
    
    # Находим бан по id и снимаем
    row = await _db.fetchone("SELECT user_id, username FROM bans WHERE id = ?", (ban_id,))
    if not row:
        await callback.message.edit_text("❌ Бан не найден (уже истёк?)")
        return
    
    await unban_user(user_id=row[0], username=row[1])
    who = f"@{row[1]}" if row[1] else f"ID:{row[0]}"
    await callback.message.edit_text(f"✅ Бан снят: {who}")
