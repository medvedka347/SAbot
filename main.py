import asyncio
import logging
import re
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from config import BOT_TOKEN, DB_NAME
from db_utils import init_db, setup_initial_users, cleanup_expired_bans
from utils import error_handler
from handlers.conversation_utils import back_handler, main_menu_fallback, in_state

from handlers import common, materials, events, roles, bans, mocks, search, buddy

from handlers.materials import (
    STATE_MATERIALS_MENU, STATE_MATERIALS_SELECTING_STAGE, STATE_MATERIALS_SELECTING_STAGE_PUBLIC,
    STATE_MATERIALS_SELECTING_ITEM, STATE_MATERIALS_INPUT_TITLE, STATE_MATERIALS_INPUT_LINK,
    STATE_MATERIALS_INPUT_DESC, STATE_MATERIALS_EDITING,
)
from handlers.events import (
    STATE_EVENTS_MENU, STATE_EVENTS_SELECTING_ITEM, STATE_EVENTS_INPUT_TYPE, STATE_EVENTS_INPUT_DATETIME,
    STATE_EVENTS_INPUT_LINK, STATE_EVENTS_INPUT_ANNOUNCEMENT, STATE_EVENTS_CONFIRM_ANNOUNCE, STATE_EVENTS_EDITING,
)
from handlers.roles import (
    STATE_ROLES_MENU, STATE_ROLES_INPUT_USERS, STATE_ROLES_SELECTING_ROLE, STATE_ROLES_SELECTING_USER_TO_DELETE,
)
from handlers.buddy import (
    STATE_BUDDY_INPUT_FULL_NAME, STATE_BUDDY_INPUT_TELEGRAM_TAG, STATE_BUDDY_INPUT_ASSIGNED_DATE,
)

logging.basicConfig(level=logging.INFO)


async def post_init(application: Application):
    await init_db(DB_NAME)
    await setup_initial_users(DB_NAME)


async def periodic_cleanup(context):
    await cleanup_expired_bans()


