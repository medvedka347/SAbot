import os
from dotenv import load_dotenv

load_dotenv()

# --- Настройки ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_NAME = os.getenv("DB_NAME", "user_roles.db")

# --- Capability-роли (гранулярные) ---
ROLE_USER = "user"
ROLE_MENTOR = "mentor"
ROLE_MANAGER = "manager"      # capability: назначение бадди
ROLE_ANALYST = "analyst"      # capability: аналитика
ROLE_ADMIN = "admin"          # capability: полный доступ (founder)
ROLES = [ROLE_USER, ROLE_MENTOR, ROLE_MANAGER, ROLE_ANALYST, ROLE_ADMIN]

# --- Bundles: название -> набор capability-ролей ---
# "manager" = бывший "лев": ментор + управление + аналитика
ROLE_BUNDLES = {
    "manager": {ROLE_MENTOR, ROLE_MANAGER, ROLE_ANALYST},
}

# --- Система приоритетов ролей ---
ROLE_PRIORITIES = {
    ROLE_USER: 100,
    ROLE_MENTOR: 200,
    ROLE_MANAGER: 300,
    ROLE_ANALYST: 300,
    ROLE_ADMIN: 400,
}

# Описания ролей для отображения
ROLE_DISPLAY_NAMES = {
    ROLE_USER: "👤 Пользователь",
    ROLE_MENTOR: "🎓 Ментор",
    ROLE_MANAGER: "📋 Менеджер",
    ROLE_ANALYST: "📊 Аналитик",
    ROLE_ADMIN: "👑 Администратор",
}

# --- Capability-based доступ ---
MODULE_ACCESS = {
    "materials_crud": {ROLE_ADMIN, ROLE_MANAGER},
    "materials_stats": {ROLE_ADMIN, ROLE_ANALYST, ROLE_MANAGER},
    "events_crud": {ROLE_ADMIN, ROLE_MANAGER},
    "roles_crud": {ROLE_ADMIN},
    "bans_crud": {ROLE_ADMIN},
    "buddy_mentor": {ROLE_ADMIN, ROLE_MENTOR},
    "buddy_add": {ROLE_ADMIN, ROLE_MENTOR, ROLE_MANAGER},
    "buddy_assign": {ROLE_ADMIN, ROLE_MANAGER},
    "buddy_analytics": {ROLE_ADMIN, ROLE_ANALYST},
    "mocks": {ROLE_USER, ROLE_MENTOR, ROLE_MANAGER, ROLE_ANALYST, ROLE_ADMIN},
    "search": {ROLE_USER, ROLE_MENTOR, ROLE_MANAGER, ROLE_ANALYST, ROLE_ADMIN},
    "buddy_view": {ROLE_USER, ROLE_MENTOR, ROLE_MANAGER, ROLE_ANALYST, ROLE_ADMIN},
}


def expand_roles(role_keys: list[str]) -> set[str]:
    """Раскрыть bundles в гранулярные capability-роли."""
    result = set()
    for rk in role_keys:
        if rk in ROLE_BUNDLES:
            result.update(ROLE_BUNDLES[rk])
        else:
            result.add(rk)
    return result


def can_access(action: str, role_keys: list[str]) -> bool:
    """Проверить, есть ли у пользователя доступ к действию."""
    allowed = MODULE_ACCESS.get(action, set())
    granular = expand_roles(role_keys)
    return any(r in allowed for r in granular)


def get_role_priority(role_key: str) -> int:
    return ROLE_PRIORITIES.get(role_key, 0)


def get_max_priority(roles: list[str]) -> int:
    if not roles:
        return 0
    return max(get_role_priority(r) for r in roles)


def get_primary_role(roles: list[str]) -> str | None:
    if not roles:
        return None
    return max(roles, key=get_role_priority)


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
