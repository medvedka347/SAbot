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
from aiogram.types import InlineKeyboardButton

from config import STAGES, ROLE_ADMIN
from db_utils import (
    get_materials, get_material, add_material, update_material, delete_material,
    get_materials_stats, HasRole
)
from utils import (
    check_rate_limit, kb, inline_kb, back_kb, stage_kb,
    get_stage_key, escape_md, safe_edit_text, get_main_keyboard
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

@router.message(F.text == "📦 Управление материалами", HasRole(ROLE_ADMIN))
async def materials_menu(message: Message, state: FSMContext):
    """Главное меню управления материалами."""
    ok, wait = check_rate_limit(message.from_user.id)
    if not ok:
        await message.answer(f"⏱️ Слишком быстро! Подождите {wait} сек.")
        return
    await state.set_state(MaterialStates.menu)
    await message.answer("📦 *Управление материалами*", parse_mode="Markdown", reply_markup=materials_menu_kb)


# ==================== Admin: View ====================

@router.message(F.text == "📖 Просмотреть", MaterialStates.menu, HasRole(ROLE_ADMIN))
async def material_select_stage(message: Message, state: FSMContext):
    """Выбор stage для просмотра материалов."""
    await state.set_state(MaterialStates.selecting_stage)
    await state.update_data(action="show_list")
    await message.answer("Выберите раздел:", reply_markup=stage_kb)


@router.message(MaterialStates.selecting_stage, HasRole(ROLE_ADMIN))
async def handle_stage_selection_admin(message: Message, state: FSMContext):
    """Обработка выбора stage в админке."""
    # Проверяем кнопку "Назад" первым делом
    if message.text and ("Назад" in message.text or message.text == "Назад"):
        await state.clear()
        welcome = f"Привет, {message.from_user.first_name}! 👋\n\nРоль: *admin*"
        kb = await get_main_keyboard(message.from_user.id)
        await message.answer(welcome, parse_mode="Markdown", reply_markup=kb)
        return
    
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
            text = f"📭 *{stage_name}*\n\nПусто."
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

@router.message(F.text == "➕ Добавить", MaterialStates.menu, HasRole(ROLE_ADMIN))
async def material_add_start(message: Message, state: FSMContext):
    """Начало добавления материала."""
    await state.set_state(MaterialStates.selecting_stage)
    await state.update_data(action="add_material")
    await message.answer("➕ Выберите раздел для добавления:", reply_markup=stage_kb)


@router.message(MaterialStates.input_title, HasRole(ROLE_ADMIN))
async def material_add_title(message: Message, state: FSMContext):
    """Получение названия материала."""
    if not message.text:
        return
    # Проверяем кнопку "Назад"
    if "Назад" in message.text:
        await materials_menu(message, state)
        return
    if len(message.text) > 200:
        await message.answer("❌ Название слишком длинное (макс 200 символов)")
        return
    
    await state.update_data(title=message.text)
    await state.set_state(MaterialStates.input_link)
    await message.answer("Введите ссылку (https://...):", reply_markup=back_kb)


@router.message(MaterialStates.input_link, HasRole(ROLE_ADMIN))
async def material_add_link(message: Message, state: FSMContext):
    """Получение ссылки на материал."""
    if not message.text:
        return
    # Проверяем кнопку "Назад"
    if "Назад" in message.text:
        await materials_menu(message, state)
        return
    
    link = message.text.strip()
    
    # Простая валидация URL
    if not (link.startswith('http://') or link.startswith('https://')):
        await message.answer("❌ Некорректная ссылка. Используйте формат: https://example.com/page")
        return
    
    await state.update_data(link=link)
    await state.set_state(MaterialStates.input_desc)
    await message.answer("Введите описание (или 'пропустить'):", reply_markup=back_kb)


@router.message(MaterialStates.input_desc, HasRole(ROLE_ADMIN))
async def material_add_desc(message: Message, state: FSMContext):
    """Получение описания и сохранение материала."""
    if not message.text:
        return
    # Проверяем кнопку "Назад"
    if "Назад" in message.text:
        await materials_menu(message, state)
        return
    
    desc = message.text.strip()
    if desc.lower() in ['пропустить', 'нет', '-']:
        desc = ""
    elif len(desc) > 1000:
        await message.answer("❌ Описание слишком длинное (макс 1000 символов)")
        return
    
    data = await state.get_data()
    await add_material(data['stage'], data['title'], data['link'], desc)
    await message.answer(f"✅ Добавлено в *{STAGES[data['stage']]}*!", parse_mode="Markdown")
    await materials_menu(message, state)


# ==================== Admin: Update ====================

@router.message(F.text == "✏️ Редактировать", MaterialStates.menu, HasRole(ROLE_ADMIN))
async def material_edit_select_stage(message: Message, state: FSMContext):
    """Выбор stage для редактирования."""
    await state.set_state(MaterialStates.selecting_stage)
    await state.update_data(action="select_for_edit")
    await message.answer("✏️ Выберите раздел:", reply_markup=stage_kb)


@router.callback_query(F.data.startswith("edit_mat:"), HasRole(ROLE_ADMIN))
async def material_edit_callback(callback: CallbackQuery, state: FSMContext):
    """Callback для выбора материала на редактирование."""
    await callback.answer()
    
    try:
        mat_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await safe_edit_text(callback, "❌ Некорректные данные")
        return
    
    mat = await get_material(mat_id)
    if not mat:
        await safe_edit_text(callback, "❌ Не найдено")
        return
    
    await state.update_data(edit_id=mat_id, edit_item=mat)
    await state.set_state(MaterialStates.editing)
    
    await safe_edit_text(
        callback,
        "✏️ Редактирование *{name}*\n\n"
        "Отправьте новые данные в формате:\n"
        "`название\\n\\nссылка\\n\\nописание`\n\n"
        "Используйте '.' для пропуска поля".format(name=mat['title']),
        parse_mode="Markdown"
    )


@router.message(MaterialStates.editing, HasRole(ROLE_ADMIN))
async def material_edit_process(message: Message, state: FSMContext):
    """Обработка редактирования материала."""
    if not message.text:
        return
    # Проверяем кнопку "Назад"
    if "Назад" in message.text:
        await materials_menu(message, state)
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

@router.message(F.text == "🗑️ Удалить", MaterialStates.menu, HasRole(ROLE_ADMIN))
async def material_delete_select_stage(message: Message, state: FSMContext):
    """Выбор stage для удаления."""
    await state.set_state(MaterialStates.selecting_stage)
    await state.update_data(action="select_for_delete")
    await message.answer("🗑️ Выберите раздел:", reply_markup=stage_kb)


@router.callback_query(F.data.startswith("del_mat:"), HasRole(ROLE_ADMIN))
async def material_delete_callback(callback: CallbackQuery, state: FSMContext):
    """Callback для удаления материала."""
    await callback.answer()
    
    try:
        mat_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await safe_edit_text(callback, "❌ Некорректные данные")
        return
    
    mat = await get_material(mat_id)
    if await delete_material(mat_id):
        await safe_edit_text(callback, f"✅ Удалено: {mat['title'] if mat else mat_id}")
    else:
        await safe_edit_text(callback, "❌ Ошибка")
    
    await state.clear()


# ==================== Admin: Stats ====================

@router.message(F.text == "📊 Статистика", MaterialStates.menu, HasRole(ROLE_ADMIN))
async def material_stats(message: Message, state: FSMContext):
    """Показ статистики по материалам."""
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
    await message.answer(
        "📚 *Материалы*\n\n"
        "Выберите нужный раздел в меню ниже:\n"
        "• 📚 Фундаментальная теория\n"
        "• 🔧 Практическая теория\n"
        "• 📝 Практические задания\n"
        "• 🗺️ Roadmap (info)",
        parse_mode="Markdown",
        reply_markup=stage_kb
    )


@router.message(MaterialStates.selecting_stage_public)
async def handle_stage_selection_public(message: Message, state: FSMContext):
    """Публичный просмотр материалов по stage."""
    # Проверяем кнопку "Назад" первым делом
    if message.text and ("Назад" in message.text or message.text == "Назад"):
        await state.clear()
        from utils import get_main_keyboard
        from config import ROLE_ADMIN, ROLE_MENTOR
        from db_utils import get_user_role
        
        role = await get_user_role(user_id=message.from_user.id, username=message.from_user.username)
        welcome = f"Привет, {message.from_user.first_name}! 👋\n\nРоль: *{role}*"
        kb = await get_main_keyboard(message.from_user.id)
        await message.answer(welcome, parse_mode="Markdown", reply_markup=kb)
        return
    
    stage = get_stage_key(message.text)
    if not stage:
        return
    
    mats = await get_materials(stage)
    stage_name = STAGES[stage]
    
    if not mats:
        text = f"📭 *{stage_name}*\n\nПока пусто."
    else:
        lines = [f"• [{m['title']}]({m['link']})" for m in mats]
        text = f"📚 *{stage_name}*\n\n" + "\n".join(lines)
    
    await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)
    
    # Оставляем возможность переключаться между разделами
    await state.set_state(MaterialStates.selecting_stage_public)
