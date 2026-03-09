import aiosqlite
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram.types import Message
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from config import DB_NAME, STAGES, ROLE_ADMIN, ROLE_PRIORITIES, ROLE_DISPLAY_NAMES, get_max_priority


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
        # Включаем проверку foreign keys для целостности данных
        await db.execute("PRAGMA foreign_keys=ON")
    
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
            
            # === НОВАЯ СИСТЕМА РОЛЕЙ (v2) ===
            # Справочник ролей с приоритетами
            await db.execute("""
                CREATE TABLE IF NOT EXISTS roles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role_key TEXT UNIQUE NOT NULL,
                    role_name TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    description TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Таблица связи пользователей с ролями (many-to-many)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_role_assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    role_id INTEGER NOT NULL,
                    assigned_by INTEGER,
                    assigned_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, role_id),
                    FOREIGN KEY (user_id) REFERENCES user_roles(id) ON DELETE CASCADE,
                    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
                    FOREIGN KEY (assigned_by) REFERENCES user_roles(id) ON DELETE SET NULL
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_role_assignments(user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_role_assignments(role_id)")
            
            # Инициализация базовых ролей
            await self._init_roles(db)
            
            # === МИГРАЦИЯ v1 -> v2 (строки -> нормализованная схема) ===
            # Таблица для отслеживания выполненных миграций
            await db.execute("""
                CREATE TABLE IF NOT EXISTS _migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    migration_name TEXT UNIQUE NOT NULL,
                    executed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    details TEXT
                )
            """)
            
            # Проверяем, выполнялась ли миграция ролей
            cursor = await db.execute(
                "SELECT 1 FROM _migrations WHERE migration_name = 'roles_v1_to_v2'"
            )
            migration_done = await cursor.fetchone()
            
            if not migration_done:
                logging.warning("=== НАЧИНАЕМ МИГРАЦИЮ РОЛЕЙ v1 -> v2 ===")
                await self._migrate_roles_v1_to_v2(db)
                await db.execute(
                    "INSERT INTO _migrations (migration_name, details) VALUES (?, ?)",
                    ("roles_v1_to_v2", "Миграция строковых ролей в нормализованную схему")
                )
                logging.warning("=== МИГРАЦИЯ РОЛЕЙ ЗАВЕРШЕНА ===")
            else:
                logging.info("Миграция ролей v1->v2 уже выполнена, пропускаем")
            
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bans_user ON bans(user_id, username)")
            
            await db.commit()
        
        logging.info(f"База данных '{self.db_path}' готова")
    
    async def _init_roles(self, db: aiosqlite.Connection):
        """Инициализация базовых ролей в справочнике."""
        for role_key, priority in ROLE_PRIORITIES.items():
            role_name = ROLE_DISPLAY_NAMES.get(role_key, role_key)
            await db.execute("""
                INSERT OR IGNORE INTO roles (role_key, role_name, priority, description)
                VALUES (?, ?, ?, ?)
            """, (role_key, role_name, priority, f"Роль {role_key}"))
    
    async def _drop_role_column(self, db: aiosqlite.Connection):
        """Удаление устаревшего поля role из user_roles (SQLite не поддерживает DROP COLUMN)."""
        await db.execute("DROP TABLE IF EXISTS user_roles_new")
        await db.execute("""
            CREATE TABLE user_roles_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id BIGINT UNIQUE,
                username TEXT UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                CHECK (user_id IS NOT NULL OR username IS NOT NULL)
            )
        """)
        # Переносим данные (без поля role)
        await db.execute("""
            INSERT INTO user_roles_new (id, user_id, username, created_at)
            SELECT id, user_id, username, created_at FROM user_roles
        """)
        await db.execute("DROP TABLE user_roles")
        await db.execute("ALTER TABLE user_roles_new RENAME TO user_roles")
        logging.info("Поле 'role' удалено из user_roles")
    
    async def _migrate_roles_v1_to_v2(self, db: aiosqlite.Connection):
        """
        Миграция ролей из строкового формата в нормализованную схему.
        
        v1: user_roles.role = 'admin,lion' (строка через запятую)
        v2: user_role_assignments (many-to-many)
        """
        migrated_count = 0
        skipped_count = 0
        error_count = 0
        
        try:
            # Получаем всех пользователей со старыми ролями
            cursor = await db.execute("""
                SELECT id, user_id, username, role 
                FROM user_roles 
                WHERE role IS NOT NULL AND role != ''
                ORDER BY id
            """)
            users = await cursor.fetchall()
            
            total = len(users)
            logging.info(f"Найдено {total} пользователей для миграции ролей")
            
            for user_record in users:
                internal_id, telegram_id, username, role_str = user_record
                
                try:
                    # Разбиваем строку ролей
                    role_keys = [r.strip().lower() for r in role_str.split(',') if r.strip()]
                    
                    if not role_keys:
                        logging.warning(f"Пользователь {telegram_id or username}: пустая строка ролей, пропускаем")
                        skipped_count += 1
                        continue
                    
                    roles_added = []
                    for role_key in role_keys:
                        # Получаем ID роли из справочника
                        cursor = await db.execute(
                            "SELECT id FROM roles WHERE role_key = ?", (role_key,)
                        )
                        role_row = await cursor.fetchone()
                        
                        if role_row:
                            role_id = role_row[0]
                            # Проверяем, не назначена ли уже эта роль
                            cursor = await db.execute(
                                "SELECT 1 FROM user_role_assignments WHERE user_id = ? AND role_id = ?",
                                (internal_id, role_id)
                            )
                            exists = await cursor.fetchone()
                            
                            if not exists:
                                await db.execute("""
                                    INSERT INTO user_role_assignments (user_id, role_id, assigned_at)
                                    VALUES (?, ?, datetime('now'))
                                """, (internal_id, role_id))
                                roles_added.append(role_key)
                        else:
                            logging.warning(f"Неизвестная роль '{role_key}' для пользователя {telegram_id or username}")
                    
                    if roles_added:
                        logging.info(f"Мигрирован пользователь {telegram_id or username}: {', '.join(roles_added)}")
                        migrated_count += 1
                    else:
                        skipped_count += 1
                        
                except Exception as e:
                    logging.error(f"Ошибка миграции пользователя {telegram_id or username}: {e}")
                    error_count += 1
            
            logging.warning(
                f"Миграция ролей завершена: "
                f"{migrated_count} мигрировано, "
                f"{skipped_count} пропущено, "
                f"{error_count} ошибок"
            )
            
            # После успешной миграции данных - удаляем поле role
            if error_count == 0:
                logging.warning("Удаление устаревшего поля 'role' из user_roles")
                await self._drop_role_column(db)
            
        except Exception as e:
            logging.error(f"Критическая ошибка миграции ролей: {e}")
            raise  # Пробрасываем ошибку, чтобы остановить запуск если миграция не удалась
    
    async def _migrate_user_roles(self, db: aiosqlite.Connection):
        """
        Создание/обновление таблицы user_roles.
        ВАЖНО: Поле role пока сохраняем для миграции данных (удалится позже в _migrate_roles_v1_to_v2).
        """
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_roles'")
        if not await cursor.fetchone():
            # Таблицы нет - создаем старую схему v1 (с полем role) для совместимости
            # Поле role будет удалено после миграции данных
            await db.execute("""
                CREATE TABLE user_roles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id BIGINT UNIQUE,
                    username TEXT UNIQUE,
                    role TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    CHECK (user_id IS NOT NULL OR username IS NOT NULL)
                )
            """)
            logging.info("Создана таблица user_roles (v1, для последующей миграции)")
            return
        
        # Таблица существует - проверим структуру
        # НЕ удаляем поле role здесь - это сделает _migrate_roles_v1_to_v2 после миграции данных
    
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
    """Найти пользователя по ID. Возвращает внутренний id из user_roles."""
    row = await db.fetchone(
        "SELECT id, user_id, username FROM user_roles WHERE user_id = ?",
        (user_id,)
    )
    if row:
        return {"id": row[0], "user_id": row[1], "username": row[2]}
    return None


async def get_user_by_db_id(db_id: int) -> dict | None:
    """Найти пользователя по внутреннему ID (id из user_roles)."""
    row = await db.fetchone(
        "SELECT id, user_id, username FROM user_roles WHERE id = ?",
        (db_id,)
    )
    if row:
        return {"id": row[0], "user_id": row[1], "username": row[2]}
    return None


async def get_user_by_username(username: str) -> dict | None:
    """Найти пользователя по username. Возвращает внутренний id из user_roles."""
    username = normalize_username(username)
    if not username:
        return None
    row = await db.fetchone(
        "SELECT id, user_id, username FROM user_roles WHERE username = ?",
        (username,)
    )
    if row:
        return {"id": row[0], "user_id": row[1], "username": row[2]}
    return None


async def get_user_role(user_id: int = None, username: str = None) -> str | None:
    """Получить роль по ID или username (для обратной совместимости).
    Для мультиролей используйте get_user_roles()."""
    roles = await get_user_roles(user_id, username)
    return roles[0] if roles else None


# ==================== НОРМАЛИЗОВАННАЯ СИСТЕМА РОЛЕЙ (v2) ====================

async def get_user_roles(user_id: int = None, username: str = None) -> list[dict]:
    """
    Получить список ролей пользователя с полной информацией.
    
    Returns:
        list[dict]: [{'role_key': 'admin', 'role_name': '...', 'priority': 300}, ...]
    """
    username = normalize_username(username)
    
    # Сначала получаем внутренний ID пользователя
    user = None
    if user_id:
        user = await get_user_by_id(user_id)
    if not user and username:
        user = await get_user_by_username(username)
    
    if not user:
        return []
    
    # Получаем роли через нормализованную схему
    rows = await db.fetchall("""
        SELECT r.role_key, r.role_name, r.priority, r.description
        FROM user_role_assignments ura
        JOIN roles r ON ura.role_id = r.id
        WHERE ura.user_id = ?
        ORDER BY r.priority DESC
    """, (user['id'],))
    
    return [
        {
            'role_key': row[0],
            'role_name': row[1],
            'priority': row[2],
            'description': row[3]
        }
        for row in rows
    ]


async def get_user_roles_simple(user_id: int = None, username: str = None) -> list[str]:
    """
    Получить список role_key пользователя (для обратной совместимости).
    
    Returns:
        list[str]: ['admin', 'mentor', ...]
    """
    roles = await get_user_roles(user_id, username)
    return [r['role_key'] for r in roles]


async def get_user_max_priority(user_id: int = None, username: str = None) -> int:
    """Получить максимальный приоритет пользователя (0 если нет ролей)."""
    roles = await get_user_roles(user_id, username)
    if not roles:
        return 0
    return max(r['priority'] for r in roles)


async def get_user_primary_role(user_id: int = None, username: str = None) -> dict | None:
    """Получить роль с максимальным приоритетом."""
    roles = await get_user_roles(user_id, username)
    if not roles:
        return None
    return max(roles, key=lambda r: r['priority'])


async def has_role(user_id: int, role: str) -> bool:
    """Проверить, имеет ли пользователь конкретную роль."""
    roles = await get_user_roles_simple(user_id=user_id)
    return role in roles


async def has_min_priority(user_id: int, min_priority: int) -> bool:
    """Проверить, имеет ли пользователь роль с минимальным приоритетом."""
    max_priority = await get_user_max_priority(user_id=user_id)
    return max_priority >= min_priority


async def assign_role(user_id: int, role_key: str, assigned_by: int = None) -> bool:
    """
    Назначить роль пользователю.
    
    Args:
        user_id: Telegram user_id
        role_key: Ключ роли ('admin', 'mentor', etc)
        assigned_by: ID пользователя, кто назначает (для аудита)
    
    Returns:
        bool: True если успешно
    """
    # Получаем внутренний ID пользователя
    user = await get_user_by_id(user_id)
    if not user:
        logging.warning(f"assign_role: пользователь {user_id} не найден")
        return False
    
    # Получаем ID роли
    row = await db.fetchone("SELECT id FROM roles WHERE role_key = ?", (role_key,))
    if not row:
        logging.warning(f"assign_role: роль {role_key} не найдена")
        return False
    
    role_id = row[0]
    
    # Назначаем роль
    try:
        await db.execute("""
            INSERT OR IGNORE INTO user_role_assignments (user_id, role_id, assigned_by)
            VALUES (?, ?, ?)
        """, (user['id'], role_id, assigned_by))
        logging.info(f"Роль {role_key} назначена пользователю {user_id}")
        return True
    except Exception as e:
        logging.error(f"Ошибка назначения роли: {e}")
        return False


async def revoke_role(user_id: int, role_key: str) -> bool:
    """Отозвать роль у пользователя."""
    user = await get_user_by_id(user_id)
    if not user:
        return False
    
    row = await db.fetchone("SELECT id FROM roles WHERE role_key = ?", (role_key,))
    if not row:
        return False
    
    role_id = row[0]
    
    deleted = await db.execute(
        "DELETE FROM user_role_assignments WHERE user_id = ? AND role_id = ?",
        (user['id'], role_id)
    )
    return deleted > 0


async def set_user_roles(user_id: int, role_keys: list[str], assigned_by: int = None) -> bool:
    """
    Установить роли пользователя (заменить все текущие).
    
    Args:
        user_id: Telegram user_id
        role_keys: Список ключей ролей
        assigned_by: ID назначившего
    
    Returns:
        bool: True если успешно
    """
    user = await get_user_by_id(user_id)
    if not user:
        return False
    
    # Удаляем текущие роли
    await db.execute(
        "DELETE FROM user_role_assignments WHERE user_id = ?",
        (user['id'],)
    )
    
    # Назначаем новые
    success = True
    for role_key in role_keys:
        if not await assign_role(user_id, role_key, assigned_by):
            success = False
    
    return success


# ==================== ОБРАТНАЯ СОВМЕСТИМОСТЬ (старая система) ====================

async def add_user_role(user_id: int, new_role: str) -> bool:
    """Добавить роль пользователю (обратная совместимость)."""
    return await assign_role(user_id, new_role)


async def remove_user_role(user_id: int, role_to_remove: str) -> bool:
    """Удалить роль у пользователя (обратная совместимость)."""
    return await revoke_role(user_id, role_to_remove)


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
    """
    Получить всех пользователей с их ролями.
    
    Returns:
        list[dict]: [{'user_id': ..., 'username': ..., 'roles': [...], 'max_priority': ...}, ...]
    """
    rows = await db.fetchall(
        "SELECT id, user_id, username FROM user_roles ORDER BY COALESCE(username, ''), user_id"
    )
    
    users = []
    for row in rows:
        internal_id, user_id, username = row
        
        # Получаем роли пользователя
        role_rows = await db.fetchall("""
            SELECT r.role_key, r.priority 
            FROM user_role_assignments ura
            JOIN roles r ON ura.role_id = r.id
            WHERE ura.user_id = ?
            ORDER BY r.priority DESC
        """, (internal_id,))
        
        roles = [r[0] for r in role_rows]
        max_priority = role_rows[0][1] if role_rows else 0
        
        users.append({
            "id": internal_id,
            "user_id": user_id,
            "username": username,
            "roles": roles,
            "role": roles[0] if roles else None,  # Для обратной совместимости
            "max_priority": max_priority
        })
    
    # Сортируем по приоритету (убывание), затем по username
    users.sort(key=lambda u: (-u['max_priority'], u['username'] or '', u['user_id'] or 0))
    
    return users


async def add_or_update_user(user_id: int = None, username: str = None, role: str = None) -> bool:
    """
    Добавить или обновить пользователя.
    Можно указать только user_id, только username, или оба.
    При совпадении по ID или username - объединяет данные.
    
    Параметр role сохраняется для обратной совместимости (строка через запятую
    или одиночная роль), но используется нормализованная система user_role_assignments.
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
    
    # Получаем внутренний ID для обновления ролей
    internal_id = None
    if existing_by_id:
        internal_id = existing_by_id['id']
    elif existing_by_username:
        internal_id = existing_by_username['id']
    
    # Удаляем дубликаты (если user_id привязан к другому username или наоборот)
    ids_to_delete = set()
    if existing_by_id and existing_by_id.get("user_id") and existing_by_id['id'] != internal_id:
        ids_to_delete.add(existing_by_id["user_id"])
    if existing_by_username and existing_by_username.get("user_id") and existing_by_username['id'] != internal_id:
        ids_to_delete.add(existing_by_username["user_id"])
    
    for uid in ids_to_delete:
        if uid is not None:
            await db.execute("DELETE FROM user_roles WHERE user_id = ?", (uid,))
    
    # Удаляем по username если это другой пользователь
    if existing_by_username and existing_by_username.get("username"):
        if not internal_id or existing_by_username['id'] != internal_id:
            await db.execute("DELETE FROM user_roles WHERE username = ?", (existing_by_username["username"],))
    
    # Вставляем или обновляем пользователя
    try:
        if internal_id:
            # Обновляем существующего
            await db.execute(
                "UPDATE user_roles SET user_id = ?, username = ? WHERE id = ?",
                (final_user_id, final_username, internal_id)
            )
        else:
            # Создаем нового
            cursor = await db.execute(
                "INSERT INTO user_roles (user_id, username) VALUES (?, ?)",
                (final_user_id, final_username)
            )
            internal_id = cursor.lastrowid
            
    except aiosqlite.IntegrityError:
        # Пробуем обновить существующего
        if final_user_id:
            await db.execute(
                "UPDATE user_roles SET username = ? WHERE user_id = ?",
                (final_username, final_user_id)
            )
    
    # Назначаем роль если указана (через нормализованную систему)
    if role and final_user_id:
        # Поддержка строки через запятую для обратной совместимости: "admin,mentor"
        if ',' in role:
            role_keys = [r.strip() for r in role.split(',') if r.strip()]
            for role_key in role_keys:
                await assign_role(final_user_id, role_key)
        else:
            # Одиночная роль
            await assign_role(final_user_id, role.strip())
    
    action = "Обновлен" if (existing_by_id or existing_by_username) else "Добавлен"
    logging.info(f"{action} пользователь: id={final_user_id}, @{final_username}")
    return True