def main():
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # ========== Commands (highest priority) ==========
    application.add_handler(CommandHandler("start", common.start_handler))
    application.add_handler(CommandHandler("help", common.help_handler))
    application.add_handler(CommandHandler("search", search.search_handler))

    # ========== Group commands ==========
    application.add_handler(CommandHandler("events", search.group_events_handler))
    application.add_handler(CommandHandler("sabot_help", search.group_help_handler))
    application.add_handler(CommandHandler("material", search.group_material_handler))
    application.add_handler(CommandHandler(["off", "remove_kb"], search.group_remove_keyboard))

    # ========== Buddy: callbacks & panels ==========
    application.add_handler(MessageHandler(filters.Regex(r'^🤝 Buddy$'), common.buddy_handler))
    application.add_handler(MessageHandler(filters.Regex(r'^📊 Список менторов$'), buddy.buddy_analytics_mentors))
    application.add_handler(MessageHandler(filters.Regex(r'^📋 Все менти$'), buddy.buddy_analytics_all_mentees))
    application.add_handler(MessageHandler(filters.Regex(r'^➕ Назначить бадди$'), buddy.buddy_manager_assign))

    application.add_handler(CallbackQueryHandler(buddy.buddy_show_mentee, pattern=r'^buddy_mentee:'))
    application.add_handler(CallbackQueryHandler(buddy.buddy_change_status_start, pattern=r'^buddy_chstatus:'))
    application.add_handler(CallbackQueryHandler(buddy.buddy_set_status, pattern=r'^buddy_status:'))
    application.add_handler(CallbackQueryHandler(buddy.buddy_delete_mentee, pattern=r'^buddy_del:'))
    application.add_handler(CallbackQueryHandler(buddy.buddy_confirm_delete, pattern=r'^buddy_conf_del:'))
    application.add_handler(CallbackQueryHandler(buddy.buddy_back_to_list, pattern=r'^buddy_back_to_list$'))
    application.add_handler(CallbackQueryHandler(buddy.buddy_analytics_mentor_details, pattern=r'^buddy_report:'))
    application.add_handler(CallbackQueryHandler(buddy.buddy_analytics_back, pattern=r'^buddy_report_back$'))
    application.add_handler(CallbackQueryHandler(buddy.buddy_manager_select_mentor, pattern=r'^buddy_mgr_sel:'))

    # ========== Admin entry points ==========
    application.add_handler(MessageHandler(filters.Regex(r'^⚙️ Админка$'), common.admin_handler))
    application.add_handler(MessageHandler(filters.Regex(r'^📦 Управление материалами$'), materials.materials_menu))
    application.add_handler(MessageHandler(filters.Regex(r'^📋 Управление событиями$'), events.events_menu))
    application.add_handler(MessageHandler(filters.Regex(r'^👥 Управление ролями$'), roles.roles_menu))
    application.add_handler(MessageHandler(filters.Regex(r'^🚫 Управление банами$'), bans.bans_menu))

    # ========== Materials CRUD ==========
    application.add_handler(MessageHandler(in_state(STATE_MATERIALS_MENU) & filters.Regex(r'^📖 Просмотреть$'), materials.material_select_stage))
    application.add_handler(MessageHandler(in_state(STATE_MATERIALS_MENU) & filters.Regex(r'^➕ Добавить$'), materials.material_add_start))
    application.add_handler(MessageHandler(in_state(STATE_MATERIALS_MENU) & filters.Regex(r'^✏️ Редактировать$'), materials.material_edit_select_stage))
    application.add_handler(MessageHandler(in_state(STATE_MATERIALS_MENU) & filters.Regex(r'^🗑️ Удалить$'), materials.material_delete_select_stage))
    application.add_handler(MessageHandler(in_state(STATE_MATERIALS_MENU) & filters.Regex(r'^📊 Статистика$'), materials.material_stats))
    application.add_handler(MessageHandler(in_state(STATE_MATERIALS_SELECTING_STAGE) & filters.TEXT & ~filters.COMMAND, materials.handle_stage_selection_admin))
    application.add_handler(MessageHandler(in_state(STATE_MATERIALS_INPUT_TITLE) & filters.TEXT & ~filters.COMMAND, materials.material_add_title))
    application.add_handler(MessageHandler(in_state(STATE_MATERIALS_INPUT_LINK) & filters.TEXT & ~filters.COMMAND, materials.material_add_link))
    application.add_handler(MessageHandler(in_state(STATE_MATERIALS_INPUT_DESC) & filters.TEXT & ~filters.COMMAND, materials.material_add_desc))
    application.add_handler(MessageHandler(in_state(STATE_MATERIALS_EDITING) & filters.TEXT & ~filters.COMMAND, materials.material_edit_process))
    application.add_handler(MessageHandler(in_state(STATE_MATERIALS_SELECTING_STAGE_PUBLIC) & filters.TEXT & ~filters.COMMAND, materials.handle_stage_selection_public))
    application.add_handler(CallbackQueryHandler(materials.material_edit_callback, pattern=r'^edit_mat:'))
    application.add_handler(CallbackQueryHandler(materials.material_delete_confirm_callback, pattern=r'^del_mat:'))
    application.add_handler(CallbackQueryHandler(materials.material_delete_execute_callback, pattern=r'^conf_del_mat:'))
    application.add_handler(CallbackQueryHandler(materials.material_delete_cancel_callback, pattern=r'^cancel_del_mat$'))

    # ========== Events CRUD ==========
    application.add_handler(MessageHandler(in_state(STATE_EVENTS_MENU) & filters.Regex(r'^📖 Просмотреть$'), events.events_show_all))
    application.add_handler(MessageHandler(in_state(STATE_EVENTS_MENU) & filters.Regex(r'^➕ Добавить$'), events.event_add_start))
    application.add_handler(MessageHandler(in_state(STATE_EVENTS_MENU) & filters.Regex(r'^✏️ Редактировать$'), events.event_edit_select))
    application.add_handler(MessageHandler(in_state(STATE_EVENTS_MENU) & filters.Regex(r'^🗑️ Удалить$'), events.event_delete_select))
    application.add_handler(MessageHandler(in_state(STATE_EVENTS_INPUT_TYPE) & filters.TEXT & ~filters.COMMAND, events.event_add_type))
    application.add_handler(MessageHandler(in_state(STATE_EVENTS_INPUT_DATETIME) & filters.TEXT & ~filters.COMMAND, events.event_add_datetime))
    application.add_handler(MessageHandler(in_state(STATE_EVENTS_INPUT_LINK) & filters.TEXT & ~filters.COMMAND, events.event_add_link))
    application.add_handler(MessageHandler(in_state(STATE_EVENTS_INPUT_ANNOUNCEMENT) & filters.TEXT & ~filters.COMMAND, events.event_add_announcement))
    application.add_handler(MessageHandler(in_state(STATE_EVENTS_CONFIRM_ANNOUNCE) & filters.TEXT & ~filters.COMMAND, events.event_confirm_announce))
    application.add_handler(MessageHandler(in_state(STATE_EVENTS_EDITING) & filters.TEXT & ~filters.COMMAND, events.event_edit_process))
    application.add_handler(CallbackQueryHandler(events.event_edit_callback, pattern=r'^edit_ev:'))
    application.add_handler(CallbackQueryHandler(events.event_delete_confirm_callback, pattern=r'^del_ev:'))
    application.add_handler(CallbackQueryHandler(events.event_delete_execute_callback, pattern=r'^conf_del_ev:'))
    application.add_handler(CallbackQueryHandler(events.event_delete_cancel_callback, pattern=r'^cancel_del_ev$'))

    # ========== Roles CRUD ==========
    application.add_handler(MessageHandler(in_state(STATE_ROLES_MENU) & filters.Regex(r'^📋 Список пользователей$'), roles.roles_show))
    application.add_handler(MessageHandler(in_state(STATE_ROLES_MENU) & filters.Regex(r'^➕ Назначить роль$'), roles.role_add_start))
    application.add_handler(MessageHandler(in_state(STATE_ROLES_MENU) & filters.Regex(r'^🗑️ Удалить пользователя$'), roles.role_delete_start))
    application.add_handler(MessageHandler(in_state(STATE_ROLES_INPUT_USERS) & filters.TEXT & ~filters.COMMAND, roles.role_receive_users))
    application.add_handler(CallbackQueryHandler(roles.role_set_confirm, pattern=r'^set_role:'))
    application.add_handler(CallbackQueryHandler(roles.role_set_execute, pattern=r'^conf_set_role$'))
    application.add_handler(CallbackQueryHandler(roles.role_set_cancel, pattern=r'^cancel_set_role$'))
    application.add_handler(CallbackQueryHandler(roles.users_page_callback, pattern=r'^users_page:'))
    application.add_handler(CallbackQueryHandler(roles.role_delete_confirm, pattern=r'^del_user:'))
    application.add_handler(CallbackQueryHandler(roles.role_delete_execute, pattern=r'^conf_del_user$'))
    application.add_handler(CallbackQueryHandler(roles.role_delete_cancel, pattern=r'^cancel_del_user$'))

    # ========== Bans ==========
    application.add_handler(CallbackQueryHandler(bans.ban_unban_callback, pattern=r'^unban:'))

    # ========== Buddy add flow ==========
    application.add_handler(MessageHandler(filters.Regex(r'^➕ Добавить менти$'), buddy.buddy_add_start))
    application.add_handler(MessageHandler(in_state(STATE_BUDDY_INPUT_FULL_NAME) & filters.TEXT & ~filters.COMMAND, buddy.buddy_add_full_name))
    application.add_handler(MessageHandler(in_state(STATE_BUDDY_INPUT_TELEGRAM_TAG) & filters.TEXT & ~filters.COMMAND, buddy.buddy_add_telegram_tag))
    application.add_handler(MessageHandler(in_state(STATE_BUDDY_INPUT_ASSIGNED_DATE) & filters.TEXT & ~filters.COMMAND, buddy.buddy_add_date))

    # ========== Mocks ==========
    application.add_handler(MessageHandler(filters.Regex(r'^⏱️ Записаться на мок$'), mocks.booking_handler))
    from config import MOCK_MENTORS
    mentor_names = [re.escape(name) for name in MOCK_MENTORS.keys()]
    mock_pattern = rf'^👤? ?(?:{"|".join(mentor_names)})$'
    application.add_handler(MessageHandler(filters.Regex(mock_pattern), mocks.mock_select_handler))

    # ========== Public entries ==========
    application.add_handler(MessageHandler(filters.Regex(r'^📚 Материалы$'), materials.public_materials_select))
    application.add_handler(MessageHandler(filters.Regex(r'^📅 События комьюнити$'), events.public_events_show))

    # ========== Navigation ==========
    application.add_handler(MessageHandler(filters.Regex(r'^🏠 Главное меню$'), main_menu_fallback))
    application.add_handler(MessageHandler(filters.Regex(r'^🔙 Назад$'), back_handler))

    # ========== Fallback ==========
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, common.fallback_handler))

    # ========== Error & Jobs ==========
    application.add_error_handler(error_handler)
    application.job_queue.run_repeating(periodic_cleanup, interval=3600, first=3600)

    application.run_polling()


if __name__ == "__main__":
    main()
