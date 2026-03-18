import asyncio
import logging
import os
import signal
import sys
from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent

from config import BOT_TOKEN, DB_NAME
from db_utils import (
    init_db, setup_initial_users, cleanup_expired_bans, AuthMiddleware
)
from utils import error_handler

# Импортируем роутеры из модулей handlers
from handlers import common, materials, events, roles, bans, mocks, search, buddy

logging.basicConfig(level=logging.INFO)

# Защита от двойного запуска
PID_FILE = "/tmp/sabot_bot.pid"


def check_single_instance():
    """Проверяем что бот не запущен дважды."""
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
            if old_pid and os.path.exists(f"/proc/{old_pid}"):
                logging.error(f"Бот уже запущен (PID: {old_pid})")
                sys.exit(1)
    
    # Записываем текущий PID
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    """Удаляем PID файл при выходе."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except:
        pass


async def periodic_cleanup():
    """Периодическая очистка истёкших банов каждый час."""
    while True:
        await asyncio.sleep(3600)
        try:
            await cleanup_expired_bans()
        except Exception as e:
            logging.error(f"Error in periodic_cleanup: {e}")


async def main():
    # Проверяем что бот не запущен дважды
    check_single_instance()
    
    # Инициализация БД
    await init_db(DB_NAME)
    await setup_initial_users(DB_NAME)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    # Graceful shutdown handling
    shutdown_event = asyncio.Event()
    
    def signal_handler(signum, frame):
        logging.info(f"Получен сигнал {signum}, начинаем graceful shutdown...")
        shutdown_event.set()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Подключаем middleware авторизации (проверяет всех, кроме /start)
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    
    # Подключаем роутеры (порядок важен - fallback должен быть последним)
    dp.include_router(search.router)      # /search, /material (group), /sabot_help
    dp.include_router(materials.router)   # Материалы (CRUD + public)
    dp.include_router(events.router)      # События (CRUD + public)
    dp.include_router(roles.router)       # Управление ролями
    dp.include_router(bans.router)        # Управление банами
    dp.include_router(mocks.router)       # Запись на мок
    dp.include_router(buddy.router)       # 🤝 Buddy - система наставничества
    dp.include_router(common.router)      # /start, /help, ⚙️ Админка, 🔙 Назад, 🤝 Buddy, fallback
    
    # Глобальный обработчик ошибок
    dp.errors.register(error_handler)

    await bot.delete_webhook(drop_pending_updates=True)
    cleanup_task = asyncio.create_task(periodic_cleanup())
    logging.info("Бот запущен")
    
    try:
        # Запускаем polling с таймаутами и проверкой shutdown
        polling_task = asyncio.create_task(
            dp.start_polling(
                bot,
                skip_updates=True,
                polling_timeout=30,
                timeout=30,
                relax=0.1,
                error_sleep=5.0,
            )
        )
        
        # Ждем либо окончания polling, либо сигнала shutdown
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        
        done, pending = await asyncio.wait(
            [polling_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        
        # Если пришел сигнал shutdown - отменяем polling
        if shutdown_task in done:
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
        
    except Exception as e:
        logging.error(f"Fatal error in polling: {e}", exc_info=True)
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        remove_pid_file()


if __name__ == "__main__":
    asyncio.run(main())
