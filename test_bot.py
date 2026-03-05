#!/usr/bin/env python3
"""
Юнит-тесты для SABot
Запуск: python test_bot.py
"""

import asyncio
import sys
import os
from datetime import datetime, timedelta

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ==================== Тесты для utils.py ====================

def test_check_rate_limit():
    """Тест rate limiting"""
    import time
    from utils import check_rate_limit, _rate_limits, RATE_LIMIT_MIN_GAP
    
    # Очищаем хранилище
    _rate_limits.clear()
    
    # Первые 20 запросов должны пройти (с задержкой между ними)
    for i in range(20):
        ok, wait = check_rate_limit(12345)
        assert ok == True, f"Request {i+1} should pass"
        assert wait == 0, f"Wait should be 0"
        time.sleep(RATE_LIMIT_MIN_GAP + 0.01)  # Ждем между запросами
    
    # 21-й запрос должен быть заблокирован
    ok, wait = check_rate_limit(12345)
    assert ok == False, "21st request should be blocked"
    assert wait > 0, "Should have wait time"
    
    print("[OK] test_check_rate_limit PASSED")


def test_check_group_rate_limit():
    """Тест группового rate limiting"""
    from utils import check_group_rate_limit, _group_rate_limits
    
    # Очищаем хранилище
    _group_rate_limits.clear()
    
    # Первые 3 запроса должны пройти
    for i in range(3):
        ok, muted = check_group_rate_limit(-100123456, "events")
        assert ok == True, f"Request {i+1} should pass"
        assert muted == False, "Should not be muted"
    
    # 4-й запрос должен поставить мут
    ok, muted = check_group_rate_limit(-100123456, "events")
    assert ok == False, "4th request should be blocked"
    assert muted == True, "Should be muted"
    
    print("[OK] test_check_group_rate_limit PASSED")


def test_is_valid_url():
    """Тест валидации URL"""
    from utils import is_valid_url
    
    # Valid URLs
    assert is_valid_url("https://example.com") == True
    assert is_valid_url("http://localhost:8080") == True
    assert is_valid_url("https://cal.com/akhmadishin/мок") == True
    
    # Invalid URLs
    assert is_valid_url("not-a-url") == False
    assert is_valid_url("ftp://files.com") == False  # Not http/https
    assert is_valid_url("") == True  # Empty string allowed
    assert is_valid_url("a" * 2001) == False  # Too long
    
    print("[OK] test_is_valid_url PASSED")


def test_get_stage_key():
    """Тест получения ключа stage"""
    from utils import get_stage_key
    from config import STAGES
    
    for key, name in STAGES.items():
        result = get_stage_key(name)
        assert result == key, f"For {name} should return {key}"
    
    assert get_stage_key("Неизвестный") is None
    
    print("[OK] test_get_stage_key PASSED")


def test_escape_md():
    """Тест экранирования Markdown"""
    from utils import escape_md
    
    # Check that special chars are escaped
    test = "*bold* _italic_ [link](url)"
    result = escape_md(test)
    assert "\*" in result or "bold" in result
    assert "\_" in result or "italic" in result
    
    # Пустая строка
    assert escape_md("") == ""
    assert escape_md(None) == ""
    
    print("[OK] test_escape_md PASSED")


def test_parse_users_input():
    """Тест парсинга ввода пользователей"""
    from utils import parse_users_input
    
    # Only ID
    users, errors = parse_users_input("123456789")
    assert len(users) == 1
    assert users[0]["user_id"] == 123456789
    
    # Only username
    users, errors = parse_users_input("@ivan")
    assert len(users) == 1
    assert users[0]["username"] == "ivan"
    
    # ID + username
    users, errors = parse_users_input("123456789 @ivan")
    assert len(users) == 1
    assert users[0]["user_id"] == 123456789
    assert users[0]["username"] == "ivan"
    
    # Multiple users
    users, errors = parse_users_input("@user1, @user2, 999")
    assert len(users) == 2  # user1 alone, user2+999 combined
    
    # Invalid data
    users, errors = parse_users_input("invalid!!!")
    assert len(users) == 0
    assert len(errors) == 1
    
    print("[OK] test_parse_users_input PASSED")


# ==================== Тесты для db_utils.py ====================

