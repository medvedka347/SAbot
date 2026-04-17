"""
Лёгкий state machine и фильтры для PTB.
"""
from telegram import Update
from telegram.ext import ContextTypes
from telegram.ext.filters import BaseFilter

MAX_HISTORY_LEN = 20


# ==================== State helpers ====================

def get_user_state(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return context.user_data.get("_state")


async def set_user_state(context: ContextTypes.DEFAULT_TYPE, state: str):
    current = context.user_data.get("_state")
    history = context.user_data.setdefault("_history", [])
    if current:
        history.append(current)
        if len(history) > MAX_HISTORY_LEN:
            history.pop(0)
    context.user_data["_state"] = state


async def clear_user_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("_state", None)
    context.user_data.pop("_history", None)


# ==================== Navigation handlers ====================

async def back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Универсальный обработчик кнопки 'Назад'."""
    from handlers.common import main_menu_handler

    history = context.user_data.get("_history", [])
    if history:
        prev = history.pop()
        context.user_data["_state"] = prev
        await update.message.reply_text("🔙 Назад")
        return
    await clear_user_state(context)
    await main_menu_handler(update, context)


async def main_menu_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сброс состояния и возврат в главное меню (для кнопки 🏠)."""
    from handlers.common import main_menu_handler

    await clear_user_state(context)
    await main_menu_handler(update, context)


# ==================== Custom PTB filter by state ====================

class StateFilter(BaseFilter):
    """Фильтр, который пропускает только если context.user_data['_state'] == заданное состояние."""

    __slots__ = ("state_name",)

    def __init__(self, state_name: str):
        self.state_name = state_name
        self.name = f"StateFilter({state_name})"

    async def __call__(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        return get_user_state(context) == self.state_name


def in_state(state_name: str) -> StateFilter:
    return StateFilter(state_name)
