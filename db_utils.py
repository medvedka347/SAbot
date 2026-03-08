import aiosqlite
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram.types import Message
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from config import DB_NAME, STAGES, ROLE_ADMIN


class Database:
    """Асинхронный класс для работы с SQLite с WAL режимом."""
    
    def __init__(self, db_path: str = DB_NAME):
        self.db_path = db_path
        self._lock = asyncio.Lock()  # Для сериализации записей
    
    async def _init_connection(self, db):
        """Включаем WAL режим для конкурентного доступа."""
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")  # Баланс скорость/надёжность
        await db.execute("PRAGMA cache_size=10000")  # Увеличиваем кэш
        await db.execute("PRAGMA temp_store=MEMORY")
    
    async def execute(self, query: str, params: tuple = ()) -> int:
        """Выполнить запрос с блокировкой для записей."""
        async with self._lock:  # Сериализуем записи
            async with aiosqlite.connect(self.db_path) as db:
                await self._init_connection(db)
                cursor = await db.execute(query, params)
                await db.commit()
                return cursor.rowcount
    
    async def fetchone(self, query: str, params: tuple = ()):
        """Получить одну запись (чтение параллельное)."""
        async with aiosqlite.connect(self.db_path) as db:
            await self._init_connection(db)
            cursor = await db.execute(query, params)
            return await cursor.fetchone()
    
    async def fetchall(self, query: str, params: tuple = ()) -> list:
        """Получить все записи (чтение параллельное)."""
        async with aiosqlite.connect(self.db_path) as db:
            await self._init_connection(db)
            cursor = await db.execute(query, params)
            return await cursor.fetchall()
    
    async def init_tables(self):
        """Инициализация таблиц."""
        async with aiosqlite.connect(self.db_path) as db:
            await self._migrate_user_roles(db)
            
            # Materials
            await db.execute("""
                CREATE TABLE IF NOT EXISTS materials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stage TEXT NOT NULL CHECK (stage IN ('fundamental', 'practical_theory', 'practical_tasks', 'roadmap')),
                    title TEXT NOT NULL,
                    link TEXT NOT NULL,
                    description TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Events
            await db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    event_datetime TEXT NOT NULL,
                    link TEXT,
                    announcement TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Неудачные попытки авторизации
            await db.execute("""
                CREATE TABLE IF NOT EXISTS failed_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id BIGINT UNIQUE,
                    username TEXT UNIQUE,
                    attempt_count INTEGER DEFAULT 0,
                    last_attempt TEXT DEFAULT CURRENT_TIMESTAMP,
                    CHECK (user_id IS NOT NULL OR username IS NOT NULL)
                )
            """)
            
            # Баны (mute)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS bans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id BIGINT,
                    username TEXT,
                    ban_level INTEGER DEFAULT 1 CHECK (ban_level IN (1, 2, 3)),
                    banned_until TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    CHECK (user_id IS NOT NULL OR username IS NOT NULL)
                )
            """)
            
            # Buddy - система наставничества
            await db.execute("""
                CREATE TABLE IF NOT EXISTS buddy_mentorships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mentor_id INTEGER NOT NULL,
                    mentee_id INTEGER,
                    mentee_full_name TEXT NOT NULL,
                    mentee_telegram_tag TEXT,
                    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'paused', 'dropped')),
                    assigned_date TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (mentor_id) REFERENCES user_roles(id) ON DELETE CASCADE,
                    FOREIGN KEY (mentee_id) REFERENCES user_roles(id) ON DELETE SET NULL
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_buddy_mentor ON buddy_mentorships(mentor_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_buddy_mentee ON buddy_mentorships(mentee_id)")
            
            # Создаем индексы для быстрого поиска
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bans_active ON bans(user_id, username, banned_until)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_failed_user ON failed_attempts(user_id, username)")
            
            await self._migrate_materials(db)
            
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bans_user ON bans(user_id, username)")
            
            await db.commit()
        
        logging.info(f"База данных '{self.db_path}' готова")
    
    async def _migrate_user_roles(self, db: aiosqlite.Connection):
        """Миграция: создание таблицы с правильными UNIQUE ограничениями."""
        # Проверяем, существует ли таблица
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_roles'")
        if not await cursor.fetchone():
            # Таблицы нет - создаем новую с правильной схемой
            await db.execute("""
                CREATE TABLE user_roles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id BIGINT UNIQUE,
                    username TEXT UNIQUE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'mentor', 'admin', 'lion')),
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    CHECK (user_id IS NOT NULL OR username IS NOT NULL)
                )
            """)
            logging.info("Создана таблица user_roles")
            return
        
        # Проверяем текущую схему таблицы
        cursor = await db.execute("PRAGMA table_info(user_roles)")
        columns = {row[1]: row for row in await cursor.fetchall()}
        
        # Если есть колонка 'id' - значит новая схема
        if 'id' in columns:
            # Проверяем CHECK ограничение на 'lion'
            cursor = await db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='user_roles'"
            )
            row = await cursor.fetchone()
            if row and 'lion' not in row[0]:
                logging.warning("Миграция: добавление роли 'lion' в CHECK ограничение")
                # Пересоздаем таблицу с новым CHECK
                await self._migrate_role_check(db)
            return
        
        # Таблица без колонки 'id' - нужна миграция на новую схему
        logging.warning("Миграция: исправление структуры user_roles")
        
        await db.execute("DROP TABLE IF EXISTS user_roles_new")
        await db.execute("""
            CREATE TABLE user_roles_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id BIGINT UNIQUE,
                username TEXT UNIQUE,
                role TEXT NOT NULL CHECK (role IN ('user', 'mentor', 'admin', 'lion')),
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                CHECK (user_id IS NOT NULL OR username IS NOT NULL)
            )
        """)
        
        # Переносим данные
        if 'created_at' in columns:
            await db.execute("""
                INSERT INTO user_roles_new (user_id, username, role, created_at)
                SELECT user_id, username, role, created_at FROM user_roles
            """)
        else:
            await db.execute("""
                INSERT INTO user_roles_new (user_id, username, role, created_at)
                SELECT user_id, username, role, CURRENT_TIMESTAMP FROM user_roles
            """)
        
        await db.execute("DROP TABLE user_roles")
        await db.execute("ALTER TABLE user_roles_new RENAME TO user_roles")
        logging.info("Миграция user_roles выполнена")
    
    async def _migrate_role_check(self, db: aiosqlite.Connection):
        """Миграция: добавление 'lion' в CHECK ограничение роли."""
        await db.execute("DROP TABLE IF EXISTS user_roles_new")
        await db.execute("""
            CREATE TABLE user_roles_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id BIGINT UNIQUE,
                username TEXT UNIQUE,
                role TEXT NOT NULL CHECK (role IN ('user', 'mentor', 'admin', 'lion')),
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                CHECK (user_id IS NOT NULL OR username IS NOT NULL)
            )
        """)
        await db.execute("""
            INSERT INTO user_roles_new (id, user_id, username, role, created_at)
            SELECT id, user_id, username, role, created_at FROM user_roles
        """)
        await db.execute("DROP TABLE user_roles")
        await db.execute("ALTER TABLE user_roles_new RENAME TO user_roles")
        logging.info("Миграция CHECK ограничения выполнена")
    
    async def _migrate_materials(self, db: aiosqlite.Connection):
        """Миграция: добавление поля stage."""
        try:
            await db.execute("SELECT stage FROM materials LIMIT 1")
        except aiosqlite.OperationalError:
            logging.warning("Миграция: добавление поля stage")
            await db.execute("ALTER TABLE materials RENAME TO materials_old")
            await db.executescript("""
                CREATE TABLE materials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stage TEXT NOT NULL CHECK (stage IN ('fundamental', 'practical_theory', 'practical_tasks', 'roadmap')),
                    title TEXT NOT NULL,
                    link TEXT NOT NULL,
                    description TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO materials (id, stage, title, link, description, created_at)
                SELECT id, 'fundamental', title, link, description, created_at FROM materials_old;
                DROP TABLE materials_old;
            """)
            logging.info("Миграция materials выполнена")


# Глобальный экземпляр БД
db = Database()


# ==================== УТИЛИТЫ ====================

async def init_db(db_path: str = DB_NAME):
    """Инициализация (для совместимости)."""
    await Database(db_path).init_tables()


# ==================== USER ROLES (с username) ====================

def normalize_username(username: str | None) -> str | None:
    """Нормализовать username (убрать @, привести к lowercase)."""
    if not username:
        return None
    username = username.strip().lower()
    if username.startswith('@'):
        username = username[1:]
    return username if username else None


async def get_user_by_id(user_id: int) -> dict | None:
    """Найти пользователя по ID."""
    row = await db.fetchone(
        "SELECT user_id, username, role FROM user_roles WHERE user_id = ?",
        (user_id,)
    )
    if row:
        return {"user_id": row[0], "username": row[1], "role": row[2]}
    return None


async def get_user_by_username(username: str) -> dict | None:
    """Найти пользователя по username."""
    username = normalize_username(username)
    if not username:
        return None
    row = await db.fetchone(
        "SELECT user_id, username, role FROM user_roles WHERE username = ?",
        (username,)
    )
    if row:
        return {"user_id": row[0], "username": row[1], "role": row[2]}
    return None


async def get_user_role(user_id: int = None, username: str = None) -> str | None:
    """Получить роль по ID или username (для обратной совместимости).
    Для мультиролей используйте get_user_roles()."""
    roles = await get_user_roles(user_id, username)
    return roles[0] if roles else None


async def get_user_roles(user_id: int = None, username: str = None) -> list[str]:
    """Получить список ролей по ID или username (поддержка мультиролей).
    Роли хранятся как 'admin,lion,mentor' и возвращаются как ['admin', 'lion', 'mentor']."""
    username = normalize_username(username)
    
    if user_id and username:
        row = await db.fetchone(
            "SELECT role FROM user_roles WHERE user_id = ? OR username = ? LIMIT 1",
            (user_id, username)
        )
    elif user_id:
        row = await db.fetchone(
            "SELECT role FROM user_roles WHERE user_id = ? LIMIT 1",
            (user_id,)
        )
    elif username:
        row = await db.fetchone(
            "SELECT role FROM user_roles WHERE username = ? LIMIT 1",
            (username,)
        )
    else:
        return []
    
    if not row or not row[0]:
        return []
    
    # Разбиваем роли по запятой и чистим
    roles = [r.strip() for r in row[0].split(',') if r.strip()]
    return roles


async def has_role(user_id: int, role: str) -> bool:
    """Проверить, имеет ли пользователь конкретную роль."""
    roles = await get_user_roles(user_id=user_id)
    return role in roles


async def add_user_role(user_id: int, new_role: str) -> bool:
    """Добавить роль пользователю (для мультиролей)."""
    roles = await get_user_roles(user_id=user_id)
    if new_role in roles:
        return True  # Уже есть
    
    roles.append(new_role)
    roles_str = ','.join(roles)
    
    return await db.execute(
        "UPDATE user_roles SET role = ? WHERE user_id = ?",
        (roles_str, user_id)
    ) > 0


async def remove_user_role(user_id: int, role_to_remove: str) -> bool:
    """Удалить роль у пользователя."""
    roles = await get_user_roles(user_id=user_id)
    if role_to_remove not in roles:
        return True  # Не было такой роли
    
    roles.remove(role_to_remove)
    roles_str = ','.join(roles) if roles else 'user'  # Если ролей не осталось - даем user
    
    return await db.execute(
        "UPDATE user_roles SET role = ? WHERE user_id = ?",
        (roles_str, user_id)
    ) > 0


def validate_user_id(user_id: any) -> int | None:
    """Валидация ID пользователя."""
    if not user_id:
        return None
    try:
        uid = int(user_id)
        if 0 < uid < 10_000_000_000:
            return uid
    except (ValueError, TypeError):
        pass
    return None


async def get_all_users() -> list[dict]:
    """Получить всех пользователей."""
    rows = await db.fetchall(
        "SELECT user_id, username, role FROM user_roles ORDER BY role, COALESCE(username, ''), user_id"
    )
    return [{"user_id": r[0], "username": r[1], "role": r[2]} for r in rows]


async def add_or_update_user(user_id: int = None, username: str = None, role: str = None) -> bool:
    """
    Добавить или обновить пользователя.
    Можно указать только user_id, только username, или оба.
    Если пользователь с таким user_id или username уже есть - обновит роль.
    При совпадении по ID или username - объединяет данные.
    """
    if not user_id and not username:
        raise ValueError("Нужно указать user_id или username")
    
    username = normalize_username(username)
    
    # Ищем существующую запись по ID или username
    existing_by_id = await get_user_by_id(user_id) if user_id else None
    existing_by_username = await get_user_by_username(username) if username else None
    
    # Определяем итоговые значения
    final_user_id = user_id
    final_username = username
    
    if existing_by_id and not final_username:
        final_username = existing_by_id.get("username")
    if existing_by_username and not final_user_id:
        final_user_id = existing_by_username.get("user_id")
    
    # Удаляем старые записи
    ids_to_delete = set()
    if existing_by_id and existing_by_id.get("user_id"):
        ids_to_delete.add(existing_by_id["user_id"])
    if existing_by_username and existing_by_username.get("user_id"):
        ids_to_delete.add(existing_by_username["user_id"])
    
    for uid in ids_to_delete:
        if uid is not None:
            await db.execute("DELETE FROM user_roles WHERE user_id = ?", (uid,))
    
    # Удаляем по username
    if existing_by_username and existing_by_username.get("username"):
        await db.execute("DELETE FROM user_roles WHERE username = ?", (existing_by_username["username"],))
    if existing_by_id and existing_by_id.get("username") and existing_by_id.get("username") != final_username:
        await db.execute("DELETE FROM user_roles WHERE username = ?", (existing_by_id["username"],))
    
    # Вставляем новую запись
    try:
        await db.execute(
            """
            INSERT INTO user_roles (user_id, username, role) 
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET 
                username = excluded.username,
                role = excluded.role
            """,
            (final_user_id, final_username, role)
        )
    except aiosqlite.IntegrityError:
        if final_username:
            await db.execute(
                "UPDATE user_roles SET user_id = ?, role = ? WHERE username = ?",
                (final_user_id, role, final_username)
            )
    
    action = "Обновлен" if (existing_by_id or existing_by_username) else "Добавлен"
    logging.info(f"{action} пользователь: id={final_user_id}, @{final_username} -> {role}")
    return True


async def set_users_batch(users: list[dict], role: str):
    """
    Массовое назначение роли.
    users: список словарей [{'user_id': 123, 'username': '@name'}, ...]
    """
    for user in users:
        await add_or_update_user(
            user_id=user.get("user_id"),
            username=user.get("username"),
            role=role
        )
    logging.info(f"Роли {len(users)} пользователей -> {role}")


async def update_user_id_by_username(username: str, user_id: int) -> bool:
    """Обновить user_id для пользователя, добавленного по username."""
    username = normalize_username(username)
    if not username or not user_id:
        return False
    
    # Проверяем, существует ли пользователь с таким username и пустым user_id
    existing = await get_user_by_username(username)
    if existing and existing.get("user_id") is None:
        await db.execute(
            "UPDATE user_roles SET user_id = ? WHERE username = ?",
            (user_id, username)
        )
        logging.info(f"Обновлен user_id для @{username}: {user_id}")
        return True
    
    # Если user_id уже совпадает - тоже ок
    if existing and existing.get("user_id") == user_id:
        return True
    
    return False


async def delete_user(user_id: int = None, username: str = None) -> bool:
    """Удалить пользователя по ID или username."""
    if user_id is not None:
        deleted = await db.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
        if deleted > 0:
            return True
    if username:
        username = normalize_username(username)
        deleted = await db.execute("DELETE FROM user_roles WHERE username = ?", (username,))
        if deleted > 0:
            return True
    return False


async def setup_initial_users(db_path: str = DB_NAME, initial_admin_id: int = None):
    """Начальная настройка."""
    from config import ROLE_ADMIN
    import os
    
    await cleanup_expired_bans()
    
    count_row = await db.fetchone("SELECT COUNT(*) FROM user_roles")
    count = count_row[0] if count_row else 0
    
    if count == 0:
        admin_id_from_env = os.getenv("INITIAL_ADMIN_ID", "").strip()
        admin_id = initial_admin_id
        
        if admin_id_from_env and admin_id_from_env.isdigit():
            admin_id = int(admin_id_from_env)
        
        if admin_id:
            await add_or_update_user(user_id=admin_id, role=ROLE_ADMIN)
            logging.warning(f"Добавлен начальный админ (ID: {admin_id})")
        else:
            logging.warning(
                "База данных пуста! Начальный админ не задан.\n"
                "Установите переменную INITIAL_ADMIN_ID в .env файле и перезапустите бота.\n"
                "Пример: INITIAL_ADMIN_ID=123456789"
            )


# ==================== RATE LIMITING & BANS ====================

async def cleanup_expired_bans():
    """Удалить истекшие баны (можно вызывать периодически)."""
    deleted = await db.execute("DELETE FROM bans WHERE banned_until <= datetime('now')")
    if deleted > 0:
        logging.info(f"Очищено {deleted} истекших банов")


async def get_ban_status(user_id: int = None, username: str = None) -> dict | None:
    """
    Проверить статус бана пользователя.
    Returns: {'ban_level': int, 'banned_until': datetime} или None если не забанен
    """
    username = normalize_username(username)
    
    if user_id and username:
        query = """
            SELECT ban_level, banned_until FROM bans 
            WHERE (user_id = ? OR username = ?) 
            AND banned_until > datetime('now')
            ORDER BY banned_until DESC LIMIT 1
        """
        params = (user_id, username)
    elif user_id:
        query = """
            SELECT ban_level, banned_until FROM bans 
            WHERE user_id = ? AND banned_until > datetime('now')
            ORDER BY banned_until DESC LIMIT 1
        """
        params = (user_id,)
    elif username:
        query = """
            SELECT ban_level, banned_until FROM bans 
            WHERE username = ? AND banned_until > datetime('now')
            ORDER BY banned_until DESC LIMIT 1
        """
        params = (username,)
    else:
        return None
    
    row = await db.fetchone(query, params)
    if row:
        return {
            "ban_level": row[0],
            "banned_until": datetime.fromisoformat(row[1])
        }
    return None


async def record_failed_attempt(user_id: int = None, username: str = None) -> dict | None:
    """
    Записать неудачную попытку авторизации.
    Returns: информация о бане если применен, иначе None
    """
    username = normalize_username(username)
    
    existing = await db.fetchone(
        "SELECT attempt_count FROM failed_attempts WHERE user_id = ? OR username = ?",
        (user_id, username)
    )
    
    if existing:
        new_count = existing[0] + 1
        await db.execute(
            "UPDATE failed_attempts SET attempt_count = ?, last_attempt = datetime('now') WHERE user_id = ? OR username = ?",
            (new_count, user_id, username)
        )
    else:
        new_count = 1
        await db.execute(
            "INSERT INTO failed_attempts (user_id, username, attempt_count) VALUES (?, ?, ?)",
            (user_id, username, new_count)
        )
    
    if new_count >= 3:
        return await apply_ban(user_id, username)
    
    return None


async def apply_ban(user_id: int = None, username: str = None) -> dict:
    """
    Применить бан на основе предыдущих банов.
    Возвращает информацию о примененном бане.
    """
    username = normalize_username(username)
    
    existing_ban = await db.fetchone(
        "SELECT ban_level FROM bans WHERE user_id = ? OR username = ? ORDER BY created_at DESC LIMIT 1",
        (user_id, username)
    )
    
    if existing_ban:
        ban_level = min(existing_ban[0] + 1, 3)
    else:
        ban_level = 1
    
    now = datetime.now()
    if ban_level == 1:
        banned_until = now + timedelta(minutes=5)
    elif ban_level == 2:
        banned_until = now + timedelta(minutes=10)
    else:
        banned_until = now + timedelta(days=30)
    
    await db.execute(
        "INSERT OR REPLACE INTO bans (user_id, username, ban_level, banned_until) VALUES (?, ?, ?, ?)",
        (user_id, username, ban_level, banned_until.isoformat())
    )
    
    await db.execute(
        "DELETE FROM failed_attempts WHERE user_id = ? OR username = ?",
        (user_id, username)
    )
    
    logging.warning(f"Пользователь id={user_id}, @{username} забанен на уровне {ban_level} до {banned_until}")
    
    return {
        "ban_level": ban_level,
        "banned_until": banned_until
    }


async def clear_failed_attempts(user_id: int = None, username: str = None):
    """Очистить счетчик неудачных попыток (при успешной авторизации)."""
    username = normalize_username(username)
    await db.execute(
        "DELETE FROM failed_attempts WHERE user_id = ? OR username = ?",
        (user_id, username)
    )


async def unban_user(user_id: int = None, username: str = None) -> bool:
    """Снять бан с пользователя (для админов)."""
    username = normalize_username(username)
    deleted = await db.execute(
        "DELETE FROM bans WHERE user_id = ? OR username = ?",
        (user_id, username)
    )
    await db.execute(
        "DELETE FROM failed_attempts WHERE user_id = ? OR username = ?",
        (user_id, username)
    )
    return deleted > 0


async def get_active_bans() -> list[dict]:
    """Получить список активных банов."""
    rows = await db.fetchall(
        "SELECT id, user_id, username, ban_level, banned_until FROM bans "
        "WHERE banned_until > datetime('now') ORDER BY banned_until DESC"
    )
    return [
        {"id": r[0], "user_id": r[1], "username": r[2], "ban_level": r[3], "banned_until": r[4]}
        for r in rows
    ]


# ==================== MIDDLEWARE & FILTERS ====================

class AuthMiddleware(BaseMiddleware):
    """Middleware для проверки авторизации один раз на сообщение.
    
    Проверяет, есть ли пользователь в БД. Если да — сохраняет роль в data.
    Если нет — блокирует доступ.
    """
    async def __call__(self, handler, event, data):
        # Пропускаем не-сообщения (callback_query и др.)
        if not hasattr(event, 'from_user'):
            return await handler(event, data)
            
        user_id = event.from_user.id
        username = event.from_user.username
        
        # Проверяем роль один раз
        role = await get_user_role(user_id=user_id, username=username)
        
        if not role:
            # Неавторизованный — отправляем сообщение и не пропускаем
            try:
                if hasattr(event, 'message') and event.message:
                    # Для callback_query отправляем через message
                    await event.message.answer("❌ У вас нет доступа к боту. Обратитесь к администратору.")
                elif hasattr(event, 'answer'):
                    # Для обычных сообщений
                    await event.answer("❌ У вас нет доступа к боту. Обратитесь к администратору.")
            except Exception:
                pass  # Не можем отправить — просто молча блокируем
            return
        
        # Сохраняем роль для использования в хендлерах
        data["user_role"] = role
        data["user_id"] = user_id
        data["username"] = username
        
        return await handler(event, data)


class IsAuthorizedUser:
    """Проверка авторизации по ID или username (для совместимости).
    Теперь используется как fallback, middleware делает основную работу.
    """
    async def __call__(self, message: Message) -> bool:
        # Если middleware уже проверила — пропускаем
        # Это fallback для callback_query и других событий
        user_id = message.from_user.id
        username = message.from_user.username
        
        if await get_user_by_id(user_id):
            return True
        
        if username and await get_user_by_username(username):
            return True
        
        return False


class HasRole:
    """Фильтр проверки роли с поддержкой мультиролей."""
    def __init__(self, role: str | list[str]):
        """Принимает одну роль или список ролей (достаточно одной)."""
        if isinstance(role, str):
            self.roles = [role]
        else:
            self.roles = role
    
    async def __call__(self, message: Message, **data) -> bool:
        # Проверяем кеш из middleware
        user_roles = data.get("user_roles")  # Кеш теперь список
        
        # Если кеша нет - берём из БД
        if user_roles is None:
            user_id = message.from_user.id
            username = message.from_user.username
            user_roles = await get_user_roles(user_id=user_id, username=username)
        
        # Проверяем, есть ли у пользователя хотя бы одна из требуемых ролей
        return any(role in user_roles for role in self.roles)


# ==================== EVENTS ====================

async def add_event(event_type: str, dt: str, link: str, announcement: str):
    datetime.fromisoformat(dt.replace("Z", "+00:00"))
    await db.execute(
        "INSERT INTO events (event_type, event_datetime, link, announcement) VALUES (?, ?, ?, ?)",
        (event_type, dt, link, announcement)
    )


async def get_events(upcoming_only: bool = False) -> list[dict]:
    query = "SELECT id, event_type, event_datetime, link, announcement FROM events"
    if upcoming_only:
        query += " WHERE event_datetime > datetime('now')"
    query += " ORDER BY event_datetime"
    
    rows = await db.fetchall(query)
    return [
        {"id": r[0], "type": r[1], "datetime": r[2], "link": r[3], "announcement": r[4]}
        for r in rows
    ]


async def update_event(event_id: int, **fields) -> bool:
    allowed = frozenset({'event_type', 'event_datetime', 'link', 'announcement'})
    
    for key in fields:
        if key not in allowed:
            logging.warning(f"Попытка SQL-инъекции: недопустимый ключ '{key}'")
            return False
    
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    
    if 'event_datetime' in updates:
        datetime.fromisoformat(updates['event_datetime'].replace("Z", "+00:00"))
    
    query = "UPDATE events SET " + ", ".join(f"{k}=?" for k in updates) + " WHERE id=?"
    params = list(updates.values()) + [event_id]
    return await db.execute(query, tuple(params)) > 0


async def delete_event(event_id: int) -> bool:
    return await db.execute("DELETE FROM events WHERE id = ?", (event_id,)) > 0


# ==================== MATERIALS ====================

async def add_material(stage: str, title: str, link: str, description: str = ""):
    await db.execute(
        "INSERT INTO materials (stage, title, link, description) VALUES (?, ?, ?, ?)",
        (stage, title, link, description)
    )


async def get_materials(stage: str = None) -> list[dict]:
    query = "SELECT id, stage, title, link, description FROM materials"
    params = ()
    if stage:
        query += " WHERE stage = ?"
        params = (stage,)
    query += " ORDER BY stage, created_at"
    
    rows = await db.fetchall(query, params)
    return [
        {"id": r[0], "stage": r[1], "title": r[2], "link": r[3], "description": r[4]}
        for r in rows
    ]


async def get_material(material_id: int) -> dict | None:
    row = await db.fetchone(
        "SELECT id, stage, title, link, description FROM materials WHERE id = ?",
        (material_id,)
    )
    if row:
        return {"id": row[0], "stage": row[1], "title": row[2], "link": row[3], "description": row[4]}
    return None


async def update_material(material_id: int, **fields) -> bool:
    allowed = frozenset({'stage', 'title', 'link', 'description'})
    
    for key in fields:
        if key not in allowed:
            logging.warning(f"Попытка SQL-инъекции: недопустимый ключ '{key}'")
            return False
    
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    
    query = "UPDATE materials SET " + ", ".join(f"{k}=?" for k in updates) + " WHERE id=?"
    params = list(updates.values()) + [material_id]
    return await db.execute(query, tuple(params)) > 0


async def delete_material(material_id: int) -> bool:
    return await db.execute("DELETE FROM materials WHERE id = ?", (material_id,)) > 0


async def get_materials_stats() -> dict:
    rows = await db.fetchall("SELECT stage, COUNT(*) FROM materials GROUP BY stage")
    stats = {stage: 0 for stage in STAGES}
    stats.update({r[0]: r[1] for r in rows})
    return stats


async def search_materials(query: str) -> list[dict]:
    """Поиск материалов по названию или описанию (case-insensitive)."""
    like = f"%{query.strip()}%"
    rows = await db.fetchall(
        "SELECT id, stage, title, link, description FROM materials "
        "WHERE title LIKE ? OR description LIKE ? ORDER BY stage, created_at",
        (like, like)
    )
    return [
        {"id": r[0], "stage": r[1], "title": r[2], "link": r[3], "description": r[4]}
        for r in rows
    ]


async def search_materials_by_title(query: str) -> list[dict]:
    """Поиск материалов только по названию (case-insensitive)."""
    like = f"%{query.strip()}%"
    rows = await db.fetchall(
        "SELECT id, stage, title, link, description FROM materials "
        "WHERE title LIKE ? ORDER BY stage, created_at",
        (like,)
    )
    return [
        {"id": r[0], "stage": r[1], "title": r[2], "link": r[3], "description": r[4]}
        for r in rows
    ]


# ==================== BUDDY ====================

async def add_mentorship(mentor_id: int, mentee_full_name: str, 
                         mentee_telegram_tag: str = None, 
                         mentee_id: int = None,
                         assigned_date: str = None,
                         status: str = 'active') -> int:
    """Добавить новое наставничество."""
    if assigned_date is None:
        from datetime import datetime
        assigned_date = datetime.now().strftime("%d.%m.%y")
    
    cursor = await db.execute(
        """INSERT INTO buddy_mentorships 
           (mentor_id, mentee_id, mentee_full_name, mentee_telegram_tag, status, assigned_date)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (mentor_id, mentee_id, mentee_full_name, mentee_telegram_tag, status, assigned_date)
    )
    return cursor.lastrowid


