import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from config import BOT_TOKEN, DB_NAME, ROLE_ADMIN, ROLE_MENTOR
from db_utils import init_db, get_user_role, setup_initial_users, get_ban_status, record_failed_attempt, clear_failed_attempts, cleanup_expired_bans
from admin_module import register_handlers, mentor_kb, admin_kb, user_kb, mock_kb

logging.basicConfig(level=logging.INFO)


async def start_handler(message: Message):
    """Стартовый обработчик с проверкой бана."""
    user_id = message.from_user.id
    username = message.from_user.username
    
    # Проверяем бан (сначала чистим старые)
    cleanup_expired_bans()
    ban = get_ban_status(user_id=user_id, username=username)
    if ban:
        ban_level = ban['ban_level']
        
        ban_text = {
            1: "5 минут",
            2: "10 минут", 
            3: "1 месяц"
        }.get(ban_level, "некоторое время")
        
        await message.answer(
            f"❌ *Доступ временно заблокирован*\n\n"
            f"Причина: превышено количество попыток авторизации\n"
            f"Длительность: {ban_text}\n\n"
            f"Попробуйте позже или обратитесь к администратору.",
            parse_mode="Markdown"
        )
        return
    
    # Проверяем роль
    role = get_user_role(user_id=user_id, username=username)
    
    if not role:
        # Записываем неудачную попытку
        new_ban = record_failed_attempt(user_id=user_id, username=username)
        
        if new_ban:
            # Применен новый бан
            ban_until = new_ban['banned_until']
            await message.answer(
                f"❌ *Доступ запрещен*\n\n"
                f"3 неудачные попытки авторизации.\n"
                f"Вы заблокированы до: `{ban_until.strftime('%Y-%m-%d %H:%M:%S')}`",
                parse_mode="Markdown"
            )
        else:
            attempts = 3 - (new_ban.get('remaining', 2) if new_ban else 2)
            await message.answer(
                f"❌ У вас нет доступа к боту.\n\n"
                f"⚠️ После 3 неудачных попыток вы получите временный бан."
            )
        return
    
    # Успешная авторизация - очищаем неудачные попытки
    clear_failed_attempts(user_id=user_id, username=username)
    
    welcome = f"Привет, {message.from_user.first_name}! 👋\n\nРоль: *{role}*"
    
    # Выбираем клавиатуру по роли
    if role == ROLE_ADMIN:
        kb = admin_kb
    elif role == ROLE_MENTOR:
        kb = mentor_kb
    else:
        kb = user_kb
    
    await message.answer(welcome, parse_mode="Markdown", reply_markup=kb)


async def booking_handler(message: Message):
    """Обработчик записи на мок."""
    # Показываем детальную инструкцию и меню выбора ментора
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
        '*Порядок "Первый, второй, третий, четвёртый" не является порядком, это просто список. Порядок может быть любым. Это написано для удобства структуры и читаемости*',
        parse_mode="Markdown",
        reply_markup=mock_kb
    )


async def mock_select_handler(message: Message):
    """Обработчик выбора ментора для мока."""
    mentor = message.text.replace("👤 ", "")
    
    if mentor == "Руслан":
        await message.answer(
            f"👤 *Руслан*\n\n"
            f"[Записаться на мок](https://cal.com/akhmadishin/мок)\n\n"
            f"Нажмите на ссылку для выбора удобного времени.",
            parse_mode="Markdown",
            reply_markup=user_kb
        )
    elif mentor in ["Влад", "Регина", "Иван"]:
        await message.answer(
            f"👤 *{mentor}*\n\n"
            f"_Запись пока недоступна_\n\n"
            f"Выберите другого ментора или попробуйте позже.",
            parse_mode="Markdown",
            reply_markup=mock_kb
        )
    elif mentor == "🔙 Назад":
        # Возвращаем в главное меню
        role = get_user_role(user_id=message.from_user.id, username=message.from_user.username)
        if role == ROLE_ADMIN:
            kb = admin_kb
        elif role == ROLE_MENTOR:
            kb = mentor_kb
        else:
            kb = user_kb
        await message.answer("Главное меню:", reply_markup=kb)


async def main():
    init_db(DB_NAME)
    setup_initial_users(DB_NAME)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(start_handler, CommandStart())
    dp.message.register(booking_handler, F.text.in_(["⏱️ Записаться на мок", "Записаться на мок"]))
    dp.message.register(mock_select_handler, F.text.in_(["👤 Влад", "👤 Регина", "👤 Руслан", "👤 Иван", "🔙 Назад"]))
    
    register_handlers(dp)

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
