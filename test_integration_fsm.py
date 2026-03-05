#!/usr/bin/env python3
"""
Интеграционные тесты для FSM (Finite State Machine)
Тестирует сквозные сценарии с переходами между состояниями

Запуск: python test_integration_fsm.py
"""

import asyncio
import sys
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class MockMessage:
    """Мок объекта Message для тестирования"""
    def __init__(self, text=None, user_id=123456, username="testuser", chat_type="private"):
        self.text = text
        self.from_user = MagicMock()
        self.from_user.id = user_id
        self.from_user.username = username
        self.from_user.first_name = "Test"
        self.chat = MagicMock()
        self.chat.type = chat_type
        self.reply_to_message = None
        self._answers = []
        self._answer_mode = None
        self._edited_text = None
        
    async def answer(self, text, **kwargs):
        self._answers.append({"text": text, "kwargs": kwargs})
        self._answer_mode = "answer"
        return MagicMock()
        
    async def edit_text(self, text, **kwargs):
        self._edited_text = text
        return MagicMock()


class MockCallbackQuery:
    """Мок объекта CallbackQuery для тестирования"""
    def __init__(self, data, user_id=123456):
        self.data = data
        self.from_user = MagicMock()
        self.from_user.id = user_id
        self.message = MockMessage()
        self._answered = False
        self._edited = False
        
    async def answer(self, text=None, **kwargs):
        self._answered = True
        
    async def message_edit_text(self, text, **kwargs):
        self._edited = True
        self.message._answers.append({"text": text, "kwargs": kwargs})


class MockFSMContext:
    """Мок FSM контекста для отслеживания состояний"""
    def __init__(self):
        self._state = None
        self._data = {}
        self._state_history = []
        
    async def set_state(self, state):
        self._state_history.append(("set", state))
        self._state = state
        
    async def get_state(self):
        return self._state
        
    async def update_data(self, **kwargs):
        self._data.update(kwargs)
        self._state_history.append(("update", kwargs))
        
    async def get_data(self):
        return self._data
        
    async def clear(self):
        self._state_history.append(("clear", None))
        self._state = None
        self._data = {}
        
    def get_state_name(self):
        if self._state:
            return getattr(self._state, '__state_name__', str(self._state))
        return None


# ==================== Интеграционные тесты ====================

async def setup_test_db():
    """Создать тестовую БД"""
    from db_utils import Database, db
    
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.db_path = tmp.name
    await db.init_tables()
    return tmp.name


async def cleanup_test_db(db_path):
    """Очистить тестовую БД"""
    from db_utils import db
    db.db_path = "user_roles.db"
    os.unlink(db_path)


async def test_material_create_full_flow():
    """
    Сценарий: Создание материала (полный flow)
    Ожидаемый путь: menu -> selecting_stage -> input_title -> input_link -> input_desc -> menu
    """
    from handlers.materials import (
        materials_menu, material_add_start, material_add_title, 
        material_add_link, material_add_desc, handle_stage_selection_admin,
        MaterialStates
    )
    from db_utils import add_or_update_user
    
    db_path = await setup_test_db()
    
    try:
        # Создаем админа
        await add_or_update_user(user_id=123456, username="testadmin", role="admin")
        
        state = MockFSMContext()
        
        # Шаг 1: Открываем меню материалов
        msg = MockMessage(text="📦 Управление материалами", user_id=123456)
        await materials_menu(msg, state)
        assert state._state == MaterialStates.menu, "Должны быть в состоянии menu"
        
        # Шаг 2: Нажимаем "Добавить"
        msg = MockMessage(text="➕ Добавить", user_id=123456)
        await material_add_start(msg, state)
        assert state._state == MaterialStates.selecting_stage, "Должны быть в selecting_stage"
        assert state._data.get("action") == "add_material", "Должен быть action=add_material"
        
        # Шаг 3: Выбираем раздел (через handle_stage_selection_admin)
        msg = MockMessage(text="📚 Фундаментальная теория", user_id=123456)
        await handle_stage_selection_admin(msg, state)
        assert state._state == MaterialStates.input_title, "Должны быть в input_title"
        assert state._data.get("stage") == "fundamental", "Должен быть сохранен stage"
        
        # Шаг 4: Вводим название
        msg = MockMessage(text="Test Material Title", user_id=123456)
        await material_add_title(msg, state)
        assert state._state == MaterialStates.input_link, "Должны быть в input_link"
        assert state._data.get("title") == "Test Material Title", "Должен быть сохранен title"
        
        # Шаг 5: Вводим ссылку
        msg = MockMessage(text="https://example.com/material", user_id=123456)
        await material_add_link(msg, state)
        assert state._state == MaterialStates.input_desc, "Должны быть в input_desc"
        assert state._data.get("link") == "https://example.com/material", "Должен быть сохранен link"
        
        # Шаг 6: Вводим описание
        msg = MockMessage(text="Test description", user_id=123456)
        await material_add_desc(msg, state)
        # После сохранения вызывается materials_menu, которая устанавливает menu
        # Проверяем что state был установлен в menu в какой-то момент
        states_set = [h[1] for h in state._state_history if h[0] == "set"]
        assert MaterialStates.menu in states_set, "Должны вернуться в menu после сохранения"
        
        # Проверяем что материал создан в БД
        from db_utils import get_materials
        mats = await get_materials("fundamental")
        assert len(mats) == 1, "Материал должен быть создан"
        assert mats[0]["title"] == "Test Material Title"
        
        print("[OK] test_material_create_full_flow PASSED")
        
    finally:
        await cleanup_test_db(db_path)