async def set_users_batch(users: list[dict], role: str, assigned_by: int = None):
    """
    Массовое назначение роли (добавление к существующим ролям).
    users: список словарей [{'user_id': 123, 'username': '@name'}, ...]
    """
    for user in users:
        user_id = user.get("user_id")
        username = user.get("username")
        
        # Создаем пользователя если не существует
        await add_or_update_user(user_id=user_id, username=username, role=None)
        
        # Назначаем роль через нормализованную систему
        await assign_role(user_id, role, assigned_by)
        
    logging.info(f"Роли {len(users)} пользователей -> {role} (добавлено к существующим)")


async def update_user_id_by_username(username: str, user_id: int) -> bool:
    """Обновить user_id для пользователя, добавленного по username."""
    username = normalize_username(username)
    if not username or not user_id:
        return False
    
    # Проверяем, не занят ли уже этот user_id другим пользователем
    existing_by_id = await get_user_by_id(user_id)
    if existing_by_id:
        # Этот user_id уже привязан к другому username - пропускаем
        logging.warning(f"user_id {user_id} уже занят пользователем @{existing_by_id.get('username')}")
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
    
    Проверяет, есть ли пользователь в БД. Если да — сохраняет роли и приоритет в data.
    Если нет — блокирует доступ.
    
    Исключение: команда /start пропускается всегда (start_handler сам обрабатывает
    неавторизованных пользователей - считает неудачные попытки и выдает баны).
    """
    async def __call__(self, handler, event, data):
        # Пропускаем не-сообщения (callback_query и др.)
        if not hasattr(event, 'from_user'):
            return await handler(event, data)
            
        user_id = event.from_user.id
        username = event.from_user.username
        
        # Пропускаем /start команду - start_handler сам обработает неавторизованных
        # (включая логику неудачных попыток и банов)
        if hasattr(event, 'text') and event.text and event.text.startswith('/start'):
            return await handler(event, data)
        
        # Проверяем роли через нормализованную систему
        user_roles = await get_user_roles(user_id=user_id, username=username)
        
        if not user_roles:
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
        
        # Сохраняем данные для использования в хендлерах
        data["user_roles"] = user_roles  # Список dict с role_key, priority, etc
        data["user_role"] = user_roles[0]['role_key'] if user_roles else None  # Для обратной совместимости
        data["user_max_priority"] = max(r['priority'] for r in user_roles) if user_roles else 0
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
    """
    Фильтр проверки роли с поддержкой приоритетов.
    
    Поддерживает проверку:
    - По конкретной роли: HasRole("admin")
    - По списку ролей: HasRole(["admin", "lion"])
    - По минимальному приоритету: HasRole(min_priority=300)
    """
    def __init__(self, role: str | list[str] = None, min_priority: int = None):
        """
        Args:
            role: Одна роль или список ролей
            min_priority: Минимальный требуемый приоритет (альтернатива role)
        """
        if role is not None:
            if isinstance(role, str):
                self.roles = [role]
            else:
                self.roles = role
        else:
            self.roles = []
        
        self.min_priority = min_priority
        
        # Если указана роль без приоритета - вычисляем приоритет
        if self.roles and min_priority is None:
            from config import get_role_priority
            self.min_priority = max(get_role_priority(r) for r in self.roles)
    
    async def __call__(self, message: Message, **data) -> bool:
        # Проверяем кеш из middleware
        user_roles = data.get("user_roles")  # Список dict с role_key, priority
        user_max_priority = data.get("user_max_priority")
        
        # Если кеша нет - берём из БД
        if user_roles is None:
            user_id = message.from_user.id
            username = message.from_user.username
            user_roles = await get_user_roles(user_id=user_id, username=username)
            user_max_priority = max(r['priority'] for r in user_roles) if user_roles else 0
        
        # Проверка по приоритету (если задан)
        if self.min_priority is not None:
            return user_max_priority >= self.min_priority
        
        # Проверка по конкретным ролям
        user_role_keys = [r['role_key'] for r in user_roles] if user_roles else []
        return any(role in user_role_keys for role in self.roles)


class HasMinPriority:
    """Фильтр проверки минимального приоритета."""
    def __init__(self, min_priority: int):
        self.min_priority = min_priority
    
    async def __call__(self, message: Message, **data) -> bool:
        user_max_priority = data.get("user_max_priority")
        
        if user_max_priority is None:
            user_id = message.from_user.id
            username = message.from_user.username
            user_max_priority = await get_user_max_priority(user_id=user_id, username=username)
        
        return user_max_priority >= self.min_priority


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
    import aiosqlite
    
    if assigned_date is None:
        from datetime import datetime
        assigned_date = datetime.now().strftime("%d.%m.%y")
    
    # Проверяем существование ментора
    mentor_exists = await db.fetchone(
        "SELECT 1 FROM user_roles WHERE id = ?", (mentor_id,)
    )
    if not mentor_exists:
        logging.error(f"add_mentorship: ментор с id={mentor_id} не найден в user_roles")
        raise ValueError(f"Ментор с id={mentor_id} не найден")
    
    # Используем соединение напрямую для получения lastrowid
    async with aiosqlite.connect(db.db_path) as conn:
        await db._init_connection(conn)
        cursor = await conn.execute(
            """INSERT INTO buddy_mentorships 
               (mentor_id, mentee_id, mentee_full_name, mentee_telegram_tag, status, assigned_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (mentor_id, mentee_id, mentee_full_name, mentee_telegram_tag, status, assigned_date)
        )
        await conn.commit()
        mentorship_id = cursor.lastrowid
        logging.info(f"add_mentorship: создано наставничество id={mentorship_id} для ментора {mentor_id}")
        return mentorship_id


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
    """Получить список всех менторов (для Льва) через нормализованную схему."""
    rows = await db.fetchall(
        """SELECT ur.id, ur.user_id, ur.username, ur.created_at 
           FROM user_roles ur
           JOIN user_role_assignments ura ON ur.id = ura.user_id
           JOIN roles r ON ura.role_id = r.id
           WHERE r.role_key = 'mentor'
           ORDER BY ur.created_at DESC"""
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
