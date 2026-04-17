"""
Модуль управления материалами.
"""
import logging
from telegram import Update, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import STAGES, MODULE_ACCESS
from db_utils import require_any_role, get_materials, get_material, add_material, update_material, delete_material, get_materials_stats
from utils import check_rate_limit, kb, inline_kb, back_kb, stage_kb, get_stage_key, escape_md, get_main_keyboard
from audit_logger import log_material_create, log_material_delete
from handlers.conversation_utils import get_user_state, set_user_state, clear_user_state

# States
STATE_MATERIALS_MENU = "materials_menu"
STATE_MATERIALS_SELECTING_STAGE = "materials_selecting_stage"
STATE_MATERIALS_SELECTING_STAGE_PUBLIC = "materials_selecting_stage_public"
STATE_MATERIALS_SELECTING_ITEM = "materials_selecting_item"
STATE_MATERIALS_INPUT_TITLE = "materials_input_title"
STATE_MATERIALS_INPUT_LINK = "materials_input_link"
STATE_MATERIALS_INPUT_DESC = "materials_input_desc"
STATE_MATERIALS_EDITING = "materials_editing"

materials_menu_kb = kb(
    ["📖 Просмотреть", "➕ Добавить", "✏️ Редактировать", "🗑️ Удалить", "📊 Статистика"],
    "🔙 Назад"
)


async def materials_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth = await require_any_role(update, context, MODULE_ACCESS["materials_crud"])
    if not auth:
        return
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Нет прав.")
        return
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    await set_user_state(context, STATE_MATERIALS_MENU)
    await update.message.reply_text("📦 *Управление материалами*", parse_mode="Markdown", reply_markup=materials_menu_kb)


async def material_select_stage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_MATERIALS_MENU:
        return
    auth = await require_any_role(update, context, MODULE_ACCESS["materials_crud"])
    if not auth:
        return
    await set_user_state(context, STATE_MATERIALS_SELECTING_STAGE)
    context.user_data["materials_action"] = "show_list"
    await update.message.reply_text("Выберите раздел:", reply_markup=stage_kb)


async def handle_stage_selection_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_MATERIALS_SELECTING_STAGE:
        return
    stage = get_stage_key(update.message.text)
    if not stage:
        return
    action = context.user_data.get("materials_action")
    if not action:
        await clear_user_state(context)
        await update.message.reply_text(
            "⚠️ Сессия устарела. Пожалуйста, начните сначала.",
            reply_markup=await get_main_keyboard(update.effective_user.id)
        )
        return
    if action == "show_list":
        mats = await get_materials(stage)
        stage_name = STAGES[stage]
        if not mats:
            text = f"📭 *{stage_name}*\n\nПока нет материалов.\n\n💡 Администратор скоро добавит 😊"
        else:
            lines = [format_material(m) for m in mats]
            text = f"📚 *{stage_name}* ({len(mats)})\n\n" + "\n".join(lines)
        await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)
        await materials_menu(update, context)
        return
    if action == "add_material":
        context.user_data["materials_stage"] = stage
        await set_user_state(context, STATE_MATERIALS_INPUT_TITLE)
        await update.message.reply_text("Введите название:", reply_markup=back_kb)
        return
    if action in ("select_for_edit", "select_for_delete"):
        cfg = {
            "select_for_edit": {"prefix": "", "action": "edit", "label": "Выберите материал:"},
            "select_for_delete": {"prefix": "🗑️ ", "action": "del", "label": "Выберите для удаления:"}
        }[action]
        mats = await get_materials(stage)
        if not mats:
            await update.message.reply_text("📭 Пусто", reply_markup=stage_kb)
            return
        context.user_data["materials_stage"] = stage
        await set_user_state(context, STATE_MATERIALS_SELECTING_ITEM)
        kb_inline = inline_kb([
            [InlineKeyboardButton(
                text=f"{cfg['prefix']}{m['id']}. {m['title'][:30]}",
                callback_data=f"{cfg['action']}_mat:{m['id']}"
            )] for m in mats
        ])
        await update.message.reply_text(cfg['label'], reply_markup=kb_inline)


