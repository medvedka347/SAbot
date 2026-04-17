"""
Вспомогательные функции и утилиты для SABot (PTB версия).
"""
import logging
import re
from time import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import ROLE_ADMIN, ROLE_MENTOR, ROLES, STAGES


# ==================== RATE LIMITING ====================

_rate_limits = {}
RATE_LIMIT_WINDOW = 10.0
RATE_LIMIT_MAX_REQUESTS = 20
RATE_LIMIT_MIN_GAP = 0.15
MAX_RATE_LIMIT_ENTRIES = 50000
RATE_LIMIT_CLEANUP_RATIO = 0.2

_group_rate_limits = {}
GROUP_RATE_LIMIT_WINDOW = 30.0
GROUP_RATE_LIMIT_MAX = 3
GROUP_RATE_LIMIT_MUTE = 60
MAX_GROUP_RATE_LIMIT_ENTRIES = 10000


def check_rate_limit(user_id: int) -> tuple[bool, int]:
    now = time()
    if user_id not in _rate_limits:
        _rate_limits[user_id] = []
    _rate_limits[user_id] = [t for t in _rate_limits[user_id] if now - t < RATE_LIMIT_WINDOW]
    if _rate_limits[user_id]:
        last_request = _rate_limits[user_id][-1]
        gap = now - last_request
        if gap < RATE_LIMIT_MIN_GAP:
            wait = max(1, int(RATE_LIMIT_MIN_GAP - gap))
            return False, wait
    if len(_rate_limits[user_id]) >= RATE_LIMIT_MAX_REQUESTS:
        oldest = _rate_limits[user_id][0]
        wait = int(RATE_LIMIT_WINDOW - (now - oldest)) + 1
        return False, wait
    _rate_limits[user_id].append(now)
    if len(_rate_limits) > MAX_RATE_LIMIT_ENTRIES:
        sorted_items = sorted(_rate_limits.items(), key=lambda x: x[1][-1] if x[1] else 0)
        to_remove = int(len(sorted_items) * RATE_LIMIT_CLEANUP_RATIO)
        for k, _ in sorted_items[:to_remove]:
            del _rate_limits[k]
        logging.warning(f"Rate limit cleanup: removed {to_remove} old entries")
    return True, 0


def check_group_rate_limit(chat_id: int, command: str) -> tuple[bool, bool]:
    now = time()
    if chat_id not in _group_rate_limits:
        _group_rate_limits[chat_id] = {}
    if command not in _group_rate_limits[chat_id]:
        _group_rate_limits[chat_id][command] = {"timestamps": [], "muted_until": 0}
    data = _group_rate_limits[chat_id][command]
    if data["muted_until"] > now:
        return False, True
    data["timestamps"] = [t for t in data["timestamps"] if now - t < GROUP_RATE_LIMIT_WINDOW]
    if len(data["timestamps"]) >= GROUP_RATE_LIMIT_MAX:
        data["muted_until"] = now + GROUP_RATE_LIMIT_MUTE
        data["timestamps"] = []
        return False, True
    data["timestamps"].append(now)
    if len(_group_rate_limits) > MAX_GROUP_RATE_LIMIT_ENTRIES:
        _cleanup_group_rate_limits(now)
        if len(_group_rate_limits) > MAX_GROUP_RATE_LIMIT_ENTRIES:
            cutoff = now - 300
            old_chats = [cid for cid, cmds in _group_rate_limits.items()
                        if all(cmd_data.get("muted_until", 0) < cutoff and
                              (not cmd_data.get("timestamps") or cmd_data["timestamps"][-1] < cutoff)
                              for cmd_data in cmds.values())]
            for cid in old_chats:
                del _group_rate_limits[cid]
    return True, False


def _cleanup_group_rate_limits(now: float):
    to_delete = []
    for chat_id, commands in _group_rate_limits.items():
        empty_commands = [cmd for cmd, data in commands.items() if data["muted_until"] < now and not data["timestamps"]]
        for cmd in empty_commands:
            del commands[cmd]
        if not commands:
            to_delete.append(chat_id)
    for chat_id in to_delete:
        del _group_rate_limits[chat_id]


# ==================== DATE PARSING ====================

def parse_date_flexible(date_str: str) -> str | None:
    date_str = date_str.strip()
    if date_str.lower() in ['сегодня', 'now', '-', 'today']:
        return datetime.now().strftime("%d.%m.%y")
    normalized = date_str.replace(',', '.')
    match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$', normalized)
    if not match:
        return None
    day, month, year = match.groups()
    day = day.zfill(2)
    month = month.zfill(2)
    if len(year) == 4:
        year = year[2:]
    try:
        datetime.strptime(f"{day}.{month}.{year}", "%d.%m.%y")
        return f"{day}.{month}.{year}"
    except ValueError:
        return None