async def get_mentor_mentees(mentor_id: int) -> list[dict]:
    """Получить список менти ментора."""
    rows = await db.fetchall(
        """SELECT id, mentee_full_name, mentee_telegram_tag, status, assigned_date, mentee_id
           FROM buddy_mentorships 
           WHERE mentor_id = ? 
           ORDER BY assigned_date DESC""",
        (mentor_id,)
    )
    return [
        {
            "id": r[0],
            "full_name": r[1],
            "telegram_tag": r[2],
            "status": r[3],
            "assigned_date": r[4],
            "mentee_id": r[5]
        }
        for r in rows
    ]


async def get_user_mentor(user_id: int) -> dict | None:
    """Получить ментора пользователя."""
    row = await db.fetchone(
        """SELECT m.id, m.mentee_full_name, m.mentee_telegram_tag, m.status, 
                  m.assigned_date, u.user_id, u.username
           FROM buddy_mentorships m
           JOIN user_roles u ON m.mentor_id = u.id
           WHERE m.mentee_id = ? AND m.status = 'active'
           LIMIT 1""",
        (user_id,)
    )
    if not row:
        return None
    return {
        "id": row[0],
        "mentee_name": row[1],
        "mentee_tag": row[2],
        "status": row[3],
        "assigned_date": row[4],
        "mentor_id": row[5],
        "mentor_username": row[6]
    }