async def material_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_MATERIALS_MENU:
        return
    context.user_data["materials_action"] = "add_material"
    await set_user_state(context, STATE_MATERIALS_SELECTING_STAGE)
    await update.message.reply_text("➕ Выберите раздел для добавления:", reply_markup=stage_kb)


async def material_add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_MATERIALS_INPUT_TITLE:
        return
    if not update.message.text:
        return
    if len(update.message.text) > 200:
        await update.message.reply_text("❌ Название слишком длинное (макс 200 символов)")
        return
    context.user_data["materials_title"] = update.message.text
    await set_user_state(context, STATE_MATERIALS_INPUT_LINK)
    await update.message.reply_text("Введите ссылку (https://...):", reply_markup=back_kb)


async def material_add_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_MATERIALS_INPUT_LINK:
        return
    if not update.message.text:
        return
    link = update.message.text.strip()
    if not (link.startswith('http://') or link.startswith('https://')):
        await update.message.reply_text("❌ Некорректная ссылка. Используйте формат: https://example.com/page")
        return
    context.user_data["materials_link"] = link
    await set_user_state(context, STATE_MATERIALS_INPUT_DESC)
    await update.message.reply_text("Введите описание (или 'пропустить'):", reply_markup=back_kb)


async def material_add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_MATERIALS_INPUT_DESC:
        return
    if not update.message.text:
        return
    desc = update.message.text.strip()
    if desc.lower() in ['пропустить', 'нет', '-']:
        desc = ""
    elif len(desc) > 1000:
        await update.message.reply_text("❌ Описание слишком длинное (макс 1000 символов)")
        return
    data = context.user_data
    stage = data.get("materials_stage")
    title = data.get("materials_title")
    link = data.get("materials_link")
    mat_id = await add_material(stage, title, link, desc)
    log_material_create(
        user_id=update.effective_user.id,
        mat_id=mat_id,
        title=title,
        stage=stage
    )
    await update.message.reply_text(f"✅ Добавлено в *{STAGES[stage]}*!", parse_mode="Markdown")
    await materials_menu(update, context)


async def material_edit_select_stage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_MATERIALS_MENU:
        return
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Нет прав.")
        return
    context.user_data["materials_action"] = "select_for_edit"
    await set_user_state(context, STATE_MATERIALS_SELECTING_STAGE)
    await update.message.reply_text("✏️ Выберите раздел:", reply_markup=stage_kb)


async def material_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if get_user_state(context) != STATE_MATERIALS_SELECTING_ITEM:
        await query.edit_message_text("❌ Сессия устарела")
        return
    ok, wait = check_rate_limit(query.from_user.id)
    if not ok:
        await query.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.", show_alert=True)
        return
    try:
        mat_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    mat = await get_material(mat_id)
    if not mat:
        await query.edit_message_text("❌ Не найдено")
        return
    context.user_data["materials_edit_id"] = mat_id
    context.user_data["materials_edit_item"] = mat
    await set_user_state(context, STATE_MATERIALS_EDITING)
    await query.edit_message_text(
        "✏️ Редактирование *{name}*\n\n"
        "Отправьте новые данные в формате:\n"
        "`название\n\nссылка\n\nописание`\n\n"
        "Используйте '.' для пропуска поля".format(name=mat['title']),
        parse_mode="Markdown"
    )


async def material_edit_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_MATERIALS_EDITING:
        return
    if not update.message.text:
        return
    mat_id = context.user_data.get("materials_edit_id")
    if not mat_id:
        await clear_user_state(context)
        await update.message.reply_text(
            "⚠️ Сессия истекла. Начните сначала.",
            reply_markup=await get_main_keyboard(update.effective_user.id)
        )
        return
    parts = [p.strip() for p in update.message.text.split('\n\n') if p.strip()]
    updates = {}
    if parts and parts[0] != '.':
        updates['title'] = parts[0]
    if len(parts) > 1 and parts[1] != '.':
        updates['link'] = parts[1]
    if len(parts) > 2 and parts[2] != '.':
        updates['description'] = parts[2]
    if updates:
        await update_material(mat_id, **updates)
        await update.message.reply_text("✅ Обновлено!")
    else:
        await update.message.reply_text("❌ Ничего не изменено")
    await materials_menu(update, context)