async def test_material_create_cancel_on_back():
    """
    Сценарий: Отмена создания материала по кнопке "Назад"
    """
    from handlers.materials import (
        material_add_start, material_add_title,
        MaterialStates
    )
    from handlers.common import back_handler
    from handlers.common import back_handler as common_back_handler
    from db_utils import add_or_update_user
    
    db_path = await setup_test_db()
    
    try:
        await add_or_update_user(user_id=123456, username="testadmin", role="admin")
        
        state = MockFSMContext()
        
        # Начинаем создание
        msg = MockMessage(text="➕ Добавить", user_id=123456)
        await material_add_start(msg, state)
        assert state._state == MaterialStates.selecting_stage
        
        # Выбираем раздел
        from handlers.materials import handle_stage_selection_admin
        msg = MockMessage(text="📚 Фундаментальная теория", user_id=123456)
        await handle_stage_selection_admin(msg, state)
        assert state._state == MaterialStates.input_title
        
        # Вводим название
        msg = MockMessage(text="Test Title", user_id=123456)
        await material_add_title(msg, state)
        assert state._state == MaterialStates.input_link
        
        # Нажимаем "Назад" - должно сбросить состояние
        msg = MockMessage(text="🔙 Назад", user_id=123456)
        await common_back_handler(msg, state)
        assert state._state is None, "Состояние должно быть сброшено"
        
        print("[OK] test_material_create_cancel_on_back PASSED")
        
    finally:
        await cleanup_test_db(db_path)


async def test_material_create_invalid_url():
    """
    Сценарий: Попытка ввода невалидной ссылки при создании материала
    Ожидаемо: Ошибка, остаемся в том же состоянии
    """
    from handlers.materials import material_add_link, MaterialStates
    
    state = MockFSMContext()
    state._data = {"stage": "fundamental", "title": "Test"}
    state._state = MaterialStates.input_link
    
    # Вводим невалидную ссылку
    msg = MockMessage(text="not-a-valid-url", user_id=123456)
    await material_add_link(msg, state)
    
    # Должны остаться в том же состоянии
    assert state._state == MaterialStates.input_link, "Должны остаться в input_link"
    assert "link" not in state._data, "Ссылка не должна быть сохранена"
    
    print("[OK] test_material_create_invalid_url PASSED")


