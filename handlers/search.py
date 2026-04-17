"""
Модуль поиска по материалам.
"""
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes

from config import STAGES
from db_utils import search_materials, search_materials_by_title
from utils import check_rate_limit, check_group_rate_limit, escape_md


async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text:
        return
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "🔍 *Поиск по материалам*\n\n"
            "Использование: `/search <запрос>`\n"
            "Пример: `/search REST API`",
            parse_mode="Markdown"
        )
        return
    query = parts[1].strip()
    if len(query) > 100:
        await update.message.reply_text("❌ Запрос слишком длинный (макс 100 символов)")
        return
    results = await search_materials(query)
    if not results:
        await update.message.reply_text(f"🔍 По запросу *{query}* ничего не найдено.", parse_mode="Markdown")
        return
    lines = [f'🔍 *Результаты по запросу "{query}" ({len(results)}):*\n']
    for m in results[:20]:
        stage_name = STAGES.get(m['stage'], m['stage'])
        lines.append(f"• [{m['title']}]({m['link']}) _({stage_name})_")
    if len(results) > 20:
        lines.append(f"\n_...и ещё {len(results) - 20} результатов_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


async def group_events_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return
    if update.message.reply_to_message:
        return
    ok, muted = check_group_rate_limit(update.effective_chat.id, "events")
    if muted:
        return
    if not ok:
        await update.message.reply_text("⏱️ Слишком быстро! Подождите минуту.")
        return
    from db_utils import get_events
    events = await get_events(upcoming_only=True)
    if not events:
        await update.message.reply_text("📭 Нет предстоящих событий")
        return
    lines = ["📅 *Предстоящие события:*\n"]
    for e in events:
        lines.append(f"• *{e['type']}* — {e['datetime'][:10]}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def group_help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return
    if update.message.reply_to_message:
        return
    ok, muted = check_group_rate_limit(update.effective_chat.id, "help")
    if muted:
        return
    if not ok:
        await update.message.reply_text("⏱️ Слишком быстро! Подождите минуту.")
        return
    help_text = (
        "🤖 *Команды SABot в группе:*\n\n"
        "`/sabot_help` - эта справка\n"
        "`/events` - предстоящие события\n"
        "`/material <название>` - найти материал\n"
        "`/off` или `/remove_kb` - убрать клавиатуру бота\n\n"
        "_Для управления используйте бота в ЛС_"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def group_material_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return
    if update.message.reply_to_message:
        return
    ok, muted = check_group_rate_limit(update.effective_chat.id, "material")
    if muted:
        return
    if not ok:
        await update.message.reply_text("⏱️ Слишком быстро! Подождите минуту.")
        return
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "📚 *Поиск материала*\n\n"
            "Использование: `/material <ключевое слово>`\n"
            "Пример: `/material REST`",
            parse_mode="Markdown"
        )
        return
    query = parts[1].strip()
    for char in ['<', '>', '"', "'"]:
        query = query.replace(char, '')
    query = query.strip()
    if not query:
        await update.message.reply_text("❌ Укажите ключевое слово для поиска")
        return
    results = await search_materials_by_title(query)
    if not results:
        await update.message.reply_text(f"🔍 По запросу *{query}* ничего не найдено.", parse_mode="Markdown")
        return
    lines = [f'📚 *Результаты по "{query}":*\n']
    for m in results[:5]:
        lines.append(f"• [{m['title']}]({m['link']})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


async def group_remove_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return
    await update.message.reply_text("⌨️ Клавиатура скрыта.", reply_markup=ReplyKeyboardRemove())
