"""
Модуль записи на мок-интервью.

Включает:
- Меню выбора ментора
- Ссылки на календари для записи
"""
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

from utils import check_rate_limit, user_kb

router = Router(name="mocks")


# ==================== Keyboards ====================

mock_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👤 Влад")],
        [KeyboardButton(text="👤 Регина")],
        [KeyboardButton(text="👤 Руслан")],
        [KeyboardButton(text="👤 Иван")],
        [KeyboardButton(text="🔙 Назад")],
    ],
    resize_keyboard=True
)


# ==================== Handlers ====================

@router.message(F.text.in_(["⏱️ Записаться на мок", "Записаться на мок"]))
async def booking_handler(message: Message):
    """Обработчик записи на мок."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await message.answer(
        "📋 *Запись на мок*\n\n"
        "У нас проводится 2-4 мока\n\n"
        "⚠️ *Моки необходимо проходить после составления легенды!*\n\n"
        "Моки можно проходить в любом порядке, снизу только рекомендованный порядок.\n"
        "Если кто-то из собеседующих недоступен, то идите к другому, а потом идите к недоступному когда он станет доступен.\n\n"
        "*Действия перед моком:*\n"
        "1️⃣ Предупредить собеседующего (чисто на всякий случай)\n"
        "2️⃣ Скинуть резюме собеседующему\n\n"
        "_Если очередь на мок больше 3 дней, то проверьте доступность других собеседующих._\n"
        "_Если очередь на мок больше 3 дней у всех, то напишите в ЛС. Найду слот_\n\n"
        '*Порядок "Первый, второй, третий, четвёртый" не является порядком, это просто список. '
        'Порядок может быть любым. Это написано для удобства структуры и читаемости*',
        parse_mode="Markdown",
        reply_markup=(mock_kb if message.chat.type == "private" else None)
    )


@router.message(F.text.in_(["👤 Влад", "👤 Регина", "👤 Руслан", "👤 Иван"]))
async def mock_select_handler(message: Message):
    """Обработчик выбора ментора для мока."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    mentor = message.text.replace("👤 ", "")
    
    if mentor == "Руслан":
        await message.answer(
            f"👤 *Руслан*\n\n"
            f"[Записаться на мок](https://cal.com/akhmadishin/мок)\n\n"
            f"Нажмите на ссылку для выбора удобного времени.",
            parse_mode="Markdown",
            reply_markup=(user_kb if message.chat.type == "private" else None)
        )
    elif mentor == "Регина":
        await message.answer(
            f"👤 *Регина*\n\n"
            f"[Записаться на мок](https://cal.com/ocpocmak/mock)\n\n"
            f"Нажмите на ссылку для выбора удобного времени.",
            parse_mode="Markdown",
            reply_markup=(user_kb if message.chat.type == "private" else None)
        )
    elif mentor in ["Влад", "Иван"]:
        await message.answer(
            f"👤 *{mentor}*\n\n"
            f"_Запись пока недоступна_\n\n"
            f"Выберите другого ментора или попробуйте позже.",
            parse_mode="Markdown",
            reply_markup=mock_kb
        )