async def test_normalize_username():
    """Тест нормализации username"""
    from db_utils import normalize_username
    
    assert normalize_username("@Ivan") == "ivan"
    assert normalize_username("USER123") == "user123"
    assert normalize_username("  @Test  ") == "test"
    assert normalize_username("") is None
    assert normalize_username(None) is None
    
    print("[OK] test_normalize_username PASSED")


async def test_validate_user_id():
    """Тест валидации user_id"""
    from db_utils import validate_user_id
    
    assert validate_user_id(123456789) == 123456789
    assert validate_user_id("123456789") == 123456789
    assert validate_user_id(0) is None
    assert validate_user_id(-1) is None
    assert validate_user_id(10_000_000_000) is None
    assert validate_user_id("not-a-number") is None
    assert validate_user_id(None) is None
    
    print("[OK] test_validate_user_id PASSED")


async def test_database_operations():
    """Тест операций с БД"""
    from db_utils import Database
    import tempfile
    
    # Создаем временную БД
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        db = Database(db_path)
        
        # Создаем таблицы
        await db.init_tables()
        
        # Тест: execute и fetchone
        await db.execute("INSERT INTO user_roles (user_id, username, role) VALUES (?, ?, ?)", 
                        (123, "testuser", "user"))
        
        row = await db.fetchone("SELECT user_id, username, role FROM user_roles WHERE user_id = ?", (123,))
        assert row is not None
        assert row[0] == 123
        assert row[1] == "testuser"
        assert row[2] == "user"
        
        # Тест: fetchall
        await db.execute("INSERT INTO user_roles (user_id, username, role) VALUES (?, ?, ?)", 
                        (456, "testuser2", "admin"))
        
        rows = await db.fetchall("SELECT user_id FROM user_roles ORDER BY user_id")
        assert len(rows) == 2
        
        print("[OK] test_database_operations PASSED")
    finally:
        os.unlink(db_path)


async def test_user_crud():
    """Тест CRUD пользователей"""
    from db_utils import (
        add_or_update_user, get_user_by_id, get_user_by_username, 
        get_user_role, delete_user, Database
    )
    import tempfile
    
    # Используем временную БД
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    # Переопределяем глобальный db
    from db_utils import db
    original_path = db.db_path
    db.db_path = db_path
    
    try:
        await db.init_tables()
        
        # Create
        result = await add_or_update_user(user_id=123456, username="testuser", role="user")
        assert result == True
        
        # Read
        user = await get_user_by_id(123456)
        assert user is not None
        assert user["user_id"] == 123456
        assert user["username"] == "testuser"
        assert user["role"] == "user"
        
        user = await get_user_by_username("testuser")
        assert user is not None
        
        role = await get_user_role(user_id=123456)
        assert role == "user"
        
        # Update
        await add_or_update_user(user_id=123456, username="testuser", role="admin")
        user = await get_user_by_id(123456)
        assert user["role"] == "admin"
        
        # Delete
        result = await delete_user(user_id=123456)
        assert result == True
        user = await get_user_by_id(123456)
        assert user is None
        
        print("[OK] test_user_crud PASSED")
    finally:
        db.db_path = original_path
        os.unlink(db_path)


async def test_ban_system():
    """Тест системы банов"""
    from db_utils import (
        record_failed_attempt, get_ban_status, unban_user, 
        apply_ban, clear_failed_attempts, Database
    )
    import tempfile
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    from db_utils import db
    original_path = db.db_path
    db.db_path = db_path
    
    try:
        await db.init_tables()
        
        # Нет бана изначально
        ban = await get_ban_status(user_id=999)
        assert ban is None
        
        # Первая неудачная попытка
        result = await record_failed_attempt(user_id=999)
        assert result is None  # Бан еще не применен
        
        # Вторая попытка
        result = await record_failed_attempt(user_id=999)
        assert result is None
        
        # Третья попытка - бан
        result = await record_failed_attempt(user_id=999)
        assert result is not None
        assert result["ban_level"] == 1
        
        # Проверяем бан
        ban = await get_ban_status(user_id=999)
        assert ban is not None
        assert ban["ban_level"] == 1
        
        # Снимаем бан
        await unban_user(user_id=999)
        ban = await get_ban_status(user_id=999)
        assert ban is None
        
        # Очищаем неудачные попытки
        await record_failed_attempt(user_id=888)
        await clear_failed_attempts(user_id=888)
        
        print("[OK] test_ban_system PASSED")
    finally:
        db.db_path = original_path
        os.unlink(db_path)


