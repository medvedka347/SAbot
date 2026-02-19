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
ROLES = [ROLE_USER, ROLE_MENTOR, ROLE_ADMIN]

# --- Стадии материалов ---
STAGE_FUNDAMENTAL = "fundamental"
STAGE_PRACTICAL_THEORY = "practical_theory"
STAGE_PRACTICAL_TASKS = "practical_tasks"
STAGE_ROADMAP = "roadmap"

STAGES = {
    STAGE_FUNDAMENTAL: "📚 Фундаментальная теория",
    STAGE_PRACTICAL_THEORY: "🔧 Практическая теория",
    STAGE_PRACTICAL_TASKS: "📝 Практические задания",
    STAGE_ROADMAP: "🗺️ Roadmap (info)",
}

# Проверка токена
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле!")