async def material_delete_select_stage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_MATERIALS_MENU:
        return
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Нет прав.")
        return
    context.user_data["materials_action"] = "select_for_delete"
    await set_user_state(context, STATE_MATERIALS_SELECTING_STAGE)
    await update.message.reply_text("🗑️ Выберите раздел:", reply_markup=stage_kb)


async def material_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if get_user_state(context) != STATE_MATERIALS_SELECTING_ITEM:
        await query.edit_message_text("❌ Сессия устарела")
        return
    try:
        mat_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    mat = await get_material(mat_id)
    if not mat:
        await query.edit_message_text("❌ Материал не найден")
        return
    kb_inline = inline_kb([
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"conf_del_mat:{mat_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_del_mat")]
    ])
    await query.edit_message_text(
        f"🗑️ *Удалить материал?*\n\n"
        f"📚 {mat['title']}\n"
        f"🔗 {mat['link'][:50]}...\n\n"
        f"⚠️ Это действие нельзя отменить.",
        parse_mode="Markdown",
        reply_markup=kb_inline
    )


async def material_delete_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        mat_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректные данные")
        return
    mat = await get_material(mat_id)
    if await delete_material(mat_id):
        log_material_delete(
            user_id=query.from_user.id,
            mat_id=mat_id,
            title=mat['title'] if mat else 'Unknown'
        )
        await query.edit_message_text(f"✅ Удалено: {mat['title'] if mat else mat_id}")
    else:
        await query.edit_message_text("❌ Ошибка при удалении")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📦 *Управление материалами*",
        parse_mode="Markdown",
        reply_markup=materials_menu_kb
    )
    await set_user_state(context, STATE_MATERIALS_MENU)


async def material_delete_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("❌ Удаление отменено")
    await query.edit_message_text("❌ Удаление отменено")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📦 *Управление материалами*",
        parse_mode="Markdown",
        reply_markup=materials_menu_kb
    )
    await set_user_state(context, STATE_MATERIALS_MENU)


async def material_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_MATERIALS_MENU:
        return
    auth = await require_any_role(update, context, MODULE_ACCESS["materials_stats"])
    if not auth:
        return
    stats = await get_materials_stats()
    total = sum(stats.values())
    text = f"📊 *Всего материалов: {total}*\n\n" + "\n".join(
        f"{STAGES[st]}: `{cnt}`" for st, cnt in stats.items()
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=materials_menu_kb)


# ==================== Public ====================

async def public_materials_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, wait = check_rate_limit(update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    await set_user_state(context, STATE_MATERIALS_SELECTING_STAGE_PUBLIC)
    await update.message.reply_text(
        "📚 *Материалы*\n\n"
        "Выберите нужный раздел в меню ниже:\n"
        "• 📚 Фундаментальная теория\n"
        "• 🔧 Практическая теория\n"
        "• 📝 Практические задания\n"
        "• 🗺️ Прочие гайды",
        parse_mode="Markdown",
        reply_markup=stage_kb
    )


async def handle_stage_selection_public(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_state(context) != STATE_MATERIALS_SELECTING_STAGE_PUBLIC:
        return
    stage = get_stage_key(update.message.text)
    if not stage:
        return
    await update.effective_chat.send_action(action="typing")
    mats = await get_materials(stage)
    stage_name = STAGES[stage]
    if not mats:
        text = f"📭 *{stage_name}*\n\nПока нет материалов.\n\n💡 Загляните позже — мы добавляем новые материалы регулярно!"
    else:
        lines = [f"• [{m['title']}]({m['link']})" for m in mats]
        text = f"📚 *{stage_name}*\n\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)


def format_material(mat: dict) -> str:
    desc = f"\n   📝 {escape_md(mat['description'][:50])}..." if mat.get('description') else ""
    return f"🔹 *ID:{mat['id']}* [{escape_md(mat['title'])}]({mat['link']}){desc}"
