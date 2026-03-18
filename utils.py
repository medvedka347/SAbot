"""
Вспомогательные функции и утилиты для SABot.

Этот модуль содержит общие функции, используемые хендлерами из handlers/:
- Клавиатуры (kb, inline_kb)
- Rate limiting
- Форматирование текста
- Валидация URL
"""
import logging
import re
from time import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
from aiogram.types import ErrorEvent
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, \
    CallbackQuery, Message

from config import ROLE_ADMIN, ROLE_MENTOR, ROLES, STAGES


# ==================== RATE LIMITING ====================

# In-memory хранилище для rate limiting: {user_id: [timestamps]}
_rate_limits = {}
RATE_LIMIT_WINDOW = 10.0      # Увеличили окно до 10 секунд
RATE_LIMIT_MAX_REQUESTS = 20  # Увеличили до 20 запросов за окно
RATE_LIMIT_MIN_GAP = 0.15     # Уменьшили до 150ms между кликами

# Защита от переполнения памяти
MAX_RATE_LIMIT_ENTRIES = 50000  # Максимум записей
RATE_LIMIT_CLEANUP_RATIO = 0.2   # Очищать 20% при превышении


# Хранилище для групп: {chat_id: {"command": {"timestamps": [], "muted_until": 0}}}
_group_rate_limits = {}
GROUP_RATE_LIMIT_WINDOW = 30.0      # 30 секунд
GROUP_RATE_LIMIT_MAX = 3            # Макс 3 одинаковые команды
GROUP_RATE_LIMIT_MUTE = 60          # Мут на 60 секунд
MAX_GROUP_RATE_LIMIT_ENTRIES = 10000  # Максимум записей для групп


def check_rate_limit(user_id: int) -> tuple[bool, int]:
    """Очень мягкий rate limit для комфортной работы.
    Returns: (можно_обрабатывать, секунд_до_следующего_запроса)
    """
    now = time()
    
    if user_id not in _rate_limits:
        _rate_limits[user_id] = []
    
    # Очищаем старые записи (старше окна)
    _rate_limits[user_id] = [t for t in _rate_limits[user_id] if now - t < RATE_LIMIT_WINDOW]
    
    # Проверяем минимальный gap с последним запросом
    if _rate_limits[user_id]:
        last_request = _rate_limits[user_id][-1]
        gap = now - last_request
        if gap < RATE_LIMIT_MIN_GAP:
            wait = max(1, int(RATE_LIMIT_MIN_GAP - gap))
            return False, wait
    
    # Проверяем количество запросов за окно
    if len(_rate_limits[user_id]) >= RATE_LIMIT_MAX_REQUESTS:
        oldest = _rate_limits[user_id][0]
        wait = int(RATE_LIMIT_WINDOW - (now - oldest)) + 1
        return False, wait
    
    # Регистрируем запрос
    _rate_limits[user_id].append(now)
    
    # Защита от переполнения памяти
    if len(_rate_limits) > MAX_RATE_LIMIT_ENTRIES:
        # LRU cleanup: удаляем 20% самых старых записей
        sorted_items = sorted(_rate_limits.items(), key=lambda x: x[1][-1] if x[1] else 0)
        to_remove = int(len(sorted_items) * RATE_LIMIT_CLEANUP_RATIO)
        for k, _ in sorted_items[:to_remove]:
            del _rate_limits[k]
        logging.warning(f"Rate limit cleanup: removed {to_remove} old entries")
    
    return True, 0


# ==================== DATE PARSING ====================

def parse_date_flexible(date_str: str) -> str | None:
    """
    Гибкий парсинг даты. Поддерживает:
    - Разделители: точка, запятая
    - Год: 2 цифры (25) или 4 цифры (2025)
    - Специальные слова: сегодня, today, now, -
    - Возвращает: ДД.ММ.ГГ (2 цифры года)
    """
    date_str = date_str.strip()
    
    # Специальные слова
    if date_str.lower() in ['сегодня', 'now', '-', 'today']:
        return datetime.now().strftime("%d.%m.%y")
    
    # Заменяем запятые на точки для унификации
    normalized = date_str.replace(',', '.')
    
    # Паттерн: ДД.ММ.ГГ или ДД.ММ.ГГГГ
    match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$', normalized)
    if not match:
        return None
    
    day, month, year = match.groups()
    day = day.zfill(2)      # 5 -> 05
    month = month.zfill(2)  # 3 -> 03
    
    # Преобразуем 4-значный год в 2-значный
    if len(year) == 4:
        year = year[2:]
    
    # Валидация
    try:
        datetime.strptime(f"{day}.{month}.{year}", "%d.%m.%y")
        return f"{day}.{month}.{year}"
    except ValueError:
        return None