async def test_materials_crud():
    """Тест CRUD материалов"""
    from db_utils import (
        add_material, get_materials, get_material, 
        update_material, delete_material, Database
    )
    import tempfile
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    from db_utils import db
    original_path = db.db_path
    db.db_path = db_path
    
    try:
        await db.init_tables()
        
        # Create
        await add_material("fundamental", "Test Material", "https://example.com", "Description")
        
        # Read all
        mats = await get_materials()
        assert len(mats) == 1
        assert mats[0]["title"] == "Test Material"
        
        # Read by stage
        mats = await get_materials("fundamental")
        assert len(mats) == 1
        
        mats = await get_materials("roadmap")
        assert len(mats) == 0
        
        # Read by id
        mat = await get_material(1)
        assert mat is not None
        assert mat["title"] == "Test Material"
        
        # Update
        result = await update_material(1, title="Updated Title")
        assert result == True
        mat = await get_material(1)
        assert mat["title"] == "Updated Title"
        
        # Update с недопустимым полем
        result = await update_material(1, invalid_field="test")
        assert result == False
        
        # Delete
        result = await delete_material(1)
        assert result == True
        mat = await get_material(1)
        assert mat is None
        
        print("[OK] test_materials_crud PASSED")
    finally:
        db.db_path = original_path
        os.unlink(db_path)


async def test_events_crud():
    """Тест CRUD событий"""
    from db_utils import (
        add_event, get_events, update_event, delete_event, Database
    )
    import tempfile
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    from db_utils import db
    original_path = db.db_path
    db.db_path = db_path
    
    try:
        await db.init_tables()
        
        future = (datetime.now() + timedelta(days=1)).isoformat()
        past = (datetime.now() - timedelta(days=1)).isoformat()
        
        # Create
        await add_event("Webinar", future, "https://example.com", "Test announcement")
        await add_event("Meetup", past, "", "Past event")
        
        # Read all
        events = await get_events()
        assert len(events) == 2
        
        # Read upcoming only
        events = await get_events(upcoming_only=True)
        assert len(events) == 1
        assert events[0]["type"] == "Webinar"
        
        # Update
        result = await update_event(1, event_type="Updated Webinar")
        assert result == True
        
        # Update с недопустимым полем
        result = await update_event(1, invalid="test")
        assert result == False
        
        # Delete
        result = await delete_event(1)
        assert result == True
        
        print("[OK] test_events_crud PASSED")
    finally:
        db.db_path = original_path
        os.unlink(db_path)


async def test_search_materials():
    """Тест поиска материалов"""
    from db_utils import (
        add_material, search_materials, search_materials_by_title, Database
    )
    import tempfile
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    from db_utils import db
    original_path = db.db_path
    db.db_path = db_path
    
    try:
        await db.init_tables()
        
        await add_material("fundamental", "REST API Guide", "https://rest.com", "About REST")
        await add_material("practical_theory", "GraphQL Tutorial", "https://graphql.com", "About GraphQL")
        await add_material("fundamental", "REST Best Practices", "https://rest2.com", "More REST")
        
        # Поиск по названию и описанию
        results = await search_materials("REST")
        assert len(results) == 2
        
        # Поиск по заголовку
        results = await search_materials_by_title("GraphQL")
        assert len(results) == 1
        assert results[0]["title"] == "GraphQL Tutorial"
        
        # Поиск без результатов
        results = await search_materials("nonexistent")
        assert len(results) == 0
        
        print("[OK] test_search_materials PASSED")
    finally:
        db.db_path = original_path
        os.unlink(db_path)


# ==================== Запуск всех тестов ====================

def run_sync_tests():
    """Запуск синхронных тестов"""
    test_check_rate_limit()
    test_check_group_rate_limit()
    test_is_valid_url()
    test_get_stage_key()
    test_escape_md()
    test_parse_users_input()


async def run_async_tests():
    """Запуск асинхронных тестов"""
    await test_normalize_username()
    await test_validate_user_id()
    await test_database_operations()
    await test_user_crud()
    await test_ban_system()
    await test_materials_crud()
    await test_events_crud()
    await test_search_materials()


async def main():
    """Главная функция запуска тестов"""
    print("=" * 50)
    print("SABot Unit Tests")
    print("=" * 50)
    
    try:
        run_sync_tests()
        await run_async_tests()
        
        print("=" * 50)
        print("All tests PASSED!")
        print("=" * 50)
        return 0
    except AssertionError as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