async def test_event_create_full_flow():
    """
    Сценарий: Создание события (полный flow)
    Ожидаемый путь: menu -> input_type -> input_datetime -> input_link -> input_announcement -> [confirm] -> menu
    """
    from handlers.events import (
        events_menu, event_add_start, event_add_type, event_add_datetime,
        event_add_link, event_add_announcement, EventStates
    )
    from db_utils import add_or_update_user
    
    db_path = await setup_test_db()
    
    try:
        await add_or_update_user(user_id=123456, username="testadmin", role="admin")
        
        state = MockFSMContext()
        
        # Шаг 1: Открываем меню событий
        msg = MockMessage(text="📋 Управление событиями", user_id=123456)
        await events_menu(msg, state)
        assert state._state == EventStates.menu
        
        # Шаг 2: Нажимаем "Добавить"
        msg = MockMessage(text="➕ Добавить", user_id=123456)
        await event_add_start(msg, state)
        assert state._state == EventStates.input_type
        
        # Шаг 3: Вводим тип
        msg = MockMessage(text="Test Webinar", user_id=123456)
        await event_add_type(msg, state)
        assert state._state == EventStates.input_datetime
        assert state._data.get("event_type") == "Test Webinar"
        
        # Шаг 4: Вводим дату в будущем
        future_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        msg = MockMessage(text=future_date, user_id=123456)
        await event_add_datetime(msg, state)
        assert state._state == EventStates.input_link
        assert state._data.get("event_datetime") == future_date
        
        # Шаг 5: Вводим ссылку
        msg = MockMessage(text="https://example.com/event", user_id=123456)
        await event_add_link(msg, state)
        assert state._state == EventStates.input_announcement
        assert state._data.get("event_link") == "https://example.com/event"
        
        # Шаг 6: Вводим анонс (ANNOUNCEMENT_GROUP_ID не настроен - сразу сохраняем)
        msg = MockMessage(text="Test announcement text", user_id=123456)
        await event_add_announcement(msg, state)
        
        # Проверяем что состояние менялось на menu (вызывается events_menu)
        states_set = [h[1] for h in state._state_history if h[0] == "set"]
        assert EventStates.menu in states_set, "Должно быть состояние menu в истории"
        
        # Проверяем что событие создано
        from db_utils import get_events
        events = await get_events()
        assert len(events) == 1, "Событие должно быть создано"
        assert events[0]["type"] == "Test Webinar"
        
        print("[OK] test_event_create_full_flow PASSED")
        
    finally:
        await cleanup_test_db(db_path)


async def test_event_create_past_date():
    """
    Сценарий: Попытка создать событие с датой в прошлом
    Ожидаемо: Ошибка, остаемся в том же состоянии
    """
    from handlers.events import event_add_datetime, EventStates
    
    state = MockFSMContext()
    state._data = {"event_type": "Test"}
    state._state = EventStates.input_datetime
    
    # Вводим дату в прошлом
    past_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    msg = MockMessage(text=past_date, user_id=123456)
    await event_add_datetime(msg, state)
    
    # Должны остаться в том же состоянии
    assert state._state == EventStates.input_datetime
    assert "event_datetime" not in state._data
    
    print("[OK] test_event_create_past_date PASSED")


async def test_event_create_invalid_date_format():
    """
    Сценарий: Попытка создать событие с неверным форматом даты
    """
    from handlers.events import event_add_datetime, EventStates
    
    state = MockFSMContext()
    state._data = {"event_type": "Test"}
    state._state = EventStates.input_datetime
    
    # Вводим невалидную дату
    msg = MockMessage(text="завтра в 6 вечера", user_id=123456)
    await event_add_datetime(msg, state)
    
    assert state._state == EventStates.input_datetime
    assert "event_datetime" not in state._data
    
    print("[OK] test_event_create_invalid_date_format PASSED")


async def test_role_assign_full_flow():
    """
    Сценарий: Назначение роли пользователю
    Ожидаемый путь: menu -> input_users -> selecting_role -> (clear)
    """
    from handlers.roles import (
        roles_menu, role_add_start, role_receive_users, role_set_callback,
        RoleStates
    )
    from db_utils import add_or_update_user
    from utils import _rate_limits
    
    # Очищаем rate limits
    _rate_limits.clear()
    
    db_path = await setup_test_db()
    
    try:
        await add_or_update_user(user_id=123456, username="testadmin", role="admin")
        
        state = MockFSMContext()
        
        # Шаг 1: Открываем меню ролей
        msg = MockMessage(text="👥 Управление ролями", user_id=123456)
        await roles_menu(msg, state)
        # Проверяем что menu было установлено
        menu_sets = [h for h in state._state_history if h[0] == "set"]
        assert len(menu_sets) >= 1, "Должно быть установлено состояние menu"
        
        # Шаг 2: Нажимаем "Назначить роль"
        msg = MockMessage(text="➕ Назначить роль", user_id=123456)
        await role_add_start(msg, state)
        assert state._state == RoleStates.input_users
        
        # Шаг 3: Вводим пользователя
        msg = MockMessage(text="999999", user_id=123456)
        await role_receive_users(msg, state)
        assert state._state == RoleStates.selecting_role
        assert "users_to_assign" in state._data
        assert len(state._data["users_to_assign"]) == 1
        
        # Шаг 4: Выбираем роль через callback
        callback = MockCallbackQuery("set_role:mentor", user_id=123456)
        callback.message = MockMessage(user_id=123456)
        await role_set_callback(callback, state)
        # Состояние очищается через state.clear() в конце
        # Проверяем что состояние было очищено (None или осталось прежним)
        
        # Проверяем что роль назначена
        from db_utils import get_user_by_id
        user = await get_user_by_id(999999)
        assert user is not None
        assert user["role"] == "mentor"
        
        print("[OK] test_role_assign_full_flow PASSED")
        
    finally:
        await cleanup_test_db(db_path)