def parse_datetime_flexible(text: str) -> tuple[str | None, str | None]:
    """
    Гибкий парсинг даты и времени для событий.
    
    Поддерживает:
    - ISO формат: 2024-12-31 18:00:00
    - Точки: 31.12.2024 18:00 или 31.12.24 18:00
    - Слова: сегодня 18:00, завтра 18:00
    
    Returns:
        (iso_datetime_str, error_message)
        iso_datetime_str - строка в формате 2024-12-31 18:00:00 или None
        error_message - сообщение об ошибке или None
    """
    text = text.strip()
    
    # Пробуем ISO формат сразу
    try:
        dt = datetime.fromisoformat(text)
        if dt <= datetime.now():
            return None, "❌ Дата должна быть в будущем!"
        return dt.strftime("%Y-%m-%d %H:%M:%S"), None
    except ValueError:
        pass
    
    # Паттерны для разных форматов
    patterns = [
        # 31.12.2024 18:00 или 31.12.24 18:00
        (r'^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})$', 'dmy'),
        (r'^(\d{1,2})\.(\d{1,2})\.(\d{2})\s+(\d{1,2}):(\d{2})$', 'dmy_short'),
        # сегодня 18:00 или today 18:00
        (r'^(сегодня|today)\s+(\d{1,2}):(\d{2})$', 'today'),
        # завтра 18:00 или tomorrow 18:00
        (r'^(завтра|tomorrow)\s+(\d{1,2}):(\d{2})$', 'tomorrow'),
    ]
    
    for pattern, ptype in patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            try:
                if ptype == 'dmy':
                    day, month, year, hour, minute = match.groups()
                    dt = datetime(int(year), int(month), int(day), int(hour), int(minute))
                elif ptype == 'dmy_short':
                    day, month, year, hour, minute = match.groups()
                    year_int = int(year)
                    # Преобразуем 2-значный год в 4-значный (25 -> 2025, 99 -> 1999)
                    if year_int < 50:
                        year_int += 2000
                    else:
                        year_int += 1900
                    dt = datetime(year_int, int(month), int(day), int(hour), int(minute))
                elif ptype == 'today':
                    hour, minute = match.groups()[1:]
                    now = datetime.now()
                    dt = datetime(now.year, now.month, now.day, int(hour), int(minute))
                elif ptype == 'tomorrow':
                    hour, minute = match.groups()[1:]
                    tomorrow = datetime.now() + timedelta(days=1)
                    dt = datetime(tomorrow.year, tomorrow.month, tomorrow.day, int(hour), int(minute))
                
                # Проверяем что дата в будущем
                if dt <= datetime.now():
                    return None, "❌ Дата должна быть в будущем!"
                
                return dt.strftime("%Y-%m-%d %H:%M:%S"), None
                
            except ValueError as e:
                return None, f"❌ Неверная дата или время: {str(e)}"
    
    return None, "❌ Неверный формат. Используйте:\n• `2024-12-31 18:00:00`\n• `31.12.2024 18:00`\n• `сегодня 18:00`\n• `завтра 18:00`"


# ==================== RATE LIMITING FOR GROUPS ====================


