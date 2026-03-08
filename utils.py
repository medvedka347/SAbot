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
from datetime import datetime
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


def check_rate_limit(user_id: int) -> tuple[bool, int]:
    """Очень мягкий rate limit для комфортной работы.
    Returns: (можно_обрабатывать, секунд_до_следующего_запроса)
    """
    from time import time
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
    
    # Periodic cleanup
    if len(_rate_limits) > 10000:
        cutoff = now - 3600
        old_keys = [k for k, v in _rate_limits.items() if not v or v[-1] < cutoff]
        for k in old_keys:
            del _rate_limits[k]
    
    return True, 0


# ==================== RATE LIMITING FOR GROUPS ====================

# Хранилище для групп: {chat_id: {"command": {"timestamps": [], "muted_until": 0}}}
_group_rate_limits = {}
GROUP_RATE_LIMIT_WINDOW = 30.0      # 30 секунд
GROUP_RATE_LIMIT_MAX = 3            # Макс 3 одинаковые команды
GROUP_RATE_LIMIT_MUTE = 60          # Мут на 60 секунд


def check_group_rate_limit(chat_id: int, command: str) -> tuple[bool, bool]:
    """
    Rate limit для команд в группах (общий на чат, по типу команды).
    Returns: (можно_обрабатывать, в_муте)
    """
    from time import time
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
    
    # Periodic cleanup
    if len(_group_rate_limits) > 1000:
        _cleanup_group_rate_limits(now)
    
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


# Главные меню
user_kb = kb(["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок", "🤝 Buddy"])
mentor_kb = kb(["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок", "⚙️ Админка", "🤝 Buddy"])
admin_kb = kb(["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок",
               "📦 Управление материалами", "👥 Управление ролями", "📋 Управление событиями",
               "🚫 Управление банами", "🤝 Buddy", "🔙 Назад"])

back_kb = kb([], "🔙 Назад")
stage_kb = kb(list(STAGES.values()), "🔙 Назад")


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
    """Получить клавиатуру для пользователя с учетом мультиролей."""
    from db_utils import get_user_roles
    from config import ROLE_ADMIN, ROLE_MENTOR, ROLE_LION
    
    roles = await get_user_roles(user_id=user_id)
    
    # Собираем кнопки из всех ролей
    buttons = set()
    
    # User базовые кнопки (для всех)
    buttons.update(["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок", "🤝 Buddy"])
    
    if ROLE_MENTOR in roles:
        buttons.add("⚙️ Админка")
    
    if ROLE_ADMIN in roles:
        buttons.update([
            "📦 Управление материалами",
            "👥 Управление ролями",
            "📋 Управление событиями",
            "🚫 Управление банами"
        ])
        buttons.add("🔙 Назад")
    
    if ROLE_LION in roles:
        # Лев видит свою панель в Buddy
        pass  # 🤝 Buddy уже добавлено
    
    # Формируем keyboard
    keyboard = []
    row = []
    for btn in ["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок", "🤝 Buddy"]:
        if btn in buttons:
            row.append(btn)
        if len(row) == 2:
            keyboard.append([KeyboardButton(text=b) for b in row])
            row = []
    if row:
        keyboard.append([KeyboardButton(text=b) for b in row])
    
    # Добавляем админские кнопки
    admin_buttons = [b for b in buttons if b not in ["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок", "🤝 Buddy"]]
    for btn in admin_buttons:
        keyboard.append([KeyboardButton(text=btn)])
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


# ==================== GLOBAL ERROR HANDLER ====================

async def error_handler(event: ErrorEvent):
    """Global error handler to prevent bot from crashing."""
    exception = event.exception
    logging.error(f"Error occurred: {exception}", exc_info=True)
    # Try to notify user if possible
    if hasattr(event, 'message') and event.message:
        try:
            await event.message.answer("❌ Произошла ошибка. Попробуйте позже.")
        except:
            pass
    elif hasattr(event, 'callback_query') and event.callback_query:
        try:
            await event.callback_query.answer("❌ Ошибка!")
        except:
            pass