async def update_mentorship_status(mentorship_id: int, status: str) -> bool:
    """Обновить статус наставничества."""
    if status not in ('active', 'completed', 'paused', 'dropped'):
        return False
    return await db.execute(
        "UPDATE buddy_mentorships SET status = ? WHERE id = ?",
        (status, mentorship_id)
    ) > 0


async def delete_mentorship(mentorship_id: int) -> bool:
    """Удалить наставничество."""
    return await db.execute(
        "DELETE FROM buddy_mentorships WHERE id = ?",
        (mentorship_id,)
    ) > 0


async def get_mentorship_by_id(mentorship_id: int) -> dict | None:
    """Получить наставничество по ID."""
    row = await db.fetchone(
        """SELECT id, mentor_id, mentee_id, mentee_full_name, 
                  mentee_telegram_tag, status, assigned_date
           FROM buddy_mentorships WHERE id = ?""",
        (mentorship_id,)
    )
    if not row:
        return None
    return {
        "id": row[0],
        "mentor_id": row[1],
        "mentee_id": row[2],
        "full_name": row[3],
        "telegram_tag": row[4],
        "status": row[5],
        "assigned_date": row[6]
    }


# ==================== BUDDY LION (META ADMIN) ====================

async def get_all_mentors() -> list[dict]:
    """Получить список всех менторов (для Льва)."""
    rows = await db.fetchall(
        """SELECT id, user_id, username, created_at 
           FROM user_roles 
           WHERE role = 'mentor' 
           ORDER BY created_at DESC"""
    )
    return [
        {
            "id": r[0],
            "user_id": r[1],
            "username": r[2],
            "created_at": r[3]
        }
        for r in rows
    ]


