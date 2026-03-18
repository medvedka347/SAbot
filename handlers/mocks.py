"""
Модуль записи на мок-интервью.

Включает:
- Меню выбора ментора (динамически из конфига)
- Ссылки на календари для записи
"""
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

from config import MOCK_MENTORS
from utils import check_rate_limit, user_kb

router = Router(name="mocks")


# ==================== Keyboards ====================

def build_mock_kb() -> ReplyKeyboardMarkup:
    """Создать клавиатуру менторов из конфига."""
    keyboard = []
    for mentor_name, mentor_data in MOCK_MENTORS.items():
        emoji = mentor_data.get("emoji", "👤")
        keyboard.append([KeyboardButton(text=f"{emoji} {mentor_name}")])
    keyboard.append([KeyboardButton(text="🔙 Назад")])
    
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
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
    
    # Формируем список доступных менторов
    available_mentors = [
        name for name, data in MOCK_MENTORS.items() 
        if data.get("available")
    ]
    available_text = ", ".join(available_mentors) if available_mentors else "пока нет"
    
    await message.answer(
        "📋 *Запись на мок*\n\n"
        "У нас проводится 2-4 мока\n\n"
        "⚠️ *Моки необходимо проходить после составления легенды!*\n\n"
        f"*Доступные менторы:* {available_text}\n\n"
        "Моки можно проходить в любом порядке.\n"
        "Если кто-то из собеседующих недоступен, выберите другого.\n\n"
        "*Действия перед моком:*\n"
        "1️⃣ Предупредить собеседующего\n"
        "2️⃣ Скинуть резюме собеседующему\n\n"
        "_Если очередь на мок больше 3 дней — напишите в ЛС администратору._",
        parse_mode="Markdown",
        reply_markup=(build_mock_kb() if message.chat.type == "private" else None)
    )


@router.message(F.text)
async def mock_select_handler(message: Message):
    """Обработчик выбора ментора для мока.
    
    Проверяет что текст заканчивается на имя ментора из MOCK_MENTORS.
    Работает независимо от эмодзи в начале строки.
    """
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    # Ищем имя ментора в конце строки (после эмодзи/префикса)
    text = message.text
    mentor = None
    for name in MOCK_MENTORS.keys():
        if text.endswith(name):
            mentor = name
            break
    
    if not mentor:
        return  # Не наше сообщение — пропускаем
    
    if mentor not in MOCK_MENTORS:
        await message.answer("❌ Ментор не найден")
        return
    
    mentor_data = MOCK_MENTORS[mentor]
    cal_link = mentor_data.get("cal_link")
    available = mentor_data.get("available", False)
    
    if available and cal_link:
        await message.answer(
            f"👤 *{mentor}*\n\n"
            f"[Записаться на мок]({cal_link})\n\n"
            f"Нажмите на ссылку для выбора удобного времени.",
            parse_mode="Markdown",
            reply_markup=(user_kb if message.chat.type == "private" else None)
        )
    else:
        await message.answer(
            f"👤 *{mentor}*\n\n"
            f"_Запись пока недоступна_\n\n"
            f"Выберите другого ментора или попробуйте позже.",
            parse_mode="Markdown",
            reply_markup=(build_mock_kb() if message.chat.type == "private" else None)
        )
