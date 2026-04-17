"""
Модуль записи на мок-интервью.
"""
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes

from config import MOCK_MENTORS
from utils import check_rate_limit, user_kb


def build_mock_kb() -> ReplyKeyboardMarkup:
    keyboard = []
    for mentor_name, mentor_data in MOCK_MENTORS.items():
        emoji = mentor_data.get("emoji", "👤")
        keyboard.append([KeyboardButton(text=f"{emoji} {mentor_name}")])
    keyboard.append([KeyboardButton(text="🔙 Назад")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def booking_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    available_mentors = [name for name, data in MOCK_MENTORS.items() if data.get("available")]
    available_text = ", ".join(available_mentors) if available_mentors else "пока нет"
    await update.message.reply_text(
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
        reply_markup=(build_mock_kb() if update.effective_chat.type == "private" else None)
    )


async def mock_select_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    text = update.message.text
    mentor = None
    for name in MOCK_MENTORS.keys():
        if text.endswith(name):
            mentor = name
            break
    if not mentor:
        return
    mentor_data = MOCK_MENTORS[mentor]
    cal_link = mentor_data.get("cal_link")
    available = mentor_data.get("available", False)
    if available and cal_link:
        await update.message.reply_text(
            f"👤 *{mentor}*\n\n"
            f"[Записаться на мок]({cal_link})\n\n"
            f"Нажмите на ссылку для выбора удобного времени.",
            parse_mode="Markdown",
            reply_markup=(user_kb if update.effective_chat.type == "private" else None)
        )
    else:
        await update.message.reply_text(
            f"👤 *{mentor}*\n\n"
            f"_Запись пока недоступна_\n\n"
            f"Выберите другого ментора или попробуйте позже.",
            parse_mode="Markdown",
            reply_markup=(build_mock_kb() if update.effective_chat.type == "private" else None)
        )
