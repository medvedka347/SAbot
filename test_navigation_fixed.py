#!/usr/bin/env python3
"""
Тесты проверяющие что навигация "Назад" теперь работает корректно
"""

import asyncio
import sys
import os
import tempfile
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class MockMessage:
    def __init__(self, text=None, user_id=123456, username="testadmin", chat_type="private"):
        self.text = text
        self.from_user = MagicMock()
        self.from_user.id = user_id
        self.from_user.username = username
        self.from_user.first_name = "Test"
        self.chat = MagicMock()
        self.chat.type = chat_type
        self.reply_to_message = None
        self._answers = []
        
    async def answer(self, text, **kwargs):
        self._answers.append({"text": text, "kwargs": kwargs})
        return MagicMock()


class MockFSMContext:
    """Улучшенный мок FSM контекста"""
    def __init__(self):
        self._state = None
        self._data = {}
        
    async def set_state(self, state):
        self._state = state
        
    async def get_state(self):
        return self._state
        
    async def update_data(self, **kwargs):
        self._data.update(kwargs)
        
    async def get_data(self):
        return self._data
        
    async def clear(self):
        self._state = None
        self._data = {}


async def setup_test_db():
    from db_utils import db
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.db_path = tmp.name
    await db.init_tables()
    return tmp.name


async def cleanup_test_db(db_path):
    from db_utils import db
    db.db_path = "user_roles.db"
    os.unlink(db_path)


async def test_back_navigation_materials():
    """Тест навигации назад в создании материалов"""
    from handlers.common import back_handler, STATE_MAP
    from handlers.materials import MaterialStates
    
    state = MockFSMContext()
    
    # Симулируем что мы в input_title и пришли из selecting_stage
    await state.set_state(MaterialStates.input_title)
    await state.update_data(stage="fundamental", action="add_material", _prev_state="selecting_stage")
    
    # Нажимаем "Назад"
    msg = MockMessage(text="🔙 Назад")
    await back_handler(msg, state)
    
    # Проверяем что вернулись в selecting_stage
    assert state._state == MaterialStates.selecting_stage, \
        f"Expected selecting_stage, got {state._state}"
    
    # Проверяем что _prev_state очищен
    assert state._data.get("_prev_state") is None, \
        "_prev_state should be cleared after navigation"
    
    # Проверяем что данные сохранены
    assert state._data.get("stage") == "fundamental", \
        "Stage data should be preserved"
    
    print("[PASS] test_back_navigation_materials: BACK from input_title to selecting_stage")


async def test_back_navigation_events():
    """Тест навигации назад в создании событий"""
    from handlers.common import back_handler
    from handlers.events import EventStates
    
    state = MockFSMContext()
    
    # Симулируем что мы в input_datetime и пришли из input_type
    await state.set_state(EventStates.input_datetime)
    await state.update_data(event_type="Webinar", _prev_state="input_type")
    
    # Нажимаем "Назад"
    msg = MockMessage(text="🔙 Назад")
    await back_handler(msg, state)
    
    # Проверяем что вернулись в input_type
    assert state._state == EventStates.input_type, \
        f"Expected input_type, got {state._state}"
    
    # Проверяем что данные сохранены
    assert state._data.get("event_type") == "Webinar", \
        "event_type should be preserved"
    
    print("[PASS] test_back_navigation_events: BACK from input_datetime to input_type")


async def test_back_navigation_roles():
    """Тест навигации назад в назначении ролей"""
    from handlers.common import back_handler
    from handlers.roles import RoleStates
    
    state = MockFSMContext()
    
    # Симулируем что мы в selecting_role и пришли из input_users
    await state.set_state(RoleStates.selecting_role)
    await state.update_data(users_to_assign=[{"user_id": 123}], _prev_state="input_users")
    
    # Нажимаем "Назад"
    msg = MockMessage(text="🔙 Назад")
    await back_handler(msg, state)
    
    # Проверяем что вернулись в input_users
    assert state._state == RoleStates.input_users, \
        f"Expected input_users, got {state._state}"
    
    # Проверяем что данные сохранены
    assert state._data.get("users_to_assign") is not None, \
        "users_to_assign should be preserved"
    
    print("[PASS] test_back_navigation_roles: BACK from selecting_role to input_users")


async def test_back_to_main_menu_when_no_history():
    """Тест что без истории идем в главное меню"""
    from handlers.common import back_handler
    from db_utils import add_or_update_user
    
    db_path = await setup_test_db()
    try:
        await add_or_update_user(user_id=123456, username="testadmin", role="admin")
        
        state = MockFSMContext()
        
        # Нет _prev_state
        await state.set_state(None)
        
        # Нажимаем "Назад"
        msg = MockMessage(text="🔙 Назад")
        await back_handler(msg, state)
        
        # Должны быть в главном меню (state cleared)
        assert state._state is None, "Should be in main menu (state cleared)"
        
        print("[PASS] test_back_to_main_menu_when_no_history: No prev_state -> main menu")
        
    finally:
        await cleanup_test_db(db_path)


async def test_multi_step_back():
    """Тест множественного нажатия назад"""
    from handlers.common import back_handler
    from handlers.materials import MaterialStates
    
    state = MockFSMContext()
    
    # Шаг 1: input_desc -> input_link
    await state.set_state(MaterialStates.input_desc)
    await state.update_data(link="https://test.com", _prev_state="input_link")
    
    msg = MockMessage(text="🔙 Назад")
    await back_handler(msg, state)
    
    assert state._state == MaterialStates.input_link, "Step 1: should be in input_link"
    
    # Шаг 2: input_link -> input_title (симулируем что есть _prev_state)
    await state.update_data(title="Test", _prev_state="input_title")
    await state.set_state(MaterialStates.input_link)
    
    await back_handler(msg, state)
    
    assert state._state == MaterialStates.input_title, "Step 2: should be in input_title"
    
    # Шаг 3: input_title -> selecting_stage
    await state.update_data(stage="fundamental", _prev_state="selecting_stage")
    await state.set_state(MaterialStates.input_title)
    
    await back_handler(msg, state)
    
    assert state._state == MaterialStates.selecting_stage, "Step 3: should be in selecting_stage"
    
    print("[PASS] test_multi_step_back: Multi-step navigation works")


async def run_all_tests():
    print("=" * 60)
    print("FSM Navigation Tests - After Fix")
    print("=" * 60)
    print()
    
    tests = [
        test_back_navigation_materials,
        test_back_navigation_events,
        test_back_navigation_roles,
        test_back_to_main_menu_when_no_history,
        test_multi_step_back,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            await test()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {test.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {test.__name__}: {e}")
    
    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