def parse_datetime_flexible(text: str) -> tuple[str | None, str | None]:
    text = text.strip()
    try:
        dt = datetime.fromisoformat(text)
        if dt <= datetime.now():
            return None, "❌ Дата должна быть в будущем!"
        return dt.strftime("%Y-%m-%d %H:%M:%S"), None
    except ValueError:
        pass
    patterns = [
        (r'^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})$', 'dmy'),
        (r'^(\d{1,2})\.(\d{1,2})\.(\d{2})\s+(\d{1,2}):(\d{2})$', 'dmy_short'),
        (r'^(сегодня|today)\s+(\d{1,2}):(\d{2})$', 'today'),
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
                if dt <= datetime.now():
                    return None, "❌ Дата должна быть в будущем!"
                return dt.strftime("%Y-%m-%d %H:%M:%S"), None
            except ValueError as e:
                return None, f"❌ Неверная дата или время: {str(e)}"
    return None, "❌ Неверный формат. Используйте:\n• `2024-12-31 18:00:00`\n• `31.12.2024 18:00`\n• `сегодня 18:00`\n• `завтра 18:00`"


# ==================== KEYBOARDS ====================

def kb(buttons: list, back_button: str = None) -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=b)] for b in buttons]
    if back_button:
        keyboard.append([KeyboardButton(text=back_button)])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def inline_kb(buttons: list[list]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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
    return len(text) <= max_len


def is_valid_url(url: str) -> bool:
    if not url:
        return True
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


def get_stage_key(text: str) -> str | None:
    for key, name in STAGES.items():
        if name == text:
            return key
    return None


USERS_PER_PAGE = 25


def get_role_emoji(role: str) -> str:
    return {"admin": "👑", "mentor": "🎓", "user": "👤"}.get(role, "❓")


def escape_md(text: str) -> str:
    if not text:
        return ""
    chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in chars:
        text = text.replace(char, f"\\{char}")
    return text


def format_material(mat: dict) -> str:
    desc = f"\n   📝 {escape_md(mat['description'][:50])}..." if mat.get('description') else ""
    return f"🔹 *ID:{mat['id']}* [{escape_md(mat['title'])}]({mat['link']}){desc}"


def format_event(ev: dict) -> str:
    status = "✅" if ev['datetime'] > datetime.now().isoformat() else "⏰"
    link = f"[🔗]({ev['link']})" if ev['link'] else ""
    return f"{status} *ID:{ev['id']}* {escape_md(ev['type'])} ({ev['datetime'][:10]}) {link}"


def format_user(user: dict) -> str:
    parts = []
    if user.get("username"):
        parts.append(f"@{escape_md(user['username'])}")
    if user.get("user_id"):
        parts.append(f"ID:`{user['user_id']}`")
    return f"{get_role_emoji(user['role'])} {' + '.join(parts) if parts else 'Unknown'}"


def parse_users_input(text: str) -> tuple[list[dict], list[str]]:
    from db_utils import normalize_username, validate_user_id
    users = []
    errors = []
    parts = re.split(r'[\s,\n]+', text.strip())
    current_user = {}
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.startswith('@'):
            normalized = normalize_username(part)
            if not normalized:
                errors.append(part)
                continue
            if current_user.get("username"):
                users.append(current_user)
                current_user = {}
            current_user["username"] = normalized
        elif part.isdigit():
            validated_id = validate_user_id(int(part))
            if validated_id:
                if current_user.get("user_id"):
                    users.append(current_user)
                    current_user = {}
                current_user["user_id"] = validated_id
            else:
                errors.append(f"{part} (невалидный ID)")
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
    if current_user:
        users.append(current_user)
    return users, errors


async def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    from db_utils import get_user_roles
    from config import can_access
    roles = await get_user_roles(user_id=user_id)
    role_keys = [r['role_key'] for r in roles]
    keyboard = []
    
    # Базовые кнопки (для всех)
    user_buttons = ["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок", "🤝 Buddy"]
    row = []
    for btn in user_buttons:
        row.append(btn)
        if len(row) == 2:
            keyboard.append([KeyboardButton(text=b) for b in row])
            row = []
    if row:
        keyboard.append([KeyboardButton(text=b) for b in row])
    
    # Админские управленческие функции
    admin_buttons = []
    if can_access("events_crud", role_keys):
        admin_buttons.append("📋 Управление событиями")
    if can_access("materials_crud", role_keys):
        admin_buttons.append("📦 Управление материалами")
    if can_access("roles_crud", role_keys):
        admin_buttons.append("👥 Управление ролями")
    if can_access("bans_crud", role_keys):
        admin_buttons.append("🚫 Управление банами")
    
    if admin_buttons:
        keyboard.append([KeyboardButton(text="───── ⚙️ Управление ─────")])
        for btn in admin_buttons:
            keyboard.append([KeyboardButton(text=btn)])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


# ==================== GLOBAL ERROR HANDLER ====================

import os
DEBUG_MODE = os.getenv('DEBUG_MODE', 'False').lower() == 'true'


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    from telegram import Update
    logging.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        if DEBUG_MODE:
            error_msg = f"❌ Ошибка: {str(context.error)[:200]}"
        else:
            error_msg = "❌ Произошла ошибка. Попробуйте позже или обратитесь к администратору."
        await update.effective_message.reply_text(error_msg)


# ==================== INPUT VALIDATION ====================

def sanitize_input(text: str, max_length: int = 2000, allow_newlines: bool = True) -> str | None:
    if not text or not isinstance(text, str):
        return None
    if '\x00' in text:
        return None
    allowed_control = {'\n', '\r', '\t'} if allow_newlines else set()
    for char in text:
        if ord(char) < 32 and char not in allowed_control:
            return None
    if len(text) > max_length:
        return None
    dangerous_chars = ['\u202E', '\u202D', '\u200E', '\u200F']
    for char in dangerous_chars:
        if char in text:
            text = text.replace(char, '')
    return text.strip()


def validate_callback_data(data: str, prefix: str, param_type: str = 'int') -> int | str | None:
    import re
    if not data or not isinstance(data, str):
        return None
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
    if re.match(r'^[\w\-@]+$', value):
        return value
    return None