async def get_mentor_stats(mentor_id: int) -> dict:
    """Получить статистику ментора (для Льва)."""
    # Общее количество менти
    total = await db.fetchone(
        "SELECT COUNT(*) FROM buddy_mentorships WHERE mentor_id = ?",
        (mentor_id,)
    )
    # По статусам
    active = await db.fetchone(
        "SELECT COUNT(*) FROM buddy_mentorships WHERE mentor_id = ? AND status = 'active'",
        (mentor_id,)
    )
    completed = await db.fetchone(
        "SELECT COUNT(*) FROM buddy_mentorships WHERE mentor_id = ? AND status = 'completed'",
        (mentor_id,)
    )
    dropped = await db.fetchone(
        "SELECT COUNT(*) FROM buddy_mentorships WHERE mentor_id = ? AND status = 'dropped'",
        (mentor_id,)
    )
    
    return {
        "total": total[0] if total else 0,
        "active": active[0] if active else 0,
        "completed": completed[0] if completed else 0,
        "dropped": dropped[0] if dropped else 0
    }


async def get_all_mentorships_for_lion() -> list[dict]:
    """Получить все наставничества для Льва (с информацией о менторе)."""
    rows = await db.fetchall(
        """SELECT m.id, m.mentor_id, m.mentee_full_name, m.mentee_telegram_tag,
                  m.status, m.assigned_date, u.username as mentor_username, u.user_id as mentor_user_id
           FROM buddy_mentorships m
           JOIN user_roles u ON m.mentor_id = u.id
           ORDER BY m.assigned_date DESC"""
    )
    return [
        {
            "id": r[0],
            "mentor_id": r[1],
            "mentee_name": r[2],
            "mentee_tag": r[3],
            "status": r[4],
            "assigned_date": r[5],
            "mentor_username": r[6],
            "mentor_user_id": r[7]
        }
        for r in rows
    ]


# Created by Техножрец R1sl1n