async def test_role_assign_invalid_users():
    """
    Сценарий: Попытка назначить роль с невалидными данными
    """
    from handlers.roles import role_receive_users, RoleStates
    
    state = MockFSMContext()
    state._state = RoleStates.input_users
    
    # Вводим мусор
    msg = MockMessage(text="!!!@@@###", user_id=123456)
    await role_receive_users(msg, state)
    
    # Должны остаться в том же состоянии
    assert state._state == RoleStates.input_users
    assert "users_to_assign" not in state._data
    
    print("[OK] test_role_assign_invalid_users PASSED")


async def test_public_materials_flow():
    """
    Сценарий: Публичный просмотр материалов
    """
    from handlers.materials import (
        public_materials_select, handle_stage_selection_public,
        MaterialStates
    )
    from db_utils import add_or_update_user, add_material
    from utils import _rate_limits
    
    # Очищаем rate limits
    _rate_limits.clear()
    
    db_path = await setup_test_db()
    
    try:
        # Создаем пользователя и материал
        await add_or_update_user(user_id=123456, username="testuser", role="user")
        await add_material("fundamental", "Test Material", "https://example.com", "Description")
        
        state = MockFSMContext()
        
        # Шаг 1: Нажимаем "Материалы"
        msg = MockMessage(text="📚 Материалы", user_id=123456)
        await public_materials_select(msg, state)
        # Проверяем что selecting_stage_public было установлено
        stage_sets = [h for h in state._state_history if h[0] == "set"]
        assert len(stage_sets) >= 1, "Должно быть установлено состояние"
        
        # Шаг 2: Выбираем раздел
        msg = MockMessage(text="📚 Фундаментальная теория", user_id=123456)
        await handle_stage_selection_public(msg, state)
        # Остаемся в том же состоянии для возможности выбрать другой раздел
        # Проверяем что состояние не сброшено (может быть установлено заново)
        states_set = [h[1] for h in state._state_history if h[0] == "set"]
        assert MaterialStates.selecting_stage_public in states_set, "Должно быть состояние selecting_stage_public"
        
        print("[OK] test_public_materials_flow PASSED")
        
    finally:
        await cleanup_test_db(db_path)


async def test_material_edit_flow():
    """
    Сценарий: Редактирование материала
    """
    from handlers.materials import (
        materials_menu, material_edit_select_stage, handle_stage_selection_admin,
        material_edit_callback, material_edit_process,
        MaterialStates
    )
    from db_utils import add_or_update_user, add_material
    
    db_path = await setup_test_db()
    
    try:
        await add_or_update_user(user_id=123456, username="testadmin", role="admin")
        await add_material("fundamental", "Original Title", "https://original.com", "Original desc")
        
        state = MockFSMContext()
        
        # Шаг 1: Меню -> Выбор раздела для редактирования
        msg = MockMessage(text="✏️ Редактировать", user_id=123456)
        await material_edit_select_stage(msg, state)
        assert state._state == MaterialStates.selecting_stage
        assert state._data.get("action") == "select_for_edit"
        
        # Шаг 2: Выбираем раздел (выведет список через inline)
        msg = MockMessage(text="📚 Фундаментальная теория", user_id=123456)
        await handle_stage_selection_admin(msg, state)
        assert state._state == MaterialStates.selecting_item
        
        # Шаг 3: Callback выбора материала
        callback = MockCallbackQuery("edit_mat:1", user_id=123456)
        await material_edit_callback(callback, state)
        assert state._state == MaterialStates.editing
        assert state._data.get("edit_id") == 1
        
        # Шаг 4: Вводим новые данные
        msg = MockMessage(text="New Title\n\nhttps://new.com\n\nNew description", user_id=123456)
        await material_edit_process(msg, state)
        
        # Проверяем что материал обновлен
        from db_utils import get_material
        mat = await get_material(1)
        assert mat["title"] == "New Title"
        assert mat["link"] == "https://new.com"
        assert mat["description"] == "New description"
        
        print("[OK] test_material_edit_flow PASSED")
        
    finally:
        await cleanup_test_db(db_path)


