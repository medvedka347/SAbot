import logging 
import re
from datetime import datetime
from urllib.parse import urlparse
from aiogram import F, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, \
    CallbackQuery, Message
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from config import ROLE_ADMIN, ROLE_MENTOR, ROLES, STAGES, ANNOUNCEMENT_GROUP_ID, ANNOUNCEMENT_TOPIC_ID
from db_utils import (
    get_user_role, get_user_by_id, get_user_by_username, IsAuthorizedUser,
    add_or_update_user, set_users_batch, delete_user, get_all_users,
    add_event, get_events, update_event, delete_event,
    add_material, get_materials, get_material, update_material, delete_material, get_materials_stats,
    normalize_username, validate_user_id, cleanup_expired_bans,
    search_materials, get_active_bans, unban_user, db as _db
)


# ==================== RATE LIMITING ====================

# In-memory хранилище для rate limiting: {user_id: [timestamps]}
_rate_limits = {}
RATE_LIMIT_WINDOW = 10.0      # Увеличили окно до 10 секунд
RATE_LIMIT_MAX_REQUESTS = 20  # Увеличили до 20 запросов за окно
RATE_LIMIT_MIN_GAP = 0.15     # Уменьшили до 150ms между кликами

def check_rate_limit(user_id: int) -> tuple[bool, int]:
    """Очень мягкий rate limit для комфортной работы.
    Позволяет быстро протыкивать кнопки без ограничений.
    Returns: (можно_обрабатывать, секунд_до_следующего_запроса)
    """
    from time import time
    now = time()
    
    if user_id not in _rate_limits:
        _rate_limits[user_id] = []
    
    # Очищаем старые записи (старше окна)
    _rate_limits[user_id] = [t for t in _rate_limits[user_id] if now - t < RATE_LIMIT_WINDOW]
    
    # Проверяем минимальный gap с последним запросом (очень мягко)
    if _rate_limits[user_id]:
        last_request = _rate_limits[user_id][-1]
        gap = now - last_request
        if gap < RATE_LIMIT_MIN_GAP:
            # Не блокируем, просто говорим подождать чуть-чуть
            wait = max(1, int(RATE_LIMIT_MIN_GAP - gap))
            return False, wait
    
    # Проверяем количество запросов за окно (очень мягко)
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
    
    # Periodic cleanup: если много чатов - чистим старые
    if len(_group_rate_limits) > 1000:
        _cleanup_group_rate_limits(now)
    
    return True, False


def _cleanup_group_rate_limits(now: float):
    """Очистка неактивных записей rate limiter для групп."""
    to_delete = []
    for chat_id, commands in _group_rate_limits.items():
        # Удаляем пустые команды (мут истек и нет таймстампов)
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


# ==================== FSM ====================

class Form(StatesGroup):
    menu = State()
    menu_events = State()  # Отдельное состояние для меню событий
    selecting_stage = State()
    selecting_item = State()
    input_title = State()
    input_link = State()
    input_desc = State()
    input_type = State()
    input_datetime = State()
    input_announcement = State()
    confirm_announce = State()  # Подтверждение размещения анонса в группе
    input_users = State()  # Новое: ввод пользователей (ID и/или @username)
    selecting_role = State()
    selecting_user_to_delete = State()
    editing_field = State()


# ==================== КЛАВИАТУРЫ ====================

