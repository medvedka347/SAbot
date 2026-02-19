import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime
from aiogram.types import Message

from config import DB_NAME, STAGES


class Database:
    """Класс для работы с SQLite."""
    
    def __init__(self, db_path: str = DB_NAME):
        self.db_path = db_path
    
    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    
    def execute(self, query: str, params: tuple = ()) -> int:
        """Выполнить запрос, вернуть rowcount."""
        with self._connect() as conn:
            cursor = conn.execute(query, params)
            return cursor.rowcount
    
    def fetchone(self, query: str, params: tuple = ()):
        """Получить одну запись."""
        with self._connect() as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchone()
    
    def fetchall(self, query: str, params: tuple = ()) -> list:
        """Получить все записи."""
        with self._connect() as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchall()
    
    def init_tables(self):
        """Инициализация таблиц."""
        with self._connect() as conn:
            # Миграция user_roles
            self._migrate_user_roles()
            
            # Materials
            conn.execute("""
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
            conn.execute("""
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS failed_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE,
                    username TEXT UNIQUE,
                    attempt_count INTEGER DEFAULT 0,
                    last_attempt TEXT DEFAULT CURRENT_TIMESTAMP,
                    CHECK (user_id IS NOT NULL OR username IS NOT NULL)
                )
            """)
            
            # Баны (mute)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    ban_level INTEGER DEFAULT 1 CHECK (ban_level IN (1, 2, 3)),
                    banned_until TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    CHECK (user_id IS NOT NULL OR username IS NOT NULL)
                )
            """)
            
            # Создаем индексы для быстрого поиска
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bans_active ON bans(user_id, username, banned_until)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_failed_user ON failed_attempts(user_id, username)")
        
        # Миграция materials
        self._migrate_materials()
        
        # Создание индексов
        with self._connect() as conn:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON user_roles(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_username ON user_roles(username)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bans_user ON bans(user_id, username)")
        
        logging.info(f"База данных '{self.db_path}' готова")
    
    def _migrate_user_roles(self):
        """Миграция: добавление поля username."""
        with self._connect() as conn:
            # Проверяем, существует ли таблица
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_roles'")
            if not cursor.fetchone():
                # Таблицы нет - создаем новую сразу с правильной схемой
                conn.execute("""
                    CREATE TABLE user_roles (
                        user_id INTEGER,
                        username TEXT,
                        role TEXT NOT NULL CHECK (role IN ('user', 'mentor', 'admin')),
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, username),
                        CHECK (user_id IS NOT NULL OR username IS NOT NULL)
                    )
                """)
                logging.info("Создана таблица user_roles с полем username")
                return
            
            # Таблица существует - проверяем наличие колонки username
            try:
                conn.execute("SELECT username FROM user_roles LIMIT 1")
                # Если дошли сюда - колонка есть
                return
            except sqlite3.OperationalError:
                # Колонки нет - нужна миграция
                logging.warning("Миграция: добавление поля username")
                # Проверяем какие колонки есть в старой таблице
                cursor = conn.execute("PRAGMA table_info(user_roles)")
                columns = [row[1] for row in cursor.fetchall()]
                
                # Удаляем временную таблицу если осталась с прошлого раза
                conn.execute("DROP TABLE IF EXISTS user_roles_new")
                
                conn.executescript("""
                    CREATE TABLE user_roles_new (
                        user_id INTEGER,
                        username TEXT,
                        role TEXT NOT NULL CHECK (role IN ('user', 'mentor', 'admin')),
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, username),
                        CHECK (user_id IS NOT NULL OR username IS NOT NULL)
                    );
                """)
                
                # Формируем запрос вставки в зависимости от наличия колонок
                if 'created_at' in columns:
                    conn.execute("""
                        INSERT INTO user_roles_new (user_id, username, role, created_at)
                        SELECT user_id, NULL, role, created_at FROM user_roles
                    """)
                else:
                    conn.execute("""
                        INSERT INTO user_roles_new (user_id, username, role, created_at)
                        SELECT user_id, NULL, role, CURRENT_TIMESTAMP FROM user_roles
                    """)
                
                conn.execute("DROP TABLE user_roles")
                conn.execute("ALTER TABLE user_roles_new RENAME TO user_roles")
                logging.info("Миграция user_roles выполнена")
    
    def _migrate_materials(self):
        """Миграция: добавление поля stage."""
        try:
            self.fetchone("SELECT stage FROM materials LIMIT 1")
        except sqlite3.OperationalError:
            logging.warning("Миграция: добавление поля stage")
            with self._connect() as conn:
                conn.execute("ALTER TABLE materials RENAME TO materials_old")
                conn.executescript("""
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

def init_db(db_path: str = DB_NAME):
    """Инициализация (для совместимости)."""
    Database(db_path).init_tables()


# ==================== USER ROLES (с username) ====================

def normalize_username(username: str | None) -> str | None:
    """Нормализовать username (убрать @, привести к lowercase)."""
    if not username:
        return None
    username = username.strip().lower()
    if username.startswith('@'):
        username = username[1:]
    return username if username else None


def get_user_by_id(user_id: int) -> dict | None:
    """Найти пользователя по ID."""
    row = db.fetchone(
        "SELECT user_id, username, role FROM user_roles WHERE user_id = ?",
        (user_id,)
    )
    if row:
        return {"user_id": row[0], "username": row[1], "role": row[2]}
    return None


def get_user_by_username(username: str) -> dict | None:
    """Найти пользователя по username."""
    username = normalize_username(username)
    if not username:
        return None
    row = db.fetchone(
        "SELECT user_id, username, role FROM user_roles WHERE username = ?",
        (username,)
    )
    if row:
        return {"user_id": row[0], "username": row[1], "role": row[2]}
    return None


def get_user_role(user_id: int = None, username: str = None) -> str | None:
    """Получить роль по ID или username (оптимизировано - один запрос)."""
    username = normalize_username(username)
    
    if user_id and username:
        # Ищем по ID ИЛИ username одним запросом
        row = db.fetchone(
            "SELECT role FROM user_roles WHERE user_id = ? OR username = ? LIMIT 1",
            (user_id, username)
        )
    elif user_id:
        row = db.fetchone(
            "SELECT role FROM user_roles WHERE user_id = ? LIMIT 1",
            (user_id,)
        )
    elif username:
        row = db.fetchone(
            "SELECT role FROM user_roles WHERE username = ? LIMIT 1",
            (username,)
        )
    else:
        return None
    
    return row[0] if row else None


def validate_user_id(user_id: any) -> int | None:
    """Валидация ID пользователя."""
    if not user_id:
        return None
    try:
        uid = int(user_id)
        # Telegram ID должен быть положительным и не слишком большим
        if 0 < uid < 10_000_000_000:
            return uid
    except (ValueError, TypeError):
        pass
    return None


def get_all_users() -> list[dict]:
    """Получить всех пользователей."""
    rows = db.fetchall(
        "SELECT user_id, username, role FROM user_roles ORDER BY role, COALESCE(username, ''), user_id"
    )
    return [{"user_id": r[0], "username": r[1], "role": r[2]} for r in rows]


def add_or_update_user(user_id: int = None, username: str = None, role: str = None) -> bool:
    """
    Добавить или обновить пользователя.
    Можно указать только user_id, только username, или оба.
    Если пользователь с таким user_id или username уже есть - обновит роль.
    """
    if not user_id and not username:
        raise ValueError("Нужно указать user_id или username")
    
    username = normalize_username(username)
    
    # Проверяем, есть ли уже такой пользователь
    existing = None
    if user_id:
        existing = get_user_by_id(user_id)
    if not existing and username:
        existing = get_user_by_username(username)
    
    if existing:
        # Обновляем существующую запись
        # Если пришел новый user_id для существующего username (или наоборот) - объединяем
        new_user_id = user_id or existing["user_id"]
        new_username = username or existing["username"]
        
        # Удаляем старую запись
        db.execute(
            "DELETE FROM user_roles WHERE user_id = ? OR username = ?",
            (existing["user_id"], existing["username"])
        )
        
        # Вставляем обновленную
        db.execute(
            "INSERT INTO user_roles (user_id, username, role) VALUES (?, ?, ?)",
            (new_user_id, new_username, role)
        )
        logging.info(f"Обновлен пользователь: id={new_user_id}, @{new_username} -> {role}")
    else:
        # Новый пользователь
        db.execute(
            "INSERT INTO user_roles (user_id, username, role) VALUES (?, ?, ?)",
            (user_id, username, role)
        )
        logging.info(f"Добавлен пользователь: id={user_id}, @{username} -> {role}")
    
    return True


def set_users_batch(users: list[dict], role: str):
    """
    Массовое назначение роли.
    users: список словарей [{'user_id': 123, 'username': '@name'}, ...]
    """
    for user in users:
        add_or_update_user(
            user_id=user.get("user_id"),
            username=user.get("username"),
            role=role
        )
    logging.info(f"Роли {len(users)} пользователей -> {role}")


def delete_user(user_id: int = None, username: str = None) -> bool:
    """Удалить пользователя по ID или username."""
    if user_id:
        deleted = db.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
        if deleted > 0:
            return True
    if username:
        username = normalize_username(username)
        deleted = db.execute("DELETE FROM user_roles WHERE username = ?", (username,))
        if deleted > 0:
            return True
    return False


def setup_initial_users(db_path: str = DB_NAME, initial_admin_id: int = None):
    """Начальная настройка.
    
    Args:
        db_path: путь к базе данных
        initial_admin_id: ID начального админа (из переменной окружения INITIAL_ADMIN_ID)
    """
    from config import ROLE_ADMIN
    import os
    
    # Очищаем истекшие баны при старте
    cleanup_expired_bans()
    
    count = db.fetchone("SELECT COUNT(*) FROM user_roles")[0]
    if count == 0:
        # Получаем ID начального админа из переменной окружения
        admin_id_from_env = os.getenv("INITIAL_ADMIN_ID", "").strip()
        admin_id = initial_admin_id
        
        if admin_id_from_env and admin_id_from_env.isdigit():
            admin_id = int(admin_id_from_env)
        
        if admin_id:
            add_or_update_user(user_id=admin_id, role=ROLE_ADMIN)
            logging.warning(f"Добавлен начальный админ (ID: {admin_id})")
        else:
            logging.warning(
                "База данных пуста! Начальный админ не задан.\n"
                "Установите переменную INITIAL_ADMIN_ID в .env файле и перезапустите бота.\n"
                "Пример: INITIAL_ADMIN_ID=123456789"
            )


# ==================== RATE LIMITING & BANS ====================

def cleanup_expired_bans():
    """Удалить истекшие баны (можно вызывать периодически)."""
    deleted = db.execute("DELETE FROM bans WHERE banned_until <= datetime('now')")
    if deleted > 0:
        logging.info(f"Очищено {deleted} истекших банов")


def get_ban_status(user_id: int = None, username: str = None) -> dict | None:
    """
    Проверить статус бана пользователя.
    Returns: {'ban_level': int, 'banned_until': datetime} или None если не забанен
    """
    username = normalize_username(username)
    
    # Оптимизированный запрос с проверкой на NULL
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
    
    row = db.fetchone(query, params)
    if row:
        return {
            "ban_level": row[0],
            "banned_until": datetime.fromisoformat(row[1])
        }
    return None


def record_failed_attempt(user_id: int = None, username: str = None) -> dict | None:
    """
    Записать неудачную попытку авторизации.
    Returns: информация о бане если применен, иначе None
    """
    username = normalize_username(username)
    
    # Обновляем или создаем запись о неудачной попытке
    existing = db.fetchone(
        "SELECT attempt_count FROM failed_attempts WHERE user_id = ? OR username = ?",
        (user_id, username)
    )
    
    if existing:
        new_count = existing[0] + 1
        db.execute(
            "UPDATE failed_attempts SET attempt_count = ?, last_attempt = datetime('now') WHERE user_id = ? OR username = ?",
            (new_count, user_id, username)
        )
    else:
        new_count = 1
        db.execute(
            "INSERT INTO failed_attempts (user_id, username, attempt_count) VALUES (?, ?, ?)",
            (user_id, username, new_count)
        )
    
    # Проверяем нужно ли выдать бан (каждые 3 попытки)
    if new_count >= 3:
        return apply_ban(user_id, username)
    
    return None


def apply_ban(user_id: int = None, username: str = None) -> dict:
    """
    Применить бан на основе предыдущих банов.
    Возвращает информацию о примененном бане.
    """
    username = normalize_username(username)
    
    # Определяем уровень бана
    existing_ban = db.fetchone(
        "SELECT ban_level FROM bans WHERE user_id = ? OR username = ? ORDER BY created_at DESC LIMIT 1",
        (user_id, username)
    )
    
    if existing_ban:
        ban_level = min(existing_ban[0] + 1, 3)  # Макс уровень 3
    else:
        ban_level = 1
    
    # Вычисляем время бана
    now = datetime.now()
    if ban_level == 1:
        banned_until = now.replace(minute=now.minute + 5)
    elif ban_level == 2:
        banned_until = now.replace(minute=now.minute + 10)
    else:  # ban_level == 3
        # Бан на месяц
        banned_until = now.replace(month=now.month + 1) if now.month < 12 else now.replace(year=now.year + 1, month=1)
    
    # Сохраняем бан
    db.execute(
        "INSERT OR REPLACE INTO bans (user_id, username, ban_level, banned_until) VALUES (?, ?, ?, ?)",
        (user_id, username, ban_level, banned_until.isoformat())
    )
    
    # Сбрасываем счетчик неудачных попыток
    db.execute(
        "DELETE FROM failed_attempts WHERE user_id = ? OR username = ?",
        (user_id, username)
    )
    
    logging.warning(f"Пользователь id={user_id}, @{username} забанен на уровне {ban_level} до {banned_until}")
    
    return {
        "ban_level": ban_level,
        "banned_until": banned_until
    }


def clear_failed_attempts(user_id: int = None, username: str = None):
    """Очистить счетчик неудачных попыток (при успешной авторизации)."""
    username = normalize_username(username)
    db.execute(
        "DELETE FROM failed_attempts WHERE user_id = ? OR username = ?",
        (user_id, username)
    )


def unban_user(user_id: int = None, username: str = None) -> bool:
    """Снять бан с пользователя (для админов)."""
    username = normalize_username(username)
    deleted = db.execute(
        "DELETE FROM bans WHERE user_id = ? OR username = ?",
        (user_id, username)
    )
    # Также очищаем неудачные попытки
    db.execute(
        "DELETE FROM failed_attempts WHERE user_id = ? OR username = ?",
        (user_id, username)
    )
    return deleted > 0


# ==================== ФИЛЬТРЫ ====================

class IsAuthorizedUser:
    """Проверка авторизации по ID или username."""
    def __call__(self, message: Message) -> bool:
        user_id = message.from_user.id
        username = message.from_user.username
        
        # Проверяем по ID
        if get_user_by_id(user_id):
            return True
        
        # Проверяем по username
        if username and get_user_by_username(username):
            return True
        
        return False


# ==================== EVENTS ====================

def add_event(event_type: str, dt: str, link: str, announcement: str):
    datetime.fromisoformat(dt.replace("Z", "+00:00"))
    db.execute(
        "INSERT INTO events (event_type, event_datetime, link, announcement) VALUES (?, ?, ?, ?)",
        (event_type, dt, link, announcement)
    )


def get_events(upcoming_only: bool = False) -> list[dict]:
    query = "SELECT id, event_type, event_datetime, link, announcement FROM events"
    if upcoming_only:
        query += " WHERE event_datetime > datetime('now')"
    query += " ORDER BY event_datetime"
    
    rows = db.fetchall(query)
    return [
        {"id": r[0], "type": r[1], "datetime": r[2], "link": r[3], "announcement": r[4]}
        for r in rows
    ]


def update_event(event_id: int, **fields) -> bool:
    allowed = frozenset({'event_type', 'event_datetime', 'link', 'announcement'})
    
    # Строгая валидация ключей
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
    return db.execute(query, tuple(params)) > 0


def delete_event(event_id: int) -> bool:
    return db.execute("DELETE FROM events WHERE id = ?", (event_id,)) > 0


# ==================== MATERIALS ====================

def add_material(stage: str, title: str, link: str, description: str = ""):
    db.execute(
        "INSERT INTO materials (stage, title, link, description) VALUES (?, ?, ?, ?)",
        (stage, title, link, description)
    )


def get_materials(stage: str = None) -> list[dict]:
    query = "SELECT id, stage, title, link, description FROM materials"
    params = ()
    if stage:
        query += " WHERE stage = ?"
        params = (stage,)
    query += " ORDER BY stage, created_at"
    
    rows = db.fetchall(query, params)
    return [
        {"id": r[0], "stage": r[1], "title": r[2], "link": r[3], "description": r[4]}
        for r in rows
    ]


def get_material(material_id: int) -> dict | None:
    row = db.fetchone(
        "SELECT id, stage, title, link, description FROM materials WHERE id = ?",
        (material_id,)
    )
    if row:
        return {"id": row[0], "stage": row[1], "title": row[2], "link": row[3], "description": row[4]}
    return None


def update_material(material_id: int, **fields) -> bool:
    allowed = frozenset({'stage', 'title', 'link', 'description'})
    
    # Строгая валидация ключей
    for key in fields:
        if key not in allowed:
            logging.warning(f"Попытка SQL-инъекции: недопустимый ключ '{key}'")
            return False
    
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    
    query = "UPDATE materials SET " + ", ".join(f"{k}=?" for k in updates) + " WHERE id=?"
    params = list(updates.values()) + [material_id]
    return db.execute(query, tuple(params)) > 0


def delete_material(material_id: int) -> bool:
    return db.execute("DELETE FROM materials WHERE id = ?", (material_id,)) > 0


def get_materials_stats() -> dict:
    rows = db.fetchall("SELECT stage, COUNT(*) FROM materials GROUP BY stage")
    stats = {stage: 0 for stage in STAGES}
    stats.update({r[0]: r[1] for r in rows})
    return stats
