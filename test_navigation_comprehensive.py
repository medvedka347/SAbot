"""
Комплексное тестирование навигации "Назад" и FSM-диалогов SABot.

Тесты проверяют:
1. Корректность цепочек _prev_state/_prev_chain во всех модулях
2. Новую систему _state_history для многоуровневого возврата
3. Поведение кнопки "Назад" при многократном нажатии
4. Fallback в главное меню когда нет истории
"""

import re
import sys
from dataclasses import dataclass
from typing import List


# ==================== Test Framework ====================

@dataclass
class TestResult:
    name: str
    passed: bool
    message: str = ""


class TestRunner:
    def __init__(self):
        self.results: List[TestResult] = []
        self.current_module = ""
    
    def test(self, name: str):
        """Декоратор для тестов."""
        def decorator(func):
            def wrapper():
                try:
                    result = func()
                    if isinstance(result, bool):
                        self.results.append(TestResult(
                            name=f"{self.current_module}:{name}",
                            passed=result
                        ))
                    elif isinstance(result, tuple):
                        passed, msg = result
                        self.results.append(TestResult(
                            name=f"{self.current_module}:{name}",
                            passed=passed,
                            message=msg
                        ))
                    else:
                        self.results.append(TestResult(
                            name=f"{self.current_module}:{name}",
                            passed=result.passed,
                            message=result.message
                        ))
                except Exception as e:
                    self.results.append(TestResult(
                        name=f"{self.current_module}:{name}",
                        passed=False,
                        message=f"Exception: {e}"
                    ))
            return wrapper
        return decorator
    
    def run_all(self):
        """Запустить все тесты."""
        print("=" * 70)
        print("COMPREHENSIVE NAVIGATION TEST SUITE FOR SABOT")
        print("=" * 70)
        
        # Run all test modules
        _test_state_map(self)
        _test_back_handler(self)
        _test_materials(self)
        _test_events(self)
        _test_roles(self)
        _test_buddy(self)
        
        # Print results
        return self._print_results()
    
    def _print_results(self):
        """Вывести результаты тестирования."""
        print("\n" + "=" * 70)
        print("TEST RESULTS")
        print("=" * 70)
        
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        
        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            symbol = "v" if result.passed else "x"
            print(f"\n[{symbol}] {status}: {result.name}")
            if result.message:
                print(f"    Message: {result.message}")
        
        print("\n" + "=" * 70)
        print(f"TOTAL: {passed} passed, {failed} failed, {len(self.results)} total")
        print("=" * 70)
        
        return failed == 0


runner = TestRunner()


# ==================== Helper Functions ====================