def check_group_rate_limit(chat_id: int, command: str) -> tuple[bool, bool]:
    """
    Rate limit для команд в группах (общий на чат, по типу команды).
    Returns: (можно_обрабатывать, в_муте)
    """
    now = time()
    
    if chat_id not in _group_rate_limits:
        _group_rate_limits[chat_id] = {}
    
    if command not in _group_rate_limits[chat_id]:
        _group_rate_limits[chat_id][command] = {"timestamps": [], "muted_until": 0}
    
    data = _group_rate_limits[chat_id][command]
    
    # Проверяем мут
    if data["muted_until"] > now:
        return False, True
    
    # Очищаем старые записи
    data["timestamps"] = [t for t in data["timestamps"] if now - t < GROUP_RATE_LIMIT_WINDOW]
    
    # Проверяем количество запросов
    if len(data["timestamps"]) >= GROUP_RATE_LIMIT_MAX:
        # Ставим мут на эту команду
        data["muted_until"] = now + GROUP_RATE_LIMIT_MUTE
        data["timestamps"] = []
        return False, True
    
    # Регистрируем запрос
    data["timestamps"].append(now)
    
    # Защита от переполнения памяти для групп
    if len(_group_rate_limits) > MAX_GROUP_RATE_LIMIT_ENTRIES:
        _cleanup_group_rate_limits(now)
        # Если всё ещё переполнено — очищаем агрессивнее
        if len(_group_rate_limits) > MAX_GROUP_RATE_LIMIT_ENTRIES:
            cutoff = now - 300  # 5 минут
            old_chats = [chat_id for chat_id, data in _group_rate_limits.items() 
                        if all(cmd_data.get("muted_until", 0) < cutoff and 
                              (not cmd_data.get("timestamps") or cmd_data["timestamps"][-1] < cutoff)
                              for cmd_data in data.values())]
            for chat_id in old_chats:
                del _group_rate_limits[chat_id]
    
    return True, False


def _cleanup_group_rate_limits(now: float):
    """Очистка неактивных записей rate limiter для групп."""
    to_delete = []
    for chat_id, commands in _group_rate_limits.items():
        # Удаляем пустые команды
        empty_commands = [
            cmd for cmd, data in commands.items()
            if data["muted_until"] < now and not data["timestamps"]
        ]
        for cmd in empty_commands:
            del commands[cmd]
        
        # Если чат пустой - помечаем на удаление
        if not commands:
            to_delete.append(chat_id)
    
    # Удаляем пустые чаты
    for chat_id in to_delete:
        del _group_rate_limits[chat_id]


# ==================== KEYBOARDS ====================

