"""
Модуль управления банами.
"""
from telegram import Update, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import MODULE_ACCESS
from db_utils import require_any_role, get_active_bans, unban_user, db as _db
from utils import check_rate_limit, inline_kb


async def bans_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth = await require_any_role(update, context, MODULE_ACCESS["bans_crud"])
    if not auth:
        return
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Нет прав.")
        return
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    bans = await get_active_bans()
    if not bans:
        await update.message.reply_text("✅ Активных банов нет.")
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
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb_inline)


async def ban_unban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ok, wait = check_rate_limit(query.from_user.id)
    if not ok:
        await query.message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    try:
        ban_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    row = await _db.fetchone("SELECT user_id, username FROM bans WHERE id = ?", (ban_id,))
    if not row:
        await query.edit_message_text("❌ Бан не найден (уже истёк?)")
        return
    await unban_user(user_id=row[0], username=row[1])
    who = f"@{row[1]}" if row[1] else f"ID:{row[0]}"
    await query.edit_message_text(f"✅ Бан снят: {who}")