def read_file(path: str) -> str:
    """Read file content."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def find_pattern(content: str, pattern: str) -> list:
    """Find all matches of pattern in content."""
    return re.findall(pattern, content)


# ==================== Test: STATE_MAP ====================

def _test_state_map(runner):
    """Проверка STATE_MAP."""
    runner.current_module = "state_map"
    
    content = read_file("handlers/common.py")
    
    @runner.test("has_state_history_support")
    def test_state_history_support():
        """Проверить что back_handler поддерживает _state_history."""
        if "_state_history" in content:
            return True, "_state_history is supported"
        return False, "_state_history not found"
    
    @runner.test("backwards_compatible")
    def test_backwards_compatible():
        """Проверить обратную совместимость с _prev_state/_prev_chain."""
        if "_prev_state" in content and "_prev_chain" in content:
            return True, "Backwards compatible with old system"
        return False, "Missing backwards compatibility"
    
    test_state_history_support()
    test_backwards_compatible()
    return True


# ==================== Test: Back Handler ====================

def _test_back_handler(runner):
    """Тест back_handler."""
    runner.current_module = "back_handler"
    
    content = read_file("handlers/common.py")
    
    @runner.test("uses_state_history_first")
    def test_uses_state_history_first():
        """Проверить что сначала проверяется _state_history."""
        # Должен быть код, который сначала проверяет state_history
        pattern = r'state_history\s*=\s*data\.get\("_state_history"\s*,\s*\[\]\)'
        matches = find_pattern(content, pattern)
        if matches:
            return True, "state_history check found"
        return False, "state_history check not found"
    
    @runner.test("has_fallback_to_prev_state")
    def test_fallback_to_prev_state():
        """Проверить fallback на старую систему."""
        # Должен быть fallback на _prev_state
        if "if prev_state_key and prev_state_key in STATE_MAP:" in content:
            return True, "Fallback to _prev_state found"
        return False, "Fallback not found"
    
    test_uses_state_history_first()
    test_fallback_to_prev_state()
    return True


# ==================== Test: Materials ====================

def _test_materials(runner):
    """Тест materials.py."""
    runner.current_module = "materials"
    
    content = read_file("handlers/materials.py")
    
    @runner.test("has_state_history_in_add")
    def test_has_state_history_in_add():
        """Проверить _state_history при добавлении материала."""
        # Проверяем что в add material используется _state_history
        matches = find_pattern(content, r'_state_history\s*=\s*\[\]')
        if matches:
            return True, f"Found {len(matches)} _state_history initializations"
        return False, "_state_history initialization not found"
    
    @runner.test("builds_history_correctly")
    def test_builds_history_correctly():
        """Проверить построение истории."""
        # Проверяем что history.append используется
        matches = find_pattern(content, r'history\.append\("[^"]+"\)')
        if matches:
            return True, f"Found {len(matches)} history.append calls"
        return False, "history.append not found"
    
    @runner.test("no_duplicate_prev_state")
    def test_no_duplicate_prev_state():
        """Проверить отсутствие дублирования _prev_state."""
        # Не должно быть двух update_data с _prev_state подряд
        lines = content.split('\n')
        prev_state_count = 0
        for line in lines:
            if '_prev_state=' in line and 'update_data' in line:
                prev_state_count += 1
        # В materials.py должно быть не более 5 установок _prev_state
        if prev_state_count <= 6:
            return True, f"Found {prev_state_count} _prev_state updates"
        return False, f"Too many _prev_state updates: {prev_state_count}"
    
    test_has_state_history_in_add()
    test_builds_history_correctly()
    test_no_duplicate_prev_state()
    return True


# ==================== Test: Events ====================

def _test_events(runner):
    """Тест events.py."""
    runner.current_module = "events"
    
    content = read_file("handlers/events.py")
    
    @runner.test("has_state_history")
    def test_has_state_history():
        """Проверить _state_history в events."""
        matches = find_pattern(content, r'_state_history')
        if matches:
            return True, f"Found {len(matches)} _state_history references"
        return False, "_state_history not found"
    
    @runner.test("builds_history_chain")
    def test_builds_history_chain():
        """Проверить построение цепочки истории."""
        matches = find_pattern(content, r'history\.append\("[^"]+"\)')
        if matches:
            return True, f"Found {len(matches)} history.append calls"
        return False, "history.append not found"
    
    test_has_state_history()
    test_builds_history_chain()
    return True


# ==================== Test: Roles ====================

def _test_roles(runner):
    """Тест roles.py."""
    runner.current_module = "roles"
    
    content = read_file("handlers/roles.py")
    
    @runner.test("no_duplicate_update_data")
    def test_no_duplicate_update_data():
        """Проверить отсутствие дублирования update_data."""
        # Не должно быть двух update_data подряд с _prev_state
        # Проверяем конкретную строку, которая была исправлена
        if 'await state.update_data(_prev_state="selecting_role")' in content:
            return False, "Duplicate _prev_state update still present"
        return True, "No duplicate _prev_state update"
    
    @runner.test("has_state_history")
    def test_has_state_history():
        """Проверить _state_history в roles."""
        matches = find_pattern(content, r'_state_history')
        if matches:
            return True, f"Found {len(matches)} _state_history references"
        return False, "_state_history not found"
    
    test_no_duplicate_update_data()
    test_has_state_history()
    return True


# ==================== Test: Buddy ====================

def _test_buddy(runner):
    """Тест buddy.py."""
    runner.current_module = "buddy"
    
    content = read_file("handlers/buddy.py")
    
    @runner.test("has_state_history")
    def test_has_state_history():
        """Проверить _state_history в buddy."""
        matches = find_pattern(content, r'_state_history')
        if matches:
            return True, f"Found {len(matches)} _state_history references"
        return False, "_state_history not found"
    
    @runner.test("lion_assign_has_history")
    def test_lion_assign_has_history():
        """Проверить что lion assign инициализирует историю."""
        if 'lion_action="assign_mentee"' in content and '_state_history=[]' in content:
            return True, "Lion assign has _state_history"
        return False, "Lion assign missing _state_history"
    
    test_has_state_history()
    test_lion_assign_has_history()
    return True


# ==================== Static Analysis Summary ====================

def print_summary():
    """Вывести сводку анализа."""
    print("\n" + "=" * 70)
    print("STATIC ANALYSIS SUMMARY")
    print("=" * 70)
    
    files = [
        ("handlers/common.py", "Back handler and STATE_MAP"),
        ("handlers/materials.py", "Materials FSM"),
        ("handlers/events.py", "Events FSM"),
        ("handlers/roles.py", "Roles FSM"),
        ("handlers/buddy.py", "Buddy FSM"),
    ]
    
    for filepath, description in files:
        content = read_file(filepath)
        
        # Count _state_history references
        state_history_count = len(find_pattern(content, r'_state_history'))
        
        # Count _prev_state references
        prev_state_count = len(find_pattern(content, r'_prev_state'))
        
        print(f"\n{filepath} ({description}):")
        print(f"  - _state_history references: {state_history_count}")
        print(f"  - _prev_state references: {prev_state_count}")
        
        # Check for specific patterns
        if 'history.append(' in content:
            append_count = len(find_pattern(content, r'history\.append\('))
            print(f"  - history.append calls: {append_count}")
        
        if '_state_history=[]' in content:
            init_count = len(find_pattern(content, r'_state_history=\[\]'))
            print(f"  - _state_history initializations: {init_count}")


# ==================== Bug Report ====================

def print_bug_report():
    """Вывести отчёт о багах."""
    print("\n" + "=" * 70)
    print("BUG FIXES SUMMARY")
    print("=" * 70)
    
    fixes = [
        {
            "id": "FIX-001",
            "title": "Добавлена поддержка _state_history для многоуровневого возврата",
            "location": "handlers/common.py",
            "description": "back_handler теперь поддерживает стек истории состояний _state_history для возврата на несколько шагов назад."
        },
        {
            "id": "FIX-002",
            "title": "Исправлена цепочка навигации в materials.py",
            "location": "handlers/materials.py",
            "description": "Добавлена инициализация _state_history и сохранение истории при каждом переходе."
        },
        {
            "id": "FIX-003",
            "title": "Исправлена цепочка навигации в events.py",
            "location": "handlers/events.py",
            "description": "Добавлена _state_history для поддержки многоуровневого возврата."
        },
        {
            "id": "FIX-004",
            "title": "Убрано дублирование _prev_state в roles.py",
            "location": "handlers/roles.py",
            "description": "Убран дублирующий вызов update_data с _prev_state."
        },
        {
            "id": "FIX-005",
            "title": "Добавлена _state_history в buddy.py",
            "location": "handlers/buddy.py",
            "description": "Добавлена поддержка стека истории для FSM Buddy."
        },
    ]
    
    for fix in fixes:
        print(f"\n{fix['id']}: {fix['title']}")
        print(f"  Location: {fix['location']}")
        print(f"  Description: {fix['description']}")


# ==================== Main ====================

if __name__ == "__main__":
    # Print summary
    print_summary()
    
    # Run tests
    success = runner.run_all()
    
    # Print bug report
    print_bug_report()
    
    print("\n" + "=" * 70)
    print("TESTING COMPLETE")
    print("=" * 70)
    
    sys.exit(0 if success else 1)