def kb(buttons: list, back_button: str = None) -> ReplyKeyboardMarkup:
    """Создать ReplyKeyboardMarkup."""
    keyboard = [[KeyboardButton(text=b)] for b in buttons]
    if back_button:
        keyboard.append([KeyboardButton(text=back_button)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def inline_kb(buttons: list[list]) -> InlineKeyboardMarkup:
    """Создать InlineKeyboardMarkup."""
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Главные меню (для совместимости, рекомендуется использовать get_main_keyboard)
user_kb = kb(["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок", "🤝 Buddy"])
mentor_kb = kb(["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок", "🤝 Buddy",
                "📋 Управление событиями"])
admin_kb = kb(["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок", "🤝 Buddy",
               "───── ⚙️ Управление ─────",
               "📦 Управление материалами", "👥 Управление ролями", 
               "📋 Управление событиями", "🚫 Управление банами"])

back_kb = kb(["🏠 Главное меню"], "🔙 Назад")
main_menu_kb = kb(["🏠 Главное меню"])
stage_kb = kb(list(STAGES.values()) + ["🏠 Главное меню"], "🔙 Назад")


# ==================== HELPERS ====================

MAX_MESSAGE_LENGTH = 4000


def check_length(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> bool:
    """Проверка длины текста."""
    return len(text) <= max_len


def is_valid_url(url: str) -> bool:
    """Валидация URL."""
    if not url:
        return True  # Пустая ссылка разрешена
    if len(url) > 2000:
        return False
    
    try:
        result = urlparse(url)
        if result.scheme not in ('http', 'https'):
            return False
        if not result.netloc:
            return False
        if re.search(r'[<">。\'\s]', url):
            return False
        return True
    except Exception:
        return False


async def safe_edit_text(callback: CallbackQuery, text: str, **kwargs):
    """Безопасное редактирование сообщения с обработкой ошибок."""
    try:
        await callback.message.edit_text(text, **kwargs)
    except Exception as e:
        logging.debug(f"Cannot edit message: {e}")
        try:
            await callback.message.answer(text, **kwargs)
        except Exception as e2:
            logging.error(f"Cannot send message: {e2}")


def get_stage_key(text: str) -> str | None:
    """Получить ключ stage по отображаемому названию."""
    for key, name in STAGES.items():
        if name == text:
            return key
    return None


USERS_PER_PAGE = 25  # Пагинация для списка пользователей


def get_role_emoji(role: str) -> str:
    """Получить эмодзи для роли."""
    return {"admin": "👑", "mentor": "🎓", "user": "👤"}.get(role, "❓")


def escape_md(text: str) -> str:
    """Экранирование спецсимволов Markdown V2."""
    if not text:
        return ""
    chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in chars:
        text = text.replace(char, f"\\{char}")
    return text


def format_material(mat: dict) -> str:
    """Форматирование материала для отображения."""
    desc = f"\n   📝 {escape_md(mat['description'][:50])}..." if mat.get('description') else ""
    return f"🔹 *ID:{mat['id']}* [{escape_md(mat['title'])}]({mat['link']}){desc}"


def format_event(ev: dict) -> str:
    """Форматирование события для отображения."""
    status = "✅" if ev['datetime'] > datetime.now().isoformat() else "⏰"
    link = f"[🔗]({ev['link']})" if ev['link'] else ""
    return f"{status} *ID:{ev['id']}* {escape_md(ev['type'])} ({ev['datetime'][:10]}) {link}"


def format_user(user: dict) -> str:
    """Форматирование информации о пользователе."""
    parts = []
    if user.get("username"):
        parts.append(f"@{escape_md(user['username'])}")
    if user.get("user_id"):
        parts.append(f"ID:`{user['user_id']}`")
    return f"{get_role_emoji(user['role'])} {' + '.join(parts) if parts else 'Unknown'}"


def parse_users_input(text: str) -> tuple[list[dict], list[str]]:
    """
    Парсит ввод пользователей с валидацией.
    Поддерживает:
    - ID: 123456789
    - @username: @ivan_petrov
    - Комбинация: 123456789 @ivan_petrov
    - Разделители: пробел, запятая, перевод строки
    
    Returns: (список_пользователей, список_ошибок)
    """
    from db_utils import normalize_username, validate_user_id
    users = []
    errors = []
    
    # Разбиваем по разделителям
    parts = re.split(r'[\s,\n]+', text.strip())
    
    current_user = {}
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        # Проверяем @username
        if part.startswith('@'):
            normalized = normalize_username(part)
            if not normalized:
                errors.append(part)
                continue
            if current_user.get("username"):
                users.append(current_user)
                current_user = {}
            current_user["username"] = normalized
        # Проверяем ID
        elif part.isdigit():
            validated_id = validate_user_id(int(part))
            if validated_id:
                if current_user.get("user_id"):
                    users.append(current_user)
                    current_user = {}
                current_user["user_id"] = validated_id
            else:
                errors.append(f"{part} (невалидный ID)")
        # Проверяем username без @
        elif re.match(r'^[a-zA-Z0-9_]+$', part):
            normalized = normalize_username(part)
            if not normalized:
                errors.append(part)
                continue
            if current_user.get("username"):
                users.append(current_user)
                current_user = {}
            current_user["username"] = normalized
        else:
            errors.append(part)
    
    # Добавляем последнего пользователя
    if current_user:
        users.append(current_user)
    
    return users, errors


# ==================== KEYBOARD SELECTOR ====================

async def get_main_keyboard(user_id: int):
    """Получить клавиатуру для пользователя с учетом мультиролей и приоритетов.
    
    Структура:
    - Пользовательские функции (2x2 сетка)
    - Разделитель (если есть управленческие)
    - Управленческие функции (отдельные строки)
    """
    from db_utils import get_user_roles
    from config import get_max_priority

    roles = await get_user_roles(user_id=user_id)
    role_keys = [r['role_key'] for r in roles]
    max_priority = get_max_priority(role_keys)
    
    keyboard = []
    
    # === ПОЛЬЗОВАТЕЛЬСКИЕ ФУНКЦИИ (сетка 2x2) ===
    user_buttons = ["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок", "🤝 Buddy"]
    row = []
    for btn in user_buttons:
        row.append(btn)
        if len(row) == 2:
            keyboard.append([KeyboardButton(text=b) for b in row])
            row = []
    if row:
        keyboard.append([KeyboardButton(text=b) for b in row])
    
    # === УПРАВЛЕНЧЕСКИЕ ФУНКЦИИ ===
    admin_buttons = []
    
    # Менторские функции
    if max_priority >= 200:
        if "📋 Управление событиями" not in [b.text for row in keyboard for b in row]:
            admin_buttons.append("📋 Управление событиями")
    
    # Админские функции
    if max_priority >= 300:
        admin_buttons.extend([
            "📦 Управление материалами",
            "👥 Управление ролями",
            "🚫 Управление банами"
        ])
    
    # Добавляем разделитель и управленческие кнопки (каждая на отдельной строке)
    if admin_buttons:
        # Разделитель (визуально отделяем управленческие функции)
        keyboard.append([KeyboardButton(text="───── ⚙️ Управление ─────")])
        for btn in admin_buttons:
            keyboard.append([KeyboardButton(text=btn)])
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


# ==================== GLOBAL ERROR HANDLER ====================

# ==================== GLOBAL ERROR HANDLER ====================

import os

# Режим отладки (в продакшене должен быть False)
DEBUG_MODE = os.getenv('DEBUG_MODE', 'False').lower() == 'true'


async def error_handler(event: ErrorEvent):
    """Global error handler to prevent bot from crashing."""
    exception = event.exception
    
    # Всегда логируем полную ошибку с трейсом для диагностики
    logging.error(f"Error occurred: {exception}", exc_info=True)
    
    # Пользователю показываем безопасное сообщение (без деталей в продакшене)
    if DEBUG_MODE:
        # В режиме отладки показываем детали
        error_msg = f"❌ Ошибка: {str(exception)[:200]}"
    else:
        # В продакшене — generic message
        error_msg = "❌ Произошла ошибка. Попробуйте позже или обратитесь к администратору."
    
    # Try to notify user if possible
    if hasattr(event, 'message') and event.message:
        try:
            await event.message.answer(error_msg)
        except:
            pass
    elif hasattr(event, 'callback_query') and event.callback_query:
        try:
            await event.callback_query.answer("❌ Ошибка! Попробуйте позже.")
        except:
            pass


# ==================== INPUT VALIDATION ====================

def sanitize_input(text: str, max_length: int = 2000, allow_newlines: bool = True) -> str | None:
    """
    Валидация и очистка пользовательского ввода.
    
    Args:
        text: Исходный текст
        max_length: Максимальная длина (по умолчанию 2000)
        allow_newlines: Разрешены ли переносы строк
    
    Returns:
        Очищенный текст или None если ввод невалиден
    """
    if not text or not isinstance(text, str):
        return None
    
    # Проверка на null bytes
    if '\x00' in text:
        return None
    
    # Проверка на control characters (кроме разрешённых)
    allowed_control = {'\n', '\r', '\t'} if allow_newlines else set()
    for char in text:
        if ord(char) < 32 and char not in allowed_control:
            return None
    
    # Проверка длины
    if len(text) > max_length:
        return None
    
    # Проверка на RTL override и другие опасные Unicode
    dangerous_chars = ['\u202E', '\u202D', '\u200E', '\u200F']  # RTL/LTR marks
    for char in dangerous_chars:
        if char in text:
            text = text.replace(char, '')
    
    return text.strip()


def validate_callback_data(data: str, prefix: str, param_type: str = 'int') -> int | str | None:
    """
    Валидация callback_data для защиты от injection.
    
    Args:
        data: callback.data
        prefix: Ожидаемый префикс (например, 'edit_mat')
        param_type: Тип параметра ('int' или 'str')
    
    Returns:
        Валидное значение или None
    """
    import re
    
    if not data or not isinstance(data, str):
        return None
    
    # Проверка формата: prefix:value
    pattern = f'^{re.escape(prefix)}:(.+)$'
    match = re.match(pattern, data)
    if not match:
        return None
    
    value = match.group(1)
    
    if param_type == 'int':
        try:
            return int(value)
        except ValueError:
            return None
    
    # Для строк: разрешаем только буквы, цифры, underscore
    if re.match(r'^[\w\-@]+$', value):
        return value
    
    return None
