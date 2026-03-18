"""
Модуль управления материалами.

Включает:
- Публичный просмотр материалов
- Админский CRUD (создание, чтение, обновление, удаление)
- Статистику по материалам
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import STAGES, MODULE_ACCESS
from db_utils import (
    get_materials, get_material, add_material, update_material, delete_material,
    get_materials_stats, HasRole
)
from utils import (
    check_rate_limit, kb, inline_kb, back_kb, stage_kb,
    get_stage_key, escape_md, safe_edit_text, get_main_keyboard,
    validate_callback_data
)
from audit_logger import (
    log_material_create, log_material_delete, log_material_update
)

router = Router(name="materials")


# ==================== FSM States ====================

class MaterialStates(StatesGroup):
    """Состояния для работы с материалами."""
    menu = State()                    # Главное меню управления материалами
    selecting_stage = State()         # Выбор stage (раздела) - админка
    selecting_stage_public = State()  # Выбор stage (раздела) - публичный просмотр
    selecting_item = State()          # Выбор конкретного материала
    input_title = State()             # Ввод названия
    input_link = State()              # Ввод ссылки
    input_desc = State()              # Ввод описания
    editing = State()                 # Редактирование материала


# ==================== Keyboards ====================

materials_menu_kb = kb(
    ["📖 Просмотреть", "➕ Добавить", "✏️ Редактировать", "🗑️ Удалить", "📊 Статистика"],
    "🔙 Назад"
)


# ==================== Helper Functions ====================

def format_material(mat: dict) -> str:
    """Форматирование материала для отображения."""
    desc = f"\n   📝 {escape_md(mat['description'][:50])}..." if mat.get('description') else ""
    return f"🔹 *ID:{mat['id']}* [{escape_md(mat['title'])}]({mat['link']}){desc}"


# ==================== Admin: Menu ====================

@router.message(F.text == "📦 Управление материалами", HasRole(min_priority=MODULE_ACCESS["materials"]))
async def materials_menu(message: Message, state: FSMContext):
    """Главное меню управления материалами."""
    # Блокируем вызов через reply на чужое сообщение
    if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
        await message.answer("❌ Нет прав.")
        return
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    await state.set_state(MaterialStates.menu)
    await message.answer("📦 *Управление материалами*", parse_mode="Markdown", reply_markup=materials_menu_kb)


# ==================== Admin: View ====================

@router.message(F.text == "📖 Просмотреть", MaterialStates.menu, HasRole(min_priority=MODULE_ACCESS["materials"]))
async def material_select_stage(message: Message, state: FSMContext):
    """Выбор stage для просмотра материалов."""
    # Блокируем вызов через reply на чужое сообщение
    if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
        await message.answer("❌ Нет прав.")
        return
    await state.set_state(MaterialStates.selecting_stage)
    await state.update_data(action="show_list")
    await message.answer("Выберите раздел:", reply_markup=stage_kb)


@router.message(MaterialStates.selecting_stage, HasRole(min_priority=MODULE_ACCESS["materials"]))
async def handle_stage_selection_admin(message: Message, state: FSMContext):
    """Обработка выбора stage в админке."""
    stage = get_stage_key(message.text)
    if not stage:
        return
    
    data = await state.get_data()
    action = data.get("action")
    
    if not action:
        await state.clear()
        await message.answer(
            "⚠️ Сессия устарела. Пожалуйста, начните сначала.",
            reply_markup=await get_main_keyboard(message.from_user.id)
        )
        return
    
    # Показываем список материалов
    if action == "show_list":
        mats = await get_materials(stage)
        stage_name = STAGES[stage]
        
        if not mats:
            text = f"📭 *{stage_name}*\n\nПока нет материалов.\n\n💡 Администратор скоро добавит 😊"
        else:
            lines = [format_material(m) for m in mats]
            text = f"📚 *{stage_name}* ({len(mats)})\n\n" + "\n".join(lines)
        
        await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)
        await materials_menu(message, state)
        return
    
    # Добавление материала
    if action == "add_material":
        await state.update_data(stage=stage)
        await state.set_state(MaterialStates.input_title)
        await message.answer("Введите название:", reply_markup=back_kb)
        return
    
    # Редактирование/удаление
    if action in ("select_for_edit", "select_for_delete"):
        cfg = {
            "select_for_edit": {"prefix": "", "action": "edit", "label": "Выберите материал:"},
            "select_for_delete": {"prefix": "🗑️ ", "action": "del", "label": "Выберите для удаления:"}
        }[action]
        
        mats = await get_materials(stage)
        if not mats:
            await message.answer("📭 Пусто", reply_markup=stage_kb)
            return
        
        await state.update_data(stage=stage)
        await state.set_state(MaterialStates.selecting_item)
        
        kb_inline = inline_kb([
            [InlineKeyboardButton(
                text=f"{cfg['prefix']}{m['id']}. {m['title'][:30]}",
                callback_data=f"{cfg['action']}_mat:{m['id']}"
            )] for m in mats
        ])
        
        await message.answer(cfg['label'], reply_markup=kb_inline)


# ==================== Admin: Create ====================

@router.message(F.text == "➕ Добавить", MaterialStates.menu, HasRole(min_priority=MODULE_ACCESS["materials"]))
async def material_add_start(message: Message, state: FSMContext):
    """Начало добавления материала."""
    # Блокируем вызов через reply на чужое сообщение
    if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
        await message.answer("❌ Нет прав.")
        return
    await state.set_state(MaterialStates.selecting_stage)
    await state.update_data(action="add_material")
    await message.answer("➕ Выберите раздел для добавления:", reply_markup=stage_kb)


@router.message(MaterialStates.input_title, HasRole(min_priority=MODULE_ACCESS["materials"]))
async def material_add_title(message: Message, state: FSMContext):
    """Получение названия материала."""
    if not message.text:
        return
    if len(message.text) > 200:
        await message.answer("❌ Название слишком длинное (макс 200 символов)")
        return
    
    await state.update_data(title=message.text)
    await state.set_state(MaterialStates.input_link)
    await message.answer("Введите ссылку (https://...):", reply_markup=back_kb)


@router.message(MaterialStates.input_link, HasRole(min_priority=MODULE_ACCESS["materials"]))
async def material_add_link(message: Message, state: FSMContext):
    """Получение ссылки на материал."""
    if not message.text:
        return
    
    link = message.text.strip()
    
    # Простая валидация URL
    if not (link.startswith('http://') or link.startswith('https://')):
        await message.answer("❌ Некорректная ссылка. Используйте формат: https://example.com/page")
        return
    
    await state.update_data(link=link)
    await state.set_state(MaterialStates.input_desc)
    await message.answer("Введите описание (или 'пропустить'):", reply_markup=back_kb)


@router.message(MaterialStates.input_desc, HasRole(min_priority=MODULE_ACCESS["materials"]))
async def material_add_desc(message: Message, state: FSMContext):
    """Получение описания и сохранение материала."""
    if not message.text:
        return
    
    desc = message.text.strip()
    if desc.lower() in ['пропустить', 'нет', '-']:
        desc = ""
    elif len(desc) > 1000:
        await message.answer("❌ Описание слишком длинное (макс 1000 символов)")
        return
    
    data = await state.get_data()
    mat_id = await add_material(data['stage'], data['title'], data['link'], desc)
    
    # Audit log
    log_material_create(
        user_id=message.from_user.id,
        mat_id=mat_id,
        title=data['title'],
        stage=data['stage']
    )
    
    await message.answer(f"✅ Добавлено в *{STAGES[data['stage']]}*!", parse_mode="Markdown")
    await materials_menu(message, state)


# ==================== Admin: Update ====================

@router.message(F.text == "✏️ Редактировать", MaterialStates.menu, HasRole(min_priority=MODULE_ACCESS["materials"]))
async def material_edit_select_stage(message: Message, state: FSMContext):
    """Выбор stage для редактирования."""
    # Блокируем вызов через reply на чужое сообщение
    if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
        await message.answer("❌ Нет прав.")
        return
    await state.set_state(MaterialStates.selecting_stage)
    await state.update_data(action="select_for_edit")
    await message.answer("✏️ Выберите раздел:", reply_markup=stage_kb)


@router.callback_query(F.data.startswith("edit_mat:"), HasRole(min_priority=MODULE_ACCESS["materials"]))
async def material_edit_callback(callback: CallbackQuery, state: FSMContext):
    """Callback для выбора материала на редактирование."""
    # Rate limit check
    ok, wait = check_rate_limit(callback.from_user.id)
    if not ok:
        await callback.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.", show_alert=True)
        return
    await callback.answer()
    
    # Validate callback data
    mat_id = validate_callback_data(callback.data, 'edit_mat', 'int')
    if mat_id is None:
        await safe_edit_text(callback, "❌ Некорректные данные")
        return
    
    mat = await get_material(mat_id)
    if not mat:
        await safe_edit_text(callback, "❌ Не найдено")
        return
    
    await state.update_data(
        edit_id=mat_id,
        edit_item=mat
    )
    await state.set_state(MaterialStates.editing)
    
    await safe_edit_text(
        callback,
        "✏️ Редактирование *{name}*\n\n"
        "Отправьте новые данные в формате:\n"
        "`название\\n\\nссылка\\n\\nописание`\n\n"
        "Используйте '.' для пропуска поля".format(name=mat['title']),
        parse_mode="Markdown"
    )


@router.message(MaterialStates.editing, HasRole(min_priority=MODULE_ACCESS["materials"]))
async def material_edit_process(message: Message, state: FSMContext):
    """Обработка редактирования материала."""
    if not message.text:
        return
    
    data = await state.get_data()
    mat_id = data.get('edit_id')
    
    if not mat_id:
        await state.clear()
        await message.answer(
            "⚠️ Сессия истекла. Начните сначала.",
            reply_markup=await get_main_keyboard(message.from_user.id)
        )
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


# ==================== Admin: Delete ====================

@router.message(F.text == "🗑️ Удалить", MaterialStates.menu, HasRole(min_priority=MODULE_ACCESS["materials"]))
async def material_delete_select_stage(message: Message, state: FSMContext):
    """Выбор stage для удаления."""
    # Блокируем вызов через reply на чужое сообщение
    if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
        await message.answer("❌ Нет прав.")
        return
    await state.set_state(MaterialStates.selecting_stage)
    await state.update_data(action="select_for_delete")
    await message.answer("🗑️ Выберите раздел:", reply_markup=stage_kb)


@router.callback_query(F.data.startswith("del_mat:"), HasRole(min_priority=MODULE_ACCESS["materials"]))
async def material_delete_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение удаления материала."""
    # Rate limit check
    ok, wait = check_rate_limit(callback.from_user.id)
    if not ok:
        await callback.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.", show_alert=True)
        return
    await callback.answer()
    
    try:
        mat_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await safe_edit_text(callback, "❌ Некорректные данные")
        return
    
    mat = await get_material(mat_id)
    if not mat:
        await safe_edit_text(callback, "❌ Материал не найден")
        return
    
    # Подтверждение удаления
    await callback.message.edit_text(
        f"🗑️ *Удалить материал?*\n\n"
        f"📚 {mat['title']}\n"
        f"🔗 {mat['link'][:50]}...\n\n"
        f"⚠️ Это действие нельзя отменить.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"conf_del_mat:{mat_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_del_mat")]
        ])
    )


