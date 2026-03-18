import os
from dotenv import load_dotenv

load_dotenv()

# --- Настройки ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_NAME = os.getenv("DB_NAME", "user_roles.db")

# --- Роли ---
ROLE_USER = "user"
ROLE_MENTOR = "mentor"
ROLE_ADMIN = "admin"
ROLE_LION = "lion"  # Мета-роль: админ + управление Buddy
ROLES = [ROLE_USER, ROLE_MENTOR, ROLE_ADMIN, ROLE_LION]

# --- Система приоритетов ролей ---
# Приоритет: выше число = больше прав
# Иерархия: lion (400) > admin (300) > mentor (200) > user (100)
ROLE_PRIORITIES = {
    ROLE_USER: 100,
    ROLE_MENTOR: 200,
    ROLE_ADMIN: 300,
    ROLE_LION: 400,
}

# Описания ролей для отображения
ROLE_DISPLAY_NAMES = {
    ROLE_USER: "👤 Пользователь",
    ROLE_MENTOR: "🎓 Ментор",
    ROLE_ADMIN: "👑 Администратор",
    ROLE_LION: "🦁 Лев (Meta-Admin)",
}

# Минимальные приоритеты для модулей
MODULE_ACCESS = {
    "materials": ROLE_PRIORITIES[ROLE_ADMIN],      # CRUD материалов
    "events": ROLE_PRIORITIES[ROLE_MENTOR],        # CRUD событий (ментор и выше)
    "roles": ROLE_PRIORITIES[ROLE_ADMIN],          # Управление ролями
    "bans": ROLE_PRIORITIES[ROLE_ADMIN],           # Управление банами
    "buddy_lion": ROLE_PRIORITIES[ROLE_LION],      # Панель Льва
    "buddy_mentor": ROLE_PRIORITIES[ROLE_MENTOR],  # Панель ментора
    "mocks": ROLE_PRIORITIES[ROLE_USER],           # Запись на мок
    "search": ROLE_PRIORITIES[ROLE_USER],          # Поиск материалов
}

def get_role_priority(role_key: str) -> int:
    """Получить приоритет роли (0 если неизвестна)."""
    return ROLE_PRIORITIES.get(role_key, 0)

def get_max_priority(roles: list[str]) -> int:
    """Получить максимальный приоритет из списка ролей."""
    if not roles:
        return 0
    return max(get_role_priority(r) for r in roles)

def get_primary_role(roles: list[str]) -> str | None:
    """Получить роль с максимальным приоритетом."""
    if not roles:
        return None
    return max(roles, key=get_role_priority)

def has_min_priority(user_roles: list[str], min_priority: int) -> bool:
    """Проверить, есть ли у пользователя роль с минимальным приоритетом."""
    return get_max_priority(user_roles) >= min_priority

# --- Стадии материалов ---
STAGE_FUNDAMENTAL = "fundamental"
STAGE_PRACTICAL_THEORY = "practical_theory"
STAGE_PRACTICAL_TASKS = "practical_tasks"
STAGE_ROADMAP = "roadmap"

STAGES = {
    STAGE_FUNDAMENTAL: "📚 Фундаментальная теория",
    STAGE_PRACTICAL_THEORY: "🔧 Практическая теория",
    STAGE_PRACTICAL_TASKS: "📝 Практические задания",
    STAGE_ROADMAP: "🗺️ Прочие гайды",
}

# --- Настройки группы для анонсов ---
ANNOUNCEMENT_GROUP_ID = os.getenv("ANNOUNCEMENT_GROUP_ID", "")
ANNOUNCEMENT_TOPIC_ID = os.getenv("ANNOUNCEMENT_TOPIC_ID", "")

# --- Менторы для мок-интервью ---
MOCK_MENTORS = {
    "Влад": {
        "cal_link": None,
        "available": False,
        "emoji": "👤"
    },
    "Регина": {
        "cal_link": "https://cal.com/ocpocmak/mock",
        "available": True,
        "emoji": "👤"
    },
    "Руслан": {
        "cal_link": "https://cal.com/akhmadishin/мок",
        "available": True,
        "emoji": "👤"
    },
    "Иван": {
        "cal_link": None,
        "available": False,
        "emoji": "👤"
    },
}
# Конвертируем в int если заданы
if ANNOUNCEMENT_GROUP_ID:
    ANNOUNCEMENT_GROUP_ID = int(ANNOUNCEMENT_GROUP_ID)
if ANNOUNCEMENT_TOPIC_ID:
    ANNOUNCEMENT_TOPIC_ID = int(ANNOUNCEMENT_TOPIC_ID)

# Проверка токена
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле!")