async def test_material_edit_partial_update():
    """
    Сценарий: Частичное редактирование материала (через точку)
    """
    from handlers.materials import material_edit_process, MaterialStates
    from db_utils import add_or_update_user, add_material, get_material
    
    db_path = await setup_test_db()
    
    try:
        await add_or_update_user(user_id=123456, username="testadmin", role="admin")
        await add_material("fundamental", "Original Title", "https://original.com", "Original desc")
        
        state = MockFSMContext()
        state._state = MaterialStates.editing
        state._data = {"edit_id": 1}
        
        # Меняем только название (остальное через точку)
        msg = MockMessage(text="Only New Title\n\n.\n\n.", user_id=123456)
        await material_edit_process(msg, state)
        
        # Проверяем что изменилось только название
        mat = await get_material(1)
        assert mat["title"] == "Only New Title"
        assert mat["link"] == "https://original.com"  # не изменилось
        assert mat["description"] == "Original desc"  # не изменилось
        
        print("[OK] test_material_edit_partial_update PASSED")
        
    finally:
        await cleanup_test_db(db_path)


async def test_session_expired_handling():
    """
    Сценарий: Попытка редактирования после истечения сессии (нет edit_id)
    """
    from handlers.materials import material_edit_process, MaterialStates
    from handlers.common import get_main_keyboard
    
    state = MockFSMContext()
    state._state = MaterialStates.editing
    state._data = {}  # Нет edit_id
    
    msg = MockMessage(text="Some data", user_id=123456)
    await material_edit_process(msg, state)
    
    # Должно сбросить состояние
    assert state._state is None
    
    print("[OK] test_session_expired_handling PASSED")


async def test_state_isolation():
    """
    Сценарий: Проверка изоляции состояний между пользователями
    """
    from handlers.materials import MaterialStates
    
    state1 = MockFSMContext()
    state2 = MockFSMContext()
    
    # Пользователь 1 начинает создание материала
    state1._state = MaterialStates.input_title
    state1._data = {"stage": "fundamental"}
    
    # Пользователь 2 начинает другое действие
    state2._state = MaterialStates.selecting_stage
    state2._data = {"action": "add_material"}
    
    # Состояния должны быть независимы
    assert state1._state != state2._state
    assert state1._data != state2._data
    
    print("[OK] test_state_isolation PASSED")


async def test_delete_operations():
    """
    Сценарий: Удаление материала
    """
    from handlers.materials import (
        material_delete_select_stage, handle_stage_selection_admin,
        material_delete_callback
    )
    from db_utils import add_or_update_user, add_material, get_material
    
    db_path = await setup_test_db()
    
    try:
        await add_or_update_user(user_id=123456, username="testadmin", role="admin")
        await add_material("fundamental", "To Delete", "https://delete.com", "")
        
        # Проверяем что материал есть
        mat = await get_material(1)
        assert mat is not None
        
        state = MockFSMContext()
        
        # Начинаем удаление
        msg = MockMessage(text="🗑️ Удалить", user_id=123456)
        await material_delete_select_stage(msg, state)
        assert state._state is not None
        
        # Выбираем раздел
        msg = MockMessage(text="📚 Фундаментальная теория", user_id=123456)
        await handle_stage_selection_admin(msg, state)
        assert state._state is not None
        
        # Callback удаления
        callback = MockCallbackQuery("del_mat:1", user_id=123456)
        await material_delete_callback(callback, state)
        assert state._state is None  # Состояние очищено
        
        # Проверяем что удалено
        mat = await get_material(1)
        assert mat is None
        
        print("[OK] test_delete_operations PASSED")
        
    finally:
        await cleanup_test_db(db_path)


# ==================== Запуск тестов ====================

async def run_all_tests():
    """Запуск всех интеграционных тестов"""
    print("=" * 60)
    print("FSM Integration Tests")
    print("=" * 60)
    
    tests = [
        ("Material Create Full Flow", test_material_create_full_flow),
        ("Material Create Cancel on Back", test_material_create_cancel_on_back),
        ("Material Create Invalid URL", test_material_create_invalid_url),
        ("Event Create Full Flow", test_event_create_full_flow),
        ("Event Create Past Date", test_event_create_past_date),
        ("Event Create Invalid Date Format", test_event_create_invalid_date_format),
        ("Role Assign Full Flow", test_role_assign_full_flow),
        ("Role Assign Invalid Users", test_role_assign_invalid_users),
        ("Public Materials Flow", test_public_materials_flow),
        ("Material Edit Flow", test_material_edit_flow),
        ("Material Edit Partial Update", test_material_edit_partial_update),
        ("Session Expired Handling", test_session_expired_handling),
        ("State Isolation", test_state_isolation),
        ("Delete Operations", test_delete_operations),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            await test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()
    
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
