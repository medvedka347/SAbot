"""
Модуль поиска по материалам.

Включает:
- /search <запрос> — поиск по материалам (в ЛС)
- /material <запрос> — поиск материалов в группах
"""
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.filters import Command

from config import STAGES
from db_utils import search_materials, search_materials_by_title
from utils import check_rate_limit, check_group_rate_limit, escape_md

router = Router(name="search")


# ==================== Private Search ====================

@router.message(Command("search"))
async def search_handler(message: Message):
    """Обработчик команды /search <запрос>."""
    if not message.text:
        return
    
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    # Извлекаем текст запроса после /search
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "🔍 *Поиск по материалам*\n\n"
            "Использование: `/search <запрос>`\n"
            "Пример: `/search REST API`",
            parse_mode="Markdown"
        )
        return
    
    query = parts[1].strip()
    if len(query) > 100:
        await message.answer("❌ Запрос слишком длинный (макс 100 символов)")
        return
    
    results = await search_materials(query)
    if not results:
        await message.answer(f"🔍 По запросу *{query}* ничего не найдено.", parse_mode="Markdown")
        return
    
    lines = [f'🔍 *Результаты по запросу "{query}" ({len(results)}):*\n']
    for m in results[:20]:  # Ограничиваем вывод
        stage_name = STAGES.get(m['stage'], m['stage'])
        lines.append(f"• [{m['title']}]({m['link']}) _({stage_name})_")
    if len(results) > 20:
        lines.append(f"\n_...и ещё {len(results) - 20} результатов_")
    
    await message.answer("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


# ==================== Group Commands ====================

@router.message(Command("sabot_help"))
async def group_help_handler(message: Message):
    """Справка по командам в группе (/sabot_help)."""
    if message.reply_to_message:
        return
    if message.chat.type == "private":
        return  # Только для групп
    
    ok, muted = check_group_rate_limit(message.chat.id, "help")
    if muted:
        return
    if not ok:
        await message.reply("⏱️ Слишком быстро! Подождите минуту.")
        return
    
    help_text = (
        "🤖 *Команды SABot в группе:*\n\n"
        "`/sabot_help` - эта справка\n"
        "`/events` - предстоящие события\n"
        "`/material <название>` - найти материал\n"
        "`/off` или `/remove_kb` - убрать клавиатуру бота\n\n"
        "_Для управления используйте бота в ЛС_"
    )
    await message.reply(help_text, parse_mode="Markdown")


@router.message(Command("material"))
async def group_material_handler(message: Message):
    """Поиск материала в группе (/material <название>)."""
    if message.reply_to_message:
        return
    if message.chat.type == "private":
        return
    
    ok, muted = check_group_rate_limit(message.chat.id, "material")
    if muted:
        return
    if not ok:
        await message.reply("⏱️ Слишком быстро! Подождите минуту.")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply(
            "📚 *Поиск материала*\n\n"
            "Использование: `/material <ключевое слово>`\n"
            "Пример: `/material REST`",
            parse_mode="Markdown"
        )
        return
    
    query = parts[1].strip()
    # Убираем угловые скобки и кавычки
    for char in ['<', '>', '"', "'"]:
        query = query.replace(char, '')
    query = query.strip()
    
    if not query:
        await message.reply("❌ Укажите ключевое слово для поиска")
        return
    
    results = await search_materials_by_title(query)
    
    if not results:
        await message.reply(f"🔍 По запросу *{query}* ничего не найдено.", parse_mode="Markdown")
        return
    
    lines = [f'📚 *Результаты по "{query}":*\n']
    for m in results[:5]:  # Максимум 5
        lines.append(f"• [{m['title']}]({m['link']})")
    
    await message.reply("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


@router.message(Command("off", "remove_kb"))
async def group_remove_keyboard(message: Message):
    """Удаление reply-клавиатуры в группе (/off или /remove_kb)."""
    if message.chat.type == "private":
        return
    
    await message.reply(
        "⌨️ Клавиатура скрыта.",
        reply_markup=ReplyKeyboardRemove()
    )