def kb(buttons: list, back_button: str = None) -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=b)] for b in buttons]
    if back_button:
        keyboard.append([KeyboardButton(text=back_button)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def inline_kb(buttons: list[list]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Клавиатура выбора ментора для мока
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

# Главные меню
user_kb = kb(["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок", "🤝 Buddy"])
mentor_kb = kb(["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок", "⚙️ Админка", "🤝 Buddy"])
admin_kb = kb(["📚 Материалы", "📅 События комьюнити", "⏱️ Записаться на мок",
               "📦 Управление материалами", "👥 Управление ролями", "📋 Управление событиями",
               "🚫 Управление банами", "🤝 Buddy", "🔙 Назад"])
materials_menu_kb = kb(["📖 Просмотреть", "➕ Добавить", "✏️ Редактировать", "🗑️ Удалить", "📊 Статистика"], "🔙 Назад")
roles_menu_kb = kb(["📋 Список пользователей", "➕ Назначить роль", "🗑️ Удалить пользователя"], "🔙 Назад")
events_menu_kb = kb(["📖 Просмотреть", "➕ Добавить", "✏️ Редактировать", "🗑️ Удалить"], "🔙 Назад")
back_kb = kb([], "🔙 Назад")

# Клавиатура выбора stage
stage_kb = kb(list(STAGES.values()), "🔙 Назад")

# Inline клавиатуры
def role_kb(prefix: str) -> InlineKeyboardMarkup:
    return inline_kb([
        [InlineKeyboardButton(text="👤 User", callback_data=f"{prefix}:user")],
        [InlineKeyboardButton(text="🎓 Mentor", callback_data=f"{prefix}:mentor")],
        [InlineKeyboardButton(text="👑 Admin", callback_data=f"{prefix}:admin")],
    ])


# ==================== ФИЛЬТРЫ ====================

async def check_role(message: Message, role: str) -> bool:
    """Проверить роль пользователя по ID или username."""
    # Сначала проверяем по ID
    user_role = await get_user_role(user_id=message.from_user.id)
    if user_role == role:
        return True
    
    # Если не нашли по ID, проверяем по username
    if message.from_user.username:
        user_role = await get_user_role(username=message.from_user.username)
        if user_role == role:
            return True
    
    return False


class HasRole:
    """Универсальный фильтр проверки роли."""
    def __init__(self, role: str):
        self.role = role
    
    async def __call__(self, message: Message) -> bool:
        return await check_role(message, self.role)


# ==================== ХЕЛПЕРЫ ====================

MAX_MESSAGE_LENGTH = 4000  # Лимит Telegram с запасом

def check_length(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> bool:
    """Проверка длины текста."""
    return len(text) <= max_len


def is_valid_url(url: str) -> bool:
    """Валидация URL."""
    if not url:
        return True  # Пустая ссылка разрешена
    if len(url) > 2000:  # Макс длина URL
        return False
    
    try:
        result = urlparse(url)
        # Проверяем scheme и netloc
        if result.scheme not in ('http', 'https'):
            return False
        if not result.netloc:
            return False
        # Проверяем что нет странных символов в пути
        if re.search(r'[<>"。"\'\s]', url):
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
        # Пробуем отправить новое сообщение
        try:
            await callback.message.answer(text, **kwargs)
        except Exception as e2:
            logging.error(f"Cannot send message: {e2}")


def get_stage_key(text: str) -> str | None:
    for key, name in STAGES.items():
        if name == text:
            return key
    return None


USERS_PER_PAGE = 25  # Пагинация для списка пользователей


def get_role_emoji(role: str) -> str:
    return {"admin": "👑", "mentor": "🎓", "user": "👤"}.get(role, "❓")


def escape_md(text: str) -> str:
    """Экранирование спецсимволов Markdown V2."""
    if not text:
        return ""
    # Экранируем спецсимволы: _ * [ ] ( ) ~ ` > # + - = | { } . !
    chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in chars:
        text = text.replace(char, f"\\{char}")
    return text


def _format_entity(entity: dict, template: str, fields: dict) -> str:
    """Универсальное форматирование сущности."""
    data = {}
    for key, value in fields.items():
        if callable(value):
            data[key] = value(entity)
        else:
            data[key] = entity.get(value, "")
    return template.format(**data)


def format_material(mat: dict) -> str:
    desc = f"\n   📝 {escape_md(mat['description'][:50])}..." if mat.get('description') else ""
    return f"🔹 *ID:{mat['id']}* [{escape_md(mat['title'])}]({mat['link']}){desc}"


def format_event(ev: dict) -> str:
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
    - ID: 123456789 (валидируется: положительный, < 10^10)
    - @username: @ivan_petrov
    - Комбинация: 123456789 @ivan_petrov
    - Разделители: пробел, запятая, перевод строки
    
    Returns: (список_пользователей, список_ошибок)
    """
    users = []
    errors = []
    
    # Разбиваем по разделителям
    parts = re.split(r'[\s,\n]+', text.strip())
    
    current_user = {}
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        # Проверяем, является ли часть @username
        if part.startswith('@'):
            normalized = normalize_username(part)
            if not normalized:
                errors.append(part)
                continue
            # Если текущий пользователь уже имеет username - сохраняем и начинаем нового
            if current_user.get("username"):
                users.append(current_user)
                current_user = {}
            current_user["username"] = normalized
        # Проверяем, является ли часть ID (с валидацией)
        elif part.isdigit():
            validated_id = validate_user_id(int(part))
            if validated_id:
                # Если текущий пользователь уже имеет user_id - сохраняем и начинаем нового
                if current_user.get("user_id"):
                    users.append(current_user)
                    current_user = {}
                current_user["user_id"] = validated_id
            else:
                errors.append(f"{part} (невалидный ID)")
        # Проверяем, является ли часть username без @ (содержит буквы/цифры/_)
        elif re.match(r'^[a-zA-Z0-9_]+$', part):
            normalized = normalize_username(part)
            if not normalized:
                errors.append(part)
                continue
            # Если текущий пользователь уже имеет username - сохраняем и начинаем нового
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


# ==================== ГЛАВНОЕ МЕНЮ ====================

async def admin_handler(message: Message, state: FSMContext):
    # Проверяем rate limit
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await state.clear()
    role = await get_user_role(user_id=message.from_user.id)
    kb_map = {ROLE_ADMIN: admin_kb, ROLE_MENTOR: mentor_kb}
    text_map = {ROLE_ADMIN: "🔧 Панель администратора", ROLE_MENTOR: "🎓 Панель ментора"}
    
    if role in kb_map:
        await message.answer(text_map[role], reply_markup=kb_map[role])
    else:
        await message.answer("❌ Нет доступа.")


# ==================== МАТЕРИАЛЫ (CRUD) ====================

async def materials_menu(message: Message, state: FSMContext):
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    await state.set_state(Form.menu)
    await message.answer("📦 *Управление материалами*", parse_mode="Markdown", reply_markup=materials_menu_kb)


async def material_select_stage(message: Message, state: FSMContext):
    """Выбор stage для просмотра."""
    await state.set_state(Form.selecting_stage)
    await state.update_data(next_action="show_list")
    await message.answer("Выберите раздел:", reply_markup=stage_kb)


# Action handlers registry
_STAGE_ACTIONS = {
    "show_list": {
        "format": lambda m, s=None: format_material(m),
        "menu_after": "materials",
        "empty_msg": "Пусто.",
        "header": lambda stage, count: f"📚 *{STAGES[stage]}* ({count})\n\n",
    },
    "public_show": {
        "format": lambda m, s=None: f"• [{m['title']}]({m['link']})",
        "menu_after": None,
        "empty_msg": "Пока пусто.",
        "header": lambda stage, count: f"📚 *{STAGES[stage]}*\n\n",
    },
}

_SELECT_ACTIONS = {
    "select_for_edit": {"prefix": "", "action": "edit"},
    "select_for_delete": {"prefix": "🗑️ ", "action": "del"},
}

async def handle_stage_selection(message: Message, state: FSMContext):
    """Универсальный обработчик выбора stage."""
    stage = get_stage_key(message.text)
    if not stage:
        return
    
    data = await state.get_data()
    next_action = data.get("next_action")
    
    if not next_action:
        await state.clear()
        await message.answer(
            "⚠️ Сессия устарела. Пожалуйста, начните сначала.",
            reply_markup=await get_main_keyboard(message.from_user.id)
        )
        return
    
    # Обработка list/show действий
    if next_action in _STAGE_ACTIONS:
        cfg = _STAGE_ACTIONS[next_action]
        mats = await get_materials(stage)
        stage_name = STAGES[stage]
        
        if not mats:
            text = f"📭 *{stage_name}*\n\n{cfg['empty_msg']}"
        else:
            lines = [cfg['format'](m) for m in mats]
            text = cfg['header'](stage, len(mats)) + "\n".join(lines)
        
        await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)
        
        if cfg['menu_after'] == "materials":
            await materials_menu(message, state)
        else:
            # Для public_show не очищаем state, оставляем возможность переключаться между разделами
            await state.set_state(Form.selecting_stage)
            await state.update_data(next_action="public_show")
        return
    
    # Обработка add_material
    if next_action == "add_material":
        await state.update_data(stage=stage)
        await state.set_state(Form.input_title)
        await message.answer("Введите название:", reply_markup=back_kb)
        return
    
    # Обработка select_for_edit / select_for_delete (оба используют одну логику)
    if next_action in _SELECT_ACTIONS:
        cfg = _SELECT_ACTIONS[next_action]
        mats = await get_materials(stage)
        if not mats:
            await message.answer("📭 Пусто", reply_markup=stage_kb)
            return
        
        await state.update_data(stage=stage)
        await state.set_state(Form.selecting_item)
        
        kb = inline_kb([
            [InlineKeyboardButton(
                text=f"{cfg['prefix']}{m['id']}. {m['title'][:30]}", 
                callback_data=f"{cfg['action']}_mat:{m['id']}"
            )] for m in mats
        ])
        
        label = "Выберите материал:" if next_action == "select_for_edit" else "Выберите для удаления:"
        await message.answer(label, reply_markup=kb)


# --- Добавление материалов ---

async def material_add_start(message: Message, state: FSMContext):
    await state.set_state(Form.selecting_stage)
    await state.update_data(next_action="add_material")
    await message.answer("➕ Выберите раздел для добавления:", reply_markup=stage_kb)


async def material_add_title(message: Message, state: FSMContext):
    if not message.text:
        return
    if not check_length(message.text, 200):  # Название не длиннее 200 символов
        await message.answer("❌ Название слишком длинное (макс 200 символов)")
        return
    await state.update_data(title=message.text)
    await state.set_state(Form.input_link)
    await message.answer("Введите ссылку (http://...):", reply_markup=back_kb)


async def material_add_link(message: Message, state: FSMContext):
    if not message.text:
        return
    link = message.text.strip()
    if not is_valid_url(link):
        await message.answer("❌ Некорректная ссылка. Используйте формат: https://example.com/page")
        return
    await state.update_data(link=link)
    await state.set_state(Form.input_desc)
    await message.answer("Введите описание (или 'пропустить'):", reply_markup=back_kb)


async def material_add_desc(message: Message, state: FSMContext):
    if not message.text:
        return
    desc = message.text.strip()
    if desc.lower() in ['пропустить', 'нет', '-']:
        desc = ""
    elif not check_length(desc, 1000):  # Описание не длиннее 1000 символов
        await message.answer("❌ Описание слишком длинное (макс 1000 символов)")
        return
    data = await state.get_data()
    await add_material(data['stage'], data['title'], data['link'], desc)
    await message.answer(f"✅ Добавлено в *{STAGES[data['stage']]}*!", parse_mode="Markdown")
    await materials_menu(message, state)


# --- Редактирование материалов ---

async def material_edit_select_stage(message: Message, state: FSMContext):
    await state.set_state(Form.selecting_stage)
    await state.update_data(next_action="select_for_edit")
    await message.answer("✏️ Выберите раздел:", reply_markup=stage_kb)


async def _handle_item_callback(
    callback: CallbackQuery, 
    state: FSMContext,
    getter_func,
    state_to_set: State,
    edit_template: str,
    item_name_key: str = "title"
):
    """Универсальный обработчик callback для выбора элемента."""
    await callback.answer()
    try:
        item_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.edit_text("❌ Некорректные данные")
        return
    
    item = await getter_func(item_id)
    if not item:
        await callback.message.edit_text("❌ Не найдено")
        return
    
    await state.update_data(edit_id=item_id, edit_item=item)
    await state.set_state(state_to_set)
    
    name = item.get(item_name_key, str(item_id))
    await safe_edit_text(
        callback,
        edit_template.format(name=name),
        parse_mode="Markdown"
    )


async def material_edit_callback(callback: CallbackQuery, state: FSMContext):
    try:
        await _handle_item_callback(
            callback, state,
            getter_func=get_material,
            state_to_set=Form.editing_field,
            edit_template=(
                "✏️ Редактирование *{name}*\n\n"
                "Отправьте новые данные в формате:\n"
                "`название\n\nссылка\n\nописание`\n\n"
                "Используйте '.' для пропуска поля"
            )
        )
    except Exception as e:
        logging.error(f"Error in material_edit_callback: {e}")
        await safe_edit_text(callback, "❌ Ошибка обработки")


async def material_edit_process(message: Message, state: FSMContext):
    if not message.text:
        return
    data = await state.get_data()
    mat_id = data.get('edit_id')
    if not mat_id:
        await state.clear()
        await message.answer("⚠️ Сессия истекла. Начните сначала.", reply_markup=await get_main_keyboard(message.from_user.id))
        return
    parts = [p.strip() for p in message.text.split('\n\n') if p.strip()]
    
    updates = {}
    if parts and parts[0] != '.':
        updates['title'] = parts[0]
    if len(parts) > 1 and parts[1] != '.':
        updates['link'] = parts[1]
    if len(parts) > 2 and parts[2] != '.':
        updates['description'] = parts[2]
    
    if updates:
        await update_material(mat_id, **updates)
        await message.answer("✅ Обновлено!")
    else:
        await message.answer("❌ Ничего не изменено")
    await materials_menu(message, state)


# --- Удаление материалов ---

async def material_delete_select_stage(message: Message, state: FSMContext):
    await state.set_state(Form.selecting_stage)
    await state.update_data(next_action="select_for_delete")
    await message.answer("🗑️ Выберите раздел:", reply_markup=stage_kb)


async def material_delete_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        mat_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await safe_edit_text(callback, "❌ Некорректные данные")
        return
    mat = await get_material(mat_id)
    if await delete_material(mat_id):
        await safe_edit_text(callback, f"✅ Удалено: {mat['title'] if mat else mat_id}")
    else:
        await safe_edit_text(callback, "❌ Ошибка")
    await state.clear()


# --- Статистика материалов ---

async def material_stats(message: Message, state: FSMContext):
    stats = await get_materials_stats()
    total = sum(stats.values())
    text = f"📊 *Всего материалов: {total}*\n\n" + "\n".join(
        f"{STAGES[st]}: `{cnt}`" for st, cnt in stats.items()
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=materials_menu_kb)


# ==================== СОБЫТИЯ (CRUD) ====================

async def events_menu(message: Message, state: FSMContext):
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    await state.set_state(Form.menu_events)
    await message.answer("📋 *Управление событиями*", parse_mode="Markdown", reply_markup=events_menu_kb)


async def events_show_all(message: Message, state: FSMContext):
    events = await get_events()
    if not events:
        text = "📭 Нет событий"
    else:
        text = "📅 *Все события:*\n\n" + "\n\n".join(format_event(e) for e in events)
    await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=events_menu_kb)


# --- Добавление событий ---

async def event_add_start(message: Message, state: FSMContext):
    await state.set_state(Form.input_type)
    await message.answer("Введите тип (Вебинар, Митап, Квиз):", reply_markup=back_kb)


async def event_add_type(message: Message, state: FSMContext):
    if not message.text:
        return
    if not check_length(message.text, 100):
        await message.answer("❌ Тип события слишком длинный (макс 100 символов)")
        return
    await state.update_data(event_type=message.text)
    await state.set_state(Form.input_datetime)
    await message.answer("Введите дату `2024-12-31 18:00:00`:", parse_mode="Markdown", reply_markup=back_kb)


async def event_add_datetime(message: Message, state: FSMContext):
    if not message.text:
        return
    dt = message.text.strip()
    try:
        if datetime.fromisoformat(dt) <= datetime.now():
            await message.answer("❌ Дата должна быть в будущем!")
            return
    except ValueError:
        await message.answer("❌ Формат: `2024-12-31 18:00:00`", parse_mode="Markdown")
        return
    await state.update_data(event_datetime=dt)
    await state.set_state(Form.input_link)
    await message.answer("Введите ссылку (или 'нет'):", reply_markup=back_kb)


async def event_add_link(message: Message, state: FSMContext):
    if not message.text:
        return
    link = message.text.strip()
    if link.lower() == "нет":
        link = ""
    elif not is_valid_url(link):
        await message.answer("❌ Некорректная ссылка. Используйте формат: https://example.com/page")
        return
    await state.update_data(event_link=link)
    await state.set_state(Form.input_announcement)
    await message.answer("Введите анонс:", reply_markup=back_kb)


async def event_add_announcement(message: Message, state: FSMContext):
    if not message.text:
        return
    ann = message.text.strip()
    if not check_length(ann, 2000):
        await message.answer("❌ Анонс слишком длинный (макс 2000 символов)")
        return
    data = await state.get_data()
    event_type = data.get('event_type')
    event_datetime = data.get('event_datetime')
    event_link = data.get('event_link')
    if not all([event_type, event_datetime]):
        await state.clear()
        await message.answer("⚠️ Сессия истекла. Начните сначала.", reply_markup=await get_main_keyboard(message.from_user.id))
        return
    # Сохраняем анонс в state и переходим к подтверждению
    await state.update_data(event_announcement=ann)
    
    # Если не настроена группа для анонсов - сразу сохраняем
    if not ANNOUNCEMENT_GROUP_ID:
        try:
            await add_event(event_type, event_datetime, event_link, ann)
            await message.answer("✅ Событие добавлено!")
        except Exception as e:
            logging.error(e)
            await message.answer("❌ Ошибка сохранения")
        await events_menu(message, state)
        return
    
    # Спрашиваем про размещение анонса
    await state.set_state(Form.confirm_announce)
    preview = (
        f"📅 *{event_type}*\n"
        f"🕐 {event_datetime}\n"
        f"🔗 {event_link or '—'}\n\n"
        f"{ann[:500]}{'...' if len(ann) > 500 else ''}"
    )
    await message.answer(
        f"{preview}\n\n📢 Разместить анонс в группе?",
        parse_mode="Markdown",
        reply_markup=kb(["✅ Да", "❌ Нет"])
    )


async def event_confirm_announce(message: Message, state: FSMContext):
    """Подтверждение размещения анонса в группе."""
    if not message.text:
        return
    
    bot = message.bot
    
    data = await state.get_data()
    event_type = data.get('event_type')
    event_datetime = data.get('event_datetime')
    event_link = data.get('event_link')
    event_announcement = data.get('event_announcement')
    
    if not all([event_type, event_datetime, event_announcement]):
        await state.clear()
        await message.answer("⚠️ Сессия истекла. Начните сначала.", reply_markup=await get_main_keyboard(message.from_user.id))
        return
    
    # Сохраняем событие в БД
    try:
        await add_event(event_type, event_datetime, event_link, event_announcement)
        await message.answer("✅ Событие добавлено!")
    except Exception as e:
        logging.error(e)
        await message.answer("❌ Ошибка сохранения")
        await events_menu(message, state)
        return
    
    # Если выбрано "Да" - постим в группу
    if message.text == "✅ Да" and ANNOUNCEMENT_GROUP_ID:
        try:
            # Формируем анонс для группы
            group_text = (
                f"📅 *{event_type}*\n"
                f"🕐 {event_datetime}\n"
            )
            if event_link:
                group_text += f"🔗 [Ссылка на событие]({event_link})\n"
            group_text += f"\n{event_announcement}"
            
            # Клавиатура с кнопками (без обработки, для визуала)
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            rsvp_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Иду", callback_data="noop"),
                    InlineKeyboardButton(text="❌ Не иду", callback_data="noop")
                ]
            ])
            
            # Отправляем в группу (с указанием топика если есть)
            kwargs = {
                "chat_id": ANNOUNCEMENT_GROUP_ID,
                "text": group_text,
                "parse_mode": "Markdown",
                "reply_markup": rsvp_kb
            }
            if ANNOUNCEMENT_TOPIC_ID:
                kwargs["message_thread_id"] = ANNOUNCEMENT_TOPIC_ID
            
            await bot.send_message(**kwargs)
            await message.answer("📢 Анонс размещён в группе!")
        except Exception as e:
            logging.error(f"Ошибка отправки в группу: {e}")
            await message.answer("⚠️ Событие сохранено, но не удалось разместить анонс в группе.")
    
    await events_menu(message, state)


# --- Редактирование событий ---

async def event_edit_select(message: Message, state: FSMContext):
    events = await get_events()
    if not events:
        await message.answer("📭 Нет событий", reply_markup=events_menu_kb)
        return
    await state.set_state(Form.selecting_item)
    kb = inline_kb([
        [InlineKeyboardButton(text=f"✏️ {e['id']}. {e['type'][:20]} ({e['datetime'][:10]})", 
                              callback_data=f"edit_ev:{e['id']}")]
        for e in events
    ])
    await message.answer("Выберите событие:", reply_markup=kb)


async def event_edit_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        ev_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.edit_text("❌ Некорректные данные")
        return
    events = await get_events()
    ev = next((e for e in events if e['id'] == ev_id), None)
    if not ev:
        await callback.message.edit_text("❌ Не найдено")
        return
    await state.update_data(edit_id=ev_id, edit_ev=ev)
    await state.set_state(Form.editing_field)
    await callback.message.edit_text(
        f"✏️ Редактирование события *{ev_id}*\n\n"
        f"Отправьте: `тип\n\nдата\n\nссылка\n\nописание`\n\n"
        f"(используйте '.' для пропуска)",
        parse_mode="Markdown"
    )


async def event_edit_process(message: Message, state: FSMContext):
    if not message.text:
        return
    data = await state.get_data()
    ev_id = data.get('edit_id')
    if not ev_id:
        await state.clear()
        await message.answer("⚠️ Сессия истекла. Начните сначала.", reply_markup=await get_main_keyboard(message.from_user.id))
        return
    parts = [p.strip() for p in message.text.split('\n\n') if p.strip()]
    updates = {}
    if parts and parts[0] != '.':
        updates['event_type'] = parts[0]
    if len(parts) > 1 and parts[1] != '.':
        try:
            datetime.fromisoformat(parts[1])
            updates['event_datetime'] = parts[1]
        except ValueError:
            await message.answer("❌ Неверный формат даты")
            return
    if len(parts) > 2 and parts[2] != '.':
        updates['link'] = "" if parts[2].lower() == "нет" else parts[2]
    if len(parts) > 3 and parts[3] != '.':
        updates['announcement'] = parts[3]
    if updates:
        await update_event(ev_id, **updates)
        await message.answer("✅ Обновлено!")
    else:
        await message.answer("❌ Ничего не изменено")
    await events_menu(message, state)


# --- Удаление событий ---

async def event_delete_select(message: Message, state: FSMContext):
    events = await get_events()
    if not events:
        await message.answer("📭 Нет событий", reply_markup=events_menu_kb)
        return
    await state.set_state(Form.selecting_item)
    kb = inline_kb([
        [InlineKeyboardButton(text=f"🗑️ {e['id']}. {e['type'][:20]}", callback_data=f"del_ev:{e['id']}")]
        for e in events
    ])
    await message.answer("Выберите для удаления:", reply_markup=kb)


async def event_delete_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        ev_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await safe_edit_text(callback, "❌ Некорректные данные")
        return
    if await delete_event(ev_id):
        await safe_edit_text(callback, f"✅ Событие {ev_id} удалено")
    else:
        await safe_edit_text(callback, "❌ Ошибка")
    await state.clear()


# ==================== РОЛИ (CRUD с username) ====================

async def roles_menu(message: Message, state: FSMContext):
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    await state.set_state(Form.menu)
    text = (
        "👥 *Управление ролями пользователей*\n\n"
        "📋 *Список* — просмотр всех пользователей\n"
        "➕ *Назначить роль* — добавить/изменить роль\n"
        "   Поддерживается:\n"
        "   • Только ID: `123456789`\n"
        "   • Только @username: `@ivan`\n"
        "   • Оба значения: `123456789 @ivan`\n"
        "🗑️ *Удалить* — удалить пользователя"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=roles_menu_kb)


def build_users_pagination_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Создать клавиатуру пагинации для списка пользователей."""
    buttons = []
    
    # Кнопки навигации
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"users_page:{page-1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"users_page:{page+1}"))
    
    buttons.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def roles_show(message: Message, state: FSMContext):
    """Показать список всех пользователей (с пагинацией)."""
    users = await get_all_users()
    if not users:
        await message.answer("📭 Пользователей нет")
        await roles_menu(message, state)
        return
    
    # Группируем по ролям
    by_role = {r: [] for r in ROLES}
    for u in users:
        by_role[u['role']].append(u)
    
    # Плоский список всех пользователей для пагинации
    all_users_flat = []
    for role in ROLES:
        for u in by_role[role]:
            all_users_flat.append(u)
    
    total_users = len(all_users_flat)
    total_pages = (total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE
    
    # Показываем первую страницу
    await _show_users_page(message, all_users_flat, 0, total_pages, total_users, is_callback=False)
    await roles_menu(message, state)


async def _show_users_page(message_or_callback, users: list, page: int, total_pages: int, total_users: int, is_callback: bool = False):
    """Отобразить страницу со списком пользователей."""
    start_idx = page * USERS_PER_PAGE
    end_idx = min(start_idx + USERS_PER_PAGE, len(users))
    page_users = users[start_idx:end_idx]
    
    # Группируем по ролям для отображения
    by_role = {r: [] for r in ROLES}
    for u in page_users:
        by_role[u['role']].append(u)
    
    lines = [f"👥 *Всего пользователей: {total_users}* (стр. {page+1}/{total_pages})\n"]
    
    for role in ROLES:
        emoji = get_role_emoji(role)
        role_users = by_role[role]
        lines.append(f"\n{emoji} *{role.capitalize()} ({len(role_users)} на этой стр.):*")
        if role_users:
            for u in role_users:
                lines.append(f"  {format_user(u)}")
        else:
            lines.append("  _нет_")
    
    keyboard = build_users_pagination_keyboard(page, total_pages)
    text = "\n".join(lines)
    
    if is_callback:
        # Редактируем существующее сообщение
        try:
            await message_or_callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            # Если текст не изменился, пропускаем
            pass
        await message_or_callback.answer()
    else:
        await message_or_callback.answer(text, parse_mode="Markdown", reply_markup=keyboard)


async def users_page_callback(callback: CallbackQuery):
    """Callback для переключения страниц списка пользователей."""
    page = int(callback.data.split(":")[1])
    
    # Получаем всех пользователей и считаем страницы
    users = await get_all_users()
    
    # Группируем по ролям для того же порядка
    by_role = {r: [] for r in ROLES}
    for u in users:
        by_role[u['role']].append(u)
    
    all_users_flat = []
    for role in ROLES:
        for u in by_role[role]:
            all_users_flat.append(u)
    
    total_users = len(all_users_flat)
    total_pages = (total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE
    
    await _show_users_page(callback, all_users_flat, page, total_pages, total_users, is_callback=True)


# --- Назначение роли ---

async def role_add_start(message: Message, state: FSMContext):
    """Начало добавления/изменения роли."""
    await state.set_state(Form.input_users)
    text = (
        "Введите пользователей для назначения роли:\n\n"
        "*Форматы:*\n"
        "• `123456789` — только ID\n"
        "• `@ivan_petrov` — только username\n"
        "• `123456789 @ivan_petrov` — оба значения\n"
        "• Несколько: `@ivan, @petr, 123456789`\n\n"
        "Бот свяжет ID и username если они указаны вместе."
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=back_kb)


async def role_receive_users(message: Message, state: FSMContext):
    """Обработка ввода пользователей."""
    if not message.text:
        return
    users, errors = parse_users_input(message.text)
    
    if not users:
        await message.answer("❌ Не найдено корректных данных. Попробуйте снова:", reply_markup=back_kb)
        return
    
    if errors:
        await message.answer(f"⚠️ Пропущены некорректные данные: {', '.join(errors[:5])}")
    
    # Показываем что распарсили
    preview = []
    for i, u in enumerate(users[:5], 1):
        parts = []
        if u.get("user_id"):
            parts.append(f"ID:{u['user_id']}")
        if u.get("username"):
            parts.append(f"@{u['username']}")
        preview.append(f"{i}. {' + '.join(parts)}")
    
    if len(users) > 5:
        preview.append(f"... и ещё {len(users) - 5}")
    
    await state.update_data(users_to_assign=users)
    await state.set_state(Form.selecting_role)
    
    await message.answer(
        f"Найдено *{len(users)}* пользователей:\n" + "\n".join(preview) + "\n\nВыберите роль:",
        parse_mode="Markdown",
        reply_markup=role_kb("set_role")
    )


async def role_set_callback(callback: CallbackQuery, state: FSMContext):
    """Callback установки роли."""
    await callback.answer()
    role = callback.data.split(":")[1]
    data = await state.get_data()
    users = data.get("users_to_assign", [])
    
    if not users:
        await callback.message.edit_text("❌ Ошибка: список пуст")
        await state.clear()
        return
    
    # Назначаем роли
    await set_users_batch(users, role)
    
    await callback.message.edit_text(
        f"✅ Роль `{role}` назначена для *{len(users)}* пользователей!"
    )
    await state.clear()


# --- Удаление пользователя ---

async def role_delete_start(message: Message, state: FSMContext):
    """Начало удаления пользователя."""
    users = await get_all_users()
    if not users:
        await message.answer("📭 Нет пользователей", reply_markup=roles_menu_kb)
        return
    
    await state.set_state(Form.selecting_user_to_delete)
    
    # Группируем по ролям для удобства
    keyboard = []
    by_role = {r: [] for r in ROLES}
    for u in users:
        by_role[u['role']].append(u)
    
    for role in ROLES:
        if by_role[role]:
            keyboard.append([InlineKeyboardButton(text=f"—— {role.upper()} ——", callback_data="noop")])
            for u in by_role[role][:10]:  # Ограничиваем для компактности
                user_text = format_user(u)
                # Создаем callback с user_id или username (НЕ может быть None)
                if u.get('user_id'):
                    callback_data = f"del_user:id:{u['user_id']}"
                elif u.get('username'):
                    callback_data = f"del_user:un:{u['username']}"
                else:
                    continue  # Пропускаем пользователей без ID и username
                keyboard.append([InlineKeyboardButton(text=user_text, callback_data=callback_data)])
    
    await message.answer("🗑️ Выберите пользователя для удаления:", reply_markup=inline_kb(keyboard))


async def role_delete_callback(callback: CallbackQuery, state: FSMContext):
    """Callback удаления пользователя."""
    await callback.answer()
    
    if callback.data == "noop":
        return
    
    # Формат: del_user:id:123456789 или del_user:un:username
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.message.edit_text("❌ Некорректные данные")
        return
    
    key_type = parts[1]
    key_value = parts[2]
    
    if key_type == "id":
        success = await delete_user(user_id=int(key_value))
    else:  # key_type == "un"
        success = await delete_user(username=key_value)
    
    if success:
        await callback.message.edit_text(f"✅ Пользователь удалён")
    else:
        await callback.message.edit_text("❌ Не удалось удалить")
    
    await state.clear()


# ==================== ПУБЛИЧНЫЕ ОБРАБОТЧИКИ ====================

async def public_materials_select(message: Message, state: FSMContext):
    # Проверяем rate limit
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await state.set_state(Form.selecting_stage)
    await state.update_data(next_action="public_show")
    await message.answer(
        "📚 *Материалы*\n\n"
        "Выберите нужный раздел в меню ниже:\n"
        "• 📚 Фундаментальная теория\n"
        "• 🔧 Практическая теория\n"
        "• 📝 Практические задания\n"
        "• 🗺️ Roadmap (info)",
        parse_mode="Markdown",
        reply_markup=stage_kb
    )


async def public_events_show(message: Message):
    # Проверяем rate limit
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    events = await get_events(upcoming_only=True)
    if not events:
        await message.answer("📭 Нет предстоящих событий")
        return
    text = "📅 *Предстоящие события:*\n\n" + "\n\n".join(
        f"*{e['type']}* ({e['datetime'][:10]})\n{e['announcement'][:100]}..."
        for e in events
    )
    await message.answer(text, parse_mode="Markdown")


async def buddy_handler(message: Message):
    """Обработчик раздела Buddy."""
    # Проверяем rate limit
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await message.answer(
        "🤝 *Buddy*\n\n"
        "тут будет анонс системы бадди",
        parse_mode="Markdown"
    )


# ==================== УПРАВЛЕНИЕ БАНАМИ ====================

async def bans_menu(message: Message, state: FSMContext):
    """Меню управления банами."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    bans = await get_active_bans()
    if not bans:
        await message.answer("✅ Активных банов нет.")
        return

    lines = [f"🚫 *Активные баны ({len(bans)}):*\n"]
    for b in bans:
        who = f"@{b['username']}" if b['username'] else f"ID:{b['user_id']}"
        until = b['banned_until'][:16]
        lines.append(f"• {who} | уровень {b['ban_level']} | до {until}")

    kb_inline = inline_kb([
        [InlineKeyboardButton(
            text=f"🔓 {('@' + b['username']) if b['username'] else ('ID:' + str(b['user_id']))}",
            callback_data=f"unban:{b['id']}"
        )]
        for b in bans
    ])
    await message.answer("\n".join(lines), parse_mode="Markdown", reply_markup=kb_inline)


async def ban_unban_callback(callback: CallbackQuery, state: FSMContext):
    """Callback снятия бана по ID записи бана."""
    await callback.answer()
    ok, wait = check_rate_limit(callback.from_user.id)
    if not ok:
        await callback.message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    try:
        ban_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.edit_text("❌ Некорректные данные")
        return

    # Находим бан по id и снимаем
    row = await _db.fetchone("SELECT user_id, username FROM bans WHERE id = ?", (ban_id,))
    if not row:
        await callback.message.edit_text("❌ Бан не найден (уже истёк?)")
        return
    await unban_user(user_id=row[0], username=row[1])
    who = f"@{row[1]}" if row[1] else f"ID:{row[0]}"
    await callback.message.edit_text(f"✅ Бан снят: {who}")


# ==================== ПОИСК МАТЕРИАЛОВ ====================

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

    lines = [f"🔍 *Результаты по запросу \"{query}\" ({len(results)}):*\n"]
    for m in results[:20]:  # Ограничиваем вывод
        stage_name = STAGES.get(m['stage'], m['stage'])
        lines.append(f"• [{m['title']}]({m['link']}) _({stage_name})_")
    if len(results) > 20:
        lines.append(f"\n_...и ещё {len(results) - 20} результатов_")

    await message.answer("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


async def get_main_keyboard(user_id: int):
    """Получить правильную клавиатуру для пользователя по его роли."""
    role = await get_user_role(user_id=user_id)
    if role == ROLE_ADMIN:
        return admin_kb
    elif role == ROLE_MENTOR:
        return mentor_kb
    else:
        return user_kb


async def fallback_handler(message: Message, state: FSMContext):
    """Fallback handler - ловит все неизвестные сообщения от авторизованных пользователей."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        return  # Тихо игнорируем если rate limit
    
    # Сбрасываем состояние
    await state.clear()
    
    # Отправляем подсказку
    await message.answer(
        "❓ Не понял команду. Используйте кнопки меню или /start",
        reply_markup=await get_main_keyboard(message.from_user.id)
    )


async def error_handler(event, exception):
    """Global error handler to prevent bot from crashing."""
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


# ==================== КОМАНДЫ ДЛЯ ГРУПП ====================

async def group_help_handler(message: Message):
    """Справка по командам в группе (/sabot_help)."""
    # Проверяем rate limit
    if message.chat.type == "private":
        return  # Только для групп
    
    ok, muted = check_group_rate_limit(message.chat.id, "help")
    if muted:
        return  # В муте - игнорируем
    if not ok:
        await message.reply("⏱️ Слишком быстро! Подождите минуту.")
        return
    
    help_text = (
        "🤖 *Команды SABot в группе:*\n\n"
        "`/sabot_help` - эта справка\n"
        "`/events` - предстоящие события\n"
        "`/material <название>` - найти материал\n\n"
        "_Для управления используйте бота в ЛС_"
    )
    await message.reply(help_text, parse_mode="Markdown")


async def group_events_handler(message: Message):
    """Показать события в группе (/events)."""
    if message.chat.type == "private":
        return
    
    ok, muted = check_group_rate_limit(message.chat.id, "events")
    if muted:
        return
    if not ok:
        await message.reply("⏱️ Слишком быстро! Подождите минуту.")
        return
    
    events = await get_events()
    if not events:
        await message.reply("📭 Предстоящих событий нет.")
        return
    
    lines = ["📅 *Предстоящие события:*\n"]
    for e in events[:10]:  # Максимум 10
        lines.append(f"\n*{e['event_type']}* | {e['event_datetime']}")
        if e.get('link'):
            lines.append(f"🔗 [Ссылка]({e['link']})")
    
    await message.reply("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


async def group_material_handler(message: Message):
    """Поиск материала в группе (/material <название>)."""
    if message.chat.type == "private":
        return
    
    ok, muted = check_group_rate_limit(message.chat.id, "material")
    if muted:
        return
    if not ok:
        await message.reply("⏱️ Слишком быстро! Подождите минуту.")
        return
    
    # Извлекаем запрос
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
    results = await search_materials(query)
    
    if not results:
        await message.reply(f"🔍 По запросу *{query}* ничего не найдено.", parse_mode="Markdown")
        return
    
    # Выводим компактно - только название и ссылка
    lines = [f'📚 *Результаты по "{query}":*\n']
    for m in results[:5]:  # Максимум 5
        lines.append(f"• [{m['title']}]({m['link']})")
    
    await message.reply("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


# ==================== РЕГИСТРАЦИЯ ====================

def register_handlers(dp):
    """Регистрация всех обработчиков."""
    
    # Главное меню
    dp.message.register(admin_handler, F.text.in_(["⚙️ Админка", "🔙 Назад"]), IsAuthorizedUser())
    
    # Публичные
    dp.message.register(public_materials_select, F.text.in_(["📚 Материалы", "Материалы"]), IsAuthorizedUser())
    dp.message.register(handle_stage_selection, Form.selecting_stage, IsAuthorizedUser())
    dp.message.register(public_events_show, F.text.in_(["📅 События комьюнити", "События комьюнити"]), IsAuthorizedUser())
    dp.message.register(buddy_handler, F.text.in_(["🤝 Buddy", "Buddy"]), IsAuthorizedUser())
    
    # Материалы
    dp.message.register(materials_menu, F.text == "📦 Управление материалами", HasRole(ROLE_ADMIN))
    dp.message.register(material_select_stage, F.text == "📖 Просмотреть", HasRole(ROLE_ADMIN))
    dp.message.register(material_add_start, F.text == "➕ Добавить", HasRole(ROLE_ADMIN))
    dp.message.register(material_add_title, Form.input_title, HasRole(ROLE_ADMIN))
    dp.message.register(material_add_link, Form.input_link, HasRole(ROLE_ADMIN))
    dp.message.register(material_add_desc, Form.input_desc, HasRole(ROLE_ADMIN))
    dp.message.register(material_edit_select_stage, F.text == "✏️ Редактировать", HasRole(ROLE_ADMIN))
    dp.callback_query.register(material_edit_callback, F.data.startswith("edit_mat:"), HasRole(ROLE_ADMIN))
    dp.message.register(material_edit_process, Form.editing_field, HasRole(ROLE_ADMIN))
    dp.message.register(material_delete_select_stage, F.text == "🗑️ Удалить", HasRole(ROLE_ADMIN))
    dp.callback_query.register(material_delete_callback, F.data.startswith("del_mat:"), HasRole(ROLE_ADMIN))
    dp.message.register(material_stats, F.text == "📊 Статистика", HasRole(ROLE_ADMIN))
    
    # События (используем Form.menu_events для разделения с материалами)
    dp.message.register(events_menu, F.text == "📋 Управление событиями", HasRole(ROLE_ADMIN))
    dp.message.register(events_show_all, F.text == "📖 Просмотреть", Form.menu_events, HasRole(ROLE_ADMIN))
    dp.message.register(event_add_start, F.text == "➕ Добавить", Form.menu_events, HasRole(ROLE_ADMIN))
    dp.message.register(event_add_type, Form.input_type, HasRole(ROLE_ADMIN))
    dp.message.register(event_add_datetime, Form.input_datetime, HasRole(ROLE_ADMIN))
    dp.message.register(event_add_link, Form.input_link, HasRole(ROLE_ADMIN))
    dp.message.register(event_add_announcement, Form.input_announcement, HasRole(ROLE_ADMIN))
    dp.message.register(event_confirm_announce, Form.confirm_announce, HasRole(ROLE_ADMIN))
    dp.message.register(event_edit_select, F.text == "✏️ Редактировать", Form.menu_events, HasRole(ROLE_ADMIN))
    dp.callback_query.register(event_edit_callback, F.data.startswith("edit_ev:"), HasRole(ROLE_ADMIN))
    dp.message.register(event_edit_process, Form.editing_field, HasRole(ROLE_ADMIN))
    dp.message.register(event_delete_select, F.text == "🗑️ Удалить", Form.menu_events, HasRole(ROLE_ADMIN))
    dp.callback_query.register(event_delete_callback, F.data.startswith("del_ev:"), HasRole(ROLE_ADMIN))
    
    # Роли (новая система)
    dp.message.register(roles_menu, F.text == "👥 Управление ролями", HasRole(ROLE_ADMIN))
    dp.message.register(roles_show, F.text == "📋 Список пользователей", HasRole(ROLE_ADMIN))
    dp.callback_query.register(users_page_callback, F.data.startswith("users_page:"), HasRole(ROLE_ADMIN))
    dp.message.register(role_add_start, F.text == "➕ Назначить роль", HasRole(ROLE_ADMIN))
    dp.message.register(role_receive_users, Form.input_users, HasRole(ROLE_ADMIN))
    dp.callback_query.register(role_set_callback, F.data.startswith("set_role:"), HasRole(ROLE_ADMIN))
    dp.message.register(role_delete_start, F.text == "🗑️ Удалить пользователя", HasRole(ROLE_ADMIN))
    dp.callback_query.register(role_delete_callback, F.data.startswith("del_user:"), HasRole(ROLE_ADMIN))

    # Баны
    dp.message.register(bans_menu, F.text == "🚫 Управление банами", HasRole(ROLE_ADMIN))
    dp.callback_query.register(ban_unban_callback, F.data.startswith("unban:"), HasRole(ROLE_ADMIN))
    
    # Команды для групп (без проверки ролей, но с rate limit)
    dp.message.register(group_help_handler, Command("sabot_help"))
    dp.message.register(group_events_handler, Command("events"))
    dp.message.register(group_material_handler, Command("material"))
    
    # Fallback handler - ловит все неизвестные сообщения от авторизованных пользователей
    dp.message.register(fallback_handler, IsAuthorizedUser())
    
    # Global error handler
    dp.errors.register(error_handler)