@router.callback_query(F.data.startswith("conf_del_mat:"), HasRole(min_priority=MODULE_ACCESS["materials"]))
async def material_delete_execute(callback: CallbackQuery, state: FSMContext):
    """Выполнение удаления материала."""
    await callback.answer()
    
    try:
        mat_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await safe_edit_text(callback, "❌ Некорректные данные")
        return
    
    mat = await get_material(mat_id)
    if await delete_material(mat_id):
        # Audit log
        log_material_delete(
            user_id=callback.from_user.id,
            mat_id=mat_id,
            title=mat['title'] if mat else 'Unknown'
        )
        await safe_edit_text(callback, f"✅ Удалено: {mat['title'] if mat else mat_id}")
    else:
        await safe_edit_text(callback, "❌ Ошибка при удалении")
    
    # Возвращаемся в меню управления материалами
    await state.set_state(MaterialStates.menu)
    await callback.message.answer("📦 *Управление материалами*", parse_mode="Markdown", reply_markup=materials_menu_kb)


@router.callback_query(F.data == "cancel_del_mat", HasRole(min_priority=MODULE_ACCESS["materials"]))
async def material_delete_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена удаления материала."""
    await callback.answer("❌ Удаление отменено")
    await callback.message.edit_text("❌ Удаление отменено")
    
    # Возвращаемся в меню управления материалами
    await state.set_state(MaterialStates.menu)
    await callback.message.answer("📦 *Управление материалами*", parse_mode="Markdown", reply_markup=materials_menu_kb)


# ==================== Admin: Stats ====================

@router.message(F.text == "📊 Статистика", MaterialStates.menu, HasRole(min_priority=MODULE_ACCESS["materials"]))
async def material_stats(message: Message, state: FSMContext):
    """Показ статистики по материалам."""
    # Блокируем вызов через reply на чужое сообщение
    if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
        await message.answer("❌ Нет прав.")
        return
    stats = await get_materials_stats()
    total = sum(stats.values())
    text = f"📊 *Всего материалов: {total}*\n\n" + "\n".join(
        f"{STAGES[st]}: `{cnt}`" for st, cnt in stats.items()
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=materials_menu_kb)


# ==================== Public: View ====================

@router.message(F.text.in_(["📚 Материалы", "Материалы"]))
async def public_materials_select(message: Message, state: FSMContext):
    """Публичный просмотр материалов (для всех авторизованных)."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    
    await state.set_state(MaterialStates.selecting_stage_public)
    # Очищаем данные и возвращаемся в главное меню
    await message.answer(
        "📚 *Материалы*\n\n"
        "Выберите нужный раздел в меню ниже:\n"
        "• 📚 Фундаментальная теория\n"
        "• 🔧 Практическая теория\n"
        "• 📝 Практические задания\n"
        "• 🗺️ Прочие гайды",
        parse_mode="Markdown",
        reply_markup=stage_kb
    )


@router.message(MaterialStates.selecting_stage_public)
async def handle_stage_selection_public(message: Message, state: FSMContext):
    """Публичный просмотр материалов по stage."""
    stage = get_stage_key(message.text)
    if not stage:
        return
    
    # Показываем typing пока загружаем материалы
    await message.chat.do("typing")
    
    mats = await get_materials(stage)
    stage_name = STAGES[stage]
    
    if not mats:
        text = f"📭 *{stage_name}*\n\nПока нет материалов.\n\n💡 Загляните позже — мы добавляем новые материалы регулярно!"
    else:
        lines = [f"• [{m['title']}]({m['link']})" for m in mats]
        text = f"📚 *{stage_name}*\n\n" + "\n".join(lines)
    
    await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)
    
    # Оставляем возможность переключаться между разделами
    await state.set_state(MaterialStates.selecting_stage_public)
